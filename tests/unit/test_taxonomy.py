"""分类体系（领域 domains × 体裁 genres）重构的单元测试。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.sqlite import EXPORT_TABLES, SqliteRepository
from app.main import create_app


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "taxonomy"


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
    with TestClient(app) as client:
        yield client, app.state.services


@pytest.fixture()
def client(client_and_services) -> TestClient:
    return client_and_services[0]


def _import_paste(client: TestClient, title: str, *, text: str = "正文内容", domains=None, genres=None, tags=None) -> str:
    response = client.post("/api/v1/imports/paste", json={
        "title": title, "text": text, "rights": "owned",
        "domains": domains or [], "genres": genres or [], "tags": tags or [],
    })
    assert response.status_code == 201, response.text
    return response.json()["source"]["id"]


def _search_source_ids(client: TestClient, params) -> set[str]:
    response = client.get("/api/v1/search", params=params)
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()["items"] if item["kind"] == "source"}


def test_taxonomy_endpoint_is_single_source_of_truth(client: TestClient) -> None:
    response = client.get("/api/v1/taxonomy")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"domains", "genres"}
    assert [item["value"] for item in payload["domains"]] == [
        "technical", "business", "education", "news", "entertainment", "life", "other",
    ]
    assert [item["value"] for item in payload["genres"]] == [
        "document", "lecture", "interview", "podcast", "review", "recording", "other",
    ]
    assert all(set(item) == {"value", "label"} and item["label"] for item in payload["domains"] + payload["genres"])
    labels = {item["value"]: item["label"] for item in payload["domains"] + payload["genres"]}
    assert labels["technical"] == "技术" and labels["life"] == "生活"
    assert labels["document"] == "文档" and labels["recording"] == "记录"


def test_taxonomy_write_validation(client: TestClient) -> None:
    # 去重 + 排序；领域可多空值合法
    imported = client.post("/api/v1/imports/paste", json={
        "title": "校验", "text": "正文", "rights": "owned",
        "domains": ["life", "business", "business"], "genres": [],
    })
    assert imported.status_code == 201, imported.text
    assert imported.json()["source"]["domains_json"] == '["business", "life"]'
    assert imported.json()["source"]["genres_json"] == "[]"
    empty = client.post("/api/v1/imports/paste", json={"title": "空", "text": "正文", "rights": "owned"})
    assert empty.status_code == 201, empty.text

    invalid_domain = client.post("/api/v1/imports/paste", json={
        "title": "非法领域", "text": "正文", "rights": "owned", "domains": ["not-a-domain"],
    })
    assert invalid_domain.status_code == 422
    assert invalid_domain.json()["detail"] == {"code": "request_validation", "message": "请求字段无效"}
    invalid_genre = client.post("/api/v1/imports/paste", json={
        "title": "非法体裁", "text": "正文", "rights": "owned", "genres": ["not-a-genre"],
    })
    assert invalid_genre.status_code == 422
    assert invalid_genre.json()["detail"]["code"] == "request_validation"
    multi_genre = client.post("/api/v1/imports/paste", json={
        "title": "多体裁", "text": "正文", "rights": "owned", "genres": ["interview", "podcast"],
    })
    assert multi_genre.status_code == 422
    assert multi_genre.json()["detail"]["code"] == "request_validation"


def test_metadata_update_enforces_single_genre(client: TestClient) -> None:
    source_id = _import_paste(client, "体裁校验", genres=["interview"])

    multi = client.put(f"/api/v1/sources/{source_id}/metadata", json={"genres": ["interview", "podcast"]})
    assert multi.status_code == 422
    assert multi.json()["detail"] == {"code": "request_validation", "message": "请求字段无效"}
    null_genre = client.put(f"/api/v1/sources/{source_id}/metadata", json={"genres": None})
    assert null_genre.status_code == 422
    assert null_genre.json()["detail"]["code"] == "request_validation"
    invalid = client.put(f"/api/v1/sources/{source_id}/metadata", json={"domains": ["retired"]})
    assert invalid.status_code == 422

    updated = client.put(f"/api/v1/sources/{source_id}/metadata", json={"genres": [], "domains": ["life", "technical"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["genres"] == []
    assert updated.json()["domains"] == ["life", "technical"]


def _revert_sources_to_v8(repository: SqliteRepository) -> None:
    """把当前库回滚成 v8 形态（携带 categories_json），用于演练 v9 迁移。"""
    with repository.connection() as connection:
        connection.execute("ALTER TABLE sources ADD COLUMN categories_json TEXT NOT NULL DEFAULT '[]'")
        connection.execute("ALTER TABLE sources DROP COLUMN domains_json")
        connection.execute("ALTER TABLE sources DROP COLUMN genres_json")
        connection.execute("DELETE FROM schema_migrations WHERE version=9")


def _insert_legacy_source(repository: SqliteRepository, source_id: str, categories: list[str]) -> None:
    with repository.connection() as connection:
        connection.execute(
            "INSERT INTO sources(id,source_type,title,author,language,notes,source_date,rights,"
            "categories_json,tags_json,processing_state,imported_at,updated_at,deleted_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                source_id, "paste", f"旧来源{source_id}", None, "zh", None, None, "owned",
                json.dumps(categories), "[]", "succeeded",
                "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            ),
        )


def test_sqlite_v9_migration_splits_categories(runtime_root: Path) -> None:
    repository = SqliteRepository(runtime_root / "state" / "knowledge.db")
    repository.initialize()
    _revert_sources_to_v8(repository)
    legacy_rows = {
        "d-technical": ["technical"],
        "d-business": ["business"],
        "d-education": ["education"],
        "d-news": ["news"],
        "g-interview": ["interview"],
        "g-podcast": ["podcast"],
        "g-document": ["document"],
        "multi": ["technical", "interview", "podcast", "retired"],
        "empty": [],
    }
    for source_id, categories in legacy_rows.items():
        _insert_legacy_source(repository, source_id, categories)

    repository.initialize()
    # 幂等：二次初始化不改变结果
    repository.initialize()

    expected = {
        "d-technical": (["technical"], []),
        "d-business": (["business"], []),
        "d-education": (["education"], []),
        "d-news": (["news"], []),
        "g-interview": ([], ["interview"]),
        "g-podcast": ([], ["podcast"]),
        "g-document": ([], ["document"]),
        # 多体裁全部保留（≤1 规则不适用于迁移）；未知值 retired 忽略
        "multi": (["technical"], ["interview", "podcast"]),
        "empty": ([], []),
    }
    for source_id, (domains, genres) in expected.items():
        source = repository.get_source(source_id)
        assert source is not None
        assert json.loads(source["domains_json"]) == domains, source_id
        assert json.loads(source["genres_json"]) == genres, source_id
    with repository.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)")}
        versions = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
    assert "categories_json" not in columns
    assert 9 in versions


def test_legacy_multi_genre_source_edit_enforces_single_genre(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 迁移保留的多体裁来源：读取照常，写入仍受 ≤1 约束
    repository = SqliteRepository(runtime_root / "state" / "knowledge.db")
    repository.initialize()
    _revert_sources_to_v8(repository)
    _insert_legacy_source(repository, "legacy-multi", ["interview", "podcast"])
    monkeypatch.setenv("YUANZHIKU_EMBEDDED_WORKER", "false")
    app = create_app(runtime_root, acquire_lock=False)
    with TestClient(app) as client:
        source = client.get("/api/v1/sources/legacy-multi")
        assert source.status_code == 200, source.text
        assert source.json()["genres"] == ["interview", "podcast"]

        rejected = client.put("/api/v1/sources/legacy-multi/metadata", json={"genres": ["interview", "podcast"]})
        assert rejected.status_code == 422
        assert rejected.json()["detail"]["code"] == "request_validation"
        accepted = client.put("/api/v1/sources/legacy-multi/metadata", json={"genres": ["podcast"]})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["genres"] == ["podcast"]


def _build_schema7_export(archive_path: Path, artifact_bytes: bytes) -> None:
    """手工构造 schema 7 导出归档：sources 行携带 categories_json。"""
    sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    records = {table: [] for table in EXPORT_TABLES}
    records["artifacts"] = [{"sha256": sha256, "byte_size": len(artifact_bytes), "stored_at": "2026-01-01T00:00:00+00:00"}]
    records["sources"] = [{
        "id": "legacy-source", "source_type": "paste", "title": "旧归档来源", "author": None,
        "language": "zh", "notes": None, "source_date": None, "rights": "owned",
        "categories_json": '["technical", "interview", "podcast", "retired"]', "tags_json": '["旧标签"]',
        "processing_state": "succeeded",
        "imported_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00", "deleted_at": None,
    }]
    records["content_versions"] = [{
        "id": "legacy-version", "source_id": "legacy-source", "artifact_sha256": sha256, "ordinal": 1,
        "original_name": "pasted.md", "media_type": "text/markdown", "completeness": "pending",
        "created_at": "2026-01-01T00:00:00+00:00",
    }]
    records_bytes = json.dumps({"schema_version": 7, "records": records}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    manifest = {
        "schema_version": 7,
        "archive_type": "export",
        "database_backend": "sqlite",
        "created_at": "2026-01-01T00:00:00+00:00",
        "entries": [
            {"path": "records.json", "sha256": hashlib.sha256(records_bytes).hexdigest(), "byte_size": len(records_bytes)},
            {"path": f"artifacts/{sha256[:2]}/{sha256}", "sha256": sha256, "byte_size": len(artifact_bytes)},
        ],
        "exclusions": [],
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("records.json", records_bytes)
        archive.writestr(f"artifacts/{sha256[:2]}/{sha256}", artifact_bytes)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def test_schema7_archive_reimport_splits_categories(client: TestClient, runtime_root: Path) -> None:
    archive_path = runtime_root / "legacy-export.zip"
    _build_schema7_export(archive_path, b"legacy text")

    response = client.post("/api/v1/reimports", json={"archive_path": str(archive_path)})

    assert response.status_code == 200, response.text
    source = client.get("/api/v1/sources/legacy-source")
    assert source.status_code == 200, source.text
    assert source.json()["domains"] == ["technical"]
    assert source.json()["genres"] == ["interview", "podcast"]
    assert source.json()["tags"] == ["旧标签"]


def test_search_domain_and_genre_filters(client: TestClient) -> None:
    alpha = _import_paste(client, "阿尔法", domains=["technical", "life"], genres=["document"])
    beta = _import_paste(client, "贝塔", domains=["business"], genres=["interview"])
    gamma = _import_paste(client, "伽马")

    # 领域 OR 语义：命中任一所选领域
    assert _search_source_ids(client, [("domains", "technical"), ("domains", "business")]) == {alpha, beta}
    assert _search_source_ids(client, {"domains": "news"}) == set()
    # "_none" 哨兵：未分类（空领域）来源
    assert _search_source_ids(client, {"domains": "_none"}) == {gamma}
    assert _search_source_ids(client, [("domains", "_none"), ("domains", "business")]) == {beta, gamma}
    # 体裁单值精确匹配
    assert _search_source_ids(client, {"genre": "document"}) == {alpha}
    assert _search_source_ids(client, {"genre": "_none"}) == {gamma}
    # 非法取值一律 422（与请求校验错误同型）
    invalid_domain = client.get("/api/v1/search", params={"domains": "retired"})
    assert invalid_domain.status_code == 422
    assert invalid_domain.json()["detail"] == {"code": "request_validation", "message": "请求字段无效"}
    invalid_genre = client.get("/api/v1/search", params={"genre": "retired"})
    assert invalid_genre.status_code == 422
    assert invalid_genre.json()["detail"]["code"] == "request_validation"


def test_search_excludes_classification_tokens_from_fulltext(client_and_services) -> None:
    client, services = client_and_services
    source_id = _import_paste(
        client, "普通标题", text="这段正文不含任何分类词",
        domains=["technical"], genres=["document"], tags=["独特标签词"],
    )
    services.jobs.run_once()

    assert source_id not in _search_source_ids(client, {"q": "独特标签词"})
    assert not _search_source_ids(client, {"q": "technical"})
    assert not _search_source_ids(client, {"q": "document"})
    assert source_id in _search_source_ids(client, {"q": "普通标题"})
    # 分类仍可通过专用过滤器命中
    assert source_id in _search_source_ids(client, {"tag": "独特标签词"})
    assert source_id in _search_source_ids(client, {"domains": "technical"})
