"""REQ-021：Docling 路径 PDF 证据必须携带真实页码 locator。

用真实 docling-core 文档模型构造合成 DoclingDocument（不触发任何模型
下载），monkeypatch DocumentConverter.convert 后走完整 parse() 路径验证。
"""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest
from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem

from app.adapters.parsers import LocalDocumentParser, _docling_segments


def _synthetic_document() -> DoclingDocument:
    doc = DoclingDocument(name="synthetic")
    bbox = BoundingBox(l=0, t=0, r=100, b=20)
    doc.add_text(label=DocItemLabel.TEXT, text="第一页第一段", prov=ProvenanceItem(page_no=1, bbox=bbox, charspan=(0, 6)))
    doc.add_text(label=DocItemLabel.TEXT, text="第一页第二段", prov=ProvenanceItem(page_no=1, bbox=bbox, charspan=(0, 6)))
    doc.add_text(label=DocItemLabel.TEXT, text="第二页第一段", prov=ProvenanceItem(page_no=2, bbox=bbox, charspan=(0, 6)))
    return doc


def _parser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> LocalDocumentParser:
    parser = LocalDocumentParser(tmp_path / "models", tmp_path / "models.lock.json")
    monkeypatch.setattr(LocalDocumentParser, "_model_status", lambda self: (True, "", {"models": [{}]}))
    return parser


def test_docling_parse_emits_page_locators(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parser = _parser(monkeypatch, tmp_path)
    artifact = tmp_path / "synthetic.pdf"
    artifact.write_bytes(b"%PDF-synthetic")
    monkeypatch.setattr(
        "docling.document_converter.DocumentConverter.convert",
        lambda self, source: SimpleNamespace(document=_synthetic_document()),
    )

    result = parser.parse(artifact, "synthetic.pdf", "application/pdf", tmp_path / "workspace")

    assert result.parser_name == "docling-local"
    assert result.blocked_reason is None
    assert result.text == "第一页第一段\n第一页第二段\n\n第二页第一段"
    locators = [segment.locator for segment in result.segments]
    assert locators == [
        {"type": "pdf_page_char_range", "page": 1, "char_range": [0, 13]},
        {"type": "pdf_page_char_range", "page": 2, "char_range": [0, 6]},
    ]
    # segments 偏移与文本自洽：每条 segment 能切回本页文本
    assert result.text[result.segments[0].start:result.segments[0].end] == "第一页第一段\n第一页第二段"
    assert result.text[result.segments[1].start:result.segments[1].end] == "第二页第一段"


def test_docling_segments_fallback_when_provenance_missing(tmp_path: Path) -> None:
    doc = DoclingDocument(name="synthetic")
    doc.add_text(label=DocItemLabel.TEXT, text="无页码条目")
    assert _docling_segments(doc) is None


def test_docling_parse_without_provenance_uses_whole_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parser = _parser(monkeypatch, tmp_path)
    artifact = tmp_path / "synthetic.pdf"
    artifact.write_bytes(b"%PDF-synthetic")
    doc = DoclingDocument(name="synthetic")
    doc.add_text(label=DocItemLabel.TEXT, text="无页码整文回退内容")
    monkeypatch.setattr(
        "docling.document_converter.DocumentConverter.convert",
        lambda self, source: SimpleNamespace(document=doc),
    )

    result = parser.parse(artifact, "synthetic.pdf", "application/pdf", tmp_path / "workspace")

    assert result.parser_name == "docling-local"
    assert result.text.strip()
    assert result.segments == ()
