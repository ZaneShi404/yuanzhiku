"""Ports define technology-neutral boundaries."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import BinaryIO, Protocol

from app.domain.artifacts import StoredArtifact


class ArtifactStoragePort(Protocol):
    def operation(self) -> AbstractContextManager[None]: ...

    def artifact_path(self, sha256: str) -> Path: ...

    def staging_path(self) -> Path: ...

    def check_capacity(self, expected_bytes: int) -> None: ...

    def store_stream(self, stream: BinaryIO, expected_bytes: int | None = None) -> StoredArtifact: ...

    def delete(self, sha256: str) -> None: ...

    def verify(self, sha256: str) -> bool: ...
