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
        domains: list[str] | None = None,
        genre: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        language: str | None = None,
        processing_state: str | None = None,
        source_date_from: date | None = None,
        source_date_to: date | None = None,
        imported_at_from: date | None = None,
        imported_at_to: date | None = None,
        topic_id: str | None = None,
        sort: str = "relevance",
    ) -> list[dict]:
        if sort not in {"relevance", "updated", "title"}:
            raise ValueError("不支持的排序方式")
        if source_date_from and source_date_to and source_date_from > source_date_to:
            raise ValueError("来源日期起始值不能晚于结束值")
        if imported_at_from and imported_at_to and imported_at_from > imported_at_to:
            raise ValueError("导入日期起始值不能晚于结束值")
        needle = query.strip().casefold()
        # topic_id 只过滤来源分支：主题不存在或为空时来源零命中，知识与外部卡分支不受影响。
        topic_source_ids = self.repository.source_ids_for_topic(topic_id) if topic_id else None
        candidates: list[dict] = []
        for source in self.repository.list_sources():
            if topic_source_ids is not None and source["id"] not in topic_source_ids:
                continue
            if source_type and source["source_type"] != source_type:
                continue
            source_domains = json.loads(source["domains_json"])
            source_genres = json.loads(source["genres_json"])
            # 领域过滤为 OR 语义；哨兵 "_none" 匹配未分类（空列表）来源。
            if domains:
                matched = bool(set(domains).intersection(source_domains)) or ("_none" in domains and not source_domains)
                if not matched:
                    continue
            if genre:
                if genre == "_none":
                    if source_genres:
                        continue
                elif genre not in source_genres:
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
            # 全文语料只含正文类文本：分类（领域/体裁/标签）token 不进入检索。
            text_parts = [source["title"], source["author"] or "", source["notes"] or ""]
            for version in versions:
                if not include_incomplete and version["completeness"] != "complete":
                    continue
                for representation in self.repository.representations_for_version(version["id"]):
                    if representation["parser_name"] == "ffmpeg-local":
                        # 视频容器元数据模板是纯噪声，退出全文检索；图片元数据（pillow-local）保留。
                        continue
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
