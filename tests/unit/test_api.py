from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def runtime_root() -> Path:
    root = Path(__file__).resolve().parents[1] / "runtime" / "pytest-api"
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


def import_and_run(client: TestClient) -> dict:
    response = client.post("/api/v1/imports/paste", json={
        "title": "合成中文来源", "text": "# 测试标题\n\n这是一段用于证据和检索的合成中文文本。", "rights": "owned",
        "categories": ["technical"], "tags": ["测试", "证据"],
    })
    assert response.status_code == 201, response.text
    result = response.json()
    run = client.post("/api/v1/jobs/run-once")
    assert run.status_code == 200, run.text
    return result


def test_health_openapi_and_paste_evidence_chain(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"
    schema = client.get("/openapi.json").json()
    assert "/api/v1/imports/paste" in schema["paths"]
    imported = import_and_run(client)
    source_id = imported["source"]["id"]
    source = client.get(f"/api/v1/sources/{source_id}")
    assert source.status_code == 200
    assert "categories_json" not in source.json()
    version_id = source.json()["versions"][0]["id"]
    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    assert representations[-1]["parser_name"] == "native-utf8"
    evidence = client.get(f"/api/v1/representations/{representations[-1]['id']}/evidence").json()[0]
    assert evidence["artifact_sha256"] == imported["artifact"]["sha256"]
    assert evidence["content_version_id"] == version_id
    assert evidence["locator"]["type"] == "text_range"


def test_knowledge_publish_requires_evidence(client: TestClient) -> None:
    no_evidence = client.post("/api/v1/knowledge", json={"kind": "fact", "statement": "没有证据的事实"}).json()
    blocked = client.post(f"/api/v1/knowledge/{no_evidence['id']}/publish")
    assert blocked.status_code == 422
    imported = import_and_run(client)
    version_id = imported["content_version"]["id"]
    representation = client.get(f"/api/v1/documents/{version_id}/representations").json()[0]
    evidence_id = client.get(f"/api/v1/representations/{representation['id']}/evidence").json()[0]["id"]
    knowledge = client.post("/api/v1/knowledge", json={"kind": "fact", "statement": "有证据的事实", "evidence_ids": [evidence_id]})
    assert client.post(f"/api/v1/knowledge/{knowledge.json()['id']}/publish").json()["status"] == "published"


def test_external_douyin_is_literal_only(client: TestClient) -> None:
    rejected = client.post("/api/v1/external/douyin", json={"title": "错误", "url": "http://www.douyin.com/a"})
    assert rejected.status_code == 422
    exact = "https://www.douyin.com/video/123?from=test"
    created = client.post("/api/v1/external/douyin", json={"title": "参考", "url": exact, "tags": ["视频"]})
    assert created.status_code == 201
    assert created.json()["url"] == exact


def test_lifecycle_backup_export_restore_and_verify(client: TestClient, runtime_root: Path) -> None:
    imported = import_and_run(client)
    source_id = imported["source"]["id"]
    assert client.post("/api/v1/verify", json={"full": True}).json()["valid"]
    backup = client.post("/api/v1/backups").json()
    assert backup["state"] == "succeeded"
    restore_root = runtime_root.parent / "pytest-restored"
    if restore_root.exists():
        shutil.rmtree(restore_root)
    restored = client.post(f"/api/v1/backups/{backup['id']}/restore", json={"target_data_root": str(restore_root)})
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored_artifacts"] == 1
    exported = client.post("/api/v1/exports", json={"confirmed": True})
    assert exported.status_code == 201
    assert client.post(f"/api/v1/sources/{source_id}/delete").status_code == 200
    assert client.post(f"/api/v1/sources/{source_id}/purge").json()["purged"] is True
    shutil.rmtree(restore_root)


def test_search_and_file_import(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/imports/file",
        data={"rights": "open_license", "title": "合成文件", "categories": '["document"]', "tags": '["样本"]', "language": "zh"},
        files={"file": ("synthetic.md", "# 合成文件\n\n用于本地文件导入和搜索的文本。".encode("utf-8"), "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    client.post("/api/v1/jobs/run-once")
    output = client.get("/api/v1/search", params={"q": "本地文件"}).json()
    assert any(item["id"] == uploaded.json()["source"]["id"] for item in output["items"])


def test_manual_representation_and_reimport(client: TestClient, runtime_root: Path) -> None:
    imported = import_and_run(client)
    version_id = imported["content_version"]["id"]
    original = client.get(f"/api/v1/documents/{version_id}/representations").json()[0]
    revised = client.post(
        f"/api/v1/documents/{version_id}/representations/manual",
        json={"text": "人工修订后的本地表示。"},
    )
    assert revised.status_code == 201
    assert revised.json()["representation"]["parent_representation_id"] == original["id"]
    citation = client.get(f"/api/v1/citations/{revised.json()['citation']['id']}").json()
    assert citation["human_revised"] is True
    exported = client.post("/api/v1/exports", json={"confirmed": True}).json()
    imported_again = client.post("/api/v1/reimports", json={"archive_path": exported["archive_path"]}).json()
    assert imported_again["imported"] is True
    assert imported_again["report"]["inserted_records"] == 0
