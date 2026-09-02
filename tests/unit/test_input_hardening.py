"""Task 5（加固计划）：结构化与文件内容输入上限。

- tags ≤100 项、每项 1–100 字符、strip 后稳定去重；
- evidence_ids/source_ids ≤500 项、每项 ≤128 字符；
- JSON 请求体 >12MiB → 413 request_too_large；
- PDF 必须 %PDF- 开头、DOCX 必须含结构成员、TXT/MD 拒绝 NUL——
  校验在 artifact 入库前，失败时零 source/artifact/job 残留；
- DOCX zip 元数据上限：成员 ≤10,000、解压总量 ≤512MiB、单成员 ≤256MiB、
  压缩比 ≤200。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domain.input_limits import (
    DOCX_MAX_COMPRESSION_RATIO,
    InputContentInvalid,
    InputTooLarge,
    validate_docx_members,
    validate_document_head,
    validate_id_list,
    normalize_tags,
)
from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def _assert_nothing_persisted(client: TestClient) -> None:
    assert client.get("/api/v1/sources").json() == []
    # lifespan 会例行入队 backup/integrity_sample；只断言没有导入类残留。
    kinds = {job["kind"] for job in client.get("/api/v1/jobs").json()}
    assert not kinds & {"parse", "image_analyze", "video_analyze"}, kinds


# --- 结构化上限 -------------------------------------------------------------


def test_tags_over_limit_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/paste",
        json={"title": "t", "text": "正文", "rights": "owned", "tags": [f"t{i}" for i in range(101)]},
    )
    assert response.status_code == 422


def test_tag_item_too_long_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/paste",
        json={"title": "t", "text": "正文", "rights": "owned", "tags": ["x" * 101]},
    )
    assert response.status_code == 422


def test_tags_stripped_and_deduped(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/paste",
        json={"title": "t", "text": "正文", "rights": "owned", "tags": [" AI ", "ai", "知识库", "知识库 "]},
    )
    assert response.status_code == 201
    source_id = response.json()["source"]["id"]
    detail = client.get(f"/api/v1/sources/{source_id}").json()
    assert detail["tags"] == sorted({"AI", "ai", "知识库"}), detail["tags"]


def test_evidence_ids_over_limit_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge",
        json={"kind": "fact", "statement": "s", "evidence_ids": [str(i) for i in range(501)]},
    )
    assert response.status_code == 422


def test_evidence_id_too_long_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/knowledge",
        json={"kind": "fact", "statement": "s", "evidence_ids": ["x" * 129]},
    )
    assert response.status_code == 422


def test_json_body_over_12mib_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/paste",
        content=json.dumps({"title": "t", "text": "x" * (13 * 1024 * 1024), "rights": "owned"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


# --- 文件内容校验（入库前） --------------------------------------------------


def test_pdf_without_magic_rejected_and_nothing_persisted(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("fake.pdf", b"NOT A PDF" * 8, "application/pdf")},
        data={"rights": "owned"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_file_content"
    _assert_nothing_persisted(client)


def test_txt_with_nul_rejected_and_nothing_persisted(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("note.txt", b"before\x00after", "text/plain")},
        data={"rights": "owned"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_file_content"
    _assert_nothing_persisted(client)


def test_paste_text_with_nul_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/paste",
        json={"title": "t", "text": "前\x00后", "rights": "owned"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_file_content"


def _docx_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


VALID_DOCX_MEMBERS = {
    "[Content_Types].xml": b"<?xml version='1.0'?><Types/>",
    "word/document.xml": b"<w:document/>",
}


def test_valid_docx_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("a.docx", _docx_bytes(VALID_DOCX_MEMBERS), "application/octet-stream")},
        data={"rights": "owned"},
    )
    assert response.status_code == 201


def test_docx_missing_required_members_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("a.docx", _docx_bytes({"word/document.xml": b"<w/>"}), "application/octet-stream")},
        data={"rights": "owned"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_file_content"
    _assert_nothing_persisted(client)


def test_docx_not_a_zip_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("a.docx", b"plain text not zip", "application/octet-stream")},
        data={"rights": "owned"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_file_content"


def test_docx_member_count_over_limit(client: TestClient) -> None:
    members = dict(VALID_DOCX_MEMBERS)
    for index in range(10_001):
        members[f"word/media/img{index}.png"] = b"x"
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("a.docx", _docx_bytes(members), "application/octet-stream")},
        data={"rights": "owned"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


# --- 纯校验器（伪造 ZipInfo，避免构造超大真实文件） --------------------------


class _FakeInfo:
    def __init__(self, filename: str, file_size: int, compress_size: int) -> None:
        self.filename = filename
        self.file_size = file_size
        self.compress_size = compress_size


def test_validate_docx_members_total_size_limit() -> None:
    infos = [
        _FakeInfo("[Content_Types].xml", 10, 10),
        _FakeInfo("word/document.xml", 400 * 1024 * 1024, 1024),
        _FakeInfo("word/media/a.bin", 200 * 1024 * 1024, 1024),
    ]
    with pytest.raises(InputTooLarge):
        validate_docx_members(infos)


def test_validate_docx_members_single_member_limit() -> None:
    infos = [
        _FakeInfo("[Content_Types].xml", 10, 10),
        _FakeInfo("word/document.xml", 300 * 1024 * 1024, 1024),
    ]
    with pytest.raises(InputTooLarge):
        validate_docx_members(infos)


def test_validate_docx_members_compression_ratio_limit() -> None:
    infos = [
        _FakeInfo("[Content_Types].xml", 10, 10),
        _FakeInfo("word/document.xml", 100 * 1024 * 1024, 1),
    ]
    assert DOCX_MAX_COMPRESSION_RATIO == 200
    with pytest.raises(InputContentInvalid):
        validate_docx_members(infos)


def test_validate_id_list_and_tags_helpers() -> None:
    assert validate_id_list(["a" * 128], field="evidence_ids") == ["a" * 128]
    with pytest.raises(ValueError):
        validate_id_list(["x" * 129], field="evidence_ids")
    assert normalize_tags(["  a ", "a", "", "b"]) == ["a", "b"]
    with pytest.raises(ValueError):
        normalize_tags([f"t{i}" for i in range(101)])


def test_validate_document_head_pdf_magic_and_nul() -> None:
    validate_document_head(".pdf", b"%PDF-1.7 rest")
    with pytest.raises(InputContentInvalid):
        validate_document_head(".pdf", b"not pdf")
    with pytest.raises(InputContentInvalid):
        validate_document_head(".txt", b"ab\x00cd")
    validate_document_head(".md", "中文正文".encode("utf-8"))
