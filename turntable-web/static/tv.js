"use strict";
/* Turntable-1's TV view (tv.html, and the Cast receiver, receiver.html): concept B with Beepnik's
   needs and a combined uplink ticker (Ground Control and system lines). Reads the same feeds as the
   main page. Shared helpers below are copied from app.js. */

const $ = (id) => document.getElementById(id);
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.append(c);
  return node;
}

/* --- shared with app.js --- */
const STAGE_KM = { leo: 400, geo: 35786, lunar: 384400, deep: 1.5e6 };
function kmFor(alt, o) {
  const pts = [[o.reentry_altitude || 1, 100]];
  for (const [a, id] of (o.stages || []).slice(1)) if (STAGE_KM[id]) pts.push([a, STAGE_KM[id]]);
  pts.push([o.ceiling || 30, 1e7]);
  pts.sort((p, q) => p[0] - q[0]);
  if (alt <= pts[0][0]) return pts[0][1] * Math.max(0, alt / pts[0][0]);
  for (let i = 1; i < pts.length; i++) {
    const [a0, k0] = pts[i - 1], [a1, k1] = pts[i];
    if (alt <= a1) return k0 * Math.pow(k1 / k0, (alt - a0) / (a1 - a0));
  }
  return pts[pts.length - 1][1];
}
function kmText(km) {
  if (km >= 1e6) return `${(km / 1e6).toFixed(km >= 1e7 ? 0 : 1).replace(/\.0$/, "")} million km`;
  const r = km >= 100000 ? Math.round(km / 100) * 100 : km >= 10000 ? Math.round(km / 100) * 100 : km >= 1000 ? Math.round(km / 10) * 10 : Math.round(km);
  return `${r.toLocaleString("en-US")} km`;
}
function faceFor(state) {
  if (state.activity === "sleeping") return "sleep";
  const s = state.mood_score;
  return s >= 85 ? "ecstatic" : s >= 65 ? "happy" : s >= 45 ? "content" : s >= 25 ? "restless" : "glum";
}
function looks(st) {
  const c = st.cosmetics || {};
  const age = (c.age_days || 0) > 90 ? 120 : (c.age_days || 0) > 30 ? 45 : 0;
  return { patches: c.patches || 0, age, generation: c.generation || st.generation || 1 };
}
const NEED_NAMES = { fuel: "Fuel", variety: "Variety", curiosity: "Curiosity", taste: "Taste" };
const LOW_FLAVORS = ["starving", "peckish", "restless", "bored", "homesick"];
function miniFace(kind) {
  const eyes = { happy: '<path d="M14 19 q4 -5 8 0 M26 19 q4 -5 8 0" stroke="#1d183b" stroke-width="2.6" fill="none" stroke-linecap="round"/>',
    content: '<circle cx="17" cy="19" r="2.6" fill="#1d183b"/><circle cx="31" cy="19" r="2.6" fill="#1d183b"/>',
    restless: '<circle cx="17" cy="19" r="3.2" fill="#fff" stroke="#1d183b" stroke-width="1.4"/><circle cx="18.5" cy="19" r="1.5" fill="#1d183b"/><circle cx="31" cy="19" r="3.2" fill="#fff" stroke="#1d183b" stroke-width="1.4"/><circle cx="32.5" cy="19" r="1.5" fill="#1d183b"/>',
    glum: '<path d="M14 20 h7 M27 20 h7" stroke="#1d183b" stroke-width="2.6" stroke-linecap="round"/>',
    sleep: '<path d="M14 20 q4 3 8 0 M26 20 q4 3 8 0" stroke="#1d183b" stroke-width="2.4" fill="none" stroke-linecap="round"/>' };
  const mouth = { happy: '<path d="M15 27 q9 9 18 0" stroke="#1d183b" stroke-width="2.6" fill="none" stroke-linecap="round"/>',
    content: '<path d="M17 29 q7 4 14 0" stroke="#1d183b" stroke-width="2.4" fill="none" stroke-linecap="round"/>',
    restless: '<path d="M17 30 q3.5 -3 7 0 t7 0" stroke="#1d183b" stroke-width="2.2" fill="none" stroke-linecap="round"/>',
    glum: '<path d="M17 32 q7 -6 14 0" stroke="#1d183b" stroke-width="2.4" fill="none" stroke-linecap="round"/>',
    sleep: '<path d="M20 30 h8" stroke="#1d183b" stroke-width="2.2" stroke-linecap="round"/>' };
  const ring = { happy: "#88e3bf", content: "#ffd16f", restless: "#ffa366", glum: "#ff5c82", sleep: "#7aa2ff" }[kind];
  const span = el("span", { class: "mface" });
  // each face its own gradient: with a shared id, a face could borrow one from a hidden section and lose its fill
  const gid = `mfg${(miniFace.n = (miniFace.n || 0) + 1)}`;
  span.innerHTML = `<svg viewBox="0 0 48 48"><defs><radialGradient id="${gid}" cx=".35" cy=".3"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#aea4d0"/></radialGradient></defs>` +
    `<circle cx="24" cy="24" r="21" fill="url(#${gid})" stroke="${ring}" stroke-width="3"/>${eyes[kind]}${mouth[kind]}</svg>`;
  return span;
}

/* --- the stage ---------------------------------------------------------------------------------- */
const W = 1920, H = 1080;
const params = new URLSearchParams(location.search);
let nowData = null, pet = null, summary = null, sideClock = null;

function fit() {
  // the page's own layout size: a TV browser's zoom can't distort it (innerWidth can)
  const w = document.documentElement.clientWidth || innerWidth, h = document.documentElement.clientHeight || innerHeight;
  const s = Math.min(w / W, h / H);
  $("stage").style.transform = `translate(-50%, -50%) scale(${s})`;
}

function stars() {
  let out = "", seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < 170; i++)
    out += `<circle cx="${(rnd() * W).toFixed(0)}" cy="${(rnd() * H).toFixed(0)}" r="${[0.8, 1, 1.2, 1.6][Math.floor(rnd() * 4)]}" fill="#fff" opacity="${[0.25, 0.4, 0.6, 0.8][Math.floor(rnd() * 4)]}"/>`;
  return out;
}

function build() {
  $("stage").innerHTML = `
  <div class="drift">
    <svg class="stars" viewBox="0 0 ${W} ${H}">${stars()}</svg>
    <div class="wash" id="wash"></div><div class="shade"></div>
    <div class="top"><div class="logo">Turntable<span>-1</span></div><div class="sp"></div>
      <span class="stat" id="orbit-stat"></span><span class="pill" id="pill"></span><span class="clock" id="clock"></span></div>
    <div class="playing">
      <div class="cover" id="cover"></div>
      <div class="rider" id="rider"></div>
      <div class="np"><div class="eyebrow">NOW PLAYING</div><h1 id="title"></h1><div class="artist" id="artist"></div>
        <div class="album" id="album"></div><div class="timeline" id="timeline"></div><div class="sidenote" id="sidenote"></div>
        <div class="say"><p id="say-play"></p><small id="say-play-who">BEEPNIK</small></div></div>
    </div>
    <div class="idle">
      <div class="scene" id="scene"></div>
      <div class="np"><div class="eyebrow">STANDING BY</div><h1>Ready when you are</h1>
        <div class="say"><p id="say-idle"></p><small id="say-idle-who">BEEPNIK</small></div>
        <div class="cards"><div class="card art"><img class="cc" id="last-cover" alt="" hidden><div><h6>LAST SPIN</h6><p id="last-title"></p><span id="last-artist"></span></div></div>
          <div class="card art"><img class="cc" id="crave-cover" alt="" hidden><div><h6>CRAVING</h6><p id="crave-title"></p><span id="crave-note"></span></div></div>
          <div class="card"><h6>MISSION</h6><p id="mission-title"></p><span id="mission-note"></span></div></div></div>
    </div>
    <div class="replay" id="replay">
      <div class="cover" id="rp-cover"></div>
      <div class="np"><div class="eyebrow" id="rp-eyebrow">PLAYING BACK</div><h1 id="rp-title"></h1><div class="artist" id="rp-artist"></div>
        <div class="album" id="rp-album"></div>
        <div class="rp-prog"><b id="rp-fill"></b></div><div class="rp-times"><span id="rp-pos"></span><span id="rp-track"></span><span id="rp-left"></span></div>
        <div class="rp-ctrls" id="rp-ctrls"></div>
        <div class="rp-live" id="rp-live" hidden></div>
        <div class="rp-tracks" id="rp-tracks"></div></div>
    </div>
    <div class="needs" id="needs"></div>
    <div class="uplink off" id="uplink"><span class="tag">UPLINK</span><div class="box" id="uplink-box"><div class="tape" id="tape"></div></div></div>
    <div class="tapbar" id="tapbar">Press OK to start the sound</div>
  </div>`;
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

/* --- now playing -------------------------------------------------------------------------------- */
const VINYL = `<svg viewBox="0 0 540 540"><rect width="540" height="540" fill="#1e174b"/><circle cx="270" cy="270" r="236" fill="#100f16"/>${Array.from({ length: 20 }, (_, i) =>
  `<circle cx="270" cy="270" r="${98 + i * 7}" fill="none" stroke="#222030" stroke-width="2"/>`).join("")}
  <circle cx="270" cy="270" r="84" fill="#ffd16f"/><circle cx="270" cy="270" r="70" fill="none" stroke="#ffc13b" stroke-width="2"/>
  <circle cx="270" cy="270" r="6" fill="#0a0a0e"/></svg>`;
let coverSrc;                                       // undefined until the first render (null means "no cover")
function renderCover(src) {
  if (src === coverSrc) return;
  coverSrc = src;
  $("cover").innerHTML = src ? "" : `<div class="vinyl">${VINYL}</div>`;
  if (src) { const img = el("img", { src, alt: "" }); img.onerror = () => { $("cover").innerHTML = `<div class="vinyl">${VINYL}</div>`; }; $("cover").append(img); }
  $("wash").style.backgroundImage = src ? `url("${src}")` : "";
  $("wash").classList.toggle("on", !!src);
}

function fmt(s) { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }

function renderSide() {
  const box = $("timeline"), note = $("sidenote");
  if (!sideClock) { box.replaceChildren(); note.textContent = ""; return; }
  const v = sideClock, el_ = (Date.now() / 1000 - v.at) + v.elapsed;
  if (box.dataset.key !== v.key) {
    box.dataset.key = v.key;
    box.replaceChildren(...v.tracks.map((t) => el("div", { class: `tl ${t.state}` }, el("div", { class: "bar" }, el("b")),
      el("span", {}, el("em", { text: t.position }), document.createTextNode(t.title)))));
  }
  const now = box.children[v.index];
  if (now) {
    const secs = v.tracks[v.index].secs;
    now.querySelector("b").style.width = secs ? `${Math.min(100, (100 * el_) / secs)}%` : "100%";
  }
  const left = v.side_left != null ? Math.max(0, v.side_left - (Date.now() / 1000 - v.at)) : null;
  note.textContent = `Side ${v.side || ""}, track ${v.index + 1} of ${v.count}.` + (left != null ? ` ${fmt(left)} left on the side.` : "");
}

function renderNow() {
  const d = nowData;
  if (!d) return;
  // playing back a recording: the stage shows the playback; the turntable gets a note
  if (window.tvReplaying && window.tvReplaying()) {
    $("stage").dataset.mode = "replay";
    const live = $("rp-live"), cur = d.state === "playing" ? d.current : null;
    live.hidden = !cur;
    if (cur) live.innerHTML = `<b>ON THE TURNTABLE NOW</b> ${esc(cur.title || "A record")}<span>${esc(cur.artist || "")}</span><em>Stop to switch to live</em>`;
    if (window.tvFitReplay) window.tvFitReplay();
    const pill = $("pill"); pill.className = "pill"; pill.textContent = "Playing back";
    renderUplink();
    return;
  }
  const cur = d.current, playing = (d.state === "playing" || d.state === "unknown");
  $("stage").dataset.mode = playing ? "playing" : "idle";
  const pill = $("pill");
  if (d.state === "playing" && cur) {
    pill.className = "pill"; pill.textContent = `On the turntable since ${new Date(cur.started_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
    const t = $("title"); t.textContent = cur.title || "";
    t.className = (cur.title || "").length > 26 ? "longer" : (cur.title || "").length > 16 ? "long" : "";
    $("artist").textContent = cur.artist || "";
    const rel = cur.release;
    $("album").textContent = rel ? [rel.title, rel.year, rel.label].filter(Boolean).join(", ") : (cur.album || "");
    renderCover(cur.cover || (rel && rel.cover) || null);
    sideClock = cur.side_view ? { ...cur.side_view, at: Date.now() / 1000, key: `${cur.id}:${cur.side_view.index}` } : null;
  } else if (d.state === "unknown") {
    pill.className = "pill"; pill.textContent = "On the turntable now";
    $("title").textContent = "Something unrecognized"; $("title").className = "long";
    $("artist").textContent = ""; $("album").textContent = "Listening for a match.";
    renderCover(null); sideClock = null;
  } else {
    pill.className = "pill quiet"; pill.textContent = "Standing by";
  }
  renderSide();
  renderUplink();
}

/* --- Beepnik ------------------------------------------------------------------------------------ */
let riderKey = null, sceneKey = null;
function renderBeepnik() {
  if (!pet || !window.Probe) return;
  const st = pet, lk = looks(st), face = faceFor(st);
  const opts = { orbit: st.orbit.id, tier: st.signal.draw_tier, traits: (st.traits || []).map((t) => t.id), egg: !st.hatched, face, ...lk };
  const rk = JSON.stringify(opts);
  if (rk !== riderKey) { riderKey = rk; $("rider").innerHTML = `<svg viewBox="40 18 240 190">${Probe.companionMarkup({ ...opts, layers: "probe" })}</svg>`; }
  const sk = rk + ":scene";
  if (sk !== sceneKey) { sceneKey = sk; $("scene").innerHTML = `<svg viewBox="0 0 320 240">${Probe.companionMarkup({ ...opts, layers: "all" })}</svg>`; }
  const line = (st.lines || [])[0];
  const ago = line ? Math.round((Date.now() / 1000 - line.at) / 60) : null;
  const text = line ? line.text : "Standing by for launch.";
  $("say-play").textContent = text; $("say-idle").textContent = text;
  const who = (st.name || "Beepnik").toUpperCase();
  $("say-play-who").textContent = who;
  $("say-idle-who").textContent = ago == null ? who : `${who}, ${ago < 1 ? "JUST NOW" : ago < 60 ? `${ago} MIN AGO` : ago < 48 * 60 ? `${Math.round(ago / 60)} H AGO` : `${Math.round(ago / 1440)} DAYS AGO`}`;
  const o = st.orbit;
  if (o && o.stages && !st.lost) $("orbit-stat").innerHTML = `${o.label}, <b>${kmText(kmFor(o.altitude, o))}</b> from Earth`;
  else $("orbit-stat").textContent = st.lost ? "Lost at Point Nemo" : "";
  // idle cards
  const ls = summary && summary.last_spin;
  $("last-title").textContent = ls ? ls.title : "Nothing yet"; $("last-artist").textContent = ls ? ls.artist : "";
  const art = (img, src) => { if (src) { if (img.getAttribute("src") !== src) img.src = src; img.hidden = false; } else img.hidden = true; };
  art($("last-cover"), ls && ls.cover);
  art($("crave-cover"), st.craving && st.craving.cover);
  const c = st.craving;
  $("crave-title").textContent = c ? (c.title || "Something") : "Resting";
  const cl = c && c.expires_at ? (c.expires_at - Date.now() / 1000) / 3600 : null;
  $("crave-note").textContent = !c ? "Content for now" : c.savoring ? "Savoring it now" : cl == null ? "" : cl < 1 ? "Patience running low" : `Gives up in ${Math.round(cl)} h`;
  const m = st.mission;
  $("mission-title").textContent = m ? (m.short || m.text) : "No mission right now";
  $("mission-note").textContent = m ? `+${m.reward || 25} curiosity, ${Math.max(0, Math.round(((m.expires_at || 0) - Date.now() / 1000) / 3600))} h left` : "";
  renderNeeds();
}

/* --- the needs row ------------------------------------------------------------------------------ */
/* Each need has its own colour, everywhere: fuel gold, variety sky, curiosity comet pink, taste lilac. */
const NEED_HEX = { fuel: "#ffcf6b", variety: "#7fa7ff", curiosity: "#ff7a9c", taste: "#c3b8ee" };
function dialSvg(value, color, marks = [], cls = "gs") {
  const cx = 60, cy = 62, r = 44, a0 = -210, span = 240;
  const pt = (v, rr) => { const a = ((a0 + (span * v) / 100) * Math.PI) / 180; return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]; };
  const arc = (v) => { const [x0, y0] = pt(0, r), [x1, y1] = pt(v, r);
    return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${(span * v) / 100 > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`; };
  const v = Math.max(0, Math.min(100, value));
  return `<svg viewBox="0 0 120 96" class="${cls}" aria-hidden="true"><path d="${arc(100)}" fill="none" stroke="#2c255f" stroke-width="10" stroke-linecap="round"/>` +
    (v >= 1 ? `<path d="${arc(v)}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"/>` : "") +
    marks.map((m) => { const [x1, y1] = pt(m.v, r + 8), [x2, y2] = pt(m.v, r - 8);
      return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${m.color}" stroke-width="3" stroke-linecap="round"/>`; }).join("") +
    `<text x="60" y="70" text-anchor="middle" font-family="Righteous, sans-serif" font-size="27" fill="#f4eefe">${Math.round(v)}</text></svg>`;
}
// kept under their old names: the TV's tabs call them too
function gaugeSvg(v, need = "variety") { return [NEED_HEX[need], dialSvg(v, NEED_HEX[need])]; }
function tankSvg(v, hold, burn) { return dialSvg(v, NEED_HEX.fuel, [{ v: hold, color: "#ff7a9c" }, { v: burn, color: "#f4eefe" }]); }
/* Beepnik's own face, for the mood card: his head, cropped from the probe drawing. */
function beepHead(st, cls = "face") {
  const opts = { orbit: st.orbit.id, tier: 0, traits: (st.traits || []).map((t) => t.id), egg: !st.hatched, face: faceFor(st), ...looks(st), layers: "probe" };
  return `<span class="${cls}"><svg viewBox="118 64 84 84" aria-hidden="true">${window.Probe ? Probe.companionMarkup(opts) : ""}</svg></span>`;
}
const TONE = { fuel: "c-fuel", variety: "c-variety", curiosity: "c-curiosity", taste: "c-taste", low: "c-pink", ok: "c-mint", muted: "" };
function card(iconHtml, name, value, vcol, l1, l1col, l2, low = false) {
  return `<div class="need${low ? " low" : ""}">${iconHtml}<div class="nt"><b>${name}${value !== "" ? ` <span class="${TONE[vcol] || ""}">${value}</span>` : ""}${low ? ' <i class="lowtag">LOW</i>' : ""}</b>
    <small class="${TONE[l1col] || ""}">${l1 || ""}</small>${l2 ? `<small>${l2}</small>` : ""}</div></div>`;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function renderNeeds() {
  const st = pet, n = st.needs || {};
  const md = st.mood_detail || {};
  const face = beepHead(st);
  const mood = (st.mood || "").charAt(0).toUpperCase() + (st.mood || "").slice(1);
  const flav = md.flavor ? `${md.joiner} ${md.flavor}` : "";
  const fuel = Math.round(n.fuel ?? 0), o = st.orbit || {}, hold = o.hold_level ?? 30, burn = o.burn_line ?? 60;
  const fuelStatus = !st.hatched ? "Filling up" : fuel >= burn ? "Burn ready" : fuel > hold ? "Holding orbit" : "Can't hold orbit";
  const [, vs] = gaugeSvg(n.variety ?? 0, "variety"), [, cs] = gaugeSvg(n.curiosity ?? 0, "curiosity"), [, ts] = gaugeSvg(n.taste ?? 0, "taste");
  const low = (k) => (st.low || []).includes(k);
  const v = st.variety || {}, cd = (st.curiosity || {}).detail || {}, td = st.taste_detail || {};
  const t = cd.today || {};
  const cSub = (st.curiosity || {}).streak >= 2 ? `streak: ${st.curiosity.streak} in a row` : (st.curiosity || {}).familiar_now ? "a familiar record"
    : t.tracks || t.sides || t.records ? "fed today" : "nothing new lately";
  const tSub = !(td.favorites || []).length ? "no favorites yet" : td.fed >= .35 ? "well fed lately" : td.fed >= .12 ? "some favorites" : "missing favorites";
  $("needs").innerHTML =
    card(face, esc(mood), "", "", esc(flav), md.flavor && LOW_FLAVORS.includes(md.flavor) ? "low" : "ok", "Mood") +
    card(tankSvg(fuel, hold, burn), "Fuel", fuel, "fuel", fuelStatus, fuel > hold ? "muted" : "low", `Burns at ${Math.round(burn)}`, low("fuel")) +
    card(vs, "Variety", Math.round(n.variety ?? 0), "variety", v.styles_worth ? `${v.styles_worth} genres` : "", "muted", v.eras_worth ? `${v.eras_worth} decades` : "", low("variety")) +
    card(cs, "Curiosity", Math.round(n.curiosity ?? 0), "curiosity", cSub, "muted", "", low("curiosity")) +
    card(ts, "Taste", Math.round(n.taste ?? 0), "taste", tSub, "muted", "", low("taste"));
}

/* --- the uplink: Ground Control's line for this track, and recent system lines -------------------- */
let tapeKey = null, tapeAnim = null;
function renderUplink() {
  const d = nowData, playing = d && d.state === "playing" && d.current;
  const items = [];
  if (playing) items.push({ voice: "ground", text: d.current.ground ? d.current.ground.text : "Acquiring the track, stand by." });
  const since = Date.now() / 1000 - 1800;
  for (const t of (pet && pet.transmissions) || []) if (t.voice === "system" && t.at >= since && items.length < 4) items.push(t);
  const box = $("uplink");
  box.classList.toggle("off", !items.length);
  const key = JSON.stringify(items);
  if (key === tapeKey) return;
  tapeKey = key;
  const tape = $("tape");
  tape.innerHTML = items.map((it, i) => (i ? '<span class="sep">\u25C6</span>' : "") + (it.voice === "ground"
    ? `<span class="who">GROUND CONTROL \u25B8</span> ${esc(it.text)}` : `<span class="whos">SYSTEM \u25B8</span> <span class="sys">${esc(it.text)}</span>`)).join("");
  if (tapeAnim) tapeAnim.cancel();
  const boxW = $("uplink-box").clientWidth || 1600, textW = tape.scrollWidth, speed = 90, gap = 160;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || !items.length) return;
  tapeAnim = tape.animate([{ transform: `translate(${boxW}px,-50%)` }, { transform: `translate(${-textW - gap}px,-50%)` }],
    { duration: ((boxW + textW + gap) / speed) * 1000, iterations: Infinity, easing: "linear" });
}

/* --- clock, polling, sound and Cast ------------------------------------------------------------- */
function tickClock() { $("clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, ""); }

async function pollNow() { try { nowData = await getJSON("/api/now"); renderNow(); } catch (e) { /* the next poll retries */ } }
async function pollPet() { try { pet = await getJSON("/api/pet"); renderBeepnik(); renderUplink(); } catch (e) { } }
async function pollSummary() { try { summary = await getJSON("/api/summary"); if (pet) renderBeepnik(); } catch (e) { } }

function startSound() {
  const a = $("tv-audio");
  if (!a) return;
  a.src = "/stream";
  a.play().then(() => $("tapbar").classList.remove("on")).catch(() => {
    $("tapbar").classList.add("on");                 // the browser wants a press first
    const go = () => { a.play().then(() => $("tapbar").classList.remove("on")).catch(() => {}); };
    addEventListener("keydown", go, { once: true }); addEventListener("click", go, { once: true });
  });
}

function startCast() {
  // The Cast receiver: the stream is loaded by the phone (the sender) and played by the Cast player;
  // this page draws the view. Stay up while the music plays, however long.
  const ctx = cast.framework.CastReceiverContext.getInstance();
  const opts = new cast.framework.CastReceiverOptions();
  opts.disableIdleTimeout = true;
  opts.maxInactivity = 3600;
  ctx.start(opts);
}

document.addEventListener("DOMContentLoaded", () => {
  build(); fit(); addEventListener("resize", fit);
  tickClock(); setInterval(tickClock, 10000);
  pollNow(); pollPet(); pollSummary();
  setInterval(pollNow, 4000); setInterval(pollPet, 10000); setInterval(pollSummary, 60000);
  setInterval(renderSide, 1000);
  if (window.TV_CAST && window.cast && cast.framework) startCast();
  else if (params.get("audio") === "1") startSound();
});
