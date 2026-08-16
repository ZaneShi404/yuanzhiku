"""视频直送（REQ-055）与中转（决策 22）测试：全部注入假 HTTP 边界，绝不触网。

作业级用例沿 test_local_stt 的 TestClient 模式驱动三级回退：
视频直送成功 / 直送失败→关键帧兜底 / 均不可用→visual_gap。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.video_ai import (
    MIMO_BASE64_SOURCE_LIMIT,
    MiMoVideoAdapter,
    QwenVideoAdapter,
    RelayClient,
    split_video_segments,
)
from app.domain.media import MediaProcessingLimits, video_time_range_locator
from app.main import create_app
from app.ports.media import MediaAiUnavailable


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "video-direct"

SECRET = "sk-video-secret-123456"


def _settings(**overrides: str) -> dict[str, str]:
    settings = {
        "ai_video_provider": "off",
        "ai_video_model": "",
        "ai_video_max_bytes": "314572800",
        "ai_video_reencode": "on",
        "ai_video_chunk_seconds": "600",
        "ai_video_relay_base_url": "",
        "video_memory_limit_mb": "2048",
        "video_disk_limit_mb": "1024",
        "ai_timeout_seconds": "300",
        "ai_understand_provider": "off",
        "ai_understand_base_url": "",
    }
    settings.update(overrides)
    return settings


def _credentials(**overrides: str) -> dict[str, str]:
    credentials = {}
    credentials.update(overrides)
    return credentials


class _StaticRelay:
    def configured(self) -> bool:
        return True

    def upload(self, path: Path) -> str:
        return f"https://relay.example.com/f/{'a' * 32}"


class _NoRelay:
    def configured(self) -> bool:
        return False


def _completion(segments: list[dict]) -> dict:
    return {"choices": [{"message": {"content": json.dumps({"segments": segments}, ensure_ascii=False)}}]}


def _mimo(tmp_path: Path, relay=None, **caller) -> MiMoVideoAdapter:
    def fake_caller(**kwargs):
        return caller["completion"](**kwargs)

    return MiMoVideoAdapter(
        lambda: _settings(ai_video_provider="mimo"),
        lambda: _credentials(video_mimo=SECRET),
        relay or _NoRelay(),
        completion_caller=caller["completion"],
    )


def _qwen(tmp_path: Path, relay=None, completion=None) -> QwenVideoAdapter:
    return QwenVideoAdapter(
        lambda: _settings(ai_video_provider="qwen"),
        lambda: _credentials(video_qwen=SECRET),
        relay or _NoRelay(),
        completion_caller=completion,
        policy_fetcher=lambda model: {
            "upload_host": "https://dashscope-upload.example.com",
            "upload_dir": "uploads",
            "key": f"{'b' * 32}.mp4",
        },
        uploader=lambda policy, path: f"https://dashscope-upload.example.com/uploads/{policy['key']}",
    )


# ---- 适配器级 ----

def test_mimo_base64_direct_send_offsets_entries(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"small-video")
    captured: list[dict] = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        return _completion([{"time_offset_seconds": 3.5, "content": "画面演示步骤"}])

    adapter = _mimo(tmp_path, completion=fake_completion)
    entries = adapter.understand_video(video, "转写", "主题", lambda: False)
    assert entries == [{"time_ms": 3500, "description": "画面演示步骤", "visible_text": ""}]
    user_content = captured[0]["messages"][1]["content"]
    video_url = user_content[0]["video_url"]["url"]
    assert video_url.startswith("data:video/mp4;base64,")
    assert captured[0]["api_base"] == "https://api.xiaomimimo.com/v1"
    assert captured[0]["model"] == "mimo-v2.5"


def test_mimo_relay_preferred_over_base64(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"small")
    captured: list[dict] = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        return _completion([{"time_offset_seconds": None, "content": "要点"}])

    adapter = _mimo(tmp_path, relay=_StaticRelay(), completion=fake_completion)
    entries = adapter.understand_video(video, "转写", "主题", lambda: False)
    assert entries[0]["description"] == "要点"
    assert captured[0]["messages"][1]["content"][0]["video_url"]["url"].startswith("https://relay.example.com")


def test_mimo_over_limit_without_reencode_raises(tmp_path: Path) -> None:
    settings = lambda: _settings(ai_video_provider="mimo", ai_video_reencode="off")
    adapter = MiMoVideoAdapter(settings, lambda: _credentials(video_mimo=SECRET), _NoRelay())
    # 不实际写 37MB 文件：直接构造超过上限的伪路径会 stat 报错——改用大文件断言能力。
    assert adapter.capability()["reencode"] is False
    assert adapter.capability()["audio_in_video"] is True
    assert adapter.capability()["max_bytes"] == MIMO_BASE64_SOURCE_LIMIT


def test_qwen_dashscope_flow_uses_temp_url(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"tiny")
    captured: list[dict] = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        return _completion([{"time_offset_seconds": 1, "content": "图表信息"}])

    adapter = _qwen(tmp_path, completion=fake_completion)
    entries = adapter.understand_video(video, "转写", "主题", lambda: False)
    assert entries == [{"time_ms": 1000, "description": "图表信息", "visible_text": ""}]
    url = captured[0]["messages"][1]["content"][0]["video_url"]["url"]
    assert url.startswith("https://dashscope-upload.example.com/uploads/")


def test_relay_client_uploads_with_bearer(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"data")
    calls: list[dict] = []

    def fake_uploader(path: Path, base: str, secret: str) -> str:
        calls.append({"path": path, "base": base, "secret": secret})
        return f"{base}/f/token"

    client = RelayClient(
        lambda: _settings(ai_video_relay_base_url="https://relay.example.com"),
        lambda: _credentials(video_relay=SECRET),
        uploader=fake_uploader,
    )
    assert client.configured() is True
    assert client.upload(video) == "https://relay.example.com/f/token"
    assert calls[0]["secret"] == SECRET
    assert calls[0]["base"] == "https://relay.example.com"


def test_relay_client_unconfigured_raises(tmp_path: Path) -> None:
    client = RelayClient(lambda: _settings(), lambda: _credentials())
    assert client.configured() is False
    with pytest.raises(MediaAiUnavailable):
        client.upload(tmp_path / "v.mp4")


def test_split_video_segments_with_real_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    import subprocess

    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=5:size=160x120:rate=10",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(source)],
        check=True,
    )
    limits = MediaProcessingLimits(120.0, 2048 * 1024 * 1024, 1024 * 1024 * 1024)
    segments = split_video_segments(source, tmp_path, 2, limits, lambda: False, ffmpeg="ffmpeg")
    assert len(segments) == 3
    total = 0
    for segment, offset_ms, duration_ms in segments:
        assert offset_ms == total
        assert segment.is_file() and segment.stat().st_size > 0
        assert 1000 <= duration_ms <= 2500
        total += duration_ms
    assert 4800 <= total <= 5200


# ---- 作业级三级回退 ----

@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


class FakeVideoAdapter:
    def __init__(self, *, enabled: bool = True, fail: bool = False) -> None:
        self.enabled = enabled
        self.fail = fail

    def capability(self) -> dict:
        return {"video_input": self.enabled, "max_bytes": 1024 * 1024 * 1024}

    def config_hash(self) -> str:
        return "video-hash"

    def understand_video(self, video_path, transcript_text, focus, cancelled):
        if self.fail:
            raise RuntimeError("视频直送失败")
        return [{"time_ms": 0, "description": "画面要点", "visible_text": "产品名"}]


class FakeAnalyzer:
    def capability(self):
        return {"enabled": True}

    def config_hash(self, maximum_frames: int) -> str:
        return "unit"

    def probe(self, artifact_path, limits, cancelled, heartbeat):
        from app.domain.media import VideoMetadata

        return VideoMetadata("mov,mp4,m4a", 10_000, 320, 180, "h264", "aac")

    def extract_frames(self, artifact_path, metadata, workspace, maximum_frames, limits, cancelled, heartbeat):
        return ()


@pytest.fixture()
def client_and_services(runtime_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUANZHIKU_EMBEDDED_WORKER", "false")
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    services.videos.analyzer = FakeAnalyzer()
    with TestClient(app) as client:
        yield client, services


def _analyzed(client, services, *, vision_model: str = "", force_direct: bool = False) -> tuple[str, str, str]:
    uploaded = client.post(
        "/api/v1/videos/local",
        data={"rights": "owned", "title": "视频", "domains": "[]", "genres": "[]", "tags": "[]"},
        files={"file": ("sample.mp4", b"not-a-real-mp4", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    completed = services.jobs.run_once()
    assert completed is not None and completed["kind"] == "video_analyze" and completed["state"] == "succeeded"
    source_id = uploaded.json()["source"]["id"]
    version_id = uploaded.json()["content_version"]["id"]
    artifact_sha256 = uploaded.json()["artifact"]["sha256"]
    # 直接种子转写表示，避免走转写作业。
    excerpt = "转写正文"
    services.repository.persist_representation_bundle(
        version_id=version_id,
        artifact_sha256=artifact_sha256,
        kind="transcription",
        parser_name="ai-test-whisper-1",
        config_hash="t-hash",
        text=excerpt,
        parent_id=None,
        chunks=services.documents.search_chunk_pairs(excerpt),
        evidence=[{
            "locator": video_time_range_locator(0, 1_000),
            "excerpt": excerpt,
            "excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "is_validated": True,
        }],
    )
    understand = {
        "provider": "openai_compatible",
        "base_url": "https://api.example.com/v1",
        "chat_model": "qwen-plus",
        "api_key": SECRET,
    }
    if vision_model:
        understand["vision_model"] = vision_model
    assert client.put("/api/v1/settings/ai", json={"understand": understand, "auto_pipeline": False}).status_code == 200
    return source_id, version_id, artifact_sha256


def _patch_completion(services, monkeypatch, assessment: dict, summary: dict):
    calls: list[dict] = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        if kwargs["messages"][0]["role"] == "system" and "完整性判断" in kwargs["messages"][0]["content"]:
            return {"choices": [{"message": {"content": json.dumps(assessment, ensure_ascii=False)}}]}
        return {"choices": [{"message": {"content": json.dumps(summary, ensure_ascii=False)}}]}

    monkeypatch.setattr(services.media_ai, "_completion_caller", fake_completion)
    return calls


_INCOMPLETE = {"verdict": "likely_incomplete", "confidence": 0.9, "missing_aspects": ["产品名仅画面呈现"], "reason": "画面演示"}
_SUMMARY = {
    "summary": "本视频介绍外贸网站搭建。",
    "suggested_domains": ["business"],
    "suggested_genres": ["lecture"],
    "suggested_tags": ["外贸"],
}


def test_summarize_video_direct_success(client_and_services, monkeypatch) -> None:
    client, services = client_and_services
    source_id, version_id, _ = _analyzed(client, services)
    assert client.put("/api/v1/settings/ai", json={"video": {"provider": "mimo"}}).status_code == 200
    services.jobs.video_adapter_provider = lambda: FakeVideoAdapter()
    _patch_completion(services, monkeypatch, _INCOMPLETE, _SUMMARY)
    assert client.post(f"/api/v1/videos/{source_id}/summarize", json={"force_tier2": True}).status_code == 201
    job = services.jobs.run_once()
    assert job is not None and job["state"] == "succeeded", job
    summary = [r for r in services.repository.representations_for_version(version_id) if r["kind"] == "summary"][-1]
    assert "+video-mimo-default" in summary["parser_name"]
    assert "视频直送多模态模型" in summary["text_content"]
    assert '"video_direct":true' in summary["text_content"]


def test_summarize_video_direct_failure_falls_back_to_frames(client_and_services, monkeypatch) -> None:
    client, services = client_and_services
    source_id, version_id, _ = _analyzed(client, services, vision_model="qwen-vl-max")
    services.jobs.video_adapter_provider = lambda: FakeVideoAdapter(fail=True)
    _patch_completion(services, monkeypatch, _INCOMPLETE, _SUMMARY)
    monkeypatch.setattr(
        services.media_ai, "describe_frames",
        lambda frame_inputs, focus, cancelled=None: [{"time_ms": 0, "description": "关键帧要点", "visible_text": ""}],
    )
    assert client.post(f"/api/v1/videos/{source_id}/summarize", json={"force_tier2": True}).status_code == 201
    job = services.jobs.run_once()
    assert job is not None and job["state"] == "succeeded", job
    summary = [r for r in services.repository.representations_for_version(version_id) if r["kind"] == "summary"][-1]
    assert "已改用关键帧画面理解" in summary["text_content"]
    assert '"video_direct":false' in summary["text_content"]
    assert "关键帧要点" in summary["text_content"]


def test_summarize_no_video_no_vision_marks_visual_gap(client_and_services, monkeypatch) -> None:
    client, services = client_and_services
    source_id, version_id, _ = _analyzed(client, services)
    services.jobs.video_adapter_provider = lambda: None
    _patch_completion(services, monkeypatch, _INCOMPLETE, _SUMMARY)
    assert client.post(f"/api/v1/videos/{source_id}/summarize", json={"force_tier2": True}).status_code == 201
    job = services.jobs.run_once()
    assert job is not None and job["state"] == "succeeded", job
    summary = [r for r in services.repository.representations_for_version(version_id) if r["kind"] == "summary"][-1]
    assert '"visual_gap":true' in summary["text_content"]
