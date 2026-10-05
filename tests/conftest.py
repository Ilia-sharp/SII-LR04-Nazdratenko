"""Общие помощники тестов: крошечное видео и подставной детектор (без torch)."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from src.detector import Box

LEVEL_STEP = 25  # шаг яркости: JPEG-сжатие сдвигает уровни на единицы, а не на десятки
RADIX = 10  # номер кадра = (десятки, единицы), каждая цифра - яркость своей половины кадра


def make_video(path: Path, n_frames: int, fps: float = 10.0, size: tuple[int, int] = (64, 48)) -> Path:
    """Записать видео, где номер кадра (до 99) закодирован яркостью двух половин кадра."""
    assert n_frames <= RADIX * RADIX
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), fps, size)
    assert writer.isOpened(), "в этой сборке OpenCV нет кодека MJPG"
    for i in range(n_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, : width // 2] = (i % RADIX) * LEVEL_STEP + LEVEL_STEP // 2
        frame[:, width // 2 :] = (i // RADIX) * LEVEL_STEP + LEVEL_STEP // 2
        writer.write(frame)
    writer.release()
    return path


def frame_number(frame: np.ndarray) -> int:
    """Вернуть номер кадра, закодированный в яркости (см. make_video)."""
    half = frame.shape[1] // 2
    units = int(frame[:, :half].mean() // LEVEL_STEP)
    tens = int(frame[:, half:].mean() // LEVEL_STEP)
    return tens * RADIX + units


class FakeDetector:
    """Подставной детектор: запоминает номера кадров и возвращает заранее заданные числа объектов."""

    def __init__(self, counts_by_call: list[int] | None = None) -> None:
        self.seen: list[int] = []
        self._counts = counts_by_call or []

    def detect(self, frame) -> list[Box]:
        call = len(self.seen)
        self.seen.append(frame_number(frame))
        n = self._counts[call] if call < len(self._counts) else call % 3
        return [Box(2 + 10 * k, 2, 10 + 10 * k, 20, 0.9) for k in range(n)]


@pytest.fixture
def tiny_video(tmp_path):
    """Фабрика: tiny_video(n_frames) -> путь к видео в tmp_path."""

    def factory(n_frames: int, fps: float = 10.0) -> Path:
        return make_video(tmp_path / f"clip_{n_frames}.avi", n_frames, fps)

    return factory
