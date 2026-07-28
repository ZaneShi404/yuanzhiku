"""Application configuration and local instance coordination."""

from __future__ import annotations

import json
import os
import socket
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

    def create(self) -> None:
        for path in (self.root, self.state, self.artifacts, self.staging, self.models, self.backups, self.exports, self.logs):
            path.mkdir(parents=True, exist_ok=True)


def data_paths(root: str | Path | None = None) -> DataPaths:
    selected = root or os.environ.get("YUANZHIKU_DATA_ROOT") or DEFAULT_DATA_ROOT
    return DataPaths(Path(selected).expanduser().resolve())


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


def choose_port(paths: DataPaths, requested_port: int | None = None) -> int:
    paths.create()
    if requested_port is not None:
        if not 1024 <= requested_port <= 65535:
            raise ValueError("端口必须在 1024 至 65535 之间")
        port = requested_port
    elif paths.port_file.exists():
        try:
            port = int(json.loads(paths.port_file.read_text(encoding="utf-8"))["port"])
        except (ValueError, KeyError, json.JSONDecodeError):
            port = 0
        if port and _available(port):
            return port
        port = 0
    else:
        port = 0
    if not port:
        for candidate in range(8765, 8866):
            if _available(candidate):
                port = candidate
                break
    if not port or not _available(port):
        raise RuntimeError("没有可用的本地端口")
    paths.port_file.write_text(json.dumps({"port": port}), encoding="utf-8")
    return port


class InstanceLock:
    """A Windows-compatible advisory exclusive lock retained for app lifetime."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: IO[bytes] | None = None

    def acquire(self) -> None:
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
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("该数据根已有运行中的源知库实例") from exc

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
