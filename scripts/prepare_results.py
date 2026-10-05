"""Подготовка учебного sample и всех результатов одной командой.

    python scripts/prepare_results.py --video путь/к/скачанному_видео.mp4

Что делает:
  1. обрезает и пережимает видео через ffmpeg в data/sample.mp4 (H.264, без звука, до 8 МБ);
  2. запускает CLI по варианту 16 (car, stride 5) и сохраняет результат в examples/sample/;
  3. проверяет report.json скриптом check_report.py и размеры файлов в examples/;
  4. прогоняет эксперимент со stride 1/5/10/30 и пишет docs/stride_experiment.md.

Все числа берутся из реальных запусков. Нужны установленные зависимости проекта и ffmpeg.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample.mp4"
EXAMPLE_DIR = ROOT / "examples" / "sample"

MIN_SECONDS = 10
MAX_SECONDS = 60
MAX_SAMPLE_MB = 8
MAX_FRAME_KB = 150
MAX_EXAMPLE_VIDEO_MB = 3
MAX_FPS = 30
# Лестница параметров: если файл не влез в лимит, сжимаем сильнее.
ENCODE_LADDER = [(28, 720), (32, 720), (32, 480)]  # (crf, максимальная высота)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def probe(video: Path) -> tuple[float, int]:
    """Вернуть (длительность в секундах, высота кадра) через ffprobe."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=height:format=duration", "-of", "json", str(video),
        ],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    return float(data["format"]["duration"]), int(data["streams"][0]["height"])


def make_sample(video: Path, seconds: float) -> None:
    """Обрезать и пережать видео в data/sample.mp4, уложившись в лимит размера."""
    SAMPLE.parent.mkdir(parents=True, exist_ok=True)
    for crf, max_height in ENCODE_LADDER:
        scale = f"scale=-2:'min({max_height},ih)',fps='min({MAX_FPS},source_fps)'"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-t", f"{seconds:g}",
            "-vf", scale, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
            "-preset", "slow", "-movflags", "+faststart", str(SAMPLE),
        ]  # fmt: skip
        run(cmd)
        size_mb = SAMPLE.stat().st_size / 1048576
        print(f"sample: crf {crf}, высота до {max_height}, {size_mb:.2f} МБ")
        if size_mb <= MAX_SAMPLE_MB:
            return
    sys.exit(f"Не удалось уложиться в {MAX_SAMPLE_MB} МБ: возьмите более короткий или лёгкий ролик.")


def shrink_frames(frames_dir: Path) -> None:
    """Убедиться, что каждый JPEG в examples/ не тяжелее лимита (иначе пережать)."""
    for path in sorted(frames_dir.glob("*.jpg")):
        quality = 80
        image = cv2.imread(str(path))
        while path.stat().st_size > MAX_FRAME_KB * 1024 and quality > 40:
            quality -= 10
            cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        print(f"кадр {path.name}: {path.stat().st_size / 1024:.0f} КБ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, type=Path, help="исходное видео (скачанное с источника)")
    parser.add_argument("--seconds", type=float, default=25, help="сколько секунд взять с начала (по умолчанию 25)")
    parser.add_argument("--skip-experiment", action="store_true", help="не запускать эксперимент со stride")
    parser.add_argument("--clip-seconds", type=float, default=None, help="для эксперимента: взять первые N секунд")
    args = parser.parse_args()

    if not args.video.is_file():
        sys.exit(f"Файл не найден: {args.video}")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"Не найден {tool}: установите ffmpeg и повторите.")

    duration, height = probe(args.video)
    seconds = min(args.seconds, duration, MAX_SECONDS)
    print(f"Исходник: {duration:.1f} с, высота {height} px. Берём {seconds:g} с.")
    if seconds < MIN_SECONDS:
        sys.exit(f"Ролик короче {MIN_SECONDS} с (получилось {seconds:.1f} с): по ТЗ нужно 10-60 с. Возьмите другой.")

    make_sample(args.video, seconds)

    if EXAMPLE_DIR.exists():
        shutil.rmtree(EXAMPLE_DIR)
    run([sys.executable, "-m", "src.count_video", "--source", "data/sample.mp4", "--class", "car",
         "--stride", "5", "--out", "examples/sample"])  # fmt: skip

    shrink_frames(EXAMPLE_DIR / "frames")
    video = EXAMPLE_DIR / "annotated.mp4"
    if video.is_file() and video.stat().st_size > MAX_EXAMPLE_VIDEO_MB * 1048576:
        print(f"annotated.mp4 больше {MAX_EXAMPLE_VIDEO_MB} МБ: в examples/ его не оставляем")
        video.unlink()
    run([sys.executable, "scripts/check_report.py", "examples/sample/report.json"])

    if not args.skip_experiment:
        cmd = [sys.executable, "scripts/stride_experiment.py", "--source", "data/sample.mp4"]
        if args.clip_seconds:
            cmd += ["--clip-seconds", str(args.clip_seconds)]
        run(cmd)

    print("\nГотово. Проверьте git status, затем внесите числа в README (разделы 6 и 7).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
