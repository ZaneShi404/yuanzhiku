"""Import prefill suggestions: read-only metadata recognition, no network, no persistence (REQ-049)."""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path

TITLE_MAX_LENGTH = 500
AUTHOR_MAX_LENGTH = 300
LANGUAGE_SAMPLE_LENGTH = 4000

TEXT_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
ALLOWED_SUFFIXES = frozenset({".pdf", ".docx"}) | TEXT_SUFFIXES | IMAGE_SUFFIXES

_EXIF_DATETIME_ORIGINAL = 0x9003  # 36867 DateTimeOriginal
_EXIF_ARTIST = 0x013B  # 315 Artist


def _empty_suggestion() -> dict[str, str | None]:
    return {"title": None, "author": None, "language": None, "source_date": None}


def _clean(value: object, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:maximum] if cleaned else None


def _clean_date(value: object) -> str | None:
    return value.date().isoformat() if isinstance(value, datetime) else None


def detect_language(sample: str) -> str | None:
    """Best-effort zh/en heuristic over the first 4000 characters; None means no suggestion."""
    cjk = 0
    latin = 0
    for char in sample[:LANGUAGE_SAMPLE_LENGTH]:
        if "一" <= char <= "鿿":
            cjk += 1
        elif "a" <= char <= "z" or "A" <= char <= "Z":
            latin += 1
    letters = cjk + latin
    if not letters:
        return None
    ratio = cjk / letters
    if ratio >= 0.2:
        return "zh"
    if ratio < 0.02 and latin >= 50:
        return "en"
    return None


def suggest_text(text: str) -> dict[str, str | None]:
    suggestion = _empty_suggestion()
    if not text.strip():
        return suggestion
    title: str | None = None
    first_line: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if first_line is None:
            first_line = stripped
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            break
    suggestion["title"] = _clean(title if title is not None else first_line, TITLE_MAX_LENGTH)
    suggestion["language"] = detect_language(text)
    return suggestion


def _clean_exif_text(value: object) -> str | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value.strip() if ch.isprintable())[:AUTHOR_MAX_LENGTH]
    return text or None


def _exif_date(value: object) -> str | None:
    text = _clean_exif_text(value)
    if not text:
        return None
    match = re.match(r"^(\d{4}):(\d{2}):(\d{2})", text)
    return "-".join(match.groups()) if match else None


def suggest_image(filename: str, data: bytes) -> dict[str, str | None]:
    """Image suggestions: filename stem as title, plus EXIF Artist/DateTimeOriginal when readable."""
    suggestion = _empty_suggestion()
    suggestion["title"] = _clean(Path(filename).stem, TITLE_MAX_LENGTH)
    try:
        from PIL import ExifTags, Image

        with Image.open(io.BytesIO(data)) as image:
            exif = image.getexif()
            try:
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            except Exception:
                exif_ifd = {}
            if not isinstance(exif_ifd, dict):
                exif_ifd = {}
            artist = exif.get(_EXIF_ARTIST) or exif_ifd.get(_EXIF_ARTIST)
            taken = exif.get(_EXIF_DATETIME_ORIGINAL) or exif_ifd.get(_EXIF_DATETIME_ORIGINAL)
    except Exception:
        return suggestion
    suggestion["author"] = _clean_exif_text(artist)
    suggestion["source_date"] = _exif_date(taken)
    return suggestion


def _suggest_pdf(data: bytes) -> dict[str, str | None]:
    suggestion = _empty_suggestion()
    try:
        from pypdf import PdfReader

        metadata = PdfReader(io.BytesIO(data)).metadata
    except Exception:
        return suggestion
    if metadata is None:
        return suggestion
    try:
        suggestion["title"] = _clean(metadata.title, TITLE_MAX_LENGTH)
    except Exception:
        pass
    try:
        suggestion["author"] = _clean(metadata.author, AUTHOR_MAX_LENGTH)
    except Exception:
        pass
    try:
        created = metadata.creation_date
    except Exception:
        created = None
    source_date = _clean_date(created)
    if source_date is None:
        raw = metadata.get("/CreationDate")
        if isinstance(raw, str):
            match = re.match(r"^D:(\d{4})(\d{2})(\d{2})", raw.strip())
            if match:
                source_date = "-".join(match.groups())
    suggestion["source_date"] = source_date
    return suggestion


def _suggest_docx(data: bytes) -> dict[str, str | None]:
    suggestion = _empty_suggestion()
    try:
        import docx

        properties = docx.Document(io.BytesIO(data)).core_properties
    except Exception:
        return suggestion
    try:
        suggestion["title"] = _clean(properties.title, TITLE_MAX_LENGTH)
    except Exception:
        pass
    try:
        suggestion["author"] = _clean(properties.author, AUTHOR_MAX_LENGTH)
    except Exception:
        pass
    try:
        suggestion["source_date"] = _clean_date(properties.created)
    except Exception:
        pass
    return suggestion


def suggest_document(filename: str, data: bytes) -> dict[str, str | None]:
    """Suggest metadata by suffix; unreadable files yield an all-None dict instead of raising."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pdf":
            return _suggest_pdf(data)
        if suffix == ".docx":
            return _suggest_docx(data)
        if suffix in TEXT_SUFFIXES:
            try:
                return suggest_text(data.decode("utf-8"))
            except UnicodeDecodeError:
                return _empty_suggestion()
        if suffix in IMAGE_SUFFIXES:
            return suggest_image(filename, data)
    except Exception:
        return _empty_suggestion()
    return _empty_suggestion()
