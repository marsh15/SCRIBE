"""The single-page review UI (plain HTML/CSS/JS, no build step, no external requests). Everything coming from items or
documents is inserted with textContent, never as HTML."""

PAGE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>scribe-eval review (__KIND__)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#f6f6f3;--ink:#1c1c1a;--mute:#6b6b66;--line:#d8d8d2;--card:#fff;--ok:#1f7a3d;--bad:#b3261e;--hit:#ffe066;--accent:#2b5cab}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:5;background:var(--card);border-bottom:1px solid var(--line);padding:8px 16px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.bar{flex:1;min-width:240px;height:12px;border-radius:6px;background:#e7e7e1;overflow:hidden;display:flex}
.bar .rv{background:var(--ok)}.bar .dr{background:var(--bad)}
.mute{color:var(--mute)}.small{font-size:12px}
main{display:grid;grid-template-columns:minmax(300px,420px) 1fr;gap:16px;padding:16px;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:12px}
h2{font-size:15px;margin:0 0 8px}h3{font-size:13px;margin:0 0 6px}
dl{margin:0}dt{font-weight:600;font-size:12px;color:var(--mute);margin-top:8px;text-transform:uppercase;letter-spacing:.03em}
dd{margin:2px 0 0;white-space:pre-wrap;word-break:break-word}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid var(--line)}
.badge.ok{background:#e4f4e9;color:var(--ok);border-color:#b7dfc3}
.warn{background:#fdeceb;color:var(--bad);border:1px solid #f1b8b4;border-radius:6px;padding:6px 10px;font-weight:700;margin:6px 0}
.problems{background:#fff6e0;border:1px solid #ecd59a;border-radius:6px;padding:6px 10px;margin-bottom:10px}
.src{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}
.src.single{grid-template-columns:1fr}
.src img{max-width:100%;border:1px solid var(--line);border-radius:4px;background:#fff}
.txt{white-space:pre-wrap;word-break:break-word;font:12.5px/1.5 ui-monospace,Menlo,monospace;max-height:560px;overflow:auto;position:relative;border:1px solid var(--line);border-radius:4px;padding:8px;background:#fcfcfa}
mark{background:var(--hit);padding:1px 0;outline:2px solid #e0b800}
.quote{font-style:italic;margin:4px 0 6px}
footer{position:sticky;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:8px 16px}
button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:6px;padding:5px 12px;cursor:pointer}
button kbd{font-weight:700;margin-right:4px}button.ok{border-color:var(--ok);color:var(--ok)}button.bad{border-color:var(--bad);color:var(--bad)}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
input[type=text],textarea,select{font:inherit;width:100%;border:1px solid var(--line);border-radius:6px;padding:5px 8px;background:#fff}
textarea{min-height:60px;resize:vertical}textarea.json{min-height:320px;font:12.5px ui-monospace,Menlo,monospace}
.panel{background:#fffef6;border:2px solid var(--accent);border-radius:8px;padding:12px;margin-bottom:12px}
.panel label{display:block;font-weight:600;font-size:12px;color:var(--mute);margin:8px 0 2px;text-transform:uppercase}
.ev{border:1px solid var(--line);border-radius:6px;padding:8px;margin:6px 0;background:#fff}
.ev .grid{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:6px}
.errs{color:var(--bad);font-weight:600;white-space:pre-wrap;margin-top:8px}
.toast{color:var(--ok);font-weight:600}
[hidden]{display:none!important}
</style></head><body>
<header>
  <div class="row"><strong>scribe-eval review</strong><span class="badge">__KIND__</span>
    <span class="mute small">reviewer: __REVIEWER__</span>
    <div class="bar" id="bar" title="reviewed / dropped / remaining"><span class="rv" id="bar-rv"></span><span class="dr" id="bar-dr"></span></div>
    <span id="counts" class="small"></span></div>
</header>
<main>
  <section id="left"><div class="card" id="itemcard"><span class="mute">Loading...</span></div></section>
  <section id="right"></section>
</main>
<div style="padding:0 16px"><div id="editpanel" class="panel" hidden></div><div id="droppanel" class="panel" hidden></div></div>
<footer><div class="row">
  <button class="ok" id="b-accept"><kbd>a</kbd>accept</button>
  <button id="b-edit"><kbd>e</kbd>edit</button>
  <button class="bad" id="b-drop"><kbd>d</kbd>drop</button>
  <button id="b-next"><kbd>n</kbd>next (skip)</button>
  <button id="b-prev"><kbd>p</kbd>previous</button>
  <input type="text" id="note" placeholder="optional review note (saved with accept / edit)" style="flex:1;min-width:220px">
  <span id="msg" class="small"></span>
</div></footer>
<script>
"use strict";
const $ = (s) => document.querySelector(s);
let cur = null;      // item payload currently shown
let state = null;
function h(tag, attrs, ...kids){
  const e = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs || {})) { if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v); }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
}
function say(text, bad){ const m=$("#msg"); m.textContent=text||""; m.style.color = bad ? "var(--bad)" : "var(--ok)"; }
async function api(path, body){
  const opt = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  let j = null; try { j = await r.json(); } catch(_) {}
  if (!r.ok) { const e = new Error((j && j.errors ? j.errors.join("\n") : r.statusText)); e.errors = j && j.errors || [r.statusText]; throw e; }
  return j;
}
function renderProgress(s){
  state = s; const t = Math.max(1, s.total + s.dropped);
  $("#bar-rv").style.width = (100*s.reviewed/t)+"%"; $("#bar-dr").style.width = (100*s.dropped/t)+"%";
  $("#counts").textContent = `${s.reviewed} reviewed / ${s.dropped} dropped / ${s.remaining} remaining`;
}
function fmt(v){ return typeof v === "string" ? v : JSON.stringify(v); }
function renderItem(d){
  cur = d; closePanels(); $("#note").value = "";
  const card = $("#itemcard"); card.replaceChildren();
  card.append(h("h2", {}, d.id, " ", h("span", {class: "badge" + (d.reviewed ? " ok" : "")}, d.reviewed ? "reviewed" : "pending"),
    " ", h("span", {class:"mute small"}, `${d.position} of ${d.count} in review order`)));
  if (d.problems.length) card.append(h("div", {class:"problems"}, h("strong", {}, "This item fails validation:"), ...d.problems.map(p => h("div", {}, "- " + p))));
  const dl = h("dl"); for (const f of d.fields) dl.append(h("dt", {}, f.key), h("dd", {}, fmt(f.value)));
  card.append(dl);
  const rk = Object.keys(d.review); if (rk.length) { card.append(h("hr")); card.append(h("div", {class:"mute small"}, rk.map(k => `${k}: ${d.review[k]}`).join("  |  "))); }
  const right = $("#right"); right.replaceChildren();
  if (!d.sources.length) right.append(h("div", {class:"card mute"}, "This item has no evidence to show."));
  for (const s of d.sources) right.append(renderSource(s));
}
function renderSource(s){
  const card = h("div", {class:"card"});
  const pg = s.page ? `, page ${s.page}` + (s.page_count ? ` of ${s.page_count}` : "") : "";
  card.append(h("h3", {}, `${s.label}: ${s.doc}${pg}`, s.meta ? h("span", {class:"mute small"}, "  (" + s.meta + ")") : null));
  if (s.needle) card.append(h("div", {class:"quote"}, "“" + s.needle + "”"));
  if (s.warning) card.append(h("div", {class:"warn"}, s.warning));
  for (const n of s.notes) card.append(h("div", {class:"mute small"}, n));
  const body = h("div", {class: "src" + (s.image_url ? "" : " single")});
  if (s.image_url) body.append(h("img", {src: s.image_url, alt: `${s.doc} page ${s.page}`, loading:"lazy"}));
  if (s.mode !== "missing") {
    const txt = h("div", {class:"txt"});
    for (const g of s.segments) txt.append(g.hit ? h("mark", {}, g.t) : document.createTextNode(g.t));
    body.append(txt);
    requestAnimationFrame(() => { const m = txt.querySelector("mark"); if (m) txt.scrollTop = Math.max(0, m.offsetTop - txt.clientHeight/3); });
  }
  card.append(body); return card;
}
async function refreshState(){ renderProgress(await api("/api/state")); }
async function show(id){
  if (!id) { await refreshState(); $("#itemcard").replaceChildren(h("h2", {}, "Nothing left to decide"), h("div", {class:"mute"}, "Every item is reviewed or dropped. Press p to look back, or stop the server.")); $("#right").replaceChildren(); cur = null; return; }
  try { renderItem(await api("/api/items/" + encodeURIComponent(id))); } catch(e) { say(e.message, true); }
}
function closePanels(){ $("#editpanel").hidden = true; $("#droppanel").hidden = true; }
function inForm(){ const a = document.activeElement; return a && ["INPUT","TEXTAREA","SELECT"].includes(a.tagName); }

async function decide(kind, body){
  if (!cur) return;
  try {
    const j = await api(`/api/items/${encodeURIComponent(cur.id)}/${kind}`, body);
    renderProgress(j.state); say(kind + " saved"); await show(j.next); return true;
  } catch(e) { say("not saved", true); return e; }
}
const accept = () => cur && decide("accept", {note: $("#note").value});
async function go(dir){ if (!cur) { if (dir==="next") await show((await api("/api/nav")).id); return; }
  const j = await api(`/api/nav?frm=${encodeURIComponent(cur.id)}&direction=${dir}&pending=${dir==="next"}`); if (j.id) await show(j.id); else say("no other item"); }

// ---- edit
function field(label, el, help){ return h("div", {}, h("label", {}, label + (help ? " (" + help + ")" : "")), el); }
function evRow(ev){
  const row = h("div", {class:"ev"});
  const i = (name, val, ph) => h("input", {type:"text", "data-k":name, value: val === null || val === undefined ? "" : String(val), placeholder: ph});
  row.append(h("div", {class:"grid"}, i("doc", ev.doc, "doc"), i("version", ev.version, "version"), i("page", ev.page, "page (empty = none)"), i("grade", ev.grade, "grade 1|2")));
  const q = h("textarea", {"data-k":"quote", placeholder:"quote (max 200 chars, exactly once in the document)"}); q.value = ev.quote || "";
  row.append(h("label", {}, "quote"), q, h("button", {type:"button", class:"bad"}, "remove evidence"));
  row.querySelector("button").onclick = () => row.remove();
  return row;
}
function openEdit(){
  if (!cur) return; closePanels();
  const p = $("#editpanel"); p.replaceChildren(h("h2", {}, "Edit " + cur.id));
  const ed = cur.editor, raw = cur.raw; const get = {};
  for (const f of ed.fields) {
    if (f.type === "json") { const t = h("textarea", {class:"json", spellcheck:"false"}); t.value = JSON.stringify(raw, null, 2); p.append(field("item as JSON (validated on save)", t)); get[f.name] = () => t.value; }
    else if (f.type === "textarea") { const t = h("textarea"); t.value = raw[f.name] || ""; p.append(field(f.name, t)); get[f.name] = () => t.value; }
    else if (f.type === "text") { const t = h("input", {type:"text"}); t.value = raw[f.name] || ""; p.append(field(f.name, t)); get[f.name] = () => t.value; }
    else if (f.type === "bool") { const c = h("input", {type:"checkbox"}); c.checked = !!raw[f.name]; p.append(field(f.name, c)); get[f.name] = () => c.checked; }
    else if (f.type === "lines") { const t = h("textarea"); const v = raw[f.name] || []; t.value = v.map(x => typeof x === "string" ? x : (x.doc ?? JSON.stringify(x))).join("\n"); p.append(field(f.name, t, f.help)); get[f.name] = () => t.value.split("\n"); }
    else if (f.type === "csv") { const t = h("input", {type:"text"}); t.value = (raw[f.name] || []).join(", "); p.append(field(f.name, t, f.help)); get[f.name] = () => t.value.split(",").map(s => s.trim()).filter(Boolean); }
    else if (f.type === "evidence") {
      const box = h("div"); for (const ev of raw.evidence || []) box.append(evRow(ev));
      const add = h("button", {type:"button"}, "+ add evidence"); add.onclick = () => box.append(evRow({version: (raw.evidence && raw.evidence[0] && raw.evidence[0].version) || 1, grade: 2}));
      p.append(field("evidence[]", box), add);
      get.evidence = () => [...box.querySelectorAll(".ev")].map(r => { const o = {}; r.querySelectorAll("[data-k]").forEach(x => o[x.dataset.k] = x.value); return o; });
    }
  }
  const errs = h("div", {class:"errs"}), save = h("button", {class:"primary", type:"button"}, "save and mark reviewed (Ctrl+Enter)"), cancel = h("button", {type:"button"}, "cancel (Esc)");
  const doSave = async () => {
    const values = {}; for (const [k, fn] of Object.entries(get)) values[k] = fn();
    const r = await decide("edit", {values, note: $("#note").value});
    if (r instanceof Error) errs.textContent = "Not saved, nothing was written:\n" + r.errors.join("\n");
  };
  save.onclick = doSave; cancel.onclick = closePanels; p.doSave = doSave;
  p.append(h("div", {class:"row", style:"margin-top:10px"}, save, cancel), errs);
  p.hidden = false; p.scrollIntoView({block:"nearest"});
}
// ---- drop
function openDrop(){
  if (!cur) return; closePanels();
  const p = $("#droppanel"); p.replaceChildren(h("h2", {}, "Drop " + cur.id));
  const r = h("input", {type:"text", placeholder:"one-line reason (required)"}), errs = h("div", {class:"errs"});
  const go = h("button", {class:"bad", type:"button"}, "drop item (Enter)"), cancel = h("button", {type:"button"}, "cancel (Esc)");
  const doDrop = async () => { const x = await decide("drop", {reason: r.value}); if (x instanceof Error) errs.textContent = x.errors.join("\n"); };
  go.onclick = doDrop; cancel.onclick = closePanels; r.onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); doDrop(); } };
  p.append(h("div", {class:"mute small"}, "The item is removed from the items file and appended to dropped.jsonl with your name, the time and this reason."), r, h("div", {class:"row", style:"margin-top:10px"}, go, cancel), errs);
  p.hidden = false; r.focus();
}
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.altKey) return;
  if (e.key === "Escape") { closePanels(); document.activeElement && document.activeElement.blur(); return; }
  if (e.key === "Enter" && e.ctrlKey && !$("#editpanel").hidden) { e.preventDefault(); $("#editpanel").doSave(); return; }
  if (inForm() || e.ctrlKey) return;
  const k = e.key.toLowerCase();
  if (k === "a") { e.preventDefault(); accept(); }
  else if (k === "e") { e.preventDefault(); openEdit(); }
  else if (k === "d") { e.preventDefault(); openDrop(); }
  else if (k === "n") { e.preventDefault(); go("next"); }
  else if (k === "p") { e.preventDefault(); go("prev"); }
});
$("#b-accept").onclick = accept; $("#b-edit").onclick = openEdit; $("#b-drop").onclick = openDrop;
$("#b-next").onclick = () => go("next"); $("#b-prev").onclick = () => go("prev");
(async () => { try { const s = await api("/api/state"); renderProgress(s); await show(s.current); } catch(e) { say(e.message, true); } })();
</script></body></html>
"""
