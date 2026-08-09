"""Technology-neutral document parsing contract."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.domain.parsing import ParsedDocument


class DocumentParserPort(Protocol):
    def parse(self, artifact_path: Path, filename: str, media_type: str | None, workspace: Path) -> ParsedDocument: ...

    def capability(self) -> dict[str, object]: ...
