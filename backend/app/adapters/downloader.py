"""Restricted whitelisted-platform link download adapter (REQ-047 / REQ-047a).

``YtDlpDownloader`` runs the locked yt-dlp as a shell-less subprocess
(``sys.executable -m yt_dlp``) with a minimal fixed argument skeleton. Every
outbound connection travels through a job-scoped loopback filtering proxy that
validates each new target host against the registered domain list before a
single outbound byte is sent (decision 7). Cookies are accepted only through an
explicit staging copy of the imported cookies.txt file (decision 8).

The proxy and the registry live in this module with no dependencies beyond the
standard library and the ports/domain contracts.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.domain.media import MediaProcessingLimits
from app.ports.media import (
    DownloadedVideo,
    DownloadInputInvalid,
    DownloadProcessingCancelled,
    DownloadUnavailable,
    MediaDownloaderPort,
)

MAX_COOKIE_BYTES = 1024 * 1024

# 出站注册域清单（7.2.1，决策 7）：平台主域 + 显式登记的 CDN/API 域。
# 清单变更属于安全边界变更，必须附实测证据并经独立审核门禁；未实证域一律不登记。
DOWNLOAD_REGISTRY: dict[str, tuple[str, ...]] = {
    "bilibili": ("bilibili.com", "bilivideo.com", "hdslb.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com", "snssdk.com", "douyinvod.com"),
}

DOWNLOAD_PLATFORMS = tuple(DOWNLOAD_REGISTRY)

# 隧道段例外（决策 10，fake-IP 环境兼容）：代理工具 fake-IP/TUN 模式的常见
# 隧道地址，公网不可路由、由本地 TUN 设备独占路由并映射到工具自身配置的
# 真实目的地——攻击者无法借此把连接引向受害主机内部服务（经典 DNS 重绑定
# 目标）；主机名注册域白名单仍是第一道且唯一的域名控制。前提：主机名已通过
# _validate_host 注册域校验（本常量只在 _open_validated_connection 内参与
# 判定，而该函数仅在校验通过后执行，调用关系不变）。其余保留段仍无条件拒绝。
TUNNEL_RANGES = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("28.0.0.0/8"))


def registered_domains(platform: str) -> tuple[str, ...]:
    """Return the registered outbound domains for a whitelisted platform."""
    return DOWNLOAD_REGISTRY.get(platform, ())


def host_matches_registered_domain(host: str, domains: tuple[str, ...]) -> bool:
    """Match by label boundary: equal to or a subdomain of a registered domain.

    IP literals (IPv4 or bracketed IPv6 forms) never match a registered domain
    and are rejected by the caller.
    """
    lowered = host.rstrip(".").lower()
    if not lowered or ":" in lowered:
        return False
    return any(lowered == domain or lowered.endswith("." + domain) for domain in domains)


class LoopbackFilterProxy:
    """Job-scoped loopback HTTP CONNECT filtering proxy (standard library only).

    Listens exclusively on ``127.0.0.1:<random port>`` for the lifetime of one
    download job. Every new connection's target host must hit the registered
    domain list before resolution; resolved addresses must not be loopback,
    private, link-local or reserved. Resolution happens exactly once per
    connection (resolve-then-connect) and the connected peer address is
    re-checked against the validated set. Startup failure is fail-closed: the
    caller must never fall back to a direct connection.
    """

    MAX_HEADER_BYTES = 64 * 1024
    IO_TIMEOUT_SECONDS = 30.0

    def __init__(self, allowed_domains: tuple[str, ...]) -> None:
        self._allowed_domains = tuple(domain.rstrip(".").lower() for domain in allowed_domains)
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._client_sockets: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._closing = False
        # 内存计数表（作业结束即丢弃）：全部 CONNECT 目标主机供测试断言"出站 ⊆ 注册表"。
        self._connected_hosts: dict[str, int] = {}
        self._denied_hosts: dict[str, int] = {}

    def start(self) -> int:
        """Bind the loopback listener and start accepting; returns the port."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(16)
        listener.settimeout(0.5)
        self._listener = listener
        thread = threading.Thread(target=self._accept_loop, name="yuanzhiku-download-proxy", daemon=True)
        thread.start()
        self._thread = thread
        return listener.getsockname()[1]

    def close(self) -> None:
        """Close the listener and every live relay; safe to call repeatedly."""
        self._closing = True
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._lock:
            sockets = list(self._client_sockets)
        for client in sockets:
            try:
                client.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def connected_hosts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._connected_hosts)

    def denied_hosts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._denied_hosts)

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._closing:
            try:
                client, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            client.settimeout(self.IO_TIMEOUT_SECONDS)
            with self._lock:
                self._client_sockets.add(client)
            threading.Thread(target=self._handle_connection, args=(client,), daemon=True).start()

    def _untrack(self, client: socket.socket) -> None:
        with self._lock:
            self._client_sockets.discard(client)

    @staticmethod
    def _read_line(client: socket.socket) -> str | None:
        data = bytearray()
        while len(data) <= LoopbackFilterProxy.MAX_HEADER_BYTES:
            try:
                chunk = client.recv(1)
            except OSError:
                return None
            if not chunk:
                return None
            if chunk == b"\n":
                line = bytes(data).decode("latin-1")
                return line[:-1] if line.endswith("\r") else line
            data += chunk
        return None

    @staticmethod
    def _split_authority(authority: str) -> tuple[str | None, int]:
        """Split ``host:port`` including the bracketed IPv6 literal form."""
        if authority.startswith("["):
            end = authority.find("]")
            if end < 0:
                return None, 0
            host = authority[1:end]
            rest = authority[end + 1:]
            port = 443
            if rest.startswith(":"):
                try:
                    port = int(rest[1:])
                except ValueError:
                    return None, 0
            return host, port
        if ":" in authority:
            host, _, port_raw = authority.rpartition(":")
            try:
                port = int(port_raw)
            except ValueError:
                return None, 0
            return host, port
        return authority, 443

    def _record_connected(self, host: str) -> None:
        with self._lock:
            self._connected_hosts[host] = self._connected_hosts.get(host, 0) + 1

    def _record_denied(self, host: str) -> None:
        with self._lock:
            self._denied_hosts[host] = self._denied_hosts.get(host, 0) + 1

    def _validate_host(self, host: str) -> bool:
        lowered = host.rstrip(".").lower()
        try:
            ipaddress.ip_address(lowered)
            literal = True
        except ValueError:
            literal = False
        if literal or not host_matches_registered_domain(lowered, self._allowed_domains):
            self._record_denied(host)
            return False
        return True

    def _reject_resolved_ip(self, ip: str) -> bool:
        """Production fail-closed: refuse everything that is not global unicast.

        Covers 127.0.0.0/8、10.0.0.0/8、172.16.0.0/12、192.168.0.0/16、
        169.254.0.0/16、::1、fe80::/10、100.64.0.0/10（CGNAT 共享段——Python
        3.13 下 is_private/is_reserved 对该段全为 False）、文档段、广播段及
        其余保留/多播地址。唯一例外是隧道段 TUNNEL_RANGES（决策 10）：仅当
        主机名已通过 _validate_host 注册域校验后才可能到达本方法（本方法只被
        _open_validated_connection 调用，而该函数仅在校验通过后执行），隧道段
        放行不会构成主机名白名单绕过。Test code may subclass to exempt this
        check (decision 9); production code has no such branch.
        """
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return True
        if any(address in network for network in TUNNEL_RANGES):
            # 隧道段例外（决策 10）：注册域主机名解析落入 fake-IP 隧道段时放行。
            return False
        # 多播段须显式拒绝：Python 3.13 下多播地址（如 224.0.0.1）is_global
        # 为 True，仅凭 not is_global 会漏放行。
        return not address.is_global or address.is_multicast

    def _open_validated_connection(self, host: str, port: int) -> socket.socket | None:
        """Resolve once, connect with the validated IPs, then re-check the peer."""
        try:
            resolved = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            self._record_denied(host)
            return None
        candidates: list[tuple] = []
        for family, socktype, proto, _, sockaddr in resolved:
            if self._reject_resolved_ip(sockaddr[0]):
                continue
            candidates.append((family, socktype, proto, sockaddr))
        if not candidates:
            self._record_denied(host)
            return None
        validated_ips = {sockaddr[0] for _, _, _, sockaddr in candidates}
        remote: socket.socket | None = None
        for family, socktype, proto, sockaddr in candidates:
            try:
                candidate = socket.socket(family, socktype, proto)
                candidate.settimeout(self.IO_TIMEOUT_SECONDS)
                candidate.connect(sockaddr)
                remote = candidate
                break
            except OSError:
                if candidate is not None:
                    candidate.close()
        if remote is None:
            self._record_denied(host)
            return None
        peer = remote.getpeername()
        if not peer or peer[0] not in validated_ips:
            remote.close()
            self._record_denied(host)
            return None
        return remote

    def _handle_connection(self, client: socket.socket) -> None:
        try:
            request_line = self._read_line(client)
            if not request_line:
                return
            parts = request_line.split()
            if len(parts) < 3 or not parts[2].upper().startswith("HTTP/"):
                return
            method = parts[0].upper()
            if method == "CONNECT":
                host, port = self._split_authority(parts[1])
                if host is None or not self._validate_host(host):
                    return  # 未登记域：立即断开，无任何字节出站
                self._relay_connect(client, host, port)
                return
            self._forward_plain_request(client, method, parts[1])
        finally:
            try:
                client.close()
            except OSError:
                pass
            self._untrack(client)

    def _relay_connect(self, client: socket.socket, host: str, port: int) -> None:
        remote = self._open_validated_connection(host, port)
        if remote is None:
            return
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        except OSError:
            remote.close()
            return
        self._record_connected(host)
        self._bidirectional_relay(client, remote)

    def _forward_plain_request(self, client: socket.socket, method: str, target: str) -> None:
        headers: list[str] = []
        while True:
            line = self._read_line(client)
            if line is None:
                return
            if line == "":
                break
            headers.append(line)
        if target.startswith(("http://", "https://")):
            parsed = urlsplit(target)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            origin_form = parsed.path or "/"
            if parsed.query:
                origin_form = f"{origin_form}?{parsed.query}"
        else:
            host_line = next((line for line in headers if line.lower().startswith("host:")), None)
            if host_line is None:
                return
            authority = host_line.split(":", 1)[1].strip()
            host, port = self._split_authority(authority)
            if host is None:
                return
            origin_form = target
        if not self._validate_host(host):
            return
        remote = self._open_validated_connection(host, port)
        if remote is None:
            return
        try:
            remote.sendall(f"{method} {origin_form} HTTP/1.1\r\n".encode("latin-1", "replace"))
            for header in headers:
                remote.sendall(header.encode("latin-1", "replace") + b"\r\n")
            remote.sendall(b"\r\n")
        except OSError:
            remote.close()
            return
        self._record_connected(host)
        self._bidirectional_relay(client, remote)

    def _bidirectional_relay(self, client: socket.socket, remote: socket.socket) -> None:
        def pump(source: socket.socket, target: socket.socket) -> None:
            try:
                while True:
                    data = source.recv(64 * 1024)
                    if not data:
                        break
                    target.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    target.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        upstream = threading.Thread(target=pump, args=(client, remote), daemon=True)
        downstream = threading.Thread(target=pump, args=(remote, client), daemon=True)
        upstream.start()
        downstream.start()
        upstream.join(timeout=self.IO_TIMEOUT_SECONDS * 2)
        downstream.join(timeout=self.IO_TIMEOUT_SECONDS * 2)
        try:
            remote.close()
        except OSError:
            pass


class YtDlpDownloader(MediaDownloaderPort):
    """Locked yt-dlp subprocess downloader routed through the loopback proxy."""

    # 所选格式/档位归一化描述（不含 URL），写入 provenance.format_profile。
    format_profile = "res:1080+mp4-remux"

    def __init__(
        self,
        ffprobe: str | None = None,
        ffmpeg: str | None = None,
        cookie_file_path: Path | None = None,
        proxy_factory: Callable[[str], LoopbackFilterProxy] | None = None,
    ) -> None:
        self.ffprobe = ffprobe or os.environ.get("YUANZHIKU_FFPROBE_BIN", "ffprobe")
        self.ffmpeg = ffmpeg or os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg")
        self.cookie_file_path = cookie_file_path
        self._proxy_factory = proxy_factory or self._default_proxy_factory
        # 无进展断路器观察窗口间隔：由作业按设置注入（单 worker，无并发竞争）。
        self.no_progress_seconds = 10.0

    @staticmethod
    def _default_proxy_factory(platform: str) -> LoopbackFilterProxy:
        return LoopbackFilterProxy(registered_domains(platform))

    @staticmethod
    def _yt_dlp_version() -> str:
        try:
            import yt_dlp  # type: ignore[import-not-found]

            return str(getattr(yt_dlp.version, "__version__", "unknown"))
        except ImportError:
            return "unavailable"

    @staticmethod
    def _extract_title(output: bytes) -> str:
        """Clean the --print captured platform title for durable storage.

        去除控制字符与换行，并截断至 title 字段上限（models.py 的
        DownloadLinkRequest/PasteImportRequest 一致为 max_length=500）；
        空或捕获失败退化为空串，由落库侧回退"未命名视频"。
        """
        text = output.decode("utf-8", "replace")
        cleaned = "".join(character for character in text if character.isprintable() or character == " ")
        return cleaned.strip()[:500].strip()

    def _cookie_file_available(self) -> bool:
        if self.cookie_file_path is None:
            return False
        try:
            return self.cookie_file_path.is_file() and self.cookie_file_path.stat().st_size <= MAX_COOKIE_BYTES
        except OSError:
            return False

    def capability(self) -> dict[str, object]:
        return {
            "enabled": self._yt_dlp_version() != "unavailable"
            and bool(shutil.which(self.ffprobe) and shutil.which(self.ffmpeg)),
            "adapter": "yt-dlp",
            "version": self._yt_dlp_version(),
            "supported_platforms": ["bilibili", "douyin"],
            "cookie_file_available": self._cookie_file_available(),
            "network": True,
        }

    @staticmethod
    def config_hash(platform: str, format_profile: str) -> str:
        value = f"yt-dlp:1:{platform}:{format_profile}".encode("ascii")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _subprocess_environment() -> dict[str, str]:
        """Child environment with every proxy variable cleared as a second lock."""
        blocked = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
        return {key: value for key, value in os.environ.items() if key.upper() not in blocked}

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen) -> None:
        """Terminate yt-dlp and any ffmpeg children it spawned (REQ-047.3)."""
        if process.poll() is not None:
            return
        try:
            import psutil  # type: ignore[import-not-found]

            try:
                parent = psutil.Process(process.pid)
                members = parent.children(recursive=True) + [parent]
                for member in members:
                    try:
                        member.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(members, timeout=3)
                for member in alive:
                    try:
                        member.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.wait(timeout=5)
                return
            except psutil.NoSuchProcess:
                return
        except ImportError:
            pass
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def download(
        self,
        *,
        url: str,
        platform: str,
        workspace: Path,
        limits: MediaProcessingLimits,
        use_cookie: bool,
        cookie_path: Path | None,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        progress: Callable[[int, str], None],
    ) -> DownloadedVideo:
        domains = registered_domains(platform)
        if not domains:
            raise DownloadInputInvalid("platform")
        if use_cookie and (cookie_path is None or not cookie_path.is_file()):
            raise DownloadInputInvalid("cookie")
        # 启动回环过滤代理；启动失败 fail-closed → blocked，绝不直连回退。
        proxy = self._proxy_factory(platform)
        try:
            try:
                port = proxy.start()
            except OSError as exc:
                raise DownloadUnavailable("proxy") from exc
            return self._run_download(
                url, port, use_cookie, cookie_path, workspace, limits, cancelled, heartbeat, progress
            )
        finally:
            proxy.close()

    def _run_download(
        self,
        url: str,
        port: int,
        use_cookie: bool,
        cookie_path: Path | None,
        workspace: Path,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        progress: Callable[[int, str], None],
    ) -> DownloadedVideo:
        command = [
            sys.executable,
            "-m", "yt_dlp",
            "--proxy", f"http://127.0.0.1:{port}",
            "--no-playlist",
            "--no-simulate",
            "--ignore-config",
            "--no-cache-dir",
            "--retries", "1",
            "--socket-timeout", "30",
            "--merge-output-format", "mp4",
            "--remux-video", "mp4",
            "-S", "res:1080",
            "-o", str(workspace / "video.%(ext)s"),
            "--print", "%(title)s",
        ]
        if use_cookie:
            assert cookie_path is not None
            command.extend(["--cookies", str(cookie_path)])
        command.append(url)
        # FFmpeg 仅作本地合并/remux；不指定任何以 ffmpeg 为下载器的选项。
        # stderr 只用于输出字节计数（无进展断路器），绝不落入日志/数据库正文；
        # stdout 只捕获 --print 的平台标题（512KB 上限纪律），同样不落盘不落日志。
        stderr_file = tempfile.TemporaryFile()
        stdout_file = tempfile.TemporaryFile()
        try:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    env=self._subprocess_environment(),
                )
            except FileNotFoundError as exc:
                raise DownloadUnavailable("yt_dlp_missing") from exc
        except Exception:
            stderr_file.close()
            stdout_file.close()
            raise
        started = time.monotonic()
        deadline = limits.deadline_monotonic or (started + limits.timeout_seconds)
        window = max(1.0, float(self.no_progress_seconds))
        last_check = started
        last_size = 0
        last_output = 0
        idle_windows = 0
        try:
            while True:
                if cancelled():
                    raise DownloadProcessingCancelled()
                if time.monotonic() >= deadline:
                    raise DownloadInputInvalid("timeout")
                size = LocalFfmpegMediaAnalyzer._workspace_size(workspace)
                if size > limits.maximum_workspace_bytes:
                    raise DownloadInputInvalid("workspace_limit")
                # 内存监测为尽力而为：子进程恰好退出等竞态（如 psutil.NoSuchProcess）
                # 不视为作业失败。
                memory = None
                if process.poll() is None:
                    try:
                        memory = LocalFfmpegMediaAnalyzer._process_memory_bytes(process.pid)
                    except Exception:
                        memory = None
                if memory is not None and memory > limits.maximum_memory_bytes:
                    raise DownloadInputInvalid("memory_limit")
                try:
                    process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    heartbeat()
                current = time.monotonic()
                if current - last_check >= window:
                    stderr_file.seek(0, os.SEEK_END)
                    stdout_file.seek(0, os.SEEK_END)
                    output_bytes = stderr_file.tell() + stdout_file.tell()
                    current_size = LocalFfmpegMediaAnalyzer._workspace_size(workspace)
                    if current_size > last_size or output_bytes > last_output:
                        idle_windows = 0
                    else:
                        idle_windows += 1
                        if idle_windows >= 2:
                            raise DownloadInputInvalid("no_progress")
                    last_check = current
                    last_size = current_size
                    last_output = output_bytes
                    progress(min(90, 15 + current_size // (1024 * 1024)), "正在下载视频")
        finally:
            if process.poll() is None:
                self._terminate_process_tree(process)
            title_output = b""
            try:
                if process.returncode == 0:
                    stdout_file.seek(0, os.SEEK_END)
                    if stdout_file.tell() > 512 * 1024:
                        raise DownloadInputInvalid("output_limit")
                    stdout_file.seek(0)
                    title_output = stdout_file.read()
            finally:
                stderr_file.close()
                stdout_file.close()
        if process.returncode != 0:
            raise DownloadInputInvalid("failed")
        title = self._extract_title(title_output)
        outputs = [
            item for item in workspace.iterdir()
            if item.is_file() and item.suffix.lower() in {".mp4", ".webm"} and not item.name.startswith(".")
        ]
        if len(outputs) != 1:
            raise DownloadInputInvalid("no_output")
        product = outputs[0]
        media_type = "video/mp4" if product.suffix.lower() == ".mp4" else "video/webm"
        return DownloadedVideo(
            filename=product.name,
            media_type=media_type,
            byte_size=product.stat().st_size,
            title=title,
        )
