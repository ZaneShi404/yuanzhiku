from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.core.config import data_paths
from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, VideoMetadata
from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.main import create_app


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "video-media"


class FakeMediaAnalyzer:
    def capability(self) -> dict[str, object]:
        return {"enabled": True, "adapter": "unit", "network": False}

    def config_hash(self, maximum_frames: int) -> str:
        return hashlib.sha256(f"unit:{maximum_frames}".encode("ascii")).hexdigest()

    def probe(
        self,
        artifact_path: Path,
        limits: MediaProcessingLimits,
        cancelled,
        heartbeat,
    ) -> VideoMetadata:
        assert artifact_path.is_file()
        assert limits.deadline_monotonic is not None
        assert not cancelled()
        heartbeat()
        return VideoMetadata("mov,mp4,m4a,3gp,3g2,mj2", 10_000, 320, 180, "h264", "aac")

    def extract_frames(
        self,
        artifact_path: Path,
        metadata: VideoMetadata,
        workspace: Path,
        maximum_frames: int,
        limits: MediaProcessingLimits,
        cancelled,
        heartbeat,
    ) -> tuple[ExtractedVideoFrame, ...]:
        assert artifact_path.is_file()
        assert metadata.duration_ms == 10_000
        assert limits.maximum_workspace_bytes > 0
        frames: list[ExtractedVideoFrame] = []
        for ordinal in range(min(maximum_frames, 2)):
            assert not cancelled()
            path = workspace / f"frame-{ordinal}.jpg"
            path.write_bytes(b"synthetic-jpeg-frame-" + bytes([ordinal]))
            heartbeat()
            frames.append(ExtractedVideoFrame(ordinal, (ordinal + 1) * 3_000, path, 320, 180))
        return tuple(frames)


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def client_and_services(runtime_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUANZHIKU_EMBEDDED_WORKER", "false")
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    services.videos.analyzer = FakeMediaAnalyzer()
    with TestClient(app) as client:
        yield client, services


def test_local_video_analysis_stream_and_disabled_ai(client_and_services) -> None:
    client, services = client_and_services
    uploaded = client.post(
        "/api/v1/videos/local",
        data={"rights": "owned", "title": "本地视频", "categories": "[]", "tags": "[\"样本\"]"},
        files={"file": ("sample.mp4", b"not-a-real-mp4", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    source_id = uploaded.json()["source"]["id"]

    completed = services.jobs.run_once()
    assert completed is not None and completed["kind"] == "video_analyze"
    assert completed["state"] == "succeeded"

    detail = client.get(f"/api/v1/videos/{source_id}")
    assert detail.status_code == 200, detail.text
    analysis = detail.json()["analysis"]
    assert analysis["metadata"]["duration_ms"] == 10_000
    assert len(analysis["frames"]) == 2

    frame_id = analysis["frames"][0]["id"]
    frame = client.get(f"/api/v1/videos/{source_id}/frames/{frame_id}")
    assert frame.status_code == 200
    assert frame.headers["content-type"].startswith("image/jpeg")
    assert frame.headers["x-content-type-options"] == "nosniff"

    stream = client.get(f"/api/v1/videos/{source_id}/stream", headers={"Range": "bytes=0-3"})
    assert stream.status_code == 206
    assert stream.content == b"not-"
    assert stream.headers["content-range"] == "bytes 0-3/14"
    assert stream.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/v1/videos/{source_id}/stream", headers={"Range": "bytes=wrong"}).status_code == 416

    queued = client.post(f"/api/v1/videos/{source_id}/transcribe")
    assert queued.status_code == 201
    blocked = services.jobs.run_once()
    assert blocked is not None and blocked["kind"] == "video_transcribe"
    assert blocked["state"] == "blocked"
    assert blocked["message"] == "未配置媒体 AI 服务"
    assert services.repository.get_version(uploaded.json()["content_version"]["id"])["completeness"] == "complete"


def test_local_video_upload_rejects_non_video_suffix(client_and_services) -> None:
    client, _ = client_and_services
    response = client.post(
        "/api/v1/videos/local",
        data={"rights": "owned", "categories": "[]", "tags": "[]"},
        files={"file": ("sample.txt", b"not video", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "仅支持 MP4 和 WebM 视频"


def _analyzed_video(client, services) -> tuple[str, str, list[str]]:
    uploaded = client.post(
        "/api/v1/videos/local",
        data={"rights": "owned", "title": "可移植视频", "categories": "[]", "tags": "[]"},
        files={"file": ("portable.mp4", b"portable-video", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    completed = services.jobs.run_once()
    assert completed is not None and completed["state"] == "succeeded"
    source_id = uploaded.json()["source"]["id"]
    detail = client.get(f"/api/v1/videos/{source_id}")
    assert detail.status_code == 200, detail.text
    return (
        source_id,
        uploaded.json()["artifact"]["sha256"],
        [frame["artifact_sha256"] for frame in detail.json()["analysis"]["frames"]],
    )


def test_video_detail_stream_and_frames_stay_with_selected_version(client_and_services) -> None:
    client, services = client_and_services
    source_id, _, _ = _analyzed_video(client, services)
    first = services.videos.detail(source_id)
    assert first is not None
    first_version = dict(first["version"])
    first_frame = first["analysis"]["frames"][0]

    with services.repository.connection() as connection:
        connection.execute(
            "INSERT INTO artifacts(sha256,byte_size,stored_at) VALUES(?,?,?)",
            ("f" * 64, 15, "2026-07-30T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO content_versions(id,source_id,artifact_sha256,ordinal,original_name,media_type,completeness,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "second-video-version", source_id, "f" * 64, 2, "second.mp4", "video/mp4", "pending",
                "2026-07-30T00:00:00+00:00",
            ),
        )
    services.artifacts.artifact_path("f" * 64).parent.mkdir(parents=True, exist_ok=True)
    services.artifacts.artifact_path("f" * 64).write_bytes(b"second-video-v2")

    latest = client.get(f"/api/v1/videos/{source_id}")
    historical = client.get(f"/api/v1/videos/{source_id}?version_id={first_version['id']}")
    historical_stream = client.get(
        f"/api/v1/videos/{source_id}/stream?version_id={first_version['id']}",
        headers={"Range": "bytes=0-7"},
    )
    historical_frame = client.get(
        f"/api/v1/videos/{source_id}/frames/{first_frame['id']}?version_id={first_version['id']}"
    )
    latest_frame = client.get(f"/api/v1/videos/{source_id}/frames/{first_frame['id']}")

    assert latest.status_code == 200
    assert latest.json()["version"]["id"] == "second-video-version"
    assert latest.json()["analysis"] is None
    assert historical.status_code == 200
    assert historical.json()["version"]["id"] == first_version["id"]
    assert historical.json()["analysis"]["id"] == first["analysis"]["id"]
    assert historical_stream.status_code == 206
    assert historical_stream.content == b"portable"
    assert historical_frame.status_code == 200
    assert latest_frame.status_code == 404


def test_video_purge_removes_unreferenced_original_and_frames(client_and_services) -> None:
    client, services = client_and_services
    source_id, original_hash, frame_hashes = _analyzed_video(client, services)

    assert client.post(f"/api/v1/sources/{source_id}/delete").status_code == 200
    purged = client.post(f"/api/v1/sources/{source_id}/purge")

    assert purged.status_code == 200, purged.text
    assert purged.json()["unreferenced_artifacts_removed"] == 1 + len(frame_hashes)
    assert not services.artifacts.artifact_path(original_hash).exists()
    assert all(not services.artifacts.artifact_path(sha256).exists() for sha256 in frame_hashes)
    rows = services.repository.rows_for_export()
    assert rows["video_analyses"] == []
    assert rows["video_frames"] == []


@pytest.mark.parametrize("mutate", ["frame", "metadata"])
def test_video_export_rejects_tampered_video_records(client_and_services, mutate: str) -> None:
    client, services = client_and_services
    _analyzed_video(client, services)
    exported = services.transfers.create_export(True)
    archive_path = Path(exported["archive_path"])
    with ZipFile(archive_path) as archive:
        members = {entry.filename: archive.read(entry.filename) for entry in archive.infolist()}
    payload = json.loads(members["records.json"])
    records = payload["records"]
    if mutate == "frame":
        records["video_frames"][0]["video_analysis_id"] = "missing-analysis"
    else:
        metadata = json.loads(records["video_analyses"][0]["metadata_json"])
        metadata["duration_ms"] = 0
        records["video_analyses"][0]["metadata_json"] = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    members["records.json"] = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    manifest = json.loads(members.pop("manifest.json"))
    for entry in manifest["entries"]:
        if entry["path"] == "records.json":
            entry["sha256"] = hashlib.sha256(members["records.json"]).hexdigest()
            entry["byte_size"] = len(members["records.json"])
            break
    tampered = archive_path.with_name(f"tampered-{mutate}.zip")
    with ZipFile(tampered, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

    assert services.transfers.verify_archive(tampered) == {"valid": False, "errors": ["视频记录无效"]}


def test_video_export_reimport_preserves_analysis_frames_and_artifacts(client_and_services, runtime_root: Path) -> None:
    client, services = client_and_services
    source_id, original_hash, frame_hashes = _analyzed_video(client, services)
    exported = services.transfers.create_export(True)
    recipient = create_app(runtime_root / "recipient", acquire_lock=False).state.services

    result = recipient.transfers.reimport(exported["archive_path"])

    assert result["imported"] is True
    imported = recipient.videos.detail(source_id)
    assert imported is not None
    assert imported["analysis"] is not None
    assert len(imported["analysis"]["frames"]) == len(frame_hashes)
    assert recipient.artifacts.verify(original_hash)
    assert all(recipient.artifacts.verify(sha256) for sha256 in frame_hashes)


def test_extract_frames_escapes_comma_in_scale_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ffmpeg filtergraph 中逗号是链分隔符：min(640,iw) 必须转义为 min(640\,iw)。"""
    captured: list[list[str]] = []

    def fake_run(cls, command, limits, cancelled, heartbeat, *, workspace=None, capture_stdout=False):
        captured.append(list(command))
        destination = Path(command[-1])
        destination.write_bytes(b"fake-jpeg")
        return b""

    monkeypatch.setattr(LocalFfmpegMediaAnalyzer, "_run", classmethod(fake_run))
    analyzer = LocalFfmpegMediaAnalyzer()
    workspace = tmp_path / "frames"
    workspace.mkdir()
    metadata = VideoMetadata("mov,mp4,m4a,3gp,3g2,mj2", 10_000, 1280, 720, "h264", "aac")
    analyzer.extract_frames(
        tmp_path / "artifact.mp4", metadata, workspace, maximum_frames=2,
        limits=MediaProcessingLimits(30.0, 1024 ** 3, 1024 ** 3),
        cancelled=lambda: False, heartbeat=lambda: None,
    )
    assert captured
    for command in captured:
        assert "-vf" in command
        assert command[command.index("-vf") + 1] == "scale=min(640\,iw):-2"


def _recently_exited_pid() -> int:
    """Return a PID that just exited (psutil.Process 将抛 NoSuchProcess)。

    连续短命子进程收尾取最后一个 PID，把 Windows 下 PID 复用概率压到最小。
    """
    pid = 0
    for _ in range(4):
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        pid = process.pid
    return pid


def test_process_memory_bytes_tolerates_exited_process() -> None:
    """竞态回归：psutil.NoSuchProcess 继承 Exception 而非 OSError，监测必须
    尽力而为返回 None，绝不炸 video_analyze 抽帧作业。"""
    result = LocalFfmpegMediaAnalyzer._process_memory_bytes(_recently_exited_pid())
    assert result is None or isinstance(result, int)


def test_video_time_range_locator_validation() -> None:
    """REQ-016：转写证据唯一允许的 locator 必须带合法毫秒起止范围。"""
    from app.domain.media import video_time_range_locator

    locator = video_time_range_locator(0, 1500)
    assert locator == {"type": "video_time_range", "start_ms": 0, "end_ms": 1500}
    for start_ms, end_ms in [(-1, 10), (10, 10), (20, 10)]:
        with pytest.raises(ValueError):
            video_time_range_locator(start_ms, end_ms)
    for bad in ("0", 1.5, True):
        with pytest.raises(ValueError):
            video_time_range_locator(bad, 10)  # type: ignore[arg-type]
