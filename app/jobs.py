"""Задачи обработки: запуск CLI подпроцессом, чтение прогресса, отмена, таймаут, TTL.

Модуль не импортирует torch, ultralytics и cv2: веб-процесс остаётся лёгким, а обработка
(и проверка видео) идёт в отдельных процессах. Если их убьёт нехватка памяти, сервер жив.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Параметры варианта 16 (менять нельзя) и границы слайдера.
CLASS_NAME = "car"
CLASS_LABEL = "автомобили"
DEFAULT_STRIDE = 5
MIN_STRIDE = 1
MAX_STRIDE = 30

ALLOWED_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm")
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
# Что можно отдавать по сети из папки результатов: ничего, кроме этого списка.
FILE_NAME_RE = re.compile(r"^(report\.json|run_info\.json|run\.log|annotated\.mp4|frames/frame_\d{6}\.jpg)$")

TTL_AFTER_FINISH_SEC = 30 * 60
KILL_GRACE_SEC = 5.0
PROBE_TIMEOUT_SEC = 120
EXIT_OOM_CODES = (-9, 137)  # SIGKILL от OOM-killer
EXIT_CANCELLED = 130


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value) if value.isdigit() and int(value) > 0 else default


class Limits:
    """Лимиты загрузки и обработки; читаются из переменных окружения при каждом обращении."""

    @property
    def max_upload_mb(self) -> int:
        return _env_int("MAX_UPLOAD_MB", 25)

    @property
    def max_duration_sec(self) -> int:
        return _env_int("MAX_DURATION_SEC", 60)

    @property
    def max_width(self) -> int:
        return _env_int("MAX_WIDTH", 1920)

    @property
    def job_timeout_min(self) -> int:
        return _env_int("JOB_TIMEOUT_MIN", 45)


limits = Limits()


def jobs_root() -> Path:
    """Папка с задачами (эфемерная файловая система: /tmp/jobs)."""
    path = Path(os.environ.get("JOBS_DIR") or Path(tempfile.gettempdir()) / "jobs")
    path.mkdir(parents=True, exist_ok=True)
    return path


def sample_video() -> Path:
    """Встроенный учебный пример."""
    return Path(os.environ.get("SAMPLE_VIDEO") or ROOT / "data" / "sample.mp4")


def example_dir() -> Path:
    """Готовый результат запуска на примере (коммитится в репозиторий)."""
    return Path(os.environ.get("EXAMPLE_DIR") or ROOT / "examples" / "sample")


class Busy(Exception):
    """Уже идёт другая обработка."""


class VideoRejected(Exception):
    """Видео не прошло проверку лимитов; текст сообщения показывается пользователю."""


@dataclass
class Job:
    """Одна обработка. Состояние хранится только в памяти процесса."""

    id: str
    dir: Path
    stride: int
    source_label: str
    input_path: Path
    delete_input: bool
    proc: subprocess.Popen | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    returncode: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    timer: threading.Timer | None = None

    @property
    def out_dir(self) -> Path:
        return self.dir / "out"

    @property
    def progress_path(self) -> Path:
        return self.dir / "progress.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _kill_tree(proc: subprocess.Popen) -> None:
    """Жёстко завершить процесс вместе с дочерним ffmpeg."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


def probe_video(path: Path) -> dict[str, Any]:
    """Прочитать метаданные видео отдельным процессом (cv2 не попадает в веб-процесс)."""
    code = (
        "import json,sys\n"
        "from src.pipeline import probe_video\n"
        "i = probe_video(sys.argv[1])\n"
        "print(json.dumps({'fps': i.fps, 'width': i.width, 'height': i.height, 'frames': i.frame_count_est}))\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, str(path)],
            cwd=ROOT, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise VideoRejected("Не удалось вовремя прочитать видео. Попробуйте другой файл.") from None
    if done.returncode != 0:
        raise VideoRejected(
            "Не удалось прочитать видео. Проверьте, что файл не повреждён и это mp4, mov, avi, mkv или webm."
        )
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        raise VideoRejected("Не удалось прочитать метаданные видео.") from None


def check_limits(meta: dict[str, Any]) -> None:
    """Проверить длительность и ширину до запуска обработки."""
    if meta["width"] > limits.max_width:
        raise VideoRejected(
            f"Ширина кадра {meta['width']} px больше допустимых {limits.max_width} px. Уменьшите разрешение."
        )
    if meta["frames"] and meta["fps"]:
        duration = meta["frames"] / meta["fps"]
        if duration > limits.max_duration_sec + 0.5:
            raise VideoRejected(
                f"Видео длится {duration:.0f} с, допустимо до {limits.max_duration_sec} с. Обрежьте ролик."
            )


class JobManager:
    """Одна активная задача за раз; завершённые хранятся 30 минут."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.reserved: str | None = None  # место занято на время загрузки файла, до запуска процесса

    # ---- запуск и управление -------------------------------------------------

    def purge_leftovers(self) -> None:
        """При старте сервиса убрать папки задач, оставшиеся от прошлого запуска."""
        for child in jobs_root().iterdir():
            if child.is_dir() and JOB_ID_RE.match(child.name):
                shutil.rmtree(child, ignore_errors=True)

    def _running_locked(self) -> Job | None:
        """Задача с живым процессом (вызывать под self.lock)."""
        for job in self.jobs.values():
            if job.proc is not None and job.returncode is None:
                return job
        return None

    def active(self) -> Job | None:
        with self.lock:
            return self._running_locked()

    def is_busy(self) -> bool:
        """Идёт обработка или кто-то прямо сейчас загружает файл."""
        with self.lock:
            return self.reserved is not None or self._running_locked() is not None

    def create_dir(self) -> tuple[str, Path]:
        """Занять место под новую задачу (или отказать, если уже идёт другая).

        Проверка и бронь делаются под одним замком: два одновременных запроса не запустят две обработки.
        """
        self.cleanup()
        with self.lock:
            if self.reserved is not None or self._running_locked() is not None:
                raise Busy("Сейчас идёт другая обработка. Дождитесь её окончания или отмените.")
            job_id = uuid.uuid4().hex[:12]
            self.reserved = job_id
        job_dir = jobs_root() / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.release(job_id)
            raise
        return job_id, job_dir

    def release(self, job_id: str) -> None:
        """Снять бронь (загрузка не удалась или отменена)."""
        with self.lock:
            if self.reserved == job_id:
                self.reserved = None

    def start(
        self, job_id: str, job_dir: Path, input_path: Path, source_label: str, stride: int, delete_input: bool
    ) -> Job:
        """Запустить CLI подпроцессом: аргументы списком, без shell."""
        stride = max(MIN_STRIDE, min(MAX_STRIDE, int(stride)))
        job = Job(job_id, job_dir, stride, source_label, input_path, delete_input)
        cmd = [
            sys.executable, "-m", "src.count_video",
            "--source", str(input_path),
            "--class", CLASS_NAME,
            "--stride", str(stride),
            "--out", str(job.out_dir),
            "--progress-json", str(job.progress_path),
            "--source-label", source_label,
            "--quiet",
        ]  # fmt: skip
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
        env.setdefault("MALLOC_ARENA_MAX", "2")
        log = open(job_dir / "stdout.log", "wb")  # noqa: SIM115 - закрывается в _watch
        with self.lock:
            self.jobs[job_id] = job
            self.reserved = None  # дальше «занято» определяется живым процессом
            job.proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=hasattr(os, "setsid"),
            )  # fmt: skip
        job.timer = threading.Timer(limits.job_timeout_min * 60, self._on_timeout, args=(job,))
        job.timer.daemon = True
        job.timer.start()
        threading.Thread(target=self._watch, args=(job, log), daemon=True).start()
        return job

    def _watch(self, job: Job, log) -> None:
        """Дождаться конца процесса, зафиксировать код и убрать загруженный исходник."""
        assert job.proc is not None
        code = job.proc.wait()
        log.close()
        if job.timer is not None:
            job.timer.cancel()
        if job.delete_input:
            job.input_path.unlink(missing_ok=True)
        with self.lock:
            job.returncode = code
            job.finished_at = time.time()

    def _on_timeout(self, job: Job) -> None:
        if job.proc is not None and job.proc.poll() is None:
            job.timed_out = True
            job.proc.terminate()
            threading.Timer(KILL_GRACE_SEC, _kill_tree, args=(job.proc,)).start()

    def cancel(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.proc is None or job.proc.poll() is not None:
            return False
        job.cancelled = True
        job.proc.terminate()  # SIGTERM: CLI сам закроет файлы и запишет «cancelled»
        threading.Timer(KILL_GRACE_SEC, _kill_tree, args=(job.proc,)).start()
        return True

    def get(self, job_id: str) -> Job | None:
        if not JOB_ID_RE.match(job_id):
            return None
        return self.jobs.get(job_id)

    def cleanup(self) -> None:
        """Удалить задачи, завершённые более 30 минут назад."""
        now = time.time()
        with self.lock:
            stale = [j for j in self.jobs.values() if j.finished_at and now - j.finished_at > TTL_AFTER_FINISH_SEC]
            for job in stale:
                del self.jobs[job.id]
        for job in stale:
            shutil.rmtree(job.dir, ignore_errors=True)

    # ---- состояние -----------------------------------------------------------

    def snapshot(self, job: Job) -> dict[str, Any]:
        """Собрать состояние для API: файл прогресса + статус процесса."""
        self.cleanup()
        progress = _read_json(job.progress_path) or {}
        code = job.returncode
        status, error = "running", None
        if code is not None:
            status, error = self._finished_status(job, code, progress)
        state: dict[str, Any] = {
            "id": job.id,
            "status": status,
            "error": error,
            "stride": job.stride,
            "source_label": job.source_label,
            "stage": progress.get("stage", "loading_model"),
            "percent": progress.get("percent", 0.0),
            "frames_read": progress.get("frames_read", 0),
            "frames_total_est": progress.get("frames_total_est", 0),
            "frames_processed": progress.get("frames_processed", 0),
            "frames_expected": progress.get("frames_expected", 0),
            "last_count": progress.get("last_count", 0),
            "elapsed_sec": progress.get("elapsed_sec", 0.0),
            "eta_sec": progress.get("eta_sec"),
            "rss_mb": progress.get("rss_mb"),
            "message": progress.get("message") or "Запускаю обработку",
            "counts": progress.get("counts", []),
            "wall_sec": round((job.finished_at or time.time()) - job.started_at, 1),
        }
        if status == "done":
            state.update(stage="done", percent=100.0, **self.result_payload(job.out_dir, f"/api/jobs/{job.id}"))
        return state

    @staticmethod
    def _finished_status(job: Job, code: int, progress: dict[str, Any]) -> tuple[str, str | None]:
        if code == 0 and (job.out_dir / "report.json").is_file():
            return "done", None
        if job.timed_out:
            return "error", f"Превышено время обработки ({limits.job_timeout_min} мин). Попробуйте видео короче."
        if job.cancelled or code == EXIT_CANCELLED or progress.get("stage") == "cancelled":
            return "cancelled", "Обработка отменена."
        if code in EXIT_OOM_CODES:
            return "error", "Не хватило памяти на бесплатном тарифе. Попробуйте видео короче или меньшего разрешения."
        message = progress.get("message") if progress.get("stage") == "error" else None
        return (
            "error",
            message or f"Обработка завершилась с ошибкой (код {code}). Подробности смотрите в журнале сервера.",
        )

    @staticmethod
    def result_payload(out_dir: Path, base_url: str) -> dict[str, Any]:
        """Готовые результаты: отчёт, сведения о запуске, список кадров и ссылки."""
        report = _read_json(out_dir / "report.json")
        info = _read_json(out_dir / "run_info.json") or {}
        per_frame = info.pop("per_frame", [])
        frames = sorted(p.name for p in (out_dir / "frames").glob("frame_*.jpg"))
        return {
            "report": report,
            "run_info": info,
            "counts": [item["count"] for item in per_frame],
            "frame_numbers": [item["frame"] for item in per_frame],
            "frames": frames,
            "has_video": (out_dir / "annotated.mp4").is_file(),
            "files_base": f"{base_url}/files/",
            "archive_url": f"{base_url}/archive.zip",
        }


def safe_result_file(out_dir: Path, name: str) -> Path | None:
    """Путь к файлу результата, если имя разрешено списком и файл лежит внутри out_dir."""
    if not FILE_NAME_RE.match(name):
        return None
    path = (out_dir / name).resolve()
    if out_dir.resolve() not in path.parents or not path.is_file():
        return None
    return path


def build_archive(out_dir: Path, target: Path) -> Path:
    """Собрать zip со всеми результатами во временный файл (не в память)."""
    names = ["report.json", "run_info.json", "run.log", "annotated.mp4"]
    names += [f"frames/{p.name}" for p in sorted((out_dir / "frames").glob("frame_*.jpg"))]
    tmp = target.with_name(target.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            path = out_dir / name
            if path.is_file():
                archive.write(path, name)
    os.replace(tmp, target)
    return target


manager = JobManager()
