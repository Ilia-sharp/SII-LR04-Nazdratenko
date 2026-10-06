// ЛР №4: одна страница. Видео → ход работы → результат → история последних 5 видео.
// История хранится в браузере: сводка в localStorage, сами видео в IndexedDB.

const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat("ru-RU");
const nf2 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
const INK = "#111", HOT = "#FF5A1F", MUTE = "#6B6B6B", GRID = "#E2E2E2";
const JOB_KEY = "lr04.job", HIST_KEY = "lr04.hist", HIST_MAX = 5;

const S = { cfg: null, file: null, xhr: null, token: null, jobId: null, pend: null, shown: 0, counts: [], frames: [], item: null, urls: [], previewUrl: null, report: "" };

// ---------- помощники ----------
class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }
const detail = (b, s) => (b && typeof b.detail === "string" ? b.detail : `Ошибка сервера ${s}.`);
async function api(path, options) {
  let res;
  try { res = await fetch(path, options); } catch { throw new ApiError(0, "Сервер не отвечает."); }
  let body = null;
  try { body = await res.json(); } catch { /* не JSON */ }
  if (!res.ok) throw new ApiError(res.status, detail(body, res.status));
  return body;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mb = (b) => (b / 1048576).toFixed(1).replace(".", ",");
const clock = (s) => (s == null || !Number.isFinite(s) ? "—" : `${String(Math.floor(Math.max(0, s) / 60)).padStart(2, "0")}:${String(Math.round(Math.max(0, s)) % 60).padStart(2, "0")}`);
const when = (ts) => new Date(ts).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const lget = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } };
const lset = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* нет места */ } };
const sget = () => { try { return JSON.parse(sessionStorage.getItem(JOB_KEY)); } catch { return null; } };
const sset = (v) => { try { v ? sessionStorage.setItem(JOB_KEY, JSON.stringify(v)) : sessionStorage.removeItem(JOB_KEY); } catch { /* ok */ } };

let toastT = 0;
function toast(t) { $("toast").textContent = t; $("toast").classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => $("toast").classList.remove("show"), 4000); }
const glide = (id) => setTimeout(() => $(id).scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" }), 150);

function tween(el, to, fmt = (v) => nf.format(Math.round(v))) {
  const from = Number(el.dataset.v ?? 0);
  el.dataset.v = String(to);
  cancelAnimationFrame(Number(el.dataset.raf || 0));
  if (calm || from === to) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  const tick = (now) => {
    const k = Math.min((now - t0) / 500, 1);
    el.textContent = fmt(from + (to - from) * (1 - (1 - k) ** 3));
    if (k < 1) el.dataset.raf = String(requestAnimationFrame(tick));
  };
  el.dataset.raf = String(requestAnimationFrame(tick));
}

// ---------- IndexedDB: файлы видео ----------
let dbp;
const db = () => (dbp ??= new Promise((res, rej) => {
  const r = indexedDB.open("lr04", 1);
  r.onupgradeneeded = () => r.result.createObjectStore("b");
  r.onsuccess = () => res(r.result);
  r.onerror = () => rej(r.error);
}));
async function idb(mode, fn) {
  const d = await db();
  return new Promise((res, rej) => {
    const t = d.transaction("b", mode);
    const req = fn(t.objectStore("b"));
    t.oncomplete = () => res(req.result);
    t.onerror = () => rej(t.error);
  });
}
const bPut = (k, v) => idb("readwrite", (s) => s.put(v, k)).catch(() => {});
const bGet = (k) => idb("readonly", (s) => s.get(k)).catch(() => null);
const bDel = (k) => idb("readwrite", (s) => s.delete(k)).catch(() => {});

// ---------- история ----------
function addHistory(item) {
  const all = [item, ...lget(HIST_KEY, []).filter((x) => x.id !== item.id)];
  for (const old of all.slice(HIST_MAX)) { bDel(`${old.id}:src`); bDel(`${old.id}:ann`); }
  lset(HIST_KEY, all.slice(0, HIST_MAX));
  renderHistory();
}
function updateHistory(id, patch) {
  lset(HIST_KEY, lget(HIST_KEY, []).map((x) => (x.id === id ? { ...x, ...patch } : x)));
}
function removeHistory(id) {
  lset(HIST_KEY, lget(HIST_KEY, []).filter((x) => x.id !== id));
  bDel(`${id}:src`); bDel(`${id}:ann`);
  if (S.item?.id === id) { $("out").classList.remove("open"); $("out").setAttribute("inert", ""); S.item = null; }
  renderHistory();
}
function renderHistory() {
  const list = lget(HIST_KEY, []);
  $("hist-empty").hidden = list.length > 0;
  $("hist-list").replaceChildren(...list.map((it) => {
    const li = document.createElement("li");
    li.classList.toggle("on", S.item?.id === it.id);
    const open = document.createElement("button");
    open.type = "button"; open.className = "open";
    const name = document.createElement("b"); name.textContent = it.name;
    const meta = document.createElement("small");
    meta.textContent = `${when(it.ts)} · каждый ${it.stride}-й · среднее ${nf2.format(it.report.detections_per_frame_mean)} · максимум ${it.report.detections_per_frame_max}`;
    open.append(name, meta);
    open.addEventListener("click", () => { showItem(it, null); glide("out"); });
    const del = document.createElement("button");
    del.type = "button"; del.className = "del"; del.textContent = "×"; del.setAttribute("aria-label", `Удалить ${it.name}`);
    del.addEventListener("click", () => removeHistory(it.id));
    li.append(open, del);
    return li;
  }));
}

// ---------- графики ----------
function bars(canvas, counts, frames, axes, onHover) {
  const dpr = devicePixelRatio || 1, w = canvas.clientWidth || 600, h = canvas.clientHeight || 100;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!counts.length) return;
  const pad = axes ? { l: 28, b: 4, t: 6 } : { l: 0, b: 0, t: 2 };
  const pw = w - pad.l, ph = h - pad.t - pad.b, max = Math.max(1, ...counts);
  const gap = counts.length > 80 ? 1 : 3, bw = Math.max(1, (pw - gap * (counts.length - 1)) / counts.length);
  const draw = (hot = -1) => {
    ctx.clearRect(0, 0, w, h);
    if (axes) {
      ctx.font = "12px ui-monospace, Consolas, monospace"; ctx.fillStyle = MUTE; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillText(String(max), pad.l - 6, pad.t + 6); ctx.fillText("0", pad.l - 6, pad.t + ph);
    }
    counts.forEach((v, i) => {
      const bh = v === 0 ? 2 : Math.max(3, (v / max) * ph);
      ctx.fillStyle = i === hot || (hot < 0 && axes && v === max) ? HOT : v === 0 ? GRID : INK;
      ctx.fillRect(pad.l + i * (bw + gap), pad.t + ph - bh, bw, bh);
    });
  };
  draw();
  if (onHover) {
    canvas.onmousemove = (e) => { const i = Math.floor((e.offsetX - pad.l) / (bw + gap)); if (i >= 0 && i < counts.length) { draw(i); onHover(i); } };
    canvas.onmouseleave = () => { draw(); onHover(-1); };
  }
}
function drawChart() {
  const cap = $("chart-cap"), base = "Машин в одном кадре, а не разных машин.";
  bars($("chart"), S.counts, S.frames, true, (i) => {
    cap.classList.toggle("hit", i >= 0);
    cap.textContent = i < 0 ? base : `кадр ${nf.format(S.frames[i])} · машин: ${S.counts[i]}`;
  });
}
addEventListener("resize", () => { if (S.item) drawChart(); if (!$("spark").hidden) bars($("spark"), S.counts, [], false); });

// ---------- выбор видео ----------
const limitsText = () => `до ${S.cfg.limits.max_duration_sec} с · до ${S.cfg.limits.max_upload_mb} МБ`;
const fail = (t) => { $("err").textContent = t || ""; $("err").hidden = !t; };

function setStride(v) {
  const { min, max, step } = S.cfg.stride;
  const n = Math.max(min, Math.min(max, Math.round((Number(v) || min) / step) * step));
  $("stride").value = String(n);
  const out = $("stride-out");
  if (out.textContent !== String(n)) { out.textContent = String(n); out.classList.add("bump"); setTimeout(() => out.classList.remove("bump"), 160); }
  document.querySelectorAll("#ticks button").forEach((b) => b.classList.toggle("on", Number(b.dataset.v) === n));
}

function readDuration(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file), v = document.createElement("video");
    const done = (x) => { URL.revokeObjectURL(url); v.removeAttribute("src"); resolve(x); };
    v.preload = "metadata"; v.onloadedmetadata = () => done(v.duration); v.onerror = () => done(NaN);
    setTimeout(() => done(NaN), 4000); v.src = url;
  });
}

function clearFile() {
  S.file = null; $("file").value = "";
  if (S.previewUrl) { URL.revokeObjectURL(S.previewUrl); S.previewUrl = null; }
  $("preview").hidden = true; $("preview").removeAttribute("src");
  $("drop").classList.remove("has");
  $("drop-main").textContent = "Перетащите видео или нажмите";
  $("drop-sub").textContent = limitsText();
  $("go").disabled = true;
}

async function pickFile(file) {
  fail("");
  if (!file || $("pick").classList.contains("locked")) return;
  const { limits, extensions } = S.cfg;
  const ext = `.${file.name.split(".").pop().toLowerCase()}`;
  if (!extensions.includes(ext)) { clearFile(); return fail("Нужен mp4, mov, avi, mkv или webm."); }
  if (file.size > limits.max_upload_mb * 1048576) { clearFile(); return fail(`Файл ${mb(file.size)} МБ, а можно до ${limits.max_upload_mb} МБ.`); }
  const dur = await readDuration(file);
  if (Number.isFinite(dur) && dur > limits.max_duration_sec + 0.5) { clearFile(); return fail(`Видео ${Math.round(dur)} с, а можно до ${limits.max_duration_sec} с.`); }
  clearFile();
  S.file = file;
  $("drop").classList.add("has");
  $("drop-main").textContent = file.name;
  $("drop-sub").textContent = `${mb(file.size)} МБ${Number.isFinite(dur) ? ` · ${Math.round(dur)} с` : ""} · нажмите, чтобы заменить`;
  S.previewUrl = URL.createObjectURL(file);
  $("preview").src = S.previewUrl; $("preview").hidden = false; // сразу можно посмотреть
  $("go").disabled = false;
}

function initPick() {
  const { min, max, step } = S.cfg.stride;
  Object.assign($("stride"), { min, max, step });
  for (let v = min; v <= max; v += step) {
    const b = document.createElement("button");
    b.type = "button"; b.dataset.v = String(v); b.textContent = String(v);
    b.addEventListener("click", () => setStride(v));
    $("ticks").append(b);
  }
  setStride(S.cfg.stride.default);
  $("stride").addEventListener("input", (e) => setStride(e.target.value));
  const drop = $("drop");
  $("file").addEventListener("change", () => pickFile($("file").files[0]));
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file").click(); } });
  let depth = 0;
  addEventListener("dragenter", (e) => { e.preventDefault(); depth++; drop.classList.add("over"); });
  addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; drop.classList.remove("over"); } });
  addEventListener("dragover", (e) => e.preventDefault());
  addEventListener("drop", (e) => { e.preventDefault(); depth = 0; drop.classList.remove("over"); pickFile(e.dataTransfer.files[0]); });
  $("go").addEventListener("click", () => start(false));
  $("sample").addEventListener("click", () => start(true));
  $("example").addEventListener("click", openExample);
  $("sample").hidden = !S.cfg.sample_available;
  $("example").hidden = !S.cfg.example_available;
  clearFile();
}

// ---------- ход работы ----------
const STEP_OF = { loading_model: "model", processing: "frames", encoding: "build", finalizing: "build" };
const ORDER = ["upload", "model", "frames", "build"];
const STATUS = { upload: "Отправляю видео", model: "Готовлю модель", frames: "Ищу машины", build: "Собираю результат" };

function setSteps(active, { failed = false, all = false } = {}) {
  const idx = ORDER.indexOf(active);
  document.querySelectorAll("#steps li").forEach((li) => {
    const i = ORDER.indexOf(li.dataset.s);
    li.classList.toggle("done", all || i < idx);
    li.classList.toggle("active", !all && !failed && i === idx);
    li.classList.toggle("failed", failed && i === idx);
  });
}
const detailOf = (step, text) => { $(`d-${step}`).textContent = text || ""; };

// Общий прогресс не откатывается: 0–10 % отправка файла, 10–100 % работа сервера.
function setProgress(pct, wait = false) {
  const v = Math.max(S.shown, Math.min(100, pct));
  S.shown = v;
  $("bar").querySelector("i").style.width = `${v}%`;
  $("bar").setAttribute("aria-valuenow", String(Math.round(v)));
  $("bar").classList.toggle("wait", wait);
  tween($("pct"), v, (x) => `${Math.round(x)}%`);
  document.title = `${Math.round(v)}% · Подсчёт автомобилей`;
}

function resetRun() {
  S.shown = 0; S.counts = [];
  $("pct").dataset.v = "0"; $("pct").textContent = "0%";
  $("bar").classList.remove("fail"); $("run-err").hidden = true; $("spark").hidden = true;
  for (const s of ORDER) detailOf(s, "");
  $("cancel").hidden = false; $("cancel").textContent = "Отмена"; $("cancel").onclick = cancel;
  setProgress(0, true); setSteps("upload");
  $("status").textContent = STATUS.upload;
}

function idle(message) {
  S.token = null; S.jobId = null; sset(null);
  $("pick").classList.remove("locked"); $("pick").removeAttribute("inert");
  $("cancel").hidden = true;
  document.title = "Подсчёт автомобилей в видео — Nazdratenko";
  if (message) toast(message);
}

function upload(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    S.xhr = xhr; xhr.open("POST", "/api/jobs"); xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      setProgress((e.loaded / e.total) * 10);
      detailOf("upload", `${mb(e.loaded)} из ${mb(e.total)} МБ`);
      if (e.loaded >= e.total) $("status").textContent = "Проверяю видео";
    };
    xhr.onload = () => { S.xhr = null; xhr.status >= 200 && xhr.status < 300 ? resolve(xhr.response) : reject(new ApiError(xhr.status, detail(xhr.response, xhr.status))); };
    xhr.onerror = () => { S.xhr = null; reject(new ApiError(0, "Нет связи с сервером. Попробуйте ещё раз.")); };
    xhr.onabort = () => { S.xhr = null; reject(new ApiError(-1, "")); };
    xhr.send(form);
  });
}

async function start(useSample) {
  if (!useSample && !S.file) return;
  const form = new FormData();
  form.append("stride", $("stride").value);
  const id = Date.now().toString(36);
  S.pend = { id, name: useSample ? "Встроенный пример" : S.file.name, src: !useSample };
  if (useSample) form.append("use_sample", "true"); else { form.append("file", S.file, S.file.name); bPut(`${id}:src`, S.file); }
  $("pick").classList.add("locked"); $("pick").setAttribute("inert", "");
  resetRun(); glide("run");
  if (useSample) detailOf("upload", "не нужна");
  try {
    const { job_id: job } = await upload(form);
    S.jobId = job; sset({ job, ...S.pend });
    setProgress(10, true); setSteps("model"); detailOf("upload", "готово");
    poll(job);
  } catch (err) {
    bDel(`${id}:src`);
    idle(err.status === -1 ? "Отменено" : "");
    if (err.status !== -1) fail(err.message);
  }
}

function render(s) {
  const step = STEP_OF[s.stage] || "model";
  setSteps(step);
  setProgress(10 + s.percent * 0.9, s.stage === "loading_model");
  $("status").textContent = s.stage === "loading_model" && s.wall_sec > 20 ? "Готовлю модель, на бесплатном сервере это долго" : STATUS[step];
  detailOf("model", step === "model" ? clock(s.wall_sec) : "готово");
  if (step === "frames" || step === "build") {
    detailOf("frames", `${nf.format(s.frames_processed)} из ~${nf.format(s.frames_expected)}`);
    detailOf("build", step === "build" ? "идёт" : "");
    $("spark").hidden = false;
    S.counts = s.counts || [];
    bars($("spark"), S.counts, [], false);
  }
}

async function poll(job) {
  const token = {}; S.token = token;
  let misses = 0;
  while (S.token === token) {
    try {
      const s = await api(`/api/jobs/${job}`);
      if (S.token !== token) return;
      misses = 0;
      if (s.status === "running") render(s); else return end(s);
    } catch (err) {
      if (S.token !== token) return;
      if (err.status === 404) { idle(); return fail("Сервис перезапустился, задача потеряна. Запустите заново."); }
      misses++; $("status").textContent = "Нет связи, пробую снова";
    }
    await sleep(Math.min(1000 + misses * 600, 5000));
  }
}

function end(s) {
  S.token = null; sset(null);
  if (s.status === "done") {
    setProgress(100); setSteps("build", { all: true });
    $("status").textContent = "Готово"; $("cancel").hidden = true;
    $("pick").classList.remove("locked"); $("pick").removeAttribute("inert");
    const r = s.report, p = S.pend || { id: Date.now().toString(36), name: s.source_label || "Видео", src: false };
    const item = { id: p.id, name: p.name, stride: r.stride, ts: Date.now(), report: r, counts: s.counts || [], frames: s.frame_numbers || [], src: p.src, ann: false };
    addHistory(item);
    showItem({ ...item, hasAnn: s.has_video }, { base: s.files_base, zip: s.archive_url });
    saveAnnotated(item.id, `${s.files_base}annotated.mp4`, s.has_video);
    glide("out");
    return;
  }
  if (s.status === "cancelled") { bDel(`${S.pend?.id}:src`); return idle("Отменено"); }
  $("bar").classList.remove("wait"); $("bar").classList.add("fail");
  setSteps(document.querySelector("#steps li.active")?.dataset.s || "model", { failed: true });
  $("status").textContent = "Не получилось";
  $("run-err").textContent = s.error || "Обработка завершилась с ошибкой."; $("run-err").hidden = false;
  bDel(`${S.pend?.id}:src`);
  $("cancel").textContent = "Понятно";
  $("cancel").onclick = () => idle();
}

async function saveAnnotated(id, url, has) {
  if (!has) return;
  try {
    const blob = await (await fetch(url)).blob();
    if (blob.size > 120 * 1048576) return;
    await bPut(`${id}:ann`, blob);
    updateHistory(id, { ann: true });
  } catch { /* видео с рамками останется только на сервере */ }
}

async function cancel() {
  if (S.xhr) return S.xhr.abort();
  $("cancel").disabled = true; $("status").textContent = "Останавливаю";
  try { await api(`/api/jobs/${S.jobId}/cancel`, { method: "POST" }); }
  catch (err) { if (err.status === 404) idle(); }
  finally { $("cancel").disabled = false; }
}

// ---------- результат ----------
function colorJson(v) {
  const t = JSON.stringify(v, null, 2).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return t.replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(null)\b|\b(true|false|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b/g, (m, str, colon, nul, num) =>
    str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`) : nul ? `<span class="z">${nul}</span>` : `<span class="n">${num}</span>`);
}
function tile(label, value, sub) {
  const box = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
  dt.textContent = label; dd.textContent = value;
  if (sub) { const sm = document.createElement("small"); sm.textContent = sub; dd.append(sm); }
  box.append(dt, dd); return box;
}

async function videoSources(item, live) {
  const out = {};
  if (item.src) { const b = await bGet(`${item.id}:src`); if (b) out.src = URL.createObjectURL(b); }
  if (live && item.hasAnn) out.ann = `${live.base}annotated.mp4`;
  else if (item.ann) { const b = await bGet(`${item.id}:ann`); if (b) out.ann = URL.createObjectURL(b); }
  return out;
}

async function showItem(item, live) {
  S.item = item;
  const r = item.report;
  S.urls.forEach((u) => URL.revokeObjectURL(u)); S.urls = [];
  S.counts = item.counts; S.frames = item.frames;
  $("lead").textContent = `${item.name} · каждый ${r.stride}-й кадр, ${nf.format(r.frames_processed)} из ${nf.format(r.frames_total)}`;
  $("metrics").replaceChildren(
    tile("Среднее на кадр", nf2.format(r.detections_per_frame_mean)),
    tile("Максимум", nf.format(r.detections_per_frame_max)),
    tile("Кадров", nf.format(r.frames_processed), `из ${nf.format(r.frames_total)}`),
    tile("Время", clock(r.elapsed_sec)),
  );
  S.report = JSON.stringify(r, null, 2);
  $("json").innerHTML = colorJson(r);
  const blobUrl = URL.createObjectURL(new Blob([S.report], { type: "application/json" }));
  S.urls.push(blobUrl);
  $("dl-report").href = live ? `${live.base}report.json?download=true` : blobUrl;
  $("dl-zip").hidden = !live?.zip; if (live?.zip) $("dl-zip").href = live.zip;

  $("out").classList.add("open"); $("out").removeAttribute("inert");
  requestAnimationFrame(drawChart); setTimeout(drawChart, 800);
  renderHistory();

  const src = await videoSources(item, live);
  S.urls.push(...Object.values(src).filter((u) => u.startsWith("blob:")));
  if (S.item !== item) return;
  const kinds = ["src", "ann"].filter((k) => src[k]);
  $("tabs").hidden = kinds.length < 2;
  $("video").hidden = kinds.length === 0;
  const pick = (k) => {
    const v = $("video"), t = v.currentTime || 0;
    v.src = src[k];
    v.addEventListener("loadedmetadata", () => { try { v.currentTime = t; } catch { /* ok */ } }, { once: true });
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.v === k));
  };
  document.querySelectorAll("#tabs button").forEach((b) => { b.hidden = !src[b.dataset.v]; b.onclick = () => pick(b.dataset.v); });
  if (kinds.length) pick(src.ann ? "ann" : "src");
}

async function openExample() {
  try {
    const s = await api("/api/example"), r = s.report;
    showItem({ id: "example", name: "Готовый пример", stride: r.stride, ts: Date.now(), report: r, counts: s.counts || [], frames: s.frame_numbers || [], src: false, ann: false, hasAnn: s.has_video }, { base: s.files_base, zip: s.archive_url });
    glide("out");
  } catch (err) { toast(err.message); }
}

async function copyReport() {
  try { await navigator.clipboard.writeText(S.report); }
  catch { const a = document.createElement("textarea"); a.value = S.report; document.body.append(a); a.select(); document.execCommand("copy"); a.remove(); }
  toast("Скопировано");
}

// ---------- запуск ----------
async function init() {
  try { S.cfg = await api("/api/config"); }
  catch {
    S.cfg = { stride: { default: 5, min: 5, max: 30, step: 5 }, limits: { max_upload_mb: 25, max_duration_sec: 60 }, extensions: [".mp4", ".mov", ".avi", ".mkv", ".webm"], sample_available: false, example_available: false };
    toast("Сервер просыпается, подождите минуту.");
  }
  initPick();
  $("copy").addEventListener("click", copyReport);
  renderHistory();
  const saved = sget();
  if (saved?.job) {
    S.jobId = saved.job; S.pend = { id: saved.id, name: saved.name, src: saved.src };
    $("pick").classList.add("locked"); $("pick").setAttribute("inert", "");
    resetRun(); setSteps("model"); detailOf("upload", "готово"); setProgress(10, true);
    poll(saved.job);
  }
}
init();
