"""T-INT-001：本地全链路集成测试（REQ-045）。

TestClient 全链路：导入 → 解析 → 证据 → 引用 → 知识发布 → 检索 → 备份 →
导出 → 再导入 → 生命周期清理。数据根落在 tests/runtime/（集成纪律），
不触碰日常数据目录；真实 compose 集成测试需 Docker 环境，另行编写。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import compose_data_root
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
        "categories": ["technical"], "tags": ["集成"],
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
