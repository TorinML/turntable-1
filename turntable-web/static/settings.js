"use strict";
/* Turntable-1's settings page: sign in, then every setting by section. Changes are kept while you
   move between sections and saved together; the server checks them all before anything is written. */

const $ = (id) => document.getElementById(id);
function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) n.append(c);
  return n;
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data = {};
  try { data = await r.json(); } catch (e) { }
  return { ok: r.ok, status: r.status, data };
}
let toastTimer = null;
function toast(text, bad = false) {
  const t = $("toast");
  t.textContent = text; t.className = bad ? "st-toast bad" : "st-toast"; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
}

/* --- signing in ------------------------------------------------------------------------------------ */
async function start() {
  const s = (await api("/api/admin/session")).data;
  if (s.version) $("version").textContent = s.version === "dev" ? "development build" : `v${s.version.replace(/^v/, "")}`;
  if (s.signed_in) return load();
  const setup = !s.configured;
  $("gate").hidden = false;
  $("gate-title").textContent = setup ? "Create an admin password" : "Sign in";
  $("gate-about").textContent = setup ? "It protects your settings and editing. At least 8 characters; you'll use it to sign in on each device."
    : "Settings and editing need the admin password.";
  $("gate-pw2-row").hidden = !setup;
  $("gate-pw").setAttribute("autocomplete", setup ? "new-password" : "current-password");
  $("gate-go").textContent = setup ? "Create and sign in" : "Sign in";
  $("gate-form").onsubmit = async (e) => {
    e.preventDefault();
    const pw = $("gate-pw").value;
    if (setup && pw !== $("gate-pw2").value) { $("gate-err").textContent = "The two passwords don't match."; return; }
    const r = await api(setup ? "/api/admin/setup" : "/api/admin/login", { password: pw });
    if (!r.ok) { $("gate-err").textContent = r.data.detail || "That didn't work."; return; }
    $("gate").hidden = true;
    load();
  };
  $("gate-pw").focus();
}

/* --- the settings ---------------------------------------------------------------------------------- */
let pages = [], categories = [], current = null, query = "", pending = { values: {}, secrets: {} }, errors = {};
const ACCOUNT = { id: "account", category: "System", title: "Account", groups: [], advanced: [] };
const fieldsOf = (p) => [...p.groups, ...p.advanced].flatMap((g) => g.fields);

async function load(keep) {
  const r = await api("/api/settings");
  if (r.status === 401) { location.reload(); return; }
  pages = r.data.pages || [];
  categories = r.data.categories || [];
  $("main").hidden = false;
  $("signout").hidden = false;
  if (!keep) { pending = { values: {}, secrets: {} }; errors = {}; }
  renderNav();
  show(current && [...pages, ACCOUNT].some((p) => p.id === current) ? current : pages[0].id);
  renderBar();
}

function hasPending(p) { return fieldsOf(p).some((f) => f.path in pending.values || (f.kind === "secret" && f.path.slice(7) in pending.secrets)); }
function hasErrors(p) { return fieldsOf(p).some((f) => errors[f.path]); }

function renderNav() {
  const nav = $("nav"), pick = $("pick");
  const search = el("input", { type: "search", class: "st-search", placeholder: "Search settings", "aria-label": "Search settings" });
  search.value = query;
  search.addEventListener("input", () => { query = search.value.trim(); if (query) showSearch(); else show(current === "search" ? pages[0].id : current); });
  nav.replaceChildren(search); pick.replaceChildren();
  for (const cat of [...categories, ...(categories.includes("System") ? [] : ["System"])]) {
    const list = [...pages, ACCOUNT].filter((p) => p.category === cat);
    if (!list.length) continue;
    nav.append(el("div", { class: "st-cat", text: cat }));
    const og = el("optgroup", { label: cat });
    for (const p of list) {
      const mark = hasErrors(p) ? " \u26A0" : hasPending(p) ? " \u2022" : "";
      const b = el("button", { type: "button", "aria-current": String(p.id === current) }, p.title, mark ? el("span", { class: "dot", text: mark }) : null);
      b.addEventListener("click", () => { query = ""; show(p.id); });
      nav.append(b);
      og.append(el("option", { value: p.id, selected: p.id === current ? true : null, text: p.title + mark }));
    }
    pick.append(og);
  }
  pick.onchange = () => { query = ""; show(pick.value); };
}

function section(title, about, applies) {
  const box = el("section", { class: "st-sec" }, el("h2", {}, title,
    applies ? el("span", { class: `applies ${applies}`, text: applies === "live" ? "APPLIES RIGHT AWAY" : "APPLIES AFTER A RESTART" }) : null));
  if (about) box.append(el("p", { text: about }));
  return box;
}

function show(id) {
  current = id;
  renderNav();
  const p = [...pages, ACCOUNT].find((x) => x.id === id);
  if (id === "account") { $("body").replaceChildren(accountSection()); return; }
  const box = section(p.title, p.about, p.applies);
  for (const g of p.groups) {
    if (g.title) box.append(el("h3", { class: "st-group", text: g.title }));
    for (const f of g.fields) box.append(field(f));
  }
  if (p.advanced.length) {
    const n = p.advanced.reduce((k, g) => k + g.fields.length, 0);
    const d = el("details", { class: "adv" }, el("summary", { text: `Advanced (${n})` }));
    if (p.advanced.some((g) => g.fields.some((f) => errors[f.path] || f.path in pending.values))) d.open = true;
    for (const g of p.advanced) {
      if (g.title) d.append(el("h3", { class: "st-group", text: g.title }));
      for (const f of g.fields) d.append(field(f));
    }
    box.append(d);
  }
  $("body").replaceChildren(box);
  window.scrollTo(0, 0);
}

function showSearch() {
  current = "search";
  const q = query.toLowerCase();
  const box = section(`Settings matching \u201c${query}\u201d`, "", null);
  let n = 0;
  for (const p of pages) for (const g of [...p.groups, ...p.advanced]) for (const f of g.fields) {
    if (!`${f.label} ${f.help || ""} ${f.path}`.toLowerCase().includes(q)) continue;
    n++;
    box.append(el("div", { class: "st-where", text: [p.category, p.title, g.title].filter(Boolean).join(" \u203a ") }), field(f));
  }
  if (!n) box.append(el("p", { text: "Nothing matches. Try another word." }));
  $("body").replaceChildren(box);
  for (const b of $("nav").querySelectorAll("button")) b.setAttribute("aria-current", "false");
}

/* one setting: its input, its default, a reset, and any error */
const show_ = (v) => (Array.isArray(v) || (v && typeof v === "object") ? JSON.stringify(v) : String(v));
function asText(f, v) {
  if (f.kind === "list") return (v || []).join(", ");
  if (f.kind === "json") return JSON.stringify(v, null, 1);
  return v === null || v === undefined ? "" : String(v);
}
function same(f, a, b) {
  if (f.kind === "json") { try { return JSON.stringify(JSON.parse(a)) === JSON.stringify(b); } catch (e) { return false; } }
  if (f.kind === "list") return a.split(",").map((x) => x.trim()).filter(Boolean).join("|") === (b || []).map(String).join("|");
  if (f.kind === "int" || f.kind === "float") return a !== "" && Number(a) === Number(b);
  return String(a) === String(b);
}
function parsed(f, raw) {
  if (f.kind === "int" || f.kind === "float") return raw === "" ? raw : Number(raw);
  return raw;                       // lists and JSON are checked by the server
}

function field(f) {
  const row = el("div", { class: "st-field", "data-path": f.path });
  const lab = el("div", { class: "lab" }, el("b", { text: f.label }), f.help ? el("small", { text: f.help }) : null);
  const ctl = el("div", { class: "ctl" });
  row.append(lab, ctl);
  if (f.kind === "secret") {
    const key = f.path.slice(7);
    const state = el("span", { class: "secret-state", text: key in pending.secrets ? "Will be replaced" : f.set ? "Set" : "Not set" });
    const input = el("input", { type: "password", autocomplete: "new-password", placeholder: f.set ? "Type a new value to replace it" : "Not set", hidden: !(key in pending.secrets) ? true : null });
    if (key in pending.secrets) input.value = pending.secrets[key];
    const btn = el("button", { type: "button", class: "st-btn", text: f.set ? "Replace" : "Set" });
    btn.addEventListener("click", () => { input.hidden = false; btn.hidden = true; input.focus(); });
    input.addEventListener("input", () => {
      if (input.value) pending.secrets[key] = input.value; else delete pending.secrets[key];
      state.textContent = input.value ? "Will be replaced" : f.set ? "Set" : "Not set";
      row.classList.toggle("changed", !!input.value); renderBar(); markNav();
    });
    ctl.append(state, input, btn);
    if (key in pending.secrets) { btn.hidden = true; row.classList.add("changed"); }
  } else {
    let input;
    const start = f.path in pending.values ? pending.values[f.path] : f.value;
    if (f.kind === "bool") {
      input = el("input", { type: "checkbox", class: "switch", role: "switch" });
      input.checked = !!start;
    } else if (f.choices) {
      input = el("select", {}, ...f.choices.map((c) => el("option", { value: String(c), text: c === "" ? "Provider's default" : String(c) })));
      input.value = String(start);
    } else if (f.kind === "json") {
      input = el("textarea", { spellcheck: "false" });
      input.value = typeof start === "string" ? start : asText(f, start);
    } else {
      input = el("input", { type: f.kind === "int" || f.kind === "float" ? "number" : "text", step: f.kind === "float" ? "any" : null,
        min: f.min, max: f.max, spellcheck: "false" });
      input.value = f.kind === "list" && typeof start !== "string" ? asText(f, start) : (start ?? "");
    }
    const value = () => (f.kind === "bool" ? input.checked : input.value);
    const differs = (v, base) => (f.kind === "bool" ? v !== base : !same(f, v, base));
    const update = () => {
      const v = value();
      if (differs(v, f.value)) pending.values[f.path] = f.kind === "bool" ? v : f.choices && typeof f.choices[0] === "number" ? Number(v) : parsed(f, v);
      else delete pending.values[f.path];
      delete errors[f.path];
      row.classList.toggle("changed", f.path in pending.values);
      const err = row.querySelector(".err"); if (err) err.remove();
      reset.hidden = f.default === null || f.default === undefined || !differs(v, f.default);
      def.hidden = reset.hidden;
      renderBar(); markNav();
    };
    input.addEventListener(f.kind === "bool" || f.choices ? "change" : "input", update);
    const reset = el("button", { type: "button", class: "reset", title: "Back to the default", "aria-label": `Reset ${f.label} to its default`, text: "\u21BA" });
    reset.addEventListener("click", () => {
      if (f.kind === "bool") input.checked = !!f.default;
      else input.value = f.choices ? String(f.default) : asText(f, f.default);
      update();
    });
    ctl.append(input, reset);
    const def = el("div", { class: "def", text: `Default: ${f.default === "" ? "(empty)" : show_(f.default)}` });
    row.append(def);
    reset.hidden = def.hidden = f.default === null || f.default === undefined || !differs(value(), f.default);
    if (f.path in pending.values) row.classList.add("changed");
  }
  if (errors[f.path]) row.append(el("div", { class: "err", role: "alert", text: `This ${errors[f.path]}.` }));
  return row;
}

/* the nav's change and error marks, without rebuilding it (keeps the search box's focus) */
function markNav() {
  for (const b of $("nav").querySelectorAll("button")) {
    const p = [...pages, ACCOUNT].find((x) => x.title === b.firstChild.textContent);
    if (!p) continue;
    const mark = hasErrors(p) ? " \u26A0" : hasPending(p) ? " \u2022" : "";
    let dot = b.querySelector(".dot");
    if (!mark) { if (dot) dot.remove(); continue; }
    if (!dot) { dot = el("span", { class: "dot" }); b.append(dot); }
    dot.textContent = mark;
  }
}

/* --- saving ---------------------------------------------------------------------------------------- */
function count() { return Object.keys(pending.values).length + Object.keys(pending.secrets).length; }
function renderBar() {
  const n = count();
  $("bar").hidden = n === 0;
  $("bar-text").textContent = `${n} unsaved ${n === 1 ? "change" : "changes"}`;
}
$("discard").addEventListener("click", () => { pending = { values: {}, secrets: {} }; errors = {}; renderBar(); if (current === "search") showSearch(); else show(current); });
$("save").addEventListener("click", async () => {
  $("save").disabled = true;
  const r = await api("/api/settings", pending);
  $("save").disabled = false;
  if (r.status === 401) { toast("Your sign-in ran out. Sign in again; your changes are still here.", true); return; }
  if (!r.ok) {
    errors = (r.data && r.data.errors) || {};
    const n = Object.keys(errors).length;
    toast(`${n} ${n === 1 ? "setting needs" : "settings need"} fixing; nothing was saved.`, true);
    const first = pages.find((p) => hasErrors(p));
    query = "";
    show(first ? first.id : current);
    return;
  }
  const restart = r.data.restart || [];
  toast(restart.length ? `Saved. Restart Turntable-1 to apply the changes to: ${restart.join(", ")}.` : "Saved. It applies right away.");
  await load(false);
});

/* --- the account ----------------------------------------------------------------------------------- */
function accountSection() {
  const cur = el("input", { type: "password", autocomplete: "current-password", placeholder: "Current password" });
  const pw = el("input", { type: "password", autocomplete: "new-password", placeholder: "New password (at least 8 characters)", minlength: "8" });
  const pw2 = el("input", { type: "password", autocomplete: "new-password", placeholder: "New password again" });
  const err = el("p", { class: "st-err", role: "alert" });
  const go = el("button", { type: "button", class: "st-btn gold", text: "Change the password" });
  go.addEventListener("click", async () => {
    if (pw.value !== pw2.value) { err.textContent = "The two new passwords don't match."; return; }
    const r = await api("/api/admin/password", { current: cur.value, password: pw.value });
    if (!r.ok) { err.textContent = r.data.detail || "That didn't work."; return; }
    cur.value = pw.value = pw2.value = ""; err.textContent = "";
    toast("Password changed. Other devices will need to sign in again.");
  });
  return el("section", { class: "st-sec" }, el("h2", { text: "Account" }),
    el("p", { text: "The admin password protects settings and editing. Changing it signs every other device out." }),
    el("div", { class: "acct" }, cur, pw, pw2, err, go));
}
$("signout").addEventListener("click", async () => { await api("/api/admin/logout", {}); location.reload(); });

document.addEventListener("DOMContentLoaded", start);
