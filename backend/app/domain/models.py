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


# 分类体系：领域（多选、可空）× 体裁（单选、可空），后端为唯一来源（GET /taxonomy 下发）。
TAXONOMY_DOMAINS = (
    {"value": "technical", "label": "技术"},
    {"value": "business", "label": "商业"},
    {"value": "education", "label": "教育"},
    {"value": "news", "label": "资讯"},
    {"value": "entertainment", "label": "娱乐"},
    {"value": "life", "label": "生活"},
    {"value": "other", "label": "其他"},
)
TAXONOMY_GENRES = (
    {"value": "document", "label": "文档"},
    {"value": "lecture", "label": "讲解"},
    {"value": "interview", "label": "访谈"},
    {"value": "podcast", "label": "播客"},
    {"value": "review", "label": "评测"},
    {"value": "recording", "label": "记录"},
    {"value": "other", "label": "其他"},
)
TAXONOMY_DOMAIN_VALUES = tuple(item["value"] for item in TAXONOMY_DOMAINS)
TAXONOMY_GENRE_VALUES = tuple(item["value"] for item in TAXONOMY_GENRES)
RELATION_TYPES = ("new_version_of", "revision_of", "related_to", "user_declared_same_work")

# 旧固定分类拆分映射（数据库迁移与旧归档规范化共用）：前四项归领域，后三项归体裁。
_LEGACY_CATEGORY_DOMAINS = ("technical", "business", "education", "news")
_LEGACY_CATEGORY_GENRES = ("interview", "podcast", "document")


def split_legacy_categories(categories: list[str]) -> tuple[list[str], list[str]]:
    """旧固定分类 → (domains, genres)；未知值忽略，多体裁全部保留（≤1 规则不适用于迁移）。"""
    values = set(categories)
    return (
        sorted(values.intersection(_LEGACY_CATEGORY_DOMAINS)),
        sorted(values.intersection(_LEGACY_CATEGORY_GENRES)),
    )


def validate_taxonomy_domains(value: list[str]) -> list[str]:
    invalid = sorted(set(value) - set(TAXONOMY_DOMAIN_VALUES))
    if invalid:
        raise ValueError(f"不支持的领域: {', '.join(invalid)}")
    return sorted(set(value))


def validate_taxonomy_genres(value: list[str]) -> list[str]:
    invalid = sorted(set(value) - set(TAXONOMY_GENRE_VALUES))
    if invalid:
        raise ValueError(f"不支持的体裁: {', '.join(invalid)}")
    if len(set(value)) > 1:
        raise ValueError("体裁最多选择一项")
    return sorted(set(value))


class PasteImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=10 * 1024 * 1024)
    rights: RightsCategory
    language: str = Field(default="zh", max_length=32)
    author: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)
    source_date: date | None = None
    domains: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("domains")
    @classmethod
    def valid_domains(cls, value: list[str]) -> list[str]:
        return validate_taxonomy_domains(value)

    @field_validator("genres")
    @classmethod
    def valid_genres(cls, value: list[str]) -> list[str]:
        return validate_taxonomy_genres(value)


class SourceMetadataUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    language: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)
    source_date: date | None = None
    domains: list[str] | None = None
    genres: list[str] | None = None
    tags: list[str] | None = None

    @model_validator(mode="after")
    def required_fields_are_not_cleared(self) -> "SourceMetadataUpdate":
        non_nullable = {"title", "language", "domains", "genres", "tags"}
        cleared = non_nullable.intersection(self.model_fields_set)
        if any(getattr(self, field) is None for field in cleared):
            raise ValueError("标题、语言、领域、体裁和标签不能设为 null；请提交有效值或省略字段")
        return self

    @field_validator("domains")
    @classmethod
    def valid_domains(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return validate_taxonomy_domains(value)

    @field_validator("genres")
    @classmethod
    def valid_genres(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return validate_taxonomy_genres(value)


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


class TopicRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SettingsUpdate(BaseModel):
    parser_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    parser_no_progress_seconds: int | None = Field(default=None, ge=60, le=86_400)
    parser_memory_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    parser_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    video_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    video_memory_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    video_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    video_max_frames: int | None = Field(default=None, ge=1, le=32)
    image_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    image_memory_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    image_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
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


# 媒体 AI 提供方：off 关闭 / openai_compatible 走 OpenAI 兼容端点（litellm 通道）。
AI_PROVIDER_VALUES = ("off", "openai_compatible")


def validate_ai_base_url(value: str) -> None:
    """AI 服务端点校验：空串表示使用提供方默认端点；非空必须是 ≤2048 的 HTTPS 公网地址。

    与 validate_download_url 同一脱敏纪律：拒绝消息不含 URL 内容。
    """
    if not value:
        return
    if len(value) > 2048:
        raise ValueError("invalid_base_url")
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        raise ValueError("invalid_base_url") from None
    if parsed.scheme.lower() != "https" or not host:
        raise ValueError("invalid_base_url")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("invalid_base_url")
    if _host_is_reserved(host):
        raise ValueError("invalid_base_url")


def _valid_ai_base_url(value: str | None) -> str | None:
    if value is None:
        return value
    stripped = value.strip()
    validate_ai_base_url(stripped)
    return stripped


def _valid_ai_provider(value: str | None) -> str | None:
    if value is not None and value not in AI_PROVIDER_VALUES:
        raise ValueError("不支持的 AI 提供方")
    return value


class AiTranscribeSettings(BaseModel):
    """语音转写分组；字段缺省表示保持不变（api_key 空串不触碰既有凭据）。"""

    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)

    _provider = field_validator("provider")(_valid_ai_provider)
    _base_url = field_validator("base_url")(_valid_ai_base_url)


class AiUnderstandSettings(BaseModel):
    """理解与摘要分组：纯文本 chat 模型（用户裁定：不需要视觉模型，v1.5）。"""

    provider: str | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    chat_model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=500)

    _provider = field_validator("provider")(_valid_ai_provider)
    _base_url = field_validator("base_url")(_valid_ai_base_url)


class AiTranscriberSettings(BaseModel):
    """转写路径策略（REQ-054）；字段缺省表示保持不变。"""

    engine: str | None = None
    local_stt_model: str | None = None
    stt_timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    stt_memory_limit_mb: int | None = Field(default=None, ge=64, le=32_768)
    stt_disk_limit_mb: int | None = Field(default=None, ge=64, le=32_768)

    @field_validator("engine")
    @classmethod
    def valid_engine(cls, value: str | None) -> str | None:
        if value is not None and value not in ("auto", "local", "api"):
            raise ValueError("不支持的转写路径策略")
        return value

    @field_validator("local_stt_model")
    @classmethod
    def valid_local_model(cls, value: str | None) -> str | None:
        if value is not None and value not in ("paraformer-zh", "paraformer-zh-quant"):
            raise ValueError("不支持的本地转写模型")
        return value


class AiVideoSettings(BaseModel):
    """视频直送与自备中转配置（REQ-055，决策 17/20/21/22）；字段缺省表示保持不变。"""

    provider: str | None = None
    model: str | None = Field(default=None, max_length=100)
    max_bytes: int | None = Field(default=None, ge=1_048_576, le=536_870_912)
    reencode: str | None = None
    chunk_seconds: int | None = Field(default=None, ge=60, le=3600)
    relay_base_url: str | None = Field(default=None, max_length=2048)
    relay_kind: str | None = None
    cos_bucket: str | None = Field(default=None, max_length=200)
    cos_region: str | None = Field(default=None, max_length=64)
    qwen_api_key: str | None = Field(default=None, max_length=500)
    mimo_api_key: str | None = Field(default=None, max_length=500)
    relay_secret: str | None = Field(default=None, max_length=500)
    cos_secret_id: str | None = Field(default=None, max_length=500)
    cos_secret_key: str | None = Field(default=None, max_length=500)

    @field_validator("provider")
    @classmethod
    def valid_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in ("off", "qwen", "mimo"):
            raise ValueError("不支持的视频直送供应商")
        return value

    @field_validator("reencode")
    @classmethod
    def valid_reencode(cls, value: str | None) -> str | None:
        if value is not None and value not in ("on", "off"):
            raise ValueError("不支持的重编码开关")
        return value

    @field_validator("relay_kind")
    @classmethod
    def valid_relay_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in ("off", "http", "cos"):
            raise ValueError("不支持的中转形态")
        return value

    @field_validator("relay_base_url")
    @classmethod
    def valid_relay_base_url(cls, value: str | None) -> str | None:
        if value:
            return _valid_ai_base_url(value)
        return value


class AiSettingsUpdate(BaseModel):
    transcribe: AiTranscribeSettings | None = None
    understand: AiUnderstandSettings | None = None
    transcriber: AiTranscriberSettings | None = None
    video: AiVideoSettings | None = None
    timeout_seconds: int | None = Field(default=None, ge=60, le=86_400)
    auto_pipeline: bool | None = None


class AiConnectionTestRequest(BaseModel):
    part: str

    @field_validator("part")
    @classmethod
    def valid_part(cls, value: str) -> str:
        if value not in ("transcribe", "understand"):
            raise ValueError("不支持的测试分组")
        return value


class SttModelActionRequest(BaseModel):
    """本地转写模型管理操作（REQ-054.3）：download 经作业异步执行，delete 同步幂等。"""

    action: str

    @field_validator("action")
    @classmethod
    def valid_action(cls, value: str) -> str:
        if value not in ("download", "delete"):
            raise ValueError("不支持的模型操作")
        return value


class JobsDeleteRequest(BaseModel):
    """作业批量删除：运行中的作业不得删除（端点侧拒绝）。"""

    job_ids: list[str] = Field(min_length=1, max_length=200)

    @field_validator("job_ids")
    @classmethod
    def valid_job_ids(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item for item in value if isinstance(item, str) and item.strip()))
        if not cleaned:
            raise ValueError("作业 id 列表不能为空")
        return cleaned


class VideoSummarizeRequest(BaseModel):
    force_tier2: bool = False


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
    domains: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("domains")
    @classmethod
    def valid_domains(cls, value: list[str]) -> list[str]:
        return validate_taxonomy_domains(value)

    @field_validator("genres")
    @classmethod
    def valid_genres(cls, value: list[str]) -> list[str]:
        return validate_taxonomy_genres(value)


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
