"""Отбор кадров: обрабатываются именно кадры 0, N, 2N, ... (без torch)."""

import math

import pytest

from src.pipeline import VideoError, probe_video, run
from tests.conftest import FakeDetector


@pytest.mark.parametrize(("n_frames", "stride"), [(23, 5), (20, 5), (12, 1), (12, 3), (7, 10)])
def test_processes_every_nth_frame(tiny_video, tmp_path, n_frames, stride):
    video = tiny_video(n_frames)
    detector = FakeDetector()
    result = run(video, detector, "car", stride, tmp_path / "out", write_video=False, save_frames=0)

    expected_indices = list(range(0, n_frames, stride))
    assert detector.seen == expected_indices  # детектор видел именно эти кадры
    assert [item["frame"] for item in result.per_frame] == expected_indices
    assert result.frames_total == n_frames
    assert result.frames_processed == math.ceil(n_frames / stride)  # инвариант из ТЗ


def test_stride_larger_than_video_processes_only_frame_zero(tiny_video, tmp_path):
    detector = FakeDetector()
    result = run(tiny_video(6), detector, "car", 100, tmp_path / "out", write_video=False, save_frames=0)
    assert detector.seen == [0]
    assert result.frames_total == 6
    assert result.frames_processed == 1


def test_single_frame_video(tiny_video, tmp_path):
    result = run(tiny_video(1), FakeDetector(), "car", 5, tmp_path / "out", write_video=False, save_frames=0)
    assert (result.frames_total, result.frames_processed) == (1, 1)


def test_counts_follow_detector(tiny_video, tmp_path):
    detector = FakeDetector([4, 0, 7])
    result = run(tiny_video(11), detector, "car", 5, tmp_path / "out", write_video=False, save_frames=0)
    assert result.counts == [4, 0, 7]
    assert result.per_frame[-1] == {"frame": 10, "count": 7}


def test_invalid_stride_is_rejected(tiny_video, tmp_path):
    with pytest.raises(ValueError):
        run(tiny_video(3), FakeDetector(), "car", 0, tmp_path / "out")


def test_missing_and_broken_video(tmp_path):
    with pytest.raises(VideoError):
        probe_video(tmp_path / "net_takogo.mp4")
    broken = tmp_path / "broken.mp4"
    broken.write_bytes("это не видео".encode())
    with pytest.raises(VideoError):
        probe_video(broken)


def test_saved_frames_include_max_count_frame(tiny_video, tmp_path):
    # Максимум (9 объектов) на кадре 15 - он обязан попасть в сохранённые кадры.
    detector = FakeDetector([1, 2, 3, 9, 1, 0])
    out = tmp_path / "out"
    result = run(tiny_video(30), detector, "car", 5, out, write_video=False, save_frames=3)
    names = sorted(p.name for p in (out / "frames").glob("*.jpg"))
    assert "frame_000015.jpg" in names
    assert len(names) <= 3
    assert result.saved_frames == names
