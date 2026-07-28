"""Ports define technology-neutral boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ArtifactStoragePort(Protocol):
    def artifact_path(self, sha256: str) -> Path: ...

    def verify(self, sha256: str) -> bool: ...
