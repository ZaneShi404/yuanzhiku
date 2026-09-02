"""Source and artifact ingestion orchestration."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import BinaryIO

from app.domain.input_limits import (
    InputContentInvalid,
    InputTooLarge,
    validate_docx_members,
    validate_document_head,
)
from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort
from app.domain.models import PasteImportRequest, SourceType


ALLOWED_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt"}
VIDEO_SUFFIXES = {".mp4", ".webm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
CANONICAL_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ImportService:
    def __init__(self, repository: RepositoryPort, artifacts: ArtifactStoragePort) -> None:
        self.repository = repository
        self.artifacts = artifacts

    @staticmethod
    def _inspect_document_stream(suffix: str, stream: BinaryIO) -> None:
        """入库前内容校验（加固计划 Task 5）；校验后流位置回到起点。

        PDF 校验 %PDF- 魔数；TXT/Markdown 全量拒绝 NUL；DOCX 以 zip 中央
        目录做结构/成员上限校验（元数据可伪造，真实解压炸弹由解析作业的
        内存/磁盘断路器兜底）。失败抛 InputContentInvalid/InputTooLarge。
        """
        try:
            stream.seek(0)
        except (OSError, AttributeError) as exc:
            raise InputContentInvalid("文档内容校验失败") from exc
        chunk_size = 1024 * 1024
        try:
            if suffix == ".docx":
                # ZipFile 关闭时不会关闭底层流；UploadFile 的 SpooledTemporaryFile 可 seek。
                with zipfile.ZipFile(stream) as archive:
                    validate_docx_members(archive.infolist())
                stream.seek(0)
                return
            first = stream.read(chunk_size)
            validate_document_head(suffix, first)
            if suffix in {".txt", ".md", ".markdown"}:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    if b"\x00" in chunk:
                        raise InputContentInvalid("文本文件包含非法空字节")
            stream.seek(0)
        except (InputContentInvalid, InputTooLarge):
            stream.seek(0)
            raise
        except zipfile.BadZipFile as exc:
            stream.seek(0)
            raise InputContentInvalid("DOCX 结构不完整") from exc
        except OSError as exc:
            stream.seek(0)
            raise InputContentInvalid("文档内容校验失败") from exc

    def _persist_ingest(
        self,
        *, encoded: bytes | None, stream: BinaryIO | None, expected_bytes: int | None, source_type: str,
        title: str, author: str | None, language: str, notes: str | None, rights: str,
        domains: list[str], genres: list[str], tags: list[str], original_name: str, media_type: str, audit_event: str,
        source_date: str | None, job_kind: str = "parse",
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
                    domains=domains,
                    genres=genres,
                    tags=tags,
                    source_date=source_date,
                    artifact_sha256=stored.sha256,
                    original_name=original_name,
                    media_type=media_type,
                    byte_size=stored.byte_size,
                    job_payload={"filename": original_name, "media_type": media_type},
                    priority=100,
                    audit_event=audit_event,
                    job_kind=job_kind,
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
        if "\x00" in request.text:
            raise InputContentInvalid("粘贴文本包含非法空字节")
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
            domains=request.domains,
            genres=request.genres,
            tags=request.tags,
            original_name="pasted.md",
            media_type="text/markdown",
            audit_event="paste_import",
            source_date=request.source_date.isoformat() if request.source_date else None,
        )

    def video(
        self, stream: BinaryIO, filename: str, title: str, rights: str, author: str | None,
        language: str, notes: str | None, domains: list[str], genres: list[str], tags: list[str], expected_bytes: int | None,
        source_date: str | None = None,
    ) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise ValueError("仅支持 MP4 和 WebM 视频")
        if expected_bytes is not None:
            self.artifacts.check_capacity(expected_bytes)
        if not title.strip():
            title = Path(filename).stem or "未命名视频"
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
            domains=domains,
            genres=genres,
            tags=tags,
            original_name=Path(filename).name,
            media_type=CANONICAL_MEDIA_TYPES[suffix],
            audit_event="video_import",
            source_date=source_date,
            job_kind="video_analyze",
        )

    def image(
        self, stream: BinaryIO, filename: str, title: str, rights: str, author: str | None,
        language: str, notes: str | None, domains: list[str], genres: list[str], tags: list[str], expected_bytes: int | None,
        source_date: str | None = None,
    ) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            raise ValueError("仅支持 JPG、PNG 和 WebP 图片")
        if expected_bytes is not None:
            self.artifacts.check_capacity(expected_bytes)
        if not title.strip():
            title = Path(filename).stem or "未命名图片"
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
            domains=domains,
            genres=genres,
            tags=tags,
            original_name=Path(filename).name,
            media_type=CANONICAL_MEDIA_TYPES[suffix],
            audit_event="image_import",
            source_date=source_date,
            job_kind="image_analyze",
        )

    def downloaded_video(
        self,
        stream: BinaryIO,
        expected_bytes: int | None,
        *,
        platform: str,
        url_sanitized: str,
        yt_dlp_version: str,
        format_profile: str,
        cookie_used: bool,
        config_hash: str,
        title: str,
        author: str | None,
        language: str,
        notes: str | None,
        rights: str,
        domains: list[str],
        genres: list[str],
        tags: list[str],
        source_date: str | None,
        original_name: str,
        media_type: str,
        source_id: str | None = None,
        version_id: str | None = None,
    ) -> dict:
        """Persist a downloaded video and its provenance in one transaction.

        The source/content version/artifact rows and the matching
        ``video_download_provenance`` row commit together (source_id UNIQUE);
        on failure the whole transaction rolls back and this method compensates
        only the artifact file it created (same pattern as ``_persist_ingest``).
        ``create_ingest`` queues the ``video_analyze`` job in the same
        transaction, so a failure never leaves a half-created source.
        """
        if not title.strip():
            title = "未命名视频"
        with self.artifacts.operation():
            stored = self.artifacts.store_stream(stream, expected_bytes)
            try:
                source, version, job = self.repository.create_ingest(
                    source_type=SourceType.VIDEO_LINK.value,
                    title=title.strip(),
                    author=author,
                    language=language,
                    notes=notes,
                    rights=rights,
                    domains=domains,
                    genres=genres,
                    tags=tags,
                    source_date=source_date,
                    artifact_sha256=stored.sha256,
                    original_name=original_name,
                    media_type=media_type,
                    byte_size=stored.byte_size,
                    job_payload={"filename": original_name, "media_type": media_type},
                    priority=100,
                    audit_event="video_download",
                    job_kind="video_analyze",
                    download_provenance={
                        "platform": platform,
                        "url_sanitized": url_sanitized[:4096],
                        "yt_dlp_version": yt_dlp_version,
                        "format_profile": format_profile,
                        "cookie_used": 1 if cookie_used else 0,
                        "config_hash": config_hash,
                    },
                    source_id=source_id,
                    version_id=version_id,
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

    def file(
        self, stream: BinaryIO, filename: str, content_type: str | None, title: str, rights: str,
        author: str | None, language: str, notes: str | None, domains: list[str], genres: list[str], tags: list[str], expected_bytes: int | None,
        source_date: str | None = None,
    ) -> dict:
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError("仅支持 PDF、DOCX、Markdown 和 TXT")
        self._inspect_document_stream(suffix, stream)
        if expected_bytes is not None:
            self.artifacts.check_capacity(expected_bytes)
        if not title.strip():
            title = Path(filename).stem or "未命名文档"
        # Upload MIME metadata is untrusted and is never used to serve originals.
        media_type = CANONICAL_MEDIA_TYPES[suffix]
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
            domains=domains,
            genres=genres,
            tags=tags,
            original_name=Path(filename).name,
            media_type=media_type,
            audit_event="file_import",
            source_date=source_date,
        )
