"""Обёртка над YOLO.

Модуль можно импортировать без torch и ultralytics: тяжёлые библиотеки
подключаются лениво, только внутри YoloDetector.__init__. Благодаря этому
логику конвейера тестируют на подставном детекторе.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Корень репозитория: сюда кладутся и отсюда читаются веса (папка models/).
ROOT_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT_DIR / "models"


@dataclass(frozen=True)
class Box:
    """Рамка найденного объекта в пикселях исходного кадра."""

    x1: float
    y1: float
    x2: float
    y2: float
    conf: float


class Detector(Protocol):
    """Всё, что нужно конвейеру от детектора."""

    def detect(self, frame) -> list[Box]:
        """Вернуть рамки объектов выбранного класса на кадре."""
        ...


class UnknownClassError(ValueError):
    """Класс отсутствует среди классов модели."""


def resolve_class_id(names: dict[int, str] | list[str], class_name: str) -> int:
    """Найти id класса по имени (регистр не важен).

    Для 'car' в COCO это ровно класс 2; грузовики, автобусы и мотоциклы
    имеют другие id и не считаются.
    """
    pairs = list(names.items() if isinstance(names, dict) else enumerate(names))
    wanted = class_name.strip().lower()
    for class_id, name in pairs:
        if str(name).lower() == wanted:
            return int(class_id)
    available = ", ".join(str(name) for _, name in pairs)
    raise UnknownClassError(f"Неизвестный класс «{class_name}». Доступные классы: {available}")


def resolve_weights(model: str) -> str:
    """Выбрать путь к весам.

    Порядок: существующий файл как есть → models/<имя> → models/<имя>
    (если файла нет, ultralytics скачает веса именно туда).
    """
    candidate = Path(model)
    if candidate.is_file():
        return str(candidate)
    in_models = MODELS_DIR / candidate.name
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return str(in_models)


class YoloDetector:
    """Детекция одного класса через model.predict (трекера нет)."""

    def __init__(self, weights: str, class_name: str, conf: float, imgsz: int) -> None:
        # Без автоустановки пакетов и лишних сообщений: поведение должно быть
        # детерминированным и на ноутбуке, и на сервере.
        os.environ.setdefault("YOLO_AUTOINSTALL", "False")
        os.environ.setdefault("YOLO_VERBOSE", "False")
        import torch
        from ultralytics import YOLO

        torch.set_num_threads(1)  # на 0.1 CPU лишние потоки только мешают
        self.weights_path = resolve_weights(weights)
        self.model = YOLO(self.weights_path)
        self.class_id = resolve_class_id(self.model.names, class_name)
        self.conf = conf
        self.imgsz = imgsz

    def detect(self, frame) -> list[Box]:
        """Найти объекты выбранного класса на одном кадре."""
        result = self.model.predict(
            frame,
            classes=[self.class_id],
            conf=self.conf,
            imgsz=self.imgsz,
            device="cpu",
            verbose=False,
        )[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return []
        coords = boxes.xyxy.tolist()
        scores = boxes.conf.tolist()
        return [Box(x1, y1, x2, y2, score) for (x1, y1, x2, y2), score in zip(coords, scores, strict=True)]
