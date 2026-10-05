// Интерфейс ЛР №4. Чистый JS без сборки: загрузка → обработка → результаты.
// Прогресс виден на каждом ожидании: отправка файла (XHR), подготовка модели, кадры, сборка.

const $ = (id) => document.getElementById(id);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const STORE_KEY = "lr04.job";
const RIBBON_CELLS = 30;
const POLL_MS = 1000;
const COLORS = { a: "#5B8CFF", b: "#8B7BFF", grid: "rgba(31,42,68,.10)", text: "#5F6E8C" };
const nfInt = new Intl.NumberFormat("ru-RU");
const nfDec = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

const state = {
  cfg: null,
  file: null,
  xhr: null,
  pollToken: null,
  jobId: null,
  isExample: false,
  lastCounts: [],
  lastFrames: [],
};

// ---------- мелкие помощники ----------

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function detailText(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  return `Сервер вернул ошибку ${status}.`;
}

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new ApiError(0, "Сервер не отвечает.");
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* тело не JSON */
  }
  if (!res.ok) throw new ApiError(res.status, detailText(body, res.status));
  return body;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fmtTime(sec) {
  if (sec == null || !Number.isFinite(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

const fmtMB = (bytes) => (bytes / 1048576).toFixed(1).replace(".", ",");

function store(value) {
  try {
    if (value) sessionStorage.setItem(STORE_KEY, value);
    else sessionStorage.removeItem(STORE_KEY);
  } catch {
    /* хранилище недоступно: обновление страницы просто начнёт с начала */
  }
}

function stored() {
  try {
    return sessionStorage.getItem(STORE_KEY);
  } catch {
    return null;
  }
}

let toastTimer = 0;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 4500);
}

function show(name) {
  for (const key of ["upload", "progress", "result"]) {
    const el = $(`view-${key}`);
    el.hidden = key !== name;
    if (key === name) {
      el.classList.remove("enter");
      void el.offsetWidth; // перезапуск анимации появления
      el.classList.add("enter");
    }
  }
  window.scrollTo({ top: 0 });
}

// Плавный счётчик чисел: значение «доезжает» до нового за 350 мс.
function tween(el, to, fmt = (v) => nfInt.format(Math.round(v))) {
  const from = Number(el.dataset.v ?? 0);
  el.dataset.v = String(to);
  cancelAnimationFrame(Number(el.dataset.raf || 0));
  if (reduceMotion || from === to) {
    el.textContent = fmt(to);
    return;
  }
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min((now - t0) / 350, 1);
    el.textContent = fmt(from + (to - from) * (1 - (1 - k) ** 3));
    if (k < 1) el.dataset.raf = String(requestAnimationFrame(step));
  };
  el.dataset.raf = String(requestAnimationFrame(step));
}

function setBar(barEl, percent) {
  const value = Math.max(0, Math.min(100, percent));
  barEl.querySelector("i").style.width = `${value}%`;
  if (barEl.hasAttribute("aria-valuenow")) barEl.setAttribute("aria-valuenow", String(Math.round(value)));
}

// ---------- график: столбики числа машин по обработанным кадрам ----------

function drawBars(canvas, counts, frames, withAxes) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 600;
  const height = Number(canvas.getAttribute("height")) || 120;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  if (!counts.length) return;

  const pad = withAxes ? { l: 34, r: 4, t: 10, b: 24 } : { l: 0, r: 0, t: 4, b: 0 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  const max = Math.max(1, ...counts);
  const gap = counts.length > 90 ? 1 : 3;
  const barW = Math.max(1, (plotW - gap * (counts.length - 1)) / counts.length);

  ctx.font = "12px Inter, system-ui, sans-serif";
  ctx.fillStyle = COLORS.text;
  if (withAxes) {
    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    for (const level of [0, max]) {
      const y = pad.t + plotH - (level / max) * plotH;
      ctx.beginPath();
      ctx.moveTo(pad.l, y + 0.5);
      ctx.lineTo(width - pad.r, y + 0.5);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(String(level), pad.l - 8, y);
    }
    ctx.textBaseline = "top";
    ctx.textAlign = "left";
    ctx.fillText(`кадр ${frames.length ? frames[0] : 0}`, pad.l, height - pad.b + 8);
    ctx.textAlign = "right";
    if (frames.length > 1) ctx.fillText(`кадр ${frames[frames.length - 1]}`, width - pad.r, height - pad.b + 8);
  }

  const gradient = ctx.createLinearGradient(0, pad.t, 0, pad.t + plotH);
  gradient.addColorStop(0, COLORS.b);
  gradient.addColorStop(1, COLORS.a);
  counts.forEach((value, i) => {
    const h = value === 0 ? 2 : Math.max(3, (value / max) * plotH);
    ctx.fillStyle = value === 0 ? COLORS.grid : gradient;
    const x = pad.l + i * (barW + gap);
    ctx.beginPath();
    ctx.roundRect(x, pad.t + plotH - h, barW, h, Math.min(3, barW / 2));
    ctx.fill();
  });
}

let resizeTimer = 0;
addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (!$("view-result").hidden) drawBars($("chart"), state.lastCounts, state.lastFrames, true);
    if (!$("live").hidden) drawBars($("spark"), state.lastCounts, [], false);
  }, 150);
});

// ---------- A. экран загрузки ----------

function renderRibbon(stride) {
  const ribbon = $("ribbon");
  if (!ribbon.children.length) {
    for (let i = 0; i < RIBBON_CELLS; i++) ribbon.append(document.createElement("i"));
  }
  [...ribbon.children].forEach((cell, i) => cell.classList.toggle("on", i % stride === 0));
  const rate = nfDec.format(30 / stride);
  $("ribbon-caption").textContent =
    stride === 1
      ? "Анализируется каждый кадр: самый точный и самый медленный режим."
      : `Анализируется каждый ${stride}-й кадр (подсвечены на ленте из 30 кадров). При 30 кадр/с это ${rate} анализов в секунду.`;
}

function setStride(value) {
  const stride = Math.max(1, Math.min(30, Number(value) || 5));
  $("stride").value = String(stride);
  $("stride-out").textContent = String(stride);
  renderRibbon(stride);
}

function uploadError(message) {
  const el = $("upload-error");
  el.textContent = message || "";
  el.hidden = !message;
}

function readDuration(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    const done = (value) => {
      URL.revokeObjectURL(url);
      video.removeAttribute("src");
      resolve(value);
    };
    video.preload = "metadata";
    video.onloadedmetadata = () => done(video.duration);
    video.onerror = () => done(NaN); // avi/mkv браузер может не открыть: тогда проверит сервер
    setTimeout(() => done(NaN), 5000);
    video.src = url;
  });
}

function clearFile() {
  state.file = null;
  $("file-input").value = "";
  $("dropzone").classList.remove("has-file");
  $("drop-title").textContent = "Перетащите видео сюда или нажмите, чтобы выбрать";
  $("drop-hint").textContent = "mp4, mov, avi, mkv или webm";
  $("btn-start").disabled = true;
}

async function setFile(file) {
  uploadError("");
  if (!file) return;
  const { limits, extensions } = state.cfg;
  const ext = `.${file.name.split(".").pop().toLowerCase()}`;
  if (!extensions.includes(ext)) {
    clearFile();
    return uploadError(`Формат ${ext} не поддерживается. Нужен mp4, mov, avi, mkv или webm.`);
  }
  if (file.size > limits.max_upload_mb * 1048576) {
    clearFile();
    return uploadError(`Файл весит ${fmtMB(file.size)} МБ, допустимо до ${limits.max_upload_mb} МБ. Сожмите или обрежьте видео.`);
  }
  const duration = await readDuration(file);
  if (Number.isFinite(duration) && duration > limits.max_duration_sec + 0.5) {
    clearFile();
    return uploadError(`Видео длится ${Math.round(duration)} с, допустимо до ${limits.max_duration_sec} с. Обрежьте ролик.`);
  }
  state.file = file;
  $("dropzone").classList.add("has-file");
  $("drop-title").textContent = file.name;
  const length = Number.isFinite(duration) ? `, ${Math.round(duration)} с` : "";
  $("drop-hint").textContent = `${fmtMB(file.size)} МБ${length}. Нажмите, чтобы заменить.`;
  $("btn-start").disabled = false;
}

function initUpload() {
  const zone = $("dropzone");
  const input = $("file-input");
  input.addEventListener("change", () => setFile(input.files[0]));
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  for (const type of ["dragenter", "dragover"]) {
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    });
  }
  for (const type of ["dragleave", "drop"]) {
    zone.addEventListener(type, (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
    });
  }
  zone.addEventListener("drop", (e) => setFile(e.dataTransfer.files[0]));

  $("stride").addEventListener("input", (e) => setStride(e.target.value));
  $("stride-reset").addEventListener("click", () => setStride(state.cfg.stride.default));
  $("btn-start").addEventListener("click", () => startJob(false));
  $("btn-sample").addEventListener("click", () => startJob(true));
  $("btn-example").addEventListener("click", openExample);
}

// ---------- B. экран обработки ----------

const STEPS = ["upload", "model", "frames", "build", "done"];
const STAGE_TO_STEP = { loading_model: "model", processing: "frames", encoding: "build", finalizing: "build", done: "done" };

function setSteps(activeKey, { failed = false } = {}) {
  const activeIndex = STEPS.indexOf(activeKey);
  document.querySelectorAll("#stepper li").forEach((li) => {
    const index = STEPS.indexOf(li.dataset.step);
    li.classList.toggle("done", index < activeIndex || (activeKey === "done" && !failed));
    li.classList.toggle("active", index === activeIndex && activeKey !== "done");
    li.classList.toggle("failed", failed && index === activeIndex);
  });
}

function resetProgressView() {
  for (const id of ["d-upload", "d-model", "d-frames", "d-build", "d-done"]) $(id).textContent = "";
  setBar($("main-bar"), 0);
  $("main-bar").classList.add("running");
  $("main-bar").classList.remove("failed", "finished");
  $("p-percent").dataset.v = "0";
  $("p-percent").textContent = "0%";
  $("p-status").textContent = "Готовлюсь к запуску";
  $("live").hidden = true;
  $("progress-error").hidden = true;
  $("progress-back").hidden = true;
  $("btn-cancel").hidden = false;
  $("progress-note").hidden = false;
  $("h-progress").textContent = "Обработка идёт";
  $("upload-bar").hidden = true;
  state.lastCounts = [];
  document.title = "Подсчёт автомобилей в видео — Nazdratenko";
}

function showProgressError(message) {
  $("main-bar").classList.remove("running");
  $("main-bar").classList.add("failed");
  $("h-progress").textContent = "Не получилось";
  $("progress-error").textContent = message;
  $("progress-error").hidden = false;
  $("progress-back").hidden = false;
  $("btn-cancel").hidden = true;
  $("progress-note").hidden = true;
  const active = document.querySelector("#stepper li.active");
  if (active) setSteps(active.dataset.step, { failed: true });
  document.title = "Ошибка — Подсчёт автомобилей в видео";
}

function backToUpload(message) {
  state.pollToken = null;
  state.jobId = null;
  store(null);
  show("upload");
  if (message) toast(message);
}

function uploadWithProgress(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    state.xhr = xhr;
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    const started = performance.now();
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = (e.loaded / e.total) * 100;
      const seconds = Math.max((performance.now() - started) / 1000, 0.1);
      const speed = e.loaded / seconds;
      setBar($("upload-bar"), pct);
      const left = speed > 0 ? (e.total - e.loaded) / speed : null;
      $("d-upload").textContent =
        e.loaded >= e.total
          ? "Файл отправлен. Сервер проверяет видео…"
          : `${Math.round(pct)}% · ${fmtMB(e.loaded)} из ${fmtMB(e.total)} МБ · ${fmtMB(speed)} МБ/с · осталось ${fmtTime(left)}`;
      $("p-status").textContent = "Отправляю файл на сервер";
    };
    xhr.onload = () => {
      state.xhr = null;
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response);
      else reject(new ApiError(xhr.status, detailText(xhr.response, xhr.status)));
    };
    xhr.onerror = () => {
      state.xhr = null;
      reject(new ApiError(0, "Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз."));
    };
    xhr.onabort = () => {
      state.xhr = null;
      reject(new ApiError(-1, "Загрузка отменена."));
    };
    xhr.send(form);
  });
}

async function startJob(useSample) {
  if (!useSample && !state.file) return;
  state.isExample = false;
  const form = new FormData();
  form.append("stride", $("stride").value);
  if (useSample) form.append("use_sample", "true");
  else form.append("file", state.file, state.file.name);

  resetProgressView();
  show("progress");
  setSteps("upload");
  if (useSample) {
    $("d-upload").textContent = "Не требуется: используется встроенный пример";
    $("p-status").textContent = "Запускаю обработку примера";
  } else {
    $("upload-bar").hidden = false;
    $("upload-bar").classList.add("running");
    $("d-upload").textContent = "Начинаю отправку…";
    setBar($("upload-bar"), 0);
  }

  try {
    const { job_id: jobId } = await uploadWithProgress(form);
    state.jobId = jobId;
    store(jobId);
    $("upload-bar").classList.remove("running");
    if (!useSample) {
      setBar($("upload-bar"), 100);
      $("d-upload").textContent = `Загружено: ${fmtMB(state.file.size)} МБ`;
    }
    setSteps("model");
    poll(jobId);
  } catch (err) {
    if (err.status === -1) return backToUpload("Загрузка отменена.");
    backToUpload();
    uploadError(err.message);
  }
}

function renderProgress(s) {
  const stepKey = STAGE_TO_STEP[s.stage] || "model";
  setSteps(stepKey);
  setBar($("main-bar"), s.percent);
  tween($("p-percent"), s.percent, (v) => `${Math.round(v)}%`);
  document.title = `${Math.round(s.percent)}% — Подсчёт автомобилей в видео`;

  if (!$("d-upload").textContent) $("d-upload").textContent = "Файл получен";
  const live = s.stage === "processing" || s.stage === "encoding" || s.stage === "finalizing";
  $("live").hidden = !live;

  if (s.stage === "done") $("p-status").textContent = "Готово, завершаю работу…";
  if (s.stage === "loading_model") {
    $("p-status").textContent = s.message || "Загружаю модель";
    $("d-model").textContent = `Загружаю YOLOv8n · прошло ${fmtTime(s.wall_sec)}. На бесплатном сервере это занимает время.`;
  } else {
    $("d-model").textContent = "Модель готова";
  }
  if (s.stage === "processing") {
    $("p-status").textContent = s.message;
    const memory = s.rss_mb ? ` · память процесса ${Math.round(s.rss_mb)} МБ` : "";
    $("d-frames").textContent = `Обработано ${nfInt.format(s.frames_processed)} из ~${nfInt.format(s.frames_expected)}${memory}`;
  } else if (live) {
    $("d-frames").textContent = `Обработано кадров: ${nfInt.format(s.frames_processed)}`;
  }
  if (s.stage === "encoding") {
    const share = Math.max(0, Math.min(100, ((s.percent - 90) / 8) * 100));
    $("p-status").textContent = "Собираю видео с рамками";
    $("d-build").textContent = `Кодирую видео: ${Math.round(share)}%`;
  } else if (s.stage === "finalizing") {
    $("p-status").textContent = "Записываю отчёт";
    $("d-build").textContent = "Записываю report.json";
  }

  if (live) {
    const skipped = Math.max(0, s.frames_read - s.frames_processed);
    tween($("s-frame"), s.frames_read);
    tween($("s-processed"), s.frames_processed);
    tween($("s-skipped"), skipped);
    tween($("s-last"), s.last_count);
    $("s-elapsed").textContent = fmtTime(s.elapsed_sec);
    $("s-eta").textContent = s.stage === "processing" && s.eta_sec != null ? `~${fmtTime(s.eta_sec)}` : "—";
    state.lastCounts = s.counts || [];
    drawBars($("spark"), state.lastCounts, [], false);
  }
}

async function poll(jobId) {
  const token = {};
  state.pollToken = token;
  let failures = 0;
  while (state.pollToken === token) {
    try {
      const s = await api(`/api/jobs/${jobId}`);
      if (state.pollToken !== token) return;
      failures = 0;
      if (s.status === "running") renderProgress(s);
      else return finish(s);
    } catch (err) {
      if (state.pollToken !== token) return;
      if (err.status === 404) return lost();
      failures += 1;
      $("p-status").textContent = "Сервер не отвечает, пробуем снова…";
    }
    await sleep(Math.min(POLL_MS + failures * 500, 4000));
  }
}

function lost() {
  backToUpload();
  uploadError("Задача потеряна (сервис перезапущен). Запустите обработку заново.");
}

function finish(s) {
  state.pollToken = null;
  $("main-bar").classList.remove("running");
  if (s.status === "done") {
    $("main-bar").classList.add("finished");
    setBar($("main-bar"), 100);
    setSteps("done");
    $("d-done").textContent = "Результаты готовы";
    state.isExample = false;
    renderResult(s);
    return;
  }
  if (s.status === "cancelled") return backToUpload("Обработка отменена.");
  renderProgress({ ...s, stage: s.stage === "done" ? "processing" : s.stage });
  showProgressError(s.error || "Обработка завершилась с ошибкой.");
}

async function cancel() {
  if (state.xhr) {
    state.xhr.abort();
    return;
  }
  $("btn-cancel").disabled = true;
  $("p-status").textContent = "Останавливаю обработку…";
  try {
    await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
  } catch (err) {
    if (err.status === 404) return lost();
  } finally {
    $("btn-cancel").disabled = false;
  }
}

// ---------- C. экран результатов ----------

function highlightJson(value) {
  const text = JSON.stringify(value, null, 2).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const token = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  return text.replace(token, (match, str, colon, bool, nul, num) => {
    if (str) return colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`;
    if (nul) return `<span class="z">${nul}</span>`;
    if (bool || num) return `<span class="n">${match}</span>`;
    return match;
  });
}

function metricTile({ label, value, sub, isNull, help }) {
  const tile = document.createElement("div");
  tile.className = `metric${isNull ? " null" : ""}`;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = value;
  if (sub) {
    const span = document.createElement("span");
    span.className = "sub";
    span.textContent = sub;
    dd.append(span);
  }
  tile.append(dt, dd);
  if (help) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "help";
    button.textContent = "?";
    button.setAttribute("aria-label", `Что значит ${label}`);
    button.setAttribute("aria-expanded", "false");
    const text = document.createElement("p");
    text.className = "help-text";
    text.hidden = true;
    text.textContent = help;
    button.addEventListener("click", () => {
      text.hidden = !text.hidden;
      button.setAttribute("aria-expanded", String(!text.hidden));
    });
    tile.append(button, text);
  }
  return tile;
}

function renderWhy(report, info) {
  const stride = report.stride;
  const fps = info.fps_source || 30;
  $("why-title").textContent = `Почему stride = ${stride}`;
  const window = nfDec.format(stride / fps);
  const body = $("why-body");
  body.replaceChildren();
  const cost = document.createElement("p");
  cost.textContent =
    stride === 1
      ? "Каждый кадр идёт в YOLO: это самый точный и самый дорогой режим, на слабом CPU он очень медленный."
      : `Стоимость почти пропорциональна числу кадров, которые идут в YOLO: при stride ${stride} их в ${stride} раз меньше, чем при обработке каждого кадра. На слабом CPU это решающее.`;
  const risk = document.createElement("p");
  risk.style.marginTop = "8px";
  risk.textContent =
    `Риск: событие короче ${window} с (${stride} кадров при ${nfDec.format(fps)} кадр/с) может оказаться между обработанными кадрами и не попасть в счёт. ` +
    "Машина обычно видна в кадре секундами, поэтому потерять её целиком маловероятно, но быстрые и мимолётные события stride пропустит.";
  body.append(cost, risk);
}

function renderResult(s) {
  const r = s.report;
  const info = s.run_info || {};
  const base = s.files_base;
  const skipped = r.frames_total - r.frames_processed;

  $("result-lead").textContent = state.isExample
    ? "Это готовый результат примера: ваша обработка не запускалась."
    : `Обработано ${nfInt.format(r.frames_processed)} из ${nfInt.format(r.frames_total)} кадров (каждый ${r.stride}-й) за ${fmtTime(r.elapsed_sec)}.`;

  const tiles = [
    { label: "Всего кадров", value: nfInt.format(r.frames_total) },
    { label: "Обработано", value: nfInt.format(r.frames_processed), sub: `каждый ${r.stride}-й кадр` },
    { label: "Пропущено", value: nfInt.format(skipped) },
    { label: "Stride", value: String(r.stride) },
    { label: "Среднее машин на кадр", value: nfDec.format(r.detections_per_frame_mean), sub: "по обработанным кадрам" },
    { label: "Максимум в кадре", value: nfInt.format(r.detections_per_frame_max) },
    { label: "Время обработки", value: fmtTime(r.elapsed_sec), sub: `${nfDec.format(r.elapsed_sec)} с, без загрузки модели` },
    {
      label: "unique_tracks",
      value: "null",
      sub: "не измерялось: трекера нет",
      isNull: true,
      help: "Трекера нет — число уникальных машин не определяется. null означает «не измерялось», а не «ноль машин».",
    },
  ];
  $("metrics").replaceChildren(...tiles.map(metricTile));

  state.lastCounts = s.counts || [];
  state.lastFrames = s.frame_numbers || [];
  $("chart-caption").textContent =
    `Максимум ${r.detections_per_frame_max}, среднее ${nfDec.format(r.detections_per_frame_mean)}. Это число машин в одном кадре, а не число разных машин.`;

  $("video-card").hidden = !s.has_video;
  if (s.has_video) $("result-video").src = `${base}annotated.mp4`;

  const gallery = $("gallery");
  gallery.replaceChildren();
  for (const name of s.frames || []) {
    const number = Number(name.replace(/\D/g, ""));
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-label", `Открыть кадр ${number}`);
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = `${base}frames/${name}`;
    img.alt = `Кадр ${number} с рамками`;
    button.append(img);
    button.addEventListener("click", () => {
      $("lightbox-img").src = img.src;
      $("lightbox-img").alt = img.alt;
      $("lightbox").showModal();
    });
    gallery.append(button);
  }
  $("gallery-card").hidden = !(s.frames || []).length;

  $("report-json").innerHTML = highlightJson(r);
  state.reportText = JSON.stringify(r, null, 2);
  const download = (name) => `${base}${name}?download=true`;
  $("dl-report").href = download("report.json");
  $("dl-report2").href = download("report.json");
  $("dl-info").href = download("run_info.json");
  $("dl-video").href = download("annotated.mp4");
  $("dl-video").hidden = !s.has_video;
  $("dl-zip").href = s.archive_url;

  renderWhy(r, info);
  store(null);
  show("result");
  drawBars($("chart"), state.lastCounts, state.lastFrames, true);
}

async function openExample() {
  try {
    const s = await api("/api/example");
    state.isExample = true;
    state.jobId = null;
    renderResult(s);
  } catch (err) {
    toast(err.message);
  }
}

async function copyReport() {
  try {
    await navigator.clipboard.writeText(state.reportText);
  } catch {
    const area = document.createElement("textarea");
    area.value = state.reportText;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast("report.json скопирован");
}

// ---------- запуск ----------

async function init() {
  try {
    state.cfg = await api("/api/config");
  } catch {
    state.cfg = {
      stride: { default: 5, min: 1, max: 30 },
      limits: { max_upload_mb: 25, max_duration_sec: 60, max_width: 1920 },
      extensions: [".mp4", ".mov", ".avi", ".mkv", ".webm"],
      sample_available: true,
      example_available: true,
      job_timeout_min: 45,
    };
    toast("Сервер ещё просыпается. Попробуйте через минуту.");
  }
  const { limits } = state.cfg;
  $("limits-note").textContent =
    `Видео до ${limits.max_duration_sec} с и ${limits.max_upload_mb} МБ. Бесплатный сервер медленный: обработка может занять несколько минут. Не закрывайте вкладку.`;
  $("btn-sample").hidden = state.cfg.sample_available === false;
  setStride(state.cfg.stride.default);
  initUpload();

  $("btn-cancel").addEventListener("click", cancel);
  $("btn-back").addEventListener("click", () => backToUpload());
  $("btn-again").addEventListener("click", () => {
    clearFile();
    backToUpload();
  });
  $("btn-copy").addEventListener("click", copyReport);
  $("lightbox").addEventListener("click", (e) => {
    if (e.target === $("lightbox")) $("lightbox").close();
  });

  const saved = stored();
  if (saved) {
    state.jobId = saved;
    resetProgressView();
    $("d-upload").textContent = "Файл получен";
    show("progress");
    setSteps("model");
    poll(saved);
  } else {
    show("upload");
  }
}

init();
