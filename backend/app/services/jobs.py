"""Durable single-worker job execution."""

from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import queue
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from app.domain.media import MediaProcessingLimits
from app.domain.models import sanitize_download_url
from app.domain.parsing import ParsedDocument
from app.ports.parser import DocumentParserPort
from app.ports.media import (
    DownloadInputInvalid,
    DownloadProcessingCancelled,
    DownloadUnavailable,
    MediaAiUnavailable,
    MediaDownloaderPort,
    MediaInputInvalid,
    MediaProcessingCancelled,
    MediaToolUnavailable,
)
from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort
from app.services.documents import DocumentService
from app.services.imports import ImportService
from app.services.videos import VideoService


def _parse_worker(
    result_queue,
    parser: DocumentParserPort,
    artifact_path: str,
    filename: str,
    media_type: str | None,
    workspace: str,
    maximum_bytes: int,
) -> None:
    try:
        # The only adapter invocation is inside the parser port. No parser may
        # initiate a cloud fallback or write outside this per-job workspace.
        try:
            result = parser.parse(Path(artifact_path), filename, media_type, Path(workspace), maximum_bytes)
        except TypeError:
            # Allows small deterministic parser fakes used by isolated tests.
            result = parser.parse(Path(artifact_path), filename, media_type, Path(workspace))
        result_queue.put(("result", result))
    except BaseException:
        # Parent deliberately keeps parser details out of durable logs/messages.
        result_queue.put(("error", None))


def _resident_memory_bytes(process_id: int) -> int | None:
    """Read process RSS without making psutil a mandatory runtime dependency."""
    try:
        import psutil  # type: ignore[import-not-found]

        process = psutil.Process(process_id)
        return process.memory_info().rss + sum(child.memory_info().rss for child in process.children(recursive=True))
    except (ImportError, OSError):
        pass
    if os.name == "nt":
        try:
            process_query_information = 0x0400
            process_vm_read = 0x0010
            handle = ctypes.windll.kernel32.OpenProcess(process_query_information | process_vm_read, False, process_id)
            if not handle:
                return None
            try:
                class ProcessMemoryCountersEx(ctypes.Structure):
                    _fields_ = [
                        ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t),
                    ]

                counters = ProcessMemoryCountersEx()
                counters.cb = ctypes.sizeof(counters)
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return int(counters.WorkingSetSize)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    return None


def _directory_size(path: Path) -> int:
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


class ParserCircuitBreaker(RuntimeError):
    pass


class ParserCancelled(RuntimeError):
    pass


class JobLeaseLost(RuntimeError):
    pass


class JobService:
    def __init__(
        self,
        repository: RepositoryPort,
        artifacts: ArtifactStoragePort,
        documents: DocumentService,
        backup_runner: Callable[[], dict] | None = None,
        parse_runner: Callable[[Path, str, str | None, float, float, Callable[[], bool], Callable[[], None]], ParsedDocument] | None = None,
        *,
        parser: DocumentParserPort | None = None,
        integrity_runner: Callable[[int], dict] | None = None,
        videos: VideoService | None = None,
        imports: ImportService | None = None,
        downloader: MediaDownloaderPort | None = None,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.documents = documents
        self.videos = videos
        self.imports = imports
        self.downloader = downloader
        if parser is None:
            from app.adapters.parsers import LocalDocumentParser

            parser = LocalDocumentParser(artifacts.paths.models, Path(__file__).resolve().parents[2] / "models.lock.json")
        self.parser = parser
        self.backup_runner = backup_runner
        self.integrity_runner = integrity_runner
        self.parse_runner = parse_runner or self._run_parser_with_circuit_breakers
        self._memory_limit_mb = 2048
        self._disk_limit_mb = 1024

    def run_once(self) -> dict | None:
        job = self.repository.claim_next_job()
        if job is None:
            return None
        job_id = job["id"]
        try:
            if self.repository.job_cancel_requested(job_id):
                self._finish(job, "cancelled", "已在执行前取消")
                return self.repository.get_job(job_id)
            if job["kind"] == "parse":
                self._parse(job)
            elif job["kind"] == "video_analyze":
                self._video_analyze(job)
            elif job["kind"] == "video_download":
                self._video_download(job)
            elif job["kind"] in {"video_transcribe", "video_summarize"}:
                self._finish(job, "blocked", "未配置媒体 AI 服务", progress=100)
            elif job["kind"] == "backup":
                if self.backup_runner is None:
                    self._finish(job, "blocked", "备份服务不可用")
                else:
                    self._run_with_lease_heartbeat(job, self.backup_runner)
                    payload = json.loads(job["payload_json"])
                    settings = (
                        {"last_backup_date": payload["date"]}
                        if isinstance(payload.get("date"), str)
                        else None
                    )
                    self._finish(job, "succeeded", "备份完成", progress=100, settings=settings)
            elif job["kind"] == "integrity_sample":
                if self.integrity_runner is None:
                    self._finish(job, "blocked", "完整性抽样服务不可用")
                else:
                    payload = json.loads(job["payload_json"])
                    sample_size = payload.get("sample_size", 10)
                    if not isinstance(sample_size, int) or isinstance(sample_size, bool):
                        raise ValueError("完整性抽样参数无效")
                    result = self._run_with_lease_heartbeat(
                        job,
                        lambda: self.integrity_runner(max(1, min(10_000, sample_size))),
                    )
                    if not result.get("valid"):
                        raise RuntimeError("空闲完整性抽样发现校验失败")
                    settings = (
                        {"last_integrity_sample_date": payload["date"]}
                        if isinstance(payload.get("date"), str)
                        else None
                    )
                    self._finish(job, "succeeded", "空闲完整性抽样完成", progress=100, settings=settings)
            else:
                self._finish(job, "failed", "未知作业类型")
        except JobLeaseLost:
            return self.repository.get_job(job_id)
        except ParserCancelled:
            try:
                self._finish(job, "cancelled", "解析已取消")
            except JobLeaseLost:
                pass
        except MediaProcessingCancelled:
            try:
                if self._finish(job, "cancelled", "视频分析已取消"):
                    self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                    self.repository.update_processing(job["source_id"], "cancelled")
            except JobLeaseLost:
                pass
        except MediaToolUnavailable:
            try:
                self._finish(job, "blocked", "未找到本地 FFmpeg 或 ffprobe", progress=100)
                self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                self.repository.update_processing(job["source_id"], "blocked")
            except JobLeaseLost:
                pass
        except MediaInputInvalid:
            try:
                self._finish(job, "failed", "本地视频无法分析")
                self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                self.repository.update_processing(job["source_id"], "failed")
            except JobLeaseLost:
                pass
        except MediaAiUnavailable:
            try:
                self._finish(job, "blocked", "未配置媒体 AI 服务", progress=100)
            except JobLeaseLost:
                pass
        except DownloadProcessingCancelled:
            try:
                self._finish(job, "cancelled", "链接下载已取消")
            except JobLeaseLost:
                pass
        except DownloadUnavailable as exc:
            try:
                # 工具缺失或回环代理启动失败（fail-closed）：可安装工具后从作业页重试。
                if str(exc) == "ffmpeg_missing":
                    message = "未找到本地 FFmpeg 或 ffprobe"
                else:
                    message = "链接下载工具不可用：需要 yt-dlp 与 FFmpeg/ffprobe"
                self._finish(job, "blocked", message, progress=100)
            except JobLeaseLost:
                pass
        except DownloadInputInvalid:
            try:
                # 反爬/链接失效/平台拒绝/超限/产物无效：通用脱敏消息，可有限重试。
                self._finish(job, "failed", "链接失效、平台拒绝或下载产物无效，请重新复制分享链接或稍后重试")
            except JobLeaseLost:
                pass
        except ParserCircuitBreaker as exc:
            try:
                if self._finish(job, "failed", str(exc)):
                    self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                    self.repository.update_processing(job["source_id"], "failed")
            except JobLeaseLost:
                pass
        except Exception:
            # No exception detail is persisted because it may include source paths or content.
            retry_count = job.get("retry_count", max(0, job["attempt_count"] - 1))
            state = "retry_wait" if retry_count < job["max_attempts"] else "failed"
            try:
                if self._finish(job, state, "本地处理失败", outcome="retryable_failure"):
                    if state == "failed" and job["kind"] == "parse" and job["content_version_id"]:
                        self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                    if job["source_id"] and state == "failed":
                        self.repository.update_processing(job["source_id"], "failed")
            except JobLeaseLost:
                pass
        return self.repository.get_job(job_id)

    def _finish(
        self,
        job: dict,
        state: str,
        message: str,
        *,
        progress: int | None = None,
        outcome: str | None = None,
        settings: dict[str, str | int] | None = None,
    ) -> bool:
        updated = self.repository.update_job(
            job["id"],
            job["lease_token"],
            state=state,
            progress=progress,
            message=message,
            done=True,
            outcome=outcome,
            settings=settings,
        )
        if not updated:
            raise JobLeaseLost()
        return True

    def _heartbeat(self, job: dict) -> None:
        if not self.repository.touch_job(job["id"], job["lease_token"]):
            raise JobLeaseLost()

    def _run_with_lease_heartbeat(self, job: dict, runner: Callable[[], object]) -> object:
        """Run non-parser work while renewing the claim at a bounded interval."""
        result: list[object] = []
        failure: list[BaseException] = []

        def execute() -> None:
            try:
                result.append(runner())
            except BaseException as exc:
                failure.append(exc)

        worker = threading.Thread(target=execute, daemon=True)
        worker.start()
        lease_seconds = self._lease_seconds()
        interval = max(1.0, min(30.0, lease_seconds / 3))
        while worker.is_alive():
            worker.join(timeout=interval)
            if worker.is_alive():
                self._heartbeat(job)
        if failure:
            raise failure[0]
        return result[0] if result else None

    def _lease_seconds(self) -> int:
        try:
            return max(60, min(86_400, int(self.repository.get_settings().get("job_lease_seconds", "300"))))
        except (TypeError, ValueError):
            return 300

    def _run_parser_with_circuit_breakers(
        self,
        artifact_path: Path,
        filename: str,
        media_type: str | None,
        timeout_seconds: float,
        no_progress_seconds: float,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> ParsedDocument:
        """Execute parsing in a child process with bounded time, RSS and disk."""
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        workspace = self.artifacts.staging_path().with_suffix("")
        workspace.mkdir(parents=True, exist_ok=False)
        maximum_bytes = self._memory_limit_mb * 1024 * 1024
        process = context.Process(
            target=_parse_worker,
            args=(result_queue, self.parser, str(artifact_path), filename, media_type, str(workspace), maximum_bytes),
            daemon=True,
        )
        process.start()
        started = last_progress = time.monotonic()
        try:
            while process.is_alive():
                if cancelled():
                    raise ParserCancelled()
                current = time.monotonic()
                if current - started >= timeout_seconds:
                    raise ParserCircuitBreaker("解析超时断路器已触发")
                if current - last_progress >= no_progress_seconds:
                    raise ParserCircuitBreaker("解析无进展断路器已触发")
                process_id = getattr(process, "pid", None)
                rss = _resident_memory_bytes(process_id) if isinstance(process_id, int) and process_id > 0 else None
                if rss is not None and rss > maximum_bytes:
                    raise ParserCircuitBreaker("解析内存断路器已触发")
                if _directory_size(workspace) > self._disk_limit_mb * 1024 * 1024:
                    raise ParserCircuitBreaker("解析临时磁盘断路器已触发")
                heartbeat()
                try:
                    kind, payload = result_queue.get(timeout=min(0.1, max(0.01, no_progress_seconds)))
                except queue.Empty:
                    continue
                last_progress = time.monotonic()
                if kind == "result" and isinstance(payload, ParsedDocument):
                    return payload
                raise RuntimeError("本地解析失败")
            try:
                kind, payload = result_queue.get_nowait()
            except queue.Empty as exc:
                raise RuntimeError("本地解析未返回结果") from exc
            if kind == "result" and isinstance(payload, ParsedDocument):
                return payload
            raise RuntimeError("本地解析失败")
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            result_queue.close()
            shutil.rmtree(workspace, ignore_errors=True)

    def _video_analyze(self, job: dict) -> None:
        if self.videos is None:
            if self._finish(job, "blocked", "本地视频分析服务不可用", progress=100):
                self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                self.repository.update_processing(job["source_id"], "blocked")
            return
        settings = self.repository.get_settings()
        try:
            maximum_frames = max(1, min(32, int(settings.get("video_max_frames", "12"))))
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("video_timeout_seconds", "3600"))))
            memory_limit_mb = max(64, min(32_768, int(settings.get("video_memory_limit_mb", "2048"))))
            disk_limit_mb = max(64, min(32_768, int(settings.get("video_disk_limit_mb", "1024"))))
        except (TypeError, ValueError):
            maximum_frames = 12
            timeout_seconds = 3600.0
            memory_limit_mb = 2048
            disk_limit_mb = 1024
        limits = MediaProcessingLimits(
            timeout_seconds=timeout_seconds,
            maximum_memory_bytes=memory_limit_mb * 1024 * 1024,
            maximum_workspace_bytes=disk_limit_mb * 1024 * 1024,
        )
        self.videos.analyze(
            version_id=job["content_version_id"],
            artifact_sha256=job["artifact_sha256"],
            maximum_frames=maximum_frames,
            limits=limits,
            cancelled=lambda: self.repository.job_cancel_requested(job["id"]),
            heartbeat=lambda: self._heartbeat(job),
            progress=lambda value, message: self._update_video_progress(job, value, message),
        )
        if self.repository.job_cancel_requested(job["id"]):
            self._finish(job, "cancelled", "视频分析已取消")
            return
        if self._finish(job, "succeeded", "本地视频分析完成", progress=100):
            self.repository.set_version_completeness(job["content_version_id"], "complete")
            self.repository.update_processing(job["source_id"], "succeeded")

    def _update_video_progress(self, job: dict, progress: int, message: str) -> None:
        if not self.repository.update_job(job["id"], job["lease_token"], progress=progress, message=message):
            raise JobLeaseLost()

    def _video_download(self, job: dict) -> None:
        """Restricted link download flow (REQ-047): payload 校验 → 工具可用性 →
        per-job staging + Cookie 拷贝 → 回环过滤代理 → download → probe（含高度
        ≤1080）→ 容量预检 → artifact → 同事务 source/version/provenance 与
        video_analyze 入队 → 审计；任何失败路径不残留半成品 source。
        """
        if self.downloader is None or self.videos is None or self.imports is None:
            if self._finish(job, "blocked", "链接下载服务不可用", progress=100):
                return
            return
        try:
            payload = json.loads(job["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        url = payload.get("url")
        platform = payload.get("platform")
        rights = payload.get("rights")
        use_cookie = bool(payload.get("use_cookie"))
        if (
            not isinstance(url, str) or not url
            or not isinstance(platform, str) or platform not in {"bilibili", "douyin"}
            or rights not in {"owned", "authorized", "permitted", "open_license", "other"}
        ):
            raise DownloadInputInvalid("invalid_payload")
        if not self.downloader.capability().get("enabled"):
            raise DownloadUnavailable()
        settings = self.repository.get_settings()
        try:
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("download_timeout_seconds", "3600"))))
            no_progress_seconds = max(10.0, min(86_400.0, float(settings.get("download_no_progress_seconds", "10"))))
            disk_limit_mb = max(64, min(32_768, int(settings.get("download_disk_limit_mb", "2048"))))
            memory_limit_mb = max(64, min(32_768, int(settings.get("video_memory_limit_mb", "2048"))))
        except (TypeError, ValueError):
            timeout_seconds = 3600.0
            no_progress_seconds = 10.0
            disk_limit_mb = 2048
            memory_limit_mb = 2048
        limits = MediaProcessingLimits(
            timeout_seconds=timeout_seconds,
            maximum_memory_bytes=memory_limit_mb * 1024 * 1024,
            maximum_workspace_bytes=disk_limit_mb * 1024 * 1024,
        )
        workspace = self.artifacts.staging_path().with_suffix("")
        workspace.mkdir(parents=True, exist_ok=False)
        try:
            cookie_copy: Path | None = None
            if use_cookie:
                # 作业内只读取导入的 cookies.txt 并拷贝进 staging；原文件不被修改。
                cookie_source = self.artifacts.paths.download / "cookies.txt"
                try:
                    available = cookie_source.is_file() and cookie_source.stat().st_size <= 1024 * 1024
                except OSError:
                    available = False
                if not available:
                    raise DownloadInputInvalid("cookie")
                cookie_copy = workspace / "cookies.txt"
                shutil.copyfile(cookie_source, cookie_copy)
            # 观察窗口间隔由设置注入（端口签名不含该参数；单 worker 无竞争）。
            setattr(self.downloader, "no_progress_seconds", no_progress_seconds)
            self._update_video_progress(job, 5, "正在启动链接下载")
            result = self.downloader.download(
                url=url,
                platform=platform,
                workspace=workspace,
                limits=limits,
                use_cookie=use_cookie,
                cookie_path=cookie_copy,
                cancelled=lambda: self.repository.job_cancel_requested(job["id"]),
                heartbeat=lambda: self._heartbeat(job),
                progress=lambda value, message: self._update_video_progress(job, value, message),
            )
            if self.repository.job_cancel_requested(job["id"]):
                raise DownloadProcessingCancelled()
            candidate = workspace / result.filename
            if not candidate.is_file() or candidate.stat().st_size != result.byte_size:
                raise DownloadInputInvalid("product_missing")
            self._update_video_progress(job, 92, "正在校验下载产物")
            probe_limits = replace(limits, deadline_monotonic=time.monotonic() + limits.timeout_seconds)
            try:
                metadata = self.videos.analyzer.probe(
                    candidate,
                    probe_limits,
                    cancelled=lambda: self.repository.job_cancel_requested(job["id"]),
                    heartbeat=lambda: self._heartbeat(job),
                )
            except MediaProcessingCancelled:
                # probe 阶段的协作取消统一落"链接下载已取消"，不复用视频分析文案。
                raise DownloadProcessingCancelled() from None
            except MediaInputInvalid as exc:
                raise DownloadInputInvalid("product_invalid") from exc
            if metadata.height is not None and metadata.height > 1080:
                # 高度 ≤1080 后置断言：格式选择 + probe 双保险（REQ-047.9）。
                raise DownloadInputInvalid("height")
            self.artifacts.check_capacity(result.byte_size)
            capability = self.downloader.capability()
            url_sanitized = sanitize_download_url(url)
            # 标题优先级：用户显式提交 > 下载器捕获的平台标题 > "未命名视频"（落库侧回退）。
            title = str(payload.get("title") or "").strip() or str(getattr(result, "title", "") or "")
            self._update_video_progress(job, 96, "正在写入不可变 artifact")
            with candidate.open("rb") as stream:
                ingested = self.imports.downloaded_video(
                    stream,
                    result.byte_size,
                    platform=platform,
                    url_sanitized=url_sanitized,
                    yt_dlp_version=str(capability.get("version") or "unknown"),
                    format_profile=getattr(self.downloader, "format_profile", "res:1080+mp4-remux"),
                    cookie_used=use_cookie,
                    config_hash=self.downloader.config_hash(platform, getattr(self.downloader, "format_profile", "res:1080+mp4-remux")),
                    title=title,
                    author=payload.get("author") if isinstance(payload.get("author"), str) else None,
                    language=str(payload.get("language") or "zh"),
                    notes=payload.get("notes") if isinstance(payload.get("notes"), str) else None,
                    rights=rights,
                    categories=[item for item in payload.get("categories", []) if isinstance(item, str)],
                    tags=[item for item in payload.get("tags", []) if isinstance(item, str)],
                    source_date=payload.get("source_date") if isinstance(payload.get("source_date"), str) else None,
                    original_name=result.filename,
                    media_type=result.media_type,
                )
            self.repository.audit("video_download", ingested["source"]["id"], "succeeded")
            self._finish(job, "succeeded", "链接下载完成，已排入本地视频分析", progress=100)
        finally:
            # 作业结束（无论成败/取消）：回环代理由适配器随作业关闭；
            # staging 与 Cookie 拷贝在这里统一清理。
            shutil.rmtree(workspace, ignore_errors=True)

    def _parse(self, job: dict) -> None:
        if self.repository.job_cancel_requested(job["id"]):
            self._finish(job, "cancelled", "已取消")
            return
        payload = json.loads(job["payload_json"])
        settings = self.repository.get_settings()
        timeout_seconds = float(settings.get("parser_timeout_seconds", "86400"))
        no_progress_seconds = float(settings.get("parser_no_progress_seconds", "86400"))
        self._memory_limit_mb = int(settings.get("parser_memory_limit_mb", "2048"))
        self._disk_limit_mb = int(settings.get("parser_disk_limit_mb", "1024"))
        result = self.parse_runner(
            self.artifacts.artifact_path(job["artifact_sha256"]),
            payload["filename"],
            payload.get("media_type"),
            timeout_seconds,
            no_progress_seconds,
            lambda: self.repository.job_cancel_requested(job["id"]),
            lambda: self._heartbeat(job),
        )
        capability = self.parser.capability()
        if result.parser_name != "docling-local":
            reason = capability.get("unavailable_reason") or result.parser_name
            self.repository.audit("parser_fallback", job["content_version_id"], str(reason))
        if result.blocked_reason:
            state = "blocked" if result.blocked_reason == "awaiting_ocr" or "加密" in result.blocked_reason else "failed"
            if self._finish(job, state, result.blocked_reason, progress=100):
                self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                self.repository.update_processing(job["source_id"], "awaiting_ocr" if result.blocked_reason == "awaiting_ocr" else state)
            return
        if self.repository.job_cancel_requested(job["id"]):
            self._finish(job, "cancelled", "已取消")
            return
        chunks, evidence_items = self.documents.parsed_bundle(
            result.text, result.config_hash, result.format, result.segments
        )
        existing = self.repository.find_extraction_representation(job["content_version_id"], result.parser_name, result.config_hash)
        if existing is None or not self.repository.representation_bundle_complete(
            existing["id"],
            version_id=job["content_version_id"],
            artifact_sha256=job["artifact_sha256"],
            kind="extraction",
            parser_name=result.parser_name,
            config_hash=result.config_hash,
            text=result.text,
            chunks=chunks,
            evidence=evidence_items,
        ):
            with self.artifacts.operation():
                output = self.documents.record_parsed(
                    job["content_version_id"], job["artifact_sha256"], result.text,
                    result.parser_name, result.config_hash, result.format, result.segments,
                )
                representation = output["representation"]
        else:
            representation = existing
        if not self.repository.representation_bundle_complete(
            representation["id"],
            version_id=job["content_version_id"],
            artifact_sha256=job["artifact_sha256"],
            kind="extraction",
            parser_name=result.parser_name,
            config_hash=result.config_hash,
            text=result.text,
            chunks=chunks,
            evidence=evidence_items,
        ) or not self.artifacts.verify(job["artifact_sha256"]):
            raise RuntimeError("输出、证据、索引或 artifact 校验失败")
        if self._finish(job, "succeeded", "本地解析完成", progress=100):
            self.repository.set_version_completeness(job["content_version_id"], "complete")
            self.repository.update_processing(job["source_id"], "succeeded")
