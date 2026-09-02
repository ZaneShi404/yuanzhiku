"""Task 4a（加固计划）：AI/relay 出站的最小修复。

- 应用内全部 httpx 客户端必须 trust_env=False：绝不信任环境代理变量。
- DashScope 临时上传的 upload_host 必须逐次校验（HTTPS/公网/无 userinfo，
  REQ-052），校验失败脱敏拒绝且不发起任何出站。
- relay 服务：Bearer 密钥恒时比较；返回 URL 只由
  VIDEO_RELAY_PUBLIC_BASE_URL 与随机 token 构造，不信任 X-Forwarded-*。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters.media_ai import _httpx_models_probe
from app.adapters.video_ai import QwenVideoAdapter, RelayClient

RELAY_APP = Path(__file__).resolve().parents[2] / "tools" / "video-relay" / "app.py"


class _ClientSpy:
    """包装 httpx.Client：记录构造 kwargs，并注入 MockTransport 隔离网络。"""

    def __init__(self, real: type, recorder: list[dict], responder) -> None:
        self._real = real
        self._recorder = recorder
        self._responder = responder

    def __call__(self, **kwargs):
        self._recorder.append(dict(kwargs))
        kwargs.setdefault("transport", httpx.MockTransport(self._responder))
        return self._real(**kwargs)


@pytest.fixture()
def capture_httpx(monkeypatch: pytest.MonkeyPatch):
    recorder: list[dict] = []

    def install(responder):
        spy = _ClientSpy(httpx.Client, recorder, responder)
        monkeypatch.setattr(httpx, "Client", spy)
        return recorder

    yield install


def test_models_probe_ignores_environment_proxy(capture_httpx) -> None:
    recorder = capture_httpx(lambda request: httpx.Response(200))
    _httpx_models_probe("https://api.example.com/v1", "key", 1.0)
    assert recorder, "httpx.Client 未被调用"
    assert recorder[0].get("trust_env") is False


def test_relay_uploader_ignores_environment_proxy(capture_httpx, tmp_path) -> None:
    recorder = capture_httpx(
        lambda request: httpx.Response(200, json={"url": "https://relay.example.com/f/ab"})
    )
    sample = tmp_path / "v.mp4"
    sample.write_bytes(b"0" * 16)
    url = RelayClient._default_uploader(sample, "https://relay.example.com", "secret")
    assert url == "https://relay.example.com/f/ab"
    assert recorder and recorder[0].get("trust_env") is False


def _qwen_adapter(monkeypatch: pytest.MonkeyPatch) -> QwenVideoAdapter:
    monkeypatch.setenv("YUANZHIKU_TEST", "1")
    return QwenVideoAdapter(
        lambda: {"ai_video_provider": "qwen"},
        lambda: {"video_qwen": "k"},
        RelayClient(lambda: {}, lambda: {}),
    )


def test_dashscope_policy_fetch_ignores_environment_proxy(
    capture_httpx, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _qwen_adapter(monkeypatch)
    recorder = capture_httpx(lambda request: httpx.Response(400))
    with pytest.raises(RuntimeError):
        adapter._dashscope_temp_url(Path("x.mp4"))
    assert recorder and recorder[0].get("trust_env") is False


def test_dashscope_upload_host_https_public_required(
    capture_httpx, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _qwen_adapter(monkeypatch)
    sent: list[str] = []
    current: dict[str, str] = {"host": ""}

    def responder(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={"data": {"upload_host": current["host"], "key": "k", "upload_dir": "d"}})

    capture_httpx(responder)
    for upload_host in ("http://192.168.1.8:9000", "https://user:pass@oss.example.com", ""):
        current["host"] = upload_host
        with pytest.raises(RuntimeError):
            adapter._dashscope_temp_url(Path("x.mp4"))
    # 校验失败时绝不向 upload_host 发起任何请求（仅 policy 拉取经过 mock）。
    assert sent and all(
        "192.168.1.8" not in url and "user:pass" not in url and url != "" for url in sent
    ), sent


def test_dashscope_upload_host_valid_passes(capture_httpx, monkeypatch, tmp_path) -> None:
    adapter = _qwen_adapter(monkeypatch)
    policy: dict = {}
    sample = tmp_path / "v.mp4"
    sample.write_bytes(b"0" * 8)

    def responder(request: httpx.Request) -> httpx.Response:
        if (request.url.host or "").endswith("dashscope.aliyuncs.com"):
            return httpx.Response(200, json={"data": policy})
        return httpx.Response(201)  # 上传端点

    recorder = capture_httpx(responder)
    policy.update({"upload_host": "https://oss.example.com", "key": "k", "upload_dir": "d"})
    url = adapter._dashscope_temp_url(sample)
    assert url == "https://oss.example.com/d/k"
    assert recorder and recorder[0].get("trust_env") is False


def _load_relay_module(monkeypatch: pytest.MonkeyPatch, *, with_public_base: bool = True):
    monkeypatch.setenv("VIDEO_RELAY_SECRET", "s" * 32)
    if with_public_base:
        monkeypatch.setenv("VIDEO_RELAY_PUBLIC_BASE_URL", "https://relay.example.com")
    else:
        monkeypatch.delenv("VIDEO_RELAY_PUBLIC_BASE_URL", raising=False)
    spec = importlib.util.spec_from_file_location("video_relay_app_under_test", RELAY_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_relay_upload_url_uses_public_base_only(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_RELAY_TOKEN_DIR", str(tmp_path / "tokens"))
    module = _load_relay_module(monkeypatch)
    with TestClient(module.app) as client:
        response = client.post(
            "/upload",
            headers={"Authorization": f"Bearer {'s' * 32}"},
            files={"file": ("v.mp4", b"0" * 8, "application/octet-stream")},
        )
        assert response.status_code == 200
        url = response.json()["url"]
    assert url.startswith("https://relay.example.com/f/")
    assert len(url.rsplit("/", 1)[1]) >= 32


def test_relay_upload_ignores_forwarded_headers(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_RELAY_TOKEN_DIR", str(tmp_path / "tokens"))
    module = _load_relay_module(monkeypatch)
    with TestClient(module.app) as client:
        response = client.post(
            "/upload",
            headers={
                "Authorization": f"Bearer {'s' * 32}",
                "X-Forwarded-Host": "evil.example.com",
                "X-Forwarded-Proto": "http",
            },
            files={"file": ("v.mp4", b"0" * 8, "application/octet-stream")},
        )
    assert response.json()["url"].startswith("https://relay.example.com/")


def test_relay_upload_rejects_wrong_secret(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_RELAY_TOKEN_DIR", str(tmp_path / "tokens"))
    module = _load_relay_module(monkeypatch)
    with TestClient(module.app) as client:
        response = client.post(
            "/upload",
            headers={"Authorization": f"Bearer {'x' * 32}"},
            files={"file": ("v.mp4", b"0" * 8, "application/octet-stream")},
        )
        assert response.status_code == 401


def test_relay_missing_public_base_blocks_startup(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIDEO_RELAY_TOKEN_DIR", str(tmp_path / "tokens"))
    with pytest.raises(RuntimeError):
        _load_relay_module(monkeypatch, with_public_base=False)
