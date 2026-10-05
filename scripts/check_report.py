"""Проверка report.json по формату из ТЗ.

    python scripts/check_report.py outputs/sample/report.json

Код выхода: 0 - отчёт корректен, 1 - есть ошибки.
"""

import json
import math
import sys
from pathlib import Path

EXPECTED_KEYS = [
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
]


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check(report: dict) -> list[str]:
    """Вернуть список найденных проблем (пустой - всё хорошо)."""
    problems: list[str] = []
    keys = list(report)
    if keys != EXPECTED_KEYS:
        problems.append(f"ключи должны быть ровно {EXPECTED_KEYS} в этом порядке, а не {keys}")
        return problems

    if not isinstance(report["source"], str) or "\\" in report["source"]:
        problems.append("source: строка с прямыми слешами")
    if not isinstance(report["class_name"], str) or not report["class_name"]:
        problems.append("class_name: непустая строка")
    for key in ("stride", "frames_total", "frames_processed", "detections_per_frame_max"):
        if not is_int(report[key]):
            problems.append(f"{key}: целое число")
    if not is_number(report["detections_per_frame_mean"]):
        problems.append("detections_per_frame_mean: число")
    if not is_number(report["elapsed_sec"]) or report["elapsed_sec"] < 0:
        problems.append("elapsed_sec: неотрицательное число")
    if report["unique_tracks"] is not None:
        problems.append("unique_tracks: без трекера должно быть null")
    if not isinstance(report["model_version"], str):
        problems.append("model_version: строка")
    if problems:
        return problems

    stride, total, processed = report["stride"], report["frames_total"], report["frames_processed"]
    if stride < 1:
        problems.append("stride должен быть ≥ 1")
    elif processed != math.ceil(total / stride):
        problems.append(f"frames_processed ({processed}) != ceil(frames_total / stride) = {math.ceil(total / stride)}")
    if report["detections_per_frame_mean"] > report["detections_per_frame_max"]:
        problems.append("среднее не может быть больше максимума")
    if report["detections_per_frame_mean"] < 0:
        problems.append("среднее не может быть отрицательным")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("outputs/sample/report.json")
    if not path.is_file():
        print(f"Файл не найден: {path}")
        return 1
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Не JSON: {exc}")
        return 1
    problems = check(report)
    if problems:
        print(f"{path}: ОШИБКИ")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"{path}: OK ({report['frames_processed']} из {report['frames_total']} кадров, stride {report['stride']})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
