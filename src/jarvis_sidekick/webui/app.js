"use strict";
// The Jarvis screen. Everything shown comes from the server as data and is written with textContent,
// so nothing fetched from a news feed or a job listing can become markup here.

const $ = (id) => document.getElementById(id);
const COLORS = { accent: "#3987e5", soft: "#7fb2f0", muted: "#898781", up: "#0ca30c", down: "#e66767",
                 surface: "#1a1a19", second: "#c3c2b7" };
const EQUITY = new Set(["NIFTY 50", "SENSEX", "NIFTY Bank", "S&P 500", "Nasdaq", "Dow Jones", "FTSE 100",
                        "Nikkei 225", "Hang Seng"]);
const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

let mode = "idle";            // idle | listening | thinking | speaking | asleep
let level = 0;                // 0..1, how loud the microphone or the reply is right now
let audioContext = null, analyser = null, micStream = null, recorder = null, chunks = [];

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options,
    headers: { "X-Jarvis": "1", ...(options.headers || {}) } });
  if (response.status === 401 && path !== "/api/login/finish") { showLogin(); throw new Error("signed out"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

// ---------------------------------------------------------------- sign-in

function showLogin() { $("app").hidden = true; $("login").hidden = false; }

$("send-code").addEventListener("click", async () => {
  $("login-error").textContent = "";
  try {
    await api("/api/login/start", { method: "POST" });
    $("code-form").hidden = false; $("send-code").textContent = "Send another code";
    $("login-hint").textContent = "Sent. Open Telegram, read the code from your bot, and enter it here.";
    $("code").focus();
  } catch (error) { $("login-error").textContent = error.message; }
});

$("code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/login/finish", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: $("code").value }) });
    $("code").value = ""; start();
  } catch (error) { $("login-error").textContent = error.message; }
});

$("sign-out").addEventListener("click", async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); showLogin(); });

// ---------------------------------------------------------------- the orb

const canvas = $("orb-canvas"), pen = canvas.getContext("2d");
let smooth = 0;

function drawOrb(time) {
  const size = canvas.width, mid = size / 2, t = calm ? 0 : time / 1000;
  smooth += (level - smooth) * 0.25;
  const energy = mode === "listening" || mode === "speaking" ? smooth : mode === "thinking" ? 0.25 : 0.06;
  const dim = mode === "asleep" ? 0.35 : 1;
  pen.clearRect(0, 0, size, size);

  const glow = pen.createRadialGradient(mid, mid, size * 0.08, mid, mid, size * 0.5);
  glow.addColorStop(0, `rgba(57,135,229,${(0.42 + energy * 0.4) * dim})`);
  glow.addColorStop(0.55, `rgba(57,135,229,${0.10 * dim})`);
  glow.addColorStop(1, "rgba(57,135,229,0)");
  pen.fillStyle = glow; pen.beginPath(); pen.arc(mid, mid, size * 0.5, 0, Math.PI * 2); pen.fill();

  // Three broken rings, each turning at its own pace; they swell with the voice.
  const rings = [[0.30, 3.2, 0.35, 5], [0.365, 2.0, -0.22, 3], [0.43, 4.4, 0.12, 2]];
  rings.forEach(([radius, gapCount, speed, width], index) => {
    const r = size * (radius + energy * 0.03 * (index + 1));
    const turn = t * speed * (mode === "thinking" ? 4 : 1);
    pen.lineWidth = width; pen.lineCap = "round";
    pen.strokeStyle = index === 0 ? COLORS.soft : COLORS.accent;
    pen.globalAlpha = (index === 0 ? 0.95 : 0.55 - index * 0.12) * dim;
    const arc = (Math.PI * 2) / gapCount;
    for (let i = 0; i < gapCount; i++) {
      pen.beginPath(); pen.arc(mid, mid, r, turn + i * arc, turn + i * arc + arc * 0.72); pen.stroke();
    }
  });
  pen.globalAlpha = dim;
  pen.fillStyle = COLORS.soft; pen.beginPath();
  pen.arc(mid, mid, size * (0.085 + energy * 0.07 + (calm ? 0 : Math.sin(t * 1.6) * 0.004)), 0, Math.PI * 2); pen.fill();
  pen.globalAlpha = 1;

  if (analyser && (mode === "listening" || mode === "speaking")) {
    const samples = new Uint8Array(analyser.fftSize); analyser.getByteTimeDomainData(samples);
    let sum = 0; for (const s of samples) sum += ((s - 128) / 128) ** 2;
    level = Math.min(1, Math.sqrt(sum / samples.length) * 3.2);
  } else { level = 0; }
  requestAnimationFrame(drawOrb);
}

function setMode(next, message) {
  mode = next;
  $("status").textContent = message || { idle: "Hold the orb and speak", listening: "Listening…",
    thinking: "Working on it…", speaking: "Speaking…", asleep: "Asleep. Say Jarvis to wake me." }[next];
}

// ---------------------------------------------------------------- talking

function sound() {
  if (!audioContext) audioContext = new (window.AudioContext || window.webkitAudioContext)();
  if (audioContext.state === "suspended") audioContext.resume();
  return audioContext;
}

async function startListening() {
  if (mode === "listening" || mode === "thinking") return;
  sound();
  try {
    micStream = micStream || await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch { return setMode("idle", "I need the microphone. Allow it in the browser and try again."); }
  const source = audioContext.createMediaStreamSource(micStream);
  analyser = audioContext.createAnalyser(); analyser.fftSize = 1024; source.connect(analyser);
  const type = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
  recorder = new MediaRecorder(micStream, type ? { mimeType: type } : {});
  chunks = [];
  recorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);
  recorder.onstop = sendVoice;
  recorder.start(); setMode("listening");
}

function stopListening() { if (recorder && recorder.state === "recording") recorder.stop(); }

async function sendVoice() {
  const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
  if (blob.size < 1500) return setMode("idle", "That was too short. Hold the orb while you speak.");
  setMode("thinking");
  try {
    const result = await api("/api/voice", { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
    if (result.error) { addTurn("jarvis problem", result.error); return setMode("idle"); }
    addTurn("you", result.heard); showAnswer(result);
  } catch (error) { if (error.message !== "signed out") { addTurn("jarvis problem", error.message); setMode("idle"); } }
}

async function sayText(text) {
  if (!text.trim()) return;
  sound(); addTurn("you", text); setMode("thinking");
  try {
    showAnswer(await api("/api/say", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }) }));
  } catch (error) { if (error.message !== "signed out") { addTurn("jarvis problem", error.message); setMode("idle"); } }
}

function addTurn(kind, text, file) {
  const turn = document.createElement("div"); turn.className = "turn " + kind; turn.textContent = text;
  if (file) {
    const link = document.createElement("a"); link.className = "file"; link.href = file.url; link.target = "_blank";
    link.rel = "noopener"; link.textContent = file.name; turn.append(document.createElement("br"), link);
  }
  $("log").prepend(turn);
}

function showAnswer(result) {
  addTurn("jarvis", result.reply, result.file);
  renderChips(result.choices && result.choices.length ? result.choices : null);
  if (result.audio) speak(result.audio, result.asleep); else setMode(result.asleep ? "asleep" : "idle");
}

async function speak(url, asleep) {
  try {
    const response = await fetch(url, { credentials: "same-origin", headers: { "X-Jarvis": "1" } });
    if (!response.ok) throw new Error("no audio");
    const buffer = await sound().decodeAudioData(await response.arrayBuffer());
    const source = audioContext.createBufferSource(); source.buffer = buffer;
    analyser = audioContext.createAnalyser(); analyser.fftSize = 1024;
    source.connect(analyser); analyser.connect(audioContext.destination);
    source.onended = () => setMode(asleep ? "asleep" : "idle");
    setMode("speaking"); source.start();
  } catch { setMode(asleep ? "asleep" : "idle"); }
}

const orb = $("orb");
orb.addEventListener("pointerdown", (event) => { event.preventDefault(); orb.setPointerCapture(event.pointerId); startListening(); });
orb.addEventListener("pointerup", stopListening);
orb.addEventListener("pointercancel", stopListening);
orb.addEventListener("keydown", (event) => { if ((event.key === " " || event.key === "Enter") && !event.repeat) { event.preventDefault(); startListening(); } });
orb.addEventListener("keyup", (event) => { if (event.key === " " || event.key === "Enter") stopListening(); });
orb.addEventListener("contextmenu", (event) => event.preventDefault());
$("say-form").addEventListener("submit", (event) => { event.preventDefault(); const box = $("say"); sayText(box.value); box.value = ""; });

// ---------------------------------------------------------------- chips and the board

let buttons = [];
function renderChips(answers) {
  const holder = $("chips"); holder.replaceChildren();
  for (const label of answers || buttons) {
    const chip = document.createElement("button"); chip.type = "button";
    chip.className = "chip" + (answers ? " answer" : ""); chip.textContent = label;
    chip.addEventListener("click", () => sayText(label)); holder.append(chip);
  }
}

const SVG = "http://www.w3.org/2000/svg";
function sparkline(values) {
  const svg = document.createElementNS(SVG, "svg"); svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 124 40"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "trend, last 60 sessions");
  if (!values || values.length < 2) return svg;
  const low = Math.min(...values), span = (Math.max(...values) - low) || 1, step = 116 / (values.length - 1);
  const points = values.map((v, i) => [4 + i * step, 4 + 32 * (1 - (v - low) / span)]);
  const line = document.createElementNS(SVG, "polyline");
  line.setAttribute("points", points.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" "));
  line.setAttribute("fill", "none"); line.setAttribute("stroke", COLORS.muted); line.setAttribute("stroke-width", "2");
  line.setAttribute("stroke-linejoin", "round"); line.setAttribute("stroke-linecap", "round");
  const dot = document.createElementNS(SVG, "circle"); const last = points[points.length - 1];
  dot.setAttribute("cx", last[0].toFixed(1)); dot.setAttribute("cy", last[1].toFixed(1)); dot.setAttribute("r", "4");
  dot.setAttribute("fill", COLORS.accent); dot.setAttribute("stroke", COLORS.surface); dot.setAttribute("stroke-width", "2");
  svg.append(line, dot); return svg;
}

function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text != null) node.textContent = text; return node; }
const number = (v) => Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-IN") : v.toFixed(2);

function renderBoard(state) {
  $("greeting").textContent = `${state.greeting}, ${state.title}.`;
  $("as-of").textContent = state.as_of ? "· as of " + state.as_of.replace("T", " ") + " UTC" : "";
  const tiles = $("tiles"); tiles.replaceChildren();
  for (const row of state.indices) {
    const tile = el("div", "tile"); tile.append(el("div", "label", row.name), el("div", "value", number(row.price)));
    const foot = el("div", "foot"), delta = el("div", "delta");
    if (row.day_pct != null) {
      // The triangle carries the direction; colour repeats it only where "up" is plainly good news.
      const mark = el("span", "mark", row.day_pct > 0 ? "▲" : row.day_pct < 0 ? "▼" : "●");
      mark.style.color = !EQUITY.has(row.name) || row.day_pct === 0 ? COLORS.second : row.day_pct > 0 ? COLORS.up : COLORS.down;
      delta.append(mark, Math.abs(row.day_pct).toFixed(2) + "% today");
    }
    foot.append(delta, sparkline(row.spark)); tile.append(foot); tiles.append(tile);
  }
  if (!state.indices.length) tiles.append(el("p", "empty", "Market data is not available right now."));
  const list = $("headlines"); list.replaceChildren();
  for (const item of state.headlines) {
    const row = el("li"), link = el("a", null, item.title);
    if (/^https?:\/\//.test(item.link)) { link.href = item.link; link.target = "_blank"; link.rel = "noopener noreferrer"; }
    row.append(el("span", "source", item.source), link); list.append(row);
  }
  if (!state.headlines.length) list.append(el("li", "empty", "No headlines could be fetched."));
  buttons = state.buttons || []; renderChips(null);
  if (!state.voice) setMode("idle", "Voice is off on the server. Type below.");
  else if (state.asleep) setMode("asleep"); else if (mode === "asleep") setMode("idle");
}

async function loadBoard() { try { renderBoard(await api("/api/state")); } catch { /* stay as is; the next refresh retries */ } }

async function start() {
  const session = await fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json()).catch(() => ({}));
  if (!session.signed_in) return showLogin();
  $("login").hidden = true; $("app").hidden = false;
  loadBoard(); setInterval(loadBoard, 10 * 60 * 1000);
}

requestAnimationFrame(drawOrb);
start();
