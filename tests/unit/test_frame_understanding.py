"""帧级画面理解（v1.7 REQ-057，T-FRAME-001）：兜底/增强分支、联络表调用
与 visual_understanding 证据落库。全部使用 fake 适配器替身，绝不触网。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from app.adapters.video_ai import QwenVideoAdapter, RelayClient
from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, VideoMetadata
from app.main import create_app

RUN_ROOT = Path(__file__).resolve().parents[1] / "runtime" / "unit-frame-understanding"
BASE_URL = "https://api.example.com/v1"
SECRET = "frame-secret-8888"


class FakeMediaAnalyzer:
    def capability(self) -> dict[str, object]:
        return {"enabled": True, "adapter": "unit", "network": False}

    def config_hash(self, maximum_frames: int) -> str:
        return hashlib.sha256(f"unit:{maximum_frames}".encode("ascii")).hexdigest()

    def probe(self, artifact_path: Path, limits: MediaProcessingLimits, cancelled, heartbeat) -> VideoMetadata:
        return VideoMetadata("mov,mp4,m4a,3gp,3g2,mj2", 10_000, 320, 180, "h264", "aac")

    def extract_frames(self, artifact_path, metadata, workspace, maximum_frames, limits, cancelled, heartbeat, transcript_segments=None):
        frames: list[ExtractedVideoFrame] = []
        for ordinal in range(min(maximum_frames, 2)):
            path = workspace / f"frame-{ordinal}.jpg"
            path.write_bytes(b"synthetic-jpeg-frame-" + bytes([ordinal]))
            frames.append(ExtractedVideoFrame(ordinal, (ordinal + 1) * 3_000, path, 320, 180))
        return tuple(frames)


class FakeVideoAdapter:
    """直送/帧理解替身：可分别配置直送失败与帧理解返回条目。"""

    def __init__(self, *, video_input: bool = True, image_input: bool = True, fail_video: bool = False) -> None:
        self.video_input = video_input
        self.image_input = image_input
        self.fail_video = fail_video
        self.frame_calls: list[list[tuple[int, int]]] = []

    def capability(self) -> dict[str, object]:
        return {"video_input": self.video_input, "image_input": self.image_input, "max_bytes": 1024, "audio_in_video": True, "reencode": False}

    def config_hash(self) -> str:
        return "fake-video-adapter-hash"

    def understand_video(self, video_path, transcript_text, focus, cancelled):
        if self.fail_video:
            raise RuntimeError("视频直送不可行")
        return {"summary": "直送摘要", "supplements": [], "suggested_domains": [], "suggested_genres": [], "suggested_tags": []}

    def understand_frames(self, sheet_image, cells, transcript_text, cancelled):
        assert sheet_image.is_file() and sheet_image.read_bytes()
        self.frame_calls.append(list(cells))
        entries = []
        for index, (start_ms, end_ms) in enumerate(cells[:2]):
            entries.append({
                "start_ms": start_ms, "end_ms": end_ms, "time_ms": start_ms,
                "description": f"画面要点{index + 1}",
                "visible_text": "2026 销量图表" if index == 0 else "",
            })
        return entries


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


from fastapi.testclient import TestClient  # noqa: E402  （夹具定义之上导入会干扰可读性，保持与既有测试一致的布局）


def _configure_ai(client) -> None:
    response = client.put("/api/v1/settings/ai", json={
        "transcribe": {"provider": "openai_compatible", "base_url": BASE_URL, "model": "whisper-1", "api_key": SECRET},
        "understand": {"provider": "openai_compatible", "base_url": BASE_URL, "chat_model": "qwen-plus", "api_key": SECRET},
        "timeout_seconds": 600,
    })
    assert response.status_code == 200, response.text


def _upload_video(client, **form: str) -> tuple[str, str]:
    data = {"rights": "owned", "title": "帧理解视频", "domains": "[]", "genres": "[]", "tags": "[]"}
    data.update(form)
    uploaded = client.post(
        "/api/v1/videos/local",
        data=data,
        files={"file": ("sample.mp4", b"not-a-real-mp4", "video/mp4")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return uploaded.json()["source"]["id"], uploaded.json()["content_version"]["id"]


def _jobs_audio_extractor(artifact_path, workspace, limits, cancelled) -> list:
    chunk = workspace / "chunk-000.mp3"
    chunk.write_bytes(b"audio-one")
    return [(chunk, 0, 2_000)]


def _fake_transcription_calls():
    def fake(**kwargs):
        return {"segments": [{"start": 0.0, "end": 1.5, "text": "量子计算入门讲解"}]}

    return fake


def _summary_payload() -> dict:
    return {
        "summary": "本视频介绍量子计算的基本概念。",
        "suggested_domains": ["technical"],
        "suggested_genres": ["lecture"],
        "suggested_tags": ["量子"],
    }


def _completion_response(payload: dict):
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def _fake_sheet_builder(tmp_path: Path, calls: list[list[int]]):
    def build(video_path, cell_times_ms, workspace, limits, cancelled, *, ffmpeg, heartbeat):
        sheet = tmp_path / "contact-sheet.jpg"
        sheet.write_bytes(b"fake-sheet-jpeg-bytes")
        calls.append(list(cell_times_ms))
        return sheet

    return build


def _transcribe_and_analyze(client, services, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """入库（双入队）→ 转写 → 分析，全部成功；分析成功链式摘要。"""
    source_id, version_id = _upload_video(client)
    monkeypatch.setattr("app.services.jobs.extract_audio_chunks", _jobs_audio_extractor)
    monkeypatch.setattr(services.api_transcriber, "_transcription_caller", _fake_transcription_calls())
    monkeypatch.setattr(services.media_ai, "_completion_caller", lambda **kwargs: _completion_response(_summary_payload()))
    transcribed = services.jobs.run_once()
    assert transcribed is not None and transcribed["kind"] == "video_transcribe" and transcribed["state"] == "succeeded"
    analyzed = services.jobs.run_once()
    assert analyzed is not None and analyzed["kind"] == "video_analyze" and analyzed["state"] == "succeeded"
    return source_id, version_id


def test_frame_fallback_rescues_infeasible_direct(client_and_services, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """直送不可行 + 兜底开关默认开 + image_input 可行 → 联络表帧理解接管：
    摘要 tier2、frame_fallback 标记、visual_understanding 逐条时间定位证据、
    visual_gap 收窄为 False。"""
    client, services = client_and_services
    _configure_ai(client)
    builder_calls: list[list[int]] = []
    monkeypatch.setattr("app.services.jobs.build_contact_sheet", _fake_sheet_builder(tmp_path, builder_calls))
    adapter = FakeVideoAdapter(fail_video=True)
    monkeypatch.setattr(services.jobs, "video_adapter_provider", lambda: adapter)
    source_id, version_id = _transcribe_and_analyze(client, services, monkeypatch)

    summarized = services.jobs.run_once()
    assert summarized is not None and summarized["kind"] == "video_summarize" and summarized["state"] == "succeeded", summarized

    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    by_kind = {item["kind"]: item for item in representations}
    summary = by_kind["summary"]
    text = summary["text_content"]
    assert "补充理解方式：视频直送不可行，已按关键帧联络表补充画面理解" in text
    assert "画面理解：" in text and "画面要点1" in text and "（画面文字：2026 销量图表）" in text
    marker = json.loads(re.search(r"<!--yuanzhiku:suggestions (\{.*\}) -->", text).group(1))
    assert marker == {
        "domains": ["technical"], "genres": ["lecture"], "tags": ["量子"],
        "tier": 2, "visual_gap": False, "video_direct": False,
        "frame_fallback": True, "enriched": False, "applied": True,
    }

    # visual_understanding：独立表示（父链挂转写）、逐条 video_time_range 证据。
    visual = by_kind["visual_understanding"]
    visual_row = services.repository.get_representation(visual["id"])
    assert visual_row is not None and visual_row["parent_representation_id"] == by_kind["transcription"]["id"]
    rows = services.repository.evidence_for_representation(visual["id"])
    assert [json.loads(row["locator_json"])["type"] for row in rows] == ["video_time_range"] * 2

    # 格子时间窗：升序、连续、覆盖到片尾（候选 = 分析帧 + 转写锚点）。
    assert adapter.frame_calls, "联络表必须被构建并调用"
    windows = adapter.frame_calls[0]
    assert windows == sorted(windows) and all(start < end for start, end in windows)

    # 瞬态联络表绝不入 video_frames：分析帧保持 2 帧。
    analyses = services.repository.list_video_analyses(version_id)
    assert len(analyses[0]["frames"]) == 2


def test_frame_fallback_disabled_keeps_visual_gap(client_and_services, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """兜底开关关闭：直送不可行时回到 v1.6 语义（tier1 + visual_gap），无
    visual_understanding 表示、不构建联络表。"""
    client, services = client_and_services
    _configure_ai(client)
    assert client.put("/api/v1/settings/ai", json={"video": {"frames_fallback": "off"}}).status_code == 200
    builder_calls: list[list[int]] = []
    monkeypatch.setattr("app.services.jobs.build_contact_sheet", _fake_sheet_builder(tmp_path, builder_calls))
    monkeypatch.setattr(services.jobs, "video_adapter_provider", lambda: FakeVideoAdapter(fail_video=True))
    source_id, version_id = _transcribe_and_analyze(client, services, monkeypatch)

    summarized = services.jobs.run_once()
    assert summarized is not None and summarized["state"] == "succeeded", summarized

    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    kinds = {item["kind"] for item in representations}
    assert "visual_understanding" not in kinds
    marker = json.loads(re.search(r"<!--yuanzhiku:suggestions (\{.*\}) -->", [i for i in representations if i["kind"] == "summary"][-1]["text_content"]).group(1))
    assert marker["visual_gap"] is True and marker["frame_fallback"] is False and marker["tier"] == 1
    assert builder_calls == []


def test_frame_enrichment_runs_on_complete_transcript(client_and_services, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """转写完整 + 增强开关开 → 联络表画面补充（tier 1.5，enriched 标记）。"""
    client, services = client_and_services
    _configure_ai(client)
    assert client.put("/api/v1/settings/ai", json={"video": {"frames_enrich": "on"}}).status_code == 200
    builder_calls: list[list[int]] = []
    monkeypatch.setattr("app.services.jobs.build_contact_sheet", _fake_sheet_builder(tmp_path, builder_calls))
    adapter = FakeVideoAdapter()
    monkeypatch.setattr(services.jobs, "video_adapter_provider", lambda: adapter)
    source_id, version_id = _transcribe_and_analyze(client, services, monkeypatch)
    # 完整性判定固定为 complete（绕过规则层与 LLM 判定的随机性）。
    monkeypatch.setattr(services.media_ai, "assess_completeness", lambda text, context: {
        "verdict": "complete", "confidence": 0.95, "missing_aspects": [], "reason": "覆盖充分", "rule_triggered": False,
    })

    summarized = services.jobs.run_once()
    assert summarized is not None and summarized["kind"] == "video_summarize" and summarized["state"] == "succeeded", summarized

    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    by_kind = {item["kind"]: item for item in representations}
    marker = json.loads(re.search(r"<!--yuanzhiku:suggestions (\{.*\}) -->", by_kind["summary"]["text_content"]).group(1))
    assert marker["tier"] == 1.5 and marker["enriched"] is True and marker["visual_gap"] is False
    assert "补充理解方式：画面理解增强（关键帧联络表）" in by_kind["summary"]["text_content"]
    assert "visual_understanding" in by_kind
    assert adapter.frame_calls


def test_understand_frames_drops_out_of_range_cells(tmp_path: Path) -> None:
    """模型引用越界/非法格子号的条目一律丢弃；全部无效时按失败处理（绝不伪造）。"""
    settings = {"ai_video_provider": "qwen", "ai_video_model": "", "ai_understand_provider": "off"}
    credentials = {"video_qwen": SECRET}
    relay = RelayClient(lambda: settings, lambda: credentials)
    adapter = QwenVideoAdapter(
        lambda: settings, lambda: credentials, relay,
        completion_caller=lambda **kwargs: _completion_response({
            "moments": [
                {"cell": 99, "content": "越界条目"},
                {"cell": 0, "content": "零号条目"},
                {"cell": "bad", "content": "非法条目"},
                {"cell": 2, "content": "有效条目", "visible_text": "画面文字"},
            ],
        }),
    )
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"fake-sheet")
    cells = [(1_000, 2_000), (2_000, 3_000)]
    entries = adapter.understand_frames(sheet, cells, "转写文本", lambda: False)
    assert entries == [{
        "start_ms": 2_000, "end_ms": 3_000, "time_ms": 2_000,
        "description": "有效条目", "visible_text": "画面文字",
    }]

    empty = QwenVideoAdapter(
        lambda: settings, lambda: credentials, relay,
        completion_caller=lambda **kwargs: _completion_response({"moments": [{"cell": 99, "content": "越界"}]}),
    )
    with pytest.raises(RuntimeError):
        empty.understand_frames(sheet, cells, "转写文本", lambda: False)
