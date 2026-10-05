"""CLI подсчёта объектов в видео.

    python -m src.count_video --source data/sample.mp4 --class car --stride 5

Коды выхода: 0 - успех; 2 - неверные аргументы или неизвестный класс;
3 - файл не найден / не открывается / нет кадров; 130 - прерывание (Ctrl+C, SIGTERM).
"""

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

import cv2

from . import detector as detector_module
from .detector import UnknownClassError
from .pipeline import ASSUMED_FPS, PCT_ENCODING_END, PCT_MODEL_DONE, VideoError, VideoInfo, probe_video, run
from .report import ProgressWriter, build_report, build_run_info, peak_rss_mb, write_json_atomic

# Параметры варианта 16 из журнала: менять нельзя.
DEFAULT_CLASS = "car"
DEFAULT_STRIDE = 5
DEFAULT_SOURCE = "data/sample.mp4"
DEFAULT_MODEL = "yolov8n.pt"
DEFAULT_CONF = 0.25
DEFAULT_IMGSZ = 640
DEFAULT_SAVE_FRAMES = 8

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_VIDEO = 3
EXIT_INTERRUPTED = 130

log = logging.getLogger("lr04")


class RussianParser(argparse.ArgumentParser):
    """Парсер, который сообщает об ошибках по-русски и выходит с кодом 2."""

    def error(self, message: str):  # type: ignore[override]
        self.exit(EXIT_ARGS, f"Ошибка аргументов: {message}\nПодсказка: python -m src.count_video --help\n")


def _int_min(minimum: int, label: str):
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{label} должен быть целым числом, получено «{text}»") from None
        if value < minimum:
            raise argparse.ArgumentTypeError(f"{label} должен быть ≥ {minimum}, получено {value}")
        return value

    return parse


def _confidence(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"conf должен быть числом, получено «{text}»") from None
    if not 0.0 < value <= 1.0:
        raise argparse.ArgumentTypeError(f"conf должен быть в диапазоне (0; 1], получено {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Описание аргументов командной строки."""
    env_imgsz = os.environ.get("YOLO_IMGSZ")
    default_imgsz = int(env_imgsz) if env_imgsz and env_imgsz.isdigit() else DEFAULT_IMGSZ
    p = RussianParser(
        prog="python -m src.count_video",
        description="Подсчёт объектов выбранного класса в видео: каждый N-й кадр -> YOLO -> report.json.",
    )
    p.add_argument("--source", default=DEFAULT_SOURCE, help="путь к видео (по умолчанию %(default)s)")
    p.add_argument(
        "--class",
        dest="class_name",
        default=DEFAULT_CLASS,
        help="имя класса COCO (по умолчанию %(default)s: ровно car, без грузовиков и автобусов)",
    )
    p.add_argument(
        "--stride",
        type=_int_min(1, "stride"),
        default=DEFAULT_STRIDE,
        help="обрабатывать каждый N-й кадр, N ≥ 1 (по умолчанию %(default)s)",
    )
    p.add_argument("--out", default=None, help="папка результатов (по умолчанию outputs/<имя_видео>)")
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="веса YOLO: ищутся в models/, иначе их скачает ultralytics (по умолчанию %(default)s)",
    )
    p.add_argument(
        "--conf", type=_confidence, default=DEFAULT_CONF, help="порог уверенности (по умолчанию %(default)s)"
    )
    p.add_argument(
        "--imgsz",
        type=_int_min(32, "imgsz"),
        default=default_imgsz,
        help="размер входа YOLO (по умолчанию %(default)s, берётся из YOLO_IMGSZ)",
    )
    p.add_argument(
        "--save-frames",
        type=_int_min(0, "save-frames"),
        default=DEFAULT_SAVE_FRAMES,
        help="сколько кадров с рамками сохранить, 0 - не сохранять (по умолчанию %(default)s)",
    )
    p.add_argument("--no-video", action="store_true", help="не писать видео с рамками")
    p.add_argument("--progress-json", default=None, help="служебный: файл для записи прогресса (используется вебом)")
    p.add_argument(
        "--source-label", default=None, help="служебный: как записать источник в report.json (используется вебом)"
    )
    p.add_argument("--quiet", action="store_true", help="меньше логов в консоли")
    return p


class _QuietFilter(logging.Filter):
    """В тихом режиме убирает периодические строки о ходе обработки."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not hasattr(record, "periodic")


def setup_logging(out_dir: Path, quiet: bool) -> None:
    """Лог в консоль и в run.log: формат ЧЧ:ММ:СС | уровень | сообщение."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # консоли Windows бывают не в UTF-8
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    if quiet:
        console.addFilter(_QuietFilter())
    file_handler = logging.FileHandler(out_dir / "run.log", mode="w", encoding="utf-8")
    file_handler.setFormatter(fmt)
    log.addHandler(console)
    log.addHandler(file_handler)


def create_detector(model: str, class_name: str, conf: float, imgsz: int):
    """Создать детектор YOLO (отдельная функция, чтобы тесты могли её подменить)."""
    return detector_module.YoloDetector(model, class_name, conf, imgsz)


def _on_sigterm(signum, frame):  # noqa: ARG001
    """SIGTERM от веб-сервера (кнопка «Отменить») обрабатываем как Ctrl+C."""
    raise KeyboardInterrupt


def _describe(info: VideoInfo) -> str:
    fps_note = " (в метаданных не указан, принят по умолчанию)" if info.fps_assumed else ""
    est = f", кадров по метаданным ~{info.frame_count_est}" if info.frame_count_est else ""
    return f"{info.width}x{info.height}, {info.fps:.2f} fps{fps_note}{est}"


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI. Возвращает код выхода."""
    args = build_parser().parse_args(argv)
    source = args.source
    if not Path(source).is_file():
        print(f"Ошибка: файл не найден: {source}", file=sys.stderr)
        return EXIT_VIDEO

    out_dir = Path(args.out) if args.out else Path("outputs") / Path(source).stem
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir, args.quiet)
    progress = ProgressWriter(Path(args.progress_json) if args.progress_json else None)
    progress.update("loading_model", 0.0, force=True, message="Проверяю видео")
    cv2.setNumThreads(1)  # на слабом CPU лишние потоки только мешают
    signal.signal(signal.SIGTERM, _on_sigterm)

    try:
        return _execute(args, out_dir, progress)
    except KeyboardInterrupt:
        log.warning("Прервано пользователем")
        progress.update("cancelled", 0.0, force=True, message="Обработка отменена")
        return EXIT_INTERRUPTED
    except VideoError as exc:
        log.error("%s", exc)
        progress.update("error", 0.0, force=True, message=str(exc))
        return EXIT_VIDEO
    except UnknownClassError as exc:
        log.error("%s", exc)
        progress.update("error", 0.0, force=True, message=str(exc))
        return EXIT_ARGS
    except Exception as exc:  # непредвиденная ошибка: трейсбек только в run.log
        log.exception("Непредвиденная ошибка")
        progress.update("error", 0.0, force=True, message=f"Непредвиденная ошибка: {exc}")
        print(f"Непредвиденная ошибка: {exc}. Подробности - в {out_dir / 'run.log'}", file=sys.stderr)
        return 1


def _execute(args: argparse.Namespace, out_dir: Path, progress: ProgressWriter) -> int:
    info = probe_video(args.source)  # быстро падаем на плохом видео, не загружая модель
    log.info(
        "Старт: источник %s | класс %s | stride %d | модель %s | imgsz %d | %s",
        args.source,
        args.class_name,
        args.stride,
        args.model,
        args.imgsz,
        _describe(info),
    )
    if info.fps_assumed:
        log.warning("fps в метаданных отсутствует или некорректен, принято %.0f", ASSUMED_FPS)

    progress.update(
        "loading_model", 1.0, force=True, message="Загружаю модель YOLO", frames_total_est=info.frame_count_est
    )
    t_model = time.perf_counter()
    detector = create_detector(args.model, args.class_name, args.conf, args.imgsz)
    model_load_sec = time.perf_counter() - t_model
    log.info("Модель загружена за %.2f с", model_load_sec)
    progress.update("loading_model", PCT_MODEL_DONE, force=True, message="Модель готова")

    result = run(
        args.source,
        detector,
        args.class_name,
        args.stride,
        out_dir,
        info=info,
        save_frames=args.save_frames,
        write_video=not args.no_video,
        progress=progress,
    )

    progress.update(
        "finalizing",
        PCT_ENCODING_END,
        force=True,
        message="Записываю отчёт",
        elapsed_sec=round(result.elapsed_sec, 1),
        eta_sec=None,
    )
    source_label = args.source_label or args.source
    report = build_report(
        source_label, args.class_name, args.stride, result.frames_total, result.counts, result.elapsed_sec
    )
    run_info = build_run_info(
        weights=Path(getattr(detector, "weights_path", args.model)).name,
        class_id=int(getattr(detector, "class_id", -1)),
        imgsz=args.imgsz,
        conf=args.conf,
        fps_source=result.info.fps,
        fps_assumed=result.info.fps_assumed,
        width=result.info.width,
        height=result.info.height,
        frames_total=result.frames_total,
        frames_processed=result.frames_processed,
        model_load_sec=model_load_sec,
        peak_rss_mb=peak_rss_mb(result.peak_rss_mb),
        ffmpeg_used=result.ffmpeg_used,
        per_frame=result.per_frame,
    )
    write_json_atomic(out_dir / "run_info.json", run_info)
    write_json_atomic(out_dir / "report.json", report)  # последним: его наличие = «всё готово»

    skipped = result.frames_total - result.frames_processed
    log.info(
        "Готово: всего кадров %d, обработано %d, пропущено %d, stride %d, время %.2f с",
        result.frames_total,
        result.frames_processed,
        skipped,
        args.stride,
        result.elapsed_sec,
    )
    log.info(
        "Среднее на обработанный кадр: %.2f, максимум: %d | результаты: %s",
        report["detections_per_frame_mean"],
        report["detections_per_frame_max"],
        out_dir.as_posix(),
    )
    progress.update(
        "done",
        100.0,
        force=True,
        message="Готово",
        elapsed_sec=round(result.elapsed_sec, 1),
        eta_sec=0.0,
        frames_processed=result.frames_processed,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
