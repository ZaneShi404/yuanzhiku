"""T-INT-001：本地全链路集成测试（REQ-045）。

TestClient 全链路：导入 → 解析 → 证据 → 引用 → 知识发布 → 检索 → 备份 →
导出 → 再导入 → 生命周期清理。数据根落在 tests/runtime/（集成纪律），
不触碰日常数据目录；真实 compose 集成测试需 Docker 环境，另行编写。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import compose_data_root
from app.domain.media import ExtractedVideoFrame, MediaTranscript, MediaTranscriptSegment, VideoMetadata
from app.main import create_app


@pytest.fixture()
def runtime_root() -> Path:
    isolated_root = os.environ.get("YUANZHIKU_TEST_RUNTIME")
    root = (
        Path(isolated_root) / "integration" / "local-full-chain"
        if isolated_root
        else Path(__file__).resolve().parents[1] / "runtime" / "integration-local-full-chain"
    )
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    yield root
    if root.exists():
        shutil.rmtree(root)


@pytest.fixture()
def client(runtime_root: Path):
    app = create_app(runtime_root, acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def test_compose_data_root_guard() -> None:
    accepted = compose_data_root(Path(__file__).resolve().parents[1] / "runtime" / "compose-guard-check")
    assert accepted.name.startswith("compose-")
    with pytest.raises(ValueError):
        compose_data_root(Path(__file__).resolve().parents[2] / "data")
    with pytest.raises(ValueError):
        compose_data_root(Path(__file__).resolve().parents[1] / "runtime" / "not-compose-prefixed")
    with pytest.raises(ValueError):
        compose_data_root("")


def test_local_full_chain(client: TestClient) -> None:
    # 导入（粘贴文本）→ 解析作业
    imported = client.post("/api/v1/imports/paste", json={
        "title": "集成合成来源", "text": "# 集成测试\n\n这是用于全链路集成验证的合成中文文本。", "rights": "owned",
        "domains": ["technical"], "tags": ["集成"],
    })
    assert imported.status_code == 201, imported.text
    source_id = imported.json()["source"]["id"]
    run = client.post("/api/v1/jobs/run-once")
    assert run.status_code == 200, run.text

    # 表示与证据
    source = client.get(f"/api/v1/sources/{source_id}")
    assert source.status_code == 200, source.text
    version_id = source.json()["versions"][0]["id"]
    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    assert representations, "解析后应有 extraction representation"
    evidence = client.get(f"/api/v1/representations/{representations[-1]['id']}/evidence").json()
    assert evidence and evidence[0]["is_validated"], "证据应已验证"

    # 引用（含 REQ-023 后端字段：来源状态与定位动作）
    created = client.post(f"/api/v1/citations?evidence_id={evidence[0]['id']}")
    assert created.status_code == 201, created.text
    detail = client.get(f"/api/v1/citations/{created.json()['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["processing_state"]
    assert detail.json()["location_action"]["evidence_id"] == evidence[0]["id"]
    assert len(detail.json()["context"]) <= 300

    # 知识创建与发布（fact 需有效证据）
    knowledge = client.post("/api/v1/knowledge", json={
        "kind": "fact", "statement": "集成测试事实陈述", "evidence_ids": [evidence[0]["id"]],
    })
    assert knowledge.status_code == 201, knowledge.text
    published = client.post(f"/api/v1/knowledge/{knowledge.json()['id']}/publish")
    assert published.status_code == 200, published.text
    listed = client.get("/api/v1/knowledge", params={"published_only": True}).json()
    assert any(item["statement"] == "集成测试事实陈述" for item in listed)

    # 检索命中（默认范围）
    found = client.get("/api/v1/search", params={"q": "全链路集成验证"})
    assert found.status_code == 200, found.text
    assert any(item.get("source_id") == source_id or item.get("id") == source_id for item in found.json()["items"])

    # 备份 → 导出 → 再导入（幂等不覆盖）
    backup = client.post("/api/v1/backups")
    assert backup.status_code == 201, backup.text
    exported = client.post("/api/v1/exports", json={"confirmed": True})
    assert exported.status_code == 201, exported.text
    reimported = client.post("/api/v1/reimports", json={"archive_path": exported.json()["archive_path"]})
    assert reimported.status_code == 200, reimported.text
    assert reimported.json()["report"]["conflicts"] == []

    # 生命周期：软删除 → 恢复 → 软删除 → 永久删除
    assert client.post(f"/api/v1/sources/{source_id}/delete").status_code == 200
    assert client.post(f"/api/v1/sources/{source_id}/restore").status_code == 200
    assert client.post(f"/api/v1/sources/{source_id}/delete").status_code == 200
    purged = client.post(f"/api/v1/sources/{source_id}/purge")
    assert purged.status_code == 200, purged.text
    assert client.get(f"/api/v1/sources/{source_id}").status_code == 404


class _FakeMediaAnalyzer:
    def capability(self) -> dict[str, object]:
        return {"enabled": True, "adapter": "integration-fake", "network": False}

    def config_hash(self, maximum_frames: int) -> str:
        return hashlib.sha256(f"integration-fake:{maximum_frames}".encode("ascii")).hexdigest()

    def probe(self, artifact_path, limits, cancelled, heartbeat) -> VideoMetadata:
        return VideoMetadata("mov,mp4,m4a,3gp,3g2,mj2", 10_000, 320, 180, "h264", "aac")

    def extract_frames(self, artifact_path, metadata, workspace, maximum_frames, limits, cancelled, heartbeat):
        frames: list[ExtractedVideoFrame] = []
        for ordinal in range(min(maximum_frames, 2)):
            path = workspace / f"frame-{ordinal}.jpg"
            path.write_bytes(b"synthetic-jpeg-" + bytes([ordinal]))
            frames.append(ExtractedVideoFrame(ordinal, (ordinal + 1) * 3_000, path, 320, 180))
        return tuple(frames)


class _FakeMediaAi:
    """集成用假 AI 端口：全部边界本地确定性返回，绝不触网。"""

    def capability(self) -> dict[str, object]:
        return {
            "enabled": True,
            "transcribe_enabled": True,
            "understand_enabled": True,
            "tier2_enabled": False,
            "network": True,
            "provider": "integration-fake",
        }

    def config_hash(self, operation: str) -> str:
        return hashlib.sha256(f"integration-fake-ai:{operation}:1".encode("ascii")).hexdigest()

    def transcribe(self, artifact_path, media_type, cancelled) -> MediaTranscript:
        assert not cancelled()
        return MediaTranscript(
            "整链路转写术语覆盖\n第二部分讲解",
            (
                MediaTranscriptSegment("整链路转写术语覆盖", 0, 1_500),
                MediaTranscriptSegment("第二部分讲解", 1_500, 3_000),
            ),
        )

    def assess_completeness(self, transcript_text, context) -> dict:
        return {"verdict": "complete", "confidence": 0.9, "missing_aspects": [], "reason": "覆盖充分", "rule_triggered": False}

    def describe_frames(self, frame_inputs, focus, cancelled=None) -> list[dict]:
        return [{"time_ms": int(item.get("time_ms") or 0), "description": "画面", "visible_text": ""} for item in frame_inputs]

    def summarize(self, inputs, cancelled) -> dict:
        assert not cancelled()
        return {
            "summary": "整链路摘要：视频讲解整链路转写术语。",
            "suggested_domains": ["technical"],
            "suggested_genres": ["lecture"],
            "suggested_tags": ["集成"],
        }


def test_local_full_chain_with_media_ai(runtime_root: Path) -> None:
    """视频导入 → 本地分析 → AI 转写 → AI 摘要 → 检索命中转写术语（假 AI 端口）。"""
    os.environ["YUANZHIKU_EMBEDDED_WORKER"] = "false"
    try:
        app = create_app(runtime_root, acquire_lock=False)
        services = app.state.services
        services.videos.analyzer = _FakeMediaAnalyzer()
        services.media_ai = _FakeMediaAi()
        services.jobs.media_ai = services.media_ai
        with TestClient(app) as client:
            uploaded = client.post(
                "/api/v1/videos/local",
                data={"rights": "owned", "title": "整链路视频", "domains": "[]", "genres": "[]", "tags": "[]"},
                files={"file": ("chain.mp4", b"chain-video", "video/mp4")},
            )
            assert uploaded.status_code == 201, uploaded.text
            source_id = uploaded.json()["source"]["id"]
            version_id = uploaded.json()["content_version"]["id"]

            analyzed = client.post("/api/v1/jobs/run-once").json()["job"]
            assert analyzed["kind"] == "video_analyze" and analyzed["state"] == "succeeded"

            assert client.post(f"/api/v1/videos/{source_id}/transcribe").status_code == 201
            transcribed = client.post("/api/v1/jobs/run-once").json()["job"]
            assert transcribed["kind"] == "video_transcribe" and transcribed["state"] == "succeeded"

            assert client.post(f"/api/v1/videos/{source_id}/summarize", json={}).status_code == 201
            summarized = client.post("/api/v1/jobs/run-once").json()["job"]
            assert summarized["kind"] == "video_summarize" and summarized["state"] == "succeeded"

            representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
            kinds = [item["kind"] for item in representations]
            assert "transcription" in kinds and "summary" in kinds
            summary = representations[-1]
            assert summary["kind"] == "summary"
            assert "<!--yuanzhiku:suggestions" in summary["text_content"]

            found = client.get("/api/v1/search", params={"q": "整链路转写术语"})
            assert found.status_code == 200
            assert any(item.get("id") == source_id or item.get("source_id") == source_id for item in found.json()["items"])
            # REQ-033a：AI 附加产物完成后版本状态保持视频分析结论。
            assert services.repository.get_version(version_id)["completeness"] == "complete"
            assert services.repository.get_source(source_id)["processing_state"] == "succeeded"

            # 导出 → 再导入：派生证据链校验不拒绝 transcription/summary 表示。
            exported = client.post("/api/v1/exports", json={"confirmed": True})
            assert exported.status_code == 201, exported.text
            reimported = client.post("/api/v1/reimports", json={"archive_path": exported.json()["archive_path"]})
            assert reimported.status_code == 200, reimported.text
            assert reimported.json()["report"]["conflicts"] == []
    finally:
        os.environ.pop("YUANZHIKU_EMBEDDED_WORKER", None)
