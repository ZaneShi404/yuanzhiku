"""本地转写（REQ-054）与转写双路径（REQ-051 修订）测试。

引擎与下载器全部注入假替身，绝不触网、绝不加载真实模型；
作业级用例沿 test_media_ai 的 TestClient 模式驱动。
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.local_stt import LocalFunasrTranscriber
from app.domain.media import MediaTranscript, MediaTranscriptSegment
from app.main import create_app
from app.ports.media import MediaAiUnavailable, MediaProcessingCancelled
from app.services.jobs import _select_transcriber
from app.services.stt_models import SttModelManager


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "local-stt"


class FakeManager:
    """模型管理器替身：控制 available 与目录。"""

    def __init__(self, available: bool = False, model_name: str = "paraformer-zh") -> None:
        self._available = available
        self._model_name = model_name

    def status(self) -> dict:
        return {"model_name": self._model_name, "model_available": self._available}

    def model_dirs(self) -> dict[str, Path]:
        return {"paraformer": Path("paraformer"), "vad": Path("vad"), "punc": Path("punc")}


class FakeEngine:
    flavor = "funasr"

    def __init__(self, outputs: list[list[dict]]) -> None:
        self._outputs = list(outputs)
        self.calls: list[Path] = []

    def infer(self, wav_path: Path) -> list[dict]:
        self.calls.append(wav_path)
        return self._outputs.pop(0) if self._outputs else []


def _settings(**overrides: str):
    settings = {
        "ai_local_stt_model": "paraformer-zh",
        "stt_timeout_seconds": "3600",
        "stt_memory_limit_mb": "2048",
        "stt_disk_limit_mb": "1024",
    }
    settings.update(overrides)
    return lambda: settings


def _identity_wav(path, limits, cancelled) -> Path:
    """测试注入：块已是 wav，直接使用（不删输入，由用例自行清理）。"""
    return path


def _transcriber(tmp_path: Path, *, available: bool = True, outputs=None):
    manager = FakeManager(available=available)
    engine = FakeEngine(outputs if outputs is not None else [])
    return LocalFunasrTranscriber(
        manager,
        _settings(),
        engine_loader=lambda *_: engine,
        wav_converter=_identity_wav,
    )


def _fake_chunks(tmp_path: Path) -> list[tuple[Path, int, int]]:
    first = tmp_path / "chunk-000.mp3"
    first.write_bytes(b"audio-one")
    return [(first, 10_000, 12_000)]


# ---- SttModelManager ----

def test_manager_download_writes_state_and_is_idempotent(tmp_path: Path) -> None:
    models = tmp_path / "models"
    lock = tmp_path / "stt-models.lock.json"
    lock.write_text(json.dumps({
        "engine": "funasr",
        "model_name": "paraformer-zh",
        "models": {
            "paraformer": {"model_id": "iic/test", "revision": "master"},
            "vad": {"model_id": "iic/vad", "revision": "master"},
        },
    }), encoding="utf-8")
    calls: list[tuple] = []

    def fake_downloader(model_id: str, *, revision: str, local_dir: str) -> None:
        calls.append((model_id, revision))
        target = Path(local_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "model.onnx").write_bytes(b"weights")

    manager = SttModelManager(models, lock_file=lock, downloader=fake_downloader)
    assert manager.status()["model_available"] is False
    result = manager.download()
    assert result == {"model": "paraformer-zh", "downloaded": True}
    assert [call[0] for call in calls] == ["iic/test", "iic/vad"]
    status = manager.status()
    assert status["model_available"] is True
    state = json.loads((models / "stt" / "manifest.state.json").read_text(encoding="utf-8"))
    assert "paraformer/model.onnx" in state["files_sha256"]
    assert state["files_sha256"]["paraformer/model.onnx"] is not None
    # 幂等：已可用时不再下载
    assert manager.download() == {"model": "paraformer-zh", "downloaded": False}
    assert len(calls) == 2


def test_manager_delete_idempotent(tmp_path: Path) -> None:
    models = tmp_path / "models"
    manager = SttModelManager(models)
    manager.delete()
    manager.delete()
    assert not (models / "stt").exists()


def test_manager_invalid_lock_raises(tmp_path: Path) -> None:
    lock = tmp_path / "bad.lock.json"
    lock.write_text("not-json", encoding="utf-8")
    manager = SttModelManager(tmp_path / "models", lock_file=lock)
    # status 按设计吞掉锁文件错误（按未配置处理）；显式操作必须拒绝。
    assert manager.status()["model_configured"] is False
    with pytest.raises(RuntimeError, match="锁文件无效"):
        manager.download()


# ---- LocalFunasrTranscriber ----

def test_transcriber_model_missing_raises_unavailable(tmp_path: Path) -> None:
    transcriber = _transcriber(tmp_path, available=False)
    assert transcriber.capability()["enabled"] is False
    with pytest.raises(MediaAiUnavailable):
        transcriber.transcribe(_fake_chunks(tmp_path), lambda: False)


def test_transcriber_maps_chunk_offsets(tmp_path: Path) -> None:
    transcriber = _transcriber(
        tmp_path,
        outputs=[[{"text": "你好", "timestamp": [[0, 1200]]}, {"text": "继续", "timestamp": [[1200, 3000]]}]],
    )
    wav = tmp_path / "chunk-000.wav"
    wav.write_bytes(b"audio")
    transcript = transcriber.transcribe([(wav, 10_000, 12_000)], lambda: False)
    assert [(s.start_ms, s.end_ms, s.text) for s in transcript.segments] == [
        (10_000, 11_200, "你好"),
        (11_200, 13_000, "继续"),
    ]
    assert transcript.text == "你好\n继续"


def test_transcriber_fallback_without_timestamps(tmp_path: Path) -> None:
    transcriber = _transcriber(tmp_path, outputs=[[{"text": "整段文本"}]])
    chunks = [(tmp_path / "chunk.wav", 5_000, 12_000)]
    (tmp_path / "chunk.wav").write_bytes(b"audio")
    transcript = transcriber.transcribe(chunks, lambda: False)
    assert [(s.start_ms, s.end_ms, s.text) for s in transcript.segments] == [(5_000, 17_000, "整段文本")]


def test_transcriber_cancelled_before_inference(tmp_path: Path) -> None:
    transcriber = _transcriber(tmp_path, outputs=[[{"text": "不应推理"}]])
    with pytest.raises(MediaProcessingCancelled):
        transcriber.transcribe(_fake_chunks(tmp_path), lambda: True)


def test_transcriber_empty_output_raises(tmp_path: Path) -> None:
    transcriber = _transcriber(tmp_path, outputs=[[]])
    chunks = [(tmp_path / "chunk.wav", 0, 1_000)]
    (tmp_path / "chunk.wav").write_bytes(b"audio")
    with pytest.raises(RuntimeError, match="未返回可用文本"):
        transcriber.transcribe(chunks, lambda: False)


def test_transcriber_config_hash_changes_with_model(tmp_path: Path) -> None:
    manager = FakeManager(available=True)
    a = LocalFunasrTranscriber(manager, _settings(), engine_loader=lambda *_: FakeEngine([]))
    b = LocalFunasrTranscriber(manager, _settings(ai_local_stt_model="paraformer-zh-quant"), engine_loader=lambda *_: FakeEngine([]))
    assert a.config_hash() != b.config_hash()
    assert a.capability()["model_available"] is True


# ---- 路径选择（_select_transcriber 纯函数） ----

class _CapabilityFake:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def capability(self) -> dict:
        return {"enabled": self._enabled}


def test_select_transcriber_matrix() -> None:
    on = _CapabilityFake(True)
    off = _CapabilityFake(False)
    assert _select_transcriber("auto", on, off) == (on, "local", None)
    assert _select_transcriber("auto", off, on) == (on, "api", "local_unavailable")
    assert _select_transcriber("auto", off, off) is None
    assert _select_transcriber("local", off, on) is None
    assert _select_transcriber("local", on, off) == (on, "local", None)
    assert _select_transcriber("api", off, on) == (on, "api", None)
    assert _select_transcriber("api", on, off) is None


# ---- 作业级双路径（TestClient 全链路） ----

@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


class FakeTranscriber:
    """作业级转写替身：可控启用/失败/输出。"""

    def __init__(self, *, enabled: bool = True, fail: bool = False, hash_value: str = "fake-hash") -> None:
        self.enabled = enabled
        self.fail = fail
        self.hash_value = hash_value
        self.calls: list[list] = []

    def capability(self) -> dict:
        return {"enabled": self.enabled, "model": "fake"}

    def config_hash(self) -> str:
        return self.hash_value

    def transcribe(self, audio_chunks, cancelled):
        self.calls.append(audio_chunks)
        if self.fail:
            raise RuntimeError("本地引擎异常")
        return MediaTranscript("转写正文", (MediaTranscriptSegment("转写正文", 0, 1_000),))


@pytest.fixture()
def client_and_services(runtime_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUANZHIKU_EMBEDDED_WORKER", "false")
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    fake_local = FakeTranscriber(enabled=False)
    fake_api = FakeTranscriber(enabled=False)
    services.transcribers["local"] = fake_local
    services.transcribers["api"] = fake_api
    monkeypatch.setattr("app.services.jobs.extract_audio_chunks", lambda *_: [(Path("chunk.mp3"), 0, 1_000)])
    with TestClient(app) as client:
        yield client, services, fake_local, fake_api


def _analyzed_video(client, services) -> tuple[str, str]:
    uploaded = client.post(
        "/api/v1/videos/local",
        data={"rights": "owned", "title": "视频", "domains": "[]", "genres": "[]", "tags": "[]"},
        files={"file": ("sample.mp4", b"not-a-real-mp4", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    from app.domain.media import ExtractedVideoFrame, VideoMetadata

    class FakeAnalyzer:
        def capability(self):
            return {"enabled": True}

        def config_hash(self, maximum_frames: int) -> str:
            return "unit"

        def probe(self, artifact_path, limits, cancelled, heartbeat):
            return VideoMetadata("mov,mp4,m4a", 10_000, 320, 180, "h264", "aac")

        def extract_frames(self, artifact_path, metadata, workspace, maximum_frames, limits, cancelled, heartbeat, transcript_segments=None):
            return ()

    services.videos.analyzer = FakeAnalyzer()
    completed = services.jobs.run_once()
    assert completed is not None and completed["kind"] == "video_analyze" and completed["state"] == "succeeded"
    return uploaded.json()["source"]["id"], uploaded.json()["content_version"]["id"]


def _run_until_kind(services, kind: str, limit: int = 4) -> dict | None:
    for _ in range(limit):
        job = services.jobs.run_once()
        if job is not None and job["kind"] == kind:
            return job
        if job is None:
            return None
    return None


def _queue_transcribe(client, source_id: str) -> None:
    response = client.post(f"/api/v1/videos/{source_id}/transcribe")
    assert response.status_code == 201, response.text


def test_transcribe_blocked_when_no_path_available(client_and_services) -> None:
    client, services, _, _ = client_and_services
    client.put("/api/v1/settings/ai", json={"auto_pipeline": False})
    source_id, _ = _analyzed_video(client, services)
    _queue_transcribe(client, source_id)
    job = _run_until_kind(services, "video_transcribe")
    assert job is not None
    assert job["state"] == "blocked"
    assert "未配置任何可用转写路径" in job["message"]


def test_transcribe_local_success_writes_local_parser_name(client_and_services) -> None:
    client, services, fake_local, _ = client_and_services
    fake_local.enabled = True
    client.put("/api/v1/settings/ai", json={"auto_pipeline": False})
    source_id, version_id = _analyzed_video(client, services)
    _queue_transcribe(client, source_id)
    job = _run_until_kind(services, "video_transcribe")
    assert job is not None and job["state"] == "succeeded", job
    representations = services.repository.representations_for_version(version_id)
    transcription = [r for r in representations if r["kind"] == "transcription"][-1]
    assert transcription["parser_name"] == "local-funasr-fake"
    assert transcription["config_hash"] == "fake-hash"
    assert "降级" not in job["message"]


def test_transcribe_auto_falls_back_to_api(client_and_services) -> None:
    client, services, fake_local, fake_api = client_and_services
    fake_local.enabled = True
    fake_local.fail = True
    fake_api.enabled = True
    fake_api.hash_value = "api-hash"
    client.put("/api/v1/settings/ai", json={"auto_pipeline": False})
    source_id, version_id = _analyzed_video(client, services)
    _queue_transcribe(client, source_id)
    job = _run_until_kind(services, "video_transcribe")
    assert job is not None and job["state"] == "succeeded", job
    assert "API 转写" in job["message"]
    assert len(fake_api.calls) == 1
    representations = services.repository.representations_for_version(version_id)
    transcription = [r for r in representations if r["kind"] == "transcription"][-1]
    assert transcription["parser_name"].startswith("ai-")
    assert transcription["config_hash"] == "api-hash"


def test_transcribe_local_forced_failure_no_fallback(client_and_services) -> None:
    client, services, fake_local, fake_api = client_and_services
    fake_local.enabled = True
    fake_local.fail = True
    fake_api.enabled = True
    client.put("/api/v1/settings/ai", json={"auto_pipeline": False, "transcriber": {"engine": "local"}})
    source_id, _ = _analyzed_video(client, services)
    _queue_transcribe(client, source_id)
    job = _run_until_kind(services, "video_transcribe")
    assert job is not None and job["state"] in {"retry_wait", "failed"}
    assert len(fake_api.calls) == 0


def test_stt_model_action_and_download_job(client_and_services, monkeypatch) -> None:
    client, services, _, _ = client_and_services
    invalid = client.post("/api/v1/settings/ai/stt-model", json={"action": "bogus"})
    assert invalid.status_code == 422
    monkeypatch.setattr(services.stt_manager, "download", lambda cancelled=None, heartbeat=None: {"model": "paraformer-zh", "downloaded": True})
    created = client.post("/api/v1/settings/ai/stt-model", json={"action": "download"})
    assert created.status_code == 201, created.text
    busy = client.post("/api/v1/settings/ai/stt-model", json={"action": "download"})
    assert busy.status_code == 409
    job = _run_until_kind(services, "stt_model_download")
    assert job is not None and job["state"] == "succeeded"
    deleted = client.post("/api/v1/settings/ai/stt-model", json={"action": "delete"})
    assert deleted.status_code == 201


def test_capabilities_expose_local_stt(client_and_services) -> None:
    client, services, _, _ = client_and_services
    payload = client.get("/api/v1/capabilities").json()
    assert "local_stt" in payload["media"]["ai"]
    assert payload["media"]["ai"]["local_stt"]["enabled"] is False
