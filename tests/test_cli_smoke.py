"""Дымовые тесты CLI и конвейера на крошечном видео (без torch и весов)."""

import json
import os
from pathlib import Path

import pytest

from src import count_video
from src.report import REPORT_KEYS
from tests.conftest import FakeDetector

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_detector(monkeypatch):
    """Подменяем создание YOLO на подставной детектор."""

    def fake_create(model, class_name, conf, imgsz):
        detector = FakeDetector([1, 3, 2])
        detector.class_id = 2
        detector.weights_path = "models/fake.pt"
        return detector

    monkeypatch.setattr(count_video, "create_detector", fake_create)


def test_cli_full_run_writes_all_outputs(tiny_video, tmp_path, fake_detector, capsys):
    out = tmp_path / "out"
    code = count_video.main(["--source", str(tiny_video(14)), "--out", str(out), "--save-frames", "3"])
    assert code == 0

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert tuple(report) == REPORT_KEYS
    assert report["class_name"] == "car" and report["stride"] == 5  # значения варианта 16
    assert report["frames_total"] == 14 and report["frames_processed"] == 3
    assert report["detections_per_frame_mean"] == 2.0 and report["detections_per_frame_max"] == 3
    assert report["unique_tracks"] is None

    info = json.loads((out / "run_info.json").read_text(encoding="utf-8"))
    assert info["tracker"] == "none" and info["frames_skipped"] == 11
    assert [item["frame"] for item in info["per_frame"]] == [0, 5, 10]

    assert (out / "annotated.mp4").stat().st_size > 0
    assert list((out / "frames").glob("*.jpg"))
    log_text = (out / "run.log").read_text(encoding="utf-8")
    assert "Готово: всего кадров 14, обработано 3, пропущено 11, stride 5" in log_text


def test_cli_writes_progress_file(tiny_video, tmp_path, fake_detector):
    progress = tmp_path / "progress.json"
    code = count_video.main(
        ["--source", str(tiny_video(10)), "--out", str(tmp_path / "o"), "--progress-json", str(progress), "--no-video"]
    )
    assert code == 0
    data = json.loads(progress.read_text(encoding="utf-8"))
    assert data["stage"] == "done" and data["percent"] == 100.0
    assert data["frames_processed"] == 2 and data["counts"] == [1, 3]


def test_cli_stride_zero_exits_with_2(tiny_video, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        count_video.main(["--source", str(tiny_video(3)), "--stride", "0", "--out", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert "stride" in capsys.readouterr().err


def test_cli_missing_file_exits_with_3(tmp_path, capsys):
    assert count_video.main(["--source", str(tmp_path / "net.mp4"), "--out", str(tmp_path / "o")]) == 3
    assert "файл не найден" in capsys.readouterr().err


def test_cli_broken_video_exits_with_3(tmp_path, fake_detector):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes("мусор".encode())
    assert count_video.main(["--source", str(broken), "--out", str(tmp_path / "o")]) == 3


def test_cli_defaults_are_variant_16():
    args = count_video.build_parser().parse_args([])
    assert (args.class_name, args.stride, args.source) == ("car", 5, "data/sample.mp4")


@pytest.mark.integration
def test_real_yolo_on_sample(tmp_path):
    """Реальный YOLO на data/sample.mp4: пропускается, если нет весов или видео."""
    weights = REPO_ROOT / "models" / "yolov8n.pt"
    sample = REPO_ROOT / "data" / "sample.mp4"
    if not weights.is_file() or not sample.is_file():
        pytest.skip("нет models/yolov8n.pt или data/sample.mp4")
    pytest.importorskip("ultralytics")
    os.environ.setdefault("YOLO_AUTOINSTALL", "False")
    out = tmp_path / "real"
    assert count_video.main(["--source", str(sample), "--out", str(out), "--no-video"]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["frames_processed"] == -(-report["frames_total"] // 5)
