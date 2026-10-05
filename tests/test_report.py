"""Формат report.json и агрегаты (без torch)."""

import json

import pytest

from src.report import REPORT_KEYS, ProgressWriter, build_report, write_json_atomic


def make_report(counts=(3, 0, 5), stride=5, frames_total=11, elapsed=1.234):
    return build_report("data\\sample.mp4", "car", stride, frames_total, list(counts), elapsed)


def test_exactly_ten_keys_in_order():
    report = make_report()
    assert tuple(report) == REPORT_KEYS
    assert len(report) == 10


def test_values_and_types():
    report = make_report()
    assert report["source"] == "data/sample.mp4"  # слеши прямые
    assert report["class_name"] == "car"
    assert report["stride"] == 5 and isinstance(report["stride"], int)
    assert report["frames_total"] == 11
    assert report["frames_processed"] == 3
    assert report["detections_per_frame_mean"] == pytest.approx(2.67)
    assert report["detections_per_frame_max"] == 5 and isinstance(report["detections_per_frame_max"], int)
    assert report["unique_tracks"] is None
    assert report["model_version"] == "v1.0"
    assert report["elapsed_sec"] == 1.23


def test_mean_not_above_max():
    for counts in ([0], [1, 1, 1], [0, 9, 2, 2], [4, 4, 5]):
        report = make_report(counts)
        assert report["detections_per_frame_mean"] <= report["detections_per_frame_max"]


def test_mean_is_over_processed_frames_not_all():
    report = make_report(counts=[2, 4], frames_total=10, stride=5)
    assert report["detections_per_frame_mean"] == 3.0


def test_empty_counts_rejected():
    with pytest.raises(ValueError):
        make_report(counts=[])


def test_json_roundtrip_keeps_order_and_null(tmp_path):
    path = tmp_path / "report.json"
    write_json_atomic(path, make_report())
    text = path.read_text(encoding="utf-8")
    assert '"unique_tracks": null' in text
    assert tuple(json.loads(text)) == REPORT_KEYS
    assert not (tmp_path / "report.json.tmp").exists()  # временный файл убран


def test_progress_is_monotonic_and_capped_before_done(tmp_path):
    path = tmp_path / "progress.json"
    progress = ProgressWriter(path)
    progress.update("processing", 40.0, force=True)
    progress.update("processing", 30.0, force=True)  # назад не идём
    assert progress.state["percent"] == 40.0
    progress.update("encoding", 150.0, force=True)  # до done не выше 99
    assert progress.state["percent"] == 99.0
    progress.update("done", 99.0, force=True)
    assert json.loads(path.read_text(encoding="utf-8"))["percent"] == 100.0
