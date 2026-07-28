"""Local-only parsing adapters. No network or model download code exists here."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ParsedSegment:
    """A text range with a parser-proven native location."""

    start: int
    end: int
    locator: dict[str, int | str]


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    parser_name: str
    config_hash: str
    format: str
    blocked_reason: str | None = None
    segments: tuple[ParsedSegment, ...] = ()


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
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages)
            if not text.strip():
                return ParsedDocument("", "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf", "awaiting_ocr")
            segments: list[ParsedSegment] = []
            offset = 0
            for page_number, page_text in enumerate(pages, start=1):
                if page_text:
                    segments.append(ParsedSegment(offset, offset + len(page_text), {
                        "type": "pdf_page_char_range", "page": page_number, "char_range": [0, len(page_text)],
                    }))
                offset += len(page_text) + 2
            return ParsedDocument(text, "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf", segments=tuple(segments))
        except Exception:
            return ParsedDocument("", "pypdf-local", _config_hash("pypdf-local", "5.4.0"), "pdf", "PDF 无法本地解析")
    if suffix == ".docx" or media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            from docx import Document

            document = Document(io.BytesIO(raw))
            paragraphs = [(ordinal, paragraph.text) for ordinal, paragraph in enumerate(document.paragraphs, start=1) if paragraph.text]
            text = "\n\n".join(paragraph for _, paragraph in paragraphs)
            if not text.strip():
                return ParsedDocument("", "python-docx-local", _config_hash("python-docx-local", "1.1.2"), "docx", "DOCX 没有可提取文本")
            segments: list[ParsedSegment] = []
            offset = 0
            for ordinal, paragraph in paragraphs:
                segments.append(ParsedSegment(offset, offset + len(paragraph), {
                    "type": "docx_structure_char_range", "structure": "body", "paragraph_ordinal": ordinal, "char_range": [0, len(paragraph)],
                }))
                offset += len(paragraph) + 2
            return ParsedDocument(text, "python-docx-local", _config_hash("python-docx-local", "1.1.2"), "docx", segments=tuple(segments))
        except Exception:
            return ParsedDocument("", "python-docx-local", _config_hash("python-docx-local", "1.1.2"), "docx", "DOCX 无法本地解析")
    return ParsedDocument("", "unsupported-local", _config_hash("unsupported-local", "1"), suffix.lstrip("."), "不支持的文档类型")
