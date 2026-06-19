const PITCH = { C:0, "C#":1, Db:1, D:2, "D#":3, Eb:3, E:4, F:5, "F#":6, Gb:6,
  G:7, "G#":8, Ab:8, A:9, "A#":10, Bb:10, B:11 };

const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const root = document.documentElement;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function setState(s) { root.dataset.state = s; }

function deriveTheme(a) {
  const tonic = (a && a.key && a.key.tonic) || "C";
  const minor = !(a && a.key && a.key.mode === "major");
  const pc = PITCH[tonic] ?? 0;
  const hue = Math.round((pc * 30 + 200) % 360);

  const centroid = clamp((a.spectral_centroid_mean_hz || 1800) / 4000, 0, 1);
  const dr = clamp((a.dynamic_range_db || 40) / 60, 0, 1);
  const tension = clamp(a.harmonic_tension_mean == null ? 0.4 : a.harmonic_tension_mean, 0, 1);
  const bpm = clamp(a.tempo_bpm || 110, 50, 200);

  const satBg = minor ? 24 : 31;
  const accentSat = Math.round((minor ? 58 : 76) + centroid * 10);
  const accentL = Math.round(54 + centroid * 14);
  const Lbg = clamp(7 - dr * 2, 4, 8);
  const Link = clamp(90 + dr * 4, 88, 96);

  return {
    "--hue": String(hue),
    "--bg": `hsl(${hue} ${satBg}% ${Lbg}%)`,
    "--bg2": `hsl(${hue} ${satBg + 8}% ${Lbg + 5}%)`,
    "--ink": `hsl(${hue} ${minor ? 16 : 20}% ${Link}%)`,
    "--ink-dim": `hsl(${hue} 13% ${clamp(54 + dr * 6, 50, 64)}%)`,
    "--ink-faint": `hsl(${hue} 12% 38%)`,
    "--accent": `hsl(${hue} ${accentSat}% ${accentL}%)`,
    "--line": `hsl(${hue} 22% ${clamp(18 + dr * 6, 16, 26)}%)`,
    "--grain-op": (0.03 + tension * 0.11).toFixed(3),
    "--pulse": `${clamp(60 / bpm, 0.3, 1.2).toFixed(2)}s`,
  };
}

function applyTheme(vars) {
  for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
}

function renderReadout(a) {
  const stats = [];
  if (a.key && a.key.tonic) stats.push(["key", `${a.key.tonic} ${a.key.mode || ""}`.trim(), "key"]);
  if (a.tempo_bpm) stats.push(["bpm", Math.round(a.tempo_bpm)]);
  if (a.lufs_integrated != null) stats.push(["lufs", a.lufs_integrated.toFixed(1)]);
  if (a.dynamic_range_db != null) stats.push(["range", `${Math.round(a.dynamic_range_db)} db`]);
  if (a.spectral_centroid_mean_hz) stats.push(["centroid", `${Math.round(a.spectral_centroid_mean_hz)} hz`]);
  if (a.onset_rate_hz != null) stats.push(["onsets", `${a.onset_rate_hz.toFixed(1)}/s`]);
  $("readout").innerHTML = stats.map(([k, v, cls]) =>
    `<div class="stat ${cls || ""}"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("");
}

function buildSections(sections, dur) {
  const wrap = $("sections");
  wrap.innerHTML = "";
  const segs = (sections && sections.length) ? sections : [{ start_s: 0, end_s: dur }];
  for (const s of segs) {
    const d = document.createElement("div");
    d.className = "seg";
    d.style.flex = String(Math.max(0.02, (s.end_s - s.start_s) / dur));
    d.dataset.start = s.start_s;
    d.dataset.end = s.end_s;
    wrap.appendChild(d);
  }
}

function buildTicks(onsetRate, dur) {
  const n = clamp(Math.round((onsetRate || 1) * 6), 8, 48);
  $("tickrow").innerHTML = Array.from({ length: n },
    () => '<span class="tick-i"></span>').join("");
}

let SESSION = null;
let entryN = 0;

function appendEntry(rec) {
  const feed = $("feed");
  const nearBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
  const tick = rec.level_name === "ACTION_TEXT";
  const summary = rec.source_event_id === "session_summary";
  const id = "e" + (entryN++);
  const el = document.createElement("article");
  el.className = `entry l-${rec.level_name}${tick ? " tick" : ""}${summary ? " summary" : ""}`;
  el.id = id;
  const chips = (!tick && rec.dimensions && rec.dimensions.length)
    ? `<div class="chips">${rec.dimensions.map(d =>
        `<span class="chip">${esc(String(d).replace(/_/g, " "))}</span>`).join("")}</div>`
    : "";
  el.innerHTML =
    `<span class="tc">${fmt(rec.track_time_s)}</span>` +
    `<div class="body"><p class="text"></p>${chips}</div>`;
  el.querySelector(".text").textContent = rec.content;
  feed.appendChild(el);
  addMarker(rec, id, tick, summary);
  if (nearBottom) feed.scrollTop = feed.scrollHeight;
}

function addMarker(rec, targetId, tick, summary) {
  const dur = (SESSION && SESSION.duration_s) || 0;
  if (!dur || rec.track_time_s == null) return;
  const pct = clamp(rec.track_time_s / dur, 0, 1) * 100;
  const m = document.createElement("button");
  m.type = "button";
  m.className = "marker " + (summary ? "m-summary" : tick ? "m-tick" : "m-prose");
  m.style.left = pct + "%";
  const label = `${fmt(rec.track_time_s)} — ${rec.content.slice(0, 64)}`;
  m.title = label;
  m.setAttribute("aria-label", label);
  m.addEventListener("click", () => {
    const t = document.getElementById(targetId);
    if (!t) return;
    t.scrollIntoView({ behavior: "smooth", block: "center" });
    t.classList.remove("flash");
    void t.offsetWidth;
    t.classList.add("flash");
  });
  $("markers").appendChild(m);
}

function connectFeed() {
  const es = new EventSource("/api/feed");
  es.onmessage = (e) => {
    if (!e.data) return;
    try { appendEntry(JSON.parse(e.data)); } catch (_) {}
  };
}

function playheadLoop() {
  const s = SESSION;
  const dur = s.duration_s || 1;
  const pstart = s.playback_start_ms || 0;
  const delay = s.delay_buffer_ms || 0;
  const segs = [...document.querySelectorAll(".seg")];

  function tick() {
    const pos = (Date.now() - pstart - delay) / 1000;
    let p = pos, state = "live";
    if (pos < 0) { p = 0; state = "cued"; }
    else if (pos >= dur) { p = dur; state = "ended"; }
    setState(state);
    const pct = clamp(p / dur, 0, 1) * 100;
    $("head").style.left = pct + "%";
    $("played").style.width = pct + "%";
    $("tcNow").textContent = fmt(p);
    for (const seg of segs) {
      const on = p >= +seg.dataset.start && p < +seg.dataset.end;
      seg.classList.toggle("on", on);
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

function setup(s) {
  SESSION = s;
  const a = s.audio || {};
  applyTheme(deriveTheme(a));
  $("title").textContent = s.track_id || "untitled";
  $("artist").textContent = s.artist_id ? s.artist_id.replace(/[-_]/g, " ") : "";
  renderReadout(a);
  buildSections(s.sections, s.duration_s || 1);
  buildTicks(a.onset_rate_hz, s.duration_s || 1);
  $("tcEnd").textContent = fmt(s.duration_s);
  connectFeed();
  playheadLoop();
}

async function init() {
  try {
    const r = await fetch("/api/session", { cache: "no-store" });
    if (r.ok) { setup(await r.json()); return; }
  } catch (_) {}
  $("curtainMsg").textContent = "waiting for a session";
  setTimeout(init, 3000);
}

init();
