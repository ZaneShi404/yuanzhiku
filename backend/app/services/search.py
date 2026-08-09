"""Literal Chinese substring/phrase search. This intentionally makes no semantic claim."""

from __future__ import annotations

import json
from datetime import date

from app.ports.repository import RepositoryPort


class SearchService:
    def __init__(self, repository: RepositoryPort) -> None:
        self.repository = repository

    def search(
        self,
        query: str,
        *,
        include_historical: bool = False,
        include_incomplete: bool = False,
        source_type: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        language: str | None = None,
        processing_state: str | None = None,
        source_date_from: date | None = None,
        source_date_to: date | None = None,
        imported_at_from: date | None = None,
        imported_at_to: date | None = None,
        sort: str = "relevance",
    ) -> list[dict]:
        if sort not in {"relevance", "updated", "title"}:
            raise ValueError("不支持的排序方式")
        if source_date_from and source_date_to and source_date_from > source_date_to:
            raise ValueError("来源日期起始值不能晚于结束值")
        if imported_at_from and imported_at_to and imported_at_from > imported_at_to:
            raise ValueError("导入日期起始值不能晚于结束值")
        needle = query.strip().casefold()
        candidates: list[dict] = []
        for source in self.repository.list_sources():
            if source_type and source["source_type"] != source_type:
                continue
            if category and category not in json.loads(source["categories_json"]):
                continue
            if tag and tag not in json.loads(source["tags_json"]):
                continue
            if author and author.casefold() not in (source["author"] or "").casefold():
                continue
            if language and source["language"] != language:
                continue
            if processing_state and source["processing_state"] != processing_state:
                continue
            source_date_value = date.fromisoformat(source["source_date"]) if source.get("source_date") else None
            imported_date_value = date.fromisoformat(source["imported_at"][:10])
            if source_date_from and (source_date_value is None or source_date_value < source_date_from):
                continue
            if source_date_to and (source_date_value is None or source_date_value > source_date_to):
                continue
            if imported_at_from and imported_date_value < imported_at_from:
                continue
            if imported_at_to and imported_date_value > imported_at_to:
                continue
            versions = self.repository.versions_for_source(source["id"])
            if not include_historical:
                versions = versions[:1]
            text_parts = [source["title"], source["author"] or "", source["notes"] or "", " ".join(json.loads(source["tags_json"])), " ".join(json.loads(source["categories_json"]))]
            for version in versions:
                if not include_incomplete and version["completeness"] != "complete":
                    continue
                for representation in self.repository.representations_for_version(version["id"]):
                    text_parts.append(representation["text_content"])
            haystack = "\n".join(text_parts)
            relevance = haystack.casefold().count(needle) if needle else 1
            if relevance:
                candidates.append({"kind": "source", "id": source["id"], "title": source["title"], "source_type": source["source_type"], "processing_state": source["processing_state"], "source_date": source.get("source_date"), "imported_at": source["imported_at"], "relevance": relevance, "updated_at": source["updated_at"]})
        for item in self.repository.list_knowledge(published_only=True):
            relevance = item["statement"].casefold().count(needle) if needle else 1
            if relevance:
                candidates.append({"kind": "knowledge", "id": item["id"], "title": item["statement"][:120], "knowledge_type": item["kind"], "relevance": relevance, "updated_at": item["published_at"]})
        for card in self.repository.list_external_cards():
            text = " ".join([card["title"], card["author"] or "", card["notes"] or "", " ".join(json.loads(card["tags_json"]))])
            relevance = text.casefold().count(needle) if needle else 1
            if relevance:
                candidates.append({"kind": "external_card", "id": card["id"], "title": card["title"], "card_type": card["card_type"], "relevance": relevance, "updated_at": card["created_at"]})
        if sort == "title":
            return sorted(candidates, key=lambda item: (item["title"].casefold(), item["updated_at"] or ""))
        if sort == "updated":
            return sorted(candidates, key=lambda item: (item["updated_at"] or "", item["title"].casefold()), reverse=True)
        # Relevance first, then newest import/update, then title. ISO-8601 timestamps sort chronologically when reversed.
        return sorted(sorted(candidates, key=lambda item: item["title"].casefold()), key=lambda item: (item["relevance"], item["updated_at"] or ""), reverse=True)
