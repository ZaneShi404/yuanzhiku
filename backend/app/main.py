"""FastAPI composition root for the loopback-only local application."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.adapters.sqlite import SqliteRepository
from app.adapters.storage import ArtifactStore, StorageLimitError
from app.adapters.parsers import LocalDocumentParser
from app.adapters.media import LocalFfmpegMediaAnalyzer, UnconfiguredMediaAi
from app.core.config import DataPaths, InstanceLock, data_paths, database_backend, database_url
from app.core.operations import OperationalLog
from app.ports.repository import RepositoryPort
from app.domain.models import (
    DouyinCardCreate,
    ExportCreate,
    ExternalCardCreate,
    KnowledgeCreate,
    ManualRepresentationCreate,
    PasteImportRequest,
    RelationCreate,
    RestoreRequest,
    ReimportRequest,
    RightsCategory,
    SettingsUpdate,
    SourceMetadataUpdate,
    TopicCreate,
    VerifyRequest,
)
from app.services.documents import DocumentService
from app.services.external_cards import ExternalCardService
from app.services.imports import ImportService
from app.services.jobs import JobService
from app.services.lifecycle import LifecycleService
from app.services.videos import VideoService
from app.services.search import SearchService
from app.services.transfers import ReimportConflict, TransferService


class ApplicationServices:
    def __init__(self, paths: DataPaths) -> None:
        paths.create()
        self.paths = paths
        self.operations = OperationalLog(paths.logs)
        self.operations.prune()
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
        self.media_ai = UnconfiguredMediaAi()
        self.documents = DocumentService(self.repository)
        self.videos = VideoService(self.repository, self.artifacts, self.documents, self.media_analyzer)
        self.transfers = TransferService(paths, self.repository, self.artifacts)
        self.jobs = JobService(
            self.repository,
            self.artifacts,
            self.documents,
            self.transfers.create_backup,
            parser=self.parser,
            integrity_runner=lambda sample_size: self.transfers.verify_artifacts(False, sample_size),
            videos=self.videos,
        )
        self.imports = ImportService(self.repository, self.artifacts)
        self.external_cards = ExternalCardService(self.repository)
        self.lifecycle = LifecycleService(self.repository, self.artifacts)
        self.search = SearchService(self.repository)


def _source_view(source: dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        raise HTTPException(status_code=404, detail="来源不存在")
    value = dict(source)
    value["categories"] = json.loads(value.pop("categories_json"))
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def upload_capacity_preflight(request, call_next):
        if request.method == "POST" and request.url.path in {"/api/v1/imports/file", "/api/v1/videos/local"}:
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
            "media": {"local": svc.media_analyzer.capability(), "ai": svc.media_ai.capability()},
            "network": {"bind": "127.0.0.1", "https": False, "telemetry": False},
        }

    @app.get(f"{api}/settings", tags=["settings"])
    def get_settings(svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return svc.repository.get_settings()

    @app.put(f"{api}/settings", tags=["settings"])
    def put_settings(request: SettingsUpdate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        return svc.repository.update_settings(request.model_dump(exclude_none=True))

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
        categories: Annotated[str, Form()] = "[]",
        tags: Annotated[str, Form()] = "[]",
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            category_values = json.loads(categories)
            tag_values = json.loads(tags)
            if not isinstance(category_values, list) or not isinstance(tag_values, list):
                raise ValueError("分类和标签必须是 JSON 数组")
            from app.domain.models import PasteImportRequest

            validated = PasteImportRequest(title=title or file.filename or "未命名文档", text="x", rights=rights, language=language, source_date=source_date, categories=category_values, tags=tag_values)
            expected_bytes = file.size if isinstance(file.size, int) and file.size >= 0 else None
            return svc.imports.file(file.file, file.filename or "upload.bin", file.content_type, validated.title, rights.value, author, language, notes, validated.categories, validated.tags, expected_bytes, validated.source_date.isoformat() if validated.source_date else None)
        except (ValueError, StorageLimitError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.post(f"{api}/videos/local", status_code=201, tags=["videos"])
    async def import_local_video(
        file: Annotated[UploadFile, File(...)],
        rights: Annotated[RightsCategory, Form(...)],
        title: Annotated[str, Form()] = "",
        author: Annotated[str | None, Form()] = None,
        language: Annotated[str, Form()] = "zh",
        notes: Annotated[str | None, Form()] = None,
        source_date: Annotated[str | None, Form()] = None,
        categories: Annotated[str, Form()] = "[]",
        tags: Annotated[str, Form()] = "[]",
        svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            category_values = json.loads(categories)
            tag_values = json.loads(tags)
            if not isinstance(category_values, list) or not isinstance(tag_values, list):
                raise ValueError("分类和标签必须是 JSON 数组")
            from app.domain.models import PasteImportRequest

            validated = PasteImportRequest(
                title=title or file.filename or "未命名视频", text="x", rights=rights, language=language,
                source_date=source_date, categories=category_values, tags=tag_values,
            )
            expected_bytes = file.size if isinstance(file.size, int) and file.size >= 0 else None
            return svc.imports.video(
                file.file, file.filename or "upload.bin", validated.title, rights.value, author, language,
                notes, validated.categories, validated.tags, expected_bytes,
                validated.source_date.isoformat() if validated.source_date else None,
            )
        except (ValueError, StorageLimitError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            await file.close()

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
    def queue_video_summary(source_id: str, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        detail = svc.videos.detail(source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="视频来源不存在或尚不可用")
        version = detail["version"]
        return svc.repository.create_job(
            "video_summarize", source_id, version["id"], version["artifact_sha256"],
            svc.media_ai.config_hash("summarize"), {}, priority=100,
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
        return source

    @app.put(f"{api}/sources/{{source_id}}/metadata", tags=["sources"])
    def update_metadata(source_id: str, request: SourceMetadataUpdate, svc: ApplicationServices = Depends(get_services)) -> dict[str, Any]:
        values = request.model_dump(exclude_unset=True)
        if "categories" in values:
            values["categories_json"] = json.dumps(values.pop("categories"), ensure_ascii=False)
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
        category: str | None = None, tag: str | None = None, author: str | None = None, language: str | None = None,
        processing_state: str | None = None, source_date_from: date | None = None, source_date_to: date | None = None,
        imported_at_from: date | None = None, imported_at_to: date | None = None, sort: str = "relevance", svc: ApplicationServices = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            items = svc.search.search(
                q, include_historical=include_historical, include_incomplete=include_incomplete, source_type=source_type,
                category=category, tag=tag, author=author, language=language, processing_state=processing_state,
                source_date_from=source_date_from, source_date_to=source_date_to, imported_at_from=imported_at_from,
                imported_at_to=imported_at_to, sort=sort,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"mode": "短语、关键词、子串匹配；不提供语义检索", "sort": sort, "items": items}

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
