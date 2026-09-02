"""Task 2（加固计划）：Host 与 Origin 本地访问边界。

- Host 头必须是本机回环（127.0.0.1/localhost/::1）或测试用 testserver；
  其余（含 DNS rebinding 域）一律 403。
- 写方法（POST/PUT/PATCH/DELETE）携带 Origin 时：仅接受本机同源与开发
  前端来源；`Origin: null` 与未知来源一律 403。GET 与无 Origin 请求
  （本地 CLI/curl）保持原有成功语义。
- CORS preflight（OPTIONS）不受 Origin 检查影响，由 CORS 中间件处理。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> TestClient:
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def _health_ok(client: TestClient, **kwargs) -> bool:
    response = client.get("/api/v1/health", **kwargs)
    return response.status_code == 200


def test_health_with_default_testclient_host_passes(client: TestClient) -> None:
    assert _health_ok(client)


def test_malicious_host_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Host": "evil.example.com"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "untrusted_host"


def test_localhost_host_allowed(client: TestClient) -> None:
    assert _health_ok(client, headers={"Host": "localhost"})
    assert _health_ok(client, headers={"Host": "127.0.0.1"})


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_untrusted_origin_on_writes_rejected(client: TestClient, method: str) -> None:
    response = client.request(
        method.upper(),
        "/api/v1/external/cards",
        json={"card_type": "url", "url": "https://example.com/a", "title": "t"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "untrusted_origin"


def test_null_origin_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/external/cards",
        json={"card_type": "url", "url": "https://example.com/a", "title": "t"},
        headers={"Origin": "null"},
    )
    assert response.status_code == 403


def test_same_origin_and_dev_origin_allowed(client: TestClient) -> None:
    ok = client.post(
        "/api/v1/external/cards",
        json={"card_type": "url", "url": "https://example.com/a", "title": "t"},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert ok.status_code in {201, 409}
    dev = client.post(
        "/api/v1/external/cards",
        json={"card_type": "url", "url": "https://example.com/b", "title": "t"},
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert dev.status_code in {201, 409}


def test_write_without_origin_keeps_cli_semantics(client: TestClient) -> None:
    response = client.post(
        "/api/v1/external/cards",
        json={"card_type": "url", "url": "https://example.com/c", "title": "t"},
    )
    assert response.status_code == 201


def test_cors_preflight_passes(client: TestClient) -> None:
    response = client.options(
        "/api/v1/settings",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert response.status_code in {200, 204}


def test_multipart_post_untrusted_origin_rejected(client: TestClient, tmp_path) -> None:
    payload = tmp_path / "n.txt"
    payload.write_bytes(b"tiny")
    response = client.post(
        "/api/v1/imports/file",
        files={"file": ("n.txt", payload.read_bytes(), "text/plain")},
        data={"rights": "owned"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "untrusted_origin"
