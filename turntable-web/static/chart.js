"use strict";
/* Turntable-1 cosmic logbook.
   Every record you own is a star system, placed in its genre's constellation.
   Positions are derived from the Discogs release id, so the map never reshuffles.
     uncharted (never spun)  -> a faint ring
     charted                 -> a glowing star, brighter and larger with spins
     planets                 -> the tracks you've heard, orbiting their star (when zoomed in)
     flight path             -> stars joined in the order you first spun them, ending at the probe */
(() => {
const NS = "http://www.w3.org/2000/svg";
const W = 1400, H = 820;

// Constellation centres. Their size grows with how many records they hold (see layout).
const SECTORS = {
  rock:       { label: "Rock",                  x: 330,  y: 260, color: "#ff5c82" },
  pop:        { label: "Pop",                   x: 690,  y: 130, color: "#7fe0c8" },
  jazz:       { label: "Jazz",                  x: 990,  y: 160, color: "#ffd16f" },
  classical:  { label: "Classical",             x: 1275, y: 120, color: "#ffe8b8" },
  funk:       { label: "Funk / Soul",           x: 640,  y: 380, color: "#ff7e9c" },
  hiphop:     { label: "Hip Hop",               x: 820,  y: 590, color: "#afa0eb" },
  electronic: { label: "Electronic",            x: 1185, y: 440, color: "#6694ff" },
  folk:       { label: "Folk, World & Country", x: 220,  y: 690, color: "#ffa366" },
  blues:      { label: "Blues",                 x: 470,  y: 650, color: "#80a6ff" },
  screen:     { label: "Soundtracks & Scores",  x: 1030, y: 400, color: "#9ab8ff" },
  comedy:     { label: "Comedy",                x: 120,  y: 150, color: "#ffa366" },
  stories:    { label: "Stories & Spoken Word", x: 120,  y: 470, color: "#9be7c9" },
  holiday:    { label: "Holiday",               x: 1050, y: 745, color: "#ff5c82" },
  other:      { label: "Uncharted reaches",     x: 1270, y: 650, color: "#aea4d0" },
};
const MIN_GAP = 22;      // map units kept between neighbouring stars

function S(parent, tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  if (parent) parent.append(n);
  return n;
}

// Small deterministic hash -> [0, 1)
function hash01(n, salt) {
  let x = (n * 2654435761 + salt * 40503) >>> 0;
  x ^= x >>> 16; x = Math.imul(x, 2246822507) >>> 0; x ^= x >>> 13; x = Math.imul(x, 3266489909) >>> 0; x ^= x >>> 16;
  return (x >>> 0) / 4294967296;
}

/* Where every star goes. Each record has a home spot from its Discogs id (so it doesn't wander),
   inside a constellation sized by how many records it holds; then a gentle pass nudges
   overlapping stars apart while pulling each back toward its home. */
function layout(records) {
  const groups = new Map();
  for (const r of records) {
    const k = SECTORS[r.sector] ? r.sector : "other";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  const pos = new Map(), radius = {};
  for (const [k, list] of groups) {
    const s = SECTORS[k];
    const R = 48 + 15 * Math.sqrt(list.length);
    radius[k] = R;
    const pts = list.slice().sort((a, b) => a.id - b.id).map((r) => {
      const a = hash01(r.id, 1) * Math.PI * 2, d = Math.sqrt(hash01(r.id, 2)) * R;
      const hx = s.x + Math.cos(a) * d * 1.2, hy = s.y + Math.sin(a) * d * .85;
      return { id: r.id, x: hx, y: hy, hx, hy };
    });
    for (let it = 0; it < 60; it++) {
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const a = pts[i], b = pts[j];
          let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy);
          if (d >= MIN_GAP) continue;
          if (d < .01) { dx = hash01(a.id + b.id, 3) - .5; dy = .5 - hash01(a.id * b.id, 4); d = Math.hypot(dx, dy); }
          const push = (MIN_GAP - d) / 2;
          a.x -= dx / d * push; a.y -= dy / d * push; b.x += dx / d * push; b.y += dy / d * push;
        }
      }
      for (const p of pts) { p.x += (p.hx - p.x) * .04; p.y += (p.hy - p.y) * .04; }
    }
    for (const p of pts) pos.set(p.id, { x: p.x, y: p.y });
  }
  return { pos, radius };
}

function place(rec) { return layout([rec]).pos.get(rec.id); }

// Star sizes in screen pixels: brighter with spins, growing only gently as you zoom in.
const starPx = (spins) => (spins ? Math.min(10, 3.2 + 1.6 * Math.sqrt(spins)) : 2.8);

function sparkle(g, x, y, r, color) {
  S(g, "path", { d: `M${x} ${y - r} L${x + r * .18} ${y - r * .18} L${x + r} ${y} L${x + r * .18} ${y + r * .18} L${x} ${y + r} L${x - r * .18} ${y + r * .18} L${x - r} ${y} L${x - r * .18} ${y - r * .18}Z`, fill: color, opacity: .9 });
}

/* draw(svg, records, { view, layout, pxWidth, focus, fullPath, match, highlight, filtering }) */
function draw(svg, records, opts = {}) {
  const view = opts.view || [0, 0, W, H];
  const lay = opts.layout || layout(records);
  const pos = lay.pos;
  svg.replaceChildren();
  svg.setAttribute("viewBox", view.join(" "));
  // Screen pixels per map unit. Everything below is sized in screen pixels, then divided by
  // `sc`, so text, rings and lines stay the same size on screen at any zoom.
  const sc = (opts.pxWidth || 1092) / view[2];
  const px = (n) => n / sc;
  const grow = Math.pow(Math.max(1, sc / .78), .3);      // stars swell a little when zoomed in
  const zoomed = sc >= 1.6;

  const defs = S(svg, "defs");
  const glow = S(defs, "filter", { id: "starglow", x: "-200%", y: "-200%", width: "500%", height: "500%" });
  S(glow, "feGaussianBlur", { stdDeviation: px(2.6), result: "b" });
  const m = S(glow, "feMerge"); S(m, "feMergeNode", { in: "b" }); S(m, "feMergeNode", { in: "SourceGraphic" });
  for (const [k, s] of Object.entries(SECTORS)) {
    const g = S(defs, "radialGradient", { id: `neb-${k}` });
    S(g, "stop", { offset: 0, "stop-color": s.color, "stop-opacity": .15 });
    S(g, "stop", { offset: 1, "stop-color": s.color, "stop-opacity": 0 });
  }

  S(svg, "rect", { x: -400, y: -400, width: W + 800, height: H + 800, fill: "#09061e" });
  for (let i = 0; i < 380; i++) S(svg, "circle", { cx: hash01(i, 9) * W, cy: hash01(i, 10) * H, r: px(.7 + hash01(i, 12) * .6),
    fill: "#fff", opacity: (.12 + hash01(i, 11) * .35).toFixed(2) });

  // constellations: a soft nebula sized to the group, and its name
  for (const [k, R] of Object.entries(lay.radius)) {
    const s = SECTORS[k];
    S(svg, "ellipse", { cx: s.x, cy: s.y, rx: R * 1.75, ry: R * 1.25, fill: `url(#neb-${k})` });
    S(svg, "text", { x: s.x, y: s.y - R * .85 - px(18), "text-anchor": "middle", fill: s.color, opacity: .8,
      "font-family": "Righteous, sans-serif", "font-size": px(16) }, s.label);
  }

  // flight path: first spins in order (the recent journey by default)
  const charted = records.filter((r) => r.spins && r.first_spin).sort((a, b) => a.first_spin - b.first_spin);
  const legs = opts.fullPath ? charted : charted.slice(-(opts.recentLegs || 12) - 1);
  for (let i = 1; i < legs.length; i++) {
    const a = pos.get(legs[i - 1].id), b = pos.get(legs[i].id);
    const age = (legs.length - 1 - i) / Math.max(1, legs.length - 1);
    S(svg, "path", { d: `M${a.x.toFixed(1)} ${a.y.toFixed(1)} L${b.x.toFixed(1)} ${b.y.toFixed(1)}`, fill: "none",
      stroke: "#ffd16f", "stroke-width": px(1.5), "stroke-dasharray": `${px(5)} ${px(6)}`, "stroke-linecap": "round",
      opacity: ((opts.fullPath ? .3 : .85 - age * .7) * (opts.filtering ? .25 : 1)).toFixed(2) });
  }

  const match = opts.match || (() => true);
  const top = new Set([...charted].sort((a, b) => b.spins - a.spins).slice(0, 5).map((r) => r.id));
  const labels = [];
  for (const r of records) {
    const p = pos.get(r.id), s = SECTORS[r.sector] || SECTORS.other;
    const rad = px(starPx(r.spins) * grow);
    const focusSet = opts.focus instanceof Set ? opts.focus : new Set(opts.focus != null ? [opts.focus] : []);
    const hit = match(r), focused = focusSet.has(r.id);
    const g = S(svg, "g", { class: "star", "data-id": r.id, opacity: hit ? 1 : .14 });
    S(g, "circle", { cx: p.x, cy: p.y, r: Math.max(rad + px(4), px(11)), fill: "transparent" });   // easy to point at
    if (opts.highlight && hit) S(g, "circle", { cx: p.x, cy: p.y, r: rad + px(6), fill: "none", stroke: "#ffd16f", "stroke-width": px(1.5) });
    if (!r.spins) {
      S(g, "circle", { cx: p.x, cy: p.y, r: rad, fill: "none", stroke: hit && opts.highlight ? "#ffd16f" : "#5d4c94", "stroke-width": px(1.2) });
    } else {
      if (zoomed) {
        // planets: one per track heard; only the star you're pointing at opens its orbits.
        // Planets charted in the last day glow gold.
        const freshPlanets = new Set(r.fresh_tracks || []);
        let i = 0;
        (r.tracks || []).forEach((plays, ti) => {
          if (plays <= 0) return;
          const orbit = rad + px(focused ? 9 + i * 7 : 5 + i * 3.2);
          const a = hash01(r.id * 31 + i, 5) * Math.PI * 2;
          if (focused) S(g, "circle", { cx: p.x, cy: p.y, r: orbit, fill: "none", stroke: s.color, "stroke-width": px(.8), opacity: .5 });
          const isFresh = freshPlanets.has(ti);
          S(g, "circle", { cx: p.x + Math.cos(a) * orbit, cy: p.y + Math.sin(a) * orbit,
            r: px(focused ? Math.min(3.4, 1.6 + plays * .4) + (isFresh ? .6 : 0) : (isFresh ? 1.8 : 1.2)),
            fill: isFresh ? "#ffd16f" : "#e5dffb", opacity: focused || isFresh ? 1 : .65 });
          i++;
        });
      }
      // charted in the last day: a pulsing dashed ring for a new system, a thin ring for new planets
      if (r.fresh) S(g, "circle", { class: "fresh-ring", cx: p.x, cy: p.y, r: rad + px(8), fill: "none", stroke: "#ffd16f",
        "stroke-width": px(1.6), "stroke-dasharray": `${px(3)} ${px(3)}` });
      else if ((r.fresh_tracks || []).length) S(g, "circle", { cx: p.x, cy: p.y, r: rad + px(6), fill: "none", stroke: "#ffd16f",
        "stroke-width": px(1), opacity: .8 });
      S(g, "circle", { cx: p.x, cy: p.y, r: rad, fill: s.color, filter: "url(#starglow)" });
      S(g, "circle", { cx: p.x, cy: p.y, r: rad * .45, fill: "#fff", opacity: .85 });
      if (top.has(r.id)) sparkle(g, p.x, p.y, rad * 2.4, s.color);
    }
    if (zoomed && r.title && hit) labels.push({ r, p, rad, g, rank: focused ? 1e9 : (opts.highlight ? 1e8 : 0) + r.spins * 10 + (r.spins ? 5 : 0) });
  }

  // Titles: the star you're pointing at first, then your most-spun; skip any that would collide.
  const placed = [];
  const fs = 13;
  for (const L of labels.sort((a, b) => b.rank - a.rank)) {
    if (!L.r.spins && sc < 3 && L.rank < 1e8) continue;          // unplayed titles only when zoomed well in
    const text = L.r.title.length > 34 ? L.r.title.slice(0, 33) + "\u2026" : L.r.title;
    const w = px(text.length * fs * .56 + 8), h = px(fs + 6);
    const box = { x: L.p.x - w / 2, y: L.p.y + L.rad + px(6), w, h };
    if (placed.some((b) => box.x < b.x + b.w && b.x < box.x + box.w && box.y < b.y + b.h && b.y < box.y + box.h)) continue;
    placed.push(box);
    S(L.g, "text", { x: L.p.x, y: box.y + px(fs), "text-anchor": "middle", fill: L.r.spins ? "#ece8fc" : "#8d7bc8",
      "font-family": "Nunito, sans-serif", "font-weight": 700, "font-size": px(fs),
      "paint-order": "stroke", stroke: "#09061e", "stroke-width": px(3) }, text);
  }

  // the probe at the newest charted star
  const last = charted[charted.length - 1];
  if (last && opts.probe !== false) {
    const p = pos.get(last.id), sz = px(16);
    const g = S(svg, "g", { transform: `translate(${p.x + sz * .9} ${p.y - sz * 1.1})` });
    for (const [x2, y2] of [[-sz * .9, sz * .9], [-sz * .5, sz * 1.1], [sz * .5, sz * 1.1], [sz * .9, sz * .9]])
      S(g, "path", { d: `M0 0 L${x2} ${y2}`, stroke: "#aea4d0", "stroke-width": sz * .08, "stroke-linecap": "round" });
    S(g, "circle", { r: sz * .5, fill: "#cfc3f8", stroke: "#0d0925", "stroke-width": sz * .07 });
  }
  return pos;
}

/* ---------- interactive chart ---------- */

function mount(svg, { tip, onOpen }) {
  const st = { records: [], layout: null, view: [0, 0, W, H], focus: null, pinned: null, fullPath: false, sectors: null, query: "" };
  const pxw = () => svg.getBoundingClientRect().width || 1092;
  const isZoomed = () => pxw() / st.view[2] >= 1.6;
  const pointers = new Map();
  let drag = null, redrawTimer = null;

  const clampView = (v) => {
    let [x, y, w, h] = v;
    w = Math.max(170, Math.min(W, w)); h = w * H / W;
    x = Math.max(-w * .3, Math.min(W - w * .7, x)); y = Math.max(-h * .3, Math.min(H - h * .7, y));
    return [x, y, w, h];
  };
  const matches = (r) => {
    if (st.sectors && !st.sectors.has(r.sector)) return false;
    if (!st.query) return true;
    return `${r.title} ${r.artist}`.toLowerCase().includes(st.query);
  };
  const render = () => {
    draw(svg, st.records, { view: st.view, layout: st.layout, pxWidth: pxw(),
                            focus: new Set([st.focus, st.pinned].filter((x) => x != null)), fullPath: st.fullPath,
                            match: matches, highlight: !!st.query, filtering: !!(st.query || st.sectors) });
    placePinned();
  };
  const soon = () => { svg.setAttribute("viewBox", st.view.join(" ")); clearTimeout(redrawTimer); redrawTimer = setTimeout(render, 120); };
  const toChart = (cx, cy) => {
    const b = svg.getBoundingClientRect();
    return [st.view[0] + (cx - b.left) / b.width * st.view[2], st.view[1] + (cy - b.top) / b.height * st.view[3]];
  };
  const zoomAt = (factor, cx, cy) => {
    const [px, py] = toChart(cx, cy);
    const w = st.view[2] / factor, h = w * H / W;
    const b = svg.getBoundingClientRect();
    st.view = clampView([px - (cx - b.left) / b.width * w, py - (cy - b.top) / b.height * h, w, h]);
    soon();
  };

  svg.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(Math.pow(1.0015, -e.deltaY), e.clientX, e.clientY); }, { passive: false });
  svg.addEventListener("pointerdown", (e) => {
    svg.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    // Note the star under the finger now: with pointer capture, later events target the svg itself.
    const star = e.target.closest && e.target.closest(".star");
    drag = { x: e.clientX, y: e.clientY, view: [...st.view], moved: false, pinch: null, star };
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      drag.pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), w: st.view[2] };
    }
  });
  svg.addEventListener("pointermove", (e) => {
    if (drag && pointers.has(e.pointerId)) {
      pointers.set(e.pointerId, [e.clientX, e.clientY]);
      const b = svg.getBoundingClientRect();
      if (drag.pinch && pointers.size === 2) {
        const [p1, p2] = [...pointers.values()];
        const d = Math.hypot(p1[0] - p2[0], p1[1] - p2[1]);
        zoomAt(st.view[2] / (drag.pinch.w * drag.pinch.d / d), (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2);
        drag.moved = true;
        return;
      }
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved) {
        st.view = clampView([drag.view[0] - dx / b.width * drag.view[2], drag.view[1] - dy / b.height * drag.view[3], drag.view[2], drag.view[3]]);
        svg.setAttribute("viewBox", st.view.join(" "));
        if (st.pinned == null) tip.hidden = true; else placePinned();
      }
      return;
    }
    hover(e);
  });
  const end = (e) => {
    pointers.delete(e.pointerId);
    if (drag && !drag.moved && e.type === "pointerup" && pointers.size === 0) {
      const id = drag.star ? Number(drag.star.dataset.id) : null;
      if (id == null) unpin();
      else if (st.pinned === id) onOpen(id);
      else pin(id);
    }
    if (pointers.size === 0) { if (drag && drag.moved) render(); drag = null; }
  };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("pointerleave", () => {
    if (st.focus != null) { st.focus = null; soon(); }
    if (st.pinned == null) tip.hidden = true; else showCard(st.pinned);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && st.pinned != null) unpin(); });
  tip.querySelector(".tip-open").addEventListener("click", () => { if (st.pinned != null) onOpen(st.pinned); });

  function fillCard(id) {
    const r = st.records.find((x) => x.id === id);
    if (!r) return false;
    tip.querySelector("img").src = r.cover || "";
    tip.querySelector("img").hidden = !r.cover;
    tip.querySelector("b").textContent = r.title;
    tip.querySelector(".t-artist").textContent = r.year ? `${r.artist}, ${r.year}` : r.artist;
    const heard = (r.tracks || []).filter((t) => t > 0).length;
    tip.querySelector(".t-spins").textContent = r.spins
      ? `${r.spins} ${r.spins === 1 ? "spin" : "spins"}, ${heard} of ${r.tracks.length} tracks heard`
      : "Uncharted: never spun here";
    tip.querySelector("em").textContent = r.first_spin
      ? `First charted ${new Date(r.first_spin * 1000).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}` : "";
    tip.classList.toggle("pinned", st.pinned === id);
    return true;
  }

  function putCard(x, y) {
    // x, y: the star's position inside the chart box, in pixels
    const box = svg.parentElement.getBoundingClientRect();
    tip.hidden = false;
    let left = x + 22, top = y + 22;
    if (left + tip.offsetWidth > box.width - 8) left = x - tip.offsetWidth - 22;
    if (top + tip.offsetHeight > box.height - 8) top = y - tip.offsetHeight - 22;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  }

  function starPoint(id) {
    const p = st.layout && st.layout.pos.get(id);
    if (!p) return null;
    const b = svg.getBoundingClientRect();
    return [(p.x - st.view[0]) / st.view[2] * b.width, (p.y - st.view[1]) / st.view[3] * b.height, b];
  }

  function showCard(id) {
    if (!fillCard(id)) return;
    const pt = starPoint(id);
    if (!pt) return;
    const [x, y, b] = pt;
    if (x < -20 || y < -20 || x > b.width + 20 || y > b.height + 20) { tip.hidden = true; return; }   // panned out of view
    putCard(x, y);
  }

  function placePinned() { if (st.pinned != null && (st.focus == null || st.focus === st.pinned)) showCard(st.pinned); }

  function pin(id) { st.pinned = id; st.focus = id; render(); }
  function unpin() { st.pinned = null; tip.hidden = true; tip.classList.remove("pinned"); render(); }

  function hover(e) {
    const g = e.target.closest && e.target.closest(".star");
    const id = g ? Number(g.dataset.id) : null;
    if (id !== st.focus) { st.focus = id; if (isZoomed()) soon(); }
    if (id == null) {                       // back over empty sky: return to the pinned star, if any
      if (st.pinned != null) showCard(st.pinned); else tip.hidden = true;
      return;
    }
    if (!fillCard(id)) return;
    const box = svg.parentElement.getBoundingClientRect();
    putCard(e.clientX - box.left, e.clientY - box.top);
  }

  return {
    setRecords(records) { st.records = records; st.layout = layout(records); render(); },
    zoom(factor) { const b = svg.getBoundingClientRect(); zoomAt(factor, b.left + b.width / 2, b.top + b.height / 2); },
    reset() { st.view = [0, 0, W, H]; render(); },
    setFullPath(on) { st.fullPath = on; render(); },
    setSectors(set) { st.sectors = set; render(); },
    setQuery(q) { st.query = q.trim().toLowerCase(); render(); },
  };
}

window.StarChart = { draw, mount, SECTORS, place, W, H };
})();
