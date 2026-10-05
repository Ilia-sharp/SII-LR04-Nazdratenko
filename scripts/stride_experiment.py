"""Эксперимент со stride: запускает CLI для stride 1, 5, 10, 30 и пишет docs/stride_experiment.md.

    python scripts/stride_experiment.py --source data/sample.mp4

Таблица строится только из реальных report.json и run_info.json, ничего не подставляется вручную.
Если stride 1 слишком долгий, добавьте --clip-seconds 10: эксперимент пройдёт на первых 10 секундах
видео, и это будет отмечено в документе.
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT / "outputs" / "stride_experiment"
DOC_PATH = ROOT / "docs" / "stride_experiment.md"
VARIANT_STRIDE = 5


def make_clip(source: Path, seconds: float) -> Path:
    """Вырезать первые N секунд видео (для слишком долгого stride 1)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("Для --clip-seconds нужен ffmpeg")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    clip = WORK_DIR / "clip.mp4"
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-i", str(source), "-t", str(seconds),
        "-an", "-c:v", "libx264", "-crf", "23", str(clip),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)
    return clip


def run_stride(source: Path, class_name: str, stride: int, imgsz: int | None) -> tuple[dict, dict]:
    """Запустить CLI для одного stride и вернуть (report, run_info)."""
    out = WORK_DIR / f"stride_{stride}"
    cmd = [
        sys.executable, "-m", "src.count_video", "--source", str(source), "--class", class_name,
        "--stride", str(stride), "--out", str(out), "--no-video", "--save-frames", "0", "--quiet",
    ]  # fmt: skip
    if imgsz:
        cmd += ["--imgsz", str(imgsz)]
    print(f"stride {stride}: запуск...", flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    info = json.loads((out / "run_info.json").read_text(encoding="utf-8"))
    return report, info


def render(rows: list[tuple[dict, dict]], source_label: str, clipped: float | None) -> str:
    """Собрать markdown-документ из результатов запусков."""
    first_info = rows[0][1]
    fps = first_info["fps_source"]
    size = f"{first_info['width']}x{first_info['height']}"
    lines = [
        "# Эксперимент: как stride влияет на стоимость и результат",
        "",
        f"Дата запуска: {date.today().isoformat()}. Источник: `{source_label}`, {size}, {fps:g} fps. "
        f"Класс: `{rows[0][0]['class_name']}`, imgsz {first_info['imgsz']}, веса `{first_info['weights']}`.",
        "",
        f"Железо и окружение: {platform.platform()}, Python {first_info['python_version']}, "
        f"ultralytics {first_info['ultralytics_version']}, torch {first_info['torch_version']}.",
        "",
    ]
    if clipped:
        lines += [f"> **Важно:** эксперимент проведён на первых {clipped:g} с видео, а не на всём ролике.", ""]
    lines += [
        f"**Значение по варианту 16 - stride = {VARIANT_STRIDE}.** Остальные значения запущены только для "
        "сравнения; основной результат работы (`report.json`, `examples/`) получен со stride 5.",
        "",
        "| stride | кадров всего | обработано | среднее машин на кадр | максимум | elapsed_sec "
        "| анализов в секунду видео | окно риска, с |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for report, _ in rows:
        stride = report["stride"]
        mark = " (вариант)" if stride == VARIANT_STRIDE else ""
        lines.append(
            f"| {stride}{mark} | {report['frames_total']} | {report['frames_processed']} "
            f"| {report['detections_per_frame_mean']} | {report['detections_per_frame_max']} "
            f"| {report['elapsed_sec']} | {fps / stride:.2f} | {stride / fps:.2f} |"
        )
    base = next((r for r, _ in rows if r["stride"] == 1), None)
    lines += ["", "## Как читать таблицу", ""]
    if base:
        for report, _ in rows:
            if report["stride"] != 1 and report["elapsed_sec"] > 0:
                ratio = base["elapsed_sec"] / report["elapsed_sec"]
                lines.append(f"- stride {report['stride']} быстрее stride 1 в {ratio:.1f} раза по `elapsed_sec`.")
    lines += [
        "- Время растёт почти пропорционально числу обработанных кадров: основная цена - инференс YOLO. "
        "Пропущенные кадры всё равно декодируются, поэтому выигрыш чуть меньше идеального. "
        "Первый вызов YOLO дольше остальных (прогрев), и на коротком ролике он заметно искажает отношение времён.",
        "- Среднее и максимум считаются по обработанным кадрам, поэтому при разных stride они отличаются: "
        "это разные выборки одного и того же видео, а не ошибка.",
        "- «Окно риска» - сколько секунд подряд событие должно быть видно, "
        "чтобы гарантированно попасть в обработанный кадр.",
        "- Один запуск на каждое значение, без повторов: время зависит от загрузки машины "
        "и носит ориентировочный характер.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="data/sample.mp4")
    parser.add_argument("--class", dest="class_name", default="car")
    parser.add_argument("--strides", type=int, nargs="+", default=[1, 5, 10, 30])
    parser.add_argument("--clip-seconds", type=float, default=None, help="обрезать видео до первых N секунд")
    parser.add_argument("--imgsz", type=int, default=None)
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f"Файл не найден: {source}")
        return 3
    label = source.as_posix()
    if args.clip_seconds:
        source = make_clip(source, args.clip_seconds)
    rows = [run_stride(source, args.class_name, s, args.imgsz) for s in args.strides]
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(render(rows, label, args.clip_seconds), encoding="utf-8")
    print(f"Готово: {DOC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
