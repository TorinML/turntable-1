"use strict";

const POLL_MS = 5000;
const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);

/* ---------- helpers ---------- */

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

function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

const timeFmt = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" });
const dayFmt = new Intl.DateTimeFormat([], { weekday: "long", month: "long", day: "numeric" });
const monthDay = new Intl.DateTimeFormat([], { month: "short", day: "numeric" });
const clock = (ts) => timeFmt.format(new Date(ts * 1000));

function duration(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return "under a minute";
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  if (h === 0) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

function daysAgo(ts) {
  const a = new Date(ts * 1000); a.setHours(0, 0, 0, 0);
  const b = new Date(); b.setHours(0, 0, 0, 0);
  return Math.round((b - a) / 86400000);
}

function dayLabel(ts) {
  const n = daysAgo(ts);
  if (n === 0) return "Today";
  if (n === 1) return "Yesterday";
  return dayFmt.format(new Date(ts * 1000));
}

function art(cover) {
  return cover
    ? el("img", { class: "art", src: cover, alt: "", loading: "lazy" })
    : el("span", { class: "art", "aria-hidden": "true" });
}


function ago(ts) {
  const days = Math.floor((Date.now() / 1000 - ts) / 86400);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 45) return `${days} days ago`;
  const months = Math.round(days / 30);
  if (months < 18) return `${months} months ago`;
  return `${Math.round(days / 365)} years ago`;
}


/* ---------- record dialog ---------- */

/* A record: its cover's colours washed behind it; your history with it; each track with its plays and,
   where recorded, a play button; Beepnik's verdict; Ground Control's dossier; and the records it's
   often spun with. */
const listenedText = (sec) => sec >= 3600 ? `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} m` : `${Math.max(1, Math.round(sec / 60))} min`;
const dayText = (ts) => { const d = new Date(ts * 1000); return d.toDateString() === new Date().toDateString() ? "Today"
  : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() !== new Date().getFullYear() ? "numeric" : undefined }); };

async function openRecord(id) {
  const dlg = $("record-dlg"), body = $("dlg-body");
  body.replaceChildren(el("p", { class: "note", text: "Pulling it from the cargo hold..." }));
  dlg.style.removeProperty("--wash");
  if (!dlg.open) dlg.showModal();
  let r;
  try {
    r = await getJSON(`/api/release/${id}`);
  } catch {
    body.replaceChildren(el("p", { class: "empty", text: "Couldn't load that record. Try again in a moment." }));
    return;
  }
  if (r.cover) dlg.style.setProperty("--wash", `url("${r.cover}")`);

  // --- the recordings: tracks saved from your listening play here, as one album in the player
  const rec = r.recorded || {};
  const recTracks = r.tracks.filter((t) => rec[t.position]);
  const playAlbum = (fromPos) => {
    let at = 0;
    const parts = recTracks.map((t) => {
      const p = { id: `lib-${r.id}-${t.position}`, url: rec[t.position].url, started_at: at, ended_at: at + rec[t.position].seconds,
        seconds: rec[t.position].seconds,
        marks: [{ offset: 0, length: rec[t.position].seconds, title: t.title, position: t.position, artist: r.artist, album: r.title, cover: r.cover }] };
      at += rec[t.position].seconds;
      return p;
    });
    const start = fromPos ? parts.find((p) => p.marks[0].position === fromPos) : null;
    $("record-dlg").close();
    openDeck(parts, start ? start.started_at : 0, r.title);
  };

  // --- left: the cover, what you can do with it, and its details
  const acts = el("div", { class: "rd-acts" });
  if (recTracks.length) {
    const b = el("button", { type: "button", class: "nb-btn primary", text: "\u25B6 Play album" });
    b.addEventListener("click", () => playAlbum(null));
    acts.append(b);
  }
  acts.append(el("a", { class: "nb-btn", href: r.discogs_url, target: "_blank", rel: "noopener noreferrer", text: "View on Discogs" }));
  const recNote = el("p", { class: "rd-note", text: recTracks.length
    ? `Recorded: ${recTracks.length} of ${r.tracks.length} ${r.tracks.length === 1 ? "track" : "tracks"}` + (recTracks.length < r.tracks.length ? ". The rest record the next time they play through." : ", the whole record.")
    : "Not recorded yet: each track is saved the first time it plays through in full." });
  const dd = r.dossier || {};
  const facts = [["Genre", r.genres.join(", ")], ["Styles", r.styles.join(", ")], ["Country", dd.country],
    ["Released", dd.released && dd.released.length >= 4 ? dd.released : r.year], ["Label", r.label && (r.catno ? `${r.label}, ${r.catno}` : r.label)],
    ["Format", r.format], ["Added", r.date_added ? dayText(Date.parse(r.date_added) / 1000) : null],
    ["First spin", r.first_played ? ago(r.first_played) : null]].filter(([, v]) => v);
  const details = el("div", { class: "nb-sub rd-details" }, el("span", { class: "nb-k", text: "Details" }),
    el("dl", {}, ...facts.flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })])));
  const left = el("div", { class: "rd-left" }, cover(r.cover, "rd-cover"), acts, recNote, details);

  // --- right: who and what, your history, his verdict, the tracks
  const yr = r.year && r.pressing_year ? `${r.year} (this pressing: ${r.pressing_year})` : r.year;
  const where = r.in_collection ? "In your cargo" : "Not in your collection";
  const head = el("div", { class: "rd-head" },
    el("div", { class: "nb-k", text: [where, r.genres[0] && `${r.genres[0]} constellation`].filter(Boolean).join(" \u00b7 ") }),
    el("h2", { id: "dlg-title", text: r.title }), el("p", { class: "rd-artist", text: r.artist }),
    el("p", { class: "rd-meta", text: [r.label, yr, r.format].filter(Boolean).join(" \u00b7 ") }));
  const tile = (a, b) => el("div", { class: "nb-sub rd-tile" }, el("b", { text: a }), el("span", { text: b }));
  const stats = el("div", { class: "rd-stats" });
  if (r.spins) stats.append(tile(String(r.spins), r.spins === 1 ? "spin" : "spins"), tile(String(r.plays), r.plays === 1 ? "track heard" : "tracks heard"),
                            tile(dayText(r.last_played), "last played"), tile(listenedText(r.listened || 0), "listened"));
  else stats.append(el("div", { class: "nb-sub rd-tile wide" }, el("b", { text: "Not played here yet" }), el("span", { text: "This one's waiting for its turn." })));
  if (r.verdict) {
    const v = el("div", { class: `nb-sub rd-verdict tone-${r.verdict.tone}` });
    const pic = el("span", { class: "rd-verdict-pic" });
    if (lastPet && lastPet.enabled) pic.append(beepFace(lastPet));
    v.append(pic, el("div", {}, el("span", { class: "nb-k", text: "Beepnik's verdict" }), el("b", { text: r.verdict.title }), el("small", { text: r.verdict.why })));
    stats.append(v);
  }

  const groups = new Map();
  for (const t of r.tracks) { const k = t.side || "Tracks"; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(t); }
  const most = Math.max(1, ...r.tracks.map((t) => t.plays || 0));
  const sides = el("div", { class: "rd-sides" });
  for (const [side, tracks] of groups) {
    const box = el("div", { class: "rd-side" }, el("div", { class: "nb-k", text: side === "Tracks" ? "Tracks" : `Side ${side}` }));
    for (const t of tracks) {
      const play = rec[t.position]
        ? el("button", { type: "button", class: "trk-play", "aria-label": `Play ${t.title} from the recording`, text: "\u25B6" })
        : el("span", { class: "trk-play none", "aria-hidden": "true" });
      if (rec[t.position]) play.addEventListener("click", () => playAlbum(t.position));
      const bar = el("div", { class: "nb-bar taste" }, el("i"));
      bar.firstChild.style.width = `${Math.round((100 * (t.plays || 0)) / most)}%`;
      const n = el("span", { class: "n", text: t.plays ? `${t.plays}\u00d7` : (t.duration || ""),
        title: t.duration_source ? `Length from ${{ "discogs-master": "another Discogs version", musicbrainz: "MusicBrainz", itunes: "iTunes" }[t.duration_source] || t.duration_source}` : null });
      box.append(el("div", { class: "rd-track" + (t.plays ? " played" : "") + (rec[t.position] ? " recorded" : "") }, play,
        el("span", { class: "pos", text: t.position }), el("span", { class: "t", text: t.title }), t.plays ? bar : el("span"), n));
    }
    sides.append(box);
  }
  if (r.tracks.length === 0) sides.append(el("p", { class: "note", text: "The tracklist arrives with the next Discogs sync." }));

  const right = el("div", { class: "rd-right" }, head, stats);
  if ((r.pressings || []).length > 1) {
    const row = el("div", { class: "rd-pressings" });
    for (const pr of r.pressings) {
      const b = el("button", { type: "button", class: "rd-pressing" + (pr.current ? " current" : ""), "aria-current": pr.current ? "true" : null,
        title: pr.current ? "The pressing you're viewing" : "View this pressing" },
        cover(pr.cover), el("span", {}, el("b", { text: [pr.label, pr.year].filter(Boolean).join(", ") || "Pressing" }),
          el("small", { text: [pr.format, pr.catno].filter(Boolean).join(" \u00b7 ") || "\u00a0" })));
      if (!pr.current) b.addEventListener("click", () => openRecord(pr.id));
      row.append(b);
    }
    right.append(el("div", { class: "rd-press" }, el("div", { class: "nb-lead" }, el("span", { class: "nb-k", text: `Your copies \u00b7 ${r.pressings.length} pressings` }),
      el("small", { text: "plays count toward the album, whichever copy is on" })), row));
  }
  right.append(sides);

  // --- Ground Control's dossier, when it has reported on this record
  if (dd.credits || dd.companies || dd.matrix || dd.notes) {
    const lines = [];
    for (const c of dd.companies || []) lines.push(`${c.type} ${c.name}`);
    for (const c of dd.credits || []) lines.push(`${c.role}: ${c.name}`);
    for (const m of dd.matrix || []) lines.push(`Matrix${m.description ? ` (${m.description})` : ""}: ${m.value}`);
    const box = el("div", { class: "nb-tape rd-dossier" }, el("span", { class: "nb-k", text: "Ground Control dossier" }), el("p", { text: lines.join(" \u00b7 ") }));
    if (dd.notes) box.append(el("p", { class: "rd-notes", text: dd.notes }));
    right.append(box);
  }

  // --- your history with it, and its company
  const hist = [];
  if (r.spins) {
    const peak = Math.max(1, ...r.weeks);
    const bars = el("div", { class: "rd-weeks" }, ...r.weeks.map((w, i) => { const b = el("i", { class: w ? "on" : null, title: `${w} ${w === 1 ? "spin" : "spins"}, ${11 - i === 0 ? "this week" : `${11 - i} ${11 - i === 1 ? "week" : "weeks"} ago`}` });
      b.style.height = `${w ? 18 + (82 * w) / peak : 8}%`; return b; }));
    hist.push(el("div", { class: "nb-sub" }, el("div", { class: "nb-lead" }, el("span", { class: "nb-k", text: "Spins over time" }), el("small", { text: "last 12 weeks" })), bars));
  }
  if ((r.often_with || []).length) {
    const list = el("div", { class: "rd-often" });
    for (const o of r.often_with) {
      const b = el("button", { type: "button", title: `${o.title}, ${o.artist}` }, cover(o.cover), el("small", { text: `${o.times} ${o.times === 1 ? "time" : "times"}` }));
      b.addEventListener("click", () => openRecord(o.id));
      list.append(b);
    }
    hist.push(el("div", { class: "nb-sub" }, el("div", { class: "nb-lead" }, el("span", { class: "nb-k", text: "Often spun with" }), el("small", { text: "same sitting" })), list));
  }
  if (hist.length) right.append(el("div", { class: "rd-hist" + (hist.length === 1 ? " one" : "") }, ...hist));

  body.replaceChildren(el("div", { class: "rd" }, left, right));
  body.scrollTop = 0;
}


/* ---------- cosmic logbook ---------- */

let chart;

function setupChart() {
  chart = StarChart.mount($("chart"), { tip: $("chart-tip"), onOpen: (id) => openRecord(id) });
  $("chart-in").addEventListener("click", () => chart.zoom(1.6));
  $("chart-out").addEventListener("click", () => chart.zoom(1 / 1.6));
  $("chart-reset").addEventListener("click", () => chart.reset());
  $("chart-path").addEventListener("click", (e) => {
    const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
    e.currentTarget.setAttribute("aria-pressed", String(on));
    chart.setFullPath(on);
  });
  let t;
  $("chart-q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => chart.setQuery(e.target.value), 200); });
}

async function loadChart() {
  const data = await getJSON("/api/chart");
  const today = data.charted_today || {};
  const bits = [];
  if (today.planets) bits.push(`${today.planets} new ${today.planets === 1 ? "planet" : "planets"}`);
  if (today.systems) bits.push(`${today.systems} new ${today.systems === 1 ? "system" : "systems"}`);
  $("logbook-summary").textContent = data.total
    ? `${data.charted} of ${data.total} systems charted` + (bits.length ? `; today, ${bits.join(" and ")} (in gold)` : "")
    : "The chart fills in once your Discogs collection has synced.";
  // genre chips for the constellations present, most populated first
  const counts = {};
  for (const r of data.records) counts[r.sector] = (counts[r.sector] || 0) + 1;
  const box = $("chart-sectors");
  if (!box.dataset.built || box.dataset.built !== Object.keys(counts).sort().join()) {
    box.replaceChildren();
    const active = new Set(Object.keys(counts));
    for (const k of Object.keys(counts).sort((a, b) => counts[b] - counts[a])) {
      const sec = StarChart.SECTORS[k] || StarChart.SECTORS.other;
      const dot = el("i"); dot.style.background = sec.color;
      const b = el("button", { type: "button", "aria-pressed": "true" }, dot, document.createTextNode(`${sec.label} ${counts[k]}`));
      b.addEventListener("click", () => {
        const on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(on));
        if (on) active.add(k); else active.delete(k);
        chart.setSectors(active.size === Object.keys(counts).length ? null : new Set(active));
      });
      box.append(b);
    }
    box.dataset.built = Object.keys(counts).sort().join();
  }
  chart.setRecords(data.records);
}


/* ---------- listening ---------- */

function setupListen() {
  const btn = $("listen"), player = $("player"), note = $("listen-note");
  // In the Turntable-1 Android app, its own player plays the stream, so it keeps going in the
  // background with notification and lock-screen controls. The app reports back to keep this in step.
  if (window.TurntableApp) {
    window.turntableAppListening = (on, msg) => {
      btn.setAttribute("aria-pressed", String(on));
      $("listen-text").textContent = on ? "Stop listening" : "Listen live";
      note.textContent = msg || (on ? "Playing in the app: it keeps going in the background." : "");
    };
    btn.addEventListener("click", () => window.TurntableApp.listen(btn.getAttribute("aria-pressed") !== "true"));
    return;
  }
  const stop = (msg) => {
    player.pause(); player.removeAttribute("src"); player.load();
    btn.setAttribute("aria-pressed", "false"); $("listen-text").textContent = "Listen live";
    note.textContent = msg || "";
  };
  btn.addEventListener("click", async () => {
    if (btn.getAttribute("aria-pressed") === "true") return stop();
    player.src = `/stream?t=${Date.now()}`;     // fresh URL joins the live stream
    note.textContent = "Tuning in. Audio runs a few seconds behind the record.";
    try {
      await player.play();
      btn.setAttribute("aria-pressed", "true");
      $("listen-text").textContent = "Stop listening";
    } catch {
      stop("The stream didn't start. Check that the turntable is switched on, then try again.");
    }
  });
  player.addEventListener("error", () => {
    if (btn.getAttribute("aria-pressed") === "true") stop("The stream stopped. Check that the turntable is switched on, then try again.");
  });
}


/* ---------- shared bits ---------- */

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function cover(src, cls = "") {
  return src ? el("img", { src, alt: "", loading: "lazy", class: cls || null })
             : el("span", { class: `blank ${cls}`.trim(), "aria-hidden": "true" });
}

function mmss(sec) {
  sec = Math.max(0, Math.round(sec));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function relTime(ts) {
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 24 * 60) return clock(ts);
  return dayLabel(ts);
}

const lc = (t) => (t ? t.charAt(0).toLowerCase() + t.slice(1) : t);   // "Low Earth orbit" keeps its Earth

/* ---------- now playing ---------- */

let side = null;          // the current side, with a local clock so progress moves between polls
let lastPet = null;

function drawGrooves() {
  const g = $("grooves");
  for (let r = 52; r < 120; r += 3) g.append(svg("circle", { cx: 135, cy: 135, r }));
}

function setMotion(running) {
  running = true;   // the orbit rider is hidden while idle, and the parked Beepnik should keep blinking
  const stage = $("stage");
  if (!stage.pauseAnimations) return;
  if (running && !reducedMotion.matches) stage.unpauseAnimations(); else stage.pauseAnimations();
}

// Redraw Beepnik only when something you'd see has changed. Redrawing restarts the idle
// animations (blink, glance, float), which would make him jump back mid-movement.
const drawn = { rider: "", critter: "" };

function drawRider() {
  const st = lastPet;
  const face = !st || !st.enabled ? "sleep"
    : st.activity === "sleeping" ? "sleep" : faceFor(st);
  const opts = st && st.enabled
    ? { orbit: st.orbit.id, tier: st.signal.draw_tier, traits: st.traits.map((t) => t.id), egg: !st.hatched, face, layers: "probe", ...looks(st) }
    : { orbit: "leo", tier: 0, face, layers: "probe" };
  const parked = { ...opts, face: $("hero").classList.contains("collapsed") ? "sleep" : opts.face };
  const key = JSON.stringify([opts, parked]);
  if (key === drawn.rider) return;
  drawn.rider = key;
  $("beep-rider").innerHTML = Probe.companionMarkup(opts);
  $("beep-parked").innerHTML = Probe.companionMarkup(parked);
}

// Earned looks: mission patches, weathering by age (stepped at 30 and 90 days so it rarely redraws)
// and the generation decal.
function looks(st) {
  const c = st.cosmetics || {};
  const age = (c.age_days || 0) > 90 ? 120 : (c.age_days || 0) > 30 ? 45 : 0;
  return { patches: c.patches || 0, age, generation: c.generation || st.generation || 1 };
}

function drawCritter(opts) {
  const key = JSON.stringify(opts);
  if (key === drawn.critter) return;
  drawn.critter = key;
  Probe.companion($("critter"), opts);
}

/* The disc's label shows the turntable's record, except while a recording plays back in the card:
   then the turntable's cover is kept aside for when playback stops. */
function setLiveLabel(href) {
  if ($("hero").classList.contains("replay")) { replayLiveHref = href || null; return; }
  const label = $("label-art");
  if (href) label.setAttribute("href", href); else label.removeAttribute("href");
}
let lastNowData = null;
function renderNow(data) {
  lastNowData = data;
  if (deck) updateReplayLive();
  const { state, current: cur } = data;
  $("hero").dataset.state = state;
  // Ground Control: across the card while a record plays; its line arrives moments after each track starts
  const gcOn = state === "playing" && !!cur;
  $("gc").hidden = !gcOn;
  if (gcOn) runTicker("gc-ticker", "gc-tape", "", cur.ground ? cur.ground.text : "Acquiring the track, stand by.", lastPet && lastPet.panel);
  $("onair").dataset.state = state;
  $("onair-text").textContent = state === "idle" ? "Standby" : "Now spinning";
  setMotion(state !== "idle");

  const label = $("label-art");
  const line = $("crate-line");
  $("tag-now").hidden = state !== "unknown";
  $("not-this").hidden = !(state === "playing" && cur && !cur.manual);
  $("timeline").hidden = true;
  if ($("readouts")) $("readouts").hidden = true;
  $("idlecards").hidden = true;
  $("flip").textContent = "";
  side = null;

  if (state === "playing" && cur) {
    const rel = cur.release;
    $("when").textContent = cur.inferred ? `Probably on the turntable since ${clock(cur.started_at)}`
                                         : `On the turntable since ${clock(cur.started_at)}`;
    $("title").textContent = cur.title;
    $("artist").textContent = cur.artist;
    const bits = rel ? [rel.title, rel.year, rel.label].filter(Boolean) : [cur.album].filter(Boolean);
    $("album-line").textContent = bits.length ? bits.join(", ") + "." : "";
    if (rel) {
      line.textContent = cur.side ? `Side ${cur.side}, from your cargo` : "From your cargo";
      line.dataset.release = rel.id;
      line.hidden = false;
    } else line.hidden = true;
    setLiveLabel(cur.cover);
    document.title = `${cur.title} by ${cur.artist} | Turntable-1`;
    if (cur.side_view) {
      side = { ...cur.side_view, at: Date.now() / 1000 };
      drawSide();
    }
  } else if (state === "unknown") {
    $("when").textContent = "On the turntable now";
    $("title").textContent = "Playing something unrecognized";
    $("artist").textContent = "";
    $("album-line").textContent = "Shazam couldn't name this one yet. It keeps listening and fills this in if it finds a match.";
    line.hidden = true;
    setLiveLabel(null);
    document.title = "Turntable-1";
  } else {
    $("when").textContent = "Nothing on the platter";
    $("title").textContent = "Ready when you are";
    $("artist").textContent = "";
    $("album-line").textContent = "";
    line.hidden = true;
    setLiveLabel(null);
    document.title = "Turntable-1";
    $("idlecards").hidden = false;
  }
  updateCollapse();
}

function drawSide() {
  if (!side) return;
  const tl = $("timeline");
  tl.hidden = false;
  const segs = $("segs"), labels = $("seglabels");
  segs.replaceChildren(); labels.replaceChildren();
  const known = side.durations;
  for (const t of side.tracks) {
    const flex = known ? t.secs : 1;
    const seg = el("i", { class: t.state, title: t.title });
    seg.style.flex = String(flex);
    if (t.state === "now") seg.append(el("b", { id: "now-fill" }));
    segs.append(seg);
    const lab = el("span", { class: t.state === "now" ? "now" : null });
    lab.style.flex = String(flex);
    lab.textContent = `${t.position} ${t.title}` + (t.state === "probable" ? " (probable)" : "");
    if (t.state === "now") lab.id = "now-label";
    labels.append(lab);
  }
  tickSide();
}

function tickSide() {
  if (!side) return;
  const t = side.tracks[side.index];
  const elapsed = Math.min(t.secs || Infinity, side.elapsed + (Date.now() / 1000 - side.at));
  const fill = $("now-fill"), lab = $("now-label");
  if (fill && t.secs) fill.style.width = `${Math.min(100, (100 * elapsed) / t.secs)}%`;
  if (lab) lab.textContent = `${t.position} ${t.title}`;
  const flip = $("flip"), name = side.side ? `side ${side.side}` : "this side";
  const left = side.side_left != null ? Math.max(0, side.side_left - (Date.now() / 1000 - side.at)) : null;
  // the readouts: time into the track, time left on the side, and where we are on it
  const ro = $("readouts"), items = [];
  if (t.secs) items.push([mmss(elapsed), `of ${mmss(t.secs)}`]);
  if (left != null) items.push([mmss(left), `left on ${name}`]);
  items.push([`${side.index + 1} of ${side.count}`, side.side ? `track on side ${side.side}` : "track"]);
  ro.hidden = false;
  ro.replaceChildren(...items.map(([a, b]) => el("div", {}, el("b", { text: a }), el("span", { text: b }))));
  flip.classList.toggle("soon", !!(side.last_on_side && left != null));
  if (side.last_on_side && left != null)
    flip.replaceChildren(document.createTextNode(`Last track on ${name}. `), el("b", { text: `Flip in about ${Math.max(1, Math.round(left / 60))} min.` }));
  else flip.textContent = "";
}

let lastSummary = null;

function renderIdleCards(sm) {
  if (sm) lastSummary = sm;
  sm = lastSummary;
  if (!sm) return;
  const pet = lastPet;
  const box = $("idlecards");
  box.replaceChildren();
  const card = (title, body, onClick) => {
    const c = el(onClick ? "button" : "div", { class: "idlecard", type: onClick ? "button" : null }, el("h5", { text: title }), body);
    if (onClick) c.addEventListener("click", onClick);
    box.append(c);
  };
  const ls = sm.last_spin;
  card("Last spin", ls ? el("p", { text: ls.title }, el("span", { text: [ls.how ? ls.how.charAt(0).toUpperCase() + ls.how.slice(1) : "", ago(ls.end)].filter(Boolean).join(", ") }))
                      : el("p", { text: "Nothing yet" }, el("span", { text: "Your first record will show here." })),
       ls && ls.release_id ? () => openRecord(ls.release_id) : null);
  // Beepnik's craving
  const c = pet && pet.enabled && !pet.lost ? pet.craving : null;
  const name = pet && pet.name ? pet.name : "Beepnik";
  if (c) {
    const hrs = Math.max(0, (c.expires_at - Date.now() / 1000) / 3600);
    const sub = c.savoring ? "Savoring it now" : hrs >= 1 ? `Gives up in ${Math.round(hrs)} h` : `Gives up in ${Math.max(1, Math.round(hrs * 60))} min`;
    card(`${name} is craving`, el("div", { class: "mini" }, cover(c.cover), el("p", { text: c.title }, el("span", { text: sub }))),
         () => openRecord(c.release_id));
  } else {
    card(`${name}`, el("p", { text: pet && pet.lost ? "Lost in space" : "Not craving anything" },
         el("span", { text: pet && pet.lost ? "Play a record to launch the next probe." : "Content, for now." })));
  }
  const w = sm.waiting;
  card("Waiting in the cargo hold", w ? el("div", { class: "mini" }, cover(w.cover),
         el("p", { text: w.title }, el("span", { text: w.last_played ? `Not spun in ${ago(w.last_played).replace(" ago", "")}` : "Never played here" })))
                                 : el("p", { text: "Nothing gathering dust" }),
       w ? () => openRecord(w.id) : null);
  // the mission
  const m = pet && !pet.lost ? pet.mission : null;
  if (m) {
    const hrs = Math.max(0, (m.expires_at - Date.now() / 1000) / 3600);
    const done = Math.min(m.target, Math.floor(m.progress || 0));
    const prog = m.target <= 6 ? el("div", { class: "pips" }, ...Array.from({ length: m.target }, (_, i) => el("i", { class: i < done ? "done" : null })))
                               : el("div", { class: "mbar" }, el("b"));
    if (m.target > 6) prog.firstChild.style.width = `${Math.min(100, (100 * done) / m.target)}%`;
    card("Mission", el("p", { text: m.text },
         el("span", { text: `${done} of ${m.target}${m.target > 6 ? " min" : ""}, ${hrs >= 1 ? Math.round(hrs) + " h" : Math.max(1, Math.round(hrs * 60)) + " min"} left` }), prog));
  } else {
    const rest = pet ? (pet.mission_rest || 0) / 3600 : 0;
    card("Mission", el("p", { text: "Between missions" },
         el("span", { text: rest > 0.05 ? `The next one arrives in ${rest >= 1 ? Math.round(rest) + " h" : Math.max(1, Math.round(rest * 60)) + " min"}.` : "The next one arrives any minute." })));
  }
  renderShelf();             // the shelf skips what these cards show, so it follows them
}

/* ---------- the idle card ---------- */
// While nothing plays, the status says how long it's been quiet, and a shelf offers a few records
// worth pulling out (never played first, then the longest since a spin; they rotate daily).

function idleSince() {
  const ls = lastSummary && lastSummary.last_spin;
  return ls ? ls.end : null;
}

// kept under its old name: the page calls it whenever the state or the summary changes
function updateCollapse() {
  const hero = $("hero");
  hero.classList.remove("collapsed");
  if (hero.dataset.state !== "idle") return;
  const since = idleSince();
  let quiet = "";
  if (since) {
    const h = (Date.now() / 1000 - since) / 3600;
    quiet = h >= 48 ? ` for ${Math.round(h / 24)} days` : h >= 1 ? ` for ${Math.round(h)} h` : h * 60 >= 5 ? ` for ${Math.round(h * 60)} min` : "";
  }
  $("when").textContent = "Nothing on the platter" + quiet;
  renderShelf();
}

let shelf = { at: 0, items: null };
async function renderShelf() {
  const box = $("idle-shelf");
  if (!box) return;
  if (!shelf.items || Date.now() - shelf.at > 10 * 60 * 1000) {
    shelf.at = Date.now();
    try { shelf.items = (await getJSON("/api/dusty?limit=8")).items || []; } catch (e) { shelf.items = shelf.items || []; }
  }
  // never a record the cards above already show (the one waiting in the hold, and Beepnik's craving)
  const shown = new Set([lastSummary && lastSummary.waiting && lastSummary.waiting.id,
                         lastPet && lastPet.craving && lastPet.craving.release_id].filter(Boolean));
  const items = shelf.items.filter((r) => !shown.has(r.id)).slice(0, 5);
  const idle = $("hero").dataset.state === "idle";
  box.hidden = !idle || !items.length;
  if (box.hidden) return;
  const key = items.map((r) => r.id).join(",");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(el("div", { class: "shelf-head" }, el("span", { class: "nb-k", text: "Pull one out" }),
                                                         el("small", { text: "from your cargo, waiting for a spin" })),
    el("div", { class: "shelf-row" }, ...items.map((r) => {
      const b = el("button", { type: "button", class: "shelf-item", title: r.story || `${r.title}, ${r.artist}` },
        cover(r.cover), el("b", { text: r.title }), el("small", { text: r.artist }));
      b.addEventListener("click", () => openRecord(r.id));
      return b;
    })));
}

function setupCollapse() {
  setInterval(updateCollapse, 60 * 1000);
}

/* ---------- tonight strip ---------- */

let topDays = 30;

function renderTonight(sm) {
  const box = $("tonight");
  box.replaceChildren();
  const t = sm.tonight;
  if (!t) {
    box.append(el("div", {}, el("p", { text: "No records tonight" }), el("small", { text: "Tonight's records gather here." })));
    return;
  }
  const stack = el("div", { class: "stack" });
  for (const r of t.records.slice(0, 4)) {
    const c = cover(r.cover);
    c.title = `${r.title}, ${r.artist}`;
    if (r.release_id) c.addEventListener("click", () => openRecord(r.release_id));
    stack.append(c);
  }
  const n = t.records.length;
  const label = daysAgo(t.since) === 0 && new Date(t.since * 1000).getHours() >= 17 ? "Tonight" : daysAgo(t.since) === 0 ? "Today" : "Last session";
  box.append(stack, el("div", {}, el("p", { text: `${label}: ${n} ${n === 1 ? "record" : "records"}` }),
    el("small", { text: `${duration(t.seconds)}, since ${clock(t.since)}` })));
}

let topRows = null;
async function loadTop() {
  const s = await getJSON(`/api/stats?days=${topDays}`);
  topRows = s.top_artists;
  fitChips();
}
/* Show the chips that fit whole, then "+N more"; run again whenever the strip's width changes
   (fonts arriving, the grid resizing), so no chip is ever left half visible. */
function fitChips() {
  const box = $("chips"), rows = topRows;
  if (!rows) return;
  box.querySelectorAll(".chip").forEach((c) => c.remove());
  if (!rows.length) { box.append(el("span", { class: "chip more-chip", text: "Nothing played in this period" })); return; }
  for (const r of rows) box.append(el("span", { class: "chip", title: `${r.spins} spins, ${duration(r.seconds)}` },
    document.createTextNode(r.name), el("em", { text: String(r.spins) })));
  const limit = box.getBoundingClientRect().right;
  const chips = [...box.querySelectorAll(".chip")];
  let hidden = 0;
  for (const c of chips) if (c.getBoundingClientRect().right > limit - 2) { c.remove(); hidden++; }
  if (hidden) {
    const more = el("span", { class: "chip more-chip", text: `+${hidden} more` });
    box.append(more);
    while (box.querySelectorAll(".chip:not(.more-chip)").length && more.getBoundingClientRect().right > limit - 2) {
      const all = box.querySelectorAll(".chip:not(.more-chip)");
      all[all.length - 1].remove();
      more.textContent = `+${++hidden} more`;
    }
  }
}
if (window.ResizeObserver) {
  let lastW = 0, pending = 0;
  const ro = new ResizeObserver((entries) => {
    const w = Math.round(entries[0].contentRect.width);
    if (w === lastW) return;                 // only a real change of width
    lastW = w;
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(fitChips);
  });
  document.addEventListener("DOMContentLoaded", () => { if ($("chips")) ro.observe($("chips")); });
}
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => fitChips());

async function loadSummary() {
  const sm = await getJSON("/api/summary");
  renderTonight(sm);
  renderIdleCards(sm);
  updateCollapse();
}

/* ---------- Beepnik ---------- */

const NEED_INFO = {
  fuel: ["Fuel", "any listening"],
  variety: ["Variety", "how mixed, lately"],
  curiosity: ["Curiosity", "new tracks, sides, records"],
  taste: ["Taste", "styles it loves"],
};

function faceFor(state) {
  if (state.activity === "sleeping") return "sleep";
  const s = state.mood_score;
  return s >= 85 ? "ecstatic" : s >= 65 ? "happy" : s >= 45 ? "content" : s >= 25 ? "restless" : "glum";
}

/* Each need has its own colour, wherever it appears: fuel gold, variety sky, curiosity comet pink,
   taste lilac. */
const NEED_HEX = { fuel: "#ffcf6b", variety: "#7fa7ff", curiosity: "#ff7a9c", taste: "#c3b8ee" };

/* A dial: a 240-degree arc filled to the value, the number in the middle, and optional tick marks. */
function dialSvg(value, color, marks = []) {
  const cx = 60, cy = 62, r = 44, a0 = -210, span = 240;
  const pt = (v, rr) => { const a = ((a0 + (span * v) / 100) * Math.PI) / 180; return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]; };
  const arc = (v) => { const [x0, y0] = pt(0, r), [x1, y1] = pt(v, r);
    return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${(span * v) / 100 > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`; };
  const v = Math.max(0, Math.min(100, value));
  return `<svg viewBox="0 0 120 96" aria-hidden="true"><path d="${arc(100)}" fill="none" stroke="#2c255f" stroke-width="10" stroke-linecap="round"/>` +
    (v >= 1 ? `<path d="${arc(v)}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"/>` : "") +
    marks.map((m) => { const [x1, y1] = pt(m.v, r + 8), [x2, y2] = pt(m.v, r - 8);
      return `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" stroke="${m.color}" stroke-width="2.5" stroke-linecap="round"><title>${m.title}</title></line>`; }).join("") +
    `<text x="60" y="70" text-anchor="middle" font-family="Righteous, sans-serif" font-size="27" fill="#f4eefe">${Math.round(v)}</text></svg>`;
}

/* A need, as a card: its dial, its value, what's driving it, and the week's sparkline (drawn once
   Beepnik's story loads). The callers append their extras under it (variety's mix bars, curiosity's
   tallies, taste's favourites) and make it tappable for its breakdown, as before. */
function gauge(value, label, sub, low, danger = false, marks = []) {
  const need = label.toLowerCase();
  const d = el("div", { class: "gauge need" + (low ? " low" : "") + (danger ? " danger" : ""), role: "meter", "aria-label": label,
    "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(value), "data-need": need });
  const dial = el("div", { class: "need-dial" });
  dial.innerHTML = dialSvg(value, NEED_HEX[need] || "#ffcf6b", marks);
  d.append(dial, el("div", { class: "need-body" },
    el("b", {}, document.createTextNode(label), low ? el("i", { class: "need-low", text: "LOW" }) : null, el("em", { text: String(Math.round(value)) })),
    el("span", { text: sub }),
    el("div", { class: "need-spark", "data-need": need })));
  return d;
}

/* a scrolling readout (Ground Control's): a steady reading pace, then a short gap before it comes round again */
const tapes = {};
function runTicker(boxId, tapeId, who, text, panel) {
  const box = $(boxId), tape = $(tapeId), t = tapes[tapeId] || (tapes[tapeId] = { text: null, anim: null });
  if (text === t.text && t.anim) return;
  t.text = text;
  tape.replaceChildren(...(who ? [el("span", { class: "who", text: who }), el("span", { class: "sep", text: "\u25B8" })] : []), document.createTextNode(text));
  if (t.anim) t.anim.cancel();
  const speed = (panel && panel.ticker_speed) || 70, gapPx = ((panel && panel.ticker_gap_seconds) ?? 2) * speed;
  const boxW = box.clientWidth, textW = tape.scrollWidth;
  if (reducedMotion.matches || !boxW) { tape.style.transform = "translate(16px,-50%)"; t.anim = null; return; }
  t.anim = tape.animate([{ transform: `translate(${boxW}px,-50%)` }, { transform: `translate(${-textW - gapPx}px,-50%)` }],
                        { duration: (boxW + textW + gapPx) / speed * 1000, iterations: Infinity, easing: "linear" });
}

/* Beepnik speaks in a bubble whose tail points at him; its waveform ripples when a new line arrives
   and follows the music's level while a record plays. */
let sayText = null, sayTimer = null;
function sayLine(text) {
  const wave = $("say-wave");
  if (!wave.children.length) {
    [6, 12, 18, 9, 15, 20, 11, 7, 16, 19, 10, 5, 13, 17, 8, 14, 20, 9, 6, 12, 18, 11, 7, 15].forEach((h, i) => {
      const b = el("i"); b.style.setProperty("--h", String(h)); b.style.setProperty("--d", `${(i % 6) * 0.09}s`); wave.append(b);
    });
  }
  aimSay();
  if (text === sayText) return;
  const fresh = sayText !== null;
  sayText = text;
  $("say-text").textContent = text;
  if ($("m-say-text")) $("m-say-text").textContent = text;
  if (fresh && !reducedMotion.matches) {
    $("say").classList.add("speaking");
    clearTimeout(sayTimer);
    sayTimer = setTimeout(() => $("say").classList.remove("speaking"), 3200);
  }
}
function aimSay() {
  const say = $("say"), pic = $("critter");
  if (!say || !pic) return;
  const a = say.getBoundingClientRect(), c = pic.getBoundingClientRect();
  if (a.width) say.style.setProperty("--tail", `${Math.max(24, Math.min(a.width - 48, c.left + c.width / 2 - a.left - 12))}px`);
}

function lamp(children, onClick) {
  const l = el(onClick ? "button" : "div", { class: "lamp", type: onClick ? "button" : null }, ...children);
  if (onClick) l.addEventListener("click", onClick);
  return l;
}

function renderPet(st) {
  lastPet = st;
  renderIdleCards();
  drawRider();
  renderNowBeep(st);
  renderTraits(st);
  const panel = $("companion");
  if (!st.enabled) {
    drawCritter({ orbit: "pad", egg: true, face: "sleep" });
    $("pet-name").textContent = "Your companion";
    $("pet-meta").textContent = "Hasn't launched yet. Once the pet service starts, it hatches within a minute.";
    sayLine("Standing by for launch.");
    return;
  }
  panel.dataset.activity = st.activity;
  panel.classList.toggle("lost", !!st.lost);
  $("hero").classList.toggle("probe-lost", !!st.lost);
  drawCritter({ orbit: st.orbit.id, tier: st.signal.draw_tier, traits: st.traits.map((t) => t.id), ...looks(st),
                egg: !st.hatched, face: faceFor(st) });
  $("pet-name").textContent = st.name;
  const place = st.hatched ? `${st.signal.label} in ${lc(st.orbit.label)}` : "Egg on the launch pad";
  $("pet-meta").textContent = st.stale ? `${place}. Out of contact: the pet service hasn't checked in for a few minutes.`
    : st.lost ? `Re-entered and splashed down at Point Nemo ${relTime(st.lost.at)}. A new probe launches when you next play a record.`
    : st.danger ? `${place}. Orbit decaying: re-entry in about ${Math.max(1, Math.round((st.orbit.eta_days || 0) * 24))} h unless a record refuels it.`
    : `${place}, feeling ${st.mood}.`;

  const [latest] = st.lines;
  sayLine(latest ? latest.text : "No transmissions yet.");
  $("say-who").textContent = (st.name || "Beepnik").toUpperCase();
  $("quip-time").textContent = latest ? relTime(latest.at) : "waiting";
  $("pet-thought").textContent = st.thought && st.thought !== "nothing in particular" ? `On its mind: ${st.thought}` : "";

  const cl = $("cluster");
  cl.replaceChildren();
  const streak = (st.curiosity || {}).streak || 0;
  for (const [key, [label, what]] of Object.entries(NEED_INFO)) {
    const sub = key === "curiosity" && streak >= 2 ? `discovery streak: ${streak} in a row` : what;
    if (key === "fuel" && st.orbit && st.orbit.hold_level != null) { cl.append(fuelTank(st)); continue; }
    if (key === "variety" && st.variety) { cl.append(varietyGauge(st, label)); continue; }
    if (key === "curiosity" && st.curiosity && st.curiosity.detail) { cl.append(curiosityGauge(st, label)); continue; }
    if (key === "taste" && st.taste_detail) { cl.append(tasteGauge(st, label)); continue; }
    cl.append(gauge(st.needs[key] ?? 0, label, sub, st.low.includes(key)));
  }

  // status lamps
  const lamps = $("lamps");
  lamps.replaceChildren();
  const s = st.mood_score;
  const bulb = el("span", { class: "bulb" });
  const [c1, c2, glow] = s >= 65 ? ["#ffe8b6", "#ffd16f", "rgba(255, 209, 111,.6)"] : s >= 45 ? ["#ffe8b8", "#ffcc5f", "rgba(255, 204, 95,.45)"]
                       : s >= 25 ? ["#ffc29a", "#ffa366", "rgba(255, 163, 102,.5)"] : ["#ffa0b6", "#ff5c82", "rgba(255, 92, 130,.55)"];
  bulb.style.background = `radial-gradient(circle at 35% 30%, ${c1}, ${c2} 45%, #6b3a10)`;
  bulb.style.setProperty("--glow-c", glow);
  lamps.append(st.mood_detail && !st.lost ? moodLamp(st)
    : lamp([bulb, el("div", {}, el("h6", { text: "MOOD" }), el("p", { text: st.mood.charAt(0).toUpperCase() + st.mood.slice(1) }),
                                el("small", { text: `Score ${Math.round(s)} of 100` }))]));

  lamps.append(orbitLamp(st));

  lamps.append(signalLamp(st));

  const c = st.craving;
  if (c) {
    const heard = c.heard_minutes || 0, need = c.satisfy_minutes || 10;
    const savoring = c.savoring || heard > 0;
    const total = 2 * Math.PI * 23;
    let frac, color, sub;
    if (savoring) {
      frac = Math.min(1, heard / need); color = "#ffd16f";
      sub = `${c.savoring ? "Savoring now: " : "Partly savored: "}${Math.min(need, Math.floor(heard))} of ${need} min`;
    } else {
      const left = (c.expires_at - Date.now() / 1000), span = Math.max(1, c.expires_at - c.since);
      frac = Math.max(0, Math.min(1, left / span)); color = frac < .25 ? "#ff5c82" : "#ffd16f";
      const hrs = left / 3600;
      sub = hrs < 1 ? `Patience running low: gives up in ${Math.max(1, Math.round(left / 60))} min` : `Gives up in ${Math.round(hrs)} h`;
    }
    const ring = el("div", { class: "ring" }, cover(c.cover));
    ring.insertAdjacentHTML("afterbegin", `<svg viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="23" fill="none" stroke="#2d2565" stroke-width="4"/>
      <circle cx="26" cy="26" r="23" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round" stroke-dasharray="${(frac * total).toFixed(1)} ${total.toFixed(1)}" transform="rotate(-90 26 26)"/></svg>`);
    lamps.append(lamp([ring, el("div", { class: "grow" }, el("h6", { text: c.savoring ? "SAVORING" : "CRAVING" }), el("p", { text: c.title }), el("small", { text: sub }))],
                      () => openRecord(c.release_id)));
  } else {
    lamps.append(lamp([el("div", { class: "grow" }, el("h6", { text: "CRAVING" }), el("p", { text: "Resting" }),
      el("small", { text: "Content for now. It'll want something again soon." }))]));
  }

  renderMission(st);

  renderTerm(st);
  renderMobileSay(st);
  renderStylus(st.stylus);
  const nemo = $("nemo-open");
  nemo.hidden = false;                       // always there; empty until the first probe is lost
  nemo.textContent = st.memorials ? `Point Nemo (${st.memorials})` : "Point Nemo";
  $("pet-loves").textContent = st.favorites.length ? `${st.favorites_learned ? "Has taken a liking to" : "Loves"} ${st.favorites.join(", ")}.` : "";
  drawSparks();
}

/* Beepnik at a glance, beside Now playing: his scene, his latest line, his four needs, where he is. */
function renderNowBeep(st) {
  if (!$("nowbeep")) return;
  const on = st && st.enabled;
  const opts = on ? { orbit: st.orbit.id, tier: st.signal.draw_tier, traits: st.traits.map((t) => t.id), ...looks(st), egg: !st.hatched, face: faceFor(st) }
                  : { orbit: "pad", egg: true, face: "sleep" };
  const key = JSON.stringify(opts);
  if (key !== drawn.nowbeep) { drawn.nowbeep = key; Probe.companion($("nb-critter"), opts); }
  $("nowbeep").classList.toggle("lost", !!(on && st.lost));
  if (!on) {
    $("nb-say-text").textContent = "Standing by for launch.";
    $("nb-say-who").textContent = "YOUR COMPANION";
    $("nb-dials").replaceChildren(); $("nb-status").replaceChildren(); $("nb-mission").hidden = true;
    return;
  }
  const [latest] = st.lines;
  $("nb-say-text").textContent = latest ? latest.text : "No transmissions yet.";
  const md = st.mood_detail || {};
  $("nb-say-who").textContent = `${(st.name || "Beepnik").toUpperCase()} \u00b7 ${(md.phrase || st.mood || "").toUpperCase()}`;
  const dials = $("nb-dials");
  dials.replaceChildren();
  for (const [need, [label]] of Object.entries(NEED_INFO)) {
    const d = el("div", { class: "nb-dial" + (st.low.includes(need) ? " low" : ""), title: `${label} ${Math.round(st.needs[need] ?? 0)}` });
    d.innerHTML = dialSvg(st.needs[need] ?? 0, NEED_HEX[need]);
    d.append(el("b", { text: label }));
    dials.append(d);
  }
  const o = st.orbit, g = st.signal;
  const where = st.lost ? "Splashed down at Point Nemo" : !st.hatched ? "On the launch pad"
    : o.slipping ? "Falling: play a record" : o.climbing ? "Climbing" : "Holding";
  const left = Math.max(0, (g.next_days || 0) - g.contact_days);
  $("nb-status").replaceChildren(
    el("div", { class: "nb-sub" }, el("span", { class: "nb-k", text: "Orbit" }), el("b", { text: st.hatched ? o.label : "Egg" }),
       el("small", { class: o.slipping || st.lost ? "warn" : null, text: where })),
    el("div", { class: "nb-sub" }, el("span", { class: "nb-k sky", text: "Signal" }), el("b", { text: g.label }),
       el("small", { text: g.next_label ? `${g.next_label} in ${left} ${left === 1 ? "day" : "days"}` : "Fully equipped" })));
  const m = st.mission, box = $("nb-mission");
  box.hidden = !m;
  if (m) box.replaceChildren(el("span", { class: "mission-tag", text: "MISSION" }), el("b", { text: m.text }),
                             el("small", { text: `${Math.floor(m.progress || 0)} of ${m.target}` }));
}

/* His traits: the looks he's earned from what you play. */
function renderTraits(st) {
  const box = $("bp-traits");
  if (!box) return;
  const traits = (st && st.enabled && st.traits) || [];
  box.hidden = !traits.length;
  if (!traits.length) return;
  box.replaceChildren(el("span", { class: "nb-k lilac", text: "Traits \u00b7 earned from your listening" }),
    el("div", { class: "chips" }, ...traits.map((t) => el("span", { class: "nb-chip", text: t.label.replace(/^an? /, "").replace(/^./, (c) => c.toUpperCase()) }))));
}

/* ---------- Beepnik's story: a week of each need, his journey, his achievements ---------- */
let story = null;
async function loadStory() {
  try { story = await getJSON("/api/beepnik"); } catch (e) { return; }
  renderStory();
}

function drawSparks() {
  if (!story || !story.history || story.history.length < 2) return;
  for (const box of document.querySelectorAll(".need-spark")) {
    const need = box.dataset.need;
    box.replaceChildren(sparkline(story.history.map((h) => [h.at, h[need] ?? 0]), 200, 36, NEED_HEX[need] || "#ffcf6b"));
    box.title = "The last seven days";
  }
}

const shortDate = (ts) => {
  const d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return "Today";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};
const MEDAL = { listening: ["#ffcf6b", "#ff7a9c"], variety: ["#7fa7ff", "#ffcf6b"], curiosity: ["#ff7a9c", "#c3b8ee"], taste: ["#c3b8ee", "#7fa7ff"], flight: ["#8fe3c0", "#ffcf6b"] };
function medal(group, locked = false) {
  const [c, r] = MEDAL[group] || MEDAL.listening;
  const m = el("span", { class: "medal" + (locked ? " locked" : "") });
  m.innerHTML = `<svg viewBox="0 0 64 70" aria-hidden="true"><path d="M22 2 L32 24 L42 2" stroke="${r}" stroke-width="7" fill="none"/>` +
    `<circle cx="32" cy="44" r="22" fill="${c}"/><circle cx="32" cy="44" r="16" fill="none" stroke="#000" stroke-opacity=".18" stroke-width="2"/></svg>`;
  return m;
}

let achAll = false;
function renderStory() {
  if (!story || !story.enabled) return;
  drawSparks();

  // the journey: the newest milestones, and always where it began
  const list = $("journey");
  if (list) {
    const all = story.journey || [];
    const shown = all.slice(0, 6);
    const origin = all[all.length - 1];
    if (origin && !shown.includes(origin)) shown.push(origin);
    list.replaceChildren(...shown.map((it) => el("li", { class: `jg-${it.group}` }, el("time", { text: shortDate(it.at) }), el("i"),
      el("div", {}, el("b", { text: it.title }), it.detail ? el("small", { text: it.detail }) : null))));
    $("journey-since").textContent = origin ? `since ${shortDate(origin.at)}` : "";
  }

  // achievements: earned first (newest first), then the closest to being earned
  const ach = (story.achievements || []).slice().sort((a, b) =>
    (b.done - a.done) || (b.earned_at || 0) - (a.earned_at || 0) || (b.value / b.target) - (a.value / a.target));
  const grid = $("ach");
  if (grid) {
    $("ach-count").textContent = `${story.earned} of ${story.total}`;
    const cell = (a) => {
      const c = el("div", { class: "ach-cell" + (a.done ? "" : " locked"), title: a.how }, medal(a.group, !a.done), el("b", { text: a.name }));
      if (a.done) c.append(el("small", { text: a.earned_at ? shortDate(a.earned_at) : "earned" }));
      else {
        const bar = el("div", { class: "nb-bar" }, el("i"));
        bar.firstChild.style.width = `${Math.round((100 * a.value) / a.target)}%`;
        bar.firstChild.style.background = (MEDAL[a.group] || MEDAL.listening)[0];
        c.append(bar, el("small", { text: `${a.value} of ${a.target}` }));
      }
      return c;
    };
    const more = el("button", { type: "button", class: "ach-more", text: achAll ? "Show fewer" : `Show all ${ach.length}` });
    more.addEventListener("click", () => { achAll = !achAll; renderStory(); });
    grid.replaceChildren(...(achAll ? ach : ach.slice(0, 12)).map(cell));
    grid.after(more);
    for (const old of grid.parentNode.querySelectorAll(".ach-more")) if (old !== more) old.remove();
    const next = ach.find((a) => !a.done);
    const nx = $("ach-next");
    nx.hidden = !next;
    if (next) nx.replaceChildren(el("span", { class: "nb-k pink", text: "Next up" }), el("b", { text: next.name }),
                                 el("small", { text: `${next.how} (${next.value} of ${next.target})` }));
  }
  // on Now: the medals he has
  const strip = $("nb-medals");
  if (strip) {
    const earned = ach.filter((a) => a.done);
    strip.hidden = !story.total;
    strip.replaceChildren(el("div", { class: "nb-lead" }, el("span", { class: "nb-k", text: "Achievements" }), el("small", { text: `${story.earned} of ${story.total}` })),
      el("div", { class: "medals" }, ...earned.slice(0, 5).map((a) => { const m = medal(a.group); m.title = a.name; return m; }),
                                     ...ach.filter((a) => !a.done).slice(0, Math.max(0, 6 - Math.min(5, earned.length))).map((a) => { const m = medal(a.group, true); m.title = `${a.name}: ${a.value} of ${a.target}`; return m; })));
  }
}

/* ---------- Talking to him: "Ask for a record" and "How are you?" ---------- */
function setupTalk() {
  const ask = async (btn, url) => {
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const r = await fetch(url, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { toast(d.detail || "Beepnik didn't answer.", true); return; }
      sayLine(d.line);
      $("nb-say-text").textContent = d.line;
      const pick = $("bp-pick"), s2 = d.suggestion;
      pick.hidden = !s2;
      if (s2) {
        pick.replaceChildren(cover(s2.cover), el("span", {}, el("span", { class: "nb-k", text: "His pick" }), el("b", { text: s2.title }),
          el("small", { text: `${s2.artist}${s2.year ? `, ${s2.year}` : ""}` })), el("em", { text: "Open" }));
        pick.onclick = () => openRecord(s2.release_id);
      }
      loadPet();
    } catch (e) { toast("Couldn't reach Beepnik.", true); }
    finally { setTimeout(() => { btn.disabled = false; }, 4000); }
  };
  if ($("ask-record")) $("ask-record").addEventListener("click", () => ask($("ask-record"), "/api/beepnik/ask"));
  if ($("how-are-you")) $("how-are-you").addEventListener("click", () => ask($("how-are-you"), "/api/beepnik/howareyou"));
}

async function loadPet() {
  renderPet(await getJSON("/api/pet"));
}

// The mission strip: the goal in full, check-boxes for counted goals (a bar for minutes),
// the reward and the time left. Between missions it says when the next one arrives.
function hoursText(h) {
  return h >= 1 ? `${Math.round(h)} h` : `${Math.max(1, Math.round(h * 60))} min`;
}

function renderMission(st) {
  const box = $("mission");
  const enabled = st.mission_reward != null && !st.lost;
  box.hidden = !enabled;
  if (!enabled) return;
  const m = st.mission;
  const prog = $("mission-prog"), left = $("mission-left");
  prog.replaceChildren(); left.replaceChildren();
  box.classList.toggle("resting", !m);
  if (!m) {
    $("mission-goal").textContent = "No mission right now";
    const rest = (st.mission_rest || 0) / 3600;
    $("mission-reward").textContent = rest > 0.05 ? `The next one arrives in ${hoursText(rest)}.` : "The next one arrives any minute.";
    return;
  }
  $("mission-goal").textContent = m.text;
  $("mission-reward").textContent = `Reward: +${st.mission_reward} curiosity`;
  const done = Math.min(m.target, m.progress || 0);
  if (m.target <= 6) {                       // counted goals: one box each
    const pips = el("div", { class: "pips" });
    for (let i = 0; i < m.target; i++) pips.append(el("i", { class: i < Math.floor(done) ? "done" : null }));
    prog.append(pips, el("span", { text: `${Math.floor(done)} of ${m.target}` }));
  } else {                                   // minutes: a bar
    const bar = el("div", { class: "mbar" }, el("b"));
    bar.firstChild.style.width = `${Math.min(100, (100 * done) / m.target)}%`;
    prog.append(bar, el("span", { text: `${Math.floor(done)} of ${m.target} min` }));
  }
  const hrs = Math.max(0, (m.expires_at - Date.now() / 1000) / 3600);
  left.append(el("b", { text: hoursText(hrs) }), document.createTextNode("left"));
}

/* ---------- fuel tank and altimeter ---------- */

const daysText = (d) => d >= 1.5 ? `${Math.round(d)} days` : d >= 1 ? "a day" : `${Math.max(1, Math.round(d * 24))} h`;

// Fuel as a tank in three bands: below the hold level it can't hold its orbit (Fall), up to the
// burn line it holds (Hold), and above it whole sides fire burns (Burns).
function fuelTank(st) {
  const o = st.orbit, f = Math.max(0, Math.round(st.needs.fuel ?? 0));
  const hold = o.hold_level, burn = o.burn_line ?? o.reserve + 8;
  let tone, status, sub;
  if (st.lost) { tone = "fall"; status = "Empty"; sub = "Lost at Point Nemo"; }
  else if (!st.hatched) {
    tone = f >= burn ? "burn" : "hold";
    status = f >= burn ? "Burn ready" : "Filling up";
    sub = f >= burn ? "Whole sides lift it off" : `Burns need ${Math.round(burn)}`;
  } else if (f >= burn) { tone = "burn"; status = "Burn ready"; sub = "Whole sides will climb"; }
  else if (f > hold) { tone = "hold"; status = "Holding orbit"; sub = `Too low to burn (needs ${Math.round(burn)})`; }
  else { tone = "fall"; status = "Can't hold orbit"; sub = "Falling: play a record"; }
  const card = gauge(f, "Fuel", status, tone === "fall", !!st.danger, [
    { v: hold, color: "#ff7a9c", title: `Below ${Math.round(hold)} he can't hold his orbit` },
    { v: burn, color: "#f4eefe", title: `Above ${Math.round(burn)} a side played through fires a burn` }]);
  card.classList.add("tank", `t-${tone}`);
  card.setAttribute("aria-valuetext", `${f}, ${status.toLowerCase()}`);
  card.append(el("div", { class: "tally" }, el("i", { class: tone === "fall" ? "cold" : null, text: `holds above ${Math.round(hold)}` }),
                                            el("i", { class: tone === "burn" ? "on" : null, text: `burns at ${Math.round(burn)}` })),
              el("small", { class: "need-note", text: sub }));
  card.title = `Below ${Math.round(hold)} it can't hold its orbit; above ${Math.round(burn)} a side played through fires a burn.`;
  return card;
}

// The orbit as an altimeter: re-entry on the left, the stages along a log scale (so the low
// orbits get room), Beepnik as a dot, and how long it's safe for if things go quiet.
const STAGE_SHORT = { leo: "Low", geo: "Geo", lunar: "Lunar", deep: "Deep" };

/* Distance from Earth. Each stage sits at the real landmark it's named after, the re-entry line
   is the edge of space, and in between the distance scales smoothly (geometrically), so climbing
   and falling move it steadily. */
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
/* A live distance. Between the pet's updates the page carries the altitude on by itself: a gentle
   drift while holding (no orbit is a perfect circle), a real-time countdown while falling, and a
   smooth climb when a burn lands. */
const live = { alt: null, at: 0, k: 0, o: null, x: null, anim: null };
const orbitStatus = (o, d) => o.slipping ? `Falling: ${d} out and dropping` : o.climbing ? `Climbing: ${d} from Earth` : `Holding ${d} from Earth`;

function liveAlt(now) {
  let a = live.alt * Math.exp(-live.k * (now - live.at) / 1000);
  if (live.anim) {
    const p = Math.min(1, (now - live.anim.t0) / live.anim.dur), e = 1 - Math.pow(1 - p, 3);
    a = live.anim.from + (a - live.anim.from) * e;
    if (p >= 1) live.anim = null;
  }
  return a;
}

function setLiveOrbit(o, x) {
  const now = performance.now();
  const shown = live.alt == null || !live.o ? o.altitude : liveAlt(now);
  if (live.alt != null && Math.abs(o.altitude - shown) > 0.02) live.anim = { from: shown, t0: now, dur: 3500 };
  // anchor to when the pet service worked this altitude out, not when the page received it, so
  // the countdown carries on smoothly across refreshes instead of stepping back each time
  const age = lastPet && lastPet.updated_at ? Math.max(0, Date.now() / 1000 - lastPet.updated_at) : 0;
  live.alt = o.altitude; live.at = now - age * 1000; live.o = o; live.x = x;
  // falling: the exponential descent rate that brings it to the re-entry line in eta_days
  live.k = o.slipping && o.eta_days > 0 ? Math.log(Math.max(1.0001, o.altitude / (o.reentry_altitude || 1))) / (o.eta_days * 86400) : 0;
  tickLiveOrbit();
}

function tickLiveOrbit() {
  const o = live.o, lampEl = document.querySelector(".orbit-lamp");
  if (!o || !lampEl || !lastPet || lastPet.lost || !lastPet.hatched) return;
  const now = performance.now(), a = liveAlt(now);
  let km = kmFor(a, o);
  if (!o.slipping && !reducedMotion.matches) km *= 1 + 0.0015 * Math.sin((2 * Math.PI * Date.now()) / 120000);
  const text = `${Math.round(km).toLocaleString("en-US")} km`;
  const st = lampEl.querySelector(".astatus"), dot = lampEl.querySelector(".aship"), fill = lampEl.querySelector(".afill");
  if (st) st.textContent = orbitStatus(o, text);
  if (dot && live.x) dot.style.left = `${live.x(a)}%`;
  if (fill && live.x) fill.style.width = `${live.x(a)}%`;
  const falling = lampEl.querySelector(".afalling");
  if (falling && live.x) falling.style.left = `${live.x(a)}%`;
}
setInterval(tickLiveOrbit, 200);

function kmText(km) {
  if (km >= 1e6) return `${(km / 1e6).toFixed(km >= 1e7 ? 0 : 1).replace(/\.0$/, "")} million km`;
  const r = km >= 100000 ? Math.round(km / 100) * 100 : km >= 10000 ? Math.round(km / 100) * 100 : km >= 1000 ? Math.round(km / 10) * 10 : Math.round(km);
  return `${r.toLocaleString("en-US")} km`;
}
function orbitLamp(st) {
  const o = st.orbit;
  const head = el("div", { class: "grow" }, el("h6", { text: "ORBIT" }));
  const lampEl = lamp([head]);
  lampEl.classList.add("orbit-lamp");
  if (st.lost) {
    head.append(el("p", { text: "Re-entered" }), el("small", { class: "warn", text: "Splashed down at Point Nemo. A record launches the next probe." }));
    return lampEl;
  }
  if (!st.hatched) {
    const launch = o.launch_altitude || 2;
    const bar = el("div", { class: "launchbar" }, el("b"));
    bar.firstChild.style.width = `${Math.min(100, (100 * o.altitude) / launch)}%`;
    const up = kmFor(o.altitude, o), orbitKm = STAGE_KM.leo;
    head.append(el("p", {}, document.createTextNode("On the launch pad"),
                   el("span", { class: "burn", text: `${kmText(Math.min(up, orbitKm))} of ${kmText(orbitKm)} to orbit` })),
                bar, el("small", { text: "Each side played through lifts it closer." }));
    return lampEl;
  }
  const R = o.reentry_altitude || 1, C = o.ceiling || 30;
  const x = (a) => Math.max(0, Math.min(1, Math.log(Math.max(a, R) / R) / Math.log(C / R))) * 100;
  const tone = o.slipping ? "fall" : o.climbing ? "burn" : "hold";
  const dist = kmText(kmFor(o.altitude, o));
  const status = orbitStatus(o, dist);
  head.append(el("p", {}, document.createTextNode(o.label), el("span", { class: `${tone} astatus`, text: status })));
  setLiveOrbit(o, x);
  const alt = el("div", { class: `altim ${tone}` }, el("div", { class: "arail" }), el("div", { class: "afill" }), el("i", { class: "areentry", title: "Re-entry" }));
  alt.querySelector(".afill").style.width = `${x(o.altitude)}%`;
  for (const [a, id, label] of (o.stages || []).slice(1)) {
    const t = el("i", { class: "astage", title: `${label}: ${kmText(kmFor(a, o))}` }); t.style.left = `${x(a)}%`;
    const l = el("span", { class: "alabel", text: STAGE_SHORT[id] || label }); l.style.left = `${x(a)}%`;
    alt.append(t, l);
  }
  const dot = el("div", { class: "aship", title: `${dist} from Earth` }); dot.style.left = `${x(o.altitude)}%`;
  alt.append(dot);
  if (o.slipping) { const arr = el("div", { class: "afalling", text: "\u25C0\u25C0" }); arr.style.left = `${x(o.altitude)}%`; alt.append(arr); }
  head.append(alt);
  const eta = o.eta_days;
  head.append(el("small", { class: o.slipping ? "warn" : null,
    text: eta == null ? "" : o.slipping ? `Re-entry in about ${daysText(eta)}` : `Safe for about ${daysText(eta)} without music` }));
  return lampEl;
}

/* ---------- variety and signal ---------- */

// Variety: how mixed your recent listening is, in styles and release decades. Two thin bars show
// the mix itself: a color per style, a shade per decade, each sized by listening time.
const MIX_COLORS = ["#afa0eb", "#ffa366", "#6694ff", "#ffd16f", "#ff6f91", "#88e3bf", "#7aa2ff", "#5a4c87"];
const ERA_SHADES = ["#35278d", "#5a498f", "#7462ae", "#3773ff", "#5c8dff", "#8eb0ff", "#c1d4ff"];
function varietyGauge(st, label) {
  const v = st.variety;
  const n = (x) => (Math.round(x * 10) / 10).toString();
  const unit = v.by === "constellation" ? ["genre", "genres"] : ["style", "styles"];
  const worth = v.styles_worth ? `${n(v.styles_worth)} ${v.styles_worth === 1 ? unit[0] : unit[1]}, ` +
    (v.eras_worth ? `${n(v.eras_worth)} ${v.eras_worth === 1 ? "decade's" : "decades'"} worth` : "eras unknown") : "nothing lately";
  const g = gauge(st.needs.variety ?? 0, label, worth, st.low.includes("variety"));
  const bar = (parts, colors) => {
    const b = el("div", { class: "mix" });
    if (!parts.length) b.append(el("i", { class: "none" }));
    parts.forEach(([name, share], i) => {
      const seg = el("i", { title: `${name}: ${Math.round(share * 100)}%` });
      seg.style.flex = String(share);
      seg.style.background = name === "Other" ? "#5a4c87" : colors[i % colors.length];
      b.append(seg);
    });
    return b;
  };
  const shades = v.eras.map((_, i) => ERA_SHADES[Math.round((i * (ERA_SHADES.length - 1)) / Math.max(1, v.eras.length - 1))]);
  g.append(el("div", { class: "mixes" }, el("span", { text: v.by === "constellation" ? "Genres" : "Styles" }), bar(v.styles, MIX_COLORS),
                                         el("span", { text: "Eras" }), bar(v.eras, shades)));
  g.classList.add("variety", "clickable");
  g.tabIndex = 0;
  g.setAttribute("role", "button");
  g.setAttribute("aria-haspopup", "dialog");
  g.setAttribute("aria-label", `Variety ${st.needs.variety ?? 0}: ${worth}. Show the breakdown`);
  g.title = "Show the breakdown";
  const open = (e) => { e.stopPropagation(); toggleVarietyPop(g, st); };
  g.addEventListener("click", open);
  g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(e); } });
  return g;
}

/* The breakdown behind the Variety gauge: each style and each decade with its share of recent
   listening (weighted toward the last few days, as the meter is). */
let varietyPop = null;
function closeVarietyPop() {
  if (!varietyPop) return;
  varietyPop.remove();
  varietyPop = null;
}
function toggleVarietyPop(anchor, st) {
  if (varietyPop) { closeVarietyPop(); return; }
  closePop();
  const v = st.variety, cfgTarget = v.targets || { styles: 6, eras: 4 };
  const rows = (parts, colors, label) => {
    const list = el("div", { class: "vrows" });
    if (!parts.length) list.append(el("p", { class: "vnone", text: label === "Eras" ? "No release years known yet." : "Nothing played lately." }));
    parts.forEach(([name, share], i) => {
      const bar = el("div", { class: "vbar" }, el("b"));
      bar.firstChild.style.width = `${Math.max(2, share * 100)}%`;
      bar.firstChild.style.background = name === "Other" ? "#5a4c87" : colors[i % colors.length];
      list.append(el("div", { class: "vrow" }, el("i", { class: "vdot" }), el("span", { class: "vname", text: name }), bar,
                     el("span", { class: "vpct", text: `${Math.round(share * 100)}%` })));
      list.lastChild.firstChild.style.background = bar.firstChild.style.background;
    });
    return el("section", {}, el("h5", {}, document.createTextNode(label),
      el("span", { text: label === "Eras" ? `${v.eras_worth || 0} decades' worth` : `${v.styles_worth || 0} ${label.toLowerCase()}' worth` })), list);
  };
  const shades = v.eras.map((_, i) => ERA_SHADES[Math.round((i * (ERA_SHADES.length - 1)) / Math.max(1, v.eras.length - 1))]);
  varietyPop = el("div", { class: "vpop", role: "dialog", "aria-label": "Variety breakdown" },
    el("div", { class: "vhead" }, el("b", { text: `Variety ${Math.round(st.needs.variety ?? 0)}` }),
       el("button", { type: "button", class: "vclose", "aria-label": "Close", text: "\u00d7" })),
    rows(v.styles, MIX_COLORS, v.by === "constellation" ? "Genres" : "Styles"), rows(v.eras, shades, "Eras"),
    el("p", { class: "vnote", text: `An even mix of ${cfgTarget.styles} ${v.by === "constellation" ? "genres (as on the logbook)" : "styles"} and ${cfgTarget.eras} decades fills it. ` +
      "Shares are of your recent listening, with the last few days counting most." }));
  varietyPop.querySelector(".vclose").addEventListener("click", closeVarietyPop);
  varietyPop.addEventListener("click", (e) => e.stopPropagation());
  document.body.append(varietyPop);
  // under the gauge, kept on screen
  const r = anchor.getBoundingClientRect(), w = varietyPop.offsetWidth;
  const left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), document.documentElement.clientWidth - w - 12);
  varietyPop.style.left = `${left + window.scrollX}px`;
  varietyPop.style.top = `${r.bottom + window.scrollY + 10}px`;
  varietyPop.querySelector(".vclose").focus();
}
document.addEventListener("click", closeVarietyPop);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeVarietyPop(); });

// Signal: the link with Earth. The bars are its strength, from how many of the last 14 days had
// any listening (the strip of squares shows which); the tier comes from total days of contact.
function signalLamp(st) {
  const g = st.signal;
  const strength = g.strength ?? 0;
  const weak = strength <= 1;
  const bars = el("div", { class: `sbars${weak ? " weak" : ""}` });
  for (let i = 0; i < 5; i++) { const b = el("i", { class: i < strength ? "on" : null }); b.style.height = `${8 + i * 6}px`; bars.append(b); }
  const strip = el("div", { class: "contact", title: `In contact ${g.recent_days ?? 0} of the last 14 days` });
  (g.recent || []).forEach((on, i) => strip.append(el("i", { class: on ? "on" : null, title: i === 13 ? "Today" : null })));
  const left = Math.max(0, (g.next_days || 0) - g.contact_days);
  const next = g.next_label ? `${g.next_label} in ${left} more ${left === 1 ? "day" : "days"}` : "Fully equipped";
  const l = lamp([bars, el("div", { class: "grow" }, el("h6", { text: "SIGNAL" }), el("p", { text: g.label }), strip, el("small", { text: next }))]);
  l.title = `${g.contact_days} days of contact so far` + (g.next_label ? ` (${g.next_label} at ${g.next_days})` : "") +
            ". The bars and squares show how many of the last 14 days had any listening.";
  return l;
}

/* ---------- breakdowns: Curiosity, Taste and Mood ---------- */

let popEl = null, popFor = null;
function closePop() { if (popEl) { popEl.remove(); popEl = null; popFor = null; } }
document.addEventListener("click", closePop);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });

// A breakdown under the card it explains (the same look as Variety's).
function showPop(anchor, key, title, body, width = 390) {
  if (popFor === key) { closePop(); return; }
  closePop(); closeVarietyPop();
  popEl = el("div", { class: "vpop", role: "dialog", "aria-label": title },
    el("div", { class: "vhead" }, el("b", { text: title }), el("button", { type: "button", class: "vclose", "aria-label": "Close", text: "\u00d7" })),
    ...body);
  popEl.style.width = `${width}px`;
  popFor = key;
  popEl.querySelector(".vclose").addEventListener("click", closePop);
  popEl.addEventListener("click", (e) => e.stopPropagation());
  document.body.append(popEl);
  const r = anchor.getBoundingClientRect(), w = popEl.offsetWidth;
  const left = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), document.documentElement.clientWidth - w - 12);
  popEl.style.left = `${left + window.scrollX}px`;
  popEl.style.top = `${r.bottom + window.scrollY + 10}px`;
  popEl.querySelector(".vclose").focus();
}

function tappable(node, label, open) {
  node.classList.add("clickable");
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.setAttribute("aria-haspopup", "dialog");
  node.setAttribute("aria-label", label);
  node.title = "Show the breakdown";
  const go = (e) => { e.stopPropagation(); open(); };
  node.addEventListener("click", go);
  node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(e); } });
  return node;
}

const prow = (icon, text, pts, cls = "") => el("div", { class: `prow ${cls}` }, el("span", { class: "k", text: icon }), el("span", { text }),
  el("span", { class: `pts${pts == null ? "" : pts >= 0 ? " up" : " down"}`, text: pts == null ? "" : `${pts >= 0 ? "+" : ""}${pts}` }));
const phead = (label, aside = "") => el("h5", {}, document.createTextNode(label), el("span", { text: aside }));
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Curiosity: today's discoveries as tallies; the breakdown lists what fed and drained it.
const DISCOVERY_ICON = { never_played: "\u2605", dusty: "\u2727", side_new: "\u25d1", track_new: "\u2022", artist_new: "\u263a", mission: "\u2691" };
function curiosityGauge(st, label) {
  const c = st.curiosity, d = c.detail, t = d.today || {};
  const sub = c.streak >= 2 ? `discovery streak: ${c.streak} in a row` : c.familiar_now ? "a familiar record is draining it"
    : t.tracks || t.sides || t.records ? "fed today" : "nothing new lately";
  const g = gauge(st.needs.curiosity ?? 0, label, sub, st.low.includes("curiosity"));
  const tally = el("div", { class: "tally" });
  if (t.tracks) tally.append(el("i", { class: "on", text: plural(t.tracks, "track", "tracks") }));
  if (t.sides) tally.append(el("i", { class: "on", text: plural(t.sides, "side", "sides") }));
  if (t.records) tally.append(el("i", { class: "on", text: plural(t.records, "record", "records") }));
  if (c.familiar_now) tally.append(el("i", { class: "cold", text: "familiar record playing" }));
  if (tally.children.length) g.append(tally);
  return tappable(g, `Curiosity ${st.needs.curiosity ?? 0}: ${sub}. Show the breakdown`, () => {
    const body = [phead("Discoveries", "last 3 days")];
    if (!d.rows.length) body.push(el("p", { class: "vnone", text: "Nothing new in the last three days." }));
    for (const r of d.rows) body.push(prow(DISCOVERY_ICON[r.kind] || "\u2022", r.text, r.points));
    if (d.drains.length) {
      body.push(phead("Drained", "familiar records"));
      for (const r of d.drains) body.push(prow("\u21bb", `${r.title}, played on several recent days`, r.points));
    }
    if (st.mission) {
      body.push(phead("Mission"));
      body.push(prow("\u2691", `${st.mission.text} (${Math.floor(st.mission.progress || 0)} of ${st.mission.target})`, null));
    }
    body.push(el("p", { class: "vnote", text: "New records, sides, tracks and artists feed it, a streak of new records multiplies them, " +
      "replaying familiar records slowly drains it, and it fades without discoveries." }));
    showPop(g, "curiosity", `Curiosity ${Math.round(st.needs.curiosity ?? 0)}`, body);
  });
}

// Taste: the favorite styles as hearts, lit by how much each has been played lately.
function tasteGauge(st, label) {
  const td = st.taste_detail;
  const sub = !td.favorites.length ? "no favorites yet" : td.fed >= .35 ? "well fed lately" : td.fed >= .12 ? "getting some favorites" : "missing its favorites";
  const g = gauge(st.needs.taste ?? 0, label, sub, st.low.includes("taste"));
  if (td.favorites.length) {
    const favs = el("div", { class: "favs" });
    for (const f of td.favorites) favs.append(el("i", { class: f.share >= .2 ? "fed" : f.share >= .05 ? "part" : null, text: `${f.share >= .05 ? "\u2665" : "\u2661"} ${f.name}` }));
    g.append(favs);
  }
  return tappable(g, `Taste ${st.needs.taste ?? 0}: ${sub}. Show the breakdown`, () => {
    const body = [phead("Its favorites", td.learned ? "learned from your listening" : "set in the config")];
    if (!td.favorites.length) body.push(el("p", { class: "vnone", text: "None yet: it learns them from your listening." }));
    for (const f of td.favorites) {
      const bar = el("div", { class: "vbar" }, el("b"));
      bar.firstChild.style.width = `${Math.max(2, f.share * 100)}%`;
      bar.firstChild.style.background = f.share >= .2 ? "#ff6f91" : "#ffb3c5";
      body.push(el("div", { class: "vrow2" }, el("span", { class: "vname", text: `${f.share >= .05 ? "\u2665" : "\u2661"} ${f.name}` }), bar,
                   el("span", { class: "vpct", text: `${Math.round(f.share * 100)}%` })));
    }
    body.push(phead("Dislikes"));
    body.push(td.dislikes.length ? prow("\u2715", td.dislikes.join(", "), null) : prow("\u2715", "None set", null));
    if (td.suggestion) {
      body.push(phead("Would please it"));
      const s2 = td.suggestion;
      const r = prow("\u2605", `${s2.title} by ${s2.artist} (${s2.styles.join(", ")}), in your cargo hold`, null, "link");
      r.addEventListener("click", () => { closePop(); openRecord(s2.release_id); });
      body.push(r);
    }
    body.push(el("p", { class: "vnote", text: "Shares are of the last few days' listening, the most recent counting most. " +
      "Favorites can also be set by hand under [taste] in the config." }));
    showPop(g, "taste", `Taste ${Math.round(st.needs.taste ?? 0)}`, body);
  });
}

// Mood: a face, a flavor from the need that stands out, and the needs pulling hardest.
const NEED_NAMES = { fuel: "Fuel", variety: "Variety", curiosity: "Curiosity", taste: "Taste" };
const LOW_FLAVORS = ["starving", "peckish", "restless", "bored", "homesick"];
/* Beepnik's own face, cropped from his drawing, for the mood card and the phone's Now line. */
function beepFace(st) {
  const span = el("span", { class: "mface beepface" });
  const opts = { orbit: st.orbit.id, tier: 0, traits: (st.traits || []).map((t) => t.id), egg: !st.hatched, face: faceFor(st), ...looks(st), layers: "probe" };
  span.innerHTML = `<svg viewBox="118 64 84 84" aria-hidden="true">${Probe.companionMarkup(opts)}</svg>`;
  return span;
}

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

function sparkline(points, w, h, color) {
  const box = el("div", { class: "spark" });
  if (points.length < 2) { box.append(el("p", { class: "vnone", text: "The week's history fills in as it goes (a sample an hour)." })); return box; }
  const t0 = points[0][0], t1 = points[points.length - 1][0] || t0 + 1;
  const xy = points.map(([t, v]) => [((t - t0) / Math.max(1, t1 - t0)) * w, h - 3 - (Math.max(0, Math.min(100, v)) / 100) * (h - 6)]);
  const d = "M" + xy.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L");
  const [lx, ly] = xy[xy.length - 1];
  box.innerHTML = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">` +
    `<path d="${d} L${w} ${h} L0 ${h} Z" fill="${color}" opacity=".15"/>` +
    `<path d="${d}" stroke="${color}" stroke-width="2" fill="none" vector-effect="non-scaling-stroke"/>` +
    `<line x1="0" x2="${w}" y1="${h - 3 - .5 * (h - 6)}" y2="${h - 3 - .5 * (h - 6)}" stroke="#9a8dc7" stroke-dasharray="3 4" opacity=".35" vector-effect="non-scaling-stroke"/></svg>`;
  const dot = el("i", { class: "sdot" });       // positioned through the DOM: the page's security policy forbids inline style text
  dot.style.left = `${(lx / w) * 100}%`; dot.style.top = `${ly}px`; dot.style.background = color;
  box.append(dot);
  return box;
}

function moodLamp(st) {
  const md = st.mood_detail, s = Math.round(st.mood_score);
  const f = faceFor(st), kind = { ecstatic: "happy", happy: "happy", content: "content", restless: "restless", glum: "glum", sleep: "sleep" }[f] || "content";
  const name = el("p", { text: st.mood.charAt(0).toUpperCase() + st.mood.slice(1) });
  if (md.flavor) name.append(el("span", { class: LOW_FLAVORS.includes(md.flavor) ? "flav low" : "flav high", text: `${md.joiner} ${md.flavor}` }));
  const drivers = el("div", { class: "drivers" });
  const up = md.pulls.filter((p) => p.pull >= 3).slice(-1), down = md.pulls.filter((p) => p.pull <= -3).slice(0, 1);
  for (const p of up) drivers.append(el("i", { class: "up", text: `\u25b2 ${NEED_NAMES[p.need]}` }));
  for (const p of down) drivers.append(el("i", { class: "down", text: `\u25bc ${NEED_NAMES[p.need]}` }));
  const text = el("div", { class: "grow" }, el("h6", { text: "MOOD" }), name, drivers.children.length ? drivers : el("small", { text: `Score ${s} of 100` }));
  const l = lamp([beepFace(st), text]);
  l.classList.add("mood-lamp");
  return tappable(l, `Mood: ${md.phrase}, ${s} of 100. Show the breakdown`, () => {
    const hist = st.mood_history || [];
    const pts = hist.filter((_, i) => i % 3 === 0 || i === hist.length - 1).concat([[Date.now() / 1000, s]]);
    const weekAgo = hist.length ? hist[0][1] : null;
    const trend = weekAgo == null ? `score ${s}` : `score ${s}, ${s > weekAgo ? "up" : s < weekAgo ? "down" : "level"} from ${weekAgo} a week ago`;
    const body = [phead("This week", trend), sparkline(pts, 380, 56, s >= 65 ? "#88e3bf" : s >= 45 ? "#ffd16f" : "#ffa366"),
                  phead("What's pulling on it", "each need, weighted")];
    for (const p of [...md.pulls].reverse()) {
      const bar = el("div", { class: "vbar" }, el("b"));
      bar.firstChild.style.width = `${Math.max(2, p.value)}%`;
      bar.firstChild.style.background = p.pull >= 0 ? "#88e3bf" : "#ff668a";
      body.push(el("div", { class: "vrow2" }, el("span", { class: "vname", text: `${p.pull >= 0 ? "\u25b2" : "\u25bc"} ${NEED_NAMES[p.need]} ${p.value}` }), bar,
                   el("span", { class: `vpct ${p.pull >= 0 ? "up" : "down"}`, text: `${p.pull >= 0 ? "+" : ""}${Math.round(p.pull)}` })));
    }
    if (md.afterglow > 0) body.push(prow("\u2726", "Afterglow from a satisfied craving", Math.round(md.afterglow)));
    if (md.effect) { body.push(phead("What it's doing about it")); body.push(prow("\u270e", md.effect, null)); }
    body.push(el("p", { class: "vnote", text: "Mood is the needs' weighted average: each bar shows how far a need pulls it above or below a neutral 50. " +
      "The flavor comes from whichever need stands out most." }));
    showPop(l, "mood", md.phrase, body, 430);
  });
}

/* ---------- Point Nemo: the satellite graveyard ---------- */

function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

async function openNemo() {
  const list = $("nemo-list");
  list.replaceChildren(el("p", { class: "note", text: "Surfacing..." }));
  $("nemo-dlg").showModal();
  const { memorials } = await getJSON("/api/memorials");
  list.replaceChildren();
  if (!memorials.length) { list.append(el("p", { class: "note", text: "Nobody here yet. Keep it that way." })); return; }
  for (const m of memorials) {
    const days = Math.max(1, Math.round((m.died - m.born) / 86400));
    const stat = (big, small) => el("div", {}, el("b", { text: big }), el("span", { text: small }));
    const card = el("article", { class: "grave" },
      el("div", { class: "grave-head" }, el("h4", { text: m.name }),
        el("span", { text: `${fmtDate(m.born)} to ${fmtDate(m.died)}, ${days} ${days === 1 ? "day" : "days"} in flight` })),
      m.obituary ? el("p", { class: "obit", text: m.obituary }) : null,
      el("div", { class: "grave-stats" },
        stat(lastPet && lastPet.orbit && lastPet.orbit.stages ? kmText(kmFor(m.flight_hours, lastPet.orbit)) : String(m.flight_hours), "furthest from Earth"),
        stat(m.best_orbit || "the pad", "furthest orbit"),
        stat(m.best_tier || "Beacon", "best equipment"), stat(String(m.missions), m.missions === 1 ? "mission" : "missions"),
        stat(String(m.planets), "planets charted"), stat(String(m.systems), "new systems"), stat(String(m.records), "records spun")),
      m.traits.length ? el("p", { class: "grave-traits", text: `Wore ${m.traits.join(", ")}.` }) : null,
      m.last_words ? el("p", { class: "last-words" }, el("span", { text: "Last transmission: " }), document.createTextNode(m.last_words)) : null,
      el("p", { class: "grave-traits", text: "Re-entered over the South Pacific." }));
    list.append(card);
  }
}

// The transmission log, as a terminal below the footer row: Beepnik, Ground Control and system
// lines, newest first, with a tab per voice.
let termVoice = "all";
const TERM_WHO = { beepnik: "", ground: "GROUND CTRL", system: "SYSTEM" };
function termTime(at) {
  const d = new Date(at * 1000), today = new Date();
  const hms = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  return d.toDateString() === today.toDateString() ? hms : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${hms.slice(0, 5)}`;
}
function renderStylus(s) {
  const box = $("stylus");
  box.hidden = !s;
  if (!s) return;
  const pct = Math.min(100, s.pct);
  $("stylus-hours").textContent = `${Math.round(s.hours)} of ${s.rated} h`;
  $("stylus-fill").style.width = `${Math.max(1, pct)}%`;
  box.dataset.level = s.pct >= 90 ? "red" : s.pct >= 75 ? "amber" : "ok";
  const since = new Date(s.since * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  $("stylus-note").textContent = (s.pct >= 100 ? "Past its rated life: time for a new one. " : "") +
    (s.installed ? `Installed ${since}.` : `Counting since ${since} (set the install date in the config).`);
}

function renderTerm(st) {
  const name = (st.name || "Beepnik").toUpperCase();
  const all = [...(st.lines || []).map((l) => ({ at: l.at, voice: "beepnik", text: l.text })), ...(st.transmissions || [])]
    .sort((a, b) => a.at - b.at);              // oldest first: a terminal reads top to bottom
  $("log-toggle").textContent = `Transmission log (${all.length})`;
  $("term-host").textContent = `${name.toLowerCase().replace(/\s+/g, "-")}@deep-space : transmission log`;
  const shown = all.filter((l) => termVoice === "all" || l.voice === termVoice).slice(-80);
  // one grid for the whole log, so the columns line up and long messages wrap in their own column
  const body = $("term-body");
  // stay pinned to the newest line, unless you've scrolled up to read older ones
  const pinned = body.scrollHeight - body.scrollTop - body.clientHeight < 40 || !body.dataset.seen;
  body.replaceChildren(...shown.map((l) => el("div", { class: "ln" },
    el("span", { class: "ts", text: `[${termTime(l.at)}]` }),
    el("span", { class: `v ${l.voice}`, text: l.voice === "beepnik" ? name : TERM_WHO[l.voice] || l.voice.toUpperCase() }),
    el("span", { class: "dim", text: ">" }), el("span", { class: "t", text: l.text }))));
  if (!shown.length) body.append(el("div", { class: "ln full dim", text: "No transmissions on this channel yet." }));
  body.append(el("div", { class: "ln full" }, el("span", { class: "pr", text: "uplink@turntable-1:~$" }), document.createTextNode(" "), el("span", { class: "cur" })));
  if (pinned && !$("term").hidden) { body.scrollTop = body.scrollHeight; body.dataset.seen = "1"; }
}
function setupLog() {
  const btn = $("log-toggle");
  btn.addEventListener("click", () => {
    const open = btn.getAttribute("aria-expanded") !== "true";
    btn.setAttribute("aria-expanded", String(open));
    $("term").hidden = !open;
    $("companion").classList.toggle("log-open", open);
    if (open) { const b = $("term-body"); b.scrollTop = b.scrollHeight; b.dataset.seen = "1"; }    // open at the newest
  });
  for (const t of $("term-tabs").querySelectorAll("button")) t.addEventListener("click", () => {
    termVoice = t.dataset.voice;
    for (const o of $("term-tabs").querySelectorAll("button")) o.setAttribute("aria-selected", String(o === t));
    if (lastPet) renderTerm(lastPet);
    const b = $("term-body"); b.scrollTop = b.scrollHeight;
  });
  window.addEventListener("resize", aimSay);
}

function setupNemo() {
  $("nemo-open").addEventListener("click", () => openNemo().catch(() => {}));
  $("nemo-close").addEventListener("click", () => $("nemo-dlg").close());
  $("nemo-dlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
}

/* ---------- the cargo hold: dust, shrink-wrap and the dig ---------- */

const crate = { q: "", sort: "added", unplayed: false, offset: 0, pageSize: 25, open: false };
let dig = [], digAt = 0;

function sleeveArt(r, big = false) {
  const cls = r.sealed ? "sl sealed" : r.dust ? `sl d${r.dust}` : "sl";
  const s = el("span", { class: cls }, cover(r.cover));
  if (r.sealed) s.append(el("span", { class: "sticker" }, document.createTextNode("NEVER"), el("br"), document.createTextNode("PLAYED")));
  else if (r.dust && big) s.append(el("span", { class: "age", text: ago(r.last_played).replace(" ago", "") }));
  return s;
}

function drawDig() {
  const box = $("dig"), info = $("dig-info");
  box.replaceChildren(); info.replaceChildren();
  if (!dig.length) {
    $("dig-wrap").hidden = true;
    return;
  }
  $("dig-wrap").hidden = false;
  const order = dig.map((_, i) => dig[(digAt + i) % dig.length]);
  // back to front: the front record is drawn last
  for (let i = order.length - 1; i >= 0; i--) {
    const r = order[i];
    const s = sleeveArt(r, i === 0);
    s.style.left = `${i * 50}px`;
    s.style.transform = `rotate(${[-1, 1.5, 3.5, 6, 9][i] ?? 9}deg) translateY(${-i}px)`;
    if (i === 0) { s.classList.add("front"); s.title = "Open the record"; s.addEventListener("click", () => openRecord(r.id)); }
    box.append(s);
  }
  const r = order[0];
  const prev = el("button", { type: "button", "aria-label": "Previous record", text: "\u2039" });
  const next = el("button", { type: "button", "aria-label": "Next record", text: "\u203A" });
  const open = el("button", { type: "button", class: "open-rec", text: "Open the record" });
  prev.addEventListener("click", () => { digAt = (digAt - 1 + dig.length) % dig.length; drawDig(); });
  next.addEventListener("click", () => { digAt = (digAt + 1) % dig.length; drawDig(); });
  open.addEventListener("click", () => openRecord(r.id));
  info.append(el("p", { class: "k", text: `${(digAt % dig.length) + 1} of ${dig.length} waiting for a spin` }),
              el("h2", { text: r.title }), el("p", { class: "by", text: r.year ? `${r.artist}, ${r.year}` : r.artist }),
              el("p", { class: "why", text: r.story || "" }), el("div", { class: "flips" }, prev, next, open));
}

async function loadDusty() {
  dig = (await getJSON("/api/dusty?limit=5")).items;
  digAt = Math.min(digAt, Math.max(0, dig.length - 1));
  drawDig();
}

function sleeve(r) {
  const btn = el("button", { type: "button", class: "sleeve", "aria-label": `${r.title} by ${r.artist}` }, sleeveArt(r),
    el("b", { text: r.title }), el("span", { text: r.year ? `${r.artist}, ${r.year}` : r.artist }));
  if (r.spins) btn.querySelector(".sl").append(el("span", { class: "spins-badge", text: `${r.spins} ${r.spins === 1 ? "spin" : "spins"}` }));
  if (r.pressings > 1) {                     // one sleeve for several pressings of the album
    btn.querySelector(".sl").append(el("span", { class: "pressings-badge", text: `${r.pressings} pressings` }));
    btn.setAttribute("aria-label", `${r.title} by ${r.artist}, ${r.pressings} pressings`);
  }
  btn.addEventListener("click", () => openRecord(r.id));
  return btn;
}

async function loadCrate(append = false) {
  if (!append) crate.offset = 0;
  const params = new URLSearchParams({ q: crate.q, sort: crate.sort, unplayed: crate.unplayed, limit: crate.pageSize, offset: crate.offset });
  const data = await getJSON(`/api/collection?${params}`);
  const sm = data.summary;
  $("crate-summary").textContent = sm.records ? `${sm.records} records, ${sm.never_played} never played here` : "Syncs from Discogs";
  if (!crate.open) return;
  const grid = $("crate-grid");
  if (!append) grid.replaceChildren();
  if (sm.records === 0) {
    grid.append(el("p", { class: "empty", text: "Your Discogs collection hasn't synced yet. Once the first sync runs, every record you own appears here." }));
    $("crate-more").hidden = true;
    return;
  }
  if (!append && data.items.length === 0) {
    grid.append(el("p", { class: "empty", text: crate.unplayed ? "Every record matching that has been played. Nice." : "No records match that search." }));
  }
  for (const r of data.items) grid.append(sleeve(r));
  crate.offset += data.items.length;
  $("crate-more").hidden = crate.offset >= data.total;
}

function setCrateOpen(open) {
  crate.open = open;
  $("crate-body").hidden = !open;
  const t = $("crate-toggle");
  t.setAttribute("aria-expanded", String(open));
  t.innerHTML = open ? "Hide the cargo &#9652;" : "Show the cargo &#9662;";
  if (open) loadCrate().catch(() => {});
}

function setupCrate() {
  let t;
  $("crate-q").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { crate.q = e.target.value; if (crate.q && !crate.open) setCrateOpen(true); else loadCrate().catch(() => {}); }, 250);
  });
  $("crate-toggle").addEventListener("click", () => setCrateOpen(!crate.open));
  $("crate-sort").addEventListener("change", (e) => { crate.sort = e.target.value; loadCrate().catch(() => {}); });
  $("crate-unplayed").addEventListener("change", (e) => { crate.unplayed = e.target.checked; loadCrate().catch(() => {}); });
  $("crate-more").addEventListener("click", () => loadCrate(true).catch(() => {}));
  $("crate-line").addEventListener("click", (e) => openRecord(Number(e.currentTarget.dataset.release)));
  $("dlg-close").addEventListener("click", () => $("record-dlg").close());
  $("record-dlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
}

/* ---------- flight log: the radar and the listening log ---------- */

const RC = 235, RMAX = 205;
let radarDays = [], flightBefore = null, flightSessions = [];

function radarXY(dayIndex, hour, n) {
  const a = -Math.PI / 2 + ((n - 1 - dayIndex) / n) * Math.PI * 2;
  const rr = 26 + (hour / 23) * (RMAX - 30);
  return [RC + Math.cos(a) * rr, RC + Math.sin(a) * rr];
}

/* The month as bars: minutes listened each day, oldest on the left, today on the right. */
function drawMonth(data) {
  const box = $("month");
  if (!box || !data || !data.days) return;
  const mins = data.days.map((d) => (d.blocks || []).reduce((a, b) => a + b, 0) / 60);
  const peak = Math.max(30, ...mins);
  box.replaceChildren(...mins.map((m, i) => {
    const day = new Date((data.days[i].start || 0) * 1000);
    const b = el("i", { class: m >= 1 ? "on" : null, title: `${day.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}: ${m >= 1 ? duration(m * 60) : "no listening"}` });
    b.style.height = m >= 1 ? `${12 + (88 * m) / peak}%` : "6%";
    if (i === mins.length - 1) b.classList.add("today");
    return b;
  }));
  const total = mins.reduce((a, b) => a + b, 0), active = mins.filter((m) => m >= 1).length;
  $("month-note").textContent = active ? `${duration(total * 60)} across ${active} ${active === 1 ? "day" : "days"}` : "minutes per day";
}

function drawRadar(data) {
  drawMonth(data);
  radarDays = data.days;
  const n = radarDays.length;
  const R = $("radar");
  let out = `<defs><filter id="rbl" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4"/></filter></defs>`;
  for (let k = 1; k <= 4; k++) out += `<circle cx="${RC}" cy="${RC}" r="${RMAX * k / 4}" fill="none" stroke="#312965" stroke-width="1"/>`;
  for (let d = 0; d < n; d++) {
    const a = -Math.PI / 2 + ((n - 1 - d) / n) * Math.PI * 2;
    out += `<line x1="${RC}" y1="${RC}" x2="${RC + Math.cos(a) * RMAX}" y2="${RC + Math.sin(a) * RMAX}" stroke="#251f4c" stroke-width="${d === n - 1 ? 1.6 : .7}"/>`;
  }
  // the sweep: a fading wedge behind a bright line, turning slowly
  const sa = -Math.PI / 2, sb = sa - .9;
  out += `<g class="sweep"><path d="M${RC} ${RC} L${RC + Math.cos(sa) * RMAX} ${RC + Math.sin(sa) * RMAX} A${RMAX} ${RMAX} 0 0 0 ${RC + Math.cos(sb) * RMAX} ${RC + Math.sin(sb) * RMAX} Z" fill="#ffdd94" opacity=".09"/>
    <line x1="${RC}" y1="${RC}" x2="${RC + Math.cos(sa) * RMAX}" y2="${RC + Math.sin(sa) * RMAX}" stroke="#ffdd94" stroke-width="2" opacity=".7"/></g>`;
  radarDays.forEach((day, d) => day.blocks.forEach((secs, hr) => {
    if (secs < 60) return;
    const m = secs / 60, s = 2.5 + Math.min(60, m) / 18;
    const [x, y] = radarXY(d, hr, n);
    out += `<g class="blip" data-day="${d}" data-hour="${hr}"><circle cx="${x}" cy="${y}" r="${Math.max(9, s * 1.8)}" fill="transparent"/>
      <circle cx="${x}" cy="${y}" r="${s * 1.8}" fill="#ffd57b" opacity=".35" filter="url(#rbl)"/><circle cx="${x}" cy="${y}" r="${s}" fill="#ffe4ab"/></g>`;
  }));
  const lab = (t, rr) => `<text x="${RC + 6}" y="${RC - rr - 4}" font-family="Nunito" font-weight="800" font-size="11" fill="#ffbc2a">${t}</text>`;
  out += lab("6 AM", RMAX * .25) + lab("noon", RMAX * .5) + lab("6 PM", RMAX * .75) + lab("midnight", RMAX);
  out += `<text x="${RC}" y="18" text-anchor="middle" font-family="Righteous" font-size="13" fill="#ffdd94">TODAY</text><g id="radar-hot"></g>`;
  R.innerHTML = out;

  const sm = data.summary;
  const box = $("radar-stats");
  box.replaceChildren();
  if (sm && sm.seconds) {
    const h = sm.prime_start, fmt = (x) => { const hh = (x % 24); return hh === 0 ? "midnight" : hh === 12 ? "noon" : hh < 12 ? `${hh} AM` : `${hh - 12} PM`; };
    const stat = (big, small) => el("div", {}, el("b", { text: big }), document.createTextNode(small));
    box.append(stat(`${fmt(h)} to ${fmt(h + 3)}`, "prime time"), stat(sm.busiest, "busiest day"),
               stat(`${sm.days_with} of ${n}`, "days with a spin"), stat(duration(sm.longest), "longest day"));
    $("flight-summary").textContent = `The last ${n} days: ${duration(sm.seconds)} across ${sm.days_with} ${sm.days_with === 1 ? "day" : "days"}`;
  } else {
    $("flight-summary").textContent = "Nothing logged in the last 30 days yet.";
  }
}

function sessionBlips(s) {
  // which (day, hour) cells a session covers
  const n = radarDays.length, cells = [];
  for (let t = s.start - (s.start % 3600); t <= s.end; t += 3600) {
    const d = radarDays.findIndex((day, i) => t >= day.start && t < (radarDays[i + 1] ? radarDays[i + 1].start : day.start + 86400));
    if (d >= 0) cells.push([d, new Date(t * 1000).getHours()]);
  }
  return cells.filter(([d, h]) => d >= 0 && d < n);
}

function highlightSession(s) {
  const g = document.getElementById("radar-hot");
  if (!g) return;
  g.innerHTML = s ? sessionBlips(s).map(([d, h]) => { const [x, y] = radarXY(d, h, radarDays.length);
    return `<circle cx="${x}" cy="${y}" r="9" fill="none" stroke="#fff" stroke-width="2"/>`; }).join("") : "";
}

function sidesText(positions) {
  const sides = [...new Set(positions.map((p) => (p || "").match(/^[A-Za-z]+/)?.[0]).filter(Boolean))].sort();
  if (!sides.length) return "";
  if (sides.length === 1) return `side ${sides[0]}`;
  return sides.length > 2 ? `sides ${sides[0]} to ${sides[sides.length - 1]}` : `sides ${sides.join(" and ")}`;
}

function renderFlight(sessions, append) {
  const box = $("flight-log");
  if (!append) { box.replaceChildren(); flightSessions = []; }
  if (!append && !sessions.length) {
    box.append(el("p", { class: "empty", text: "No sessions yet. Every record you play is logged here." }));
    return;
  }
  for (const s of sessions) {
    flightSessions.push(s);
    // group the session's tracks by record, in play order
    const plays = s.plays.slice().reverse();
    const recs = [];
    for (const p of plays) {
      const key = p.release_id || `${p.artist}|${p.album}`;
      const last = recs[recs.length - 1];
      if (last && last.key === key) last.plays.push(p); else recs.push({ key, plays: [p] });
    }
    const count = recs.length;
    const head = el("h5", { text: dayLabel(s.start) });
    const parts = recordingsFor(s);
    if (parts.length) {
      const play = el("button", { type: "button", class: "play-session", text: "▶ Play", title: "Play this session's recording" });
      play.addEventListener("click", (e) => { e.stopPropagation(); openDeck(parts, Math.max(0, s.start - parts[0].started_at - 5)); });
      head.append(play);
    }
    head.append(el("span", { text: `${clock(s.start)} to ${clock(s.end)}, ${count} ${count === 1 ? "record" : "records"}, ${duration(s.seconds)}` }));
    const art = el("article", { class: "fsess", "data-start": s.start }, head);
    for (const r of recs.reverse()) {
      const p0 = r.plays[0];
      const sides = sidesText(r.plays.map((p) => p.position));
      const n = r.plays.length;
      const row = el(p0.release_id ? "button" : "div", { class: "frec", type: p0.release_id ? "button" : null },
        cover(p0.cover), el("div", {}, el("b", { text: p0.album || p0.title }),
          el("small", { text: [p0.artist, sides, `${n} ${n === 1 ? "track" : "tracks"}`].filter(Boolean).join(", ") })),
        el("time", { text: clock(p0.started_at) }));
      if (p0.release_id) row.addEventListener("click", () => openRecord(p0.release_id));
      const tracks = el("p", { class: "ftracks" });
      r.plays.forEach((p, i) => {
        if (i) tracks.append(document.createTextNode(", "));
        const span = el("span", { class: "ftrack" }, document.createTextNode(p.title));
        if (p.inferred) span.append(el("em", { text: "probable", title: "Worked out from the record's tracklist, not heard by Shazam" }));
        if (p.manual) span.append(el("em", { class: "hand", text: "by hand", title: "Tagged or checked off by hand" }));
        const rm = el("button", { type: "button", class: "rm", "aria-label": `Remove ${p.title}`, title: "Remove this play", text: "×" });
        rm.addEventListener("click", (e) => {
          e.stopPropagation();
          if (confirm(`Remove "${p.title}" from the log? It goes to quarantine and can be restored.`)) runEdit("remove", { play_id: p.id }, "Removing...");
        });
        span.append(rm);
        tracks.append(span);
      });
      if (p0.release_id) {
        const add = el("button", { type: "button", class: "frec-add", text: "+ tracks", title: "Check off other tracks you played" });
        add.addEventListener("click", (e) => {
          e.stopPropagation();
          openPicker({ mode: "many", title: "Which tracks did you play?", release: p0.release_id,
            hint: "Tick the tracks that played. They're placed in order around the ones already logged.",
            done: r.plays.map((p) => p.position).filter(Boolean),
            save: (rec, positions) => runEdit("check", { release_id: rec.id, positions, around: p0.started_at }) });
        });
        tracks.append(document.createTextNode(" "), add);
      }
      art.append(row, tracks);
    }
    for (const g of (s.gaps || []).slice().reverse()) {
      const mins = Math.max(1, Math.round((g.end - g.start) / 60));
      const before = s.plays.filter((p) => p.started_at < g.start && p.release_id).sort((a, b) => b.started_at - a.started_at)[0];
      const btn = el("button", { type: "button", text: "Fill in" });
      btn.addEventListener("click", () => openPicker({
        mode: "many", title: `What played from ${clock(g.start)} to ${clock(g.end)}?`, release: before ? before.release_id : null,
        hint: `${mins} min of music nobody identified. Pick the record and tracks, in order; they're spread across the gap.`,
        suggest: suggestForGap(g, before && { release_id: before.release_id, position: before.position }),
        save: (rec, positions) => runEdit("fill", { release_id: rec.id, positions, start: g.start, end: g.end }) }));
      art.append(el("div", { class: "fgap" }, el("span", { text: `Unidentified, ${clock(g.start)} to ${clock(g.end)} (${mins} min)` }), btn));
    }
    art.addEventListener("mouseenter", () => { art.classList.add("hot"); highlightSession(s); });
    art.addEventListener("mouseleave", () => { art.classList.remove("hot"); highlightSession(null); });
    box.append(art);
  }
  box.querySelector(".more")?.remove();
  if (flightBefore) {
    const more = el("button", { type: "button", class: "more", text: "Show earlier sessions" });
    more.addEventListener("click", () => loadFlightLog(true).catch(() => {}));
    box.append(more);
  }
}

async function loadFlightLog(append = false) {
  if (!append) { await loadRecordings(); loadUnmatchedCount(); }
  const url = append && flightBefore ? `/api/history?limit=60&before=${flightBefore}` : "/api/history?limit=60";
  const data = await getJSON(url);
  flightBefore = data.next_before;
  renderFlight(data.sessions, append);
}

async function loadRadar() {
  drawRadar(await getJSON("/api/grid?days=30&hours=1"));
}

function setupRadar() {
  $("radar").addEventListener("click", (e) => {
    const b = e.target.closest(".blip");
    if (!b) return;
    const day = radarDays[Number(b.dataset.day)], hour = Number(b.dataset.hour);
    const t = day.start + hour * 3600 + 1800;
    const s = flightSessions.find((x) => x.start - 3600 <= t && t <= x.end + 3600);
    const node = s && $("flight-log").querySelector(`.fsess[data-start="${s.start}"]`);
    if (node) {
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      node.classList.add("hot"); highlightSession(s);
      setTimeout(() => { node.classList.remove("hot"); highlightSession(null); }, 2500);
    }
  });
}

/* ---------- audio telemetry: Beepnik glows with the level and flashes on the beat ---------- */

const tele = { d: null, at: 0, lvl: 0, src: null, retry: 1000 };

function connectTelemetry() {
  if (!window.EventSource) return;
  const src = new EventSource("/api/telemetry/live");
  tele.src = src;
  src.onmessage = (e) => { try { tele.d = JSON.parse(e.data); tele.at = performance.now(); tele.retry = 1000; } catch {} };
  src.onerror = () => {                       // reconnect with a gentle backoff
    src.close(); tele.d = null;
    setTimeout(connectTelemetry, tele.retry);
    tele.retry = Math.min(30000, tele.retry * 2);
  };
}

function teleFrame() {
  const d = tele.d;
  const fresh = d && d.live && performance.now() - tele.at < 3000;
  const playing = $("hero").dataset.state !== "idle";
  const on = fresh && d.music && playing && !reducedMotion.matches;
  for (const host of [$("companion"), $("hero")]) host.classList.toggle("tele", !!on);
  $("vu").hidden = !(fresh && d.music && playing);
  if (fresh && d.music) {
    tele.lvl += ((d.level || 0) - tele.lvl) * 0.25;                // smoothed
    // Beats happen in the room before the stream hears them; shift by the configured offset.
    let beat = 0;
    if (d.period && d.last_beat && d.confidence >= 0.3) {
      const offset = ((lastPet && lastPet.panel && lastPet.panel.beat_offset_ms) ?? 800) / 1000;
      const x = ((Date.now() / 1000 - (d.last_beat - offset)) % d.period + d.period) % d.period;
      beat = x < 0.14 ? 1 - x / 0.14 : 0;
    }
    for (const host of [$("companion"), $("hero")]) {
      host.style.setProperty("--lvl", tele.lvl.toFixed(3));
      host.style.setProperty("--beat", beat.toFixed(2));
    }
    for (const k of ["bass", "mid", "treble"]) $(`vu-${k}`).style.setProperty("--h", `${Math.round((d[k] || 0) * 100)}%`);
  }
  requestAnimationFrame(teleFrame);
}

/* ---------- session playback ---------- */

let recordings = [], deck = null;

function fmtLong(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s2 = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s2).padStart(2, "0")}` : `${m}:${String(s2).padStart(2, "0")}`;
}

// A session can be recorded as several files (the recorder stops after a long pause, or when
// it's restarted). Its Play button gathers all of them, oldest first.
function recordingsFor(s) {
  return recordings.filter((r) => r.started_at < s.end + 60 && r.ended_at > s.start - 60)
                   .sort((a, b) => a.started_at - b.started_at);
}

/* The deck plays a session's parts as one timeline, from the first part's start to the last
   part's end. Gaps between parts show as a hatched strip, and playback steps over them. */
function buildDeck(parts) {
  const start = parts[0].started_at, end = Math.max(...parts.map((p) => p.ended_at));
  const marks = [], seen = new Set();
  for (const p of parts) {
    for (const m of p.marks) {
      const at = p.started_at - start + m.offset;
      const key = `${m.title}|${Math.round(at / 30)}`;       // a track split across two files, once
      if (seen.has(key)) continue;
      seen.add(key);
      marks.push({ ...m, at });
    }
  }
  marks.sort((a, b) => a.at - b.at);
  return { parts, start, total: end - start, marks, part: 0, key: parts.map((p) => p.id).join(",") };
}

const partOffset = (i) => deck.parts[i].started_at - deck.start;
const deckTime = () => (deck ? partOffset(deck.part) + $("rec-audio").currentTime : 0);

function loadPart(i, at, play) {
  const audio = $("rec-audio");
  const go = () => { audio.currentTime = Math.max(0, at); if (play) audio.play().catch(() => {}); };
  if (deck.part === i && audio.getAttribute("src") === deck.parts[i].url && audio.readyState >= 1) { go(); return; }
  deck.part = i;
  audio.src = deck.parts[i].url;
  audio.addEventListener("loadedmetadata", go, { once: true });
}

function seekDeck(T, play) {
  if (!deck) return;
  T = Math.max(0, Math.min(T, deck.total - 0.5));
  // the part playing at T; in a gap, the next part
  let i = deck.parts.findIndex((p, k) => T < partOffset(k) + p.seconds);
  if (i < 0) i = deck.parts.length - 1;
  loadPart(i, Math.max(0, T - partOffset(i)), play ?? true);
}

const PLAY_SVG = '<svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>';
const PAUSE_SVG = '<svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>';
let deckCollapsed = false, scrubbing = null;

function openDeck(parts, at = 0, label = null) {
  // only one thing plays at a time: stop listening live
  if ($("listen").getAttribute("aria-pressed") === "true") $("listen").click();
  const key = parts.map((p) => p.id).join(",");
  if (!deck || deck.key !== key) {
    deck = buildDeck(parts);
    deck.label = label || `Session, ${dayLabel(deck.start)} ${clock(deck.start)}`;
    if (window.TurntableApp && window.TurntableApp.deckQueue) {
      const abs = (u) => (u ? new URL(u, location.href).href : "");
      window.TurntableApp.deckQueue(JSON.stringify({ label: deck.label, parts: deck.parts.map((p) => {
        const m = (p.marks || [])[0] || {};
        return { url: abs(p.url), title: m.title || deck.label, artist: m.artist || "", album: m.album || "", cover: abs(m.cover) };
      }) }));
      deck.appMeta = "";
    }
    const segs = $("deck-segs");
    segs.replaceChildren();
    const pct = (x) => (100 * x) / deck.total;
    deck.parts.forEach((p, k) => {        // quiet stretches between the session's recordings
      if (!k) return;
      const from = partOffset(k - 1) + deck.parts[k - 1].seconds, to = partOffset(k);
      if (to - from < 5) return;
      const gap = el("i", { class: "gap" });
      gap.style.left = `${pct(from)}%`;
      gap.style.width = `${pct(to - from)}%`;
      segs.append(gap);
    });
    deck.marks.forEach((m, i) => {        // a tick where each track starts
      if (!i) return;
      const tick = el("i", { class: "tick" });
      tick.style.left = `${pct(m.at)}%`;
      segs.append(tick);
    });
    const list = $("deck-list");
    list.replaceChildren(...deck.marks.map((m, i) => {
      const li = el("li", {}, el("button", { type: "button" },
        el("span", { class: "n", text: m.position || String(i + 1) }),
        el("span", { class: "tt", text: m.title }), el("span", { class: "at", text: fmtLong(m.at) })));
      li.firstChild.addEventListener("click", () => { seekDeck(m.at); toggleDeckList(false); });
      return li;
    }));
    $("deck-line").setAttribute("aria-valuemax", String(Math.round(deck.total)));
  }
  embedDeck();
  seekDeck(at, true);
}

/* Playback happens in the Now card: the player moves into it, the record on the platter shows the
   recording's cover and spins while it plays. Stop returns the card to the turntable. */
let replayLiveHref = null;
function embedDeck() {
  const hero = $("hero"), d = $("deck");
  if (!hero.classList.contains("replay")) replayLiveHref = $("label-art").getAttribute("href");
  $("np-replay").insertBefore(d, $("replay-live"));
  d.classList.add("embedded");
  d.hidden = false;
  d.style.zoom = ""; d.style.width = "";
  document.body.style.paddingBottom = "";
  $("np-replay").hidden = false;
  hero.classList.add("replay");
  $("replay-label").textContent = `Playing back \u00b7 ${deck.label}`;
  $("deck-list").hidden = false;
  showTab("now");
  updateReplayLive();
  window.scrollTo({ top: 0, behavior: reducedMotion.matches ? "auto" : "smooth" });
}

function stopReplay() {
  const audio = $("rec-audio"), hero = $("hero");
  audio.pause(); audio.removeAttribute("src"); audio.load(); deck = null;
  $("deck").hidden = true;
  $("np-replay").hidden = true;
  hero.classList.remove("replay", "rolling");
  if (replayLiveHref) $("label-art").setAttribute("href", replayLiveHref); else $("label-art").removeAttribute("href");
  replayLiveHref = null;
  if (lastNowData) renderNow(lastNowData);
}

/* While playing back: say if the turntable has something on, with a way back to it. */
function updateReplayLive() {
  const chip = $("replay-live");
  if (!chip) return;
  const d = lastNowData, cur = d && d.state === "playing" ? d.current : null;
  chip.hidden = !(deck && cur);
  if (!chip.hidden) chip.replaceChildren(el("span", { class: "nb-k pink", text: "On the turntable now" }),
    el("b", { text: cur.title || "A record" }), el("small", { text: [cur.artist, cur.album].filter(Boolean).join(", ") }), el("em", { text: "Switch to live" }));
}

function currentMark(T) {
  let idx = -1;
  deck.marks.forEach((m, i) => { if (T >= m.at - 1) idx = i; });
  return idx;
}

function tickDeck() {
  if (!deck) return;
  const audio = $("rec-audio");
  const T = scrubbing != null ? scrubbing : deckTime(), total = deck.total;
  const frac = Math.min(1, Math.max(0, T / total));
  $("deck-fill").style.width = `${frac * 100}%`;
  $("deck-thumb").style.left = `${frac * 100}%`;
  $("deck-thin").style.width = `${frac * 100}%`;
  $("deck-pos").textContent = fmtLong(T);
  $("deck-left").textContent = `-${fmtLong(total - T)}`;
  $("deck-line").setAttribute("aria-valuenow", String(Math.floor(T)));
  $("deck-line").setAttribute("aria-valuetext", `${fmtLong(T)} of ${fmtLong(total)}`);
  const i = currentMark(T), m = deck.marks[i];
  const playing = m && T < m.at + m.length;
  $("deck-track").textContent = playing ? m.title : deck.label;
  if (window.TurntableApp && window.TurntableApp.deckMeta) {
    const meta = JSON.stringify({ title: playing ? m.title : deck.label, artist: playing ? (m.artist || "") : "Turntable-1",
      album: playing ? (m.album || "") : "", cover: playing && m.cover ? new URL(m.cover, location.href).href : "" });
    if (meta !== deck.appMeta) { deck.appMeta = meta; window.TurntableApp.deckMeta(meta); }
  }
  $("deck-sub").textContent = playing ? [m.album, m.artist].filter(Boolean).join(", ") : (m ? "Between tracks" : "Before the first track");
  if ($("hero").classList.contains("replay")) {
    const art = $("label-art"), want = (m && m.cover) || (deck.marks.find((x) => x.cover) || {}).cover || "";
    if ((art.getAttribute("href") || "") !== want) { if (want) art.setAttribute("href", want); else art.removeAttribute("href"); }
    $("hero").classList.toggle("rolling", !audio.paused);
  }
  const cover = $("deck-cover");
  const src = m && m.cover ? m.cover : "";
  if (cover.getAttribute("src") !== src) { if (src) cover.src = src; else cover.removeAttribute("src"); }
  cover.classList.toggle("blank", !src);
  $("deck-tracks").textContent = deck.marks.length ? `Track ${Math.max(1, i + 1)} of ${deck.marks.length}` : "No tracks";
  [...$("deck-list").children].forEach((li, k) => li.classList.toggle("now", k === i));
  for (const b of document.querySelectorAll("#deck .dplay")) {
    b.innerHTML = audio.paused ? PLAY_SVG : PAUSE_SVG;
    b.setAttribute("aria-label", audio.paused ? "Play" : "Pause");
  }
}

function deckAct(act) {
  const audio = $("rec-audio"), T = deckTime();
  if (act === "play") { if (audio.paused) audio.play().catch(() => {}); else audio.pause(); return; }
  if (act === "back") return seekDeck(T - 15);
  if (act === "fwd") return seekDeck(T + 15);
  const i = currentMark(T);
  if (act === "prev") {
    // a few seconds into a track: back to its start; otherwise the track before
    const m = deck.marks[i];
    if (m && T - m.at > 4) return seekDeck(m.at);
    return seekDeck(i > 0 ? deck.marks[i - 1].at : 0);
  }
  if (act === "next" && i + 1 < deck.marks.length) return seekDeck(deck.marks[i + 1].at);
}

function toggleDeckList(open) {
  const list = $("deck-list");
  if ($("deck").classList.contains("embedded")) { list.hidden = false; return; }   // always shown in the card
  open = open ?? list.hidden;
  list.hidden = !open;
  $("deck-tracks").setAttribute("aria-expanded", String(open));
  if (open) { const now = list.querySelector(".now"); if (now) now.scrollIntoView({ block: "center" }); }
}

function setDeckSize(collapsed) {
  deckCollapsed = collapsed;
  $("deck").classList.toggle("collapsed", collapsed);
  $("deck-size").setAttribute("aria-expanded", String(!collapsed));
  $("deck-size").setAttribute("aria-label", collapsed ? "Expand the player" : "Collapse the player");
  if (collapsed) toggleDeckList(false);
  fitDeck();
}

// On a phone the page is laid out wide and shrunk to fit; the player is drawn at the phone's own
// size instead, so its buttons are thumb-sized. Also leave room below the page for it.
function fitDeck() {
  const d = $("deck");
  const page = document.documentElement.clientWidth;
  const screenW = Math.min(window.screen.width || page, page);
  const phone = matchMedia("(pointer: coarse)").matches && screenW < 900;
  const z = phone ? page / screenW : 1;
  d.style.zoom = z === 1 ? "" : String(z);
  d.classList.toggle("phone", phone);
  d.style.width = phone ? `${(page - 24) / z}px` : "";
  if (!d.hidden) document.body.style.paddingBottom = `${Math.ceil(d.offsetHeight * z + 40)}px`;
}

/* In the Android app, the floating player's audio is played by the app (so it keeps going in the
   background, with notification and lock-screen controls). The page's audio element stays, with just
   the parts the player uses forwarded to the app; the app plays the whole queue of parts itself and
   reports its position twice a second. */
function nativeDeckAudio(audio) {
  const app = window.TurntableApp;
  let src = null, t = 0, at = performance.now(), paused = true, ready = 0;
  const now = () => (paused ? t : t + (performance.now() - at) / 1000);
  const fire = (name) => audio.dispatchEvent(new Event(name));
  Object.defineProperties(audio, {
    currentTime: { configurable: true, get: now,
      set(v) { t = Math.max(0, v); at = performance.now(); app.deckSeek(Math.round(t * 1000)); fire("seeked"); } },
    paused: { configurable: true, get: () => paused },
    readyState: { configurable: true, get: () => ready },
    src: { configurable: true, get: () => src || "",
      set(u) {
        src = u; ready = 0; t = 0; at = performance.now();
        const i = deck ? deck.parts.findIndex((p) => p.url === u) : -1;
        app.deckSelect(Math.max(0, i));
        setTimeout(() => { ready = 1; fire("loadedmetadata"); }, 0);
      } },
  });
  const getAttr = audio.getAttribute.bind(audio), removeAttr = audio.removeAttribute.bind(audio);
  audio.getAttribute = (n) => (n === "src" ? src : getAttr(n));
  audio.removeAttribute = (n) => {
    if (n === "src") { src = null; ready = 0; paused = true; app.deckStop(); } else removeAttr(n);
  };
  audio.load = () => {};
  audio.play = () => { paused = false; at = performance.now(); app.deckPlay(); fire("play"); return Promise.resolve(); };
  audio.pause = () => { t = now(); paused = true; app.deckPause(); fire("pause"); };
  window.turntableAppDeck = (s) => {
    if (!deck || src === null) return;
    if (typeof s.i === "number" && s.i !== deck.part && deck.parts[s.i]) { deck.part = s.i; src = deck.parts[s.i].url; }
    t = s.t; at = performance.now();
    const was = paused;
    paused = !s.playing;
    if (was !== paused) fire(paused ? "pause" : "play");
    fire("timeupdate");
    if (s.ended) fire("ended");
    if (s.error) fire("error");
  };
}

function setupDeck() {
  const audio = $("rec-audio");
  if (window.TurntableApp && window.TurntableApp.deckQueue) nativeDeckAudio(audio);
  for (const b of document.querySelectorAll("#deck [data-act]")) b.addEventListener("click", () => deckAct(b.dataset.act));
  $("deck-size").addEventListener("click", () => setDeckSize(!deckCollapsed));
  $("deck-tracks").addEventListener("click", () => toggleDeckList());
  $("deck-close").addEventListener("click", stopReplay);
  $("replay-stop").addEventListener("click", stopReplay);
  $("replay-live").addEventListener("click", stopReplay);
  document.addEventListener("click", (e) => {
    if (!$("deck-list").hidden && !e.target.closest("#deck-list, #deck-tracks")) toggleDeckList(false);
  });
  // scrubbing: press and drag anywhere on the bar; the position follows your finger, and
  // playback jumps when you let go
  const line = $("deck-line");
  const at = (e) => { const b = line.getBoundingClientRect(); return Math.min(1, Math.max(0, (e.clientX - b.left) / b.width)) * deck.total; };
  line.addEventListener("pointerdown", (e) => {
    if (!deck) return;
    line.setPointerCapture(e.pointerId);
    scrubbing = at(e); line.classList.add("dragging"); tickDeck();
  });
  line.addEventListener("pointermove", (e) => { if (scrubbing != null) { scrubbing = at(e); tickDeck(); } });
  const done = (e) => {
    if (scrubbing == null) return;
    const T = at(e); scrubbing = null; line.classList.remove("dragging"); seekDeck(T);
  };
  line.addEventListener("pointerup", done);
  line.addEventListener("pointercancel", () => { scrubbing = null; line.classList.remove("dragging"); tickDeck(); });
  line.addEventListener("keydown", (e) => {
    if (!deck) return;
    if (e.key === "ArrowRight") { e.preventDefault(); seekDeck(deckTime() + 15); }
    if (e.key === "ArrowLeft") { e.preventDefault(); seekDeck(deckTime() - 15); }
  });
  // one part ends: carry on with the next
  audio.addEventListener("ended", () => {
    if (deck && deck.part + 1 < deck.parts.length) loadPart(deck.part + 1, 0, true);
  });
  for (const ev of ["timeupdate", "play", "pause", "seeked"]) audio.addEventListener(ev, tickDeck);
  audio.addEventListener("error", () => { if (deck) toast("That recording couldn't be played.", true); });
  $("player").addEventListener("play", () => { if (!audio.paused) audio.pause(); });
  window.addEventListener("resize", () => { if (deck && !$("deck").classList.contains("embedded")) fitDeck(); });
}

async function loadRecordings() {
  try {
    const data = await getJSON("/api/recordings");
    recordings = data.recordings;
    const sum = $("flight-summary");
    sum.querySelector(".rec-dot")?.remove();
    if (data.recording_now) sum.append(el("span", { class: "rec-dot", text: "  \u25CF Recording this session" }));
  } catch { recordings = []; }
}

/* ---------- unmatched plays ---------- */

async function loadUnmatchedCount() {
  try {
    const d = await getJSON("/api/unmatched?limit=1");
    const b = $("unmatched-open");
    b.hidden = !d.total_plays;
    b.textContent = `Unmatched (${d.total_plays})`;
  } catch {}
}

async function openUnmatched() {
  $("unm-list").replaceChildren(el("p", { class: "note", text: "Loading..." }));
  $("unm-dlg").showModal();
  await renderUnmatched();
}

async function renderUnmatched() {
  const d = await getJSON("/api/unmatched?limit=40");
  const box = $("unm-list");
  box.replaceChildren();
  if (!d.groups.length) { box.append(el("p", { class: "note", text: "Nothing unmatched. Every identified play is on a record." })); return; }
  for (const g of d.groups) {
    const remember = el("input", { type: "checkbox" }); remember.checked = true;
    const link = (rid) => runEdit("link", { play_ids: g.play_ids, release_id: rid, remember: remember.checked }, "Linking...")
      .then((ok) => { if (ok) { card.remove(); loadUnmatchedCount(); } return ok; });
    const cands = el("div", { class: "unm-cands" });
    for (const c of g.candidates) {
      const b = el("button", { type: "button", class: "unm-cand", title: `${Math.round(c.score * 100)}% similar, ${c.tracks_found} of ${g.titles.length} titles found on it` },
        cover(c.cover), el("span", {}, el("b", { text: c.title }), el("small", { text: `${c.artist}${c.year ? ", " + c.year : ""}` })));
      b.addEventListener("click", () => link(c.id));
      cands.append(b);
    }
    const other = el("button", { type: "button", class: "unm-cand other", text: g.candidates.length ? "Another record..." : "Find the record..." });
    other.addEventListener("click", () => openPicker({ mode: "record", title: `Which record is "${g.album || g.titles[0]}"?`,
      hint: `${g.artist}: ${g.titles.slice(0, 4).join(", ")}`, save: (rec) => link(rec.id) }));
    cands.append(other);
    const dismiss = el("button", { type: "button", class: "dismiss", text: "Not mine" });
    dismiss.addEventListener("click", () => {
      if (confirm(`Set aside ${g.plays} ${g.plays === 1 ? "play" : "plays"} of ${g.artist}? They go to quarantine and can be restored.`))
        runEdit("dismiss", { play_ids: g.play_ids }, "Setting aside...").then((ok) => { if (ok) { card.remove(); loadUnmatchedCount(); } });
    });
    const card = el("article", { class: "unm" },
      el("div", { class: "unm-head" }, cover(g.cover), el("div", {}, el("b", { text: g.album ? `${g.album}` : g.titles[0] }),
        el("small", { text: g.artist })),
        el("small", { text: `${g.plays} ${g.plays === 1 ? "play" : "plays"}, last ${ago(g.last)}` })),
      el("p", { class: "unm-titles", text: g.titles.slice(0, 8).join(", ") + (g.titles.length > 8 ? ` and ${g.titles.length - 8} more` : "") }),
      cands,
      el("div", { class: "unm-foot" }, el("label", {}, remember, document.createTextNode("Remember this match for next time")), dismiss));
    box.append(card);
  }
  if (d.total_groups > d.groups.length) box.append(el("p", { class: "note", text: `And ${d.total_groups - d.groups.length} more groups; work through these first.` }));
}

function setupUnmatched() {
  $("unmatched-open").addEventListener("click", () => openUnmatched().catch(() => {}));
  $("unm-close").addEventListener("click", () => $("unm-dlg").close());
  $("unm-dlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
}

/* ---------- editing: tag, check off, fill, remove ---------- */

let toastTimer;
function toast(text, bad = false) {
  const t = $("toast");
  t.textContent = text;
  t.classList.toggle("bad", bad);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, bad ? 6000 : 3500);
}

/* Queue an edit, then follow it until the identifier has applied it (usually within a second). */
async function postEdit(kind, body) {
  const res = await fetch(`/api/edit/${kind}`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Turntable-Edit": "1" }, body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `Couldn't save that (${res.status}).`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  const { id } = await res.json();
  for (let i = 0; i < 40; i++) {
    await new Promise((r) => setTimeout(r, i < 5 ? 400 : 1000));
    const st = await getJSON(`/api/edit/${id}`);
    if (st.status === "done") return st.result;
    if (st.status === "failed") throw new Error(st.result);
  }
  throw new Error("Saved, but the identifier hasn't applied it yet. Is turntable-id running?");
}

async function runEdit(kind, body, busyText = "Saving...") {
  toast(busyText);
  try {
    const msg = await postEdit(kind, body);
    toast(msg);
    lastKey = undefined;            // redraw now playing too
    await poll();
    refreshPanels();
    return true;
  } catch (e) {
    toast(e.message, true);
    return false;
  }
}

/* The record picker. mode "one" picks a single track (tagging); "many" picks several.
   opts: { mode, title, hint, release, done (positions to show as already logged),
           suggest(record) -> positions to pre-tick, save(record, positions) -> Promise<bool> } */
const picker = { opts: null, rec: null };

function openPicker(opts) {
  picker.opts = opts; picker.rec = null;
  $("pick-title").textContent = opts.title;
  $("pick-hint").textContent = opts.hint || "";
  $("pick-q").value = "";
  $("pick-results").replaceChildren();
  $("pick-record").hidden = true;
  $("pick-back").hidden = true;
  $("pick-q").parentElement.hidden = false;
  $("pick-status").textContent = "";
  $("pick-ok").disabled = true;
  $("pick-ok").textContent = opts.mode === "one" ? "Tag it" : opts.mode === "record" ? "Link to this record" : "Save";
  $("pick-dlg").showModal();
  if (opts.release) chooseRecord(opts.release); else { searchPicker(""); $("pick-q").focus(); }
}

async function searchPicker(q) {
  const data = await getJSON(`/api/collection?${new URLSearchParams({ q, sort: q ? "artist" : "recent", limit: 8 })}`);
  const list = $("pick-results");
  list.replaceChildren();
  if (!data.items.length) { list.append(el("li", { class: "note", text: q ? "Nothing in the cargo hold matches that." : "" })); return; }
  for (const r of data.items) {
    const b = el("button", { type: "button" }, cover(r.cover), el("span", {}, el("b", { text: r.title }), el("small", { text: r.year ? `${r.artist}, ${r.year}` : r.artist })));
    b.addEventListener("click", () => chooseRecord(r.id));
    list.append(el("li", {}, b));
  }
}

async function chooseRecord(id) {
  const r = await getJSON(`/api/release/${id}`);
  picker.rec = r;
  $("pick-q").parentElement.hidden = true;
  $("pick-results").replaceChildren();
  $("pick-record").hidden = false;
  $("pick-back").hidden = !!picker.opts.release;
  $("pick-rec-head").replaceChildren(cover(r.cover), el("div", {}, el("b", { text: r.title }), el("small", { text: r.year ? `${r.artist}, ${r.year}` : r.artist })));
  const one = picker.opts.mode === "one";
  const done = new Set(picker.opts.done || []);
  const pre = new Set(picker.opts.suggest ? picker.opts.suggest(r) : []);
  const list = $("pick-tracks");
  list.replaceChildren();
  let side = null;
  for (const t of r.tracks) {
    if (t.side && t.side !== side) { side = t.side; list.append(el("li", { class: "side-break", text: `Side ${side}` })); }
    const logged = done.has(t.position);
    const input = el("input", { type: one ? "radio" : "checkbox", name: "pick-track", value: t.position });
    if (logged) { input.checked = true; input.disabled = true; } else if (pre.has(t.position)) input.checked = true;
    input.addEventListener("change", updatePickOk);
    list.append(el("li", {}, el("label", { class: logged ? "done" : null, title: logged ? "Already logged" : null },
      input, el("span", { class: "p", text: t.position }), el("span", { text: t.title }), el("span", { class: "d", text: t.duration || "" }))));
  }
  updatePickOk();
}

function pickedPositions() {
  return [...document.querySelectorAll('#pick-tracks input:checked:not(:disabled)')].map((i) => i.value);
}

function updatePickOk() {
  if (picker.opts && picker.opts.mode === "record") {      // choosing the record is the whole job
    $("pick-ok").disabled = !picker.rec;
    $("pick-status").textContent = "";
    document.querySelectorAll("#pick-tracks input").forEach((i) => { i.disabled = true; });
    return;
  }
  const n = pickedPositions().length;
  $("pick-ok").disabled = n === 0;
  $("pick-status").textContent = n && picker.opts.mode !== "one" ? `${n} ${n === 1 ? "track" : "tracks"} picked` : "";
}

function setupPicker() {
  let t;
  $("pick-q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => searchPicker(e.target.value.trim()).catch(() => {}), 220); });
  $("pick-close").addEventListener("click", () => $("pick-dlg").close());
  $("pick-dlg").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  $("pick-back").addEventListener("click", () => {
    picker.rec = null; $("pick-record").hidden = true; $("pick-back").hidden = true;
    $("pick-q").parentElement.hidden = false; $("pick-q").focus(); searchPicker($("pick-q").value.trim()).catch(() => {});
    updatePickOk();
  });
  $("pick-ok").addEventListener("click", async () => {
    const positions = pickedPositions();
    if (!picker.rec || (!positions.length && picker.opts.mode !== "record")) return;
    $("pick-ok").disabled = true;
    const ok = await picker.opts.save(picker.rec, positions);
    if (ok) $("pick-dlg").close(); else $("pick-ok").disabled = false;
  });
  $("tag-now").addEventListener("click", () => openPicker({
    mode: "one", title: "What's playing?", hint: "Pick the record and the track playing now. The rest of the side follows on from it.",
    save: (r, [position]) => runEdit("tag", { release_id: r.id, position }, "Tagging..."),
  }));
  $("not-this").addEventListener("click", () => openPicker({
    mode: "one", title: "What's really playing?", hint: "The wrong match goes to quarantine, and your pick takes its place.",
    save: (r, [position]) => runEdit("tag", { release_id: r.id, position }, "Correcting..."),
  }));
}

/* Pre-tick tracks for a gap: those following the last track logged before it, as many as fit. */
function suggestForGap(gap, before) {
  return (r) => {
    if (!before || before.release_id !== r.id) return [];
    const i = r.tracks.findIndex((t) => t.position === before.position);
    const out = []; let room = (gap.end - gap.start) * 1.25 + 30;   // vinyl lengths vary
    for (const t of r.tracks.slice(i + 1)) {
      const m = /^(\d+):(\d{2})$/.exec(t.duration || ""); const secs = m ? +m[1] * 60 + +m[2] : 210;
      if (secs > room) break;
      out.push(t.position); room -= secs;
    }
    return out;
  };
}

/* ---------- polling ---------- */

let lastKey;

function refreshPanels() {
  loadPet().catch(() => {});
  loadSummary().catch(() => {});
  loadTop().catch(() => {});
  loadDusty().catch(() => {});
  loadCrate().catch(() => {});
  loadChart().catch(() => {});
  loadRadar().catch(() => {});
  loadFlightLog().catch(() => {});
}

async function poll() {
  try {
    const data = await getJSON("/api/now");
    const key = `${data.state}:${data.current ? data.current.id : ""}:${data.current && data.current.side_view ? data.current.side_view.index : ""}`;
    if (key !== lastKey) {
      const first = lastKey === undefined;
      lastKey = key;
      renderNow(data);
      if (!first) refreshPanels();
    } else if (data.current && data.current.side_view) {
      side = { ...data.current.side_view, at: Date.now() / 1000 };   // resync the local clock
    }
  } catch {
    $("listen-note").textContent = "Can't reach the turntable right now. Retrying every few seconds.";
  }
}

function setupTop() {
  for (const b of document.querySelectorAll("#strip .seg button")) {
    b.addEventListener("click", () => {
      topDays = Number(b.dataset.days);
      for (const o of document.querySelectorAll("#strip .seg button")) o.setAttribute("aria-pressed", String(o === b));
      loadTop().catch(() => {});
    });
  }
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { for (const k in tapes) tapes[k].text = null; aimSay(); if (lastPet) renderPet(lastPet); loadTop().catch(() => {}); }, 200);
});
reducedMotion.addEventListener?.("change", () => { setMotion($("hero").dataset.state !== "idle"); for (const k in tapes) tapes[k].text = null; aimSay(); if (lastPet) renderPet(lastPet); });

document.addEventListener("DOMContentLoaded", async () => {
  drawGrooves();
  drawRider();
  setMotion(false);
  setupListen();
  setupTop();
  setupCrate();
  setupChart();
  setupRadar();
  setupPicker();
  setupDeck();
  setupUnmatched();
  setupNemo();
  setupLog();
  setupCollapse();
  connectTelemetry();
  requestAnimationFrame(teleFrame);
  await poll();
  refreshPanels();
  setInterval(poll, POLL_MS);
  setInterval(tickSide, 1000);
  setInterval(() => loadPet().catch(() => {}), 10 * 1000);
  setInterval(() => { loadRadar().catch(() => {}); loadSummary().catch(() => {}); }, 5 * 60 * 1000);
});


/* ---------- Google Cast: the TV view on a screen, or the stream on a speaker ----------
   Shown in Chrome once /static/cast-config.json holds your Cast app id (tools/setup_cast.sh). The
   phone hands the device the stream; the device runs /static/receiver.html, which draws the TV view. */
async function setupCast() {
  if (!window.chrome || window.TurntableApp) return;           // Cast from a page works in Chrome itself, not in the app
  let cfg = null;
  try { const r = await fetch("/static/cast-config.json", { cache: "no-store" }); if (r.ok) cfg = await r.json(); } catch (e) { return; }
  if (!cfg || !cfg.app_id) return;
  window.__onGCastApiAvailable = (ok) => { if (ok) castReady(cfg.app_id); };
  const s = document.createElement("script");
  s.src = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";
  document.head.append(s);
}

function castReady(appId) {
  const ctx = cast.framework.CastContext.getInstance();
  ctx.setOptions({ receiverApplicationId: appId, autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED });
  const btn = $("cast-btn");
  const update = () => {
    const sess = ctx.getCurrentSession();
    btn.hidden = ctx.getCastState() === cast.framework.CastState.NO_DEVICES_AVAILABLE && !sess;
    btn.classList.toggle("on", !!sess);
    $("cast-text").textContent = sess ? `On ${sess.getCastDevice().friendlyName}` : "Cast";
    btn.setAttribute("aria-label", sess ? `Stop casting to ${sess.getCastDevice().friendlyName}` : "Cast to a TV or speaker");
  };
  ctx.addEventListener(cast.framework.CastContextEventType.CAST_STATE_CHANGED, update);
  ctx.addEventListener(cast.framework.CastContextEventType.SESSION_STATE_CHANGED, (e) => {
    if (e.sessionState === cast.framework.SessionState.SESSION_STARTED) castStream(e.session);
    update();
  });
  btn.addEventListener("click", () => {
    if (ctx.getCurrentSession()) ctx.endCurrentSession(true);
    else ctx.requestSession().catch(() => {});
  });
  update();
}

function castStream(session) {
  // only one thing plays at a time: stop listening on this phone
  if ($("listen") && $("listen").getAttribute("aria-pressed") === "true") $("listen").click();
  const info = new chrome.cast.media.MediaInfo(`${location.origin}/stream`, "audio/mpeg");
  info.streamType = chrome.cast.media.StreamType.LIVE;
  const md = new chrome.cast.media.MusicTrackMediaMetadata();
  md.title = "Turntable-1";
  md.artist = "Live from the turntable";
  const art = $("label-art"), cover = art && art.getAttribute("href");
  if (cover) md.images = [new chrome.cast.Image(new URL(cover, location.origin).href)];
  info.metadata = md;
  session.loadMedia(new chrome.cast.media.LoadRequest(info)).catch((e) => toast(`Couldn't start the stream on the TV: ${e}`, true));
}

document.addEventListener("DOMContentLoaded", () => { setupCast(); });


/* ---------- mobile: the tabs ----------
   On phones (layout.js adds html.mobile), each section belongs to a tab: Now, Beepnik, Log, Cargo,
   Stars. Everything else on the page works as on the desktop. */
const isMobile = () => document.documentElement.classList.contains("mobile");

function showTab(tab, scroll = true) {
  // the Cargo tab is the collection: open it the first time
  if (tab === "cargo" && $("crate-toggle") && $("crate-toggle").getAttribute("aria-expanded") !== "true") $("crate-toggle").click();
  for (const sec of document.querySelectorAll("main > section[data-tab]")) sec.classList.toggle("tab-on", sec.dataset.tab === tab);
  for (const nav of [$("mtabs"), $("dtabs")]) if (nav)
    for (const b of nav.querySelectorAll("button")) b.setAttribute("aria-current", b.dataset.tab === tab ? "page" : "false");
  document.documentElement.dataset.tab = tab;
  try { sessionStorage.setItem("turntable-tab", tab); } catch (e) { }
  if (scroll) window.scrollTo(0, 0);
  // things that measure themselves (the star chart, Beepnik's bubble) do so once their tab shows
  requestAnimationFrame(() => { window.dispatchEvent(new Event("resize")); if (typeof aimSay === "function") aimSay(); });
}

function setupMobile() {
  if (!isMobile()) return;
  $("mtabs").hidden = false;
  $("m-say").hidden = false;
  for (const b of $("mtabs").querySelectorAll("button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  $("m-say").addEventListener("click", () => showTab("beepnik"));
  let start = "now";
  try { start = sessionStorage.getItem("turntable-tab") || "now"; } catch (e) { }
  showTab(start, false);
}

// the Now tab's Beepnik line: his face and how he feels
function renderMobileSay(st) {
  if (!isMobile() || !st || !$("m-say-who")) return;
  const md = st.mood_detail || {};
  $("m-say-who").textContent = `${(st.name || "Beepnik").toUpperCase()} \u00b7 ${(md.phrase || st.mood || "").toUpperCase()}`;
  const kind = { ecstatic: "happy", happy: "happy", content: "content", restless: "restless", glum: "glum", sleep: "sleep" }[faceFor(st)] || "content";
  $("m-face").replaceChildren(beepFace(st));
}

/* Tabs on the desktop: the same sections, switched from the header. */
function setupTabs() {
  for (const b of $("dtabs").querySelectorAll("button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  $("nowbeep-open").addEventListener("click", () => showTab("beepnik"));
  setupTalk();
  loadStory();
  setInterval(loadStory, 120000);
  if (isMobile()) return;                    // the phone's own setup picks its starting tab
  $("stage").setAttribute("viewBox", "7 7 262 262");
  let start = "now";
  try { start = sessionStorage.getItem("turntable-tab") || "now"; } catch (e) { }
  showTab(start, false);
}

document.addEventListener("DOMContentLoaded", setupMobile);
document.addEventListener("DOMContentLoaded", setupTabs);
