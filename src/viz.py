"""Визуализация: рамки, подписи, сохранение кадров и видео с рамками."""

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from .detector import Box

log = logging.getLogger("lr04")

ACCENT_BGR = (255, 140, 91)  # акцент #5B8CFF в порядке каналов BGR
TEXT_BGR = (255, 255, 255)
BOX_THICKNESS = 2
PLATE_ALPHA = 0.65  # непрозрачность плашки под подписью
MAX_OUTPUT_WIDTH = 960  # и для видео, и для JPEG
JPEG_QUALITY = 80
MIN_VIDEO_FPS = 1.0  # слишком малая частота кадров ломает некоторые кодеки
FFMPEG_ARGS = [
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-preset",
    "veryfast",
    "-crf",
    "28",
    "-movflags",
    "+faststart",
]


def _font_scale(width: int) -> float:
    """Масштаб шрифта от ширины кадра, чтобы подписи читались и на HD, и на малых кадрах."""
    return max(0.45, width / 1600)


def _draw_label(img: np.ndarray, text: str, x: int, y: int, scale: float) -> None:
    """Подпись белым цветом на полупрозрачной плашке цвета акцента (вверх от точки y)."""
    thickness = 1 if scale < 0.8 else 2
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    height, width = img.shape[:2]
    x0 = max(0, min(x, width - tw - 8))
    y1 = min(max(y, th + base + 4), height)
    y0 = y1 - th - base - 4
    x1 = min(x0 + tw + 8, width)
    roi = img[y0:y1, x0:x1]
    plate = np.empty_like(roi)
    plate[:] = ACCENT_BGR
    cv2.addWeighted(plate, PLATE_ALPHA, roi, 1 - PLATE_ALPHA, 0, dst=roi)
    cv2.putText(img, text, (x0 + 4, y1 - base - 2), cv2.FONT_HERSHEY_SIMPLEX, scale, TEXT_BGR, thickness, cv2.LINE_AA)


def annotate(frame: np.ndarray, boxes: list[Box], class_name: str, frame_index: int) -> np.ndarray:
    """Нарисовать рамки и подпись `car: N | frame i` прямо на кадре."""
    scale = _font_scale(frame.shape[1])
    for box in boxes:
        p1 = (int(box.x1), int(box.y1))
        p2 = (int(box.x2), int(box.y2))
        cv2.rectangle(frame, p1, p2, ACCENT_BGR, BOX_THICKNESS, cv2.LINE_AA)
        _draw_label(frame, f"{class_name} {box.conf:.2f}", p1[0], p1[1], scale * 0.8)
    caption = f"{class_name}: {len(boxes)} | frame {frame_index}"
    # Подпись кадра - в левом верхнем углу (y меньше высоты плашки: она прижмётся к верху).
    _draw_label(frame, caption, 0, 0, scale)
    return frame


def fit_size(width: int, height: int, max_width: int = MAX_OUTPUT_WIDTH) -> tuple[int, int]:
    """Размер не шире max_width с сохранением пропорций; стороны чётные (требование кодека)."""
    out_w = min(width, max_width)
    out_h = round(height * out_w / width)
    return max(2, out_w - out_w % 2), max(2, out_h - out_h % 2)


def shrink(frame: np.ndarray, max_width: int = MAX_OUTPUT_WIDTH) -> np.ndarray:
    """Уменьшить кадр до ширины не более max_width (копия, если размер изменился)."""
    height, width = frame.shape[:2]
    out_w, out_h = fit_size(width, height, max_width)
    if (out_w, out_h) == (width, height):
        return frame
    return cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)


def save_jpeg(frame: np.ndarray, path: Path) -> None:
    """Сохранить кадр в JPEG (уменьшенный до MAX_OUTPUT_WIDTH)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), shrink(frame), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise OSError(f"Не удалось записать кадр: {path}")


class AnnotatedVideo:
    """Видео с рамками: только обработанные кадры, частота fps/stride.

    Сначала пишется mp4v через OpenCV, затем ffmpeg перекодирует в H.264, который
    проигрывается в браузерах. Без ffmpeg остаётся mp4v с предупреждением.
    """

    def __init__(self, path: Path, fps: float) -> None:
        self.path = path
        self.raw_path = path.with_name(path.stem + "_raw.mp4")
        self.fps = max(fps, MIN_VIDEO_FPS)
        self.frames_written = 0
        self.ffmpeg_used = False
        self._writer: cv2.VideoWriter | None = None
        self._disabled = False

    def write(self, frame: np.ndarray) -> None:
        """Добавить кадр (с уже нарисованными рамками)."""
        if self._disabled:
            return
        small = shrink(frame)
        if self._writer is None:
            self._open(small.shape[1], small.shape[0])
            if self._disabled:
                return
        assert self._writer is not None
        self._writer.write(small)
        self.frames_written += 1

    def _open(self, width: int, height: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter.fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(self.raw_path), fourcc, self.fps, (width, height))
        if not writer.isOpened():
            log.warning("Не удалось создать видео (кодек mp4v недоступен): видео пропущено")
            self._disabled = True
            return
        self._writer = writer

    @property
    def available(self) -> bool:
        """Было ли что-то записано."""
        return self.frames_written > 0

    def close(self) -> None:
        """Закрыть файл OpenCV (нужно перед перекодированием)."""
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def discard(self) -> None:
        """Закрыть и удалить недописанные файлы (при отмене обработки)."""
        self.close()
        self.raw_path.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)

    def finalize(self, on_progress: Callable[[float], None] | None = None) -> bool:
        """Перекодировать в H.264. Вернуть True, если ffmpeg использован."""
        self.close()
        if not self.available:
            self.raw_path.unlink(missing_ok=True)
            return False
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg and self._encode(ffmpeg, on_progress):
            self.raw_path.unlink(missing_ok=True)
            self.ffmpeg_used = True
            return True
        log.warning(
            "ffmpeg недоступен или завершился с ошибкой: видео остаётся в mp4v и может не воспроизводиться в браузере"
        )
        os.replace(self.raw_path, self.path)
        return False

    def _encode(self, ffmpeg: str, on_progress: Callable[[float], None] | None) -> bool:
        """Запустить ffmpeg и читать его прогресс (out_time_us) из stdout."""
        cmd = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-nostats",
            "-progress",
            "pipe:1",
            "-i",
            str(self.raw_path),
            *FFMPEG_ARGS,
            str(self.path),
        ]
        total_us = max(self.frames_written / self.fps, 0.001) * 1_000_000
        tail: list[str] = []
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        except OSError as exc:
            log.warning("Не удалось запустить ffmpeg: %s", exc)
            return False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_us=") and on_progress:
                    try:
                        on_progress(min(int(line.split("=", 1)[1]) / total_us, 1.0))
                    except ValueError:
                        pass  # ffmpeg пишет N/A до первого кадра
                elif "=" not in line:
                    tail.append(line)
            code = proc.wait()
        finally:
            if proc.poll() is None:  # прервали Ctrl+C: не оставляем ffmpeg-сироту
                proc.kill()
        if code != 0:
            log.warning("ffmpeg вернул код %s: %s", code, " ".join(tail[-3:]))
            self.path.unlink(missing_ok=True)
            return False
        if on_progress:
            on_progress(1.0)
        return True
