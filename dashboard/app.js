/* OMNI dashboard — vanilla JS, polls the real backend. No fabricated data: any
   metric without a source is shown as "unavailable". */
const $ = (id) => document.getElementById(id);
const state = { currentTask: null, view: "home", pollTimers: {} };

const VIEW_TITLES = {
  home: "OMNI Home", command: "AI Command Center", algorithms: "Algorithm Observatory",
  models: "Local Model Center", monitor: "System Monitor", history: "Task History",
  settings: "Settings",
};

/* ---------------------------------------------------------------- helpers */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function kvRow(k, v, cls) {
  const row = el("div", "row");
  row.append(el("span", "k", k));
  const val = el("span", "v" + (cls ? " " + cls : ""), v);
  row.append(val);
  return row;
}
function meter(label, pct, valueText) {
  const wrap = el("div", "meter");
  const head = el("div", "meter-head");
  head.append(el("span", "", label));
  head.append(el("b", "", valueText !== undefined ? valueText : (pct === null ? "unavailable" : pct + "%")));
  wrap.append(head);
  const track = el("div", "track");
  const fill = el("div", "fill" + (pct >= 80 ? " bad" : pct >= 60 ? " warn" : ""));
  if (pct !== null && pct !== undefined) fill.style.width = Math.min(100, Math.max(0, pct)) + "%";
  else fill.style.width = "0%";
  track.append(fill);
  wrap.append(track);
  return wrap;
}
function fmt(x, digits = 1) { return (x === null || x === undefined) ? "unavailable" : (typeof x === "number" ? x.toFixed(digits) : x); }

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

/* ------------------------------------------------------------------- nav */
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    state.view = btn.dataset.view;
    const view = $("view-" + state.view);
    if (view) view.classList.add("active");
    $("view-title").textContent = VIEW_TITLES[state.view] || "OMNI";
    refreshAlgorithms();
    refreshModels();
  });
});

/* ------------------------------------------------------------ state poll */
async function pollState() {
  let data;
  try { data = await api("/api/state"); } catch { setPills(null); return; }
  setPills(data);
  renderHome(data);
  renderMonitor(data);
}

function setPills(data) {
  const pillOllama = $("pill-ollama"), pillModel = $("pill-model"), pillInfer = $("pill-infer");
  if (!data) {
    pillOllama.textContent = "backend: unreachable"; pillOllama.className = "pill bad";
    return;
  }
  const inf = data.inference;
  if (inf.mock) {
    pillOllama.textContent = "offline mode (simulated)"; pillOllama.className = "pill warn";
  } else if (inf.reachable) {
    pillOllama.textContent = `Ollama · ${inf.model_count} models`; pillOllama.className = "pill ok";
  } else {
    pillOllama.textContent = "Ollama unreachable"; pillOllama.className = "pill bad";
  }
  const model = activeModel(data);
  pillModel.textContent = "model: " + (model || "—");
  if (data.workload.inference_active) {
    pillInfer.textContent = "inferring…"; pillInfer.className = "pill busy";
  } else {
    pillInfer.textContent = "idle"; pillInfer.className = "pill";
  }
}

function activeModel(data) {
  if (data.active_model) return data.active_model;
  const last = data.recent_tasks.find((t) => t.model && t.model.selected);
  return last ? last.model.selected : null;
}

function renderHome(data) {
  const inf = data.inference;
  $("home-model").textContent = activeModel(data) || "auto-routing";
  $("home-model-role").textContent = inf.mock ? "offline: simulated generation" : (inf.reachable ? `${inf.model_count} models discovered` : "service unavailable");
  $("home-ollama").textContent = inf.mock ? "simulated" : inf.reachable ? "online" : "offline";
  $("home-ollama").className = "stat-value " + (inf.reachable || inf.mock ? "" : "muted");
  $("home-ollama-sub").textContent = inf.error || inf.url;
  const w = data.workload;
  $("home-workload").textContent = `${w.running} / ${w.queued}`;
  const done = data.recent_tasks.filter((t) => ["done", "failed"].includes(t.status));
  $("home-tasks").textContent = w.total;
  $("home-verified").textContent = done.length ? `${done.filter((t) => t.verification && t.verification.passed).length}/${done.length} verified` : "no completed tasks yet";

  const sys = data.system || {};
  const sysWrap = $("home-system");
  sysWrap.innerHTML = "";
  const cpu = sys.cpu || {}, mem = sys.memory || {}, gpu = sys.gpu, disk = sys.disk || {};
  sysWrap.append(
    meter("CPU load", cpu.load_pct, cpu.load_pct === undefined ? "unavailable" : `${cpu.load_pct}% (${cpu.load_1m} load, ${cpu.cores} cores)`),
    meter("Memory", mem.used_pct, mem.used_pct === null || mem.used_pct === undefined ? "unavailable" : `${fmt(mem.used_gb ?? mem.used_mb / 1024)} GB used`),
  );
  if (gpu) sysWrap.append(meter(`GPU · ${gpu.name}`, gpu.vram_used_pct, `util ${fmt(gpu.util_pct, 0)}% · VRAM ${fmt(gpu.vram_used_mb / 1024)}/${fmt(gpu.vram_total_mb / 1024)} GB`));
  else sysWrap.append(kvRow("GPU", "no NVIDIA tooling available", "muted"));
  if (disk.used_pct !== null && disk.used_pct !== undefined) sysWrap.append(meter("Disk", disk.used_pct, `${fmt(disk.used_gb)} / ${fmt(disk.total_gb)} GB`));

  const recent = $("home-recent");
  recent.innerHTML = "";
  if (!data.recent_tasks.length) recent.append(el("div", "muted", "No tasks yet — try a quick action above."));
  for (const t of data.recent_tasks.slice(0, 6)) {
    const row = el("div", "row");
    row.append(el("span", "k", (t.goal || "").slice(0, 44)));
    row.append(el("span", "v " + statusClass(t.status), t.status));
    row.addEventListener("click", () => showTask(t.task_id));
    recent.append(row);
  }

  const actions = $("home-actions");
  actions.innerHTML = "";
  for (const a of data.desktop_actions || []) {
    actions.append(kvRow(a.name, a.requires_confirmation ? "confirm" : a.tier, a.requires_confirmation ? "warn" : "accent"));
  }
}

function statusClass(status) {
  return { done: "accent", failed: "bad", running: "warn", cancelled: "muted", error: "bad" }[status] || "";
}

/* ---------------------------------------------------------------- monitor */
function renderMonitor(data) {
  const wrap = $("mon-tasks");
  wrap.innerHTML = "";
  const w = data.workload;
  wrap.append(kvRow("queued", String(w.queued)));
  wrap.append(kvRow("running", String(w.running)));
  wrap.append(kvRow("inference activity", w.inference_active ? "active" : "idle", w.inference_active ? "warn" : "accent"));
  wrap.append(kvRow("safe execution mode", data.settings.safe_execution ? "on" : "off", data.settings.safe_execution ? "accent" : "bad"));
  wrap.append(kvRow("offline mode", data.settings.offline_mode ? "on (simulated)" : "off", data.settings.offline_mode ? "warn" : "accent"));
  for (const t of data.recent_tasks.slice(0, 8)) {
    const row = el("div", "row");
    row.append(el("span", "k", `${t.task_id} · ${(t.goal || "").slice(0, 36)}`));
    row.append(el("span", "v " + statusClass(t.status), t.status));
    row.addEventListener("click", () => showTask(t.task_id));
    wrap.append(row);
  }
}

/* ----------------------------------------------------------- command view */
$("cmd-submit").addEventListener("click", submitTask);
document.querySelectorAll("[data-quick]").forEach((btn) => {
  btn.addEventListener("click", () => {
    $("cmd-goal").value = btn.dataset.quick;
    document.querySelector('[data-view="command"]').click();
    submitTask();
  });
});

async function submitTask() {
  const goal = $("cmd-goal").value.trim();
  if (!goal) return;
  $("cmd-result").textContent = "Submitting…";
  try {
    const { task_id } = await api("/api/tasks", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal }),
    });
    state.currentTask = task_id;
    renderStages([]);
    pollTask();
  } catch (e) {
    $("cmd-result").textContent = "Submission failed: " + e.message;
  }
}

async function pollTask() {
  if (!state.currentTask) return;
  let t;
  try { t = await api("/api/tasks/" + state.currentTask); } catch { return; }
  renderStages(t.stages || []);
  $("cmd-result").textContent = t.result_text || `status: ${t.status}`;
  renderVerification(t);
  renderReward(t);
  renderCmdAlgos(t);
  const stillRunning = ["queued", "running", "generating", "executing", "verifying"].includes(t.status);
  if (stillRunning) setTimeout(pollTask, 700);
}

function renderStages(stages) {
  const wrap = $("cmd-stages");
  wrap.innerHTML = "";
  if (!stages.length) { wrap.append(el("div", "muted", "Waiting for task to start…")); return; }
  const names = ["context", "iars", "ampl", "model", "scheduler", "generation", "execution", "verification", "reward", "adaptive-update"];
  const byName = Object.fromEntries(stages.map((s) => [s.name, s]));
  for (const name of names) {
    const stage = byName[name];
    if (!stage) continue;
    const detail = stageDetail(stage);
    const row = el("div", `stage ${stage.status}`);
    row.append(el("span", "dot"));
    row.append(el("span", "stage-name", name));
    row.append(el("span", "stage-detail", detail));
    row.append(el("span", "stage-chip", stage.status));
    wrap.append(row);
  }
}

function stageDetail(stage) {
  const d = stage;
  switch (d.name) {
    case "context": return d.app ? `app: ${d.app} · workspace ${d.workspace}` : "snapshot unavailable";
    case "iars": return d.agent ? `agent ${d.agent} · confidence ${d.confidence}` : "ablated";
    case "ampl": return d.resident ? `${d.resident.length} modules resident` : "ablated";
    case "model": return d.model ? `${d.model} (${d.kind})` : "no model";
    case "scheduler": return d.action ? `${d.action} · ${d.policy}` : "ablated";
    case "generation": return `${d.model} · ${d.structured ? "structured JSON" : "fallback plan"} · ${d.elapsed_s}s`;
    case "execution": return `${d.executed}/${d.total} actions executed`;
    case "verification": return `${d.checks}/${d.total} checks ${d.status}`;
    case "reward": return `reward ${d.reward}`;
    case "adaptive-update": return `IARS ${d.iars_updates} updates · KRLS ${d.krls_updates} updates`;
    default: return "";
  }
}

function renderVerification(t) {
  const wrap = $("cmd-verification");
  wrap.innerHTML = "";
  const v = t.verification || {};
  if (!v.checks) { wrap.append(el("div", "muted", "—")); return; }
  wrap.append(kvRow("passed", `${v.passed_count}/${v.total}`, v.passed ? "accent" : "bad"));
  for (const c of v.checks.slice(0, 12)) {
    wrap.append(kvRow(`${c.path} · ${c.check}`, c.passed ? "pass" : "fail", c.passed ? "accent" : "bad"));
  }
}

function renderReward(t) {
  const wrap = $("cmd-reward");
  wrap.innerHTML = "";
  const r = t.reward || {};
  if (r.reward === undefined) { wrap.append(el("div", "muted", "—")); return; }
  wrap.append(kvRow("reward", String(r.reward), r.reward >= 0 ? "accent" : "bad"));
  wrap.append(kvRow("source", r.source || ""));
  wrap.append(kvRow("IARS updated", r.iars_updated ? `yes (total ${r.iars_updates_total})` : "no"));
  wrap.append(kvRow("KRLS updated", r.krls_updated ? `yes (total ${r.krls_updates_total})` : "no"));
}

function renderCmdAlgos(t) {
  const wrap = $("cmd-algos");
  wrap.innerHTML = "";
  wrap.append(kvRow("IARS agent", t.iars?.agent || "—", "accent"));
  wrap.append(kvRow("IARS confidence", t.iars?.confidence !== undefined ? String(t.iars.confidence) : "—"));
  wrap.append(kvRow("AMPL modules", (t.ampl?.resident || []).join(", ") || (t.ampl?.status || "—")));
  wrap.append(kvRow("KRLS action", t.scheduler?.action || "—"));
  wrap.append(kvRow("KRLS policy", t.scheduler?.policy || "—"));
}

/* ------------------------------------------------------------ algorithms */
async function refreshAlgorithms() {
  if (!["algorithms", "command", "home"].includes(state.view)) return;
  let data;
  try { data = await api("/api/algorithms"); } catch { return; }
  const iars = $("alg-iars");
  iars.innerHTML = "";
  iars.append(kvRow("posterior updates", String(data.iars.updates), data.iars.updates ? "accent" : ""));
  iars.append(kvRow("state", data.iars.enabled ? "active" : "ablated", data.iars.enabled ? "accent" : "bad"));
  for (const a of data.iars.agents) iars.append(kvRow(a.name, `${a.updates} updates`));

  const ampl = $("alg-ampl");
  ampl.innerHTML = "";
  ampl.append(kvRow("state", data.ampl.enabled ? "active" : "ablated", data.ampl.enabled ? "accent" : "bad"));
  ampl.append(kvRow("resident modules", (data.ampl.resident || []).join(", ") || "none"));
  ampl.append(kvRow("resident footprint", `${data.ampl.resident_mb} MB · ${data.ampl.resident_params_m}M params`));
  ampl.append(kvRow("library size", String(data.ampl.library_size)));

  const krls = $("alg-krls");
  krls.innerHTML = "";
  krls.append(kvRow("reward updates", String(data.krls.updates), data.krls.updates ? "accent" : ""));
  krls.append(kvRow("state", data.krls.enabled ? "active" : "ablated", data.krls.enabled ? "accent" : "bad"));
  for (const [id, s] of Object.entries(data.krls.models)) {
    krls.append(kvRow(id, `${s.updates} updates · ${s.centers} centers`));
  }

  const hist = $("alg-history");
  hist.innerHTML = "";
  if (!data.history.length) hist.append(el("div", "muted", "No decisions yet."));
  for (const h of data.history.slice().reverse().slice(0, 25)) {
    const row = el("div", "row");
    row.append(el("span", "k", `${(h.goal || "").slice(0, 30)} · ${h.agent || "?"}`));
    row.append(el("span", "v " + (h.verified ? "accent" : "bad"), `r=${h.reward ?? "—"} ${h.model || ""}`));
    hist.append(row);
  }
  drawRewards(data.history);
}

function drawRewards(history) {
  const canvas = $("reward-canvas");
  const ctx = canvas.getContext("2d");
  const rewards = history.filter((h) => typeof h.reward === "number").map((h) => h.reward);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  $("alg-rewards-note").textContent = rewards.length
    ? `last ${Math.min(rewards.length, 40)} observed task rewards (real values)`
    : "rewards accumulate as tasks complete";
  if (!rewards.length) return;
  const data = rewards.slice(-40);
  const pad = 8, W = canvas.width, H = canvas.height;
  const lo = Math.min(0, ...data), hi = Math.max(0.2, ...data);
  const x = (i) => pad + (i / Math.max(data.length - 1, 1)) * (W - 2 * pad);
  const y = (v) => H - pad - ((v - lo) / (hi - lo || 1)) * (H - 2 * pad);
  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.beginPath(); ctx.moveTo(pad, y(0)); ctx.lineTo(W - pad, y(0)); ctx.stroke();
  ctx.strokeStyle = "#5ee6c5"; ctx.lineWidth = 1.8; ctx.beginPath();
  data.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v))));
  ctx.stroke();
  ctx.fillStyle = "rgba(94,230,197,0.15)";
  ctx.lineTo(x(data.length - 1), y(lo)); ctx.lineTo(x(0), y(lo)); ctx.closePath(); ctx.fill();
}

/* ---------------------------------------------------------------- models */
async function refreshModels() {
  if (state.view !== "models") return;
  let data;
  try { data = await api("/api/models"); } catch { return; }
  $("models-auto").textContent = "auto-routing: " + (data.auto_select ? "on" : "off") + (data.selected && !data.auto_select ? ` (${data.selected})` : "");
  const body = $("models-body");
  body.innerHTML = "";
  if (!data.models.length) {
    body.innerHTML = "";
    body.append(el("tr", "", "")).append(Object.assign(el("td", "muted", data.reachable === false ? "Ollama unreachable — no local models available" : "no models discovered"), { colSpan: 8 }));
    return;
  }
  for (const m of data.models) {
    const tr = el("tr");
    const radio = el("input");
    radio.type = "radio";
    radio.name = "model-select";
    radio.checked = data.selected === m.tag;
    radio.addEventListener("change", () => selectModel(m.tag));
    const cell = el("td"); cell.append(radio); tr.append(cell);
    const nameTd = el("td", "strong", m.tag);
    if (m.aliases.length) nameTd.title = "aliases: " + m.aliases.join(", ");
    tr.append(nameTd);
    tr.append(el("td", "", m.role));
    tr.append(el("td", "", m.parameters));
    tr.append(el("td", "", m.context_length ? m.context_length.toLocaleString() : "—"));
    tr.append(el("td", "", m.capabilities.join(", ")));
    tr.append(el("td", "", m.observed_latency_s !== null ? m.observed_latency_s.toFixed(2) + "s" : "not observed"));
    const status = el("td", m.loaded ? "strong" : "", m.loaded ? "loaded" : "on demand");
    tr.append(status);
    tr.addEventListener("click", () => selectModel(m.tag));
    body.append(tr);
  }
}

async function selectModel(tag) {
  await api("/api/models/select", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: tag, auto: false }),
  });
  refreshModels(); pollState();
}
$("models-refresh").addEventListener("click", async () => { await api("/api/models/refresh", { method: "POST" }); refreshModels(); });

/* ---------------------------------------------------------------- history */
async function refreshHistory() {
  if (state.view !== "history") return;
  let tasks;
  try { tasks = await api("/api/tasks"); } catch { return; }
  const body = $("history-body");
  body.innerHTML = "";
  if (!tasks.length) { body.append(el("tr")).append(Object.assign(el("td", "muted", "no tasks yet"), { colSpan: 9 })); return; }
  for (const t of tasks) {
    const tr = el("tr");
    tr.append(el("td", "mono", t.task_id));
    tr.append(el("td", "", (t.goal || "").slice(0, 42)));
    tr.append(el("td", "", t.iars?.agent || "—"));
    tr.append(el("td", "", t.model?.selected || "—"));
    tr.append(el("td", "", t.scheduler?.action || "—"));
    tr.append(el("td", "", t.duration_s.toFixed(1) + "s"));
    const v = t.verification;
    tr.append(el("td", v?.passed ? "strong" : "", v ? `${v.passed_count}/${v.total}` : "—"));
    tr.append(el("td", "", t.reward?.reward ?? "—"));
    tr.append(el("td", statusClass(t.status), t.status));
    tr.addEventListener("click", () => showTask(t.task_id));
    body.append(tr);
  }
}

async function showTask(taskId) {
  state.currentTask = taskId;
  document.querySelector('[data-view="command"]').click();
  $("cmd-result").textContent = "loading…";
  pollTask();
}

/* ---------------------------------------------------------------- settings */
async function loadSettings() {
  let data;
  try { data = await api("/api/settings"); } catch { return; }
  const form = $("settings-form");
  const s = data.settings;
  form.ollama_url.value = s.ollama_url;
  form.preferred_model.value = s.preferred_model || "";
  form.auto_select.checked = !!s.auto_select;
  form.max_gen_tokens.value = s.max_gen_tokens;
  form.gen_timeout_s.value = s.gen_timeout_s;
  form.safe_execution.checked = !!s.safe_execution;
  form.offline_mode.checked = !!s.offline_mode;
}
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = $("settings-form");
  await api("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ollama_url: form.ollama_url.value,
      preferred_model: form.preferred_model.value,
      auto_select: form.auto_select.checked,
      max_gen_tokens: Number(form.max_gen_tokens.value),
      gen_timeout_s: Number(form.gen_timeout_s.value),
      safe_execution: form.safe_execution.checked,
      offline_mode: form.offline_mode.checked,
    }),
  });
  $("settings-saved").textContent = "saved ✓";
  setTimeout(() => ($("settings-saved").textContent = ""), 2500);
  pollState();
});

/* ------------------------------------------------------------------ boot */
pollState();
loadSettings();
setInterval(pollState, 2000);
setInterval(() => { refreshAlgorithms(); refreshHistory(); refreshModels(); }, 4000);
