"""Filesystem adapter for immutable content-addressed artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import DataPaths

MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_AFTER = 10 * 1024 * 1024 * 1024
COPY_CHUNK = 1024 * 1024


class StorageLimitError(ValueError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    sha256: str
    byte_size: int
    path: Path
    was_new: bool


class ArtifactStore:
    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths
        # The process-wide instance lock prevents another application instance from
        # using this data root. This lock additionally serializes file/database
        # compensations between concurrent requests in this instance.
        self._operation_lock = threading.RLock()

    @contextmanager
    def operation(self):
        """Keep a multi-step artifact operation mutually exclusive."""
        with self._operation_lock:
            yield

    def artifact_path(self, sha256: str) -> Path:
        return self.paths.artifacts / sha256[:2] / sha256

    def check_capacity(self, expected_bytes: int) -> None:
        if expected_bytes < 0 or expected_bytes > MAX_FILE_BYTES:
            raise StorageLimitError("文件大小超过 2GB 限制")
        free = shutil.disk_usage(self.paths.root).free
        required = expected_bytes * 2 + MIN_FREE_AFTER
        if free < required:
            raise StorageLimitError("可用空间不足：需要文件两倍空间且导入后保留 10GB")

    def store_stream(self, stream: BinaryIO, expected_bytes: int | None = None) -> StoredArtifact:
        with self.operation():
            return self._store_stream(stream, expected_bytes)

    def _store_stream(self, stream: BinaryIO, expected_bytes: int | None = None) -> StoredArtifact:
        if expected_bytes is not None:
            self.check_capacity(expected_bytes)
        self.paths.staging.mkdir(parents=True, exist_ok=True)
        stage = self.paths.staging / f"{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            with stage.open("xb") as target:
                while True:
                    chunk = stream.read(COPY_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise StorageLimitError("文件大小超过 2GB 限制")
                    if size % (64 * COPY_CHUNK) == 0:
                        self.check_capacity(size)
                    digest.update(chunk)
                    target.write(chunk)
            self.check_capacity(size)
            sha256 = digest.hexdigest()
            destination = self.artifact_path(sha256)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                stage.unlink(missing_ok=True)
                return StoredArtifact(sha256, size, destination, False)
            os.replace(stage, destination)
            return StoredArtifact(sha256, size, destination, True)
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    def read_bytes(self, sha256: str) -> bytes:
        return self.artifact_path(sha256).read_bytes()

    def delete(self, sha256: str) -> None:
        path = self.artifact_path(sha256)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def verify(self, sha256: str) -> bool:
        path = self.artifact_path(sha256)
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(COPY_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest() == sha256
