"use strict";
(() => {
/* Turntable-1 companion drawings, v2.0 (Nebula): Beepnik as a real probe with a face.
   Three independent layers, so they never fight over the same pixels:
     scene(orbit)   -> the backdrop      (driven by total listening)
     probe(tier)    -> attached hardware (driven by variety of styles heard)
     probe(traits)  -> surface finish    (driven by the month's dominant genres)
   Everything is drawn in a 320 x 240 box. */

const INK = "#241a52";        // ink lines, in Nebula indigo
const LID = "#d6d0ec";        // eyelids: the hull's lilac silver

/* A tiny SVG builder that produces plain text, so this file runs in the browser
   and on the Pi (Node.js) alike. Nodes support append() and setAttribute(). */
const esc = (v) => String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
class N {
  constructor(tag, attrs = {}, text) { this.tag = tag; this.attrs = { ...attrs }; this.text = text; this.kids = []; }
  append(...kids) { this.kids.push(...kids); }
  setAttribute(k, v) { this.attrs[k] = v; }
  toString() {
    const a = Object.entries(this.attrs).map(([k, v]) => ` ${k}="${esc(v)}"`).join("");
    const inner = (this.text !== undefined ? esc(this.text) : "") + this.kids.join("");
    return `<${this.tag}${a}>${inner}</${this.tag}>`;
  }
}
function S(parent, tag, attrs = {}, text) {
  const n = new N(tag, attrs, text);
  if (parent) parent.append(n);
  return n;
}

let uid = 0;
const id = (p) => `${p}${++uid}`;

/* ---------- shared gradients (one set per svg) ---------- */

function defs(svg) {
  const d = S(svg, "defs");
  const ids = {};
  const radial = (name, stops, cx = "35%", cy = "30%", r = "72%") => {
    ids[name] = id(name);
    const g = S(d, "radialGradient", { id: ids[name], cx, cy, r });
    for (const [o, c] of stops) S(g, "stop", { offset: o, "stop-color": c });
  };
  radial("chrome", [[0, "#ffffff"], [.35, "#f3f0fb"], [.75, "#c3bbdf"], [1, "#8a7fb8"]]);
  radial("brass", [[0, "#fff6d6"], [.35, "#ffd98a"], [.8, "#c98f3a"], [1, "#7a4f12"]]);
  radial("indigo", [[0, "#eef0ff"], [.35, "#a9b4f0"], [.8, "#5563c4"], [1, "#2e3584"]]);
  radial("earth", [[0, "#b4ccff"], [.5, "#6a8fe6"], [1, "#2a3a8f"]], "40%", "35%", "75%");
  radial("moon", [[0, "#f6f3fb"], [.6, "#cfc9e2"], [1, "#8f88ad"]], "38%", "35%", "75%");
  radial("nebulaA", [[0, "rgba(255,122,156,.5)"], [1, "rgba(255,122,156,0)"]], "50%", "50%", "50%");
  radial("nebulaB", [[0, "rgba(127,167,255,.5)"], [1, "rgba(127,167,255,0)"]], "50%", "50%", "50%");
  radial("glow", [[0, "rgba(127,167,255,.5)"], [1, "rgba(127,167,255,0)"]], "50%", "50%", "50%");
  radial("glass", [[0, "#ffffff"], [.22, "#9cc0ff"], [.7, "#4a6bd0"], [1, "#1c1540"]], "35%", "30%", "80%");
  ids.foil = id("foil");
  const fg = S(d, "linearGradient", { id: ids.foil, x1: 0, y1: 0, x2: 1, y2: 1 });
  for (const [o, c] of [[0, "#ffd98a"], [.5, "#e8a04f"], [1, "#ffcf6b"]]) S(fg, "stop", { offset: o, "stop-color": c });
  ids.cell = id("cell");
  const cg = S(d, "linearGradient", { id: ids.cell, x1: 0, y1: 0, x2: 1, y2: 1 });
  for (const [o, c] of [[0, "#6a7fe6"], [1, "#221a5a"]]) S(cg, "stop", { offset: o, "stop-color": c });
  return { d, ids };
}

function stars(g, n, seed, W = 320, H = 240, maxY = H) {
  let s = seed;
  const rnd = () => ((s = (s * 9301 + 49297) % 233280) / 233280);
  for (let i = 0; i < n; i++) {
    const r = rnd() < 0.12 ? 1.6 : 0.9;
    S(g, "circle", { cx: (rnd() * W).toFixed(1), cy: (rnd() * maxY).toFixed(1), r, fill: rnd() < 0.15 ? "#ffcf6b" : "#fff", opacity: (0.35 + rnd() * 0.6).toFixed(2) });
  }
}

/* ---------- layer 1: the scene (orbit) ---------- */

const ORBITS = {
  pad:   { label: "On the launch pad" },
  leo:   { label: "Low Earth orbit" },
  geo:   { label: "Geostationary orbit" },
  lunar: { label: "Lunar transfer" },
  deep:  { label: "Deep space" },
};

function scene(svg, kit, orbit) {
  const g = S(svg, "g");
  const sky = { pad: "#1f1856", leo: "#18134a", geo: "#15113f", lunar: "#120e36", deep: "#0c0a24" }[orbit];
  S(g, "rect", { width: 320, height: 240, fill: sky });

  if (orbit === "pad") {
    stars(g, 14, 3, 320, 240, 120);
    S(g, "rect", { y: 196, width: 320, height: 44, fill: "#241d5a" });
    S(g, "rect", { x: 92, y: 186, width: 136, height: 12, rx: 3, fill: "#342a78", stroke: "#4a3f96" });
    // gantry tower
    const t = S(g, "g", { stroke: "#6a5fae", "stroke-width": 2, fill: "none" });
    S(t, "path", { d: "M228 196 V70 M248 196 V70 M228 70 H248" });
    for (let y = 80; y < 196; y += 18) S(t, "path", { d: `M228 ${y} L248 ${y + 18} M248 ${y} L228 ${y + 18}` });
    S(t, "path", { d: "M228 110 H200 M228 150 H204" });
    S(g, "circle", { cx: 238, cy: 66, r: 3.5, fill: "#ff7a9c" });
  }
  if (orbit === "leo") {
    stars(g, 26, 7, 320, 240, 150);
    S(g, "circle", { cx: 160, cy: 560, r: 400, fill: `url(#${kit.ids.glow})` });
    S(g, "circle", { cx: 160, cy: 560, r: 372, fill: `url(#${kit.ids.earth})` });
    S(g, "path", { d: "M40 214 q30 -10 60 -2 t70 -4 M190 204 q25 -8 55 -1", stroke: "#e6e0ff", "stroke-width": 3, fill: "none", opacity: .5, "stroke-linecap": "round" });
    S(g, "circle", { cx: 160, cy: 560, r: 372, fill: "none", stroke: "#b4ccff", "stroke-width": 3, opacity: .8 });
  }
  if (orbit === "geo") {
    stars(g, 40, 11);
    S(g, "circle", { cx: 64, cy: 180, r: 70, fill: `url(#${kit.ids.glow})` });
    S(g, "circle", { cx: 64, cy: 180, r: 50, fill: `url(#${kit.ids.earth})` });
    S(g, "path", { d: "M30 170 q14 -8 30 0 M52 196 q16 -6 30 2", stroke: "#e6e0ff", "stroke-width": 2.5, fill: "none", opacity: .5, "stroke-linecap": "round" });
    S(g, "ellipse", { cx: 64, cy: 180, rx: 140, ry: 34, fill: "none", stroke: "#ffcf6b", "stroke-width": 1.5, "stroke-dasharray": "5 7", opacity: .7, transform: "rotate(-14 64 180)" });
  }
  if (orbit === "lunar") {
    stars(g, 55, 19);
    S(g, "circle", { cx: 34, cy: 206, r: 14, fill: `url(#${kit.ids.earth})` });
    S(g, "circle", { cx: 266, cy: 58, r: 40, fill: `url(#${kit.ids.moon})` });
    for (const [x, y, r] of [[252, 48, 7], [276, 70, 9], [282, 44, 4], [258, 78, 4]]) S(g, "circle", { cx: x, cy: y, r, fill: "#a39bc4", opacity: .55 });
    S(g, "path", { d: "M44 196 C 110 120, 170 70, 230 66", fill: "none", stroke: "#ffcf6b", "stroke-width": 1.5, "stroke-dasharray": "5 7", opacity: .75 });
  }
  if (orbit === "deep") {
    S(g, "ellipse", { cx: 90, cy: 70, rx: 150, ry: 80, fill: `url(#${kit.ids.nebulaA})` });
    S(g, "ellipse", { cx: 250, cy: 170, rx: 160, ry: 90, fill: `url(#${kit.ids.nebulaB})` });
    stars(g, 90, 29);
    // distant ringed world
    S(g, "circle", { cx: 278, cy: 40, r: 9, fill: "#ffcf6b" });
    S(g, "ellipse", { cx: 278, cy: 40, rx: 17, ry: 4, fill: "none", stroke: "#ffe3a6", "stroke-width": 1.5, transform: "rotate(-18 278 40)" });
  }
  return g;
}

/* ---------- layer 2 and 3: the probe ---------- */

const TIERS = [
  { id: "beacon",      label: "Beacon" },
  { id: "relay",       label: "Relay" },
  { id: "explorer",    label: "Explorer" },
  { id: "surveyor",    label: "Surveyor" },
  { id: "array",       label: "Array" },
  { id: "voyager",     label: "Voyager" },
  { id: "observatory", label: "Observatory" },
];

const TRAITS = {
  jazz:       "Brass plating",
  rock:       "Rivets and scorch marks",
  electronic: "LED band",
  funk:       "Mirror-ball facets",
  folk:       "Wood-grain band",
  classical:  "Gilded ring",
  hiphop:     "Speaker cone",
  screen:     "Film-strip band",
  comedy:     "Clown nose",
  stories:    "Reading glasses",
  holiday:    "Fairy lights",
  pop:        "Candy stripes",
  blues:      "Midnight-blue finish",
};

function lightAnim(el, face) {
  if (face === "sleep") { el.setAttribute("opacity", .4); return; }
  S(el, "animate", { attributeName: "opacity", values: "1;.35;1", dur: "2.6s", repeatCount: "indefinite" });
}

function probe(svg, kit, { cx = 160, cy = 112, r = 34, tier = 0, traits = [], egg = false, face = "happy", patches = 0, age = 0, generation = 1 } = {}) {
  const g = S(svg, "g");
  const body = traits.includes("jazz") ? "brass" : traits.includes("blues") ? "indigo" : "chrome";
  const metal = { chrome: "#c3bbdf", brass: "#d9a54c", indigo: "#8f9ae0" }[body];
  const line = (d, w = 3, c = "#a59bcf") => S(g, "path", { d, stroke: c, "stroke-width": w, "stroke-linecap": "round", fill: "none" });

  if (egg) {
    S(g, "ellipse", { cx, cy: cy + 8, rx: r * 0.95, ry: r * 1.2, fill: `url(#${kit.ids.chrome})`, stroke: INK, "stroke-width": 2.2 });
    const eclip = id("egg");
    S(S(kit.d, "clipPath", { id: eclip }), "ellipse", { cx, cy: cy + 8, rx: r * .95 - 1, ry: r * 1.2 - 1 });
    S(S(g, "g", { "clip-path": `url(#${eclip})` }), "rect", { x: cx - r, y: cy + r * .62, width: r * 2, height: r, fill: `url(#${kit.ids.foil})` });
    line(`M${cx - r * .62} ${cy + r * .62} l6 6 l6 -7 l6 7 l6 -7 l6 7 l6 -6`, 1.6, INK).setAttribute("opacity", .4);
    line(`M${cx} ${cy + 8 - r * 1.2} V${cy - r * 1.2 - 4}`, 2.5, metal);
    lightAnim(S(g, "circle", { cx, cy: cy - r * 1.2 - 7, r: 3.5, fill: "#ff7a9c", class: "light" }), face);
    drawFace(g, kit, cx, cy + 10, face, r);
    return g;
  }

  // Tier 2+: solar wings with gold frames and a cell grid; Array (tier 4+) adds a second section
  const panel = (x, pw, ph) => {
    S(g, "rect", { x, y: cy - ph / 2, width: pw, height: ph, rx: 3, fill: `url(#${kit.ids.cell})`, stroke: "#ffcf6b", "stroke-width": 1.8 });
    for (let i = 1; i < 4; i++) S(g, "line", { x1: x + (pw * i) / 4, x2: x + (pw * i) / 4, y1: cy - ph / 2, y2: cy + ph / 2, stroke: "#ffcf6b", "stroke-width": .7, opacity: .6 });
    S(g, "line", { x1: x, x2: x + pw, y1: cy, y2: cy, stroke: "#ffcf6b", "stroke-width": .7, opacity: .6 });
    S(g, "path", { d: `M${x + 3} ${cy - ph / 2 + 3} l${pw * .22} 0 l${-pw * .14} ${ph * .38} z`, fill: "#fff", opacity: .16 });
  };
  if (tier >= 2) {
    for (const side of [-1, 1]) {
      const pw = tier >= 4 ? r * .95 : r * 1.15, ph = r * 0.78;
      const x = side < 0 ? cx - r - 6 - pw : cx + r + 6;
      line(`M${side < 0 ? x + pw : x} ${cy} H${side < 0 ? cx - r + 2 : cx + r - 2}`, 3.5, metal);
      if (tier >= 4) {
        const pw2 = r * .82, x2 = side < 0 ? x - 5 - pw2 : x + pw + 5;
        line(`M${side < 0 ? x : x + pw} ${cy} H${side < 0 ? x2 + pw2 : x2}`, 2.5, metal);
        panel(x2, pw2, ph);
      }
      panel(x, pw, ph);
      if (side < 0 && patches > 0) {          // mission patches, stitched onto the left wing
        for (let i = 0; i < Math.min(3, patches); i++) {
          const px = x + pw * (0.2 + i * 0.3), py = cy - ph * .18;
          S(g, "circle", { cx: px, cy: py, r: r * .13, fill: "#ffcf6b", stroke: "#8a5a12", "stroke-width": 1 });
          S(g, "path", { d: `M${px} ${py - r * .08} l${r * .025} ${r * .055} l${r * .06} 0 l${-r * .048} ${r * .035} l${r * .018} ${r * .06} l${-r * .055} ${-r * .036} l${-r * .055} ${r * .036} l${r * .018} ${-r * .06} l${-r * .048} ${-r * .035} l${r * .06} 0 Z`, fill: "#b04a6a" });
        }
      }
    }
  }
  // Tier 3+: instrument boom with magnetometer, upper left
  if (tier >= 3) {
    line(`M${cx - r * .6} ${cy - r * .7} L${cx - r * 1.75} ${cy - r * 1.6}`, 2.5, metal);
    S(g, "circle", { cx: cx - r * 1.8, cy: cy - r * 1.65, r: 5, fill: "#f3f0fb", stroke: INK, "stroke-width": 1.4 });
    S(g, "circle", { cx: cx - r * 1.8, cy: cy - r * 1.65, r: 2, fill: "#ff7a9c" });
  }
  // Voyager (tier 5+): a long power boom down to the lower left, with its generator drums
  if (tier >= 5) {
    line(`M${cx - r * .75} ${cy + r * .45} L${cx - r * 2.3} ${cy + r * 1.15}`, 2.5, metal);
    for (let i = 0; i < 3; i++) {
      const t = .45 + i * .2, bx = cx - r * .75 + (-r * 1.55) * t, by = cy + r * .45 + (r * .7) * t;
      S(g, "rect", { x: bx - r * .14, y: by - r * .11, width: r * .28, height: r * .22, rx: 2, fill: "#5a5190", stroke: INK, "stroke-width": 1.1,
        transform: `rotate(24 ${bx} ${by})` });
    }
  }
  // Observatory (tier 6): telescope tube on the right
  if (tier >= 6) {
    S(g, "rect", { x: cx + r * .55, y: cy - r * 1.45, width: r * .42, height: r * .95, rx: 4, fill: "#f3f0fb", stroke: INK, "stroke-width": 1.6, transform: `rotate(28 ${cx + r * .76} ${cy - r})` });
    S(g, "ellipse", { cx: cx + r * 1.0, cy: cy - r * 1.4, rx: r * .22, ry: r * .1, fill: "#2c255f", transform: `rotate(28 ${cx + r} ${cy - r * 1.4})` });
  }

  // Sputnik whips (every tier): its signature legs, tipped in comet pink
  for (const [x0, y0, x1, y1] of [[-.45, .8, -1.5, 2.25], [-.15, .95, -.62, 2.4], [.45, .8, 1.5, 2.25], [.15, .95, .62, 2.4]]) {
    line(`M${cx + r * x0} ${cy + r * y0} L${cx + r * x1} ${cy + r * y1}`, 2.6);
    S(g, "circle", { cx: cx + r * x1, cy: cy + r * y1, r: 2.2, fill: "#ff7a9c" });
  }
  if (traits.includes("jazz")) { // a tiny horn bell on one whip
    S(g, "path", { d: `M${cx + r * 1.5} ${cy + r * 2.25} l7 2 l-2 -9 Z`, fill: "#ffd98a", stroke: "#8a5a12", "stroke-width": 1 });
  }
  // the thruster bell, underneath
  S(g, "path", { d: `M${cx - r * .2} ${cy + r * .9} L${cx - r * .32} ${cy + r * 1.28} L${cx + r * .32} ${cy + r * 1.28} L${cx + r * .2} ${cy + r * .9} Z`, fill: metal, stroke: INK, "stroke-width": 1.3 });
  S(g, "ellipse", { cx, cy: cy + r * 1.28, rx: r * .32, ry: r * .07, fill: "#6a5fae", stroke: INK, "stroke-width": 1.1 });

  // Classical: a gilded ring behind and in front of the body
  const ring = (front) => {
    const e = S(g, "ellipse", { cx, cy, rx: r * 1.5, ry: r * .38, fill: "none", stroke: "#ffcf6b", "stroke-width": 3, transform: `rotate(-14 ${cx} ${cy})` });
    if (front) e.setAttribute("stroke-dasharray", `${r * 2.4} ${r * 9}`), e.setAttribute("stroke-dashoffset", `${-r * 0.3}`);
    return e;
  };
  if (traits.includes("classical")) ring(false);

  // The hull
  S(g, "circle", { cx, cy, r, fill: `url(#${kit.ids[body]})`, stroke: INK, "stroke-width": 2.2 });

  // On the hull, clipped to it: gold thermal foil below the equator, the riveted band, panel seams
  const clipId = id("body");
  S(S(kit.d, "clipPath", { id: clipId }), "circle", { cx, cy, r: r - 1.1 });
  const skin = S(g, "g", { "clip-path": `url(#${clipId})` });
  S(skin, "path", { d: `M${cx - r} ${cy + r * .36} Q${cx} ${cy + r * .58} ${cx + r} ${cy + r * .36} L${cx + r} ${cy + r} L${cx - r} ${cy + r} Z`, fill: `url(#${kit.ids.foil})` });
  for (let i = 0; i < 9; i++) {
    const x = cx - r + r * .12 + i * r * .23;
    S(skin, "path", { d: `M${x} ${cy + r * .55} l${r * .07} ${r * .12} l${-r * .06} ${r * .11} l${r * .08} ${r * .13}`, stroke: "#c4813a", "stroke-width": 1, fill: "none", opacity: .7 });
  }
  S(skin, "path", { d: `M${cx - r} ${cy + r * .27} Q${cx} ${cy + r * .5} ${cx + r} ${cy + r * .27}`, stroke: metal, "stroke-width": r * .1, fill: "none" });
  for (let i = 0; i <= 10; i++) {
    const t = i / 10, x = cx - r * .92 + t * r * 1.84, y = cy + r * .29 + r * .19 * (1 - (2 * t - 1) ** 2);
    S(skin, "circle", { cx: x, cy: y, r: Math.max(.8, r * .028), fill: "#7b72a8" });
  }
  for (const dx of [-.55, .55]) S(skin, "path", { d: `M${cx + dx * r * .25} ${cy - r} Q${cx + dx * r} ${cy - r * .4} ${cx + dx * r * .7} ${cy + r * .28}`, stroke: "#8a7fb8", "stroke-width": 1, fill: "none", opacity: .45 });
  S(skin, "ellipse", { cx: cx - r * .42, cy: cy - r * .55, rx: r * .3, ry: r * .16, fill: "#fff", opacity: .5, transform: `rotate(-30 ${cx - r * .42} ${cy - r * .55})` });

  // Surface traits, clipped to the hull
  if (traits.includes("folk")) {
    S(skin, "rect", { x: cx - r, y: cy + r * .52, width: r * 2, height: r * .36, fill: "#8a5a32" });
    for (let i = 0; i < 3; i++) S(skin, "path", { d: `M${cx - r} ${cy + r * (.6 + i * .1)} q${r * .5} ${-r * .05} ${r} 0 t${r} 0`, stroke: "#5e3a1c", "stroke-width": 1, fill: "none" });
  }
  if (traits.includes("pop")) {
    for (let i = -6; i < 6; i++) S(skin, "path", { d: `M${cx + i * r * .32} ${cy + r} l${r * .5} ${-r * .6}`, stroke: i % 2 ? "#ff7a9c" : "#8fe3c0", "stroke-width": r * .14, opacity: .85 });
  }
  if (traits.includes("funk")) {
    for (let x = -r; x < r; x += r * .28) for (let y = -r; y < r * .3; y += r * .28)
      S(skin, "rect", { x: cx + x + 1, y: cy + y + 1, width: r * .24, height: r * .24, fill: "#fff", opacity: ((Math.abs(x * 7 + y * 3) % 5) / 10 + .04).toFixed(2) });
  }
  if (traits.includes("rock")) {
    S(skin, "ellipse", { cx: cx - r * .55, cy: cy + r * .62, rx: r * .5, ry: r * .28, fill: "#2a1a10", opacity: .35 });
    S(skin, "ellipse", { cx: cx + r * .62, cy: cy - r * .6, rx: r * .3, ry: r * .16, fill: "#2a1a10", opacity: .25 });
  }
  if (traits.includes("electronic")) {
    S(skin, "rect", { x: cx - r, y: cy + r * .58, width: r * 2, height: r * .2, fill: "#1c1540" });
    for (let i = -3; i <= 3; i++) S(skin, "circle", { cx: cx + i * r * .22, cy: cy + r * .68, r: r * .055, fill: i % 2 ? "#7fa7ff" : "#ff7a9c" });
  }
  if (traits.includes("rock")) {
    for (let i = -3; i <= 3; i++) {
      const x = cx + i * r * .2, y = cy + r * .7 - (i * i) * r * .012;
      S(g, "circle", { cx: x, cy: y, r: 1.7, fill: "#5a5190", stroke: INK, "stroke-width": .6 });
    }
  }
  if (traits.includes("hiphop")) {
    const sx = cx + r * .58, sy = cy + r * .6;
    S(g, "circle", { cx: sx, cy: sy, r: r * .28, fill: "#1c1540", stroke: INK, "stroke-width": 1.4 });
    S(g, "circle", { cx: sx, cy: sy, r: r * .17, fill: "none", stroke: "#6a5fae", "stroke-width": 1.2 });
    S(g, "circle", { cx: sx, cy: sy, r: r * .07, fill: "#c3bbdf" });
  }
  if (traits.includes("screen")) {          // a film strip band, sprocket holes top and bottom
    S(skin, "rect", { x: cx - r, y: cy + r * .56, width: r * 2, height: r * .3, fill: "#1c1540" });
    for (let i = -6; i <= 6; i++) for (const yy of [cy + r * .6, cy + r * .78])
      S(skin, "rect", { x: cx + i * r * .16 - r * .035, y: yy, width: r * .07, height: r * .045, rx: .6, fill: "#f3f0fb", opacity: .85 });
  }
  if (age > 30) {                             // weathering: a veteran's patina
    const n = age > 90 ? 22 : 10;
    for (let i = 0; i < n; i++) {
      const a = (i * 137.5) * Math.PI / 180, d = r * (.25 + ((i * 53) % 70) / 100);
      S(skin, "circle", { cx: cx + Math.cos(a) * d, cy: cy + Math.sin(a) * d, r: 1.2 + (i % 3) * .6, fill: "#5a4228", opacity: age > 90 ? .42 : .28 });
    }
  }
  if (generation > 1) {                       // the generation, painted on the foil
    const numeral = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"][Math.min(10, generation)];
    S(g, "text", { x: cx - r * .72, y: cy + r * .82, "font-size": r * .3, "font-weight": 900, "font-family": "Righteous, sans-serif",
      fill: "#b04a6a", transform: `rotate(-14 ${cx - r * .72} ${cy + r * .82})` }, numeral);
  }
  if (tier >= 5) {                            // Voyager's golden record
    const gx = cx + r * .62, gy = cy + r * .62;
    S(g, "circle", { cx: gx, cy: gy, r: r * .26, fill: "#e0aa3e", stroke: "#8a5a12", "stroke-width": 1.3 });
    for (const rr of [.19, .13]) S(g, "circle", { cx: gx, cy: gy, r: r * rr, fill: "none", stroke: "#8a5a12", "stroke-width": .6, opacity: .7 });
    S(g, "circle", { cx: gx, cy: gy, r: r * .05, fill: "#8a5a12" });
  }
  if (traits.includes("classical")) ring(true);
  if (traits.includes("funk")) for (const [x, y, s] of [[cx + r * 1.2, cy - r * .9, 1], [cx - r * 1.25, cy - r * .2, .7], [cx + r * .9, cy + r * .9, .6]])
    S(g, "path", { d: `M${x} ${y - 7 * s} L${x + 2 * s} ${y - 2 * s} L${x + 7 * s} ${y} L${x + 2 * s} ${y + 2 * s} L${x} ${y + 7 * s} L${x - 2 * s} ${y + 2 * s} L${x - 7 * s} ${y} L${x - 2 * s} ${y - 2 * s}Z`, fill: "#ffcf6b" });

  // Tier 1+: dish on top (the Observatory gets the big one)
  if (tier >= 1) {
    const big = tier >= 6, w = big ? r * 1.3 : r * .62, top = cy - r - (big ? 22 : 14);
    line(`M${cx} ${cy - r + 1} V${top + (big ? 8 : 5)}`, 2.5, metal);
    S(g, "path", { d: `M${cx - w / 2} ${top} Q${cx} ${top + (big ? 22 : 13)} ${cx + w / 2} ${top} Z`, fill: "#f3f0fb", stroke: INK, "stroke-width": 1.6 });
    line(`M${cx} ${top + (big ? 10 : 6)} V${top - (big ? 9 : 6)}`, 1.5, INK);
    lightAnim(S(g, "circle", { cx, cy: top - (big ? 10 : 7), r: big ? 2.8 : 2.2, fill: "#ff7a9c", class: "light" }), face);
  }

  drawFace(g, kit, cx, cy, face, r);
  const ey = cy - r * .14, dx = r * .3, er = r * .17;
  if (traits.includes("stories")) {           // round reading glasses, framing his lenses
    for (const x of [cx - dx, cx + dx]) S(g, "circle", { cx: x, cy: ey, r: er + r * .1, fill: "rgba(200,230,255,.12)", stroke: "#3a2a1a", "stroke-width": 1.8 });
    S(g, "path", { d: `M${cx - dx + er + r * .1} ${ey} q${dx - er - r * .1} ${-r * .1} ${(dx - er - r * .1) * 2} 0`, stroke: "#3a2a1a", "stroke-width": 1.8, fill: "none" });
  }
  if (traits.includes("comedy")) {            // a red clown nose
    S(g, "circle", { cx, cy: cy + r * .05, r: r * .13, fill: "#e5432d", stroke: "#8a1f12", "stroke-width": 1.1 });
    S(g, "circle", { cx: cx - r * .04, cy: cy + r * .01, r: r * .04, fill: "#fff", opacity: .7 });
  }
  if (traits.includes("holiday")) {           // a string of fairy lights draped across
    const pts = [];
    for (let i = 0; i <= 10; i++) { const t = i / 10, x = cx - r * 1.05 + t * r * 2.1, y = cy + r * .78 + Math.sin(t * Math.PI) * r * .22; pts.push([x, y]); }
    S(g, "path", { d: "M" + pts.map((p) => p.join(" ")).join(" L"), stroke: "#2f6b3a", "stroke-width": 1.6, fill: "none" });
    const cols = ["#ff7a9c", "#ffcf6b", "#7fa7ff", "#8fe3c0", "#c3b8ee"];
    pts.slice(1, -1).forEach(([x, y], i) => S(g, "ellipse", { cx: x, cy: y + r * .07, rx: r * .055, ry: r * .085, fill: cols[i % cols.length] }));
  }
  return g;
}

/* Idle life, as SVG's own animation (it runs in every browser, iPhone Safari included, and is
   too small to count as motion): his eyelids blink now and then, with the odd double blink, and
   his pupils glance to one side and the other inside the lenses. */
const BLINK_TIMES = "0;.57;.585;.6;.61;.625;.64;.95;.965;.98;1";

/* His face: two camera lenses with eyelids and brows, and a mouth. The brows carry most of the
   feeling; heavy lids only when he's glum or asleep. */
const FACES = {
  //            upper lid   lower lid  brow lift (left, right)  brow tilt  mouth
  ecstatic: { top: 0,    bottom: .45, brows: [-.12, -.12], tilt: 0,    mouth: "grin",  blush: .4 },
  happy:    { top: 0,    bottom: 0,   brows: [-.08, -.08], tilt: 0,    mouth: "smile", blush: .28 },
  content:  { top: .08,  bottom: 0,   brows: [-.03, -.03], tilt: 0,    mouth: "small", blush: 0 },
  restless: { top: 0,    bottom: 0,   brows: [-.13, -.01], tilt: 0,    mouth: "flat",  blush: 0, dart: true },
  glum:     { top: .48,  bottom: 0,   brows: [.02, .02],   tilt: .1,   mouth: "frown", blush: 0 },
  sleep:    { top: 1,    bottom: 0,   brows: [.04, .04],   tilt: 0,    mouth: "o",     blush: 0 },
};

function drawFace(g, kit, cx, cy, face, r) {
  const F = FACES[face] || FACES.happy;
  const ey = cy - r * .14, dx = r * .3, er = r * .17, w = Math.max(1.6, r * .065);
  const look = S(g, "g");                     // pupils, which glance inside the lenses
  for (const [n, x] of [[0, cx - dx], [1, cx + dx]]) {
    const clip = id("lens");
    S(S(kit.d, "clipPath", { id: clip }), "circle", { cx: x, cy: ey, r: er });
    S(g, "circle", { cx: x, cy: ey, r: er + r * .045, fill: "#d6d0ec", stroke: "#8a7fb8", "stroke-width": .8 });   // the bezel
    S(g, "circle", { cx: x, cy: ey, r: er, fill: `url(#${kit.ids.glass})` });
    const lens = S(g, "g", { "clip-path": `url(#${clip})` });
    const pupil = S(lens, "g");
    S(pupil, "circle", { cx: x, cy: ey - er * .1, r: er * .48, fill: "#1c1540" });
    S(pupil, "circle", { cx: x - er * .2, cy: ey - er * .32, r: er * .19, fill: "#fff" });
    S(pupil, "animateTransform", { attributeName: "transform", type: "translate", dur: F.dart ? "3.2s" : "14s", repeatCount: "indefinite",
      keyTimes: "0;.28;.32;.46;.5;.68;.72;.82;.86;1", values: F.dart ? `0 0;${-er * .4} 0;${-er * .4} 0;${er * .4} 0;${er * .4} 0;0 0;0 0;${er * .3} 0;0 0;0 0`
        : `0 0;0 0;${-er * .3} 0;${-er * .3} 0;0 0;0 0;${er * .3} ${-er * .12};${er * .3} ${-er * .12};0 0;0 0` });
    // the upper lid: resting where the mood puts it, closing all the way to blink
    const rest = F.top * er * 2;
    const lid = S(lens, "rect", { x: x - er - 1, y: ey - er - 1, width: er * 2 + 2, height: Math.max(.01, rest), fill: LID });
    if (face !== "sleep") {
      const c = (er * 2 + 1).toFixed(2), o = Math.max(.01, rest).toFixed(2);
      S(lid, "animate", { attributeName: "height", dur: "6.2s", repeatCount: "indefinite", keyTimes: BLINK_TIMES,
                         values: [o, o, c, o, o, c, o, o, c, o, o].join(";") });
    }
    if (face === "sleep")                     // closed: a lash line, curving down
      S(lens, "path", { d: `M${x - er * .7} ${ey + er * .05} Q${x} ${ey + er * .55} ${x + er * .7} ${ey + er * .05}`, stroke: INK, "stroke-width": w * .8, fill: "none", "stroke-linecap": "round" });
    else if (rest > er * .2) S(lens, "line", { x1: x - er, x2: x + er, y1: ey - er + rest, y2: ey - er + rest, stroke: INK, "stroke-width": 1.2 });
    if (F.bottom) {                           // the lower lid, rising in a curve: smiling eyes
      const y = ey + er - F.bottom * er * 2;
      S(lens, "path", { d: `M${x - er - 1} ${ey + er + 1} L${x - er - 1} ${y + er * .3} Q${x} ${y - er * .5} ${x + er + 1} ${y + er * .3} L${x + er + 1} ${ey + er + 1} Z`, fill: LID });
      S(lens, "path", { d: `M${x - er - 1} ${y + er * .3} Q${x} ${y - er * .5} ${x + er + 1} ${y + er * .3}`, stroke: INK, "stroke-width": 1.2, fill: "none" });
    }
    S(g, "circle", { cx: x, cy: ey, r: er, fill: "none", stroke: INK, "stroke-width": 1.1 });
    // the brow: a small arc on the hull above the lens; glum tilts it, inner end up
    const by = ey - er - r * .13 + F.brows[n] * r, side = n === 0 ? -1 : 1, tilt = F.tilt * r * side;
    S(g, "path", { d: `M${x - er * .9} ${by + er * .2 - tilt} Q${x} ${by - er * .35} ${x + er * .9} ${by + er * .2 + tilt}`,
                   stroke: INK, "stroke-width": w * .85, fill: "none", "stroke-linecap": "round" });
  }
  // the mouth, above the equator band
  const my = cy + r * .12, m = (d, fill = "none") => S(g, "path", { d, fill, stroke: INK, "stroke-width": w, "stroke-linecap": "round", "stroke-linejoin": "round" });
  if (F.mouth === "grin") m(`M${cx - r * .2} ${my - r * .03} Q${cx} ${my + r * .26} ${cx + r * .2} ${my - r * .03} Z`, INK);
  else if (F.mouth === "smile") m(`M${cx - r * .17} ${my - r * .02} Q${cx} ${my + r * .15} ${cx + r * .17} ${my - r * .02}`);
  else if (F.mouth === "small") m(`M${cx - r * .11} ${my} Q${cx} ${my + r * .08} ${cx + r * .11} ${my}`);
  else if (F.mouth === "flat") m(`M${cx - r * .12} ${my + r * .03} L${cx + r * .12} ${my + r * .03}`);
  else if (F.mouth === "frown") m(`M${cx - r * .14} ${my + r * .08} Q${cx} ${my - r * .05} ${cx + r * .14} ${my + r * .08}`);
  else S(g, "ellipse", { cx, cy: my + r * .04, rx: r * .05, ry: r * .065, fill: INK });
  if (F.blush) for (const x of [cx - dx - r * .2, cx + dx + r * .2]) S(g, "ellipse", { cx: x, cy: ey + r * .26, rx: r * .1, ry: r * .06, fill: "#ff7a9c", opacity: F.blush });
}

/* The companion picture as SVG markup (inner content, 320 x 240 coordinates).
   layers: "all" (default), "scene" (backdrop only) or "probe" (probe on transparent).
   dim:    darken the scene, for night mode on the round screen. */
function companionMarkup({ orbit = "leo", tier = 0, traits = [], egg = false, face = "happy", layers = "all", dim = false, beeps = true,
                           patches = 0, age = 0, generation = 1 } = {}) {
  const root = new N("g");
  const kit = defs(root);
  if (layers !== "probe") {
    scene(root, kit, orbit);
    if (dim) S(root, "rect", { width: 320, height: 240, fill: "#000", opacity: .55 });
  }
  if (layers !== "scene") {
    const cy = egg ? 140 : 108, r = 34;
    const body = probe(root, kit, { tier, traits, egg, face, cy, r, patches, age, generation });
    body.setAttribute("class", "critter-body");
    if (dim) body.setAttribute("opacity", .85);
    if (face === "sleep") {
      S(root, "text", { x: 196, y: cy - 34, class: "zzz", "font-size": 16, fill: "#a99fd0", "font-family": "Righteous, sans-serif" }, "z");
      S(root, "text", { x: 210, y: cy - 50, class: "zzz", "font-size": 22, fill: "#a99fd0", "font-family": "Righteous, sans-serif" }, "Z");
    } else if (beeps) {      // the round screen draws its own, on the beat
      const b = S(root, "g", { class: "beeps", fill: "none", stroke: "#ff7a9c", "stroke-width": 2.6, "stroke-linecap": "round" });
      S(b, "path", { d: `M200 ${cy - 46} q6 7 0 14` });
      S(b, "path", { d: `M209 ${cy - 53} q11 14 0 28` });
    }
  }
  return root.toString();
}

/* Browser: draw into an existing <svg>. */
function companion(svg, opts = {}) {
  svg.setAttribute("viewBox", "0 0 320 240");
  svg.innerHTML = companionMarkup(opts);
}

/* A complete standalone SVG document, for rendering to an image on the Pi.
   viewBox picks the crop; the round screen uses the centre 240 x 240. */
function svgDocument(inner, { width = 240, height = 240, viewBox = "40 0 240 240" } = {}) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${viewBox}">${inner}</svg>`;
}

/* The round screen's "now spinning" background: vinyl, grooves, label ring and the
   title and artist curved around the edge. The label art itself is a separate image
   the device rotates, so it isn't drawn here. */
function recordMarkup({ title = "", artist = "", withText = true } = {}) {
  const root = new N("g");
  const d = S(root, "defs");
  S(d, "path", { id: "arcTop", d: "M 34 120 A 86 86 0 0 1 206 120" });
  S(d, "path", { id: "arcBot", d: "M 22 120 A 98 98 0 0 0 218 120" });
  S(root, "rect", { width: 240, height: 240, fill: "#0e1730" });
  S(root, "circle", { cx: 120, cy: 120, r: 104, fill: "#151517" });
  for (let i = 0; i < 26; i++) S(root, "circle", { cx: 120, cy: 120, r: 50 + i * 2, fill: "none", stroke: "#222226", "stroke-width": .8 });
  S(root, "circle", { cx: 120, cy: 120, r: 45, fill: "#3a4a6e" });
  S(root, "circle", { cx: 120, cy: 120, r: 45, fill: "none", stroke: "#aeb9c6", "stroke-width": 3 });
  if (!withText) return root.toString();   // the Pi adds the lettering itself (rsvg can't curve text)
  const clip = (t, n) => (t.length > n ? t.slice(0, n - 1).trimEnd() + "\u2026" : t);
  const top = S(root, "text", { "font-family": "Righteous, sans-serif", "font-size": 15, fill: "#eef2f6", "text-anchor": "middle" });
  S(top, "textPath", { href: "#arcTop", startOffset: "50%" }, clip(title, 26));
  const bot = S(root, "text", { "font-family": "Nunito, sans-serif", "font-weight": 800, "font-size": 12, fill: "#f2b544", "text-anchor": "middle" });
  S(bot, "textPath", { href: "#arcBot", startOffset: "50%" }, clip(artist, 34));
  return root.toString();
}

const api = { companion, companionMarkup, recordMarkup, svgDocument, ORBITS, TIERS, TRAITS };
if (typeof window !== "undefined") window.Probe = api;
if (typeof module !== "undefined") module.exports = api;
})();
