"""Immutable document representations, evidence, citations and knowledge."""

from __future__ import annotations

import hashlib
import json

from app.adapters.sqlite import SqliteRepository
from app.domain.models import KnowledgeCreate, KnowledgeType, ManualRepresentationCreate


class DocumentService:
    def __init__(self, repository: SqliteRepository) -> None:
        self.repository = repository

    @staticmethod
    def native_locator(text: str, fmt: str) -> dict:
        encoded = text.encode("utf-8")
        end_line = text.count("\n") + 1
        heading = next((line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip().startswith("#")), None)
        if fmt in {"md", "markdown", "txt"}:
            return {"type": "text_range", "heading": heading, "paragraph_ordinal": 1, "utf8_byte_range": [0, len(encoded)], "line_range": [1, end_line], "char_range": [0, len(text)]}
        if fmt == "pdf":
            return {"type": "pdf_page_char_range", "page": 1, "char_range": [0, len(text)]}
        if fmt == "docx":
            return {"type": "docx_structure_char_range", "structure": "body", "paragraph_ordinal": 1, "char_range": [0, len(text)]}
        return {"type": "text_range", "char_range": [0, len(text)]}

    @staticmethod
    def search_chunk_pairs(text: str, chunk_size: int = 1200) -> list[tuple[str, str]]:
        """Derived index payloads only; chunks are never evidence records."""
        chunks = [text[offset:offset + chunk_size] for offset in range(0, len(text), chunk_size)] or [""]
        return [(chunk, hashlib.sha256(chunk.encode("utf-8")).hexdigest()) for chunk in chunks]

    def record_parsed(self, version_id: str, artifact_sha256: str, text: str, parser_name: str, config_hash: str, fmt: str) -> dict:
        representation = self.repository.create_representation(version_id, "extraction", parser_name, config_hash, text)
        version = self.repository.get_version(version_id)
        if version is None:
            raise KeyError("内容版本不存在")
        chunks = self.repository.create_search_chunks(version["source_id"], version_id, representation["id"], self.search_chunk_pairs(text))
        excerpt = text[:300]
        evidence = self.repository.create_evidence(
            version_id=version_id,
            artifact_sha256=artifact_sha256,
            representation_id=representation["id"],
            parser_config_hash=config_hash,
            locator=self.native_locator(text, fmt),
            excerpt=excerpt,
            excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        )
        citation = self.repository.create_citation(evidence["id"])
        return {"representation": representation, "evidence": evidence, "citation": citation, "search_chunks": chunks}

    def create_manual_representation(self, version_id: str, request: ManualRepresentationCreate) -> dict:
        originals = self.repository.representations_for_version(version_id)
        parent_id = originals[-1]["id"] if originals else None
        config_hash = hashlib.sha256(b"manual-revision-v1").hexdigest()
        representation = self.repository.create_representation(version_id, "manual", "human-revised", config_hash, request.text, parent_id)
        version = self.repository.get_version(version_id)
        if version is None:
            raise KeyError("内容版本不存在")
        excerpt = request.text[:300]
        evidence = self.repository.create_evidence(
            version_id=version_id, artifact_sha256=version["artifact_sha256"], representation_id=representation["id"],
            parser_config_hash=config_hash, locator=self.native_locator(request.text, "txt"), excerpt=excerpt,
            excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        )
        citation = self.repository.create_citation(evidence["id"])
        return {"representation": representation, "evidence": evidence, "citation": citation, "note": request.note}

    def create_knowledge(self, request: KnowledgeCreate) -> dict:
        for evidence_id in request.evidence_ids:
            if self.repository.get_evidence(evidence_id) is None:
                raise ValueError("引用的 evidence 不存在")
        return self.repository.create_knowledge(request.kind.value, request.statement, request.evidence_ids)

    def publish_knowledge(self, knowledge_id: str) -> dict:
        knowledge = self.repository.get_knowledge(knowledge_id)
        if knowledge is None:
            raise KeyError("知识项不存在")
        needs_evidence = knowledge["kind"] not in {KnowledgeType.UNVERIFIED.value, KnowledgeType.OPINION.value, KnowledgeType.CITATION.value}
        if needs_evidence and not knowledge["evidence_ids"]:
            raise ValueError("实质事实、指令或案例知识发布需要有效证据")
        for evidence_id in knowledge["evidence_ids"]:
            evidence = self.repository.get_evidence(evidence_id)
            if evidence is None or not evidence["is_validated"]:
                raise ValueError("知识引用包含无效证据")
        return self.repository.publish_knowledge(knowledge_id) or {}

    def citation(self, citation_id: str) -> dict:
        citation = self.repository.citation_details(citation_id)
        if citation is None:
            raise KeyError("引用不存在")
        citation["locator"] = json.loads(citation.pop("locator_json"))
        citation["context"] = citation.pop("excerpt")[:300]
        citation["human_revised"] = citation.pop("representation_kind") == "manual"
        citation["location_action"] = {"source_id": citation["source_id"], "evidence_id": citation["evidence_id"]}
        return citation
