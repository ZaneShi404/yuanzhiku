from __future__ import annotations

import io
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import prefill


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "import-prefill"


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


def make_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({
        "/Title": "示例 PDF 标题",
        "/Author": "张三",
        "/CreationDate": "D:20240115103000",
    })
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_docx() -> bytes:
    import docx

    document = docx.Document()
    properties = document.core_properties
    properties.title = "示例 DOCX 标题"
    properties.author = "李四"
    properties.created = datetime(2024, 3, 5, 12, 0, 0)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_detect_language_chinese_sample() -> None:
    assert prefill.detect_language("这是一段用于语言识别的中文文本，包含足够多的汉字。") == "zh"


def test_detect_language_english_sample() -> None:
    sample = "This is a fairly long English sentence written only with plain Latin letters."
    assert prefill.detect_language(sample) == "en"


def test_detect_language_insufficient_or_mixed_sample() -> None:
    assert prefill.detect_language("short") is None
    assert prefill.detect_language("12345 !!!") is None


def test_suggest_text_markdown_heading_and_language() -> None:
    suggestion = prefill.suggest_text("前言\n\n# 真正的标题\n\n正文内容，足够的中文文本让语言识别为中文。")
    assert suggestion == {
        "title": "真正的标题",
        "author": None,
        "language": "zh",
        "source_date": None,
    }


def test_suggest_text_first_non_empty_line_when_no_heading() -> None:
    suggestion = prefill.suggest_text("\n\n  第一行内容  \n第二行\n")
    assert suggestion["title"] == "第一行内容"
    assert suggestion["language"] == "zh"


def test_suggest_text_empty_returns_all_none() -> None:
    assert prefill.suggest_text("   \n \n") == {
        "title": None,
        "author": None,
        "language": None,
        "source_date": None,
    }


def test_suggest_text_truncates_long_title() -> None:
    suggestion = prefill.suggest_text("长" * 600)
    assert suggestion["title"] == "长" * 500


def test_suggest_document_pdf_metadata() -> None:
    suggestion = prefill.suggest_document("report.pdf", make_pdf())
    assert suggestion == {
        "title": "示例 PDF 标题",
        "author": "张三",
        "language": None,
        "source_date": "2024-01-15",
    }


def test_suggest_document_docx_metadata() -> None:
    suggestion = prefill.suggest_document("notes.docx", make_docx())
    assert suggestion == {
        "title": "示例 DOCX 标题",
        "author": "李四",
        "language": None,
        "source_date": "2024-03-05",
    }


def test_suggest_document_markdown_text() -> None:
    suggestion = prefill.suggest_document("notes.md", "# 笔记标题\n\n中文正文内容，足够的汉字。".encode("utf-8"))
    assert suggestion["title"] == "笔记标题"
    assert suggestion["language"] == "zh"


def test_suggest_document_corrupted_pdf_returns_all_none() -> None:
    suggestion = prefill.suggest_document("broken.pdf", b"this is not a pdf at all")
    assert suggestion == {"title": None, "author": None, "language": None, "source_date": None}


def test_suggest_image_uses_filename_stem() -> None:
    suggestion = prefill.suggest_image("扫描件 照片.jpg", b"\xff\xd8\xff")
    assert suggestion == {"title": "扫描件 照片", "author": None, "language": None, "source_date": None}


def _jpeg_with_exif() -> bytes:
    from PIL import Image

    image = Image.new("RGB", (64, 48), (120, 30, 200))
    exif = Image.Exif()
    exif[0x013B] = "Synth Author"  # Artist（ASCII 类型标签，与真实相机一致）
    exif[0x8769] = {0x9003: "2024:01:15 10:30:00"}  # Exif IFD DateTimeOriginal
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def test_suggest_image_reads_exif_artist_and_date() -> None:
    suggestion = prefill.suggest_image("photo.jpg", _jpeg_with_exif())
    assert suggestion == {"title": "photo", "author": "Synth Author", "language": None, "source_date": "2024-01-15"}


def test_prefill_endpoint_with_exif_image_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("photo.jpg", _jpeg_with_exif(), "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"title": "photo", "author": "Synth Author", "language": None, "source_date": "2024-01-15"}


def test_prefill_endpoint_with_pdf_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("report.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "title": "示例 PDF 标题",
        "author": "张三",
        "language": None,
        "source_date": "2024-01-15",
    }


def test_prefill_endpoint_with_docx_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("notes.docx", make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "title": "示例 DOCX 标题",
        "author": "李四",
        "language": None,
        "source_date": "2024-03-05",
    }


def test_prefill_endpoint_with_text_field(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        data={"text": "# 粘贴标题\n\n中文正文内容，足够的汉字让语言识别为中文。"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "title": "粘贴标题",
        "author": None,
        "language": "zh",
        "source_date": None,
    }


def test_prefill_endpoint_with_image_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("封面.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"title": "封面", "author": None, "language": None, "source_date": None}


def test_prefill_endpoint_corrupted_pdf_returns_all_null(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("broken.pdf", b"garbage bytes", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"title": None, "author": None, "language": None, "source_date": None}


def test_prefill_endpoint_rejects_unsupported_suffix(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_prefill_suffix"
    assert "MZ" not in response.text


def test_prefill_endpoint_rejects_empty_request(client: TestClient) -> None:
    response = client.post("/api/v1/imports/prefill")
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["message"] == "需要提供文件或文本"


def test_prefill_endpoint_rejects_oversized_file(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("huge.txt", b"a" * (20 * 1024 * 1024 + 1), "text/plain")},
    )
    assert response.status_code == 413, response.text


def test_prefill_endpoint_rejects_oversized_text(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        data={"text": "a" * (1024 * 1024 + 1)},
    )
    assert response.status_code == 413, response.text


def test_prefill_endpoint_has_no_side_effects(client: TestClient) -> None:
    response = client.post(
        "/api/v1/imports/prefill",
        files={"file": ("report.pdf", make_pdf(), "application/pdf")},
    )
    assert response.status_code == 200, response.text
    sources = client.get("/api/v1/sources")
    assert sources.status_code == 200, sources.text
    assert sources.json() == []
