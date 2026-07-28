"""Local metadata-only external cards. This module intentionally has no HTTP client."""

from __future__ import annotations

from urllib.parse import urlparse

from app.adapters.sqlite import SqliteRepository
from app.domain.models import DouyinCardCreate, ExternalCardCreate


class ExternalCardService:
    def __init__(self, repository: SqliteRepository) -> None:
        self.repository = repository

    @staticmethod
    def _valid_general_url(value: str) -> bool:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _valid_douyin_url(value: str) -> bool:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and (host == "douyin.com" or host.endswith(".douyin.com"))

    def create(self, request: ExternalCardCreate) -> dict:
        if not self._valid_general_url(request.url):
            raise ValueError("URL 必须是完整 HTTP 或 HTTPS 地址")
        return self.repository.create_external_card("general", request.url, request.title, request.author, request.notes, request.tags)

    def create_douyin(self, request: DouyinCardCreate) -> dict:
        if not self._valid_douyin_url(request.url):
            raise ValueError("抖音卡仅接受用户输入的 HTTPS douyin.com 或其子域 URL")
        # Stored literally; do not normalize, resolve, fetch, preview, or redirect-follow.
        return self.repository.create_external_card("douyin", request.url, request.title, request.author, request.notes, request.tags)
