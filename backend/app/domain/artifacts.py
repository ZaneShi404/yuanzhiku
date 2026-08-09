"""Artifact values shared by application ports and filesystem adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredArtifact:
    sha256: str
    byte_size: int
    path: Path
    was_new: bool
