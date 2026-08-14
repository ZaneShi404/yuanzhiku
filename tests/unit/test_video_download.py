"""T-VID-003（单元负面用例）与 T-VID-004（合成集成）链接下载测试。

纪律：不触网真实平台；fixture 与运行时数据只放 tests/fixtures 与
tests/runtime/<run-id>；FFmpeg/ffprobe 未安装时沿用 unit analyzer 假件模式；
回环过滤代理的"保留段拒绝豁免"仅测试子类注入（决策 9），生产代码无该分支。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import sqlite3
import ssl
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app.adapters.downloader import (
    DOWNLOAD_REGISTRY,
    LoopbackFilterProxy,
    YtDlpDownloader,
    host_matches_registered_domain,
    registered_domains,
)
from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, VideoMetadata
from app.domain.models import sanitize_download_url
from app.main import create_app
from app.ports.media import (
    DownloadedVideo,
    DownloadInputInvalid,
    DownloadProcessingCancelled,
    DownloadUnavailable,
    MediaInputInvalid,
    MediaProcessingCancelled,
)

RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "video-download"

GENERIC_FAILURE_MESSAGE = "链接失效、平台拒绝或下载产物无效，请重新复制分享链接或稍后重试"


class FakeMediaAnalyzer:
    """Unit analyzer fake：probe 分辨率/错误可配置，供下载产物校验与 video_analyze 链使用。"""

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        probe_error: str | None = None,
        probe_cancel: bool = False,
    ) -> None:
        self.width = width
        self.height = height
        self.probe_error = probe_error
        self.probe_cancel = probe_cancel

    def capability(self) -> dict[str, object]:
        return {"enabled": True, "adapter": "unit", "network": False}

    def config_hash(self, maximum_frames: int) -> str:
        return hashlib.sha256(f"unit:{maximum_frames}".encode("ascii")).hexdigest()

    def probe(self, artifact_path: Path, limits: MediaProcessingLimits, cancelled, heartbeat) -> VideoMetadata:
        assert artifact_path.is_file()
        if self.probe_cancel:
            # 模拟产物校验阶段收到协作取消
            raise MediaProcessingCancelled()
        assert not cancelled()
        heartbeat()
        if self.probe_error is not None:
            raise MediaInputInvalid(self.probe_error)
        return VideoMetadata("mov,mp4,m4a,3gp,3g2,mj2", 10_000, self.width, self.height, "h264", "aac")

    def extract_frames(
        self, artifact_path: Path, metadata: VideoMetadata, workspace: Path, maximum_frames: int,
        limits: MediaProcessingLimits, cancelled, heartbeat,
    ) -> tuple[ExtractedVideoFrame, ...]:
        assert artifact_path.is_file()
        frames: list[ExtractedVideoFrame] = []
        for ordinal in range(min(maximum_frames, 2)):
            assert not cancelled()
            path = workspace / f"frame-{ordinal}.jpg"
            path.write_bytes(b"synthetic-jpeg-frame-" + bytes([ordinal]))
            heartbeat()
            frames.append(ExtractedVideoFrame(ordinal, (ordinal + 1) * 3_000, path, 320, 180))
        return tuple(frames)


class FakeDownloader:
    """受控假下载器：记录每次调用的参数与 staging 内 Cookie 拷贝内容。"""

    format_profile = "unit:res:1080+mp4-remux"

    def __init__(
        self,
        *,
        enabled: bool = True,
        cookie_file_path: Path | None = None,
        outcome: str = "ok",
        product: bytes = b"downloaded-mp4-bytes",
        title: str = "",
    ) -> None:
        self.enabled = enabled
        self.cookie_file_path = cookie_file_path
        self.outcome = outcome
        self.product = product
        self.title = title
        self.calls: list[dict] = []

    def capability(self) -> dict[str, object]:
        available = False
        if self.cookie_file_path is not None:
            try:
                available = self.cookie_file_path.is_file() and self.cookie_file_path.stat().st_size <= 1024 * 1024
            except OSError:
                available = False
        return {
            "enabled": self.enabled,
            "adapter": "yt-dlp",
            "version": "unit-1.0",
            "supported_platforms": ["bilibili", "douyin"],
            "cookie_file_available": available,
            "network": True,
        }

    def config_hash(self, platform: str, format_profile: str) -> str:
        return hashlib.sha256(f"unit:{platform}:{format_profile}".encode("ascii")).hexdigest()

    def download(self, *, url, platform, workspace, limits, use_cookie, cookie_path, cancelled, heartbeat, progress):
        record = {"url": url, "platform": platform, "use_cookie": use_cookie, "workspace": Path(workspace)}
        workspace.mkdir(parents=True, exist_ok=True)
        if cookie_path is not None:
            record["cookie_path"] = Path(cookie_path)
            record["cookie_content"] = cookie_path.read_bytes() if cookie_path.is_file() else None
        self.calls.append(record)
        if self.outcome == "unavailable":
            raise DownloadUnavailable("unit")
        if self.outcome == "ffmpeg_missing":
            raise DownloadUnavailable("ffmpeg_missing")
        if self.outcome == "input_invalid":
            raise DownloadInputInvalid("unit")
        if self.outcome == "cancelled":
            raise DownloadProcessingCancelled()
        product = workspace / "video.mp4"
        product.write_bytes(self.product)
        return DownloadedVideo("video.mp4", "video/mp4", len(self.product), title=self.title)


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
    downloader = FakeDownloader(cookie_file_path=services.paths.download / "cookies.txt")
    services.downloader = downloader
    services.jobs.downloader = downloader
    services.videos.analyzer = FakeMediaAnalyzer()
    with TestClient(app) as client:
        yield client, services, downloader


def _submit_link(client: TestClient, url: str, **overrides):
    body = {"url": url, "platform": "bilibili", "rights": "owned", **overrides}
    return client.post("/api/v1/videos/link", json=body)


def _claim_and_run(services) -> dict:
    job = services.jobs.run_once()
    assert job is not None
    return job


# --- 用例 1：URL 白名单（API 层） ---

@pytest.mark.parametrize("url", [
    "http://www.bilibili.com/video/BV1test",
    "https://www.evil.com/video/BV1test",
    "https://douyin.com.evil.com/video/123",
    "https://evil-bilibili.com/video/123",
    "https://user:password@www.douyin.com/video/123",
    "https://www.bilibili.com/video/" + "a" * 4096,
    "https://127.0.0.1/video/123",
    "https://10.0.0.8/video/123",
    "https://192.168.1.2/video/123",
    "https://169.254.10.10/video/123",
    "https://100.64.0.1/video/123",
    "https://192.0.2.1/video/123",
    "https://[::1]/video/123",
])
def test_download_url_whitelist_rejections(client_and_services, url: str) -> None:
    client, _, _ = client_and_services
    response = _submit_link(client, url)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_url"
    # 拒绝消息不含 URL 内容
    assert "bilibili" not in detail["message"] and "douyin" not in detail["message"]
    assert url not in detail["message"]


def test_download_url_whitelist_accepts_registered_hosts_and_b23_tv(client_and_services) -> None:
    client, _, _ = client_and_services
    for url, platform in [
        ("https://www.bilibili.com/video/BV1test", "bilibili"),
        ("https://api.bilibili.com/x/player", "bilibili"),
        ("https://b23.tv/abcdef", "bilibili"),
        ("https://v.douyin.com/abcdef/", "douyin"),
        ("https://www.douyin.com/video/123", "douyin"),
    ]:
        created = _submit_link(client, url, platform=platform)
        assert created.status_code == 201, created.text
        assert created.json()["kind"] == "video_download"


def test_download_url_platform_mismatch_rejected(client_and_services) -> None:
    client, _, _ = client_and_services
    b23_as_douyin = _submit_link(client, "https://b23.tv/abcdef", platform="douyin")
    assert b23_as_douyin.status_code == 422
    assert b23_as_douyin.json()["detail"]["code"] == "invalid_url"
    douyin_as_bilibili = _submit_link(client, "https://www.douyin.com/video/123", platform="bilibili")
    assert douyin_as_bilibili.status_code == 422
    assert douyin_as_bilibili.json()["detail"]["code"] == "invalid_url"
    unsupported = _submit_link(client, "https://www.bilibili.com/video/1", platform="youtube")
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "unsupported_platform"


def test_download_link_requires_rights_and_valid_categories(client_and_services) -> None:
    client, _, _ = client_and_services
    missing = client.post("/api/v1/videos/link", json={"url": "https://www.bilibili.com/video/BV1test", "platform": "bilibili"})
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "request_validation"
    invalid_rights = _submit_link(client, "https://www.bilibili.com/video/BV1test", rights="stolen")
    assert invalid_rights.status_code == 422
    assert invalid_rights.json()["detail"]["code"] == "request_validation"
    invalid_category = _submit_link(client, "https://www.bilibili.com/video/BV1test", categories=["not-a-category"])
    assert invalid_category.status_code == 422
    assert invalid_category.json()["detail"]["code"] == "request_validation"


# --- 用例 2：Cookie 单通道治理 ---

def test_cookie_upload_size_limit_overwrite_and_idempotent_delete(client_and_services) -> None:
    client, services, _ = client_and_services
    too_large = client.post(
        "/api/v1/settings/download-cookie",
        files={"file": ("cookies.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
    )
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "cookie_file_too_large"
    assert not (services.paths.download / "cookies.txt").exists()

    first = client.post(
        "/api/v1/settings/download-cookie",
        files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\ncontent-one", "text/plain")},
    )
    assert first.status_code == 204
    cookie_file = services.paths.download / "cookies.txt"
    assert cookie_file.read_bytes() == b"# Netscape HTTP Cookie File\ncontent-one"
    capabilities = client.get("/api/v1/capabilities").json()
    assert capabilities["downloader"]["cookie_file_available"] is True

    second = client.post(
        "/api/v1/settings/download-cookie",
        files={"file": ("cookies.txt", b"content-two", "text/plain")},
    )
    assert second.status_code == 204
    assert cookie_file.read_bytes() == b"content-two"

    deleted = client.delete("/api/v1/settings/download-cookie")
    assert deleted.status_code == 204
    assert not cookie_file.exists()
    deleted_again = client.delete("/api/v1/settings/download-cookie")
    assert deleted_again.status_code == 204
    assert client.get("/api/v1/capabilities").json()["downloader"]["cookie_file_available"] is False


def test_use_cookie_without_imported_file_rejected_without_fallback(client_and_services) -> None:
    client, services, _ = client_and_services
    response = _submit_link(client, "https://www.bilibili.com/video/BV1test", use_cookie=True)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cookie_file_unavailable"
    # 绝不静默回退：未创建任何无 Cookie 下载作业
    kinds = [job["kind"] for job in services.repository.list_jobs()]
    assert "video_download" not in kinds


def test_cookie_copy_is_staging_scoped_and_original_untouched(client_and_services) -> None:
    client, services, downloader = client_and_services
    original = b"# Netscape HTTP Cookie File\nsession-cookie"
    client.post("/api/v1/settings/download-cookie", files={"file": ("cookies.txt", original, "text/plain")})
    assert client.post(
        "/api/v1/videos/link",
        json={"url": "https://www.bilibili.com/video/BV1test", "platform": "bilibili", "rights": "owned", "use_cookie": True},
    ).status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == "succeeded"
    call = downloader.calls[0]
    assert call["use_cookie"] is True
    # 下载执行期间 staging 内存在 Cookie 拷贝且内容与原件一致
    assert call["cookie_content"] == original
    assert call["workspace"].name != "cookies.txt"
    assert not call["workspace"].exists()  # 作业结束 staging 与 Cookie 拷贝即删
    assert (services.paths.download / "cookies.txt").read_bytes() == original  # 原文件未被修改


def test_use_cookie_false_never_reads_cookie_file(client_and_services) -> None:
    client, services, downloader = client_and_services
    original = b"# Netscape HTTP Cookie File\nsession-cookie"
    client.post("/api/v1/settings/download-cookie", files={"file": ("cookies.txt", original, "text/plain")})
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test", use_cookie=False).status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == "succeeded"
    call = downloader.calls[0]
    assert call["use_cookie"] is False
    assert call.get("cookie_content") is None  # 全程未注入 Cookie 拷贝
    assert (services.paths.download / "cookies.txt").read_bytes() == original


@pytest.mark.parametrize("outcome", ["input_invalid", "cancelled"], ids=["failed", "cancelled"])
def test_cookie_copy_removed_on_failure_and_cancel(client_and_services, outcome: str) -> None:
    client, services, downloader = client_and_services
    original = b"# Netscape HTTP Cookie File\nsession-cookie"
    client.post("/api/v1/settings/download-cookie", files={"file": ("cookies.txt", original, "text/plain")})
    downloader.outcome = outcome
    assert client.post(
        "/api/v1/videos/link",
        json={"url": "https://www.bilibili.com/video/BV1test", "platform": "bilibili", "rights": "owned", "use_cookie": True},
    ).status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == ("failed" if outcome == "input_invalid" else "cancelled")
    call = downloader.calls[0]
    assert call["use_cookie"] is True
    assert call["cookie_content"] == original
    # 拷贝确实位于作业 staging 内，且作业结束（失败/取消）即删
    assert Path(call["cookie_path"]).parent == call["workspace"]
    assert not call["workspace"].exists()
    assert (services.paths.download / "cookies.txt").read_bytes() == original  # 原文件未被修改
    assert services.repository.list_sources() == []


def test_cookie_upload_content_length_preflight_rejects_before_parsing(client_and_services) -> None:
    client, services, _ = client_and_services
    # Content-Length 超过 1MB+表单开销边界：解析 multipart 前立即 413
    response = client.post(
        "/api/v1/settings/download-cookie",
        headers={"content-length": str(1024 * 1024 + 128 * 1024)},
        files={"file": ("cookies.txt", b"small-body", "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "cookie_file_too_large"
    assert not (services.paths.download / "cookies.txt").exists()


# --- 用例 3：断路器（适配器级，真实监控循环 + 假 yt_dlp 模块） ---

_FAKE_YTDLP_MAIN = """
import json
import os
import subprocess
import sys
import time

mode = os.environ.get("FAKE_YTDLP_MODE", "idle")
workspace = os.environ.get("FAKE_YTDLP_WORKSPACE", ".")

if mode == "spawn_child_sleep":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    with open(os.path.join(workspace, "child.pid"), "w") as handle:
        handle.write(str(child.pid))
    time.sleep(300)
elif mode == "write_big_then_sleep":
    with open(os.path.join(workspace, "big.bin"), "wb") as handle:
        handle.write(b"\\x00" * (4 * 1024 * 1024))
    time.sleep(300)
elif mode == "capture_args":
    with open(os.path.join(workspace, "argv.json"), "w") as handle:
        json.dump(sys.argv, handle)
    with open(os.path.join(workspace, "video.mp4"), "wb") as handle:
        handle.write(b"fake-mp4")
elif mode == "write_unmerged":
    with open(os.path.join(workspace, "video.f30077.mp4"), "wb") as handle:
        handle.write(b"video-stream")
    with open(os.path.join(workspace, "video.f30280.m4a"), "wb") as handle:
        handle.write(b"audio-stream")
elif mode == "write_partial_merge":
    with open(os.path.join(workspace, "video.mp4"), "wb") as handle:
        handle.write(b"video-only")
    with open(os.path.join(workspace, "video.f30280.m4a"), "wb") as handle:
        handle.write(b"audio-stream")
else:
    time.sleep(300)
"""


def _fake_ffmpeg_binary(tmp_path: Path) -> Path:
    """Windows 上 shutil.which 只需文件存在；供测试注入可解析的 ffmpeg。"""
    binary = tmp_path / "bin" / "ffmpeg.exe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"")
    return binary


@pytest.fixture()
def fake_ytdlp_environment(runtime_root: Path, monkeypatch: pytest.MonkeyPatch):
    package = runtime_root / "fake-ytdlp"
    (package / "yt_dlp").mkdir(parents=True)
    (package / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
    (package / "yt_dlp" / "__main__.py").write_text(_FAKE_YTDLP_MAIN, encoding="utf-8")
    previous = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", str(package) + os.pathsep + (previous or ""))
    return package


def _run_fake_download(
    tmp_path: Path, mode: str, limits: MediaProcessingLimits, no_progress_seconds: float,
    cancelled=lambda: False,
):
    workspace = tmp_path / "staging"
    workspace.mkdir()
    monkey_workspace = workspace
    os.environ["FAKE_YTDLP_MODE"] = mode
    os.environ["FAKE_YTDLP_WORKSPACE"] = str(monkey_workspace)
    downloader = YtDlpDownloader(
        ffmpeg=str(_fake_ffmpeg_binary(tmp_path)),
        proxy_factory=lambda platform: LoopbackFilterProxy(("test.invalid",)),
    )
    downloader.no_progress_seconds = no_progress_seconds
    try:
        return downloader.download(
            url="https://test.invalid/video",
            platform="bilibili",
            workspace=workspace,
            limits=limits,
            use_cookie=False,
            cookie_path=None,
            cancelled=cancelled,
            heartbeat=lambda: None,
            progress=lambda value, message: None,
        )
    finally:
        os.environ.pop("FAKE_YTDLP_MODE", None)
        os.environ.pop("FAKE_YTDLP_WORKSPACE", None)


def test_downloader_adds_ffmpeg_location_when_resolvable(
    fake_ytdlp_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "staging"
    workspace.mkdir()
    fake_bin = _fake_ffmpeg_binary(tmp_path)
    monkeypatch.setenv("FAKE_YTDLP_MODE", "capture_args")
    monkeypatch.setenv("FAKE_YTDLP_WORKSPACE", str(workspace))
    downloader = YtDlpDownloader(
        ffmpeg=str(fake_bin),
        proxy_factory=lambda platform: LoopbackFilterProxy(("test.invalid",)),
    )
    result = downloader.download(
        url="https://test.invalid/video", platform="bilibili", workspace=workspace,
        limits=MediaProcessingLimits(30.0, 1024 ** 3, 1024 ** 3),
        use_cookie=False, cookie_path=None,
        cancelled=lambda: False, heartbeat=lambda: None,
        progress=lambda value, message: None,
    )
    assert result.filename == "video.mp4"
    argv = json.loads((workspace / "argv.json").read_text(encoding="utf-8"))
    assert "--ffmpeg-location" in argv
    assert argv[argv.index("--ffmpeg-location") + 1] == str(fake_bin.parent)
    # 双保险：子进程环境 PATH 前置 FFmpeg 目录（大小写不敏感查找键）
    env = downloader._subprocess_environment()
    path_key = next(key for key in env if key.upper() == "PATH")
    assert env[path_key].startswith(str(fake_bin.parent) + os.pathsep)


def test_downloader_blocks_when_ffmpeg_unresolvable(tmp_path: Path) -> None:
    workspace = tmp_path / "staging"
    workspace.mkdir()
    downloader = YtDlpDownloader(
        ffmpeg="definitely-missing-ffmpeg-binary-xyz",
        proxy_factory=lambda platform: LoopbackFilterProxy(("test.invalid",)),
    )
    with pytest.raises(DownloadUnavailable) as excinfo:
        downloader.download(
            url="https://test.invalid/video", platform="bilibili", workspace=workspace,
            limits=MediaProcessingLimits(30.0, 1024 ** 3, 1024 ** 3),
            use_cookie=False, cookie_path=None,
            cancelled=lambda: False, heartbeat=lambda: None,
            progress=lambda value, message: None,
        )
    assert excinfo.value.args[0] == "ffmpeg_missing"


@pytest.mark.parametrize("mode", ["write_unmerged", "write_partial_merge"], ids=["no_merged", "residue_left"])
def test_downloader_rejects_unmerged_residues(
    fake_ytdlp_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    workspace = tmp_path / "staging"
    workspace.mkdir()
    monkeypatch.setenv("FAKE_YTDLP_MODE", mode)
    monkeypatch.setenv("FAKE_YTDLP_WORKSPACE", str(workspace))
    downloader = YtDlpDownloader(
        ffmpeg=str(_fake_ffmpeg_binary(tmp_path)),
        proxy_factory=lambda platform: LoopbackFilterProxy(("test.invalid",)),
    )
    with pytest.raises(DownloadInputInvalid) as excinfo:
        downloader.download(
            url="https://test.invalid/video", platform="bilibili", workspace=workspace,
            limits=MediaProcessingLimits(30.0, 1024 ** 3, 1024 ** 3),
            use_cookie=False, cookie_path=None,
            cancelled=lambda: False, heartbeat=lambda: None,
            progress=lambda value, message: None,
        )
    assert excinfo.value.args[0] == "unmerged_output"


@pytest.mark.parametrize(
    ("mode", "limits", "no_progress_seconds", "expected"),
    [
        ("idle", MediaProcessingLimits(0.5, 1024 ** 3, 1024 ** 3), 10.0, "timeout"),
        ("write_big_then_sleep", MediaProcessingLimits(30.0, 1024 ** 3, 1024 * 1024), 10.0, "workspace_limit"),
        ("idle", MediaProcessingLimits(30.0, 1, 1024 ** 3), 10.0, "memory_limit"),
        ("idle", MediaProcessingLimits(30.0, 1024 ** 3, 1024 ** 3), 0.5, "no_progress"),
    ],
    ids=["timeout", "workspace_limit", "memory_limit", "no_progress"],
)
def test_downloader_circuit_breakers(
    fake_ytdlp_environment, tmp_path: Path, mode: str, limits: MediaProcessingLimits,
    no_progress_seconds: float, expected: str,
) -> None:
    with pytest.raises(DownloadInputInvalid) as excinfo:
        _run_fake_download(tmp_path, mode, limits, no_progress_seconds)
    assert excinfo.value.args[0] == expected


def test_downloader_cancel_terminates_process_tree(
    fake_ytdlp_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "staging"
    workspace.mkdir()
    monkeypatch.setenv("FAKE_YTDLP_MODE", "spawn_child_sleep")
    monkeypatch.setenv("FAKE_YTDLP_WORKSPACE", str(workspace))
    checks: list[bool] = []

    def cancelled() -> bool:
        checks.append(True)
        return len(checks) > 8

    downloader = YtDlpDownloader(
        ffmpeg=str(_fake_ffmpeg_binary(tmp_path)),
        proxy_factory=lambda platform: LoopbackFilterProxy(("test.invalid",)),
    )
    with pytest.raises(DownloadProcessingCancelled):
        downloader.download(
            url="https://test.invalid/video", platform="bilibili", workspace=workspace,
            limits=MediaProcessingLimits(30.0, 1024 ** 3, 1024 ** 3),
            use_cookie=False, cookie_path=None, cancelled=cancelled,
            heartbeat=lambda: None, progress=lambda value, message: None,
        )
    import psutil

    child_pid = int((workspace / "child.pid").read_text())
    for _ in range(20):
        if not psutil.pid_exists(child_pid):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(child_pid)


# --- 用例 4：取消清理与失败语义 ---

def test_download_job_failure_semantics_are_generic(client_and_services) -> None:
    client, services, downloader = client_and_services
    url = "https://www.bilibili.com/video/BV1test"

    downloader.outcome = "unavailable"
    _submit_link(client, url)
    blocked = _claim_and_run(services)
    assert blocked["kind"] == "video_download" and blocked["state"] == "blocked"
    assert "yt-dlp" in blocked["message"]

    downloader.outcome = "input_invalid"
    _submit_link(client, url)
    failed = _claim_and_run(services)
    assert failed["kind"] == "video_download" and failed["state"] == "failed"
    assert failed["message"] == GENERIC_FAILURE_MESSAGE
    assert url not in failed["message"]

    downloader.outcome = "cancelled"
    _submit_link(client, url)
    cancelled_job = _claim_and_run(services)
    assert cancelled_job["kind"] == "video_download" and cancelled_job["state"] == "cancelled"
    assert cancelled_job["message"] == "链接下载已取消"
    # 任何失败路径都不残留半成品 source
    assert services.repository.list_sources() == []


def test_download_cancel_leaves_no_staging_or_source(client_and_services) -> None:
    client, services, downloader = client_and_services
    downloader.outcome = "cancelled"
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test").status_code == 201
    cancelled_job = _claim_and_run(services)
    assert cancelled_job["state"] == "cancelled"
    assert services.repository.list_sources() == []
    staging_entries = [item for item in (services.paths.staging).iterdir()] if services.paths.staging.exists() else []
    assert staging_entries == []
    workspace = downloader.calls[0]["workspace"]
    assert not workspace.exists()


def test_probe_phase_cancel_uses_download_cancel_message(client_and_services) -> None:
    client, services, _ = client_and_services
    services.videos.analyzer = FakeMediaAnalyzer(probe_cancel=True)
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test").status_code == 201
    cancelled_job = _claim_and_run(services)
    assert cancelled_job["kind"] == "video_download"
    assert cancelled_job["state"] == "cancelled"
    assert cancelled_job["message"] == "链接下载已取消"
    assert services.repository.list_sources() == []


def test_download_job_blocked_when_tools_missing(client_and_services) -> None:
    client, services, downloader = client_and_services
    downloader.enabled = False
    response = _submit_link(client, "https://www.bilibili.com/video/BV1test")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "downloader_unavailable"
    capabilities = client.get("/api/v1/capabilities").json()
    assert capabilities["downloader"]["enabled"] is False
    job = services.repository.create_job(
        "video_download", None, None, None, None,
        {"url": "https://www.bilibili.com/video/BV1test", "platform": "bilibili", "rights": "owned", "use_cookie": False},
        priority=100,
    )
    completed = _claim_and_run(services)
    assert completed["id"] == job["id"] and completed["state"] == "blocked"


def test_download_job_blocked_when_ffmpeg_missing(client_and_services) -> None:
    # REQ-047.8：下载执行期 FFmpeg 不可解析 → blocked，绝不静默产出纯视频产物。
    client, services, downloader = client_and_services
    downloader.outcome = "ffmpeg_missing"
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test").status_code == 201
    blocked = _claim_and_run(services)
    assert blocked["kind"] == "video_download"
    assert blocked["state"] == "blocked"
    assert blocked["message"] == "未找到本地 FFmpeg 或 ffprobe"
    assert services.repository.list_sources() == []


# --- 用例 5：产物校验回滚（含分辨率档位，决策 12） ---

def test_product_validation_rejects_invalid_probe_without_artifact(client_and_services) -> None:
    client, services, _ = client_and_services
    services.videos.analyzer = FakeMediaAnalyzer(probe_error="invalid_metadata")
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test").status_code == 201
    failed = _claim_and_run(services)
    assert failed["state"] == "failed"
    assert failed["message"] == GENERIC_FAILURE_MESSAGE
    assert services.repository.list_sources() == []
    artifact_root = services.paths.artifacts
    assert not [path for path in artifact_root.rglob("*") if path.is_file()] if artifact_root.exists() else True


@pytest.mark.parametrize(
    ("width", "height"),
    [(2560, 1440), (2160, 3840), (1080, 1921), (1280, 1081)],
    ids=["2k", "4k", "portrait_over_long_edge", "landscape_over_short_edge"],
)
def test_resolution_tier_oversize_is_failed_without_artifact(
    client_and_services, width: int, height: int,
) -> None:
    # 决策 12：分辨率档位 ≤1080p＝短边 ≤1080 且长边 ≤1920；2K/4K 与任一边越界
    # → failed、通用脱敏消息、无 source 残留、不写 artifact。
    client, services, _ = client_and_services
    services.videos.analyzer = FakeMediaAnalyzer(width=width, height=height)
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test").status_code == 201
    failed = _claim_and_run(services)
    assert failed["state"] == "failed"
    assert failed["message"] == GENERIC_FAILURE_MESSAGE
    assert services.repository.list_sources() == []
    artifact_root = services.paths.artifacts
    assert not [path for path in artifact_root.rglob("*") if path.is_file()] if artifact_root.exists() else True


@pytest.mark.parametrize(
    ("width", "height"),
    [(1920, 1080), (1080, 1920), (720, 1280)],
    ids=["landscape_1080p", "portrait_1080p", "portrait_720p"],
)
def test_resolution_tier_1080p_accepted(client_and_services, width: int, height: int) -> None:
    # 决策 12：横屏 1920×1080 与竖屏 1080×1920 均属 1080p 档位，合法放行。
    client, services, _ = client_and_services
    services.videos.analyzer = FakeMediaAnalyzer(width=width, height=height)
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test").status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == "succeeded"


# --- 用例 6：成功链路与出处记录/脱敏双重断言 ---

def test_download_success_creates_source_provenance_and_analyze_job(client_and_services) -> None:
    client, services, downloader = client_and_services
    raw_url = "https://www.bilibili.com/video/BV1test?p=2&from=share#t=30"
    submitted = _submit_link(client, raw_url, title="链接视频", author="作者", tags=["下载"])
    assert submitted.status_code == 201, submitted.text
    job_id = submitted.json()["id"]

    job_row = services.repository.get_job(job_id)
    payload = json.loads(job_row["payload_json"])
    # payload 只存脱敏链接（无 query/fragment/userinfo），无原文 URL 参数
    assert payload["url"] == "https://www.bilibili.com/video/BV1test"
    assert "?p=2" not in job_row["payload_json"] and "from=share" not in job_row["payload_json"]

    completed = _claim_and_run(services)
    assert completed["id"] == job_id and completed["state"] == "succeeded"

    sources = services.repository.list_sources()
    assert len(sources) == 1
    source = sources[0]
    assert source["source_type"] == "video_link"
    assert source["rights"] == "owned"
    assert source["title"] == "链接视频"
    assert source["processing_state"] == "queued"

    versions = services.repository.versions_for_source(source["id"])
    assert len(versions) == 1 and versions[0]["media_type"] == "video/mp4"
    assert services.artifacts.verify(versions[0]["artifact_sha256"])

    # video_download_provenance 行齐全且脱敏
    with services.repository.connection() as connection:
        row = connection.execute(
            "SELECT * FROM video_download_provenance WHERE source_id=?", (source["id"],)
        ).fetchone()
    assert row is not None
    assert row["platform"] == "bilibili"
    assert row["url_sanitized"] == "https://www.bilibili.com/video/BV1test"
    assert "?" not in row["url_sanitized"] and "#" not in row["url_sanitized"]
    assert row["yt_dlp_version"] == "unit-1.0"
    assert row["format_profile"] == FakeDownloader.format_profile
    assert row["cookie_used"] == 0
    assert row["config_hash"] == downloader.config_hash("bilibili", FakeDownloader.format_profile)

    # 自动入队 video_analyze（与本地导入同路径）
    queued = [job for job in services.repository.list_jobs() if job["kind"] == "video_analyze"]
    assert len(queued) == 1 and queued[0]["source_id"] == source["id"] and queued[0]["state"] == "queued"

    # 审计事件仅 event_type/entity_id/result 承载
    with services.repository.connection() as connection:
        columns = {item[1] for item in connection.execute("PRAGMA table_info(audit_events)")}
        audit_rows = connection.execute(
            "SELECT * FROM audit_events WHERE event_type='video_download'"
        ).fetchall()
    assert columns == {"id", "event_type", "entity_id", "result", "created_at"}
    assert any(row["entity_id"] == source["id"] and row["result"] == "succeeded" for row in audit_rows)


def test_download_title_backfills_from_downloader_when_not_submitted(client_and_services) -> None:
    client, services, downloader = client_and_services
    downloader.title = "平台标题"
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test", title="").status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == "succeeded"
    source = services.repository.list_sources()[0]
    assert source["title"] == "平台标题"


def test_download_title_degenerates_to_unnamed_when_capture_empty(client_and_services) -> None:
    client, services, _ = client_and_services
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test", title="").status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == "succeeded"
    assert services.repository.list_sources()[0]["title"] == "未命名视频"


def test_download_title_explicit_submission_wins_over_captured(client_and_services) -> None:
    client, services, downloader = client_and_services
    downloader.title = "平台标题"
    assert _submit_link(client, "https://www.bilibili.com/video/BV1test", title="用户标题").status_code == 201
    completed = _claim_and_run(services)
    assert completed["state"] == "succeeded"
    assert services.repository.list_sources()[0]["title"] == "用户标题"


def test_extract_title_cleans_and_truncates() -> None:
    extract = YtDlpDownloader._extract_title
    assert extract("合成平台标题\n".encode("utf-8")) == "合成平台标题"
    assert extract(b"line1\r\nline2\t\x00tail") == "line1line2tail"
    assert extract(b"   ") == ""
    assert extract(b"") == ""
    long_title = "长标题" * 300
    assert len(extract(long_title.encode("utf-8"))) == 500


def test_backup_snapshot_and_export_carry_sanitized_provenance(client_and_services) -> None:
    client, services, _ = client_and_services
    raw_url = "https://www.bilibili.com/video/BV1backup?p=9"
    _submit_link(client, raw_url)
    _claim_and_run(services)
    source_id = services.repository.list_sources()[0]["id"]

    backup = services.transfers.create_backup()
    assert backup["state"] == "succeeded"
    with ZipFile(backup["archive_path"]) as archive, tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / "knowledge.db"
        with archive.open("state/knowledge.db") as source_stream, snapshot.open("wb") as target:
            shutil.copyfileobj(source_stream, target)
        connection = sqlite3.connect(snapshot)
        try:
            payload_json = connection.execute(
                "SELECT payload_json FROM jobs WHERE kind='video_download'"
            ).fetchone()[0]
        finally:
            connection.close()
    assert "?p=9" not in payload_json
    assert "https://www.bilibili.com/video/BV1backup" in payload_json

    exported = services.transfers.create_export(True)
    with ZipFile(exported["archive_path"]) as archive:
        records = json.loads(archive.read("records.json"))["records"]
    provenance_rows = records["video_download_provenance"]
    assert [row["source_id"] for row in provenance_rows] == [source_id]
    assert provenance_rows[0]["url_sanitized"] == "https://www.bilibili.com/video/BV1backup"


def test_provenance_failure_rolls_back_and_retry_creates_single_source(client_and_services) -> None:
    client, services, _ = client_and_services
    assert _submit_link(client, "https://www.bilibili.com/video/BV1retry").status_code == 201
    with services.repository.connection() as connection:
        connection.execute("DROP TABLE video_download_provenance")
    first = _claim_and_run(services)
    # 事务失败 → 整体回滚 + 补偿删除 artifact；第一次尝试进入有限重试
    assert first["state"] in {"retry_wait", "failed"}
    assert services.repository.list_sources() == []
    artifact_root = services.paths.artifacts
    assert not [path for path in artifact_root.rglob("*") if path.is_file()] if artifact_root.exists() else True
    with services.repository.connection() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS video_download_provenance ("
            "id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) UNIQUE, "
            "platform TEXT NOT NULL, url_sanitized TEXT NOT NULL, yt_dlp_version TEXT NOT NULL, "
            "format_profile TEXT NOT NULL, cookie_used INTEGER NOT NULL, config_hash TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
    if first["state"] == "retry_wait":
        retried = _claim_and_run(services)
    else:
        services.repository.retry_job(first["id"])
        retried = _claim_and_run(services)
    assert retried["id"] == first["id"] and retried["state"] == "succeeded"
    # 重试从头执行，绝不重复创建第二个 source/version/provenance
    sources = services.repository.list_sources()
    assert len(sources) == 1
    assert len(services.repository.versions_for_source(sources[0]["id"])) == 1
    with services.repository.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM video_download_provenance").fetchone()[0]
    assert count == 1


# --- 用例 9：多P/合集/直播/需登录/会员/DRM → failed 通用脱敏 ---

def test_platform_rejections_are_failed_with_generic_message(client_and_services) -> None:
    client, services, downloader = client_and_services
    downloader.outcome = "input_invalid"
    url = "https://www.douyin.com/video/123"
    assert _submit_link(client, url, platform="douyin").status_code == 201
    failed = _claim_and_run(services)
    assert failed["state"] == "failed"
    assert failed["message"] == GENERIC_FAILURE_MESSAGE
    assert "DRM" not in failed["message"] and "会员" not in failed["message"]
    assert url not in failed["message"]


# --- 用例 10：外联控制（决策 7） ---

def test_registered_domain_matching_by_label_boundary() -> None:
    domains = DOWNLOAD_REGISTRY["bilibili"]
    assert host_matches_registered_domain("bilibili.com", domains)
    assert host_matches_registered_domain("api.bilibili.com", domains)
    assert host_matches_registered_domain("b23.tv", domains)
    assert not host_matches_registered_domain("bilibili.com.evil.com", domains)
    assert not host_matches_registered_domain("evil-bilibili.com", domains)
    assert not host_matches_registered_domain("127.0.0.1", domains)


def test_douyin_registry_includes_365yg_media_cdn() -> None:
    # 决策 11：2026-08-14 真实链接实测登记字节系媒体 CDN（v95-aw-default.365yg.com）。
    domains = registered_domains("douyin")
    assert "365yg.com" in domains
    assert host_matches_registered_domain("v95-aw-default.365yg.com", domains)
    assert host_matches_registered_domain("a1.365yg.com", domains)
    # 标签边界：非子域冒充不得匹配
    assert not host_matches_registered_domain("evil365yg.com", domains)
    assert not host_matches_registered_domain("365yg.com.evil.com", domains)


def test_proxy_rejects_unregistered_connect_without_outbound_bytes() -> None:
    proxy = LoopbackFilterProxy(("bilibili.com",))
    port = proxy.start()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(b"CONNECT evil.example:443 HTTP/1.1\r\nHost: evil.example:443\r\n\r\n")
        try:
            response = client.recv(4096)
        except ConnectionResetError:
            response = b""  # 立即断开：Windows 上表现为 RST
        client.close()
    finally:
        proxy.close()
    # 未登记域：立即断开，无 200 隧道应答、无任何字节出站
    assert not response.startswith(b"HTTP/1.1 200")
    assert proxy.denied_hosts() == {"evil.example": 1}
    assert proxy.connected_hosts() == {}


def test_proxy_rejects_reserved_resolutions_in_production_mode() -> None:
    proxy = LoopbackFilterProxy(("bilibili.com",))
    for reserved in (
        "127.0.0.1", "10.1.2.3", "172.16.5.5", "192.168.1.9", "169.254.1.1",
        "100.64.0.1", "100.127.255.255", "192.0.2.1", "169.254.169.254",
        "::1", "fe80::1",
    ):
        assert proxy._reject_resolved_ip(reserved) is True
    for public in ("1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"):
        assert proxy._reject_resolved_ip(public) is False


def test_proxy_tunnel_range_exemption_requires_registered_hostname() -> None:
    # T-VID-003 用例 10 修订（决策 10）：隧道段例外仅在注册域主机名校验通过后生效。
    proxy = LoopbackFilterProxy(("bilibili.com",))
    # (a) 注册域主机名解析到隧道段 → 放行
    assert proxy._reject_resolved_ip("198.18.0.55") is False
    assert proxy._reject_resolved_ip("198.18.255.254") is False
    assert proxy._reject_resolved_ip("28.0.0.1") is False
    assert proxy._reject_resolved_ip("28.255.255.255") is False
    # (c) 其余保留段仍无条件拒绝
    for reserved in (
        "100.64.0.1", "100.127.255.255", "169.254.169.254", "169.254.10.10",
        "192.0.2.1", "198.51.100.7", "203.0.113.9", "127.0.0.1", "::1",
        "10.0.0.1", "172.16.0.1", "192.168.1.1", "224.0.0.1", "255.255.255.255",
    ):
        assert proxy._reject_resolved_ip(reserved) is True
    # (b) 隧道段例外不会绕过主机名校验：未登记主机名与 IP 字面量在代理层直接拒绝
    assert proxy._validate_host("evil.com") is False
    assert proxy._validate_host("198.18.0.55") is False
    assert proxy.denied_hosts() == {"evil.com": 1, "198.18.0.55": 1}
    # (d) 测试注入豁免（决策 9）行为不受影响：生产代理仍拒回环，测试子类放行
    exempt = _LoopbackExemptProxy(("localhost",))
    assert proxy._reject_resolved_ip("127.0.0.1") is True
    assert exempt._reject_resolved_ip("127.0.0.1") is False


class _LoopbackExemptProxy(LoopbackFilterProxy):
    """测试注入模式（决策 9）：仅豁免回环/保留段解析拒绝，不影响注册域校验。"""

    def _reject_resolved_ip(self, ip: str) -> bool:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return True
        return False


# 自签 localhost 证书/私钥（测试专用，CN/SAN=localhost，有效期至 2126 年）。
# 标准库无法在运行时生成证书、cryptography 未安装且禁止新增依赖，故以内嵌
# 常量随测试文件携带；仅写入 tests/runtime/<run-id> 临时文件（load_cert_chain
# 需要路径），不入数据库、不入备份/导出。
_TLS_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIDITCCAgmgAwIBAgIUbqBXFksghvvRfD7/OTdNbcG2I3swDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MCAXDTI2MDgxMzA1NTIwMloYDzIxMjYw
NzIwMDU1MjAyWjAUMRIwEAYDVQQDDAlsb2NhbGhvc3QwggEiMA0GCSqGSIb3DQEB
AQUAA4IBDwAwggEKAoIBAQDqoSiCsZAP/md+CgO+vmPTYWQiRqD9SRfgMUfrrrkq
MveK8lZ8XfQxkx1JrwgDEgK3ng0rTtyZ5YAwe/mpPF7IDF1Y5SQOSCk2KRaDIsUK
T8dWA4mamrIdq7d5nPr+esoEDehIJWh1DYSxjqCYwCycW4QlgiTC9fILgi8gGR+7
WFFXp/g/gnGxVjitz+JYpCYfd2Clx1+r5t31PXeNYsiJctOknRRc0BXu8USzGC9y
rqavGR72o5nXr3sgIYW8tw1Hlg44vJWVoujaBZe++9r2vWCOIB6x9gjLba7XDB73
v/9Xnf7Z3/0qMBu+wxv2uu5L74tyDxI+PMqn25S5cDGrAgMBAAGjaTBnMB0GA1Ud
DgQWBBTVlr+s4ySywm/AxT3ky9/hWzqFYjAfBgNVHSMEGDAWgBTVlr+s4ySywm/A
xT3ky9/hWzqFYjAPBgNVHRMBAf8EBTADAQH/MBQGA1UdEQQNMAuCCWxvY2FsaG9z
dDANBgkqhkiG9w0BAQsFAAOCAQEAneTdHKgATZtSuqQGUXzzCILKXEWn6PyoNxKQ
JpbRCnrEG15sWFMk6MIl+FX2hs2pP4PnzbqCIo/FjnFx2xBX/PZ4zsmMcQx1PBMl
1QX2ORhe7iCm8xdzvMJhF1g+VGUZJh/Ta/zc5wx8i+MbQKgcAoXcYP1Hq9JP0Ax5
DsFdrNCY95ax4JdXeRsQXHl6EB3kp1hqlaWtomipdB0d3gJtWLQSymVZlPUOSNzM
jLL/bdEVztx2KUeQVczvIxn0YAf31h7lWZicwk8gkwPd6oVqbrUZqTxOv8tEARwv
hAmI4NQdH6rf7ocBNo4Cp2/Ywjn7BTF9hHiJVu1HJPgdkTzpxQ==
-----END CERTIFICATE-----
"""

_TLS_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDqoSiCsZAP/md+
CgO+vmPTYWQiRqD9SRfgMUfrrrkqMveK8lZ8XfQxkx1JrwgDEgK3ng0rTtyZ5YAw
e/mpPF7IDF1Y5SQOSCk2KRaDIsUKT8dWA4mamrIdq7d5nPr+esoEDehIJWh1DYSx
jqCYwCycW4QlgiTC9fILgi8gGR+7WFFXp/g/gnGxVjitz+JYpCYfd2Clx1+r5t31
PXeNYsiJctOknRRc0BXu8USzGC9yrqavGR72o5nXr3sgIYW8tw1Hlg44vJWVouja
BZe++9r2vWCOIB6x9gjLba7XDB73v/9Xnf7Z3/0qMBu+wxv2uu5L74tyDxI+PMqn
25S5cDGrAgMBAAECggEAZJYyh9UXrcOjGqWwdVWp9jUKeKdO3Uc4tSRrcN63AyBW
f3rlGOwuhBJNvAkNpkNSZuWbP7XPXSrGigKcRbFb8OdcHYAetQC6qj1zKUT+tCz/
iCB8HYu0UIQNZFWoRPDfKl3L9yISZhwlhvleYB4DAgU54dqpZ+uImOZ2zYv3zph8
CWNaZ+qhmNdw5kOan8lE9sxlavoloqLd9VYNQSjqZduPyUehvzKx2M+AXL9Qk8pi
KPSx5M0YSFjo5QGTo58GMZJgKcLgMc6vaPMXiVL2DQ3DKHUeO6MOi6R2+71mNiFt
yw3ggxTnsJuz1wnwmRYBY5cAb8VAmLS85pNUaq1iAQKBgQD80Qn+YJKyY+/XqSUu
7XAO8G7pvIKdMLaM5opAowSwiTXwwzzliaFQnEYFx+n6FXFMgf6ApGhqPb3DtTeL
CGCvkoibivdXZioMR3UE6n0GyrAnIoKLH5DmyGtFzSHkVek42j2Qwv27g0LcRRBi
H06AdtpecchSU9oMxsVhf0l9hQKBgQDtlX4pALaDNPioxArpVCJC6zrw3ea4435z
01QjddQTM4AKyBZQlcQxGYIyRPzPuSFz8yoeYWbywlDnQR+yDkNTDzdFz9tPyGll
r/2d8UPv3wq+d1lJpssXFvRkFIRz5NSy1zhhGw/vYOsqml/ZmGJGPKj3SkikPB97
vDNnJSRBbwKBgF9dNsjWgt95pRITgqwl8lwgQ6Y1bot+wY16tPHWzEEPMOKlssXe
2ZO/rwYlN9QW3IsAihDac2yH55n4NIBkY5w2yQLrM4urRPcmyTRWg1zZfgL1GIsE
GDOFrDlDPKKV6YiBgjGl6/IcfE78Wka5CnKY4pw3jVnIuXqSTAgP7JfFAoGBANts
5kgYUHh9w+qapTk6aypC9vze9Ohts6xl0Z+ug1/4gJl0kqd6quhuFsE21gdDhJIC
UzQb4Wjz7qSmkQ9x/NwJgZMIlhTpk+5GzIXC/mvcI6AlumE7mvaITM7h5DLldUx3
WarVw7HiYU/HpB7jjmAwRh2ejdihbrJo71CkDQghAoGAdIF91SiMT4ogAdvEPcjO
6GVjZjnIPSgw8qGSDEdIoEa8hpD9Wbcy5ErJNf/o7cNcNBS+DCu0e/Q0UCNFApaY
NJI/yNDxINkXe6a77EAE8NyGby9O+3pO6rx3AWmER4dsrHCVZ34iQUb+03qs2ejk
S598pnN9M4y9C9d/+gBjguQ=
-----END PRIVATE KEY-----
"""


def test_proxy_connect_drains_headers_before_tls_tunnel(runtime_root: Path) -> None:
    """回归：CONNECT 剩余请求头必须排空，否则残留字节污染 TLS 隧道。

    旧行为：``CONNECT host:443 HTTP/1.1`` 之后的 ``Host:`` 等请求头与空行
    被 ``_bidirectional_relay`` 当作 TLS 数据转发给服务器——真实 TLS 服务器
    回明文 ``400 Bad Request``，客户端握手报 ``WRONG_VERSION_NUMBER``。
    本用例以真实 CONNECT + TLS 握手 + HTTP GET 覆盖该路径（决策 9 注入）。
    """
    tls_dir = runtime_root / "tls"
    tls_dir.mkdir()
    cert_path = tls_dir / "cert.pem"
    key_path = tls_dir / "key.pem"
    cert_path.write_text(_TLS_CERT_PEM, encoding="ascii")
    key_path.write_text(_TLS_KEY_PEM, encoding="ascii")

    results: dict[str, object] = {}
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert_path), str(key_path))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    server_port = listener.getsockname()[1]

    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        try:
            with server_context.wrap_socket(connection, server_side=True) as tls:
                first = tls.recv(16)
                results["first_bytes"] = first
                request = first
                while b"\r\n\r\n" not in request and len(request) < 64 * 1024:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                results["request"] = request
                tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\npong")
        except Exception as exc:  # noqa: BLE001
            results["error"] = repr(exc)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    proxy = _LoopbackExemptProxy(("localhost",))  # 测试注入注册域 + 保留段豁免（决策 9）
    proxy_port = proxy.start()
    try:
        client = socket.create_connection(("127.0.0.1", proxy_port), timeout=10)
        # 真实 CONNECT：请求行 + Host 头 + User-Agent + 空行（旧行为会把头转发进隧道）
        client.sendall(
            f"CONNECT localhost:{server_port} HTTP/1.1\r\n"
            f"Host: localhost:{server_port}\r\n"
            "User-Agent: yuanzhiku-test\r\n\r\n".encode("latin-1")
        )
        established = b""
        while b"\r\n\r\n" not in established:
            chunk = client.recv(4096)
            if not chunk:
                break
            established += chunk
        assert established.startswith(b"HTTP/1.1 200")
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
        with client_context.wrap_socket(client, server_hostname="localhost") as tls:
            tls.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            body = b""
            while True:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                body += chunk
        assert b"200 OK" in body and b"pong" in body
    finally:
        proxy.close()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "error" not in results, results.get("error")
    # 旧行为不复现：TLS 解密后服务器收到的首字节是 HTTP 请求（"GET "），
    # 而不是被污染的请求头明文（"Host:"/"User-Agent:"）
    assert results.get("first_bytes", b"").startswith(b"GET ")
    assert b"GET / HTTP/1.1" in results["request"]
    assert proxy.connected_hosts() == {"localhost": 1}
    assert proxy.denied_hosts() == {}


def test_proxy_connect_drain_headers_stops_at_blank_line() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(b"Host: b23.tv:443\r\nUser-Agent: x\r\n\r\nPAYLOAD")
        assert LoopbackFilterProxy._drain_headers(right) is True
        right.settimeout(5)
        assert right.recv(7) == b"PAYLOAD"  # 只排空到空行为止，其后字节（ClientHello）保留
    finally:
        left.close()
        right.close()


def test_proxy_connect_drain_headers_fails_on_truncation() -> None:
    left, right = socket.socketpair()
    try:
        left.sendall(b"Host: b23.tv:443\r\nUser-Agent: truncated")
        left.close()
        assert LoopbackFilterProxy._drain_headers(right) is False
    finally:
        right.close()


def test_proxy_relays_registered_connect_via_validated_peer() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    server_port = server.getsockname()[1]

    def serve() -> None:
        while True:
            try:
                connection, _ = server.accept()
            except OSError:
                break
            try:
                connection.recv(4096)
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\npong")
            except OSError:
                pass
            finally:
                connection.close()

    threading.Thread(target=serve, daemon=True).start()
    proxy = _LoopbackExemptProxy(("localhost",))
    port = proxy.start()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.sendall(
            f"CONNECT localhost:{server_port} HTTP/1.1\r\nHost: localhost:{server_port}\r\n\r\n".encode("latin-1")
        )
        tunnel_ok = client.recv(4096)
        assert tunnel_ok.startswith(b"HTTP/1.1 200")
        client.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        body = b""
        while b"pong" not in body and len(body) < 4096:
            body += client.recv(4096)
        assert b"pong" in body
        client.close()
    finally:
        proxy.close()
        server.close()
    assert set(proxy.connected_hosts()) == {"localhost"}
    assert proxy.denied_hosts() == {}


# --- 用例 11：settings 边界 ---

def test_download_settings_bounds_and_defaults(client_and_services) -> None:
    client, services, _ = client_and_services
    defaults = client.get("/api/v1/settings").json()
    assert defaults["download_timeout_seconds"] == "3600"
    assert defaults["download_no_progress_seconds"] == "10"
    assert defaults["download_disk_limit_mb"] == "2048"
    for payload in (
        {"download_timeout_seconds": 59}, {"download_timeout_seconds": 86401},
        {"download_no_progress_seconds": 9}, {"download_no_progress_seconds": 86401},
        {"download_disk_limit_mb": 63}, {"download_disk_limit_mb": 32769},
    ):
        assert client.put("/api/v1/settings", json=payload).status_code == 422
    accepted = client.put("/api/v1/settings", json={
        "download_timeout_seconds": 86400,
        "download_no_progress_seconds": 10,
        "download_disk_limit_mb": 32768,
    })
    assert accepted.status_code == 200, accepted.text
    saved = client.get("/api/v1/settings").json()
    assert saved["download_timeout_seconds"] == "86400"
    assert saved["download_no_progress_seconds"] == "10"
    assert saved["download_disk_limit_mb"] == "32768"
    assert services.paths.download.is_dir()


# --- T-VID-004：合成集成（真实 yt-dlp 指向 localhost，全程不触网真实平台） ---

def _synthetic_mp4() -> bytes:
    # 合成无版权 MP4：ftyp + free + mdat 载荷。分析由 unit analyzer 假件完成，
    # 内容不依赖真实编解码器。
    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    free = b"\x00\x00\x00\x08free"
    mdat = b"\x00\x00\x00\x14mdat" + b"\xab" * 2048
    return ftyp + free + mdat


class _FixtureHandler(BaseHTTPRequestHandler):
    fixture = b""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/fixture.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(self.fixture)))
            self.end_headers()
            self.wfile.write(self.fixture)
        elif self.path == "/page.html":
            # 合成页面：og:title 供平台标题回填，og:video 指向同源 fixture
            host = self.headers.get("Host", f"127.0.0.1:{self.server.server_address[1]}")
            page = (
                "<!doctype html><html><head>"
                '<meta property="og:title" content="合成平台标题">'
                f'<meta property="og:video" content="http://{host}/fixture.mp4">'
                "</head><body></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "http://evil.example/video.mp4")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.fixture)))
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture()
def fixture_server(runtime_root: Path):
    _FixtureHandler.fixture = _synthetic_mp4()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, _FixtureHandler.fixture
    server.shutdown()
    server.server_close()


class _ChainDownloader(YtDlpDownloader):
    """真实 yt-dlp 子进程 + 测试注入代理；capability 在测试内恒 true。

    本机未安装 ffmpeg：合成服务器只提供单文件 MP4，无需合并/remux，
    故下载本身不需要 ffmpeg；probe/抽帧由 unit analyzer 假件完成。
    """

    def capability(self) -> dict[str, object]:
        capability = super().capability()
        capability["enabled"] = True
        return capability

    def _ffmpeg_available(self) -> bool:
        # 测试注入（决策 9 同源）：仅测试子类放行 ffmpeg 预检（合成单文件
        # MP4 无需合并），生产代码无该分支，fail-closed 语义不变。
        return True


def _chain_app(runtime_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YUANZHIKU_EMBEDDED_WORKER", "false")
    # 环境代理指向死端口：成功下载即证明流量只经回环代理（显式 --proxy + 清空环境变量）
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    proxies: list[LoopbackFilterProxy] = []

    def factory(platform: str) -> LoopbackFilterProxy:
        proxy = _LoopbackExemptProxy(("localhost",))  # 测试注入注册域清单（仅 fixture 域）
        proxies.append(proxy)
        return proxy

    downloader = _ChainDownloader(
        proxy_factory=factory,
        cookie_file_path=services.paths.download / "cookies.txt",
    )
    services.downloader = downloader
    services.jobs.downloader = downloader
    services.videos.analyzer = FakeMediaAnalyzer()
    return app, services, proxies


def test_synthetic_download_full_chain(
    runtime_root: Path, fixture_server, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, fixture = fixture_server
    app, services, proxies = _chain_app(runtime_root / "origin", monkeypatch)
    with TestClient(app) as client:
        url = f"http://localhost:{server.server_address[1]}/fixture.mp4"
        job = services.repository.create_job(
            "video_download", None, None, None, None,
            {
                "url": url, "platform": "bilibili", "use_cookie": False, "rights": "owned",
                "title": "合成下载视频", "author": None, "language": "zh", "notes": None,
                "source_date": None, "categories": [], "tags": [],
            },
            priority=100,
        )
        completed = _claim_and_run(services)
        assert completed["id"] == job["id"] and completed["state"] == "succeeded", completed
        assert completed["message"] == "链接下载完成，已排入本地视频分析"

        # 回环代理记录断言：全部出站 ⊆ 测试注册表，且无任何外联
        assert len(proxies) == 1
        assert set(proxies[0].connected_hosts()) <= {"localhost"}
        assert proxies[0].connected_hosts() != {}
        assert proxies[0].denied_hosts() == {}

        sources = services.repository.list_sources()
        assert len(sources) == 1
        source = sources[0]
        assert source["source_type"] == "video_link"
        version = services.repository.versions_for_source(source["id"])[0]
        assert services.artifacts.verify(version["artifact_sha256"])
        assert services.artifacts.artifact_path(version["artifact_sha256"]).read_bytes() == fixture

        with services.repository.connection() as connection:
            provenance = connection.execute(
                "SELECT * FROM video_download_provenance WHERE source_id=?", (source["id"],)
            ).fetchone()
        assert provenance is not None and provenance["platform"] == "bilibili"
        assert provenance["yt_dlp_version"] == YtDlpDownloader._yt_dlp_version()
        assert provenance["format_profile"] == YtDlpDownloader.format_profile

        # video_analyze 自动入队并成功（unit analyzer 假件）
        analyzed = _claim_and_run(services)
        assert analyzed["kind"] == "video_analyze" and analyzed["state"] == "succeeded"

        # 播放：Range 206
        stream = client.get(
            f"/api/v1/videos/{source['id']}/stream", headers={"Range": "bytes=0-7"},
        )
        assert stream.status_code == 206, stream.text
        assert stream.content == fixture[:8]
        detail = client.get(f"/api/v1/videos/{source['id']}")
        assert detail.status_code == 200 and detail.json()["analysis"] is not None

        # 备份与导出：provenance 随归档携带
        backup = services.transfers.create_backup()
        assert backup["state"] == "succeeded"
        exported = services.transfers.create_export(True)
        with ZipFile(exported["archive_path"]) as archive:
            records = json.loads(archive.read("records.json"))["records"]
        assert [row["source_id"] for row in records["video_download_provenance"]] == [source["id"]]

        # 再导入到独立接收方
        recipient = create_app(runtime_root / "recipient", acquire_lock=False).state.services
        reimported = recipient.transfers.reimport(exported["archive_path"])
        assert reimported["imported"] is True
        assert recipient.videos.detail(source["id"]) is not None

        # 清理：软删 + purge 移除无引用 artifact
        assert client.post(f"/api/v1/sources/{source['id']}/delete").status_code == 200
        purged = client.post(f"/api/v1/sources/{source['id']}/purge")
        assert purged.status_code == 200, purged.text
        assert not services.artifacts.artifact_path(version["artifact_sha256"]).exists()


def test_synthetic_redirect_to_unregistered_domain_fails_closed(
    runtime_root: Path, fixture_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    server, _ = fixture_server
    _, services, proxies = _chain_app(runtime_root / "redirect", monkeypatch)
    # 合成服务器返回 302 → 非白名单域：下载必须拒绝且无该域出站请求
    url = f"http://localhost:{server.server_address[1]}/redirect"
    downloader = services.jobs.downloader
    workspace = tmp_path / "staging"
    workspace.mkdir()
    with pytest.raises(DownloadInputInvalid):
        downloader.download(
            url=url, platform="bilibili", workspace=workspace,
            limits=MediaProcessingLimits(60.0, 1024 ** 3, 1024 ** 3),
            use_cookie=False, cookie_path=None,
            cancelled=lambda: False, heartbeat=lambda: None,
            progress=lambda value, message: None,
        )
    proxy = proxies[0]
    assert "evil.example" in proxy.denied_hosts()
    assert set(proxy.connected_hosts()) <= {"localhost"}


def test_synthetic_download_captures_platform_title(
    runtime_root: Path, fixture_server, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    server, _ = fixture_server
    _, services, _ = _chain_app(runtime_root / "title", monkeypatch)
    downloader = services.jobs.downloader
    workspace = tmp_path / "staging"
    workspace.mkdir()
    # 合成页面 og:title → 真实 yt-dlp --print 捕获并清洗回填
    page_result = downloader.download(
        url=f"http://localhost:{server.server_address[1]}/page.html",
        platform="bilibili", workspace=workspace,
        limits=MediaProcessingLimits(60.0, 1024 ** 3, 1024 ** 3),
        use_cookie=False, cookie_path=None,
        cancelled=lambda: False, heartbeat=lambda: None,
        progress=lambda value, message: None,
    )
    assert page_result.title == "合成平台标题"
    # 无页面元数据的直链：标题退化为文件名，捕获不失败
    shutil.rmtree(workspace)
    workspace.mkdir()
    direct_result = downloader.download(
        url=f"http://localhost:{server.server_address[1]}/fixture.mp4",
        platform="bilibili", workspace=workspace,
        limits=MediaProcessingLimits(60.0, 1024 ** 3, 1024 ** 3),
        use_cookie=False, cookie_path=None,
        cancelled=lambda: False, heartbeat=lambda: None,
        progress=lambda value, message: None,
    )
    assert direct_result.title in {"fixture", "fixture.mp4"}


def test_sanitize_download_url_strips_userinfo_query_and_fragment() -> None:
    assert sanitize_download_url("https://u:p@www.bilibili.com/video/BV1?p=2#t=1") == "https://www.bilibili.com/video/BV1"
    assert sanitize_download_url("https://www.douyin.com/video/123") == "https://www.douyin.com/video/123"
    long_path = "https://www.bilibili.com/" + "x" * 5000
    assert len(sanitize_download_url(long_path)) == 4096
