// ЛР №4: одна страница. Старт → процесс → результат раскрываются друг под другом.

const $ = (id) => document.getElementById(id);
const STORE_KEY = "lr04.job";
const INK = "#16150F", HOT = "#FF5A1F", MUTE = "#7A766A", GRID = "rgba(22,21,15,.16)";
const nf = new Intl.NumberFormat("ru-RU");
const nf2 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;

const S = { cfg: null, file: null, xhr: null, token: null, jobId: null, shown: 0, counts: [], frames: [], example: false, report: "" };

// ---------- помощники ----------

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
const detail = (body, status) => (body && typeof body.detail === "string" ? body.detail : `Ошибка сервера ${status}.`);

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
const clock = (sec) => {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const keep = (v) => { try { v ? sessionStorage.setItem(STORE_KEY, v) : sessionStorage.removeItem(STORE_KEY); } catch { /* ok */ } };
const kept = () => { try { return sessionStorage.getItem(STORE_KEY); } catch { return null; } };

let toastT = 0;
function toast(text) {
  $("toast").textContent = text;
  $("toast").classList.add("show");
  clearTimeout(toastT);
  toastT = setTimeout(() => $("toast").classList.remove("show"), 4000);
}

function panel(id, open) {
  const el = $(id);
  el.classList.toggle("open", open);
  el.toggleAttribute("inert", !open);
}
const glide = (id) => setTimeout(() => $(id).scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" }), 120);

// Число «доезжает» до нового значения.
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

// ---------- график ----------

function bars(canvas, counts, frames, axes, onHover) {
  const dpr = devicePixelRatio || 1;
  const w = canvas.clientWidth || 600, h = canvas.clientHeight || 100;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!counts.length) return;
  const pad = axes ? { l: 28, b: 22, t: 6 } : { l: 0, b: 0, t: 2 };
  const pw = w - pad.l, ph = h - pad.t - pad.b;
  const max = Math.max(1, ...counts);
  const gap = counts.length > 80 ? 1 : 3;
  const bw = Math.max(1, (pw - gap * (counts.length - 1)) / counts.length);
  const draw = (hot = -1) => {
    ctx.clearRect(0, 0, w, h);
    if (axes) {
      ctx.font = "12px ui-monospace, Consolas, monospace";
      ctx.fillStyle = MUTE; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      ctx.fillText(String(max), pad.l - 6, pad.t + 6);
      ctx.fillText("0", pad.l - 6, pad.t + ph);
      ctx.fillRect(pad.l, pad.t + ph, pw, 1);
    }
    counts.forEach((v, i) => {
      const bh = v === 0 ? 2 : Math.max(3, (v / max) * ph);
      ctx.fillStyle = i === hot || (hot < 0 && axes && v === max) ? HOT : v === 0 ? GRID : INK;
      ctx.fillRect(pad.l + i * (bw + gap), pad.t + ph - bh, bw, bh);
    });
  };
  draw();
  if (onHover) {
    canvas.onmousemove = (e) => {
      const i = Math.floor((e.offsetX - pad.l) / (bw + gap));
      if (i >= 0 && i < counts.length) { draw(i); onHover(i); }
    };
    canvas.onmouseleave = () => { draw(); onHover(-1); };
  }
}

addEventListener("resize", () => {
  if ($("out-panel").classList.contains("open")) drawChart();
  if ($("run-panel").classList.contains("open")) bars($("spark"), S.counts, [], false);
});

function drawChart() {
  const cap = $("chart-cap");
  const base = cap.dataset.base || cap.textContent;
  cap.dataset.base = base;
  bars($("chart"), S.counts, S.frames, true, (i) => {
    cap.classList.toggle("hit", i >= 0);
    cap.textContent = i < 0 ? base : `кадр ${nf.format(S.frames[i])} · машин: ${S.counts[i]}`;
  });
}

// ---------- старт ----------

function setStride(v) {
  const { min, max, step } = S.cfg.stride;
  const n = Math.max(min, Math.min(max, Math.round((Number(v) || min) / step) * step));
  $("stride").value = String(n);
  const out = $("stride-out");
  if (out.textContent !== String(n)) {
    out.textContent = String(n);
    out.classList.add("bump");
    setTimeout(() => out.classList.remove("bump"), 160);
  }
  document.querySelectorAll("#ticks button").forEach((b) => b.classList.toggle("on", Number(b.dataset.v) === n));
}

function fail(text) { $("err").textContent = text || ""; $("err").hidden = !text; }

function readDuration(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const v = document.createElement("video");
    const done = (x) => { URL.revokeObjectURL(url); v.removeAttribute("src"); resolve(x); };
    v.preload = "metadata";
    v.onloadedmetadata = () => done(v.duration);
    v.onerror = () => done(NaN); // avi/mkv браузер может не открыть: проверит сервер
    setTimeout(() => done(NaN), 4000);
    v.src = url;
  });
}

function clearFile() {
  S.file = null;
  $("file").value = "";
  $("drop").classList.remove("has");
  $("drop-main").textContent = "Перетащите видео или нажмите";
  $("drop-sub").textContent = limitsText();
  $("go").disabled = true;
}

const limitsText = () => `до ${S.cfg.limits.max_duration_sec} с · до ${S.cfg.limits.max_upload_mb} МБ`;

async function pickFile(file) {
  fail("");
  if (!file || $("start").classList.contains("locked")) return;
  const { limits, extensions } = S.cfg;
  const ext = `.${file.name.split(".").pop().toLowerCase()}`;
  if (!extensions.includes(ext)) { clearFile(); return fail("Нужен mp4, mov, avi, mkv или webm."); }
  if (file.size > limits.max_upload_mb * 1048576) { clearFile(); return fail(`Файл ${mb(file.size)} МБ, а можно до ${limits.max_upload_mb} МБ.`); }
  const dur = await readDuration(file);
  if (Number.isFinite(dur) && dur > limits.max_duration_sec + 0.5) { clearFile(); return fail(`Видео ${Math.round(dur)} с, а можно до ${limits.max_duration_sec} с.`); }
  S.file = file;
  $("drop").classList.add("has");
  $("drop-main").textContent = file.name;
  $("drop-sub").textContent = `${mb(file.size)} МБ${Number.isFinite(dur) ? ` · ${Math.round(dur)} с` : ""}`;
  $("go").disabled = false;
}

function lock(on) {
  $("start").classList.toggle("locked", on);
  $("start").toggleAttribute("inert", on);
}

function initStart() {
  const { min, max, step } = S.cfg.stride;
  $("stride").min = min; $("stride").max = max; $("stride").step = step;
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
  // видео можно бросить в любое место страницы
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

// ---------- процесс ----------

const STATUS = { loading_model: "Готовлю модель", processing: "Ищу машины", encoding: "Собираю видео", finalizing: "Пишу отчёт", done: "Готово" };

// Общий прогресс не откатывается назад: 0–10 % отправка файла, 10–100 % работа сервера.
function setProgress(pct, { wait = false } = {}) {
  const value = Math.max(S.shown, Math.min(100, pct));
  S.shown = value;
  $("bar").querySelector("i").style.width = `${value}%`;
  $("bar").setAttribute("aria-valuenow", String(Math.round(value)));
  $("bar").classList.toggle("wait", wait);
  tween($("pct"), value, (v) => `${Math.round(v)}%`);
  document.title = `${Math.round(value)}% · Подсчёт автомобилей`;
}

function openRun() {
  S.shown = 0; S.counts = [];
  $("pct").dataset.v = "0"; $("pct").textContent = "0%";
  $("bar").classList.remove("fail");
  $("run-err").hidden = true;
  $("cancel").hidden = false;
  $("live").hidden = true; $("spark").hidden = true;
  $("status").textContent = "Готовлюсь";
  $("s-frame").dataset.v = "0"; $("s-last").dataset.v = "0";
  setProgress(0, { wait: true });
  lock(true);
  panel("out-panel", false);
  panel("run-panel", true);
  glide("run-panel");
}

function closeRun(message) {
  S.token = null; S.jobId = null; keep(null);
  document.title = "Подсчёт автомобилей в видео — Nazdratenko";
  panel("run-panel", false);
  lock(false);
  glide("start");
  if (message) toast(message);
}

function upload(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    S.xhr = xhr;
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      setProgress((e.loaded / e.total) * 10);
      $("status").textContent = e.loaded >= e.total ? "Проверяю видео" : `Отправляю · ${mb(e.loaded)} из ${mb(e.total)} МБ`;
    };
    xhr.onload = () => {
      S.xhr = null;
      xhr.status >= 200 && xhr.status < 300 ? resolve(xhr.response) : reject(new ApiError(xhr.status, detail(xhr.response, xhr.status)));
    };
    xhr.onerror = () => { S.xhr = null; reject(new ApiError(0, "Нет связи с сервером. Попробуйте ещё раз.")); };
    xhr.onabort = () => { S.xhr = null; reject(new ApiError(-1, "")); };
    xhr.send(form);
  });
}

async function start(useSample) {
  if (!useSample && !S.file) return;
  S.example = false;
  const form = new FormData();
  form.append("stride", $("stride").value);
  if (useSample) form.append("use_sample", "true"); else form.append("file", S.file, S.file.name);
  openRun();
  $("status").textContent = useSample ? "Запускаю пример" : "Отправляю видео";
  try {
    const { job_id: id } = await upload(form);
    S.jobId = id; keep(id);
    setProgress(10, { wait: true });
    poll(id);
  } catch (err) {
    if (err.status === -1) return closeRun("Отменено");
    closeRun();
    fail(err.message);
  }
}

function render(s) {
  const live = ["processing", "encoding", "finalizing"].includes(s.stage);
  setProgress(10 + s.percent * 0.9, { wait: s.stage === "loading_model" });
  $("status").textContent = s.stage === "loading_model" && s.wall_sec > 20 ? "Готовлю модель, на бесплатном сервере это долго" : STATUS[s.stage] || "Работаю";
  $("live").hidden = !live; $("spark").hidden = !live;
  if (!live) return;
  tween($("s-frame"), s.frames_read);
  tween($("s-last"), s.last_count);
  $("s-eta").textContent = s.stage === "processing" && s.eta_sec != null ? `~${clock(s.eta_sec)}` : "—";
  S.counts = s.counts || [];
  bars($("spark"), S.counts, [], false);
}

async function poll(id) {
  const token = {};
  S.token = token;
  let misses = 0;
  while (S.token === token) {
    try {
      const s = await api(`/api/jobs/${id}`);
      if (S.token !== token) return;
      misses = 0;
      if (s.status === "running") render(s); else return end(s);
    } catch (err) {
      if (S.token !== token) return;
      if (err.status === 404) { closeRun(); return fail("Сервис перезапустился, задача потеряна. Запустите заново."); }
      misses++;
      $("status").textContent = "Нет связи, пробую снова";
    }
    await sleep(Math.min(1000 + misses * 600, 5000));
  }
}

function end(s) {
  S.token = null; keep(null);
  if (s.status === "done") {
    setProgress(100);
    S.example = false;
    return setTimeout(() => result(s), calm ? 0 : 700);
  }
  if (s.status === "cancelled") return closeRun("Отменено");
  $("bar").classList.remove("wait");
  $("bar").classList.add("fail");
  $("status").textContent = "Не получилось";
  $("run-err").textContent = s.error || "Обработка завершилась с ошибкой.";
  $("run-err").hidden = false;
  $("cancel").textContent = "Назад";
  $("cancel").onclick = () => { $("cancel").textContent = "Отмена"; $("cancel").onclick = cancel; closeRun(); };
}

async function cancel() {
  if (S.xhr) return S.xhr.abort();
  $("cancel").disabled = true;
  $("status").textContent = "Останавливаю";
  try { await api(`/api/jobs/${S.jobId}/cancel`, { method: "POST" }); }
  catch (err) { if (err.status === 404) closeRun(); }
  finally { $("cancel").disabled = false; }
}

// ---------- результат ----------

function colorJson(value) {
  const text = JSON.stringify(value, null, 2).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  return text.replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(null)\b|\b(true|false|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b/g, (m, str, colon, nul, num) =>
    str ? (colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`) : nul ? `<span class="z">${nul}</span>` : `<span class="n">${num}</span>`);
}

function tile(label, value, sub) {
  const box = document.createElement("div");
  const dt = document.createElement("dt"), dd = document.createElement("dd");
  dt.textContent = label; dd.textContent = value;
  if (sub) { const small = document.createElement("small"); small.textContent = sub; dd.append(small); }
  box.append(dt, dd);
  return box;
}

function result(s) {
  const r = s.report, base = s.files_base;
  S.counts = s.counts || []; S.frames = s.frame_numbers || [];
  $("lead").textContent = S.example
    ? "Готовый результат примера."
    : `Каждый ${r.stride}-й кадр: ${nf.format(r.frames_processed)} из ${nf.format(r.frames_total)}.`;
  $("metrics").replaceChildren(
    tile("Среднее на кадр", nf2.format(r.detections_per_frame_mean)),
    tile("Максимум", nf.format(r.detections_per_frame_max)),
    tile("Кадров", nf.format(r.frames_processed), `из ${nf.format(r.frames_total)}`),
    tile("Время", clock(r.elapsed_sec)),
  );

  $("video").hidden = !s.has_video;
  if (s.has_video) $("video").src = `${base}annotated.mp4`;
  const strip = $("strip");
  strip.replaceChildren();
  for (const name of s.frames || []) {
    const b = document.createElement("button");
    const img = document.createElement("img");
    img.loading = "lazy"; img.src = `${base}frames/${name}`; img.alt = `Кадр ${Number(name.replace(/\D/g, ""))}`;
    b.type = "button"; b.setAttribute("aria-label", img.alt); b.append(img);
    b.addEventListener("click", () => { $("lb-img").src = img.src; $("lb").showModal(); });
    strip.append(b);
  }
  strip.hidden = !strip.children.length;

  S.report = JSON.stringify(r, null, 2);
  $("json").innerHTML = colorJson(r);
  $("dl-report").href = `${base}report.json?download=true`;
  $("dl-zip").href = s.archive_url;

  lock(false);
  panel("run-panel", false);
  panel("out-panel", true);
  requestAnimationFrame(drawChart);
  setTimeout(drawChart, 850); // после раскрытия панели ширина canvas окончательная
  glide("out-panel");
  document.title = "Готово · Подсчёт автомобилей";
}

async function openExample() {
  try { S.example = true; result(await api("/api/example")); }
  catch (err) { S.example = false; toast(err.message); }
}

async function copyReport() {
  try { await navigator.clipboard.writeText(S.report); }
  catch {
    const a = document.createElement("textarea");
    a.value = S.report; document.body.append(a); a.select(); document.execCommand("copy"); a.remove();
  }
  toast("Скопировано");
}

// ---------- запуск ----------

async function init() {
  try { S.cfg = await api("/api/config"); }
  catch {
    S.cfg = { stride: { default: 5, min: 5, max: 30, step: 5 }, limits: { max_upload_mb: 25, max_duration_sec: 60 },
      extensions: [".mp4", ".mov", ".avi", ".mkv", ".webm"], sample_available: false, example_available: false };
    toast("Сервер просыпается, подождите минуту.");
  }
  initStart();
  $("cancel").onclick = cancel;
  $("copy").addEventListener("click", copyReport);
  $("again").addEventListener("click", () => {
    panel("out-panel", false);
    document.title = "Подсчёт автомобилей в видео — Nazdratenko";
    clearFile();
    glide("start");
  });
  $("lb").addEventListener("click", () => $("lb").close());

  const id = kept();
  if (id) { S.jobId = id; openRun(); poll(id); }
}

init();
