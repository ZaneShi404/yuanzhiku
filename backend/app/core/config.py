"""Application configuration and local instance coordination."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

DEFAULT_DATA_ROOT = Path(r"E:\源知库\data")


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def download(self) -> Path:
        return self.state / "download"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def database(self) -> Path:
        return self.state / "knowledge.db"

    @property
    def port_file(self) -> Path:
        return self.state / "port.json"

    @property
    def lock_file(self) -> Path:
        return self.state / "instance.lock"

    @property
    def maintenance_lock_file(self) -> Path:
        return self.state / "maintenance.lock"

    def create(self) -> None:
        for path in (self.root, self.state, self.download, self.artifacts, self.staging, self.models, self.backups, self.exports, self.logs):
            path.mkdir(parents=True, exist_ok=True)


def data_paths(root: str | Path | None = None) -> DataPaths:
    selected = root or os.environ.get("YUANZHIKU_DATA_ROOT") or DEFAULT_DATA_ROOT
    return DataPaths(Path(selected).expanduser().resolve())


def compose_data_root(value: str | Path | None = None) -> Path:
    """集成测试 compose 数据根守卫（REQ-045）。

    `YUANZHIKU_COMPOSE_DATA_ROOT` 只允许解析为仓库内
    `tests/runtime/compose-<run-id>`；任何其他位置（尤其是日常数据根）
    直接拒绝，防止集成测试污染日常使用数据。
    """
    raw = value or os.environ.get("YUANZHIKU_COMPOSE_DATA_ROOT")
    if not raw:
        raise ValueError("必须将 YUANZHIKU_COMPOSE_DATA_ROOT 设为 tests/runtime/compose-<run-id>")
    resolved = Path(raw).expanduser().resolve()
    runtime_root = (Path(__file__).resolve().parents[3] / "tests" / "runtime").resolve()
    if resolved.parent != runtime_root or not resolved.name.startswith("compose-"):
        raise ValueError("集成测试数据根必须位于 tests/runtime/compose-<run-id>，不得使用日常数据目录")
    return resolved


def database_url(paths: DataPaths) -> str:
    """Select SQLite by default; Compose supplies an explicit PostgreSQL URL."""
    return os.environ.get("YUANZHIKU_DATABASE_URL", f"sqlite:///{paths.database.as_posix()}")


class DatabaseUrlConfigurationError(ValueError):
    """Raised when the configured database backend is not supported."""


def database_backend(value: str) -> str:
    """Classify a configured URL without exposing its credentials in errors."""
    scheme = urlsplit(value).scheme.lower()
    if scheme == "sqlite":
        return "sqlite"
    if scheme in {"postgresql", "postgres"} or scheme.startswith(("postgresql+", "postgres+")):
        return "postgresql"
    display_scheme = scheme or "missing"
    raise DatabaseUrlConfigurationError(
        "YUANZHIKU_DATABASE_URL 必须使用 sqlite:// URL，或 PostgreSQL URL "
        "(postgresql://、postgres://、postgresql+<driver>://、postgres+<driver>://)；"
        f"当前 scheme 为 {display_scheme!r}"
    )


def _available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def saved_port(paths: DataPaths) -> int | None:
    """Return the saved local-port preference without changing it."""
    if not paths.port_file.exists():
        return None
    try:
        port = int(json.loads(paths.port_file.read_text(encoding="utf-8"))["port"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("保存的本地端口配置无效；请显式指定 -Port") from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError("保存的本地端口配置无效；请显式指定 -Port")
    return port


def choose_port(paths: DataPaths, requested_port: int | None = None) -> int:
    paths.create()
    if requested_port is not None:
        if not 1024 <= requested_port <= 65535:
            raise ValueError("端口必须在 1024 至 65535 之间")
        if not _available(requested_port):
            raise RuntimeError("请求的本地端口不可用；未修改保存的端口偏好")
        paths.port_file.write_text(json.dumps({"port": requested_port}), encoding="utf-8")
        return requested_port

    port = saved_port(paths)
    if port is not None:
        if not _available(port):
            raise RuntimeError("保存的本地端口不可用；未修改保存的端口偏好")
        return port

    for candidate in range(8765, 8866):
        if _available(candidate):
            paths.port_file.write_text(json.dumps({"port": candidate}), encoding="utf-8")
            return candidate
    raise RuntimeError("没有可用的本地端口")


class InterprocessLock:
    """A thread-reentrant wrapper around a Windows-compatible process file lock."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._guard = threading.RLock()
        self._depth = 0
        self._lock: InstanceLock | None = None

    def __enter__(self) -> None:
        self._guard.acquire()
        try:
            if self._depth == 0:
                self._lock = InstanceLock(self.path)
                self._lock.acquire(timeout_seconds=30.0)
            self._depth += 1
        except Exception:
            self._guard.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._lock is not None:
                self._lock.release()
                self._lock = None
        finally:
            self._guard.release()


class InstanceLock:
    """A Windows-compatible advisory exclusive lock retained for app lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: IO[bytes] | None = None

    def acquire(self, timeout_seconds: float = 0.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    if self.handle.tell() == 0:
                        self.handle.write(b"0")
                        self.handle.flush()
                        self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                self.handle.close()
                self.handle = None
                if time.monotonic() >= deadline:
                    raise RuntimeError("该数据根已有运行中的源知库实例") from exc
                time.sleep(0.05)

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
