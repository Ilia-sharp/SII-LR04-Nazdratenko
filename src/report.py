"""Сборка и запись report.json, run_info.json и файла прогресса для веба."""

import json
import os
import sys
import time
from importlib import metadata
from pathlib import Path
from typing import Any

MODEL_VERSION = "v1.0"  # как в образце ТЗ и в теге релиза; реальные веса - в run_info.json

# Ровно эти 10 ключей и именно в таком порядке требует ТЗ.
REPORT_KEYS = (
    "source",
    "class_name",
    "stride",
    "frames_total",
    "frames_processed",
    "detections_per_frame_mean",
    "detections_per_frame_max",
    "unique_tracks",
    "model_version",
    "elapsed_sec",
)

TRACKER_NOTE = (
    "Трекер не используется, поэтому unique_tracks = null: это «не измерялось», а не «ноль». "
    "Среднее и максимум считаются по отдельным кадрам; одна и та же машина на разных "
    "обработанных кадрах учитывается каждый раз заново."
)

PROGRESS_MIN_INTERVAL_SEC = 0.25  # не чаще 4 записей в секунду
PERCENT_BEFORE_DONE = 99.0


def write_json_atomic(path: Path, data: Any) -> None:
    """Записать JSON через временный файл и os.replace: читатель не увидит половину файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def build_report(
    source: str,
    class_name: str,
    stride: int,
    frames_total: int,
    counts: list[int],
    elapsed_sec: float,
) -> dict[str, Any]:
    """Собрать report.json по формату ТЗ. Среднее и максимум - по обработанным кадрам."""
    if not counts:
        raise ValueError("Нет обработанных кадров: отчёт построить нельзя")
    report = {
        "source": source.replace("\\", "/"),
        "class_name": class_name,
        "stride": stride,
        "frames_total": frames_total,
        "frames_processed": len(counts),
        "detections_per_frame_mean": round(sum(counts) / len(counts), 2),
        "detections_per_frame_max": int(max(counts)),
        "unique_tracks": None,  # трекера нет
        "model_version": MODEL_VERSION,
        "elapsed_sec": round(elapsed_sec, 2),
    }
    assert tuple(report) == REPORT_KEYS
    return report


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def build_run_info(
    *,
    weights: str,
    class_id: int,
    imgsz: int,
    conf: float,
    fps_source: float,
    fps_assumed: bool,
    width: int,
    height: int,
    frames_total: int,
    frames_processed: int,
    model_load_sec: float,
    peak_rss_mb: float | None,
    ffmpeg_used: bool,
    per_frame: list[dict[str, int]],
) -> dict[str, Any]:
    """Всё, что не вошло в report.json (формат отчёта менять нельзя)."""
    return {
        "weights": weights,
        "ultralytics_version": _package_version("ultralytics"),
        "torch_version": _package_version("torch"),
        "python_version": sys.version.split()[0],
        "imgsz": imgsz,
        "conf": conf,
        "fps_source": round(fps_source, 3),
        "fps_assumed": fps_assumed,
        "width": width,
        "height": height,
        "frames_skipped": frames_total - frames_processed,
        "model_load_sec": round(model_load_sec, 2),
        "peak_rss_mb": None if peak_rss_mb is None else round(peak_rss_mb, 1),
        "ffmpeg_used": ffmpeg_used,
        "class_id": class_id,
        "tracker": "none",
        "tracker_note": TRACKER_NOTE,
        "per_frame": per_frame,
    }


def rss_mb() -> float | None:
    """Текущая резидентная память процесса, МБ (нужен psutil, иначе None)."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process().memory_info().rss / 1024 / 1024


def peak_rss_mb(sampled_peak: float | None = None) -> float | None:
    """Пик RSS процесса: максимум из отсчётов и системного счётчика (если он есть)."""
    candidates = [sampled_peak] if sampled_peak is not None else []
    try:
        import resource

        used = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux отдаёт КБ, macOS - байты.
        candidates.append(used / 1024 if sys.platform.startswith("linux") else used / 1024 / 1024)
    except ImportError:
        pass  # Windows: остаются только отсчёты psutil
    return max(candidates) if candidates else None


class ProgressWriter:
    """Пишет прогресс в JSON-файл для веб-интерфейса (атомарно и не чаще 4 раз в секунду).

    Без пути (path=None) ничего не пишет, но состояние хранит: им пользуются и тесты.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.state: dict[str, Any] = {
            "stage": "loading_model",
            "percent": 0.0,
            "frames_read": 0,
            "frames_total_est": 0,
            "frames_processed": 0,
            "frames_expected": 0,
            "last_count": 0,
            "elapsed_sec": 0.0,
            "eta_sec": None,
            "rss_mb": None,
            "message": "",
            "counts": [],
        }
        self._last_write = 0.0

    def update(self, stage: str, percent: float, *, force: bool = False, **fields: Any) -> None:
        """Обновить состояние. Процент не убывает и не доходит до 100 до этапа done."""
        state = self.state
        if stage == "done":
            percent = 100.0
        elif stage not in ("error", "cancelled"):
            percent = min(percent, PERCENT_BEFORE_DONE)
            percent = max(percent, state["percent"])
        else:
            percent = state["percent"]
        state.update(fields)
        state["stage"] = stage
        state["percent"] = round(percent, 1)
        now = time.monotonic()
        if self.path is None:
            return
        if not force and now - self._last_write < PROGRESS_MIN_INTERVAL_SEC:
            return
        self._last_write = now
        try:
            write_json_atomic(self.path, state)
        except OSError:
            pass  # читатель держит файл (Windows): запишем в следующий раз
