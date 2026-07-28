"""Source and artifact ingestion orchestration."""

from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import BinaryIO

from app.adapters.sqlite import SqliteRepository
from app.adapters.storage import ArtifactStore
from app.domain.models import PasteImportRequest, SourceType


ALLOWED_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt"}


class ImportService:
    def __init__(self, repository: SqliteRepository, artifacts: ArtifactStore) -> None:
        self.repository = repository
        self.artifacts = artifacts

    def _persist_ingest(
        self,
        *, encoded: bytes | None, stream: BinaryIO | None, expected_bytes: int | None, source_type: str,
        title: str, author: str | None, language: str, notes: str | None, rights: str,
        categories: list[str], tags: list[str], original_name: str, media_type: str, audit_event: str,
    ) -> dict:
        # Filesystem and database cannot share a transaction. Serialize the small
        # orchestration window and compensate only a file this operation created.
        with self.artifacts.operation():
            stored = self.artifacts.store_stream(io.BytesIO(encoded) if encoded is not None else stream, expected_bytes)
            try:
                source, version, job = self.repository.create_ingest(
                    source_type=source_type,
                    title=title,
                    author=author,
                    language=language,
                    notes=notes,
                    rights=rights,
                    categories=categories,
                    tags=tags,
                    artifact_sha256=stored.sha256,
                    original_name=original_name,
                    media_type=media_type,
                    byte_size=stored.byte_size,
                    job_payload={"filename": original_name, "media_type": media_type},
                    priority=100,
                    audit_event=audit_event,
                )
            except Exception:
                if stored.was_new and self.repository.delete_artifact_if_unreferenced(stored.sha256):
                    self.artifacts.delete(stored.sha256)
                raise
        return {
            "source": source,
            "content_version": version,
            "artifact": {"sha256": stored.sha256, "byte_size": stored.byte_size, "deduplicated": not stored.was_new},
            "job": job,
        }

    def paste(self, request: PasteImportRequest) -> dict:
        encoded = request.text.encode("utf-8")
        if len(encoded) > 10 * 1024 * 1024:
            raise ValueError("粘贴 UTF-8 文本不能超过 10MB")
        return self._persist_ingest(
            encoded=encoded,
            stream=None,
            expected_bytes=len(encoded),
            source_type=SourceType.PASTE.value,
            title=request.title,
            author=request.author,
            language=request.language,
            notes=request.notes,
            rights=request.rights.value,
            categories=request.categories,
            tags=request.tags,
            original_name="pasted.md",
            media_type="text/markdown",
            audit_event="paste_import",
        )

    def file(
        self, stream: BinaryIO, filename: str, content_type: str | None, title: str, rights: str,
        author: str | None, language: str, notes: str | None, categories: list[str], tags: list[str], expected_bytes: int | None,
    ) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError("仅支持 PDF、DOCX、Markdown 和 TXT")
        if not title.strip():
            title = Path(filename).stem or "未命名文档"
        media_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return self._persist_ingest(
            encoded=None,
            stream=stream,
            expected_bytes=expected_bytes,
            source_type=SourceType.FILE.value,
            title=title.strip(),
            author=author,
            language=language,
            notes=notes,
            rights=rights,
            categories=categories,
            tags=tags,
            original_name=Path(filename).name,
            media_type=media_type,
            audit_event="file_import",
        )
