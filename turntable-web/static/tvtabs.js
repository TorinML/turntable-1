"use strict";
/* Turntable-1's TV tabs (tv.html): Now, Beepnik, Log, Cargo and Stars, driven by a TV remote.
   Arrows move between items (to the nearest one in that direction), OK opens or plays, Back steps
   out one level: an overlay, then the tab's content, then the tab bar, then Now. After a minute with
   no input it drifts back to Now, and on Now the tab bar fades away. Uses tv.js's helpers (el, esc,
   gaugeSvg, tankSvg, miniFace, faceFor, looks, kmFor, kmText) and its data (pet, nowData). */

const TABS = [["now", "Now"], ["beepnik", "Beepnik"], ["log", "Log"], ["cargo", "Cargo"], ["stars", "Stars"]];
const IDLE_MS = 60000;
let tvTab = "now", focusKey = null, overlay = null, overlayReturn = null, lastInput = Date.now(), awake = false;
let hist = null, recs = [], gridData = null, chartRecs = null, chartView = null;
let cargo = { items: [], total: 0, sort: "added", q: "", unplayed: false, loading: false };

/* --- building ------------------------------------------------------------------------------------ */
function buildTabs() {
  const top = document.querySelector(".top"), drift = document.querySelector(".drift");
  if (!top || document.getElementById("tvtabs")) return;
  const bar = el("nav", { class: "tvtabs", id: "tvtabs" });
  for (const [id, label] of TABS) bar.append(el("button", { class: "tvtab", "data-f": `tab:${id}`, "data-tab": id, text: label }));
  top.querySelector(".logo").after(bar);
  for (const [id] of TABS.slice(1)) drift.append(el("div", { class: "view", "data-view": id, id: `view-${id}` }));
  drift.append(el("div", { class: "tvplay", id: "tvplay", hidden: "" }));
  drift.append(el("div", { class: "tvhint", id: "tvhint" }));
  drift.append(el("div", { class: "tvover", id: "tvover", hidden: "" }));
  const a = el("audio", { id: "tv-rec", preload: "none" });
  document.body.append(a);
  a.addEventListener("ended", nextPart);
  a.addEventListener("timeupdate", renderPlayBar);
}

/* --- focus and the remote --------------------------------------------------------------------------- */
const visible = (n) => n.offsetParent !== null && !n.closest("[hidden]");
function scope() { return overlay ? $("tvover") : document.getElementById(`view-${tvTab}`) || $("stage"); }
function focusables() {
  const inScope = [...scope().querySelectorAll("[data-f]")].filter(visible);
  if (overlay) return inScope;
  const bar = [...document.querySelectorAll("#tvtabs [data-f]")];
  const play = [...document.querySelectorAll("#tvplay [data-f]")].filter(visible);
  const replay = tvTab === "now" ? [...document.querySelectorAll("#rp-ctrls [data-f]")].filter(visible) : [];
  return [...bar, ...(tvTab === "now" ? replay : inScope), ...play];
}
function current() { return focusKey ? document.querySelector(`[data-f="${CSS.escape(focusKey)}"]`) : null; }
function setFocus(node) {
  for (const n of document.querySelectorAll(".tvf")) n.classList.remove("tvf");
  focusKey = node ? node.dataset.f : null;
  if (node) {
    node.classList.add("tvf");
    const box = node.closest(".vscroll");
    if (box) {
      const r = node.getBoundingClientRect(), b = box.getBoundingClientRect(), s = b.height / box.clientHeight;
      if (r.bottom > b.bottom - 20 * s) box.scrollTop += (r.bottom - b.bottom) / s + 40;
      if (r.top < b.top + 20 * s) box.scrollTop -= (b.top - r.top) / s + 40;
    }
    if (node.dataset.more) loadMoreCargo();
  }
  renderHint();
}
function restoreFocus() { const n = current(); if (n) n.classList.add("tvf"); else if (focusKey) setFocus(null); }

function move(dir) {
  const cur = current();
  const list = focusables();
  if (!cur) { setFocus(list.find((n) => n.dataset.f === `tab:${tvTab}`) || list[0]); return; }
  const inBar = cur.closest("#tvtabs");
  const r = cur.getBoundingClientRect(), cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
  let best = null, bestScore = Infinity;
  for (const n of list) {
    if (n === cur) continue;
    if (inBar && (dir === "left" || dir === "right") && !n.closest("#tvtabs")) continue;
    // and the reverse: from a tab's content, left and right stay in the content (only Up reaches the tabs)
    if (!inBar && (dir === "left" || dir === "right") && n.closest("#tvtabs")) continue;
    // an overlay's Close sits in a fixed footer, always on screen: reach it only after the content runs out
    if (overlay && dir === "down" && n.closest(".foot") && !cur.closest(".foot")) continue;
    const q = n.getBoundingClientRect(), nx = (q.left + q.right) / 2, ny = (q.top + q.bottom) / 2;
    const dx = nx - cx, dy = ny - cy;
    let primary, orth;
    if (dir === "right") { if (q.left < r.right - 6) continue; primary = dx; orth = Math.abs(dy); }
    else if (dir === "left") { if (q.right > r.left + 6) continue; primary = -dx; orth = Math.abs(dy); }
    else if (dir === "down") { if (q.top < r.bottom - 6) continue; primary = dy; orth = Math.abs(dx); }
    else { if (q.bottom > r.top + 6) continue; primary = -dy; orth = Math.abs(dx); }
    const score = primary + orth * 2.2;
    if (score < bestScore) { bestScore = score; best = n; }
  }
  if (!best && dir === "up" && !inBar && !overlay) best = document.querySelector(`#tvtabs [data-tab="${tvTab}"]`);
  if (!best && dir === "down" && overlay && !cur.closest(".foot")) best = document.querySelector('#tvover .foot [data-f]');
  // leaving a tab's content upward lands on its own tab, never on whichever tab happens to sit above
  if (best && !inBar && best.closest("#tvtabs")) best = document.querySelector(`#tvtabs [data-tab="${tvTab}"]`) || best;
  if (best) {
    setFocus(best);
    if (best.closest("#tvtabs")) showTab(best.dataset.tab);          // moving along the tabs switches them
  }
}

function activate() {
  const n = current();
  if (!n) { move("down"); return; }
  if (n.tagName === "INPUT") { n.focus(); return; }
  n.click();
}

/** Back, one level at a time. True if it was handled here; false at Now (the app shows its menu). */
function back() {
  wake();
  if (overlay) { closeOverlay(); return true; }
  const cur = current();
  if (cur && !cur.closest("#tvtabs") && !cur.closest("#tvplay")) { setFocus(document.querySelector(`#tvtabs [data-tab="${tvTab}"]`)); return true; }
  if (tvTab !== "now") { showTab("now"); setFocus(document.querySelector('#tvtabs [data-tab="now"]')); return true; }
  return false;
}
window.tvBack = back;

function wake() {
  lastInput = Date.now();
  if (!awake) { awake = true; $("stage").classList.add("awake"); }
}
function onKey(e) {
  if (document.activeElement && document.activeElement.tagName === "INPUT") {
    if (e.key === "Enter" || e.key === "Escape") { document.activeElement.blur(); runSearch(); e.preventDefault(); }
    return;
  }
  const keys = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
  const wasAwake = awake;
  wake();
  if (!wasAwake && !current()) { setFocus(document.querySelector(`#tvtabs [data-tab="${tvTab}"]`)); e.preventDefault(); return; }
  if (keys[e.key]) { move(keys[e.key]); e.preventDefault(); }
  else if (e.key === "Enter" || e.key === " ") { activate(); e.preventDefault(); }
  else if (e.key === "Escape" || e.key === "Backspace" || e.key === "BrowserBack" || e.key === "GoBack") { back(); e.preventDefault(); }
  else if (e.key === "MediaPlayPause") { togglePlay(); e.preventDefault(); }
}

function idleCheck() {
  if (Date.now() - lastInput < IDLE_MS || overlay) return;
  if (tvTab !== "now") showTab("now");
  setFocus(null);
  awake = false;
  $("stage").classList.remove("awake");
}

/* --- tabs ----------------------------------------------------------------------------------------- */
function showTab(id) {
  tvTab = id;
  $("stage").dataset.tab = id;
  for (const b of document.querySelectorAll("#tvtabs .tvtab")) b.classList.toggle("cur", b.dataset.tab === id);
  if (id === "beepnik") renderBeepView();
  if (id === "log") loadLog();
  if (id === "cargo" && !cargo.items.length) loadCargo(true);
  if (id === "stars") loadStars();
  if (play) renderPlayBar(false);
  renderHint();
}

function renderHint() {
  const n = current(), h = $("tvhint");
  if (!h) return;
  let parts;
  if (overlay) parts = [["\u25B2\u25BC", "move"], ["OK", "choose"], ["BACK", "close"]];
  else if (!n || n.closest("#tvtabs")) parts = tvTab === "now" ? [["\u25C0\u25B6", "tabs"], ["BACK", "menu"]] : [["\u25C0\u25B6", "tabs"], ["\u25BC", "into the tab"], ["BACK", "to Now"]];
  else parts = [["\u25C0\u25B6\u25B2\u25BC", "move"], ["OK", n.dataset.hint || "open"], ["BACK", "tabs"]];
  h.replaceChildren(...parts.map(([k, v]) => el("span", {}, el("b", { text: k }), document.createTextNode(v))));
}

/* --- overlays -------------------------------------------------------------------------------------- */
function openOverlay(title, ...body) {
  overlayReturn = focusKey;
  overlay = true;
  const box = $("tvover");
  // Close is a fixed footer, so opening an overlay never scrolls it away from its top
  box.replaceChildren(el("div", { class: "tvcard" }, el("h2", { text: title }), el("div", { class: "vscroll" }, ...body),
    el("div", { class: "foot" }, btn("ov:close", "Close", closeOverlay))));
  box.hidden = false;
  box.querySelector(".vscroll").scrollTop = 0;
  setFocus(focusables()[0] || null);
}
function closeOverlay() {
  overlay = null;
  $("tvover").hidden = true;
  focusKey = overlayReturn;
  restoreFocus();
  renderHint();
}
const btn = (key, text, onClick, cls = "tvbtn", hint) => {
  const b = el("button", { class: cls, "data-f": key, text });
  if (hint) b.dataset.hint = hint;
  b.addEventListener("click", onClick);
  return b;
};
const row = (label, value, share, color = "#ffd16f") => {
  const bar = el("div", { class: "tvbar" }, el("b"));
  bar.firstChild.style.width = `${Math.max(2, Math.min(100, share * 100))}%`;
  bar.firstChild.style.background = color;
  return el("div", { class: "tvrow" }, el("span", { text: label }), bar, el("span", { class: "v", text: value }));
};
const line = (text, cls = "") => el("p", { class: `tvline ${cls}`, text });

/* --- Beepnik --------------------------------------------------------------------------------------- */
function needCard(key, iconHtml, name, value, sub, sub2, onOpen, need = null, low = false) {
  const c = el("div", { class: "tvneed" + (low ? " low" : ""), "data-f": key, "data-hint": "open the breakdown" });
  c.innerHTML = `${iconHtml}<b>${esc(name)}${value !== "" ? ` <span class="${need ? `c-${need}` : "c-gold"}">${esc(value)}</span>` : ""}</b>` +
    `<small>${esc(sub || "")}</small>${sub2 ? `<small>${esc(sub2)}</small>` : ""}` + (need ? sparkSvg(need) : "") + (low ? '<i class="lowtag">LOW</i>' : "");
  c.addEventListener("click", onOpen);
  return c;
}

/* ---- Beepnik's story on the TV: a week per need, his journey, his achievements ---- */
let story = null, pick = null;
async function loadStory() {
  try { story = await getJSON("/api/beepnik"); } catch (e) { return; }
  if (tvTab === "beepnik" && !overlay) renderBeepView();
}
function sparkSvg(need) {
  const h = story && story.history;
  if (!h || h.length < 2) return "";
  const w = 160, ht = 34, xy = h.map((p, i) => [(i / (h.length - 1)) * w, ht - 3 - (Math.max(0, Math.min(100, p[need] ?? 0)) / 100) * (ht - 6)]);
  const d = "M" + xy.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L"), [lx, ly] = xy[xy.length - 1];
  return `<svg class="tvspark" viewBox="-4 0 ${w + 8} ${ht}" aria-hidden="true"><path d="${d} L${w} ${ht} L0 ${ht} Z" fill="${NEED_HEX[need]}" opacity=".15"/>` +
    `<path d="${d}" fill="none" stroke="${NEED_HEX[need]}" stroke-width="2.4" stroke-linejoin="round"/><circle cx="${lx}" cy="${ly}" r="4" fill="${NEED_HEX[need]}"/></svg>`;
}
const MEDAL_C = { listening: ["#ffcf6b", "#ff7a9c"], variety: ["#7fa7ff", "#ffcf6b"], curiosity: ["#ff7a9c", "#c3b8ee"], taste: ["#c3b8ee", "#7fa7ff"], flight: ["#8fe3c0", "#ffcf6b"] };
function medalHtml(group, locked) {
  const [c, r] = MEDAL_C[group] || MEDAL_C.listening;
  return `<svg viewBox="0 0 64 70" class="medal${locked ? " locked" : ""}" aria-hidden="true"><path d="M22 2 L32 24 L42 2" stroke="${r}" stroke-width="7" fill="none"/>` +
    `<circle cx="32" cy="44" r="22" fill="${c}"/><circle cx="32" cy="44" r="16" fill="none" stroke="#000" stroke-opacity=".18" stroke-width="2"/></svg>`;
}
const shortDay = (ts) => { const d = new Date(ts * 1000); return d.toDateString() === new Date().toDateString() ? "Today" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); };

/* Talking to him, with the remote: Ask for a record, How are you? */
async function talk(url, key) {
  const b = document.querySelector(`[data-f="${key}"]`);
  if (b && b.classList.contains("busy")) return;
  if (b) { b.classList.add("busy"); b.textContent = "Thinking\u2026"; }
  try {
    const r = await fetch(url, { method: "POST" });
    const d = await r.json().catch(() => ({}));
    if (r.ok) {
      pick = d.suggestion || (url.endsWith("ask") ? null : pick);
      if (pet) pet.lines = [{ at: Date.now() / 1000, text: d.line }, ...(pet.lines || [])];
      renderBeepnik();
    } else if (b) b.textContent = r.status === 429 ? "Give him a moment" : "No answer";
  } catch (e) { if (b) b.textContent = "No answer"; }
  setTimeout(() => { if (typeof pollPet === "function") pollPet(); }, 1500);
  setTimeout(() => { if (tvTab === "beepnik" && !overlay) renderBeepView(); }, 4000);
}

function renderBeepView() {
  const st = pet, v = document.getElementById("view-beepnik");
  if (!st || !v || !window.Probe) return;
  const lk = looks(st), face = faceFor(st), n = st.needs || {}, md = st.mood_detail || {}, o = st.orbit || {};
  const low = (k) => (st.low || []).includes(k);
  const scene = el("div", { class: "tvscene" });
  scene.innerHTML = `<svg viewBox="20 20 280 200" preserveAspectRatio="xMidYMid slice">${Probe.companionMarkup({ orbit: o.id, tier: st.signal.draw_tier, traits: (st.traits || []).map((t) => t.id), egg: !st.hatched, face, ...lk, layers: "all" })}</svg>`;
  const latest = (st.lines || [])[0];
  const say = el("div", { class: "say" }, el("p", { text: latest ? latest.text : "Standing by." }),
    el("small", { text: `${(st.name || "Beepnik").toUpperCase()}${md.phrase ? ` \u00b7 ${md.phrase.toUpperCase()}` : ""}` }));
  const talkBtns = el("div", { class: "tvbtns" },
    btn("beep:ask", "\u2726 Ask for a record", () => talk("/api/beepnik/ask", "beep:ask"), "tvbtn gold", "he picks one from your cargo"),
    btn("beep:how", "How are you?", () => talk("/api/beepnik/howareyou", "beep:how"), "tvbtn", "ask how he's doing"));
  const left = [scene, say, talkBtns];
  if (pick) {
    const pc = el("button", { class: "tvpick", "data-f": "beep:pick", "data-hint": "open the record" });
    pc.innerHTML = (pick.cover ? `<img src="${esc(pick.cover)}" alt="">` : '<span class="ph"></span>') +
      `<span><small>HIS PICK</small><b>${esc(pick.title)}</b><em>${esc(pick.artist)}${pick.year ? `, ${esc(pick.year)}` : ""}</em></span>`;
    pc.addEventListener("click", () => openRecord(pick.release_id));
    left.push(pc);
  } else if (story && story.journey && story.journey.length) {
    const all = story.journey, shown = all.slice(0, 3), origin = all[all.length - 1];
    if (!shown.includes(origin)) shown.push(origin);
    left.push(el("div", { class: "tvjourney" }, el("span", { class: "lbl", text: "JOURNEY" }),
      ...shown.map((it) => el("div", { class: `jr jg-${it.group}` }, el("time", { text: shortDay(it.at) }), el("i"), el("b", { text: it.title })))));
  }
  const fuel = Math.round(n.fuel ?? 0), hold = o.hold_level ?? 30, burn = o.burn_line ?? 60;
  const [, vs] = gaugeSvg(n.variety ?? 0, "variety"), [, cs] = gaugeSvg(n.curiosity ?? 0, "curiosity"), [, ts] = gaugeSvg(n.taste ?? 0, "taste");
  const vd = st.variety || {}, cd = (st.curiosity || {}).detail || {}, td = st.taste_detail || {};
  const needs = el("div", { class: "tvneeds" },
    needCard("need:mood", beepHead(st, "mface"), (st.mood || "").replace(/^./, (c) => c.toUpperCase()), "", md.flavor ? `${md.joiner} ${md.flavor}` : "", "", openMood),
    needCard("need:fuel", tankSvg(fuel, hold, burn), "Fuel", fuel, fuel >= burn ? "Burn ready" : fuel > hold ? "Holding orbit" : "Can't hold orbit", "", openFuel, "fuel", low("fuel")),
    needCard("need:variety", vs, "Variety", Math.round(n.variety ?? 0), vd.styles_worth ? `${vd.styles_worth} genres` : "", "", openVariety, "variety", low("variety")),
    needCard("need:curiosity", cs, "Curiosity", Math.round(n.curiosity ?? 0), (cd.today || {}).tracks ? "fed today" : "nothing new lately", "", openCuriosity, "curiosity", low("curiosity")),
    needCard("need:taste", ts, "Taste", Math.round(n.taste ?? 0), td.fed >= .35 ? "well fed lately" : td.fed >= .12 ? "some favorites" : "missing favorites", "", openTaste, "taste", low("taste")));
  const lamp = (label, title, sub, extra) => el("div", { class: "tvlamp" }, el("span", { class: "lbl", text: label }), el("b", { text: title }), extra || "", el("small", { text: sub }));
  const days = el("div", { class: "tvdays" }, ...((st.signal || {}).recent || []).map((on) => el("i", { class: on ? "on" : null })));
  const c = st.craving;
  const lamps = el("div", { class: "tvlamps" },
    lamp("ORBIT", o.label || "", st.lost ? "Lost at Point Nemo" : o.slipping ? `Falling: re-entry in about ${Math.round(o.eta_days || 0)} days` :
      `${o.stages ? kmText(kmFor(o.altitude, o)) + " from Earth" : ""}`),
    lamp("SIGNAL", st.signal.label, st.signal.next_label ? `${st.signal.next_label} in ${Math.max(0, st.signal.next_days - st.signal.contact_days)} days` : "Fully equipped", days),
    lamp("CRAVING", c ? c.title : "Resting", c ? (c.savoring ? "Savoring it now" : "Wants it next") : "Content for now"));
  const m = st.mission;
  const mission = m ? el("div", { class: "tvmission" }, el("span", { class: "mtag", text: "MISSION" }), el("b", { text: m.text }),
    el("small", { text: `${Math.floor(m.progress || 0)} of ${m.target} \u00b7 +${m.reward || 25} curiosity` })) : "";
  // achievements: the medals earned, then the nearest ones still to earn
  let ach = "";
  if (story && story.achievements) {
    const list = story.achievements.slice().sort((a, b) => (b.done - a.done) || (b.earned_at || 0) - (a.earned_at || 0) || (b.value / b.target) - (a.value / a.target));
    const next = list.find((a) => !a.done);
    ach = el("div", { class: "tvach", "data-f": "beep:ach", "data-hint": "see them all" });
    ach.innerHTML = `<span class="lbl">ACHIEVEMENTS</span><b>${story.earned} of ${story.total}</b><span class="medals">${list.slice(0, 9).map((a) => medalHtml(a.group, !a.done)).join("")}</span>` +
      (next ? `<small>Next up: <b>${esc(next.name)}</b>, ${next.value} of ${next.target}</small>` : "");
    ach.addEventListener("click", () => openOverlay(`Achievements, ${story.earned} of ${story.total}`,
      el("div", { class: "tvachgrid" }, ...list.map((a) => { const d = el("div", { class: "cell" + (a.done ? "" : " locked") });
        d.innerHTML = `${medalHtml(a.group, !a.done)}<b>${esc(a.name)}</b><small>${a.done ? (a.earned_at ? `earned ${shortDay(a.earned_at)}` : "earned") : `${esc(a.how)}: ${a.value} of ${a.target}`}</small>`; return d; }))));
  }
  // the transmission log fills the rest of the column, newest line at the bottom like a terminal
  const name = (st.name || "Beepnik").toUpperCase();
  const lines = [...(st.lines || []).map((l) => ({ at: l.at, voice: "beepnik", text: l.text })), ...(st.transmissions || [])]
    .sort((a, b) => a.at - b.at).slice(-24);
  const t = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const log = el("div", { class: "tvlog", "data-f": "beep:log", "data-hint": "open the full log" },
    el("div", { class: "head" }, el("span", { text: "UPLINK" }), el("small", { text: "transmission log" }), btn("beep:nemo", "Point Nemo", (e) => { e.stopPropagation(); openNemo(); }, "tvbtn small", "open")),
    el("div", { class: "lines" }, ...lines.map((l) => el("div", { class: `ln ${l.voice}` }, el("span", { class: "ts", text: t(l.at) }),
      el("span", { class: "who", text: l.voice === "beepnik" ? name : l.voice === "ground" ? "GROUND" : "SYSTEM" }), el("span", { class: "tx", text: l.text })))));
  log.addEventListener("click", openTerminal);
  v.replaceChildren(el("div", { class: "tvcols" }, el("div", { class: "left" }, ...left), el("div", { class: "right beep" }, needs, lamps, mission, ach, log)));
  restoreFocus();
}

function openVariety() {
  const d = pet.variety || {};
  openOverlay(`Variety ${Math.round(pet.needs.variety ?? 0)}`,
    el("h4", { text: `Genres \u00b7 ${d.styles_worth || 0} genres' worth` }), ...(d.styles || []).map(([n, s], i) => row(n, `${Math.round(s * 100)}%`, s, ["#afa0eb", "#ffa366", "#6694ff", "#ffd16f", "#ff6f91", "#88e3bf", "#7aa2ff", "#5a4c87"][i % 8])),
    el("h4", { text: `Eras \u00b7 ${d.eras_worth || 0} decades' worth` }), ...(d.eras || []).map(([n, s]) => row(n, `${Math.round(s * 100)}%`, s, "#5c8dff")),
    line("How evenly your recent listening is spread across genres and decades; the last few days count most."));
}
function openCuriosity() {
  const d = (pet.curiosity || {}).detail || {};
  openOverlay(`Curiosity ${Math.round(pet.needs.curiosity ?? 0)}`,
    el("h4", { text: "Discoveries, last 3 days" }), ...((d.rows || []).length ? d.rows.map((r) => row(r.text, `+${r.points}`, Math.min(1, r.points / 40), "#88e3bf")) : [line("Nothing new in the last three days.")]),
    ...((d.drains || []).length ? [el("h4", { text: "Drained by familiar records" }), ...d.drains.map((r) => row(r.title, String(r.points), Math.min(1, -r.points / 20), "#ff668a"))] : []),
)
}
function openTaste() {
  const d = pet.taste_detail || {};
  const items = [el("h4", { text: d.learned ? "Its favorites, learned from your listening" : "Its favorites" }),
    ...(d.favorites || []).map((f) => row(f.name, `${Math.round(f.share * 100)}%`, f.share, "#ff6f91"))];
  if (d.suggestion) items.push(el("h4", { text: "Would please it" }), btn("ov:suggest", `${d.suggestion.title}, ${d.suggestion.artist}`, () => { closeOverlay(); openRecord(d.suggestion.release_id); }, "tvbtn wide", "open the record"));
  openOverlay(`Taste ${Math.round(pet.needs.taste ?? 0)}`, ...items);
}
function openMood() {
  const md = pet.mood_detail || {};
  openOverlay(md.phrase || "Mood",
    el("h4", { text: "What's pulling on it" }), ...(md.pulls || []).slice().reverse().map((p) => row(`${p.need.replace(/^./, (c) => c.toUpperCase())} ${p.value}`, `${p.pull >= 0 ? "+" : ""}${Math.round(p.pull)}`, p.value / 100, p.pull >= 0 ? "#88e3bf" : "#ff668a")),
    ...(md.effect ? [el("h4", { text: "What it's doing about it" }), line(md.effect)] : []));
}
function openFuel() {
  const n = pet.needs || {}, o = pet.orbit || {};
  openOverlay(`Fuel ${Math.round(n.fuel ?? 0)}`,
    line(`Below ${Math.round(o.hold_level ?? 30)} it can't hold its orbit and starts to fall. Above ${Math.round(o.burn_line ?? 60)}, a side played through fires a burn and climbs.`),
    line("Any listening fills the tank; silence drains it slowly."));
}
function openTerminal() {
  const name = (pet.name || "Beepnik").toUpperCase();
  const all = [...(pet.lines || []).map((l) => ({ at: l.at, voice: "beepnik", text: l.text })), ...(pet.transmissions || [])].sort((a, b) => b.at - a.at).slice(0, 30);
  const t = (at) => new Date(at * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  openOverlay("Transmission log", el("div", { class: "tvterm" }, ...all.map((l, i) => el("div", { class: `ln ${l.voice}`, "data-f": `term:${i}`, "data-hint": "scroll" },
    el("span", { class: "ts", text: t(l.at) }), el("span", { class: "who", text: l.voice === "beepnik" ? name : l.voice === "ground" ? "GROUND CTRL" : "SYSTEM" }), el("span", { class: "tx", text: l.text })))));
}
async function openNemo() {
  let mem = [];
  try { mem = (await getJSON("/api/memorials")).memorials || []; } catch (e) { }
  openOverlay("Point Nemo", ...(mem.length ? mem.slice(0, 8).map((m, i) => el("div", { class: "tvnemo", "data-f": `nemo:${i}`, "data-hint": "scroll" },
    el("b", { text: `${m.name || "Beepnik"}` }), el("small", { text: [m.best_orbit, m.last_words ? `\u201c${m.last_words}\u201d` : ""].filter(Boolean).join(" \u00b7 ") }))) : [line("No probes lost yet.")]),
)
}

/* --- Log ------------------------------------------------------------------------------------------- */
async function loadLog() {
  try {
    const [h, r, g] = await Promise.all([getJSON("/api/history?limit=300"), getJSON("/api/recordings"), getJSON("/api/grid?days=30&hours=1")]);
    hist = h; recs = r.recordings || []; gridData = g;
  } catch (e) { }
  renderLogView();
}
function radarSvg() {
  // the same radar as the main page: each day a spoke (today at the top, going back anticlockwise),
  // the hour of day the distance from the center (midnight at the rim), from hourly blocks
  if (!gridData) return "";
  const days = gridData.days || [], n = days.length, RC = 235, RMAX = 205;
  const xy = (d, hr) => { const a = -Math.PI / 2 + ((n - 1 - d) / n) * Math.PI * 2, rr = 26 + (hr / 23) * (RMAX - 30); return [RC + Math.cos(a) * rr, RC + Math.sin(a) * rr]; };
  let out = `<defs><filter id="tvrbl" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4"/></filter></defs>`;
  for (let k = 1; k <= 4; k++) out += `<circle cx="${RC}" cy="${RC}" r="${RMAX * k / 4}" fill="none" stroke="#312965" stroke-width="1"/>`;
  for (let d = 0; d < n; d++) {
    const a = -Math.PI / 2 + ((n - 1 - d) / n) * Math.PI * 2;
    out += `<line x1="${RC}" y1="${RC}" x2="${RC + Math.cos(a) * RMAX}" y2="${RC + Math.sin(a) * RMAX}" stroke="#251f4c" stroke-width="${d === n - 1 ? 1.6 : .7}"/>`;
  }
  days.forEach((day, d) => (day.blocks || []).forEach((secs, hr) => {
    if (secs < 60) return;
    const s = 2.5 + Math.min(60, secs / 60) / 18, [x, y] = xy(d, hr);
    out += `<circle cx="${x}" cy="${y}" r="${s * 1.8}" fill="#ffd57b" opacity=".35" filter="url(#tvrbl)"/><circle cx="${x}" cy="${y}" r="${s}" fill="#ffe4ab"/>`;
  }));
  const lab = (t, rr) => `<text x="${RC + 6}" y="${RC - rr - 4}" font-family="Nunito" font-weight="800" font-size="12" fill="#ffbc2a">${t}</text>`;
  out += lab("6 AM", RMAX * .25) + lab("noon", RMAX * .5) + lab("6 PM", RMAX * .75) + lab("midnight", RMAX);
  out += `<text x="${RC}" y="18" text-anchor="middle" font-family="Righteous" font-size="14" fill="#ffdd94">TODAY</text>`;
  return `<svg viewBox="0 0 470 470"><circle cx="${RC}" cy="${RC}" r="${RMAX + 20}" fill="#0c0a19"/>${out}</svg>`;
}
function sessionCard(s, i) {
  const recsFor = recs.filter((r) => r.started_at < s.end + 60 && r.ended_at > s.start - 60).sort((a, b) => a.started_at - b.started_at);
  const albums = [];
  for (const p of s.plays || []) {
    const key = p.release_id || p.album;
    let a = albums.find((x) => x.key === key);
    if (!a) { a = { key, album: p.album || p.title, artist: p.artist, cover: p.cover, rid: p.release_id, n: 0 }; albums.push(a); }
    a.n++;
  }
  const day = new Date(s.start * 1000), t = (x) => new Date(x * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const c = el("div", { class: "tvsess", "data-f": `sess:${s.start}`, "data-hint": recsFor.length ? "play the session" : "open the record" },
    el("div", { class: "sh" }, el("b", { text: day.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }) }),
      recsFor.length ? el("span", { class: "playpill", text: "\u25B6 Play" }) : ""),
    el("small", { text: `${t(s.start)} to ${t(s.end)} \u00b7 ${albums.length} ${albums.length === 1 ? "record" : "records"} \u00b7 ${Math.round(s.seconds / 60)} min` }),
    ...albums.slice(0, 3).map((a) => el("div", { class: "rec" }, a.cover ? el("img", { src: a.cover, alt: "" }) : el("span", { class: "ph" }),
      el("div", {}, el("b", { text: a.album }), el("small", { text: `${a.artist} \u00b7 ${a.n} ${a.n === 1 ? "track" : "tracks"}` })))));
  c.addEventListener("click", () => {
    if (recsFor.length) playSession(recsFor, s);
    else if (albums[0] && albums[0].rid) openRecord(albums[0].rid);
  });
  return c;
}
function renderLogView() {
  const v = document.getElementById("view-log");
  if (!v) return;
  const sum = (gridData || {}).summary || {}, sty = (pet || {}).stylus;
  const hr = (h) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" });
  const stats = el("div", { class: "tvstats" },
    el("div", {}, el("b", { text: sum.prime_start != null ? `${hr(sum.prime_start)} to ${hr((sum.prime_start + 3) % 24)}` : "-" }), el("small", { text: "prime time" })),
    el("div", {}, el("b", { text: sum.busiest || "-" }), el("small", { text: "busiest day" })),
    el("div", {}, el("b", { text: `${sum.days_with || 0} of 30` }), el("small", { text: "days with a spin" })),
    el("div", {}, el("b", { text: sum.longest ? `${Math.floor(sum.longest / 3600)} h ${Math.round((sum.longest % 3600) / 60)} min` : "-" }), el("small", { text: "longest day" })));
  const radar = el("div", { class: "tvradar" }); radar.innerHTML = radarSvg();
  const left = el("div", { class: "left" }, radar, stats,
    sty ? el("div", { class: "tvstylus" }, el("span", { class: "lbl", text: "STYLUS" }), el("b", { text: `${Math.round(sty.hours)} of ${sty.rated} h` })) : "");
  const sessions = ((hist || {}).sessions || []).map(sessionCard);
  v.replaceChildren(el("div", { class: "tvcols log" }, left, el("div", { class: "right vscroll" }, ...(sessions.length ? sessions : [line("No listening logged yet.")]))));
  restoreFocus();
}

/* --- Cargo ----------------------------------------------------------------------------------------- */
async function loadCargo(reset) {
  if (cargo.loading) return;
  cargo.loading = true;
  if (reset) cargo.items = [];
  try {
    const q = new URLSearchParams({ sort: cargo.sort, limit: "30", offset: String(cargo.items.length) });
    if (cargo.q) q.set("q", cargo.q);
    if (cargo.unplayed) q.set("unplayed", "1");
    const d = await getJSON(`/api/collection?${q}`);
    cargo.items = cargo.items.concat(d.items || []);
    cargo.total = d.total || cargo.items.length;
  } catch (e) { }
  cargo.loading = false;
  renderCargoView();
}
function loadMoreCargo() { if (cargo.items.length < cargo.total) loadCargo(false); }
function runSearch() { const i = document.getElementById("tvq"); if (i && i.value !== cargo.q) { cargo.q = i.value.trim(); loadCargo(true); } }
function renderCargoView() {
  const v = document.getElementById("view-cargo");
  if (!v) return;
  const keepScroll = v.querySelector(".vscroll") ? v.querySelector(".vscroll").scrollTop : 0;
  const search = el("input", { id: "tvq", class: "tvsearch", type: "search", "data-f": "cargo:search", "data-hint": "type to search", placeholder: `Search ${cargo.total} records`, value: cargo.q });
  const sorts = [["recent", "Recently played"], ["added", "Recently added"], ["plays", "Most spun"], ["dusty", "Dusty"], ["artist", "Artist"], ["title", "Title"], ["year", "Year"]];
  const chips = sorts.map(([k, label]) => {
    const b = btn(`cargo:sort:${k}`, label, () => { cargo.sort = k; loadCargo(true); }, `tvchip${cargo.sort === k ? " on" : ""}`, "sort");
    return b;
  });
  chips.push(btn("cargo:unplayed", "Never played", () => { cargo.unplayed = !cargo.unplayed; loadCargo(true); }, `tvchip${cargo.unplayed ? " on" : ""}`, "filter"));
  const grid = el("div", { class: "tvgrid" }, ...cargo.items.map((r, i) => {
    const c = el("div", { class: "tvsleeve", "data-f": `rec:${r.id}`, "data-hint": "open the record" },
      r.cover ? el("img", { src: r.cover, alt: "", loading: "lazy" }) : el("span", { class: "ph" }),
      el("b", { text: r.title }), el("small", { text: r.artist }), el("small", { class: "c-gold", text: r.spins ? `${r.spins} ${r.spins === 1 ? "spin" : "spins"}` : "never played" }));
    if (i >= cargo.items.length - 5) c.dataset.more = "1";
    c.addEventListener("click", () => openRecord(r.id));
    return c;
  }));
  v.replaceChildren(el("div", { class: "tvcargo" }, el("div", { class: "tools" }, search, ...chips), el("div", { class: "vscroll" }, grid)));
  v.querySelector(".vscroll").scrollTop = keepScroll;
  restoreFocus();
}

/* A record, as on the web: your history with it, Beepnik's verdict, both sides with their plays,
   the pressings, Ground Control's dossier, and the records it's often spun with. All by remote. */
async function openRecord(id) {
  let r;
  try { r = await getJSON(`/api/release/${id}`); } catch (e) { return; }
  const rec = r.recorded || {}, tracks = r.tracks || [];
  const recorded = tracks.filter((t) => rec[t.position]);
  const parts = recorded.map((t) => ({ url: rec[t.position].url, seconds: rec[t.position].seconds,
    marks: [{ offset: 0, title: t.title, position: t.position, artist: r.artist, album: r.title, cover: r.cover }] }));
  const day = (ts) => { const d = new Date(ts * 1000); return d.toDateString() === new Date().toDateString() ? "Today" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" }); };
  const listened = (sec) => sec >= 3600 ? `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} m` : `${Math.max(1, Math.round(sec / 60))} min`;

  // the top: cover; who and what; history tiles; his verdict
  const tiles = el("div", { class: "rv-tiles" });
  const tile = (a, b) => el("div", { class: "rv-tile" }, el("b", { text: a }), el("small", { text: b }));
  if (r.spins) tiles.append(tile(String(r.spins), r.spins === 1 ? "spin" : "spins"), tile(String(r.plays), "tracks heard"),
                           tile(day(r.last_played), "last played"), tile(listened(r.listened || 0), "listened"));
  else tiles.append(el("div", { class: "rv-tile wide" }, el("b", { text: "Not played here yet" }), el("small", { text: "Waiting for its turn" })));
  if (r.verdict && pet && pet.enabled) {
    const v = el("div", { class: `rv-verdict tone-${r.verdict.tone}` });
    v.innerHTML = beepHead(pet, "mface");
    v.append(el("div", {}, el("small", { text: "BEEPNIK'S VERDICT" }), el("b", { text: r.verdict.title }), el("em", { text: r.verdict.why })));
    tiles.append(v);
  }
  const top = el("div", { class: "rv-top" }, r.cover ? el("img", { src: r.cover, alt: "" }) : el("span", { class: "ph" }),
    el("div", { class: "rv-head" }, el("small", { class: "rv-eye", text: [r.in_collection ? "IN YOUR CARGO" : "NOT IN YOUR COLLECTION", r.genres && r.genres[0] && `${r.genres[0].toUpperCase()} CONSTELLATION`].filter(Boolean).join(" \u00b7 ") }),
      el("b", { class: "big", text: r.title }), el("b", { class: "rv-artist", text: r.artist }),
      el("small", { text: [r.label, r.year, r.format].filter(Boolean).join(" \u00b7 ") }), tiles));
  const items = [top];

  // what you can do: play, and the pressings
  const acts = el("div", { class: "rv-acts" });
  if (recorded.length) acts.append(btn("rec:playall", "\u25B6 Play album", () => playParts(parts, 0, r.title), "tvbtn gold", "play it on the Now stage"));
  acts.append(el("small", { class: "rv-note", text: recorded.length ? `Recorded: ${recorded.length} of ${tracks.length} tracks` : "Not recorded yet: tracks save as they play through" }));
  items.push(acts);
  if ((r.pressings || []).length > 1) {
    const row = el("div", { class: "rv-press" }, el("small", { class: "rv-k", text: `YOUR COPIES \u00b7 ${r.pressings.length} PRESSINGS \u00b7 PLAYS COUNT TOWARD THE ALBUM` }));
    const list = el("div", { class: "rv-presslist" });
    for (const pr of r.pressings) {
      const b = btn(`rec:press:${pr.id}`, "", () => { if (!pr.current) openRecord(pr.id); }, "rv-pressing" + (pr.current ? " current" : ""), pr.current ? "the one you're viewing" : "view this pressing");
      b.innerHTML = (pr.cover ? `<img src="${esc(pr.cover)}" alt="">` : '<span class="ph"></span>') +
        `<span><b>${esc([pr.label, pr.year].filter(Boolean).join(", ") || "Pressing")}</b><small>${esc([pr.format, pr.catno].filter(Boolean).join(" \u00b7 ") || " ")}</small></span>`;
      list.append(b);
    }
    row.append(list);
    items.push(row);
  }

  // both sides, with their plays; recorded tracks play from where you pick
  const groups = new Map();
  for (const t of tracks) { const k = t.side || "Tracks"; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(t); }
  const most = Math.max(1, ...tracks.map((t) => t.plays || 0));
  const sides = el("div", { class: "rv-sides" });
  for (const [side, ts] of groups) {
    const col = el("div", { class: "rv-side" }, el("small", { class: "rv-k", text: side === "Tracks" ? "TRACKS" : `SIDE ${side}` }));
    for (const t of ts) {
      const k = recorded.indexOf(t);
      const row = el("div", { class: `tvtrack rv-track${k >= 0 ? " rec" : ""}`, ...(k >= 0 ? { "data-f": `trk:${t.position}`, "data-hint": "play from here" } : {}) },
        el("span", { class: "p", text: k >= 0 ? "\u25B6" : "" }), el("span", { class: "pos", text: t.position }), el("span", { class: "t", text: t.title }));
      if (t.plays) { const bar = el("span", { class: "rv-bar" }, el("i")); bar.firstChild.style.width = `${Math.round((100 * t.plays) / most)}%`; row.append(bar, el("span", { class: "n", text: `${t.plays}\u00d7` })); }
      else row.append(el("span", { class: "rv-bar none" }), el("span", { class: "n", text: t.duration || "" }));
      if (k >= 0) row.addEventListener("click", () => playParts(parts, k, r.title));
      col.append(row);
    }
    sides.append(col);
  }
  items.push(sides);

  // Ground Control's dossier
  const dd = r.dossier || {};
  if (dd.credits || dd.companies || dd.matrix || dd.notes) {
    const lines = [];
    for (const c of dd.companies || []) lines.push(`${c.type} ${c.name}`);
    for (const c of dd.credits || []) lines.push(`${c.role}: ${c.name}`);
    for (const m of dd.matrix || []) lines.push(`Matrix${m.description ? ` (${m.description})` : ""}: ${m.value}`);
    items.push(el("div", { class: "rv-dossier" }, el("small", { class: "rv-k sky", text: "GROUND CONTROL DOSSIER" }), el("p", { text: lines.join(" \u00b7 ") }),
      dd.notes ? el("p", { class: "notes", text: dd.notes }) : ""));
  }

  // your history with it, and its company
  const hist = el("div", { class: "rv-hist" });
  if (r.spins && r.weeks) {
    const peak = Math.max(1, ...r.weeks);
    const bars = el("div", { class: "rv-weeks" }, ...r.weeks.map((w) => { const b = el("i", { class: w ? "on" : null }); b.style.height = `${w ? 18 + (82 * w) / peak : 8}%`; return b; }));
    hist.append(el("div", { class: "rv-box" }, el("small", { class: "rv-k", text: "SPINS OVER TIME \u00b7 LAST 12 WEEKS" }), bars));
  }
  if ((r.often_with || []).length) {
    const list = el("div", { class: "rv-often" });
    for (const o of r.often_with) {
      const b = btn(`rec:with:${o.id}`, "", () => openRecord(o.id), "rv-with", "open this record");
      b.innerHTML = (o.cover ? `<img src="${esc(o.cover)}" alt="">` : '<span class="ph"></span>') + `<small>${o.times} ${o.times === 1 ? "time" : "times"}</small>`;
      b.title = `${o.title}, ${o.artist}`;
      list.append(b);
    }
    hist.append(el("div", { class: "rv-box" }, el("small", { class: "rv-k", text: "OFTEN SPUN WITH \u00b7 SAME SITTING" }), list));
  }
  if (hist.children.length) items.push(hist);

  openOverlay("Record", ...items);
  const card = $("tvover").querySelector(".tvcard");
  card.classList.add("rv");
  if (r.cover) card.style.setProperty("--wash", `url("${r.cover}")`);
  setFocus(document.querySelector('#tvover [data-f="rec:playall"]') || focusables()[0] || null);
}

/* --- Stars ----------------------------------------------------------------------------------------- */
async function loadStars() {
  if (!chartRecs) { try { chartRecs = (await getJSON("/api/chart")).records || []; } catch (e) { chartRecs = []; } }
  renderStarsView();
}
function renderStarsView() {
  const v = document.getElementById("view-stars");
  if (!v || !window.StarChart || !chartRecs) return;
  const S = StarChart, counts = {};
  for (const r of chartRecs) counts[r.sector] = (counts[r.sector] || 0) + 1;
  const chips = [btn("stars:all", "All", () => { chartView = null; renderStarsView(); }, `tvchip${chartView ? "" : " on"}`, "show all")];
  for (const [k, s] of Object.entries(S.SECTORS)) {
    if (!counts[k]) continue;
    chips.push(btn(`stars:${k}`, `${s.label} ${counts[k]}`, () => { chartView = k; renderStarsView(); }, `tvchip${chartView === k ? " on" : ""}`, "zoom in"));
  }
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "tvchart");
  let view = null;
  if (chartView && S.SECTORS[chartView]) { const s = S.SECTORS[chartView]; view = [s.x - 380, s.y - 223, 760, 446]; }
  S.draw(svg, chartRecs, { view: view || undefined, pxWidth: 1760 });
  v.replaceChildren(el("div", { class: "tvstars" }, el("div", { class: "tools" }, ...chips), svg));
  restoreFocus();
}

/* --- playback: sessions and albums, on the TV ------------------------------------------------------- */
let play = null;           // { parts, i, label }
/* Playback on the TV: it takes over the Now stage (the cover, the track, the tracks, the controls);
   on the other tabs a compact bar keeps the controls at hand. */
window.tvReplaying = () => !!play;
function playParts(parts, i, label, at = 0) {
  const live = document.getElementById("tv-audio");
  if (live && !live.paused) live.pause();
  play = { parts, i, label, marks: buildMarks(parts) };
  const a = $("tv-rec");
  a.src = parts[i].url;
  a.currentTime = at;
  a.play().catch(() => {});
  if (overlay) closeOverlay();
  showTab("now");
  if (typeof renderNow === "function") renderNow();
  renderPlayBar(true);
  setFocus(document.querySelector('#rp-ctrls [data-f="play:toggle"]'));
}
/* The track list fits the space above the needs row: rows come off the end until it clears,
   never the current track (a long title or artist, or the turntable note, take room). */
function fitReplay() {
  const list = $("rp-tracks"), needs = $("needs");
  if (!list || !needs || !list.children.length) return;
  const limit = needs.getBoundingClientRect().top - 18;
  while (list.children.length > 1 && list.getBoundingClientRect().bottom > limit) {
    const last = list.lastElementChild;
    if (last.classList.contains("now")) { list.firstElementChild.remove(); continue; }
    last.remove();
  }
  list.hidden = list.getBoundingClientRect().bottom > limit;       // not even one row fits: show none
}
window.tvFitReplay = () => { const l = $("rp-tracks"); if (l) l.dataset.key = ""; if (play) renderPlayBar(false); };

/* every track across the parts, with where it starts in the whole playback */
function buildMarks(parts) {
  const out = [];
  let base = 0;
  parts.forEach((p, k) => {
    const ms = (p.marks && p.marks.length) ? p.marks : [{ offset: 0, title: p.title || "" }];
    for (const m of ms) out.push({ ...m, part: k, at: base + (m.offset || 0) });
    base += p.seconds || 0;
  });
  return out;
}
const partStart = (k) => play.parts.slice(0, k).reduce((t, p) => t + (p.seconds || 0), 0);
function playTime() { return play ? partStart(play.i) + ($("tv-rec").currentTime || 0) : 0; }
function seekTo(T) {
  if (!play) return;
  T = Math.max(0, T);
  let k = 0;
  while (k + 1 < play.parts.length && T >= partStart(k + 1)) k++;
  const a = $("tv-rec"), off = T - partStart(k);
  if (k !== play.i) { play.i = k; a.src = play.parts[k].url; a.addEventListener("loadedmetadata", () => { a.currentTime = off; }, { once: true }); }
  else a.currentTime = off;
  a.play().catch(() => {});
  renderPlayBar(true);
}
function markIndex(T) { let idx = 0; play.marks.forEach((m, i) => { if (T >= m.at - 1) idx = i; }); return idx; }
function prevTrack() { const T = playTime(), i = markIndex(T), m = play.marks[i]; seekTo(m && T - m.at > 4 ? m.at : (i > 0 ? play.marks[i - 1].at : 0)); }
function nextTrack() { const i = markIndex(playTime()); if (i + 1 < play.marks.length) seekTo(play.marks[i + 1].at); else stopPlay(); }
function playSession(recordings, s) {
  playParts(recordings.map((r) => ({ url: r.url, seconds: r.seconds, marks: r.marks || [] })), 0, `Session, ${new Date(s.start * 1000).toLocaleDateString([], { weekday: "long" })}`);
}
function nextPart() {
  if (!play) return;
  if (play.i + 1 < play.parts.length) { play.i++; const a = $("tv-rec"); a.src = play.parts[play.i].url; a.play().catch(() => {}); renderPlayBar(true); }
  else stopPlay();
}
function togglePlay() { const a = $("tv-rec"); if (!play) return; if (a.paused) a.play().catch(() => {}); else a.pause(); renderPlayBar(true); }
function stopPlay() {
  const a = $("tv-rec");
  a.pause(); a.removeAttribute("src"); a.load();
  play = null;
  $("tvplay").hidden = true;
  const live = document.getElementById("tv-audio");
  if (live && live.getAttribute("src")) live.play().catch(() => {});        // back to the turntable
  if (typeof coverSrc !== "undefined") coverSrc = undefined;                 // the Now stage redraws its own cover
  if (typeof renderNow === "function") renderNow();
  const c = current();
  if (!c || c.closest("#tvplay, #replay")) setFocus(document.querySelector(`#tvtabs [data-tab="${tvTab}"]`));
}
function renderPlayBar(rebuild) {
  if (!play) return;
  const a = $("tv-rec"), T = playTime(), i = markIndex(T), m = play.marks[i] || {};
  const total = play.parts.reduce((t, p) => t + (p.seconds || 0), 0) || a.duration || 1;
  const cover = m.cover || (play.marks.find((x) => x.cover) || {}).cover || null;
  const sub = [m.album, m.artist].filter(Boolean).join(", ");
  // the compact bar, on the other tabs
  const bar = $("tvplay");
  bar.hidden = tvTab === "now";
  if (rebuild || !bar.firstChild) {
    bar.replaceChildren(el("img", { id: "tvp-cover", alt: "" }), el("div", { class: "tt" }, el("b", { id: "tvp-title" }), el("small", { id: "tvp-sub" })),
      el("div", { class: "prog" }, el("b", { id: "tvp-fill" })),
      btn("bar:toggle", "", togglePlay, "tvbtn round", "play or pause"), btn("bar:stop", "\u25A0", stopPlay, "tvbtn round", "stop and go back to the turntable"));
  }
  $("tvp-title").textContent = m.title || play.label;
  $("tvp-sub").textContent = sub || play.label;
  const cov = $("tvp-cover");
  if (cover) { if (cov.getAttribute("src") !== cover) cov.src = cover; cov.hidden = false; } else cov.hidden = true;
  $("tvp-fill").style.width = `${Math.min(100, (100 * T) / total)}%`;
  // the Now stage
  const rc = $("rp-cover");
  if (rc && rc.dataset.src !== (cover || "")) {
    rc.dataset.src = cover || "";
    rc.innerHTML = cover ? "" : `<div class="vinyl">${typeof VINYL !== "undefined" ? VINYL : ""}</div>`;
    if (cover) rc.append(el("img", { src: cover, alt: "" }));
    const wash = $("wash");
    if (wash) { wash.style.backgroundImage = cover ? `url("${cover}")` : ""; wash.classList.toggle("on", !!cover); }
  }
  if ($("rp-title")) {
    $("rp-eyebrow").textContent = `PLAYING BACK \u00b7 ${play.label.toUpperCase()}`;
    const t = $("rp-title"); t.textContent = m.title || play.label;
    t.className = (t.textContent.length > 26) ? "longer" : t.textContent.length > 16 ? "long" : "";
    $("rp-artist").textContent = m.artist || "";
    $("rp-album").textContent = m.album || "";
    $("rp-fill").style.width = `${Math.min(100, (100 * T) / total)}%`;
    $("rp-pos").textContent = fmt(T);
    $("rp-track").textContent = play.marks.length > 1 ? `Track ${i + 1} of ${play.marks.length}` : "";
    $("rp-left").textContent = `-${fmt(total - T)}`;
    const list = $("rp-tracks"), from = Math.max(0, Math.min(i - 1, play.marks.length - 5));
    const key = `${play.marks.length}:${from}:${i}:${$("rp-live").hidden}`;
    if (list.dataset.key !== key) {
      list.dataset.key = key;
      list.replaceChildren(...play.marks.slice(from, from + 5).map((x, k) => el("div", { class: "rp-row" + (from + k === i ? " now" : from + k < i ? " done" : "") },
        el("span", { class: "n", text: x.position || String(from + k + 1) }), el("span", { class: "t", text: x.title || "" }), el("span", { class: "at", text: fmt(x.at) }))));
      fitReplay();
    }
    const ctr = $("rp-ctrls");
    if (rebuild || !ctr.firstChild) {
      const key0 = current() && current().dataset.f;
      ctr.replaceChildren(btn("play:prev", "\u23EE", prevTrack, "tvbtn round", "previous track"),
        btn("play:toggle", "", togglePlay, "tvbtn round big", "play or pause"),
        btn("play:next", "\u23ED", nextTrack, "tvbtn round", "next track"),
        btn("play:stop", "\u25A0  Stop", stopPlay, "tvbtn", "stop and go back to the turntable"));
      if (key0 && key0.startsWith("play:")) setFocus(document.querySelector(`#rp-ctrls [data-f="${key0}"]`));
    }
  }
  for (const tg of document.querySelectorAll('[data-f="play:toggle"], [data-f="bar:toggle"]')) tg.textContent = a.paused ? "\u25B6" : "\u275A\u275A";
}

/* --- wiring into the TV view ------------------------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
  if (window.TV_CAST) return;                      // the Cast receiver has no remote: just the Now view
  setTimeout(() => {
    buildTabs();
    showTab("now");
    document.addEventListener("keydown", onKey);
    setInterval(idleCheck, 3000);
    loadStory(); setInterval(loadStory, 120000);
    const rb = window.renderBeepnik;
    if (typeof rb === "function") window.renderBeepnik = function () { rb.apply(this, arguments); if (tvTab === "beepnik" && !overlay) renderBeepView(); };
  }, 0);
});
