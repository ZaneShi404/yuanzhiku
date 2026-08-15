"""领域 DTO；不依赖 FastAPI、SQLite 或文件系统。"""

from __future__ import annotations

import ipaddress
from datetime import date, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class RightsCategory(str, Enum):
    OWNED = "owned"
    AUTHORIZED = "authorized"
    PERMITTED = "permitted"
    OPEN_LICENSE = "open_license"
    OTHER = "other"


class SourceType(str, Enum):
    FILE = "file"
    PASTE = "paste"
    EXTERNAL = "external"
    DOUYIN = "douyin"
    VIDEO_LINK = "video_link"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class KnowledgeType(str, Enum):
    FACT = "fact"
    OPINION = "opinion"
    INSTRUCTION = "instruction"
    CASE = "case"
    CITATION = "citation"
    UNVERIFIED = "unverified"


FIXED_CATEGORIES = (
    "technical", "business", "education", "news", "interview", "podcast", "document"
)
RELATION_TYPES = ("new_version_of", "revision_of", "related_to", "user_declared_same_work")


class PasteImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=10 * 1024 * 1024)
    rights: RightsCategory
    language: str = Field(default="zh", max_length=32)
    author: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)
    source_date: date | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, value: list[str]) -> list[str]:
        invalid = sorted(set(value) - set(FIXED_CATEGORIES))
        if invalid:
            raise ValueError(f"不支持的固定分类: {', '.join(invalid)}")
        return sorted(set(value))


class SourceMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    language: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)
    source_date: date | None = None
    categories: list[str] | None = None
    tags: list[str] | None = None

    @model_validator(mode="after")
    def required_fields_are_not_cleared(self) -> "SourceMetadataUpdate":
        non_nullable = {"title", "language", "categories", "tags"}
        cleared = non_nullable.intersection(self.model_fields_set)
        if any(getattr(self, field) is None for field in cleared):
            raise ValueError("标题、语言、分类和标签不能设为 null；请提交有效值或省略字段")
        return self

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        invalid = sorted(set(value) - set(FIXED_CATEGORIES))
        if invalid:
            raise ValueError(f"不支持的固定分类: {', '.join(invalid)}")
        return sorted(set(value))


class RelationCreate(BaseModel):
    related_source_id: str
    relation_type: str

    @field_validator("relation_type")
    @classmethod
    def valid_relation(cls, value: str) -> str:
        if value not in RELATION_TYPES:
            raise ValueError("不支持的来源关系")
        return value


class KnowledgeCreate(BaseModel):
    kind: KnowledgeType
    statement: str = Field(min_length=1, max_length=20_000)
    evidence_ids: list[str] = Field(default_factory=list)


class ManualRepresentationCreate(BaseModel):
    text: str = Field(min_length=1, max_length=10 * 1024 * 1024)
    note: str | None = Field(default=None, max_length=1000)


class ExternalCardCreate(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    title: str = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list)


class DouyinCardCreate(ExternalCardCreate):
    pass


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(default_factory=list)


class SettingsUpdate(BaseModel):
    parser_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    parser_no_progress_seconds: int | None = Field(default=None, ge=60, le=86_400)
    parser_memory_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    parser_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    video_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    video_memory_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    video_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    video_max_frames: int | None = Field(default=None, ge=1, le=32)
    job_lease_seconds: int | None = Field(default=None, ge=60, le=86_400)
    max_retry_attempts: int | None = Field(default=None, ge=0, le=10)
    download_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    download_no_progress_seconds: int | None = Field(default=None, ge=10, le=86_400)
    download_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)


# URL 层校验白名单（与出站注册域清单两层独立控制）：主域或子域匹配。
DOWNLOAD_URL_HOSTS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com",),
}

DOWNLOAD_PLATFORM_VALUES = tuple(DOWNLOAD_URL_HOSTS)


def _host_is_reserved(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # 非公网单播一律拒绝：覆盖回环/私网/链路本地/保留段，以及 100.64.0.0/10
    # （CGNAT 共享段——Python 3.13 下 is_private/is_reserved 对该段全为 False）。
    return not address.is_global


def validate_download_url(url: str, platform: str) -> None:
    """Reject links outside the HTTPS whitelist for the selected platform.

    Raises ValueError with a generic (URL-free) message; the API maps it to the
    stable ``invalid_url`` code. 消息绝不包含 URL 内容。
    """
    if not isinstance(url, str) or not 1 <= len(url) <= 4096:
        raise ValueError("invalid_url")
    if platform not in DOWNLOAD_PLATFORM_VALUES:
        raise ValueError("invalid_platform")
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        raise ValueError("invalid_url") from None
    if parsed.scheme.lower() != "https" or not host:
        raise ValueError("invalid_url")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("invalid_url")
    if _host_is_reserved(host):
        raise ValueError("invalid_url")
    allowed = DOWNLOAD_URL_HOSTS[platform]
    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise ValueError("invalid_url")


def sanitize_download_url(value: str) -> str:
    """脱敏变换：scheme://host/path，去 userinfo/query/fragment，截断 4096。

    Port (if any) is retained as part of the authority so the download targets
    exactly the submitted endpoint; IPv6 brackets are preserved verbatim.
    """
    parsed = urlsplit(value)
    authority = parsed.netloc.rsplit("@", 1)[-1]
    path = parsed.path or "/"
    sanitized = urlunsplit((parsed.scheme, authority, path, "", ""))
    return sanitized[:4096]


class DownloadLinkRequest(BaseModel):
    url: str = Field(min_length=1)
    platform: str = Field(min_length=1, max_length=32)
    rights: RightsCategory
    use_cookie: bool = False
    title: str = Field(default="", max_length=500)
    author: str | None = Field(default=None, max_length=300)
    language: str = Field(default="zh", max_length=32)
    notes: str | None = Field(default=None, max_length=4000)
    source_date: date | None = None
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, value: list[str]) -> list[str]:
        invalid = sorted(set(value) - set(FIXED_CATEGORIES))
        if invalid:
            raise ValueError(f"不支持的固定分类: {', '.join(invalid)}")
        return sorted(set(value))


class LinkProbeRequest(BaseModel):
    """REQ-047b 链接元数据探测请求：只读子能力，不含 rights 等落库字段。"""

    url: str = Field(min_length=1, max_length=4096)
    platform: str = Field(min_length=1, max_length=32)
    use_cookie: bool = False


class ExportCreate(BaseModel):
    confirmed: bool


class RestoreRequest(BaseModel):
    target_data_root: str = Field(min_length=1, max_length=1000)
    target_database_url: str | None = Field(default=None, min_length=1, max_length=4096)


class ReimportRequest(BaseModel):
    archive_path: str = Field(min_length=1, max_length=1000)


class VerifyRequest(BaseModel):
    full: bool = False
    sample_size: int = Field(default=10, ge=1, le=10_000)


class ApiEnvelope(BaseModel):
    id: str
    created_at: datetime
    data: dict[str, Any]
