"""Ядро: отбор кадров → детекция → агрегаты.

Детектор приходит снаружи (объект с методом detect(frame) -> list[Box]),
поэтому логику проверяют без torch на подставном детекторе.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2

from .detector import Box, Detector
from .report import ProgressWriter, rss_mb
from .viz import AnnotatedVideo, annotate, save_jpeg, shrink

log = logging.getLogger("lr04")

ASSUMED_FPS = 25.0  # если в метаданных видео fps не указан или бессмыслен
MAX_SANE_FPS = 240.0
EMA_ALPHA = 0.2  # вес нового замера во времени на кадр (для ETA)
LOG_EVERY_MAX = 20  # логировать не реже, чем каждые 20 обработанных кадров

# Шкала процентов для плавной полосы прогресса.
PCT_MODEL_DONE = 5.0
PCT_PROCESSING_END = 90.0
PCT_ENCODING_END = 98.0


class VideoError(Exception):
    """Видео не найдено, не открывается или в нём нет кадров."""


@dataclass(frozen=True)
class VideoInfo:
    """Метаданные видео, прочитанные до обработки."""

    fps: float
    fps_assumed: bool
    width: int
    height: int
    frame_count_est: int  # оценка из контейнера: только для прогресса, не для отчёта


@dataclass
class RunResult:
    """Итог обработки одного видео."""

    frames_total: int
    counts: list[int]
    per_frame: list[dict[str, int]]
    elapsed_sec: float
    info: VideoInfo
    ffmpeg_used: bool
    video_written: bool
    saved_frames: list[str] = field(default_factory=list)
    peak_rss_mb: float | None = None

    @property
    def frames_processed(self) -> int:
        return len(self.counts)


def probe_video(source: str | Path) -> VideoInfo:
    """Открыть видео, прочитать метаданные и убедиться, что первый кадр читается."""
    path = Path(source)
    if not path.is_file():
        raise VideoError(f"Файл не найден: {source}")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise VideoError(f"Не удалось открыть видео: {source}")
        ok, frame = cap.read()
        if not ok or frame is None:
            raise VideoError(f"В видео нет читаемых кадров: {source}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()
    fps_assumed = not (0.0 < fps <= MAX_SANE_FPS) or math.isnan(fps)
    height, width = frame.shape[:2]
    return VideoInfo(ASSUMED_FPS if fps_assumed else fps, fps_assumed, width, height, max(count, 0))


def _uniform_ordinals(expected: int, how_many: int) -> set[int]:
    """Порядковые номера обработанных кадров, равномерно рассыпанные по видео."""
    if how_many <= 0 or expected <= 0:
        return set()
    if how_many == 1:
        return {0}
    last = expected - 1
    return {round(i * last / (how_many - 1)) for i in range(how_many)}


def run(
    source: str | Path,
    detector: Detector,
    class_name: str,
    stride: int,
    out_dir: Path,
    *,
    info: VideoInfo | None = None,
    save_frames: int = 8,
    write_video: bool = True,
    progress: ProgressWriter | None = None,
) -> RunResult:
    """Обработать каждый stride-й кадр: кадры 0, N, 2N, ... идут в YOLO, остальные пропускаются."""
    if stride < 1:
        raise ValueError("stride должен быть целым числом ≥ 1")
    progress = progress or ProgressWriter()
    info = info or probe_video(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    for old in frames_dir.glob("frame_*.jpg"):  # повторный запуск не должен смешиваться со старым
        old.unlink()

    est_total = info.frame_count_est
    expected = math.ceil(est_total / stride) if est_total else 0
    # Из лимита кадров один слот отдаём кадру с максимумом машин, остальные - равномерно.
    uniform = _uniform_ordinals(expected, save_frames - 1 if save_frames > 1 else 0)
    video = AnnotatedVideo(out_dir / "annotated.mp4", info.fps / stride) if write_video else None

    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise VideoError(f"Не удалось открыть видео: {source}")

    counts: list[int] = []
    per_frame: list[dict[str, int]] = []
    saved: dict[int, str] = {}
    best: tuple[int, int, Any] = (-1, -1, None)  # (число объектов, номер кадра, копия кадра)
    log_every = max(1, min(LOG_EVERY_MAX, expected // 10 or 1))
    peak_sample = 0.0
    ema: float | None = None

    t0 = time.perf_counter()
    mark = t0
    index = 0
    try:
        while True:
            if index % stride == 0:
                ok, frame = cap.read()  # декодируем и получаем массив
                if not ok:
                    break
                boxes: list[Box] = detector.detect(frame)  # только выбранный класс
                n = len(boxes)
                counts.append(n)
                per_frame.append({"frame": index, "count": n})
                annotate(frame, boxes, class_name, index)
                if video is not None:
                    video.write(frame)
                ordinal = len(counts) - 1
                if ordinal in uniform:
                    name = f"frame_{index:06d}.jpg"
                    save_jpeg(frame, frames_dir / name)
                    saved[index] = name
                if save_frames > 0 and n > best[0]:
                    best = (n, index, shrink(frame).copy())

                now = time.perf_counter()
                step = now - mark
                mark = now
                ema = step if ema is None else EMA_ALPHA * step + (1 - EMA_ALPHA) * ema
                processed = len(counts)
                est = max(est_total, index + 1)
                share = min((index + 1) / est, 1.0)
                exp_now = max(expected, processed)
                eta = ema * max(exp_now - processed, 0)
                rss = rss_mb()
                peak_sample = max(peak_sample, rss or 0.0)
                progress.update(
                    "processing",
                    PCT_MODEL_DONE + (PCT_PROCESSING_END - PCT_MODEL_DONE) * share,
                    frames_read=index + 1,
                    frames_total_est=est,
                    frames_processed=processed,
                    frames_expected=exp_now,
                    last_count=n,
                    elapsed_sec=round(now - t0, 1),
                    eta_sec=round(eta, 1),
                    rss_mb=None if rss is None else round(rss, 1),
                    message=f"Обработано {processed} из ~{exp_now} кадров",
                    counts=counts,
                )
                if processed % log_every == 0:
                    log.info(
                        "обработано %d / ~%d | пропущено %d | в кадре %s: %d | прошло %.1f с | ETA %.0f с",
                        processed,
                        exp_now,
                        index + 1 - processed,
                        class_name,
                        n,
                        now - t0,
                        eta,
                        extra={"periodic": True},
                    )
            else:
                if not cap.grab():  # только продвинуться, без передачи в YOLO
                    break
            index += 1
    except BaseException:
        if video is not None:
            video.discard()  # прервали (Ctrl+C, SIGTERM, ошибка): недописанное видео не оставляем
        raise
    finally:
        cap.release()
        if video is not None:
            video.close()

    frames_total = index  # реальное число пройденных кадров, а не метаданные контейнера
    if not counts:
        raise VideoError(f"В видео нет читаемых кадров: {source}")
    assert len(counts) == math.ceil(frames_total / stride), "нарушен инвариант отбора кадров"

    progress.update(
        "encoding",
        PCT_PROCESSING_END,
        force=True,
        message="Собираю видео с рамками",
        frames_read=frames_total,
        frames_total_est=frames_total,
        frames_processed=len(counts),
        frames_expected=len(counts),
        elapsed_sec=round(time.perf_counter() - t0, 1),
        eta_sec=None,
        counts=counts,
    )

    def on_encode(fraction: float) -> None:
        pct = PCT_PROCESSING_END + (PCT_ENCODING_END - PCT_PROCESSING_END) * fraction
        progress.update("encoding", pct, elapsed_sec=round(time.perf_counter() - t0, 1))

    ffmpeg_used = False
    video_written = False
    if video is not None:
        ffmpeg_used = video.finalize(on_encode)
        video_written = video.available

    if save_frames > 0 and best[1] not in saved and best[2] is not None:
        name = f"frame_{best[1]:06d}.jpg"
        save_jpeg(best[2], frames_dir / name)
        saved[best[1]] = name

    elapsed = time.perf_counter() - t0
    return RunResult(
        frames_total=frames_total,
        counts=counts,
        per_frame=per_frame,
        elapsed_sec=elapsed,
        info=info,
        ffmpeg_used=ffmpeg_used,
        video_written=video_written,
        saved_frames=[saved[k] for k in sorted(saved)],
        peak_rss_mb=peak_sample or None,
    )
