"""REQ-047b 链接元数据探测测试（POST /api/v1/videos/link/probe）。

纪律同 test_video_download.py：不触网真实平台；fake yt-dlp 子进程仅打印
合成 JSON；运行时数据只放 tests/runtime/<run-id>；回环代理生命周期经
测试注入工厂捕获断言，生产代码无豁免分支。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.downloader import LoopbackFilterProxy, YtDlpDownloader
from app.main import create_app
from app.ports.media import DownloadInputInvalid, DownloadUnavailable

RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "link-probe"

PROBE_FAILURE_MESSAGE = "链接失效、平台拒绝或探测超时，请重新复制分享链接或稍后重试"


class FakeProbeDownloader:
    """受控假下载器：实现探测端点所需端口面并记录调用；download 为哨兵。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        cookie_resolver=None,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.cookie_resolver = cookie_resolver
        self.result = result if result is not None else {
            "title": "平台标题", "author": "平台作者", "source_date": "2026-08-01",
        }
        self.error = error
        self.calls: list[dict] = []

    def capability(self) -> dict[str, object]:
        def available(platform: str) -> bool:
            if self.cookie_resolver is None:
                return False
            try:
                path = self.cookie_resolver(platform)
                return path.is_file() and path.stat().st_size <= 1024 * 1024
            except (OSError, ValueError):
                return False

        return {
            "enabled": self.enabled,
            "adapter": "yt-dlp",
            "version": "unit-1.0",
            "supported_platforms": ["bilibili", "douyin"],
            "cookies": {platform: available(platform) for platform in ("bilibili", "douyin")},
            "network": True,
        }

    def config_hash(self, platform: str, format_profile: str) -> str:
        return hashlib.sha256(f"unit:{platform}:{format_profile}".encode("ascii")).hexdigest()

    def probe_metadata(self, url: str, platform: str, use_cookie: bool) -> dict[str, str | None]:
        self.calls.append({"url": url, "platform": platform, "use_cookie": use_cookie})
        if self.error == "unavailable":
            raise DownloadUnavailable("unit")
        if self.error is not None:
            raise DownloadInputInvalid(self.error)
        return dict(self.result)

    def download(self, **kwargs):  # 哨兵：探测链路绝不触发下载
        raise AssertionError("probe endpoint must never enqueue or run a download")


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
    services = app.state.services
    downloader = FakeProbeDownloader(cookie_resolver=services.paths.download_cookie_file)
    services.downloader = downloader
    services.jobs.downloader = downloader
    with TestClient(app) as client:
        yield client, services, downloader


def _probe(client: TestClient, url: str = "https://www.bilibili.com/video/BV1test", **overrides):
    body = {"url": url, "platform": "bilibili", **overrides}
    return client.post("/api/v1/videos/link/probe", json=body)


# --- 用例 1：端点成功与只读语义 ---

def test_probe_success_returns_metadata_without_persistence(client_and_services) -> None:
    client, services, downloader = client_and_services
    jobs_before = {job["id"] for job in services.repository.list_jobs()}
    response = _probe(client)
    assert response.status_code == 200, response.text
    assert response.json() == {"title": "平台标题", "author": "平台作者", "source_date": "2026-08-01"}
    assert downloader.calls == [
        {"url": "https://www.bilibili.com/video/BV1test", "platform": "bilibili", "use_cookie": False}
    ]
    # 只读：不入队任何作业、不写任何来源表
    assert {job["id"] for job in services.repository.list_jobs()} == jobs_before
    assert services.repository.list_sources() == []


def test_probe_nullable_fields_pass_through(client_and_services) -> None:
    client, _, downloader = client_and_services
    downloader.result = {"title": None, "author": None, "source_date": None}
    response = _probe(client, "https://v.douyin.com/abcdef/", platform="douyin")
    assert response.status_code == 200, response.text
    assert response.json() == {"title": None, "author": None, "source_date": None}


# --- 用例 2：白名单与平台校验（与 /videos/link 完全同规则） ---

@pytest.mark.parametrize("url", [
    "http://www.bilibili.com/video/BV1test",           # 非 HTTPS
    "https://www.evil.com/video/BV1test",              # 非白名单域
    "https://douyin.com.evil.com/video/123",           # 子域冒充
    "https://user:password@www.douyin.com/video/123",  # 内嵌凭据
    "https://127.0.0.1/video/123",                     # 回环字面量
    "https://10.0.0.8/video/123",                      # 内网字面量
])
def test_probe_url_whitelist_rejections(client_and_services, url: str) -> None:
    client, _, downloader = client_and_services
    response = _probe(client, url)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_url"
    # 拒绝消息不含 URL 内容
    assert url not in response.text
    assert "bilibili" not in detail["message"] and "douyin" not in detail["message"]
    assert downloader.calls == []  # 校验失败绝不启动探测


def test_probe_platform_mismatch_and_unsupported(client_and_services) -> None:
    client, _, downloader = client_and_services
    mismatch = _probe(client, "https://www.douyin.com/video/123", platform="bilibili")
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "invalid_url"
    unsupported = _probe(client, "https://www.bilibili.com/video/1", platform="youtube")
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_platform"
    assert downloader.calls == []


# --- 用例 3：Cookie 规则（同 REQ-047a，绝不静默回退） ---

def test_probe_use_cookie_without_imported_file_rejected(client_and_services) -> None:
    client, services, downloader = client_and_services
    response = _probe(client, use_cookie=True)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cookie_file_unavailable"
    assert downloader.calls == []  # 绝不静默回退为无 Cookie 探测
    assert services.repository.list_sources() == []


def test_probe_use_cookie_with_imported_file(client_and_services) -> None:
    client, _, downloader = client_and_services
    uploaded = client.post(
        "/api/v1/settings/download-cookies/bilibili",
        files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\nsession", "text/plain")},
    )
    assert uploaded.status_code == 204
    response = _probe(client, use_cookie=True)
    assert response.status_code == 200, response.text
    assert downloader.calls[0]["use_cookie"] is True
    # 仅 bilibili 已导入：douyin 探测仍按该平台未导入拒绝
    douyin = _probe(client, "https://www.douyin.com/video/123", platform="douyin", use_cookie=True)
    assert douyin.status_code == 422
    assert douyin.json()["detail"]["code"] == "cookie_file_unavailable"


# --- 用例 4：不可用与失败的脱敏映射 ---

def test_probe_downloader_unavailable_returns_503(client_and_services) -> None:
    client, _, downloader = client_and_services
    downloader.enabled = False
    response = _probe(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "downloader_unavailable"
    assert downloader.calls == []


def test_probe_adapter_unavailable_maps_to_503(client_and_services) -> None:
    client, _, downloader = client_and_services
    downloader.error = "unavailable"
    response = _probe(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "downloader_unavailable"


@pytest.mark.parametrize("error", ["failed", "timeout"], ids=["process_failed", "timeout"])
def test_probe_failures_are_generic_without_url(client_and_services, error: str) -> None:
    client, _, downloader = client_and_services
    downloader.error = error
    url = "https://www.bilibili.com/video/BV1secret?from=share"
    response = _probe(client, url)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "probe_failed"
    assert detail["message"] == PROBE_FAILURE_MESSAGE
    # 脱敏：响应绝不携带 URL 内容
    assert "BV1secret" not in response.text and "from=share" not in response.text


def test_probe_adapter_cookie_backstop_maps_to_422(client_and_services) -> None:
    # 端点预检之后的适配器兜底：cookie 文件不可用 → 同语义的 422
    client, _, downloader = client_and_services
    downloader.error = "cookie"
    response = _probe(client, use_cookie=False)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cookie_file_unavailable"


# --- 用例 5：适配器级（真实监控路径 + fake yt_dlp 子进程） ---

_FAKE_PROBE_MAIN = """
import json
import os
import sys
import time

mode = os.environ.get("FAKE_PROBE_MODE", "ok")

if mode == "sleep":
    with open(os.environ["FAKE_PROBE_PID"], "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(300)
elif mode == "fail":
    sys.exit(1)
elif mode == "bad_json":
    print("not-a-json-payload")
elif mode == "capture_args":
    payload = {
        "argv": sys.argv,
        "stdin_eof": sys.stdin.read() == "",
        "proxy_env": {key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")},
    }
    with open(os.environ["FAKE_PROBE_CAPTURE"], "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    print(json.dumps({"title": "合成标题", "uploader": "合成作者", "upload_date": "20260801"}))
elif mode == "channel_fallback":
    print(json.dumps({"title": "  带空白\\n标题  ", "channel": "频道名", "upload_date": "2026-08-01"}))
else:
    print(json.dumps({"title": "合成标题", "uploader": "合成作者", "upload_date": "20260801"}))
"""


@pytest.fixture()
def fake_probe_ytdlp(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    package = runtime_root / "fake-ytdlp"
    (package / "yt_dlp").mkdir(parents=True)
    (package / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
    (package / "yt_dlp" / "__main__.py").write_text(_FAKE_PROBE_MAIN, encoding="utf-8")
    previous = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", str(package) + os.pathsep + (previous or ""))
    return package


def _probe_downloader(proxies: list[LoopbackFilterProxy], **kwargs) -> YtDlpDownloader:
    def factory(platform: str) -> LoopbackFilterProxy:
        proxy = LoopbackFilterProxy(("test.invalid",))
        proxies.append(proxy)
        return proxy

    return YtDlpDownloader(proxy_factory=factory, **kwargs)


def test_adapter_probe_success_and_proxy_lifecycle(fake_probe_ytdlp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_PROBE_MODE", "ok")
    proxies: list[LoopbackFilterProxy] = []
    downloader = _probe_downloader(proxies)
    result = downloader.probe_metadata("https://test.invalid/video", "bilibili", False)
    assert result == {"title": "合成标题", "author": "合成作者", "source_date": "2026-08-01"}
    # 请求结束代理即销毁，不留存端口
    assert len(proxies) == 1 and proxies[0]._closing is True


def test_adapter_probe_channel_fallback_and_date_cleaning(fake_probe_ytdlp, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_PROBE_MODE", "channel_fallback")
    downloader = _probe_downloader([])
    result = downloader.probe_metadata("https://test.invalid/video", "douyin", False)
    # uploader 缺失回退 channel；标题去控制字符/空白；非法日期 → None
    assert result == {"title": "带空白标题", "author": "频道名", "source_date": None}


def test_adapter_probe_argv_and_subprocess_constraints(
    fake_probe_ytdlp, runtime_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_PROBE_MODE", "capture_args")
    capture = runtime_root / "capture.json"
    monkeypatch.setenv("FAKE_PROBE_CAPTURE", str(capture))
    # 环境代理指向死端口：子进程必须被清空（显式 --proxy + env 双保险）
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    proxies: list[LoopbackFilterProxy] = []
    downloader = _probe_downloader(proxies)
    result = downloader.probe_metadata("https://test.invalid/video", "bilibili", False)
    assert result["title"] == "合成标题"
    payload = json.loads(capture.read_text(encoding="utf-8"))
    argv = payload["argv"]
    assert "--skip-download" in argv
    assert "%()j" in argv
    assert argv[argv.index("--proxy") + 1].startswith("http://127.0.0.1:")
    assert "--ignore-config" in argv and "--no-cache-dir" in argv
    assert argv[-1] == "https://test.invalid/video"
    assert payload["stdin_eof"] is True  # stdin 关闭
    assert payload["proxy_env"] == {"HTTP_PROXY": None, "HTTPS_PROXY": None, "ALL_PROXY": None, "NO_PROXY": None}
    assert proxies[0]._closing is True


def test_adapter_probe_cookie_file_passed_and_missing_rejected(
    fake_probe_ytdlp, runtime_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_PROBE_MODE", "capture_args")
    capture = runtime_root / "capture.json"
    monkeypatch.setenv("FAKE_PROBE_CAPTURE", str(capture))
    cookies_dir = runtime_root / "cookies"
    cookies_dir.mkdir()
    bilibili_cookie = cookies_dir / "bilibili.txt"
    bilibili_cookie.write_bytes(b"# Netscape HTTP Cookie File\nsession")
    downloader = _probe_downloader([], cookie_resolver=lambda platform: cookies_dir / f"{platform}.txt")
    result = downloader.probe_metadata("https://test.invalid/video", "bilibili", True)
    assert result["author"] == "合成作者"
    argv = json.loads(capture.read_text(encoding="utf-8"))["argv"]
    # 按平台取用：bilibili 探测只传 bilibili.txt
    assert argv[argv.index("--cookies") + 1] == str(bilibili_cookie)

    # 该平台未导入文件 → 与下载一致的拒绝语义，绝不静默回退
    with pytest.raises(DownloadInputInvalid) as excinfo:
        downloader.probe_metadata("https://test.invalid/video", "douyin", True)
    assert excinfo.value.args[0] == "cookie"
    # 超 1MB 上限同样拒绝
    bilibili_cookie.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(DownloadInputInvalid) as oversized:
        downloader.probe_metadata("https://test.invalid/video", "bilibili", True)
    assert oversized.value.args[0] == "cookie"


def test_adapter_probe_process_failure_and_bad_json(fake_probe_ytdlp, monkeypatch: pytest.MonkeyPatch) -> None:
    for mode in ("fail", "bad_json"):
        monkeypatch.setenv("FAKE_PROBE_MODE", mode)
        downloader = _probe_downloader([])
        with pytest.raises(DownloadInputInvalid) as excinfo:
            downloader.probe_metadata("https://test.invalid/video", "bilibili", False)
        assert excinfo.value.args[0] == "failed"


def test_adapter_probe_timeout_terminates_process(
    fake_probe_ytdlp, runtime_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_PROBE_MODE", "sleep")
    pid_file = runtime_root / "probe.pid"
    monkeypatch.setenv("FAKE_PROBE_PID", str(pid_file))
    downloader = _probe_downloader([])
    downloader.probe_timeout_seconds = 0.5
    started = time.monotonic()
    with pytest.raises(DownloadInputInvalid) as excinfo:
        downloader.probe_metadata("https://test.invalid/video", "bilibili", False)
    assert excinfo.value.args[0] == "timeout"
    assert time.monotonic() - started < 30  # 绝不挂到 fake 的 300s
    import psutil

    child_pid = int(pid_file.read_text())
    for _ in range(20):
        if not psutil.pid_exists(child_pid):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid)


def test_adapter_probe_unknown_platform_rejected_before_proxy() -> None:
    proxies: list[LoopbackFilterProxy] = []
    downloader = _probe_downloader(proxies)
    with pytest.raises(DownloadInputInvalid) as excinfo:
        downloader.probe_metadata("https://test.invalid/video", "youtube", False)
    assert excinfo.value.args[0] == "platform"
    assert proxies == []  # 平台非法：代理绝不启动


# --- 用例 6：输出清洗与日期归一化（纯函数） ---

def test_probe_output_cleaning_and_date_normalization() -> None:
    downloader = YtDlpDownloader()
    long_author = "作" * 400
    parsed = downloader._parse_probe_metadata(
        json.dumps({"title": "标题", "uploader": long_author, "upload_date": "20260102"}).encode("utf-8")
    )
    assert parsed == {"title": "标题", "author": "作" * 300, "source_date": "2026-01-02"}
    empty = downloader._parse_probe_metadata(b"{}")
    assert empty == {"title": None, "author": None, "source_date": None}
    for invalid in ("2026-01-02", "20261340", "", "2026010", 20260102, None):
        assert YtDlpDownloader._normalize_upload_date(invalid) is None
    assert YtDlpDownloader._normalize_upload_date("20240229") == "2024-02-29"  # 闰年合法
    with pytest.raises(DownloadInputInvalid):
        downloader._parse_probe_metadata(b"[1, 2, 3]")
