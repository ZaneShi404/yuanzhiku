"""媒体 AI（Phase 5）：配置、凭据、适配器与作业链测试。

所有 AI 边界一律注入假实现（transcription_caller/completion_caller/audio_extractor），
绝不触网；异常文本刻意内嵌 URL 与密钥，验证脱敏纪律。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.media_ai import ConfiguredMediaAi, sanitize_ai_error
from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, VideoMetadata
from app.domain.models import validate_ai_base_url
from app.ports.media import MediaAiUnavailable
from app.services.ai_credentials import read_ai_credentials
from app.main import create_app


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "media-ai"

SECRET = "sk-secret-abcdef123456"
BASE_URL = "https://api.example.com/v1"


def _ai_settings(**overrides: str) -> dict[str, str]:
    settings = {
        "ai_transcribe_provider": "off",
        "ai_transcribe_base_url": "",
        "ai_transcribe_model": "whisper-1",
        "ai_understand_provider": "off",
        "ai_understand_base_url": "",
        "ai_chat_model": "qwen-plus",
        "ai_vision_model": "",
        "ai_timeout_seconds": "300",
        "video_memory_limit_mb": "2048",
        "video_disk_limit_mb": "1024",
    }
    settings.update(overrides)
    return settings


def _adapter(tmp_path: Path, settings: dict[str, str], credentials: dict[str, str], **kwargs) -> ConfiguredMediaAi:
    kwargs.setdefault("audio_extractor", lambda *_: [])
    return ConfiguredMediaAi(lambda: settings, lambda: credentials, tmp_path / "staging", **kwargs)


def _completion_response(payload: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


class FakeMediaAnalyzer:
    def capability(self) -> dict[str, object]:
        return {"enabled": True, "adapter": "unit", "network": False}

    def config_hash(self, maximum_frames: int) -> str:
        return hashlib.sha256(f"unit:{maximum_frames}".encode("ascii")).hexdigest()

    def probe(self, artifact_path: Path, limits: MediaProcessingLimits, cancelled, heartbeat) -> VideoMetadata:
        return VideoMetadata("mov,mp4,m4a,3gp,3g2,mj2", 10_000, 320, 180, "h264", "aac")

    def extract_frames(self, artifact_path, metadata, workspace, maximum_frames, limits, cancelled, heartbeat):
        frames: list[ExtractedVideoFrame] = []
        for ordinal in range(min(maximum_frames, 2)):
            path = workspace / f"frame-{ordinal}.jpg"
            path.write_bytes(b"synthetic-jpeg-frame-" + bytes([ordinal]))
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


def _analyzed_video(client, services) -> tuple[str, str]:
    uploaded = client.post(
        "/api/v1/videos/local",
        data={"rights": "owned", "title": "AI 视频", "domains": "[]", "genres": "[]", "tags": "[]"},
        files={"file": ("sample.mp4", b"not-a-real-mp4", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    completed = services.jobs.run_once()
    assert completed is not None and completed["kind"] == "video_analyze" and completed["state"] == "succeeded"
    return uploaded.json()["source"]["id"], uploaded.json()["content_version"]["id"]


def _configure_ai(client, *, vision_model: str = "") -> None:
    response = client.put("/api/v1/settings/ai", json={
        "transcribe": {"provider": "openai_compatible", "base_url": BASE_URL, "model": "whisper-1", "api_key": SECRET},
        "understand": {
            "provider": "openai_compatible", "base_url": BASE_URL,
            "chat_model": "qwen-plus", "vision_model": vision_model, "api_key": SECRET,
        },
        "timeout_seconds": 600,
    })
    assert response.status_code == 200, response.text


def test_ai_base_url_validation() -> None:
    for rejected in (
        "http://api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://10.1.2.3/v1",
        "https://192.168.1.8/v1",
        "https://[::1]/v1",
        "https://user:pass@api.example.com/v1",
        "https://",
        "not-a-url",
        "https://" + "a" * 2100,
    ):
        with pytest.raises(ValueError):
            validate_ai_base_url(rejected)
    validate_ai_base_url("")
    validate_ai_base_url("https://api.example.com/v1")


def test_settings_ai_roundtrip_masks_key_and_stores_credential_file(client_and_services, runtime_root: Path) -> None:
    client, services = client_and_services
    initial = client.get("/api/v1/settings/ai")
    assert initial.status_code == 200
    assert initial.json()["transcribe"]["provider"] == "off"
    assert initial.json()["transcribe"]["has_key"] is False
    assert initial.json()["timeout_seconds"] == 300

    response = client.put("/api/v1/settings/ai", json={
        "transcribe": {"provider": "openai_compatible", "base_url": BASE_URL, "model": "whisper-1", "api_key": SECRET},
        "understand": {"provider": "openai_compatible", "base_url": "", "chat_model": "qwen-plus", "api_key": "under-key-9999"},
        "timeout_seconds": 600,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transcribe"]["has_key"] is True
    assert body["transcribe"]["key_hint"] == f"…{SECRET[-4:]}"
    assert body["understand"]["key_hint"] == "…9999"
    assert SECRET not in response.text and "under-key-9999" not in response.text

    # 凭据落文件；settings 表与通用 GET /settings 绝不含密钥。
    credentials_file = services.paths.ai_credentials_file
    assert read_ai_credentials(credentials_file) == {"transcribe": SECRET, "understand": "under-key-9999"}
    assert SECRET not in json.dumps(services.repository.get_settings())
    assert SECRET not in client.get("/api/v1/settings").text
    assert client.get("/api/v1/settings/ai").json()["timeout_seconds"] == 600

    # 分组切回 off → 该组密钥移除；另一组不受影响。
    switched_off = client.put("/api/v1/settings/ai", json={"transcribe": {"provider": "off"}})
    assert switched_off.status_code == 200
    assert switched_off.json()["transcribe"]["has_key"] is False
    assert switched_off.json()["understand"]["has_key"] is True
    assert read_ai_credentials(credentials_file) == {"understand": "under-key-9999"}


def test_settings_ai_rejects_invalid_base_url_with_stable_422(client_and_services) -> None:
    client, _ = client_and_services
    for bad_url in ("http://api.example.com", "https://127.0.0.1/v1", "https://10.0.0.2/v1"):
        response = client.put("/api/v1/settings/ai", json={
            "transcribe": {"provider": "openai_compatible", "base_url": bad_url},
        })
        assert response.status_code == 422
        assert response.json()["detail"] == {"code": "request_validation", "message": "请求字段无效"}
    bad_provider = client.put("/api/v1/settings/ai", json={"transcribe": {"provider": "bogus"}})
    assert bad_provider.status_code == 422


def test_capability_group_gating(client_and_services) -> None:
    client, services = client_and_services
    capability = client.get("/api/v1/capabilities").json()["media"]["ai"]
    assert capability == {
        "enabled": False,
        "transcribe_enabled": False,
        "understand_enabled": False,
        "tier2_enabled": False,
        "network": True,
        "provider": None,
    }
    _configure_ai(client)
    capability = client.get("/api/v1/capabilities").json()["media"]["ai"]
    assert capability["enabled"] is True
    assert capability["transcribe_enabled"] is True
    assert capability["understand_enabled"] is True
    assert capability["tier2_enabled"] is False
    client.put("/api/v1/settings/ai", json={"understand": {"vision_model": "qvq-vl"}})
    assert client.get("/api/v1/capabilities").json()["media"]["ai"]["tier2_enabled"] is True


def test_connection_test_sanitizes_failures(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    unconfigured = client.post("/api/v1/settings/ai/test", json={"part": "understand"})
    assert unconfigured.status_code == 200
    assert unconfigured.json() == {"ok": False, "message": "该分组未启用或未配置 API Key"}

    _configure_ai(client)

    def leaking_completion(**kwargs):
        raise RuntimeError(f"connect to {BASE_URL} failed with key {SECRET}")

    monkeypatch.setattr(services.media_ai, "_completion_caller", leaking_completion)
    failed = client.post("/api/v1/settings/ai/test", json={"part": "understand"})
    assert failed.status_code == 200
    payload = failed.json()
    assert payload["ok"] is False
    assert SECRET not in payload["message"]
    assert "api.example.com" not in payload["message"]

    monkeypatch.setattr(services.media_ai, "_completion_caller", lambda **kwargs: {"choices": []})
    ok = client.post("/api/v1/settings/ai/test", json={"part": "understand"})
    assert ok.json() == {"ok": True}

    def auth_failure(*args, **kwargs):
        class AuthenticationError(Exception):
            status_code = 401

        raise AuthenticationError(f"invalid key {SECRET} at {BASE_URL}")

    monkeypatch.setattr(services.media_ai, "_models_prober", auth_failure)
    transcribe = client.post("/api/v1/settings/ai/test", json={"part": "transcribe"})
    assert transcribe.json() == {"ok": False, "message": "鉴权失败：请检查 API Key"}


def test_sanitize_ai_error_never_echoes_secrets() -> None:
    class APIConnectionError(Exception):
        pass

    class NotFoundError(Exception):
        pass

    assert sanitize_ai_error(APIConnectionError(f"dns {BASE_URL} {SECRET}")) == "网络不可达或连接超时"
    assert sanitize_ai_error(NotFoundError("model")) == "端点无效或模型不可用"
    assert sanitize_ai_error(Exception(f"boom {SECRET}")) == "媒体 AI 服务调用失败"


def test_adapter_transcribe_merges_chunk_offsets(tmp_path: Path) -> None:
    settings = _ai_settings(ai_transcribe_provider="openai_compatible", ai_transcribe_base_url=BASE_URL)
    calls: list[dict] = []

    def fake_extractor(artifact_path: Path, workspace: Path, timeout: float, cancelled) -> list:
        first = workspace / "chunk-000.mp3"
        second = workspace / "chunk-001.mp3"
        first.write_bytes(b"audio-one")
        second.write_bytes(b"audio-two")
        return [(first, 0, 60_000), (second, 60_000, 60_000)]

    def fake_transcription(**kwargs):
        calls.append(kwargs)
        kwargs["file"].read()
        if len(calls) == 1:
            return {"segments": [
                {"start": 0.0, "end": 1.5, "text": "第一段"},
                {"start": 1.5, "end": 3.0, "text": "继续"},
            ]}
        return {"segments": [{"start": 0.5, "end": 2.0, "text": "第二段"}]}

    adapter = _adapter(
        tmp_path, settings, {"transcribe": SECRET},
        audio_extractor=fake_extractor, transcription_caller=fake_transcription,
    )
    transcript = adapter.transcribe(tmp_path / "video.mp4", "video/mp4", lambda: False)
    assert [(item.start_ms, item.end_ms, item.text) for item in transcript.segments] == [
        (0, 1500, "第一段"),
        (1500, 3000, "继续"),
        (60_500, 62_000, "第二段"),
    ]
    assert transcript.text == "第一段\n继续\n第二段"
    assert calls[0]["model"] == "whisper-1"
    assert calls[0]["api_key"] == SECRET
    assert calls[0]["api_base"] == BASE_URL
    assert calls[0]["timeout"] == 300.0


def test_adapter_transcribe_synthesizes_segment_without_timestamps(tmp_path: Path) -> None:
    settings = _ai_settings(ai_transcribe_provider="openai_compatible")

    def fake_extractor(artifact_path: Path, workspace: Path, timeout: float, cancelled) -> list:
        chunk = workspace / "chunk.mp3"
        chunk.write_bytes(b"audio")
        return [(chunk, 5_000, 12_000)]

    adapter = _adapter(
        tmp_path, settings, {"transcribe": SECRET},
        audio_extractor=fake_extractor,
        transcription_caller=lambda **kwargs: {"text": "整段文本"},
    )
    transcript = adapter.transcribe(tmp_path / "video.mp4", None, lambda: False)
    assert [(item.start_ms, item.end_ms) for item in transcript.segments] == [(5_000, 17_000)]


def test_adapter_group_gating_raises_unavailable(tmp_path: Path) -> None:
    off = _adapter(tmp_path, _ai_settings(), {})
    with pytest.raises(MediaAiUnavailable):
        off.transcribe(tmp_path / "v.mp4", None, lambda: False)
    with pytest.raises(MediaAiUnavailable):
        off.summarize({"transcript_text": "x"}, lambda: False)
    with pytest.raises(MediaAiUnavailable):
        off.classify("正文", {"title": "x"})
    with pytest.raises(MediaAiUnavailable):
        off.assess_completeness("x", {"duration_ms": 1000, "coverage_chars_per_sec": 10, "max_silence_ms": 0})
    no_key = _adapter(tmp_path, _ai_settings(ai_understand_provider="openai_compatible"), {})
    with pytest.raises(MediaAiUnavailable):
        no_key.summarize({"transcript_text": "x"}, lambda: False)
    with pytest.raises(MediaAiUnavailable):
        no_key.classify("正文", {})
    no_vision = _adapter(
        tmp_path, _ai_settings(ai_understand_provider="openai_compatible"), {"understand": SECRET},
    )
    with pytest.raises(MediaAiUnavailable):
        no_vision.describe_frames([{"path": "a.jpg", "time_ms": 0}], "主题")


def test_assess_completeness_rules_short_circuit_without_llm(tmp_path: Path) -> None:
    forbidden = lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM 不应被调用"))
    adapter = _adapter(
        tmp_path,
        _ai_settings(ai_understand_provider="openai_compatible"),
        {"understand": SECRET},
        completion_caller=forbidden,
    )
    low_coverage = adapter.assess_completeness("短", {"duration_ms": 100_000, "coverage_chars_per_sec": 0.4, "max_silence_ms": 0})
    assert low_coverage["verdict"] == "likely_incomplete"
    assert low_coverage["rule_triggered"] is True
    long_silence = adapter.assess_completeness("x" * 5000, {"duration_ms": 100_000, "coverage_chars_per_sec": 5, "max_silence_ms": 31_000})
    assert long_silence["verdict"] == "likely_incomplete"
    assert long_silence["rule_triggered"] is True


def test_assess_completeness_llm_threshold(tmp_path: Path) -> None:
    settings = _ai_settings(ai_understand_provider="openai_compatible")
    context = {"title": "t", "notes": "", "duration_ms": 100_000, "coverage_chars_per_sec": 5, "max_silence_ms": 1_000}

    incomplete = _adapter(
        tmp_path, settings, {"understand": SECRET},
        completion_caller=lambda **kwargs: _completion_response({
            "verdict": "likely_incomplete", "confidence": 0.8,
            "missing_aspects": ["产品名仅画面呈现"], "reason": "疑似画面演示",
        }),
    ).assess_completeness("x" * 1000, context)
    assert incomplete["verdict"] == "likely_incomplete"
    assert incomplete["rule_triggered"] is False
    assert incomplete["missing_aspects"] == ["产品名仅画面呈现"]

    low_confidence = _adapter(
        tmp_path, settings, {"understand": SECRET},
        completion_caller=lambda **kwargs: _completion_response({
            "verdict": "likely_incomplete", "confidence": 0.5, "missing_aspects": ["x"], "reason": "r",
        }),
    ).assess_completeness("x" * 1000, context)
    assert low_confidence["verdict"] == "complete"
    assert low_confidence["missing_aspects"] == []

    complete = _adapter(
        tmp_path, settings, {"understand": SECRET},
        completion_caller=lambda **kwargs: _completion_response({
            "verdict": "complete", "confidence": 0.9, "missing_aspects": [], "reason": "对齐",
        }),
    ).assess_completeness("x" * 1000, context)
    assert complete["verdict"] == "complete"
    assert complete["confidence"] == 0.9


def test_describe_frames_requires_visible_text_in_prompt(tmp_path: Path) -> None:
    settings = _ai_settings(ai_understand_provider="openai_compatible", ai_vision_model="qvq-vl")
    frames = [tmp_path / f"frame-{index}.jpg" for index in range(5)]
    for frame in frames:
        frame.write_bytes(b"jpeg" + frame.name.encode("ascii"))
    captured: list[dict] = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        image_count = sum(1 for item in kwargs["messages"][-1]["content"] if item["type"] == "image_url")
        return _completion_response({"frames": [
            {"index": index, "description": f"画面{index}", "visible_text": f"文字{index}"} for index in range(image_count)
        ]})

    adapter = _adapter(tmp_path, settings, {"understand": SECRET}, completion_caller=fake_completion)
    results = adapter.describe_frames(
        [{"path": frame, "time_ms": (index + 1) * 1000} for index, frame in enumerate(frames)],
        "产品评测",
    )
    assert len(captured) == 2  # 4 + 1 两批
    system_prompt = captured[0]["messages"][0]["content"]
    assert "画面文字" in system_prompt
    assert captured[0]["model"] == "qvq-vl"
    first_user = captured[0]["messages"][1]["content"]
    assert first_user[0]["type"] == "text"
    assert all(item["image_url"]["url"].startswith("data:image/jpeg;base64,") for item in first_user[1:])
    assert [item["time_ms"] for item in results] == [1000, 2000, 3000, 4000, 5000]
    assert results[0]["description"] == "画面0"
    assert results[0]["visible_text"] == "文字0"


def test_summarize_clamps_suggestions_to_taxonomy(tmp_path: Path) -> None:
    settings = _ai_settings(ai_understand_provider="openai_compatible")

    def fake_completion(**kwargs):
        return _completion_response({
            "summary": "这是结构化摘要。",
            "suggested_domains": ["technical", "不存在的领域", 42],
            "suggested_genres": ["lecture", "podcast"],
            "suggested_tags": ["AI", "", "长" * 30, "AI"],
        })

    adapter = _adapter(tmp_path, settings, {"understand": SECRET}, completion_caller=fake_completion)
    result = adapter.summarize({"transcript_text": "转写", "title": "标题"}, lambda: False)
    assert result["summary"] == "这是结构化摘要。"
    assert result["suggested_domains"] == ["technical"]
    assert result["suggested_genres"] == ["lecture"]
    assert result["suggested_tags"] == ["AI", "长" * 20]


def test_summarize_sanitizes_sdk_errors(tmp_path: Path) -> None:
    settings = _ai_settings(ai_understand_provider="openai_compatible", ai_understand_base_url=BASE_URL)

    def leaking_completion(**kwargs):
        raise RuntimeError(f"upstream {BASE_URL} rejected key {SECRET}")

    adapter = _adapter(tmp_path, settings, {"understand": SECRET}, completion_caller=leaking_completion)
    with pytest.raises(RuntimeError) as excinfo:
        adapter.summarize({"transcript_text": "转写"}, lambda: False)
    assert SECRET not in str(excinfo.value)
    assert "api.example.com" not in str(excinfo.value)


def _fake_audio_extractor(artifact_path: Path, workspace: Path, timeout: float, cancelled) -> list:
    first = workspace / "chunk-000.mp3"
    second = workspace / "chunk-001.mp3"
    first.write_bytes(b"audio-one")
    second.write_bytes(b"audio-two")
    return [(first, 0, 2_000), (second, 2_000, 2_000)]


def _fake_transcription_calls(calls: list[dict]):
    def fake(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"segments": [{"start": 0.0, "end": 1.5, "text": "量子计算入门讲解"}]}
        return {"segments": [{"start": 0.2, "end": 1.0, "text": "第二部分内容"}]}

    return fake


def test_transcription_job_persists_time_range_evidence_and_searchable_chunks(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    source_id, version_id = _analyzed_video(client, services)
    _configure_ai(client)
    calls: list[dict] = []
    monkeypatch.setattr(services.media_ai, "_audio_extractor", _fake_audio_extractor)
    monkeypatch.setattr(services.media_ai, "_transcription_caller", _fake_transcription_calls(calls))

    queued = client.post(f"/api/v1/videos/{source_id}/transcribe")
    assert queued.status_code == 201
    completed = services.jobs.run_once()
    assert completed is not None and completed["kind"] == "video_transcribe"
    assert completed["state"] == "succeeded", completed
    assert completed["progress"] == 100

    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    transcription = next(item for item in representations if item["kind"] == "transcription")
    assert transcription["parser_name"].startswith("ai-openai_compatible-whisper-1")
    assert "量子计算入门讲解" in transcription["text_content"]
    evidence = client.get(f"/api/v1/representations/{transcription['id']}/evidence").json()
    assert [item["locator"] for item in evidence] == [
        {"type": "video_time_range", "start_ms": 0, "end_ms": 1500},
        {"type": "video_time_range", "start_ms": 2200, "end_ms": 3000},
    ]
    assert all(item["is_validated"] for item in evidence)

    found = client.get("/api/v1/search", params={"q": "量子计算"})
    assert any(item.get("id") == source_id or item.get("source_id") == source_id for item in found.json()["items"])
    # REQ-033a：转写是附加产物，版本完整性与处理状态保持视频分析的结论。
    assert services.repository.get_version(version_id)["completeness"] == "complete"
    assert services.repository.get_source(source_id)["processing_state"] == "succeeded"


def _summary_payload() -> dict:
    return {
        "summary": "本视频介绍量子计算的基本概念。",
        "suggested_domains": ["technical", "不存在的领域"],
        "suggested_genres": ["lecture", "podcast"],
        "suggested_tags": ["量子", "入门"],
    }


def test_summarize_job_cascade_tiers_and_visual_gap(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    source_id, version_id = _analyzed_video(client, services)
    _configure_ai(client)
    # 本用例手动逐步触发转写/摘要，关闭自动流水线避免串联作业干扰。
    assert client.put("/api/v1/settings/ai", json={"auto_pipeline": False}).status_code == 200
    monkeypatch.setattr(services.media_ai, "_audio_extractor", _fake_audio_extractor)
    monkeypatch.setattr(services.media_ai, "_transcription_caller", _fake_transcription_calls([]))
    assert client.post(f"/api/v1/videos/{source_id}/transcribe").status_code == 201
    assert services.jobs.run_once()["state"] == "succeeded"

    completion_calls: list[dict] = []

    def fake_completion(**kwargs):
        completion_calls.append(kwargs)
        content = kwargs["messages"][-1]["content"]
        if isinstance(content, list):  # 视觉调用
            count = sum(1 for item in content if item["type"] == "image_url")
            return _completion_response({"frames": [
                {"index": index, "description": f"演示画面{index}", "visible_text": f"量子位{index}"} for index in range(count)
            ]})
        return _completion_response(_summary_payload())

    monkeypatch.setattr(services.media_ai, "_completion_caller", fake_completion)

    # 转写覆盖率足够但尾部长静音 → 规则判不完整；未配置视觉模型 → tier1 + visual_gap。
    queued = client.post(f"/api/v1/videos/{source_id}/summarize", json={})
    assert queued.status_code == 201
    tier1 = services.jobs.run_once()
    assert tier1 is not None and tier1["kind"] == "video_summarize"
    assert tier1["state"] == "succeeded", tier1
    summaries = [
        item for item in client.get(f"/api/v1/documents/{version_id}/representations").json()
        if item["kind"] == "summary"
    ]
    assert len(summaries) == 1
    text = summaries[0]["text_content"]
    assert "完整性判断：可能不完整" in text
    marker = re.search(r"<!--yuanzhiku:suggestions (\{.*\}) -->", text)
    assert marker is not None
    suggestions = json.loads(marker.group(1))
    assert suggestions == {"domains": ["technical"], "genres": ["lecture"], "tags": ["量子", "入门"], "tier": 1, "visual_gap": True, "applied": True}
    assert text.count("<!--yuanzhiku:suggestions") == 1

    # 配置视觉模型后强制深度理解 → tier2 表示与 tier1 共存，最新一条为 tier2。
    client.put("/api/v1/settings/ai", json={"understand": {"vision_model": "qvq-vl"}})
    forced = client.post(f"/api/v1/videos/{source_id}/summarize", json={"force_tier2": True})
    assert forced.status_code == 201
    tier2 = services.jobs.run_once()
    assert tier2 is not None and tier2["state"] == "succeeded", tier2
    summaries = [
        item for item in client.get(f"/api/v1/documents/{version_id}/representations").json()
        if item["kind"] == "summary"
    ]
    assert len(summaries) == 2
    latest = summaries[-1]["text_content"]
    assert "画面理解：" in latest
    assert "量子位" in latest
    latest_suggestions = json.loads(re.search(r"<!--yuanzhiku:suggestions (\{.*\}) -->", latest).group(1))
    assert latest_suggestions["tier"] == 2
    assert latest_suggestions["visual_gap"] is False
    assert services.repository.get_version(version_id)["completeness"] == "complete"
    assert services.repository.get_source(source_id)["processing_state"] == "succeeded"


def test_summarize_job_requires_transcription_first(client_and_services) -> None:
    client, services = client_and_services
    source_id, _ = _analyzed_video(client, services)
    _configure_ai(client)
    assert client.post(f"/api/v1/videos/{source_id}/summarize").status_code == 201
    finished = services.jobs.run_once()
    assert finished is not None and finished["kind"] == "video_summarize"
    assert finished["state"] == "failed"
    assert finished["message"] == "请先完成语音转写"


def test_transcription_failure_keeps_version_state(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    source_id, version_id = _analyzed_video(client, services)
    _configure_ai(client)
    monkeypatch.setattr(services.media_ai, "_audio_extractor", _fake_audio_extractor)

    def leaking_transcription(**kwargs):
        raise RuntimeError(f"upstream {BASE_URL} rejected key {SECRET}")

    monkeypatch.setattr(services.media_ai, "_transcription_caller", leaking_transcription)
    assert client.post(f"/api/v1/videos/{source_id}/transcribe").status_code == 201
    job = None
    for _ in range(5):
        job = services.jobs.run_once()
        if job is not None and job["state"] == "failed":
            break
    assert job is not None and job["kind"] == "video_transcribe"
    assert job["state"] == "failed"
    assert job["message"] == "本地处理失败"
    assert SECRET not in json.dumps(job, ensure_ascii=False)
    # REQ-033a：失败同样不触碰版本完整性与处理状态。
    assert services.repository.get_version(version_id)["completeness"] == "complete"
    assert services.repository.get_source(source_id)["processing_state"] == "succeeded"


def test_credentials_excluded_from_backup_and_export(client_and_services) -> None:
    client, services = client_and_services
    _analyzed_video(client, services)
    _configure_ai(client)
    assert services.paths.ai_credentials_file.is_file()

    backup = client.post("/api/v1/backups")
    assert backup.status_code == 201, backup.text
    backup_path = next(services.paths.backups.glob(backup.json()["archive_name"]))
    exported = client.post("/api/v1/exports", json={"confirmed": True})
    assert exported.status_code == 201, exported.text
    export_path = Path(exported.json()["archive_path"])

    for archive_path in (backup_path, export_path):
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            assert not any("state/ai" in name or "credentials" in name for name in names)
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            assert "state/ai" in manifest["exclusions"]
            for name in names:
                assert SECRET not in archive.read(name).decode("utf-8", errors="ignore")


def _upload_video(client, **form: str) -> tuple[str, str]:
    data = {"rights": "owned", "title": "AI 视频", "domains": "[]", "genres": "[]", "tags": "[]"}
    data.update(form)
    uploaded = client.post(
        "/api/v1/videos/local",
        data=data,
        files={"file": ("sample.mp4", b"not-a-real-mp4", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["source"]["id"], uploaded.json()["content_version"]["id"]


def _queued_kinds(services) -> list[str]:
    chained = {"video_transcribe", "video_summarize", "source_classify"}
    return [job["kind"] for job in services.repository.list_jobs() if job["state"] == "queued" and job["kind"] in chained]


def _classify_audit_results(services, source_id: str) -> list[str]:
    with services.repository.connection() as connection:
        rows = connection.execute(
            "SELECT result FROM audit_events WHERE event_type='ai_classify_applied' AND entity_id=?",
            (source_id,),
        ).fetchall()
    return [row["result"] for row in rows]


def test_auto_pipeline_chains_video_jobs_and_applies_only_empty_fields(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    _configure_ai(client)
    # 用户已填领域与标签：领域绝不覆盖，标签并集合并，体裁空缺才填入。
    source_id, _ = _upload_video(client, domains='["technical"]', tags='["已有"]')
    monkeypatch.setattr(services.media_ai, "_audio_extractor", _fake_audio_extractor)
    monkeypatch.setattr(services.media_ai, "_transcription_caller", _fake_transcription_calls([]))
    monkeypatch.setattr(services.media_ai, "_completion_caller", lambda **kwargs: _completion_response(_summary_payload()))

    analyzed = services.jobs.run_once()
    assert analyzed is not None and analyzed["kind"] == "video_analyze" and analyzed["state"] == "succeeded"
    assert _queued_kinds(services) == ["video_transcribe"]

    transcribed = services.jobs.run_once()
    assert transcribed is not None and transcribed["kind"] == "video_transcribe" and transcribed["state"] == "succeeded"
    assert _queued_kinds(services) == ["video_summarize"]

    summarized = services.jobs.run_once()
    assert summarized is not None and summarized["kind"] == "video_summarize" and summarized["state"] == "succeeded"
    source = services.repository.get_source(source_id)
    assert json.loads(source["domains_json"]) == ["technical"]
    assert json.loads(source["genres_json"]) == ["lecture"]
    assert json.loads(source["tags_json"]) == sorted({"已有", "量子", "入门"})
    # 审计只记字段与数量，不含建议内容；摘要标记 applied 供前端隐藏采纳按钮。
    assert _classify_audit_results(services, source_id) == ["genres=1 tags=3"]


def test_auto_pipeline_toggle_off_disables_chaining(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    assert client.get("/api/v1/settings/ai").json()["auto_pipeline"] is True
    _configure_ai(client)
    switched = client.put("/api/v1/settings/ai", json={"auto_pipeline": False})
    assert switched.status_code == 200 and switched.json()["auto_pipeline"] is False
    assert client.get("/api/v1/settings/ai").json()["auto_pipeline"] is False

    _upload_video(client)
    analyzed = services.jobs.run_once()
    assert analyzed is not None and analyzed["state"] == "succeeded"
    assert "video_transcribe" not in {job["kind"] for job in services.repository.list_jobs()}


def test_auto_pipeline_unconfigured_ai_creates_no_chained_jobs(client_and_services) -> None:
    client, services = client_and_services
    _upload_video(client)
    analyzed = services.jobs.run_once()
    assert analyzed is not None and analyzed["state"] == "succeeded"
    kinds = {job["kind"] for job in services.repository.list_jobs()}
    # 未配置分组时不入队，也不会因串联产生 blocked 作业。
    assert not kinds.intersection({"video_transcribe", "video_summarize", "source_classify"})

    imported = client.post("/api/v1/imports/paste", json={"title": "无 AI", "text": "正文", "rights": "owned"})
    assert imported.status_code == 201, imported.text
    parsed = services.jobs.run_once()
    assert parsed is not None and parsed["kind"] == "parse" and parsed["state"] == "succeeded"
    kinds = {job["kind"] for job in services.repository.list_jobs()}
    assert not kinds.intersection({"video_transcribe", "video_summarize", "source_classify"})


def test_auto_apply_noop_when_suggestions_empty(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    _configure_ai(client)
    source_id, _ = _upload_video(client)
    monkeypatch.setattr(services.media_ai, "_audio_extractor", _fake_audio_extractor)
    monkeypatch.setattr(services.media_ai, "_transcription_caller", _fake_transcription_calls([]))
    monkeypatch.setattr(services.media_ai, "_completion_caller", lambda **kwargs: _completion_response({
        "summary": "只有摘要，没有建议。",
        "suggested_domains": [],
        "suggested_genres": [],
        "suggested_tags": [],
    }))
    for expected_kind in ("video_analyze", "video_transcribe", "video_summarize"):
        finished = services.jobs.run_once()
        assert finished is not None and finished["kind"] == expected_kind and finished["state"] == "succeeded"
    source = services.repository.get_source(source_id)
    assert source["domains_json"] == "[]" and source["genres_json"] == "[]" and source["tags_json"] == "[]"
    # 无可填内容：除导入时的初始修订外不产生新元数据修订，也不写审计。
    assert len(services.repository.metadata_revisions_for_source(source_id)) == 1
    assert _classify_audit_results(services, source_id) == []


def test_parse_chains_source_classify_and_applies(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    _configure_ai(client)
    imported = client.post("/api/v1/imports/paste", json={"title": "量子科普", "text": "量子计算入门正文", "rights": "owned"})
    assert imported.status_code == 201, imported.text
    source_id = imported.json()["source"]["id"]
    version_id = imported.json()["content_version"]["id"]
    captured: list[dict] = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        return _completion_response({"domains": ["technical", "不存在的领域"], "genres": ["lecture", "podcast"], "tags": ["量子", "量子", "科普"]})

    monkeypatch.setattr(services.media_ai, "_completion_caller", fake_completion)

    parsed = services.jobs.run_once()
    assert parsed is not None and parsed["kind"] == "parse" and parsed["state"] == "succeeded"
    assert _queued_kinds(services) == ["source_classify"]

    classified = services.jobs.run_once()
    assert classified is not None and classified["kind"] == "source_classify"
    assert classified["state"] == "succeeded", classified
    assert classified["message"] == "AI 分类完成"
    source = services.repository.get_source(source_id)
    assert json.loads(source["domains_json"]) == ["technical"]
    assert json.loads(source["genres_json"]) == ["lecture"]
    assert json.loads(source["tags_json"]) == ["科普", "量子"]
    assert _classify_audit_results(services, source_id) == ["domains=1 genres=1 tags=2"]
    # 分类提示词含正文与分类清单。
    prompt = captured[0]["messages"][-1]["content"]
    assert "量子计算入门正文" in prompt and "technical" in prompt
    # REQ-033a：分类是附加产物，版本完整性与处理状态保持解析结论。
    assert services.repository.get_version(version_id)["completeness"] == "complete"
    assert source["processing_state"] == "succeeded"


def test_source_classify_blocked_without_understand_group(client_and_services) -> None:
    client, services = client_and_services
    imported = client.post("/api/v1/imports/paste", json={"title": "待分类", "text": "正文", "rights": "owned"})
    assert imported.status_code == 201, imported.text
    parsed = services.jobs.run_once()
    assert parsed is not None and parsed["kind"] == "parse" and parsed["state"] == "succeeded"
    assert "source_classify" not in _queued_kinds(services)

    source = imported.json()["source"]
    version = imported.json()["content_version"]
    services.repository.create_job("source_classify", source["id"], version["id"], version["artifact_sha256"], None, {}, priority=100)
    finished = services.jobs.run_once()
    assert finished is not None and finished["kind"] == "source_classify"
    assert finished["state"] == "blocked"
    assert finished["message"] == "未配置媒体 AI 服务"


def test_source_classify_failure_keeps_source_state(client_and_services, monkeypatch: pytest.MonkeyPatch) -> None:
    client, services = client_and_services
    _configure_ai(client)
    imported = client.post("/api/v1/imports/paste", json={"title": "分类失败", "text": "正文", "rights": "owned"})
    assert imported.status_code == 201, imported.text
    source_id = imported.json()["source"]["id"]
    version_id = imported.json()["content_version"]["id"]

    def leaking_completion(**kwargs):
        raise RuntimeError(f"upstream {BASE_URL} rejected key {SECRET}")

    monkeypatch.setattr(services.media_ai, "_completion_caller", leaking_completion)
    parsed = services.jobs.run_once()
    assert parsed is not None and parsed["kind"] == "parse" and parsed["state"] == "succeeded"
    assert _queued_kinds(services) == ["source_classify"]

    job = None
    for _ in range(5):
        job = services.jobs.run_once()
        if job is not None and job["state"] == "failed":
            break
    assert job is not None and job["kind"] == "source_classify"
    assert job["state"] == "failed"
    assert job["message"] == "本地处理失败"
    assert SECRET not in json.dumps(job, ensure_ascii=False)
    # REQ-033a：失败同样不触碰版本完整性与处理状态，元数据保持空缺。
    assert services.repository.get_version(version_id)["completeness"] == "complete"
    source = services.repository.get_source(source_id)
    assert source["processing_state"] == "succeeded"
    assert source["domains_json"] == "[]" and source["genres_json"] == "[]" and source["tags_json"] == "[]"
    assert _classify_audit_results(services, source_id) == []


def test_classify_clamps_values_to_taxonomy(tmp_path: Path) -> None:
    settings = _ai_settings(ai_understand_provider="openai_compatible")

    def fake_completion(**kwargs):
        return _completion_response({
            "domains": ["technical", "不存在的领域", 42],
            "genres": ["lecture", "podcast"],
            "tags": ["AI", "", "长" * 30, "AI"],
        })

    adapter = _adapter(tmp_path, settings, {"understand": SECRET}, completion_caller=fake_completion)
    result = adapter.classify("正文内容", {"title": "标题"})
    assert result == {"domains": ["technical"], "genres": ["lecture"], "tags": ["AI", "长" * 20]}


def test_classify_prompt_truncates_long_text(tmp_path: Path) -> None:
    settings = _ai_settings(ai_understand_provider="openai_compatible")
    captured: list[dict] = []

    def fake_completion(**kwargs):
        captured.append(kwargs)
        return _completion_response({"domains": [], "genres": [], "tags": []})

    adapter = _adapter(tmp_path, settings, {"understand": SECRET}, completion_caller=fake_completion)
    result = adapter.classify("正" * 20_000, {})
    assert result == {"domains": [], "genres": [], "tags": []}
    prompt = captured[0]["messages"][-1]["content"]
    assert "正" * 8000 in prompt
    assert "正" * 8001 not in prompt
