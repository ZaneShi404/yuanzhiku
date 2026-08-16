from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "topics-relations"


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def client(runtime_root: Path):
    app = create_app(runtime_root, acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def paste(client: TestClient, title: str, text: str) -> dict:
    response = client.post("/api/v1/imports/paste", json={"title": title, "text": text, "rights": "owned"})
    assert response.status_code == 201, response.text
    return response.json()


def test_topic_rename_duplicate_delete_and_membership(client: TestClient) -> None:
    source_id = paste(client, "主题成员来源", "正文内容一")["source"]["id"]
    created = client.post("/api/v1/topics", json={"name": "主题甲", "source_ids": [source_id]})
    assert created.status_code == 201, created.text
    topic_id = created.json()["id"]

    renamed = client.put(f"/api/v1/topics/{topic_id}", json={"name": "主题乙"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "主题乙"

    other_id = client.post("/api/v1/topics", json={"name": "主题丙"}).json()["id"]
    conflict = client.put(f"/api/v1/topics/{other_id}", json={"name": "主题乙"})
    assert conflict.status_code == 409
    assert client.put("/api/v1/topics/does-not-exist", json={"name": "无"}).status_code == 404

    assert client.delete(f"/api/v1/topics/{topic_id}/sources/{source_id}").status_code == 204
    assert client.delete(f"/api/v1/topics/{topic_id}/sources/{source_id}").status_code == 404
    topics = client.get("/api/v1/topics").json()
    assert next(item for item in topics if item["id"] == topic_id)["source_ids"] == []

    assert client.delete(f"/api/v1/topics/{topic_id}").status_code == 204
    assert client.delete(f"/api/v1/topics/{topic_id}").status_code == 404
    assert all(item["id"] != topic_id for item in client.get("/api/v1/topics").json())


def test_topic_delete_removes_membership_rows(client: TestClient) -> None:
    source_id = paste(client, "级联删除来源", "正文内容二")["source"]["id"]
    topic_id = client.post("/api/v1/topics", json={"name": "级联主题", "source_ids": [source_id]}).json()["id"]
    assert client.delete(f"/api/v1/topics/{topic_id}").status_code == 204
    repository = client.app.state.services.repository
    with repository.connection() as connection:
        rows = connection.execute("SELECT * FROM topic_sources WHERE topic_id=?", (topic_id,)).fetchall()
    assert rows == []


def test_relation_delete_requires_source_involvement(client: TestClient) -> None:
    first = paste(client, "关系来源甲", "正文甲")["source"]["id"]
    second = paste(client, "关系来源乙", "正文乙")["source"]["id"]
    third = paste(client, "关系来源丙", "正文丙")["source"]["id"]
    created = client.post(f"/api/v1/sources/{first}/relations", json={"related_source_id": second, "relation_type": "related_to"})
    assert created.status_code == 201, created.text
    relation_id = created.json()["id"]

    assert client.delete(f"/api/v1/sources/{third}/relations/{relation_id}").status_code == 404
    assert client.delete(f"/api/v1/sources/{first}/relations/does-not-exist").status_code == 404

    # 关系在任一方向涉及的来源端点都可删除。
    assert client.delete(f"/api/v1/sources/{second}/relations/{relation_id}").status_code == 204
    assert client.get(f"/api/v1/sources/{first}/relations").json() == []


def test_search_topic_id_filters_source_branch_only(client: TestClient) -> None:
    member = paste(client, "主题内来源", "正文三")["source"]["id"]
    paste(client, "主题外来源", "正文四")
    card = client.post("/api/v1/external/cards", json={"url": "https://example.test/topic-filter", "title": "来源外部卡"})
    assert card.status_code == 201, card.text
    topic_id = client.post("/api/v1/topics", json={"name": "检索主题", "source_ids": [member]}).json()["id"]
    empty_topic_id = client.post("/api/v1/topics", json={"name": "空主题"}).json()["id"]

    hits = client.get(f"/api/v1/search?q=来源&topic_id={topic_id}").json()["items"]
    assert [item["id"] for item in hits if item["kind"] == "source"] == [member]

    hits = client.get(f"/api/v1/search?q=来源&topic_id={empty_topic_id}").json()["items"]
    assert [item for item in hits if item["kind"] == "source"] == []
    assert [item for item in hits if item["kind"] == "external_card"]


def test_same_work_candidates_detects_same_artifact(client: TestClient) -> None:
    # 相同字节两次导入：artifact 去重，来源各自独立。
    first = paste(client, "原始标题", "完全相同的字节内容")["source"]["id"]
    second = paste(client, "另一个标题", "完全相同的字节内容")["source"]["id"]
    candidates = client.get(f"/api/v1/sources/{first}").json()["same_work_candidates"]
    assert candidates == [{"id": second, "title": "另一个标题", "reason": "same_artifact"}]


def test_same_work_candidates_detects_normalized_title(client: TestClient) -> None:
    first = paste(client, "NOTE  Alpha ", "内容完全不同的正文一")["source"]["id"]
    second = paste(client, "note alpha", "内容完全不同的正文二")["source"]["id"]
    candidates = client.get(f"/api/v1/sources/{first}").json()["same_work_candidates"]
    assert candidates == [{"id": second, "title": "note alpha", "reason": "same_title"}]


def test_same_work_candidates_excludes_declared_same_work(client: TestClient) -> None:
    first = paste(client, "声明前标题甲", "声明用的相同内容")["source"]["id"]
    second = paste(client, "声明前标题乙", "声明用的相同内容")["source"]["id"]
    assert client.get(f"/api/v1/sources/{first}").json()["same_work_candidates"]
    declared = client.post(
        f"/api/v1/sources/{first}/relations",
        json={"related_source_id": second, "relation_type": "user_declared_same_work"},
    )
    assert declared.status_code == 201, declared.text
    assert client.get(f"/api/v1/sources/{first}").json()["same_work_candidates"] == []
    assert client.get(f"/api/v1/sources/{second}").json()["same_work_candidates"] == []
