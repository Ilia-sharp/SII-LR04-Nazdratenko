"""FastAPI: тонкая обёртка над CLI (необязательная часть работы).

Запуск: uvicorn app.main:app --host 0.0.0.0 --port 10000
Эндпоинт /api/health отвечает мгновенно и не зависит от обработки.
"""

import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import jobs
from .jobs import manager

STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_CHUNK = 1024 * 1024
MULTIPART_OVERHEAD = 1024 * 1024  # запас на служебные поля формы при проверке Content-Length
NOT_FOUND_JOB = "Задача не найдена. Возможно, сервис перезапустился: запустите обработку заново."


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.purge_leftovers()
    yield


app = FastAPI(title="ЛР №4: подсчёт автомобилей в видео", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict:
    return {
        "class_name": jobs.CLASS_NAME,
        "class_label": jobs.CLASS_LABEL,
        "stride": {"default": jobs.DEFAULT_STRIDE, "min": jobs.MIN_STRIDE, "max": jobs.MAX_STRIDE, "step": jobs.STRIDE_STEP},
        "limits": {
            "max_upload_mb": jobs.limits.max_upload_mb,
            "max_duration_sec": jobs.limits.max_duration_sec,
            "max_width": jobs.limits.max_width,
        },
        "extensions": list(jobs.ALLOWED_EXTENSIONS),
        "job_timeout_min": jobs.limits.job_timeout_min,
        "busy": manager.is_busy(),
        "sample_available": jobs.sample_video().is_file(),
        "example_available": (jobs.example_dir() / "report.json").is_file(),
    }


def _job_or_404(job_id: str) -> jobs.Job:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, NOT_FOUND_JOB)
    return job


async def _save_upload(file: UploadFile, target: Path) -> None:
    """Записать загрузку на диск кусками, не держа файл в памяти; прервать при превышении лимита."""
    limit = jobs.limits.max_upload_mb * 1024 * 1024
    written = 0
    with open(target, "wb") as out:
        while chunk := await file.read(UPLOAD_CHUNK):
            written += len(chunk)
            if written > limit:
                raise HTTPException(413, f"Файл больше {jobs.limits.max_upload_mb} МБ. Сожмите или обрежьте видео.")
            out.write(chunk)
    if written == 0:
        raise HTTPException(400, "Файл пустой.")


@app.post("/api/jobs")
async def create_job(
    request: Request,
    file: UploadFile | None = File(None),
    stride: int = Form(jobs.DEFAULT_STRIDE),
    use_sample: bool = Form(False),
) -> dict[str, str]:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > jobs.limits.max_upload_mb * 1024 * 1024 + MULTIPART_OVERHEAD:
        raise HTTPException(413, f"Файл больше {jobs.limits.max_upload_mb} МБ. Сожмите или обрежьте видео.")
    if not 1 <= stride <= 10_000:
        raise HTTPException(400, "stride должен быть целым числом ≥ 1.")
    stride = jobs.snap_stride(stride)

    try:
        job_id, job_dir = manager.create_dir()
    except jobs.Busy as exc:
        raise HTTPException(409, str(exc)) from None

    started = False
    try:
        if use_sample:
            sample = jobs.sample_video()
            if not sample.is_file():
                raise HTTPException(404, "Встроенный пример недоступен на сервере. Загрузите свой файл.")
            input_path, label, delete_input = sample, "data/sample.mp4", False
        else:
            if file is None or not file.filename:
                raise HTTPException(400, "Выберите видеофайл.")
            ext = Path(file.filename).suffix.lower()
            if ext not in jobs.ALLOWED_EXTENSIONS:
                raise HTTPException(
                    400, f"Формат {ext or 'без расширения'} не поддерживается. Нужен mp4, mov, avi, mkv или webm."
                )
            input_path = job_dir / f"input{ext}"  # имя генерирует сервер, не пользователь
            await _save_upload(file, input_path)
            label = f"uploads/{Path(file.filename).name}"[:120]
            delete_input = True
            try:
                # Проверка идёт в отдельном процессе и в пуле потоков: цикл событий (health, опрос) не блокируется.
                meta = await run_in_threadpool(jobs.probe_video, input_path)
                jobs.check_limits(meta)
            except jobs.VideoRejected as exc:
                raise HTTPException(400, str(exc)) from None
        manager.start(job_id, job_dir, input_path, label, stride, delete_input)
        started = True
    finally:
        if not started:  # отказ, обрыв загрузки или отмена запроса: убрать папку и снять бронь
            shutil.rmtree(job_dir, ignore_errors=True)
            manager.release(job_id)
    return {"job_id": job_id}


@app.post("/api/jobs/cancel-all")
def cancel_all_jobs() -> dict[str, int]:
    """Остановить всё и удалить файлы всех задач."""
    return {"stopped": manager.cancel_all()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return manager.snapshot(_job_or_404(job_id))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, bool]:
    _job_or_404(job_id)
    return {"cancelled": manager.cancel(job_id)}


def _file_response(out_dir: Path, name: str, download: bool) -> FileResponse:
    path = jobs.safe_result_file(out_dir, name)
    if path is None:
        raise HTTPException(404, "Файл не найден.")
    return FileResponse(
        path, filename=path.name if download else None, content_disposition_type="attachment" if download else "inline"
    )


@app.get("/api/jobs/{job_id}/files/{name:path}")
def job_file(job_id: str, name: str, download: bool = False) -> FileResponse:
    return _file_response(_job_or_404(job_id).out_dir, name, download)


@app.get("/api/jobs/{job_id}/archive.zip")
def job_archive(job_id: str) -> FileResponse:
    job = _job_or_404(job_id)
    if not (job.out_dir / "report.json").is_file():
        raise HTTPException(404, "Результаты ещё не готовы.")
    archive = jobs.build_archive(job.out_dir, job.dir / "archive.zip")
    return FileResponse(archive, filename="results.zip", media_type="application/zip")


@app.get("/api/example")
def example() -> dict:
    out_dir = jobs.example_dir()
    if not (out_dir / "report.json").is_file():
        raise HTTPException(404, "Готового примера пока нет. Запустите обработку на своём видео.")
    payload = manager.result_payload(out_dir, "/api/example")
    return {"id": "example", "status": "done", "stage": "done", "percent": 100.0, "error": None,
            "stride": (payload["report"] or {}).get("stride"), "source_label": (payload["report"] or {}).get("source"),
            **payload}  # fmt: skip


@app.get("/api/example/files/{name:path}")
def example_file(name: str, download: bool = False) -> FileResponse:
    return _file_response(jobs.example_dir(), name, download)


@app.get("/api/example/archive.zip")
def example_archive() -> FileResponse:
    out_dir = jobs.example_dir()
    if not (out_dir / "report.json").is_file():
        raise HTTPException(404, "Готового примера пока нет.")
    archive = jobs.build_archive(out_dir, jobs.jobs_root() / "example.zip")
    return FileResponse(archive, filename="example_results.zip", media_type="application/zip")


# Статика отдаётся последней: API-маршруты выше имеют приоритет.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
