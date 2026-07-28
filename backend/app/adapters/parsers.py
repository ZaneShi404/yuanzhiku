"""Local-only parsing adapters. No network or model download code exists here."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    parser_name: str
    config_hash: str
    format: str
    blocked_reason: str | None = None


def _config_hash(name: str, version: str) -> str:
    return hashlib.sha256(f"{name}:{version}:local-only".encode("ascii")).hexdigest()


def parse_local(artifact_path: Path, filename: str, media_type: str | None = None) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    raw = artifact_path.read_bytes()
    if suffix in {".txt", ".md", ".markdown"}:
        try:
            return ParsedDocument(raw.decode("utf-8"), "native-utf8", _config_hash("native-utf8", "1"), suffix[1:])
        except UnicodeDecodeError:
            return ParsedDocument("", "native-utf8", _config_hash("native-utf8", "1"), suffix[1:], "文本不是有效 UTF-8")
    if suffix == ".pdf" or media_type == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                return ParsedDocument("", "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf", "PDF 已加密，等待人工提供可解析副本")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            if not text.strip():
                return ParsedDocument("", "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf", "awaiting_ocr")
            return ParsedDocument(text, "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf")
        except Exception:
            return ParsedDocument("", "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf", "PDF 无法本地解析")
    if suffix == ".docx" or media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            from docx import Document

            document = Document(io.BytesIO(raw))
            paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
            text = "\n\n".join(paragraphs)
            if not text.strip():
                return ParsedDocument("", "python-docx-local", _config_hash("python-docx-local", "1.1.2"), "docx", "DOCX 没有可提取文本")
            return ParsedDocument(text, "python-docx-local", _config_hash("python-docx-local", "1.1.2"), "docx")
        except Exception:
            return ParsedDocument("", "python-docx-local", _config_hash("python-docx-local", "1.1.2"), "docx", "DOCX 无法本地解析")
    return ParsedDocument("", "unsupported-local", _config_hash("unsupported-local", "1"), suffix.lstrip("."), "不支持的文档类型")
