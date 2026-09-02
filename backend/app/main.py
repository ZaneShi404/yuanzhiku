"""FastAPI composition root for the loopback-only local application."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.adapters.sqlite import SqliteRepository
from app.adapters.storage import ArtifactStore, StorageLimitError
from app.adapters.parsers import LocalDocumentParser
from app.core.permissions import secure_private_file
from app.adapters.downloader import (
    DOWNLOAD_PLATFORMS,
    DOWNLOAD_REGISTRY,
    YtDlpDownloader,
    host_matches_registered_domain,
)
from app.adapters.media import LocalFfmpegMediaAnalyzer
from app.adapters.media_ai import ApiTranscriber, ConfiguredMediaAi
from app.adapters.local_stt import LocalFunasrTranscriber
from app.adapters.video_ai import MiMoVideoAdapter, QwenVideoAdapter, RelayClient
from app.core.config import DataPaths, InstanceLock, data_paths, database_backend, database_url
from app.core.operations import OperationalLog
from app.ports.media import DownloadInputInvalid, DownloadUnavailable
from app.ports.repository import RepositoryPort
from app.domain.models import (
    AiConnectionTestRequest,
    AiSettingsUpdate,
    DownloadLinkRequest,
    DouyinCardCreate,
    ExportCreate,
    ExternalCardCreate,
    JobsDeleteRequest,
    KnowledgeCreate,
    LinkProbeRequest,
    ManualRepresentationCreate,
    PasteImportRequest,
    RelationCreate,
    RestoreRequest,
    ReimportRequest,
    RightsCategory,
    SettingsUpdate,
    SourceMetadataUpdate,
    SttModelActionRequest,
    TopicCreate,
    TopicRename,
    VerifyRequest,
    VideoSummarizeRequest,
    TAXONOMY_DOMAIN_VALUES,
    TAXONOMY_DOMAINS,
    TAXONOMY_GENRE_VALUES,
    TAXONOMY_GENRES,
    sanitize_download_url,
    validate_download_url,
)
from app.services.documents import DocumentService
from app.services.external_cards import ExternalCardService
from app.services.ai_credentials import CredentialStoreCorrupt, read_ai_credentials, write_ai_credentials
from app.services.images import ImageService
from app.services.imports import ImportService
from app.services.jobs import JobService
from app.services.prefill import ALLOWED_SUFFIXES as PREFILL_SUFFIXES
from app.services.prefill import suggest_document, suggest_text
from app.services.lifecycle import LifecycleService
from app.services.stt_models import SttModelManager
from app.services.videos import VideoService
from app.services.search import SearchService
from app.services.transfers import ReimportConflict, TransferService


class ApplicationServices:
    def __init__(self, paths: DataPaths) -> None:
        paths.create()
        self.paths = paths
        self.operations = OperationalLog(paths.logs)
        self.operations.prune()
        _migrate_legacy_download_cookie(paths, self.operations)
        _secure_existing_private_files(paths, self.operations)
        selected_database_url = database_url(paths)
        selected_backend = database_backend(selected_database_url)
        if selected_backend == "postgresql":
            from app.adapters.postgres import PostgresRepository

            migrations = Path(__file__).resolve().parents[1] / "migrations" / "postgresql"
            self.repository: RepositoryPort = PostgresRepository(selected_database_url, migrations)
        else:
            self.repository = SqliteRepository(paths.database)
        self.repository.initialize()
        self.repository.prune_source_permanent_delete_audit_events()
        self.database_backend = self.repository.backend
        self.artifacts = ArtifactStore(paths)
        self.parser = LocalDocumentParser(paths.models, Path(__file__).resolve().parents[1] / "models.lock.json")
        self.media_analyzer = LocalFfmpegMediaAnalyzer()
        # 单例即可：适配器每次调用惰性读取设置与凭据，改配置即时生效；
        # 双组全关时 capability().enabled=False，作业保持 blocked 语义。
        self.media_ai = ConfiguredMediaAi(
            settings_getter=self.repository.get_settings,
            credentials_reader=lambda: read_ai_credentials(paths.ai_credentials_file),
            staging_dir=paths.staging,
        )
        # 转写双路径（REQ-054）：本地 FunASR（默认）与远程转写端点同端口；
        # 模型经 SttModelManager 显式下载管理（锁文件 + 哈希校验），
        # 路径策略由 jobs._video_transcribe 按 ai_transcriber_engine 决定。
        self.stt_manager = SttModelManager(paths.models)
        self.local_transcriber = LocalFunasrTranscriber(self.stt_manager, self.repository.get_settings)
        self.api_transcriber = ApiTranscriber(
            self.repository.get_settings,
            lambda: read_ai_credentials(paths.ai_credentials_file),
        )
        # 与 JobService 共享同一字典引用：测试与作业侧替换互见。
        self.transcribers: dict[str, Any] = {"local": self.local_transcriber, "api": self.api_transcriber}
        # 视频直送双适配（REQ-055，决策 17/22）：relay 优先路径共用同一客户端。
        self.video_relay = RelayClient(
            self.repository.get_settings,
            lambda: read_ai_credentials(paths.ai_credentials_file),
        )
        self.video_adapters: dict[str, Any] = {
            "qwen": QwenVideoAdapter(
                self.repository.get_settings,
                lambda: read_ai_credentials(paths.ai_credentials_file),
                self.video_relay,
            ),
            "mimo": MiMoVideoAdapter(
                self.repository.get_settings,
                lambda: read_ai_credentials(paths.ai_credentials_file),
                self.video_relay,
            ),
        }
        self.downloader = YtDlpDownloader(cookie_resolver=paths.download_cookie_file)
        self.documents = DocumentService(self.repository)
        self.videos = VideoService(self.repository, self.artifacts, self.documents, self.media_analyzer)
        self.images = ImageService(self.repository, self.artifacts, self.documents)
        self.transfers = TransferService(paths, self.repository, self.artifacts)
        self.imports = ImportService(self.repository, self.artifacts)
        self.jobs = JobService(
            self.repository,
            self.artifacts,
            self.documents,
            self.transfers.create_backup,
            parser=self.parser,
            integrity_runner=lambda sample_size: self.transfers.verify_artifacts(False, sample_size),
            videos=self.videos,
            imports=self.imports,
            downloader=self.downloader,
            images=self.images,
            media_ai=self.media_ai,
            stt_manager=self.stt_manager,
            transcribers=self.transcribers,
            video_adapter_provider=lambda: self.video_adapters.get(
                self.repository.get_settings().get("ai_video_provider", "off")
            ),
        )
        self.external_cards = ExternalCardService(self.repository)
        self.lifecycle = LifecycleService(self.repository, self.artifacts)
        self.search = SearchService(self.repository)


def _migrate_legacy_download_cookie(paths: DataPaths, operations: OperationalLog) -> None:
    """遗留单文件 cookies.txt → 按平台 Cookie 库分拣（REQ-047a 修订）。

    逐行解析 Netscape 格式：跳过注释/空行，``\\t`` 分隔取首列域名（兼容
    ``#HttpOnly_`` 前缀条目），按 DOWNLOAD_REGISTRY 做标签边界子域匹配分拣到
    ``cookies/<platform>.txt``（每个文件保留原注释头 + 匹配行；同一行只进一个
    平台文件；无法匹配任何平台的行不进任何文件）。两个平台都没有匹配条目则
    不建空文件；分拣完成后删除旧 cookies.txt。行内容绝不打印/落日志；迁移
    失败不阻断启动（记操作日志事件）。
    """
    legacy = paths.download / "cookies.txt"
    if not legacy.exists():
        return
    try:
        header_lines: list[str] = []
        matched: dict[str, list[str]] = {platform: [] for platform in DOWNLOAD_REGISTRY}
        for line in legacy.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#HttpOnly_"):
                entry = stripped[len("#HttpOnly_"):]
            elif stripped.startswith("#"):
                header_lines.append(line)
                continue
            else:
                entry = stripped
            host = entry.split("\t", 1)[0].lstrip(".")
            for platform, domains in DOWNLOAD_REGISTRY.items():
                if host_matches_registered_domain(host, domains):
                    matched[platform].append(line)
                    break
        for platform, lines in matched.items():
            if not lines:
                continue
            content = "\n".join(header_lines + lines) + "\n"
            (paths.download_cookies / f"{platform}.txt").write_text(content, encoding="utf-8")
            secure_private_file(paths.download_cookies / f"{platform}.txt")
        legacy.unlink()
        operations.write("legacy_cookie_migration", "succeeded")
    except Exception:
        # 迁移失败不阻断启动；事件不含任何行内容。
        operations.write("legacy_cookie_migration", "failed")


def _secure_existing_private_files(paths: DataPaths, operations: OperationalLog) -> None:
    """启动时对既有凭据与按平台 Cookie 文件补设私密权限（加固计划 Task 1）。

    尽力而为：失败不阻断启动，仅记操作日志事件（不含路径内容与文件内容）。
    """
    targets = [paths.ai_credentials_file]
    targets.extend(path for path in paths.download_cookies.glob("*.txt") if path.is_file())
    try:
        for target in targets:
            if target.exists():
                secure_private_file(target)
    except Exception:
        operations.write("secret_permission_retrofit", "failed")
        return
    operations.write("secret_permission_retrofit", "succeeded")


def _source_view(source: dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        raise HTTPException(status_code=404, detail="来源不存在")
    value = dict(source)
    value["domains"] = json.loads(value.pop("domains_json"))
    value["genres"] = json.loads(value.pop("genres_json"))
    value["tags"] = json.loads(value.pop("tags_json"))
    return value


def _external_view(card: dict[str, Any]) -> dict[str, Any]:
    value = dict(card)
    value["tags"] = json.loads(value.pop("tags_json"))
    return value


def _evidence_view(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if evidence is None:
        raise HTTPException(status_code=404, detail="证据不存在")
    value = dict(evidence)
    value["locator"] = json.loads(value.pop("locator_json"))
    value["is_validated"] = bool(value["is_validated"])
    return value


def _ai_key_hint(value: str | None) -> str | None:
    """凭据提示只保留末四位；完整密钥绝不出现在任何响应里。"""
    return f"…{value[-4:]}" if value else None


def _video_input_capability(services: "ApplicationServices") -> dict[str, Any]:
    """按 ai_video_provider 回显所选视频直送适配器能力（REQ-055.5）。"""
    provider = services.repository.get_settings().get("ai_video_provider", "off")
    adapter = services.video_adapters.get(provider)
    if adapter is None:
        return {"video_input": False, "provider": provider}
    return adapter.capability()


def _ai_settings_view(services: "ApplicationServices") -> dict[str, Any]:
    settings = services.repository.get_settings()
    credentials = read_ai_credentials(services.paths.ai_credentials_file)
    try:
        timeout_seconds = int(settings.get("ai_timeout_seconds", "300"))
    except (TypeError, ValueError):
        timeout_seconds = 300
    try:
        stt_timeout = int(settings.get("stt_timeout_seconds", "3600"))
    except (TypeError, ValueError):
        stt_timeout = 3600
    try:
        stt_memory = int(settings.get("stt_memory_limit_mb", "2048"))
    except (TypeError, ValueError):
        stt_memory = 2048
    try:
        stt_disk = int(settings.get("stt_disk_limit_mb", "1024"))
    except (TypeError, ValueError):
        stt_disk = 1024
    try:
        video_max_bytes = int(settings.get("ai_video_max_bytes", "314572800"))
    except (TypeError, ValueError):
        video_max_bytes = 314572800
    try:
        video_chunk_seconds = int(settings.get("ai_video_chunk_seconds", "600"))
    except (TypeError, ValueError):
        video_chunk_seconds = 600
    return {
        "transcribe": {
            "provider": settings.get("ai_transcribe_provider", "off"),
            "base_url": settings.get("ai_transcribe_base_url", ""),
            "model": settings.get("ai_transcribe_model", ""),
            "has_key": bool(credentials.get("transcribe")),
            "key_hint": _ai_key_hint(credentials.get("transcribe")),
        },
        "understand": {
            "provider": settings.get("ai_understand_provider", "off"),
            "base_url": settings.get("ai_understand_base_url", ""),
            "chat_model": settings.get("ai_chat_model", ""),
            "has_key": bool(credentials.get("understand")),
            "key_hint": _ai_key_hint(credentials.get("understand")),
        },
        "transcriber": {
            "engine": settings.get("ai_transcriber_engine", "auto"),
            "local_stt_model": settings.get("ai_local_stt_model", "paraformer-zh"),
            "stt_timeout_seconds": stt_timeout,
            "stt_memory_limit_mb": stt_memory,
            "stt_disk_limit_mb": stt_disk,
        },
        "video": {
            "provider": settings.get("ai_video_provider", "off"),
            "model": settings.get("ai_video_model", ""),
            "max_bytes": video_max_bytes,
            "reencode": settings.get("ai_video_reencode", "on") == "on",
            "chunk_seconds": video_chunk_seconds,
            "qwen": {"has_key": bool(credentials.get("video_qwen")), "key_hint": _ai_key_hint(credentials.get("video_qwen"))},
            "mimo": {"has_key": bool(credentials.get("video_mimo")), "key_hint": _ai_key_hint(credentials.get("video_mimo"))},
            "relay": {
                "kind": settings.get("ai_video_relay_kind", "http"),
                "base_url": settings.get("ai_video_relay_base_url", ""),
                "has_secret": bool(credentials.get("video_relay")),
                "secret_hint": _ai_key_hint(credentials.get("video_relay")),
                "cos_bucket": settings.get("ai_video_cos_bucket", ""),
                "cos_region": settings.get("ai_video_cos_region", "ap-shanghai"),
                "cos_has_key": bool(credentials.get("video_cos_secret_id") and credentials.get("video_cos_secret_key")),
                "cos_key_hint": _ai_key_hint(credentials.get("video_cos_secret_id")),
            },
        },
        "local_stt": services.stt_manager.status(),
        "timeout_seconds": timeout_seconds,
        "auto_pipeline": settings.get("ai_auto_pipeline", "on") == "on",
    }


def _apply_ai_group(
    group: Any,
    prefix: str,
    credential_group: str,
    model_fields: dict[str, str],
    settings: dict[str, str],
    updates: dict[str, str],
    credentials: dict[str, str],
) -> bool:
    """应用单个 AI 分组的非密钥字段与凭据变更；返回凭据是否被修改。"""
    changed = False
    if group.provider is not None:
        updates[f"ai_{prefix}_provider"] = group.provider
    if group.base_url is not None:
        updates[f"ai_{prefix}_base_url"] = group.base_url
    for field, setting_key in model_fields.items():
        value = getattr(group, field)
        if value is not None:
            updates[setting_key] = value
    effective_provider = group.provider if group.provider is not None else settings.get(f"ai_{prefix}_provider", "off")
    if effective_provider == "off":
        # 分组关闭即移除其凭据（off 即不再持有密钥）。
        changed = credentials.pop(credential_group, None) is not None
    elif group.api_key:
        credentials[credential_group] = group.api_key
        changed = True
    return changed


def create_app(root: str | Path | None = None, *, acquire_lock: bool = True) -> FastAPI:
    paths = data_paths(root)
    lock = InstanceLock(paths.lock_file) if acquire_lock else None
    if lock is not None:
        lock.acquire()
    try:
        services = ApplicationServices(paths)
    except Exception:
        if lock is not None:
            lock.release()
        raise

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        date_key = datetime.now(UTC).date().isoformat()
        settings = services.repository.get_settings()
        jobs = services.repository.list_jobs()
        if settings.get("last_backup_date") != date_key and not any(
            job["kind"] == "backup" and job["state"] in {"queued", "running", "retry_wait"} for job in jobs
        ):
            services.repository.create_job("backup", None, None, None, None, {"date": date_key}, priority=-100)
        if settings.get("last_integrity_sample_date") != date_key and not any(
            job["kind"] == "integrity_sample" and job["state"] in {"queued", "running", "retry_wait"} for job in jobs
        ):
            services.repository.create_job(
                "integrity_sample", None, None, None, None, {"date": date_key, "sample_size": 10}, priority=-200
            )
        embedded_worker = os.environ.get("YUANZHIKU_EMBEDDED_WORKER", "true").lower() == "true"
        stop = asyncio.Event()

        async def worker_loop() -> None:
            while not stop.is_set():
                await asyncio.to_thread(services.jobs.run_once)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass

        worker = asyncio.create_task(worker_loop(), name="yuanzhiku-single-worker") if embedded_worker else None
        services.operations.write("application_start", "succeeded")
        try:
            yield
        finally:
            stop.set()
            if worker is not None:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            services.operations.write("application_stop", "succeeded")
            if lock is not None:
                lock.release()

    app = FastAPI(
        title="源知库 API",
        version="0.1.0",
        description="单用户本地证据知识系统。所有接口仅供 loopback 本地 UI 使用。",
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/api-docs",
        redoc_url=None,
    )
    app.state.services = services

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("code"), str) and isinstance(detail.get("message"), str):
            payload = detail
        else:
            framework_messages = {
                "Not Found": "资源不存在",
                "Method Not Allowed": "请求方法不被允许",
            }
            payload = {"code": f"http_{exc.status_code}", "message": framework_messages.get(str(detail), str(detail))}
        return JSONResponse(status_code=exc.status_code, content={"detail": payload}, headers=exc.headers)

    @app.exception_handler(Exception)
    async def internal_error(_, __: Exception) -> JSONResponse:
        services.operations.write("internal_error", "failed")
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "internal_error", "message": "本地服务内部错误"}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "request_validation", "message": "请求字段无效"}},
        )

    @app.exception_handler(CredentialStoreCorrupt)
    async def credential_store_corrupt(_, __: CredentialStoreCorrupt) -> JSONResponse:
        # 凭据文件损坏（加固计划 Task 1）：明确 503，绝不覆盖或删除原文件。
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "credential_store_corrupt", "message": "凭据存储文件损坏，请手动恢复或删除该文件后重新配置"}},
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def upload_capacity_preflight(request, call_next):
        if request.method == "POST" and request.url.path in {"/api/v1/imports/file", "/api/v1/imports/image", "/api/v1/videos/local"}:
            content_length = request.headers.get("content-length")
            try:
                expected_bytes = int(content_length) if content_length is not None else None
            except ValueError:
                expected_bytes = None
            if expected_bytes is None or expected_bytes < 0:
                return JSONResponse(
                    status_code=411,
                    content={"detail": {"code": "content_length_required", "message": "文件导入必须提供有效的 Content-Length"}},
                )
            try:
                services.artifacts.check_capacity(expected_bytes)
            except StorageLimitError as exc:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"code": "storage_capacity", "message": str(exc)}},
                )
        return await call_next(request)

    @app.middleware("http")
    async def cookie_upload_length_preflight(request, call_next):
        # 按平台 Cookie 1MB 上限前移（REQ-047a 修订）：解析 multipart 前按
        # Content-Length 预检（1MB 文件 + 表单开销边界），超大请求立即 413 不落
        # 临时盘；端点内解析后的二次校验保留兜底。
        if request.method == "POST" and request.url.path.startswith("/api/v1/settings/download-cookies/"):
            content_length = request.headers.get("content-length")
            try:
                expected_bytes = int(content_length) if content_length is not None else None
            except ValueError:
                expected_bytes = None
            if expected_bytes is not None and expected_bytes > 1024 * 1024 + 64 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"code": "cookie_file_too_large", "message": "cookies.txt 超过 1MB 限制"}},
                )
        return await call_next(request)

    @app.middleware("http")
    async def minimal_request_audit(request, call_next):
        response = await call_next(request)
        # Only a stable route template and status are recorded; never body, query, content or paths.
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        services.operations.write(f"{request.method} {route_path}", str(response.status_code))
        return response

    def get_services() -> ApplicationServices:
        return services

    api = "/api/v1"

    @app.get(f"{api}/health", tags=["health"])
    def health(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return {"status": "ok", "data_root": str(svc.paths.root), "database": svc.database_backend, "network": "127.0.0.1 only"}

    @app.get(f"{api}/capabilities", tags=["health"])
    def capabilities(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return {
            "parser": svc.parser.capability(),
            "search": {"mode": "phrase_keyword_substring", "semantic": False},
            "external_cards": {"fetch": False, "douyin_literal_only": True},
            "media": {
                "local": svc.media_analyzer.capability(),
                "ai": {
                    **svc.media_ai.capability(),
                    "local_stt": svc.local_transcriber.capability(),
                    "video_input": _video_input_capability(svc),
                },
            },
            "downloader": svc.downloader.capability(),
            "network": {"bind": "127.0.0.1", "https": False, "telemetry": False},
        }

    @app.get(f"{api}/settings", tags=["settings"])
    def get_settings(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return svc.repository.get_settings()

    @app.put(f"{api}/settings", tags=["settings"])
    def put_settings(request: SettingsUpdate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return svc.repository.update_settings(request.model_dump(exclude_none=True))

    @app.get(f"{api}/settings/ai", tags=["settings"])
    def get_ai_settings(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return _ai_settings_view(svc)

    @app.put(f"{api}/settings/ai", tags=["settings"])
    def put_ai_settings(request: AiSettingsUpdate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        settings = svc.repository.get_settings()
        updates: dict[str, str] = {}
        credentials = read_ai_credentials(svc.paths.ai_credentials_file)
        credentials_changed = False
        if request.transcribe is not None:
            credentials_changed = _apply_ai_group(
                request.transcribe, "transcribe", "transcribe",
                {"model": "ai_transcribe_model"}, settings, updates, credentials,
            ) or credentials_changed
        if request.understand is not None:
            credentials_changed = _apply_ai_group(
                request.understand, "understand", "understand",
                {"chat_model": "ai_chat_model"}, settings, updates, credentials,
            ) or credentials_changed
        if request.transcriber is not None:
            transcriber = request.transcriber
            if transcriber.engine is not None:
                updates["ai_transcriber_engine"] = transcriber.engine
            if transcriber.local_stt_model is not None:
                updates["ai_local_stt_model"] = transcriber.local_stt_model
            if transcriber.stt_timeout_seconds is not None:
                updates["stt_timeout_seconds"] = str(transcriber.stt_timeout_seconds)
            if transcriber.stt_memory_limit_mb is not None:
                updates["stt_memory_limit_mb"] = str(transcriber.stt_memory_limit_mb)
            if transcriber.stt_disk_limit_mb is not None:
                updates["stt_disk_limit_mb"] = str(transcriber.stt_disk_limit_mb)
        if request.video is not None:
            video = request.video
            if video.provider is not None:
                updates["ai_video_provider"] = video.provider
            if video.model is not None:
                updates["ai_video_model"] = video.model
            if video.max_bytes is not None:
                updates["ai_video_max_bytes"] = str(video.max_bytes)
            if video.reencode is not None:
                updates["ai_video_reencode"] = video.reencode
            if video.chunk_seconds is not None:
                updates["ai_video_chunk_seconds"] = str(video.chunk_seconds)
            if video.relay_base_url is not None:
                updates["ai_video_relay_base_url"] = video.relay_base_url
            if video.relay_kind is not None:
                updates["ai_video_relay_kind"] = video.relay_kind
            if video.cos_bucket is not None:
                updates["ai_video_cos_bucket"] = video.cos_bucket
            if video.cos_region is not None:
                updates["ai_video_cos_region"] = video.cos_region
            if video.qwen_api_key:
                credentials["video_qwen"] = video.qwen_api_key
                credentials_changed = True
            if video.mimo_api_key:
                credentials["video_mimo"] = video.mimo_api_key
                credentials_changed = True
            if video.relay_secret:
                credentials["video_relay"] = video.relay_secret
                credentials_changed = True
            if video.cos_secret_id:
                credentials["video_cos_secret_id"] = video.cos_secret_id
                credentials_changed = True
            if video.cos_secret_key:
                credentials["video_cos_secret_key"] = video.cos_secret_key
                credentials_changed = True
            if video.relay_kind == "cos":
                if credentials.pop("video_relay", None) is not None:
                    credentials_changed = True
            if video.provider == "off":
                for key in ("video_qwen", "video_mimo"):
                    if credentials.pop(key, None) is not None:
                        credentials_changed = True
        if request.timeout_seconds is not None:
            updates["ai_timeout_seconds"] = str(request.timeout_seconds)
        if request.auto_pipeline is not None:
            updates["ai_auto_pipeline"] = "on" if request.auto_pipeline else "off"
        if updates:
            svc.repository.update_settings(updates)
        if credentials_changed:
            write_ai_credentials(svc.paths.ai_credentials_file, credentials)
        return _ai_settings_view(svc)

    @app.post(f"{api}/settings/ai/test", tags=["settings"])
    def test_ai_connection(request: AiConnectionTestRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        # 轻量连通性检查：使用已保存的配置与凭据；失败消息已脱敏，不回显 URL/密钥/响应。
        ok, message = svc.media_ai.test_connection(request.part)
        return {"ok": True} if ok else {"ok": False, "message": message}

    @app.post(f"{api}/settings/ai/stt-model", status_code=201, tags=["settings"])
    def stt_model_action(request: SttModelActionRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        # 本地转写模型管理（REQ-054.3）：delete 同步幂等；download 经
        # stt_model_download 作业异步执行（单 worker 串行、租约心跳、失败
        # 可重试），已有排队/运行中下载时 409。
        if request.action == "delete":
            svc.stt_manager.delete()
            svc.repository.audit("stt_model_delete", None, "succeeded")
            return {"action": "delete", "status": svc.stt_manager.status()}
        for job in svc.repository.list_jobs():
            if job["kind"] == "stt_model_download" and job["state"] in {"queued", "running", "retry_wait"}:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "model_download_busy", "message": "本地转写模型下载进行中"},
                )
        job = svc.repository.create_job(
            "stt_model_download", None, None, None, None, {"action": "download"}, priority=50,
        )
        return {"action": "download", "job_id": job["id"]}

    @app.post(f"{api}/settings/download-cookies/{{platform}}", status_code=204, tags=["settings"])
    async def upload_download_cookie(
        platform: str,
        file: Annotated[UploadFile, File(...)],
        svc: ApplicationServices = Depends(get_services),
    ) -> Response:
        # 按平台 Cookie 库（REQ-047a 修订）：每平台 1MB 上限，重复导入覆盖旧文件。
        if platform not in DOWNLOAD_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail={"code": "unsupported_platform", "message": "不支持的视频平台"},
            )
        content = await file.read(1024 * 1024 + 1)
        await file.close()
        if len(content) > 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail={"code": "cookie_file_too_large", "message": "cookies.txt 超过 1MB 限制"},
            )
        svc.paths.download_cookies.mkdir(parents=True, exist_ok=True)
        destination = svc.paths.download_cookie_file(platform)
        staging = destination.parent / (destination.name + ".part")
        try:
            with staging.open("wb") as target:
                target.write(content)
            secure_private_file(staging)
            os.replace(staging, destination)
            secure_private_file(destination)
        finally:
            staging.unlink(missing_ok=True)
        return Response(status_code=204)

    @app.delete(f"{api}/settings/download-cookies/{{platform}}", status_code=204, tags=["settings"])
    def delete_download_cookie(platform: str, svc: ApplicationServices = Depends(get_services)) -> Response:
        # 幂等删除：不存在也返回 204。
        if platform not in DOWNLOAD_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail={"code": "unsupported_platform", "message": "不支持的视频平台"},
            )
        svc.paths.download_cookie_file(platform).unlink(missing_ok=True)
        return Response(status_code=204)

    @app.post(f"{api}/imports/paste", status_code=201, tags=["imports"])
    def import_paste(request: PasteImportRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.imports.paste(request)
        except (StorageLimitError, ValueError) as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc

    @app.post(f"{api}/imports/file", status_code=201, tags=["imports"])
    async def import_file(
        file: Annotated[UploadFile, File(...)],
        rights: Annotated[RightsCategory, Form(...)],
        title: Annotated[str, Form()] = "",
        author: Annotated[str | None, Form()] = None,
        language: Annotated[str, Form()] = "zh",
        notes: Annotated[str | None, Form()] = None,
        source_date: Annotated[str | None, Form()] = None,
        domains: Annotated[str, Form()] = "[]",
        genres: Annotated[str, Form()] = "[]",
        tags: Annotated[str, Form()] = "[]",
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            domain_values = json.loads(domains)
            genre_values = json.loads(genres)
            tag_values = json.loads(tags)
            if not isinstance(domain_values, list) or not isinstance(genre_values, list) or not isinstance(tag_values, list):
                raise ValueError("领域、体裁和标签必须是 JSON 数组")
            from app.domain.models import PasteImportRequest

            validated = PasteImportRequest(title=title or file.filename or "未命名文档", text="x", rights=rights, language=language, source_date=source_date, domains=domain_values, genres=genre_values, tags=tag_values)
            expected_bytes = file.size if isinstance(file.size, int) and file.size >= 0 else None
            return svc.imports.file(file.file, file.filename or "upload.bin", file.content_type, validated.title, rights.value, author, language, notes, validated.domains, validated.genres, validated.tags, expected_bytes, validated.source_date.isoformat() if validated.source_date else None)
        except (ValueError, StorageLimitError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.post(f"{api}/imports/image", status_code=201, tags=["imports"])
    async def import_image(
        file: Annotated[UploadFile, File(...)],
        rights: Annotated[RightsCategory, Form(...)],
        title: Annotated[str, Form()] = "",
        author: Annotated[str | None, Form()] = None,
        language: Annotated[str, Form()] = "zh",
        notes: Annotated[str | None, Form()] = None,
        source_date: Annotated[str | None, Form()] = None,
        domains: Annotated[str, Form()] = "[]",
        genres: Annotated[str, Form()] = "[]",
        tags: Annotated[str, Form()] = "[]",
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            domain_values = json.loads(domains)
            genre_values = json.loads(genres)
            tag_values = json.loads(tags)
            if not isinstance(domain_values, list) or not isinstance(genre_values, list) or not isinstance(tag_values, list):
                raise ValueError("领域、体裁和标签必须是 JSON 数组")
            from app.domain.models import PasteImportRequest

            validated = PasteImportRequest(
                title=title or (Path(file.filename).stem if file.filename else "") or "未命名图片", text="x",
                rights=rights, language=language,
                source_date=source_date, domains=domain_values, genres=genre_values, tags=tag_values,
            )
            expected_bytes = file.size if isinstance(file.size, int) and file.size >= 0 else None
            return svc.imports.image(
                file.file, file.filename or "upload.bin", validated.title, rights.value, author, language,
                notes, validated.domains, validated.genres, validated.tags, expected_bytes,
                validated.source_date.isoformat() if validated.source_date else None,
            )
        except (ValueError, StorageLimitError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.post(f"{api}/imports/prefill", tags=["imports"])
    async def import_prefill(
        file: Annotated[UploadFile | None, File()] = None,
        text: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        # 导入预填（REQ-049）：只读识别元数据，不持久化、不联网、不触碰 data root。
        if file is not None:
            try:
                filename = file.filename or ""
                suffix = Path(filename).suffix.lower()
                if suffix not in PREFILL_SUFFIXES:
                    raise HTTPException(
                        status_code=422,
                        detail={"code": "unsupported_prefill_suffix", "message": "不支持的文件类型"},
                    )
                content = await file.read(20 * 1024 * 1024 + 1)
                if len(content) > 20 * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail={"code": "prefill_file_too_large", "message": "文件超过 20MB 限制"},
                    )
                return suggest_document(filename, content)
            finally:
                await file.close()
        if text is not None and len(text.encode("utf-8")) > 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail={"code": "prefill_text_too_large", "message": "文本超过 1MB 限制"},
            )
        if text:
            return suggest_text(text)
        raise HTTPException(
            status_code=422,
            detail={"code": "prefill_input_required", "message": "需要提供文件或文本"},
        )

    @app.post(f"{api}/videos/local", status_code=201, tags=["videos"])
    async def import_local_video(
        file: Annotated[UploadFile, File(...)],
        rights: Annotated[RightsCategory, Form(...)],
        title: Annotated[str, Form()] = "",
        author: Annotated[str | None, Form()] = None,
        language: Annotated[str, Form()] = "zh",
        notes: Annotated[str | None, Form()] = None,
        source_date: Annotated[str | None, Form()] = None,
        domains: Annotated[str, Form()] = "[]",
        genres: Annotated[str, Form()] = "[]",
        tags: Annotated[str, Form()] = "[]",
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            domain_values = json.loads(domains)
            genre_values = json.loads(genres)
            tag_values = json.loads(tags)
            if not isinstance(domain_values, list) or not isinstance(genre_values, list) or not isinstance(tag_values, list):
                raise ValueError("领域、体裁和标签必须是 JSON 数组")
            from app.domain.models import PasteImportRequest

            validated = PasteImportRequest(
                title=title or file.filename or "未命名视频", text="x", rights=rights, language=language,
                source_date=source_date, domains=domain_values, genres=genre_values, tags=tag_values,
            )
            expected_bytes = file.size if isinstance(file.size, int) and file.size >= 0 else None
            return svc.imports.video(
                file.file, file.filename or "upload.bin", validated.title, rights.value, author, language,
                notes, validated.domains, validated.genres, validated.tags, expected_bytes,
                validated.source_date.isoformat() if validated.source_date else None,
            )
        except (ValueError, StorageLimitError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.post(f"{api}/videos/link", status_code=201, tags=["videos"])
    def create_video_link(request: DownloadLinkRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        if request.platform not in DOWNLOAD_PLATFORMS:
            raise HTTPException(status_code=422, detail={"code": "unsupported_platform", "message": "不支持的视频平台"})
        try:
            validate_download_url(request.url, request.platform)
        except ValueError as exc:
            # 拒绝消息不含 URL 内容（REQ-047.1）。
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_url", "message": "链接无效：仅支持哔哩哔哩或抖音的 HTTPS 链接，且不含登录凭据"},
            ) from exc
        capability = svc.downloader.capability()
        if not capability.get("enabled"):
            raise HTTPException(
                status_code=503,
                detail={"code": "downloader_unavailable", "message": "链接下载工具不可用：需要 yt-dlp 与 FFmpeg/ffprobe"},
            )
        if request.use_cookie and not capability.get("cookies", {}).get(request.platform):
            raise HTTPException(
                status_code=422,
                detail={"code": "cookie_file_unavailable", "message": "尚未导入该平台 cookies.txt，无法使用 Cookie 下载"},
            )
        # payload_json 只存脱敏链接（scheme://host/path），绝不存原文 URL 参数。
        job = svc.repository.create_job(
            "video_download", None, None, None, None,
            {
                "url": sanitize_download_url(request.url),
                "platform": request.platform,
                "use_cookie": request.use_cookie,
                "rights": request.rights.value,
                "title": request.title,
                "author": request.author,
                "language": request.language,
                "notes": request.notes,
                "source_date": request.source_date.isoformat() if request.source_date else None,
                "domains": request.domains,
                "genres": request.genres,
                "tags": request.tags,
            },
            priority=100,
        )
        return job

    @app.post(f"{api}/videos/link/probe", tags=["videos"])
    def probe_video_link(request: LinkProbeRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        # REQ-047b 只读元数据探测：与 /videos/link 同一受限通道，不入队、不写表。
        if request.platform not in DOWNLOAD_PLATFORMS:
            raise HTTPException(status_code=422, detail={"code": "unsupported_platform", "message": "不支持的视频平台"})
        try:
            validate_download_url(request.url, request.platform)
        except ValueError as exc:
            # 拒绝消息不含 URL 内容（REQ-047.1）。
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_url", "message": "链接无效：仅支持哔哩哔哩或抖音的 HTTPS 链接，且不含登录凭据"},
            ) from exc
        capability = svc.downloader.capability()
        if not capability.get("enabled"):
            raise HTTPException(
                status_code=503,
                detail={"code": "downloader_unavailable", "message": "链接下载工具不可用：需要 yt-dlp 与 FFmpeg/ffprobe"},
            )
        if request.use_cookie and not capability.get("cookies", {}).get(request.platform):
            raise HTTPException(
                status_code=422,
                detail={"code": "cookie_file_unavailable", "message": "尚未导入该平台 cookies.txt，无法使用 Cookie 识别链接"},
            )
        try:
            return svc.downloader.probe_metadata(request.url, request.platform, request.use_cookie)
        except DownloadUnavailable as exc:
            # 工具/代理启动失败：与 /videos/link 同语义 503，绝不直连回退。
            raise HTTPException(
                status_code=503,
                detail={"code": "downloader_unavailable", "message": "链接下载工具不可用：需要 yt-dlp 与 FFmpeg/ffprobe"},
            ) from exc
        except DownloadInputInvalid as exc:
            if exc.args and exc.args[0] == "cookie":
                raise HTTPException(
                    status_code=422,
                    detail={"code": "cookie_file_unavailable", "message": "尚未导入该平台 cookies.txt，无法使用 Cookie 识别链接"},
                ) from exc
            # 反爬、链接失效、平台拒绝、超时：通用脱敏消息，不含 URL 内容。
            raise HTTPException(
                status_code=502,
                detail={"code": "probe_failed", "message": "链接失效、平台拒绝或探测超时，请重新复制分享链接或稍后重试"},
            ) from exc

    @app.get(f"{api}/videos/{{source_id}}", tags=["videos"])
    def video_detail(
        source_id: str,
        version_id: str | None = None,
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        detail = svc.videos.detail(source_id, version_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="视频来源不存在或尚不可用")
        detail["media_capability"] = svc.media_analyzer.capability()
        detail["ai_capability"] = svc.media_ai.capability()
        return detail

    @app.post(f"{api}/videos/{{source_id}}/transcribe", status_code=201, tags=["videos"])
    def queue_video_transcription(source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        detail = svc.videos.detail(source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="视频来源不存在或尚不可用")
        version = detail["version"]
        return svc.repository.create_job(
            "video_transcribe", source_id, version["id"], version["artifact_sha256"],
            svc.media_ai.config_hash("transcribe"), {}, priority=100,
        )

    @app.post(f"{api}/videos/{{source_id}}/summarize", status_code=201, tags=["videos"])
    def queue_video_summary(
        source_id: str,
        request: VideoSummarizeRequest | None = None,
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        detail = svc.videos.detail(source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="视频来源不存在或尚不可用")
        version = detail["version"]
        return svc.repository.create_job(
            "video_summarize", source_id, version["id"], version["artifact_sha256"],
            svc.media_ai.config_hash("summarize"), {"force_tier2": bool(request and request.force_tier2)}, priority=100,
        )

    @app.get(f"{api}/videos/{{source_id}}/stream", tags=["videos"])
    def stream_video(
        source_id: str,
        request: Request,
        version_id: str | None = None,
        svc: ApplicationServices = Depends(get_services),
    ):
        detail = svc.videos.detail(source_id, version_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="视频来源不存在或尚不可用")
        version = detail["version"]
        path = svc.artifacts.artifact_path(version["artifact_sha256"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact 不存在")
        size = path.stat().st_size
        media_type = version["media_type"]
        headers = {"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'self'"}
        range_header = request.headers.get("range")
        if not range_header:
            return FileResponse(path, media_type=media_type, headers=headers, content_disposition_type="inline")
        try:
            unit, value = range_header.split("=", 1)
            start_raw, end_raw = value.split("-", 1)
            if unit != "bytes" or "," in value:
                raise ValueError
            start = int(start_raw) if start_raw else max(0, size - int(end_raw))
            end = int(end_raw) if end_raw else size - 1
            if start < 0 or end < start or start >= size:
                raise ValueError
            end = min(end, size - 1)
        except (TypeError, ValueError):
            return JSONResponse(status_code=416, content={"detail": {"code": "invalid_range", "message": "视频范围请求无效"}}, headers={"Content-Range": f"bytes */{size}"})

        def content():
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(content(), status_code=206, media_type=media_type, headers=headers)

    @app.get(f"{api}/videos/{{source_id}}/frames/{{frame_id}}", tags=["videos"])
    def video_frame(
        source_id: str,
        frame_id: str,
        version_id: str | None = None,
        svc: ApplicationServices = Depends(get_services),
    ) -> FileResponse:
        detail = svc.videos.detail(source_id, version_id)
        if detail is None or detail.get("analysis") is None:
            raise HTTPException(status_code=404, detail="视频关键帧不存在")
        frame = next((item for item in detail["analysis"]["frames"] if item["id"] == frame_id), None)
        if frame is None:
            raise HTTPException(status_code=404, detail="视频关键帧不存在")
        path = svc.artifacts.artifact_path(frame["artifact_sha256"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact 不存在")
        return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'self'"}, content_disposition_type="inline")

    @app.get(f"{api}/sources", tags=["sources"])
    def list_sources(include_deleted: bool = False, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        return [_source_view(item) for item in svc.repository.list_sources(include_deleted)]

    @app.get(f"{api}/sources/{{source_id}}", tags=["sources"])
    def get_source(source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        source = _source_view(svc.repository.get_source(source_id))
        source["versions"] = svc.repository.versions_for_source(source_id)
        source["relations"] = svc.repository.relations_for_source(source_id)
        source["same_work_candidates"] = svc.repository.same_work_candidates(source_id)
        return source

    @app.put(f"{api}/sources/{{source_id}}/metadata", tags=["sources"])
    def update_metadata(source_id: str, request: SourceMetadataUpdate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        values = request.model_dump(exclude_unset=True)
        if "domains" in values:
            values["domains_json"] = json.dumps(values.pop("domains"), ensure_ascii=False)
        if "genres" in values:
            values["genres_json"] = json.dumps(values.pop("genres"), ensure_ascii=False)
        if "tags" in values:
            values["tags_json"] = json.dumps(sorted(set(values.pop("tags"))), ensure_ascii=False)
        return _source_view(svc.repository.update_source_metadata(source_id, values))

    @app.get(f"{api}/sources/{{source_id}}/metadata-revisions", tags=["sources"])
    def metadata_revisions(source_id: str, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        if svc.repository.get_source(source_id) is None:
            raise HTTPException(status_code=404, detail="来源不存在")
        revisions = svc.repository.metadata_revisions_for_source(source_id)
        for revision in revisions:
            revision["snapshot"] = json.loads(revision.pop("snapshot_json"))
        return revisions

    @app.put(f"{api}/sources/{{source_id}}/rights", tags=["sources"])
    def update_rights(source_id: str, rights: RightsCategory, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return _source_view(svc.repository.update_rights(source_id, rights.value))

    @app.get(f"{api}/sources/{{source_id}}/relations", tags=["sources"])
    def list_relations(source_id: str, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        if svc.repository.get_source(source_id) is None:
            raise HTTPException(status_code=404, detail="来源不存在")
        return svc.repository.relations_for_source(source_id)

    @app.post(f"{api}/sources/{{source_id}}/relations", status_code=201, tags=["sources"])
    def add_relation(source_id: str, request: RelationCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        if svc.repository.get_source(source_id) is None or svc.repository.get_source(request.related_source_id) is None:
            raise HTTPException(status_code=404, detail="关联来源不存在")
        try:
            return svc.repository.add_relation(source_id, request.related_source_id, request.relation_type)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="来源关系已存在或无效") from exc

    @app.delete(f"{api}/sources/{{source_id}}/relations/{{relation_id}}", status_code=204, tags=["sources"])
    def delete_relation(source_id: str, relation_id: str, svc: ApplicationServices = Depends(get_services)) -> Response:
        # 关系必须涉及该来源（任一方向），否则按不存在处理。
        relation = next((item for item in svc.repository.relations_for_source(source_id) if item["id"] == relation_id), None)
        if relation is None:
            raise HTTPException(status_code=404, detail="来源关系不存在")
        svc.repository.delete_relation(relation_id)
        return Response(status_code=204)

    @app.get(f"{api}/documents/{{version_id}}/representations", tags=["documents"])
    @app.get(f"{api}/docs/{{version_id}}/representations", tags=["documents"], include_in_schema=False)
    def representations(version_id: str, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        if svc.repository.get_version(version_id) is None:
            raise HTTPException(status_code=404, detail="内容版本不存在")
        return svc.repository.representations_for_version(version_id)

    @app.post(f"{api}/documents/{{version_id}}/representations/manual", status_code=201, tags=["documents"])
    @app.post(f"{api}/docs/{{version_id}}/representations/manual", status_code=201, tags=["documents"], include_in_schema=False)
    def manual_representation(version_id: str, request: ManualRepresentationCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.documents.create_manual_representation(version_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(f"{api}/representations/{{representation_id}}/evidence", tags=["evidence"])
    @app.get(f"{api}/docs/representations/{{representation_id}}/evidence", tags=["evidence"], include_in_schema=False)
    def representation_evidence(representation_id: str, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        if svc.repository.get_representation(representation_id) is None:
            raise HTTPException(status_code=404, detail="表示不存在")
        return [_evidence_view(item) for item in svc.repository.evidence_for_representation(representation_id)]

    @app.get(f"{api}/evidence/{{evidence_id}}", tags=["evidence"])
    @app.get(f"{api}/docs/evidence/{{evidence_id}}", tags=["evidence"], include_in_schema=False)
    def get_evidence(evidence_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return _evidence_view(svc.repository.get_evidence(evidence_id))

    @app.get(f"{api}/citations", tags=["evidence"])
    def list_citations(evidence_id: str, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        if svc.repository.get_evidence(evidence_id) is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        return svc.repository.citations_for_evidence(evidence_id)

    @app.post(f"{api}/citations", status_code=201, tags=["evidence"])
    @app.post(f"{api}/docs/citations", status_code=201, tags=["evidence"], include_in_schema=False)
    def create_citation(evidence_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        if svc.repository.get_evidence(evidence_id) is None:
            raise HTTPException(status_code=404, detail="证据不存在")
        return svc.repository.create_citation(evidence_id)

    @app.get(f"{api}/citations/{{citation_id}}", tags=["evidence"])
    def get_citation(citation_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.documents.citation(citation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(f"{api}/knowledge", status_code=201, tags=["knowledge"])
    @app.post(f"{api}/docs/knowledge", status_code=201, tags=["knowledge"], include_in_schema=False)
    def create_knowledge(request: KnowledgeCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.documents.create_knowledge(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(f"{api}/knowledge", tags=["knowledge"])
    def list_knowledge(published_only: bool = False, svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        return svc.repository.list_knowledge(published_only)

    @app.post(f"{api}/knowledge/{{knowledge_id}}/publish", tags=["knowledge"])
    def publish_knowledge(knowledge_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.documents.publish_knowledge(knowledge_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get(f"{api}/search", tags=["search"])
    def search(
        q: str = "", include_historical: bool = False, include_incomplete: bool = False, source_type: str | None = None,
        domains: Annotated[list[str] | None, Query()] = None, genre: str | None = None, tag: str | None = None, author: str | None = None,
        language: str | None = None,
        processing_state: str | None = None, source_date_from: date | None = None, source_date_to: date | None = None,
        imported_at_from: date | None = None, imported_at_to: date | None = None, topic_id: str | None = None,
        sort: str = "relevance", svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        invalid_domains = sorted(set(domains or []) - set(TAXONOMY_DOMAIN_VALUES) - {"_none"})
        if invalid_domains or (genre is not None and genre != "_none" and genre not in TAXONOMY_GENRE_VALUES):
            raise HTTPException(status_code=422, detail={"code": "request_validation", "message": "请求字段无效"})
        try:
            items = svc.search.search(
                q, include_historical=include_historical, include_incomplete=include_incomplete, source_type=source_type,
                domains=domains, genre=genre, tag=tag, author=author, language=language, processing_state=processing_state,
                source_date_from=source_date_from, source_date_to=source_date_to, imported_at_from=imported_at_from,
                imported_at_to=imported_at_to, topic_id=topic_id, sort=sort,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"mode": "短语、关键词、子串匹配；不提供语义检索", "sort": sort, "items": items}

    @app.get(f"{api}/taxonomy", tags=["taxonomy"])
    def taxonomy() -> dict[str, Any]:
        # 分类体系（领域×体裁）的唯一来源；前端启动时拉取一次。
        return {"domains": list(TAXONOMY_DOMAINS), "genres": list(TAXONOMY_GENRES)}

    @app.get(f"{api}/tags", tags=["taxonomy"])
    def tags(svc: ApplicationServices = Depends(get_services)) -> list[str]:
        values = {tag for source in svc.repository.list_sources(True) for tag in json.loads(source["tags_json"])}
        values.update(tag for card in svc.repository.list_external_cards() for tag in json.loads(card["tags_json"]))
        return sorted(values)

    @app.get(f"{api}/topics", tags=["taxonomy"])
    def list_topics(svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        return svc.repository.list_topics()

    @app.post(f"{api}/topics", status_code=201, tags=["taxonomy"])
    def create_topic(request: TopicCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        if any(svc.repository.get_source(source_id) is None for source_id in request.source_ids):
            raise HTTPException(status_code=404, detail="主题包含不存在的来源")
        try:
            return svc.repository.create_topic(request.name, request.source_ids)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="主题名称已存在") from exc

    @app.post(f"{api}/topics/{{topic_id}}/sources/{{source_id}}", tags=["taxonomy"])
    def add_topic_source(topic_id: str, source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        if not svc.repository.add_source_to_topic(topic_id, source_id):
            raise HTTPException(status_code=404, detail="主题或来源不存在")
        return {"topic_id": topic_id, "source_id": source_id}

    @app.put(f"{api}/topics/{{topic_id}}", tags=["taxonomy"])
    def rename_topic(topic_id: str, request: TopicRename, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            topic = svc.repository.rename_topic(topic_id, request.name)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="主题名称已存在") from exc
        if topic is None:
            raise HTTPException(status_code=404, detail="主题不存在")
        return topic

    @app.delete(f"{api}/topics/{{topic_id}}", status_code=204, tags=["taxonomy"])
    def delete_topic(topic_id: str, svc: ApplicationServices = Depends(get_services)) -> Response:
        if not svc.repository.delete_topic(topic_id):
            raise HTTPException(status_code=404, detail="主题不存在")
        return Response(status_code=204)

    @app.delete(f"{api}/topics/{{topic_id}}/sources/{{source_id}}", status_code=204, tags=["taxonomy"])
    def remove_topic_source(topic_id: str, source_id: str, svc: ApplicationServices = Depends(get_services)) -> Response:
        if not svc.repository.remove_source_from_topic(topic_id, source_id):
            raise HTTPException(status_code=404, detail="主题关联不存在")
        return Response(status_code=204)

    @app.get(f"{api}/external/cards", tags=["external-cards"])
    def list_external_cards(svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        return [_external_view(item) for item in svc.repository.list_external_cards()]

    @app.post(f"{api}/external/cards", status_code=201, tags=["external-cards"])
    def create_external_card(request: ExternalCardCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return _external_view(svc.external_cards.create(request))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail="外部卡 URL 已存在") from exc

    @app.post(f"{api}/external/douyin", status_code=201, tags=["external-cards"])
    def create_douyin_card(request: DouyinCardCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return _external_view(svc.external_cards.create_douyin(request))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail="抖音卡 URL 已存在") from exc

    @app.get(f"{api}/jobs", tags=["jobs"])
    def list_jobs(svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        return svc.repository.list_jobs()

    @app.get(f"{api}/jobs/{{job_id}}", tags=["jobs"])
    def get_job(job_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        job = svc.repository.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="作业不存在")
        return job

    @app.post(f"{api}/jobs/{{job_id}}/cancel", tags=["jobs"])
    def cancel_job(job_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        job = svc.repository.request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="作业不存在")
        return job

    @app.post(f"{api}/jobs/{{job_id}}/retry", tags=["jobs"])
    def retry_job(job_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            job = svc.repository.retry_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=404, detail="作业不存在")
        return job

    @app.post(f"{api}/jobs/delete", tags=["jobs"])
    def delete_jobs(request: JobsDeleteRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        # 批量删除作业记录：运行中的作业拒绝（409）；不存在的 id 幂等跳过。
        existing = {job["id"]: job for job in svc.repository.list_jobs()}
        for job_id in request.job_ids:
            job = existing.get(job_id)
            if job is not None and job["state"] == "running":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "job_running", "message": "运行中的作业不能删除"},
                )
        deleted = svc.repository.delete_jobs(request.job_ids)
        for job_id in request.job_ids:
            if job_id in existing:
                svc.repository.audit("job_delete", job_id, "succeeded")
        return {"deleted": deleted}

    @app.post(f"{api}/jobs/run-once", tags=["jobs"])
    def run_one_job(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return {"job": svc.jobs.run_once()}

    @app.post(f"{api}/sources/{{source_id}}/delete", tags=["lifecycle"])
    def delete_source(source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return _source_view(svc.lifecycle.delete(source_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(f"{api}/sources/{{source_id}}/restore", tags=["lifecycle"])
    def restore_source(source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return _source_view(svc.lifecycle.restore(source_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(f"{api}/sources/{{source_id}}/purge", tags=["lifecycle"])
    def purge_source(source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.lifecycle.purge(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(f"{api}/backups", tags=["transfers"])
    def list_backups(svc: ApplicationServices = Depends(get_services)) -> list[dict[str, Any]]:
        return svc.repository.list_backups()

    @app.post(f"{api}/backups", status_code=201, tags=["transfers"])
    def create_backup(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return svc.transfers.create_backup()

    @app.post(f"{api}/backups/{{backup_id}}/restore", tags=["transfers"])
    def restore_backup(backup_id: str, request: RestoreRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.transfers.restore_backup(backup_id, request.target_data_root, request.target_database_url)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(f"{api}/exports", status_code=201, tags=["transfers"])
    def create_export(request: ExportCreate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.transfers.create_export(request.confirmed)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(f"{api}/reimports", tags=["transfers"])
    def reimport(request: ReimportRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        try:
            return svc.transfers.reimport(request.archive_path)
        except ReimportConflict as exc:
            raise HTTPException(status_code=409, detail={**exc.report, "code": "reimport_conflict", "message": "导入逻辑记录冲突"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(f"{api}/verify", tags=["transfers"])
    def verify(request: VerifyRequest, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return svc.transfers.verify_artifacts(request.full, request.sample_size)

    @app.get(f"{api}/sources/{{source_id}}/original", tags=["documents"])
    def original_artifact(source_id: str, svc: ApplicationServices = Depends(get_services)) -> FileResponse:
        source = svc.repository.get_source(source_id, include_deleted=False)
        if source is None:
            raise HTTPException(status_code=404, detail="来源不存在")
        versions = svc.repository.versions_for_source(source_id)
        if not versions:
            raise HTTPException(status_code=404, detail="内容版本不存在")
        version = versions[0]
        path = svc.artifacts.artifact_path(version["artifact_sha256"])
        if not path.exists():
            raise HTTPException(status_code=404, detail="artifact 不存在")
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'self'",
        }
        suffix = Path(version["original_name"]).suffix.lower()
        if suffix == ".pdf":
            return FileResponse(
                path,
                media_type="application/pdf",
                headers=headers,
                filename=Path(version["original_name"]).name,
                content_disposition_type="inline",
            )
        if version["media_type"] in {"image/jpeg", "image/png", "image/webp"}:
            # 图片原件 inline 返回，供 <img> 直接预览；安全头与其他分支一致。
            return FileResponse(
                path,
                media_type=version["media_type"],
                headers=headers,
                filename=Path(version["original_name"]).name,
                content_disposition_type="inline",
            )
        return FileResponse(
            path,
            media_type="text/plain; charset=utf-8" if suffix in {".txt", ".md", ".markdown"} else "application/octet-stream",
            headers=headers,
            filename=Path(version["original_name"]).name,
            content_disposition_type="attachment",
        )

    static_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

        @app.get("/{web_path:path}", include_in_schema=False)
        def web_ui(web_path: str) -> FileResponse:
            candidate = static_dir / web_path
            if web_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app


def application() -> FastAPI:
    """Uvicorn factory to prevent data-root creation while importing test modules."""
    return create_app()
