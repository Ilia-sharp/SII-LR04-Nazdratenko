"""Веб-обёртка: проверки без запуска YOLO (нужны fastapi и httpx, иначе тесты пропускаются)."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from app import jobs  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("SAMPLE_VIDEO", str(tmp_path / "net_sample.mp4"))
    monkeypatch.setenv("EXAMPLE_DIR", str(tmp_path / "net_primera"))
    return TestClient(app)


def test_health_is_instant_and_ok(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_config_has_variant_16_defaults(client):
    cfg = client.get("/api/config").json()
    assert cfg["class_name"] == "car"
    assert cfg["stride"] == {"default": 5, "min": 1, "max": 30}
    assert cfg["limits"]["max_upload_mb"] == 25 and cfg["limits"]["max_duration_sec"] == 60
    assert cfg["sample_available"] is False and cfg["example_available"] is False


def test_static_index_served(client):
    response = client.get("/")
    assert response.status_code == 200 and 'lang="ru"' in response.text


def test_unknown_job_is_404_with_russian_message(client):
    response = client.get("/api/jobs/aaaaaaaaaaaa")
    assert response.status_code == 404
    assert "перезапустился" in response.json()["detail"]


def test_upload_rejects_wrong_extension(client):
    response = client.post("/api/jobs", files={"file": ("note.txt", b"hello", "text/plain")})
    assert response.status_code == 400
    assert "не поддерживается" in response.json()["detail"]


def test_upload_rejects_empty_request_and_missing_sample(client):
    assert client.post("/api/jobs").status_code == 400
    response = client.post("/api/jobs", data={"use_sample": "true"})
    assert response.status_code == 404  # data/sample.mp4 в этом окружении подменён на несуществующий


def test_upload_rejects_garbage_video_and_cleans_up(client, tmp_path):
    response = client.post("/api/jobs", files={"file": ("clip.mp4", "не видео".encode(), "video/mp4")})
    assert response.status_code == 400
    leftovers = [p for p in (tmp_path / "jobs").iterdir() if p.is_dir()]
    assert leftovers == []  # папка отклонённой задачи удалена


def test_upload_rejects_bad_stride(client):
    response = client.post("/api/jobs", data={"use_sample": "true", "stride": "0"})
    assert response.status_code == 400


def test_result_file_whitelist(tmp_path):
    out = tmp_path / "out"
    (out / "frames").mkdir(parents=True)
    (out / "report.json").write_text("{}", encoding="utf-8")
    (out / "frames" / "frame_000010.jpg").write_bytes(b"jpg")
    (out / "progress.json").write_text("{}", encoding="utf-8")
    assert jobs.safe_result_file(out, "report.json") is not None
    assert jobs.safe_result_file(out, "frames/frame_000010.jpg") is not None
    for bad in (
        "progress.json",
        "../report.json",
        "frames/../report.json",
        "frames/x.jpg",
        "/etc/passwd",
        "stdout.log",
    ):
        assert jobs.safe_result_file(out, bad) is None, bad


def test_job_id_format_is_strict():
    assert jobs.manager.get("../../etc") is None
    assert jobs.manager.get("ABCDEF123456") is None
    assert jobs.manager.get("0123456789ab") is None  # формат верный, но такой задачи нет


def test_reservation_blocks_second_job_until_released(client):
    manager = jobs.JobManager()
    first, _ = manager.create_dir()
    assert manager.is_busy()
    with pytest.raises(jobs.Busy):
        manager.create_dir()  # место занято на время загрузки первого файла
    manager.release(first)
    assert not manager.is_busy()
    second, _ = manager.create_dir()
    assert second != first


def test_cancel_all_endpoint_clears_reservation_and_dirs(client, tmp_path):
    first, job_dir = jobs.manager.create_dir()
    assert jobs.manager.is_busy() and job_dir.exists()
    response = client.post("/api/jobs/cancel-all")
    assert response.status_code == 200 and response.json() == {"stopped": 0}
    assert not jobs.manager.is_busy()
    jobs.manager.release(first)
