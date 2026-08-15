from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def runtime_root() -> Path:
    isolated_root = os.environ.get("YUANZHIKU_TEST_RUNTIME")
    root = (
        Path(isolated_root) / "api" / "case"
        if isolated_root
        else Path(__file__).resolve().parents[1] / "runtime" / "pytest-api"
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


def test_errors_use_stable_structured_detail(client: TestClient) -> None:
    missing = client.get("/api/v1/sources/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"] == {"code": "http_404", "message": "来源不存在"}

    invalid = client.post("/api/v1/imports/paste", json={"title": "missing rights", "text": "body"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == {"code": "request_validation", "message": "请求字段无效"}

    no_evidence = client.post("/api/v1/knowledge", json={"kind": "fact", "statement": "没有证据的事实"}).json()
    blocked = client.post(f"/api/v1/knowledge/{no_evidence['id']}/publish")
    assert blocked.status_code == 422
    imported = import_and_run(client)
    version_id = imported["content_version"]["id"]
    representation = client.get(f"/api/v1/documents/{version_id}/representations").json()[0]
    evidence_id = client.get(f"/api/v1/representations/{representation['id']}/evidence").json()[0]["id"]
    knowledge = client.post("/api/v1/knowledge", json={"kind": "fact", "statement": "有证据的事实", "evidence_ids": [evidence_id]})
    assert client.post(f"/api/v1/knowledge/{knowledge.json()['id']}/publish").json()["status"] == "published"


def test_retry_rejects_nonterminal_job_with_stable_conflict(client: TestClient) -> None:
    imported = client.post("/api/v1/imports/paste", json={
        "title": "retry state", "text": "retry body", "rights": "owned",
    })
    assert imported.status_code == 201

    response = client.post(f"/api/v1/jobs/{imported.json()['job']['id']}/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "http_409",
        "message": "仅失败、阻塞或已取消的作业可以手动重试",
    }


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


def test_original_artifact_uses_safe_framework_disposition(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/imports/file",
        data={"rights": "owned", "categories": "[]", "tags": "[]"},
        files={"file": ('unsafe"name.txt', b"local text", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text

    original = client.get(f"/api/v1/sources/{uploaded.json()['source']['id']}/original")
    assert original.status_code == 200
    assert original.headers["x-content-type-options"] == "nosniff"
    assert original.headers["content-security-policy"] == "sandbox; default-src 'none'; frame-ancestors 'self'"
    assert original.headers["content-disposition"].startswith("attachment;")
    assert 'unsafe"name.txt' not in original.headers["content-disposition"]


def test_file_import_rejects_oversized_content_length_before_persisting(client: TestClient, runtime_root: Path) -> None:
    response = client.post(
        "/api/v1/imports/file",
        headers={"content-length": str(2 * 1024 * 1024 * 1024 + 1)},
        data={"rights": "owned", "categories": "[]", "tags": "[]"},
        files={"file": ("synthetic.txt", b"small body", "text/plain")},
    )

    assert response.status_code == 413, response.text
    assert client.get("/api/v1/sources").json() == []
    assert not [path for path in (runtime_root / "artifacts").rglob("*") if path.is_file()]


def test_file_import_preserves_utf8_multipart_metadata(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/imports/file",
        data={
            "rights": "owned",
            "title": "合成中文标题",
            "author": "中文作者",
            "notes": "中文备注",
            "categories": '["document"]',
            "tags": '["中文标签"]',
            "language": "zh",
        },
        files={"file": ("synthetic.txt", "中文文件正文".encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    source = client.get(f"/api/v1/sources/{uploaded.json()['source']['id']}")
    assert source.status_code == 200, source.text
    assert source.json()["title"] == "合成中文标题"
    assert source.json()["author"] == "中文作者"
    assert source.json()["notes"] == "中文备注"
    assert source.json()["tags"] == ["中文标签"]


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


def test_download_link_endpoints_in_openapi_and_capabilities(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/videos/link" in schema["paths"]
    assert "/api/v1/settings/download-cookies/{platform}" in schema["paths"]
    assert "delete" in schema["paths"]["/api/v1/settings/download-cookies/{platform}"]
    capabilities = client.get("/api/v1/capabilities").json()
    downloader = capabilities["downloader"]
    assert set(downloader) == {"enabled", "adapter", "version", "supported_platforms", "cookies", "network"}
    assert downloader["adapter"] == "yt-dlp"
    assert downloader["supported_platforms"] == ["bilibili", "douyin"]
    # enabled 与探测结果一致（不硬编码测试机无 FFmpeg）：如实报告，不伪装可用
    import shutil

    ffmpeg_bin = os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
    ffprobe_bin = os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")
    tools_available = bool(shutil.which(ffmpeg_bin) and shutil.which(ffprobe_bin))
    assert downloader["enabled"] is tools_available
    assert downloader["cookies"] == {"bilibili": False, "douyin": False}


def test_download_link_error_codes_stable(client: TestClient) -> None:
    import shutil

    ffmpeg_bin = os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
    ffprobe_bin = os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")
    tools_available = bool(shutil.which(ffmpeg_bin) and shutil.which(ffprobe_bin))
    unavailable = client.post(
        "/api/v1/videos/link",
        json={"url": "https://www.bilibili.com/video/BV1", "platform": "bilibili", "rights": "owned"},
    )
    if tools_available:
        # 工具齐备时端点可用；downloader_unavailable 语义由作业级工具缺失用例覆盖
        assert unavailable.status_code == 201
        assert unavailable.json()["kind"] == "video_download"
    else:
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "downloader_unavailable"
    invalid = client.post(
        "/api/v1/videos/link",
        json={"url": "http://www.bilibili.com/video/BV1", "platform": "bilibili", "rights": "owned"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_url"
    assert "bilibili" not in invalid.json()["detail"]["message"]
    unsupported = client.post(
        "/api/v1/videos/link",
        json={"url": "https://www.bilibili.com/video/BV1", "platform": "youtube", "rights": "owned"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_platform"
    missing_rights = client.post(
        "/api/v1/videos/link",
        json={"url": "https://www.bilibili.com/video/BV1", "platform": "bilibili"},
    )
    assert missing_rights.status_code == 422
    assert missing_rights.json()["detail"]["code"] == "request_validation"


def test_download_cookie_delete_cors_preflight_and_idempotent_delete(client: TestClient) -> None:
    preflight = client.options(
        "/api/v1/settings/download-cookies/bilibili",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert preflight.status_code == 200
    assert "DELETE" in preflight.headers.get("access-control-allow-methods", "")
    assert client.delete("/api/v1/settings/download-cookies/bilibili").status_code == 204
    assert client.delete("/api/v1/settings/download-cookies/bilibili").status_code == 204
