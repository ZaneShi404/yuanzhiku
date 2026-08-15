from __future__ import annotations

import io
import os
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "image-import"


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture()
def client(runtime_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUANZHIKU_EMBEDDED_WORKER", "false")
    app = create_app(runtime_root, acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def jpeg_with_exif() -> bytes:
    # JPEG EXIF 的 Artist/ImageDescription 为 ASCII 类型标签，与真实相机一致只写 ASCII。
    image = Image.new("RGB", (64, 48), (120, 30, 200))
    exif = Image.Exif()
    exif[0x013B] = "Synth Author"  # Artist
    exif[0x010E] = "synthetic sample description"  # ImageDescription
    exif[0x8769] = {0x9003: "2024:01:15 10:30:00"}  # Exif IFD DateTimeOriginal
    output = io.BytesIO()
    image.save(output, format="JPEG", exif=exif)
    return output.getvalue()


def plain_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 12), (1, 2, 3)).save(output, format="PNG")
    return output.getvalue()


def webp_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (33, 21), (9, 8, 7)).save(output, format="WEBP")
    return output.getvalue()


def import_image(client: TestClient, filename: str, content: bytes, media_type: str, **form: str) -> dict:
    data = {"rights": "owned", "categories": "[]", "tags": "[]", "language": "zh"}
    data.update(form)
    response = client.post("/api/v1/imports/image", data=data, files={"file": (filename, content, media_type)})
    assert response.status_code == 201, response.text
    return response.json()


def run_once(client: TestClient) -> dict:
    response = client.post("/api/v1/jobs/run-once")
    assert response.status_code == 200, response.text
    return response.json()["job"]


def representation_of(client: TestClient, version_id: str) -> dict:
    representations = client.get(f"/api/v1/documents/{version_id}/representations").json()
    assert len(representations) == 1
    return representations[0]


def test_jpeg_exif_import_analyze_evidence_and_search(client: TestClient) -> None:
    imported = import_image(client, "exif-photo.jpg", jpeg_with_exif(), "image/jpeg")
    assert imported["source"]["title"] == "exif-photo"
    assert imported["source"]["source_type"] == "file"
    assert imported["content_version"]["media_type"] == "image/jpeg"
    assert imported["job"]["kind"] == "image_analyze"
    assert imported["artifact"]["sha256"]

    job = run_once(client)
    assert job["state"] == "succeeded", job
    assert job["message"] == "本地图片分析完成"

    source = client.get(f"/api/v1/sources/{imported['source']['id']}").json()
    assert source["processing_state"] == "succeeded"
    assert source["versions"][0]["completeness"] == "complete"

    representation = representation_of(client, imported["content_version"]["id"])
    assert representation["parser_name"] == "pillow-local"
    text = representation["text_content"]
    assert "尺寸：64 x 48" in text
    assert "格式：JPEG" in text
    assert "拍摄时间：2024-01-15 10:30:00" in text
    assert "EXIF 作者：Synth Author" in text
    assert "EXIF 描述：synthetic sample description" in text

    evidence = client.get(f"/api/v1/representations/{representation['id']}/evidence").json()
    assert len(evidence) == 1
    assert evidence[0]["artifact_sha256"] == imported["artifact"]["sha256"]
    assert evidence[0]["is_validated"] is True
    assert evidence[0]["locator"] == {
        "type": "image_metadata",
        "width": 64,
        "height": 48,
        "format": "JPEG",
        "datetime_original": "2024-01-15 10:30:00",
    }

    found = client.get("/api/v1/search", params={"q": "jpeg"}).json()["items"]
    assert any(item["id"] == imported["source"]["id"] for item in found)
    found = client.get("/api/v1/search", params={"q": "64 x 48"}).json()["items"]
    assert any(item["id"] == imported["source"]["id"] for item in found)


def test_png_without_exif_still_succeeds(client: TestClient) -> None:
    imported = import_image(client, "plain.png", plain_png(), "image/png", title="显式标题")
    assert imported["source"]["title"] == "显式标题"
    assert imported["content_version"]["media_type"] == "image/png"

    job = run_once(client)
    assert job["state"] == "succeeded", job

    representation = representation_of(client, imported["content_version"]["id"])
    assert "尺寸：20 x 12" in representation["text_content"]
    assert "格式：PNG" in representation["text_content"]
    assert "拍摄时间" not in representation["text_content"]
    evidence = client.get(f"/api/v1/representations/{representation['id']}/evidence").json()
    assert evidence[0]["locator"]["format"] == "PNG"
    assert evidence[0]["locator"]["datetime_original"] is None


def test_webp_import_succeeds(client: TestClient) -> None:
    imported = import_image(client, "sample.webp", webp_image(), "image/webp")
    assert imported["content_version"]["media_type"] == "image/webp"

    job = run_once(client)
    assert job["state"] == "succeeded", job
    representation = representation_of(client, imported["content_version"]["id"])
    assert "格式：WEBP" in representation["text_content"]
    evidence = client.get(f"/api/v1/representations/{representation['id']}/evidence").json()
    assert evidence[0]["locator"]["width"] == 33
    assert evidence[0]["locator"]["height"] == 21


def test_corrupt_image_fails_with_sanitized_message(client: TestClient) -> None:
    imported = import_image(client, "broken.jpg", b"definitely not a jpeg", "image/jpeg")

    job = run_once(client)
    assert job["state"] == "failed", job
    assert job["message"] == "本地图片无法分析"

    source = client.get(f"/api/v1/sources/{imported['source']['id']}").json()
    assert source["processing_state"] == "failed"
    assert source["versions"][0]["completeness"] == "incomplete"
    assert client.get(f"/api/v1/documents/{imported['content_version']['id']}/representations").json() == []


def test_unsupported_suffix_rejected(client: TestClient) -> None:
    for filename in ("a.gif", "b.bmp", "c.exe"):
        response = client.post(
            "/api/v1/imports/image",
            data={"rights": "owned", "categories": "[]", "tags": "[]"},
            files={"file": (filename, plain_png(), "application/octet-stream")},
        )
        assert response.status_code == 422, (filename, response.text)
        assert response.json()["detail"]["message"] == "仅支持 JPG、PNG 和 WebP 图片"
    assert client.get("/api/v1/sources").json() == []


def test_oversized_content_length_rejected_by_preflight(client: TestClient, runtime_root: Path) -> None:
    response = client.post(
        "/api/v1/imports/image",
        headers={"content-length": str(2 * 1024 * 1024 * 1024 + 1)},
        data={"rights": "owned", "categories": "[]", "tags": "[]"},
        files={"file": ("small.png", plain_png(), "image/png")},
    )

    assert response.status_code == 413, response.text
    assert client.get("/api/v1/sources").json() == []
    assert not [path for path in (runtime_root / "artifacts").rglob("*") if path.is_file()]


def test_original_serves_image_inline(client: TestClient) -> None:
    content = plain_png()
    imported = import_image(client, "preview.png", content, "image/png")

    original = client.get(f"/api/v1/sources/{imported['source']['id']}/original")
    assert original.status_code == 200
    assert original.content == content
    assert original.headers["content-type"].startswith("image/png")
    assert original.headers["x-content-type-options"] == "nosniff"
    assert original.headers["content-security-policy"] == "sandbox; default-src 'none'; frame-ancestors 'self'"
    assert original.headers["content-disposition"].startswith("inline;")


def test_title_fallback_to_filename_stem(client: TestClient) -> None:
    imported = import_image(client, "度假 照片.jpeg", plain_png(), "image/jpeg")
    assert imported["source"]["title"] == "度假 照片"
    assert imported["content_version"]["media_type"] == "image/jpeg"
