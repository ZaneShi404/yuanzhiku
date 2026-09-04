"""Durable single-worker job execution."""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing
import os
import queue
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from app.adapters.media import transcript_anchor_points
from app.adapters.video_ai import SHEET_CELL_MAX_WIDTH, SHEET_PROMPT_VERSION, build_contact_sheet
from app.domain.media import MediaProcessingLimits, MediaTranscriptSegment, video_time_range_locator
from app.domain.identity import derived_identifier
from app.domain.models import TAXONOMY_DOMAIN_VALUES, TAXONOMY_GENRE_VALUES, sanitize_download_url
from app.domain.parsing import ParsedDocument
from app.ports.parser import DocumentParserPort
from app.ports.media import (
    DownloadInputInvalid,
    DownloadProcessingCancelled,
    DownloadUnavailable,
    ImageInputInvalid,
    MediaAiPort,
    MediaAiUnavailable,
    MediaDownloaderPort,
    MediaInputInvalid,
    MediaProcessingCancelled,
    MediaToolUnavailable,
)
from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort
from app.services.audio import extract_audio_chunks
from app.services.documents import DocumentService
from app.services.images import ImageService
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
    except Exception:
        # psutil.NoSuchProcess（子进程恰好退出的 TOCTOU 竞态）继承 Exception
        # 而非 OSError；视为不可测得，落入下方 ctypes 回退（已退出 PID 的
        # OpenProcess 返回空句柄 → None，行为安全）。
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


# REQ-033a：转写/摘要/分类是分析或解析之后的附加产物，其成败绝不触碰
# 版本完整性与来源处理状态。
AI_JOB_KINDS = {"video_transcribe", "video_summarize", "source_classify"}

# v1.7（REQ-056.1）：视频入库时转写作业以更高优先级与视频分析同事务入队，
# 单 worker 按 priority DESC 领取，保证转写先于分析执行（转写引导抽帧）。
TRANSCRIBE_INGEST_PRIORITY = 110


def _select_transcriber(
    engine: str,
    local: Any | None,
    api: Any | None,
) -> tuple[Any, str, str | None] | None:
    """按 ai_transcriber_engine 选转写适配器；返回 (适配器, 引擎名, 降级原因)。

    auto：本地优先、API 兜底（本地运行时失败由作业内降级重转处理，
    原因记入作业消息与表示身份）。不可用时返回 None（作业 blocked）。
    """
    if engine == "api":
        if api is not None and api.capability().get("enabled"):
            return api, "api", None
        return None
    if engine == "local":
        if local is not None and local.capability().get("enabled"):
            return local, "local", None
        return None
    if local is not None and local.capability().get("enabled"):
        return local, "local", None
    if api is not None and api.capability().get("enabled"):
        return api, "api", "local_unavailable"
    return None


def _format_ms(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _summary_text(
    result: dict[str, Any],
    assessment: dict[str, Any],
    frame_descriptions: list[dict[str, Any]] | None,
    tier: int | float,
    visual_gap: bool,
    applied: bool,
    *,
    video_direct: bool = False,
    degraded_reason: str | None = None,
    frame_fallback: bool = False,
    enriched: bool = False,
) -> str:
    """摘要表示正文：摘要 + 完整性附录 + 补充理解附录 + 建议标记行（前端解析用）。"""
    lines = [str(result["summary"]).strip(), "", "---"]
    verdict = "可能不完整" if assessment.get("verdict") == "likely_incomplete" else "内容完整"
    confidence = assessment.get("confidence")
    confidence_text = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "-"
    lines.append(f"完整性判断：{verdict}（置信度 {confidence_text}）")
    missing = [str(item) for item in assessment.get("missing_aspects") or []]
    lines.append(f"缺失方面：{'、'.join(missing) if missing else '无'}")
    reason = str(assessment.get("reason") or "").strip()
    if reason:
        lines.append(f"判断依据：{reason}")
    if frame_fallback:
        lines.append("补充理解方式：视频直送不可行，已按关键帧联络表补充画面理解")
    elif enriched:
        lines.append("补充理解方式：画面理解增强（关键帧联络表）")
    elif degraded_reason:
        lines.append(f"补充理解方式：{degraded_reason}")
    elif video_direct:
        lines.append("补充理解方式：视频直送多模态模型")
    if frame_descriptions:
        lines.extend(["", "画面理解："])
        for item in frame_descriptions:
            description = str(item.get("description") or "").strip()
            visible_text = str(item.get("visible_text") or "").strip()
            line = f"- [{_format_ms(int(item.get('time_ms') or 0))}] {description or '（无描述）'}"
            if visible_text:
                line += f"（画面文字：{visible_text}）"
            lines.append(line)
    marker = json.dumps(
        {
            "domains": result.get("suggested_domains") or [],
            "genres": result.get("suggested_genres") or [],
            "tags": result.get("suggested_tags") or [],
            "tier": tier,
            "visual_gap": visual_gap,
            "video_direct": video_direct,
            "frame_fallback": frame_fallback,
            "enriched": enriched,
            "applied": applied,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    lines.append(f"<!--yuanzhiku:suggestions {marker} -->")
    return "\n".join(lines)


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
        images: ImageService | None = None,
        media_ai: MediaAiPort | None = None,
        stt_manager: Any | None = None,
        transcribers: dict[str, Any] | None = None,
        video_adapter_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.documents = documents
        self.videos = videos
        self.imports = imports
        self.downloader = downloader
        self.images = images
        self.media_ai = media_ai
        self.stt_manager = stt_manager
        self.transcribers = transcribers or {}
        self.video_adapter_provider = video_adapter_provider
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
            elif job["kind"] == "image_analyze":
                self._image_analyze(job)
            elif job["kind"] == "stt_model_download":
                self._stt_model_download(job)
            elif job["kind"] == "video_download":
                self._video_download(job)
            elif job["kind"] == "video_transcribe":
                self._video_transcribe(job)
            elif job["kind"] == "video_summarize":
                self._video_summarize(job)
            elif job["kind"] == "source_classify":
                self._source_classify(job)
            elif job["kind"] == "artifact_cleanup":
                self._artifact_cleanup(job)
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
                    if not self.repository.commit_job_success(
                        job["id"], job["lease_token"], message="备份完成", settings=settings,
                    ):
                        raise JobLeaseLost()
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
                    if not self.repository.commit_job_success(
                        job["id"], job["lease_token"], message="空闲完整性抽样完成", settings=settings,
                    ):
                        raise JobLeaseLost()
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
                if job["kind"] in AI_JOB_KINDS:
                    # REQ-033a：附加 AI 作业的取消不影响既有版本完整性。
                    self._finish(job, "cancelled", "媒体 AI 处理已取消")
                else:
                    message = "图片分析已取消" if job["kind"] == "image_analyze" else "视频分析已取消"
                    if self._finish(job, "cancelled", message):
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
        except ImageInputInvalid:
            try:
                self._finish(job, "failed", "本地图片无法分析")
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
                    if job["source_id"] and state == "failed" and job["kind"] not in AI_JOB_KINDS:
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

    def _run_with_lease_heartbeat(
        self,
        job: dict,
        runner: Callable[[], object],
        cancel_event: threading.Event | None = None,
    ) -> object:
        """Run non-cancel work while renewing the claim at a bounded interval.

        心跳失败（租约丢失/接管）时置位 cancel_event 通知协作 runner 提前
        退出，并抛 JobLeaseLost；不合作的线程即使稍后返回，其结果的最终
        提交也会被 commit_job_success 的租约栅栏拒绝（加固计划 Task 8）。
        """
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
        try:
            while worker.is_alive():
                worker.join(timeout=interval)
                if worker.is_alive():
                    self._heartbeat(job)
        except JobLeaseLost:
            if cancel_event is not None:
                cancel_event.set()
            raise
        if failure:
            raise failure[0]
        return result[0] if result else None

    def _cancel_predicate(self, job: dict, event: threading.Event) -> Callable[[], bool]:
        return lambda: event.is_set() or self.repository.job_cancel_requested(job["id"])

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
        workspace = self.artifacts.staging_workspace("parse")
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

    def _transcription_ranges(self, transcription: dict) -> list[tuple[int, int]]:
        """转写表示证据中的 video_time_range 定位（毫秒起止，升序）。"""
        ranges: list[tuple[int, int]] = []
        for row in self.repository.evidence_for_representation(transcription["id"]):
            try:
                locator = json.loads(row["locator_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(locator, dict) and locator.get("type") == "video_time_range":
                ranges.append((max(0, int(locator.get("start_ms") or 0)), max(0, int(locator.get("end_ms") or 0))))
        ranges.sort()
        return ranges

    def _sheet_cell_windows(
        self, version_id: str, transcription: dict, sheet_frames: int, duration_ms: int,
    ) -> tuple[list[tuple[int, int]], str | None]:
        """联络表格子时间窗（REQ-057.1，决策 25）：候选 = 当前分析帧时间 ∪
        转写语义锚点（段边界/静音中点），相邻 <1s 合并，超上限按时间均匀抽稀；
        窗格为 [t_i, t_{i+1})（末格到片尾）——格子即证据时间范围。超限截断
        返回注明文本（威胁模型行 3：不静默，P2-2 处置）。"""
        ranges = self._transcription_ranges(transcription)
        effective_duration = duration_ms or max((end for _, end in ranges), default=0)
        if effective_duration <= 0:
            return [], None
        times: set[int] = set()
        analysis = self.repository.video_analysis_for_version(version_id)
        if analysis is not None:
            for frame in analysis.get("frames") or []:
                try:
                    time_ms = int(frame["time_ms"])
                except (TypeError, ValueError, KeyError):
                    continue
                if 0 < time_ms < effective_duration:
                    times.add(time_ms)
        for anchor, _ in transcript_anchor_points(ranges, effective_duration):
            times.add(anchor)
        ordered = sorted(times)
        merged: list[int] = []
        for time_ms in ordered:
            if not merged or time_ms - merged[-1] >= 1_000:
                merged.append(time_ms)
        truncation_note: str | None = None
        if len(merged) > sheet_frames:
            truncation_note = f"联络表候选 {len(merged)} 点超出上限 {sheet_frames} 格，已按上限截断"
            span = len(merged) - 1
            picked: list[int] = []
            for index in range(sheet_frames):
                position = round(index * span / (sheet_frames - 1)) if sheet_frames > 1 else 0
                if not picked or position != len(picked) - 1:
                    picked.append(merged[position])
            merged = picked
        windows = [
            (merged[index], merged[index + 1]) for index in range(len(merged) - 1)
        ]
        if merged:
            windows.append((merged[-1], effective_duration))
        return windows, truncation_note

    def _run_frame_understanding(
        self,
        job: dict,
        adapter: Any,
        transcription: dict,
        transcript_text: str,
        cancelled: Callable[[], bool],
        cancel_event: threading.Event,
        duration_ms: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """联络表帧理解（REQ-057.1，决策 25/27）：构建缩略图网格 → 单次多模态
        调用 → 带窗格时间定位的画面条目。不可行/失败返回 ([], 注明)（作业层
        按原降级语义记录），绝不伪造条目；瞬态缩略图随作业 staging 清理，绝不
        入 video_frames/artifact。截断等事项以注明文本返回（P2-2 处置）。"""
        try:
            sheet_frames = max(8, min(48, int(self.repository.get_settings().get("ai_video_sheet_frames", "24"))))
        except (TypeError, ValueError):
            sheet_frames = 24
        cells, truncation_note = self._sheet_cell_windows(job["content_version_id"], transcription, sheet_frames, duration_ms)
        if not cells:
            return [], None
        self._update_video_progress(job, 30, "正在构建关键帧联络表")
        settings = self.repository.get_settings()
        try:
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("ai_timeout_seconds", "300"))))
        except (TypeError, ValueError):
            timeout_seconds = 300.0
        try:
            memory_limit_mb = max(64, min(32_768, int(settings.get("video_memory_limit_mb", "2048"))))
            disk_limit_mb = max(64, min(32_768, int(settings.get("video_disk_limit_mb", "1024"))))
        except (TypeError, ValueError):
            memory_limit_mb, disk_limit_mb = 2048, 1024
        limits = MediaProcessingLimits(
            timeout_seconds=timeout_seconds,
            maximum_memory_bytes=memory_limit_mb * 1024 * 1024,
            maximum_workspace_bytes=disk_limit_mb * 1024 * 1024,
            deadline_monotonic=time.monotonic() + timeout_seconds,
        )
        workspace = self.artifacts.staging_workspace("video_summarize")
        try:
            sheet_image = build_contact_sheet(
                self.artifacts.artifact_path(job["artifact_sha256"]),
                [start for start, _ in cells],
                workspace,
                limits,
                cancelled,
                ffmpeg=os.environ.get("YUANZHIKU_FFMPEG_BIN", "ffmpeg"),
                heartbeat=lambda: self._heartbeat(job),
            )
            self._update_video_progress(job, 45, "正在理解关键帧画面")
            entries = self._run_with_lease_heartbeat(
                job,
                lambda: adapter.understand_frames(sheet_image, cells, transcript_text, cancelled),
                cancel_event,
            )
            return entries, truncation_note
        except MediaProcessingCancelled:
            raise
        except Exception:
            if cancelled():
                raise MediaProcessingCancelled() from None
            return [], truncation_note
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _persist_visual_understanding(
        self,
        job: dict,
        transcription: dict,
        entries: list[dict[str, Any]],
        parser_name: str,
        config_hash: str,
    ) -> None:
        """画面理解条目落库（REQ-057.4，决策 27）：独立 visual_understanding
        表示（父链挂转写），逐条 video_time_range 证据，进入全文检索。"""
        lines: list[str] = []
        evidence: list[dict] = []
        for entry in entries:
            start_ms = max(0, int(entry.get("start_ms") or 0))
            end_ms = max(start_ms + 1, int(entry.get("end_ms") or start_ms + 1))
            description = str(entry.get("description") or "").strip()
            if not description:
                continue
            line = f"- [{_format_ms(start_ms)}–{_format_ms(end_ms)}] {description}"
            visible_text = str(entry.get("visible_text") or "").strip()
            if visible_text:
                line += f"（画面文字：{visible_text}）"
            lines.append(line)
            excerpt = description[:300]
            evidence.append({
                "locator": video_time_range_locator(start_ms, end_ms),
                "excerpt": excerpt,
                "excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "is_validated": True,
            })
        if not lines:
            return
        text = "画面理解（关键帧联络表）：\n" + "\n".join(lines)
        self.repository.persist_representation_bundle(
            version_id=job["content_version_id"],
            artifact_sha256=job["artifact_sha256"],
            kind="visual_understanding",
            parser_name=parser_name,
            config_hash=config_hash,
            text=text,
            parent_id=transcription["id"],
            chunks=self.documents.search_chunk_pairs(text),
            evidence=evidence,
            representation_id=derived_identifier("representation", job["id"], "visual_understanding"),
        )

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
        # v1.7（REQ-056.2，决策 24）：读取同版本转写表示做锚点融合——语音说了
        # 什么的时刻值得看画面。无转写表示（未跑/失败/blocked）时按纯信号策略
        # 抽帧，行为与 v1.6 一致；分析本身保持零网络。
        transcription = self._latest_representation(job["content_version_id"], "transcription")
        transcript_segments: list[tuple[int, int]] = []
        transcript_source: str | None = None
        if transcription is not None:
            transcript_segments = self._transcription_ranges(transcription)
            # P2-1 处置（独立复核 2026-09-04）：分析身份纳入转写表示的唯一身份
            # （representation id）——同引擎重转产生不同转写内容时身份随之更新，
            # 重分析绝不与既有分析共享帧集而触发同键帧不一致。
            transcript_source = transcription.get("id")
        self.videos.analyze(
            version_id=job["content_version_id"],
            artifact_sha256=job["artifact_sha256"],
            maximum_frames=maximum_frames,
            limits=limits,
            cancelled=lambda: self.repository.job_cancel_requested(job["id"]),
            heartbeat=lambda: self._heartbeat(job),
            progress=lambda value, message: self._update_video_progress(job, value, message),
            transcript_segments=transcript_segments,
            transcript_source=transcript_source,
        )
        if self.repository.job_cancel_requested(job["id"]):
            self._finish(job, "cancelled", "视频分析已取消")
            return
        # 最终租约栅栏（加固计划 Task 8）：完整性与来源状态、链式后继作业
        # 与作业终态在同一事务内提交；租约丢失时整体无效。
        # v1.7（REQ-056.1）：入库已按入队矩阵同事务入队转写（更高优先级先
        # 执行）。此处仅兜底补链——无转写表示且转写器可用（如入库后才配置
        # 转写）时补入队转写；已有转写表示时链式摘要。转写仍在排队/运行中
        # 时两者皆不链：转写成功路径自身会链式摘要，避免摘要先行终态失败。
        child_jobs: list[dict[str, Any]] = []
        if self._auto_pipeline_enabled() and job["content_version_id"]:
            transcription = self._latest_representation(job["content_version_id"], "transcription")
            if transcription is None:
                if self._transcriber_available():
                    child_jobs = self._chained_child_if_due("video_transcribe", job)
            elif self.media_ai is not None and self.media_ai.capability().get("understand_enabled"):
                child_jobs = self._chained_child_if_due("video_summarize", job)
        # REQ-056.2：无转写表示（未跑/失败/blocked）时退化为纯信号抽帧，作业
        # 消息注明，不静默（P2-2 处置，独立复核 2026-09-04）。
        message = "本地视频分析完成" if transcription is not None else "转写不可用，已按场景感知策略抽帧"
        if not self.repository.commit_job_success(
            job["id"], job["lease_token"],
            message=message,
            version_id=job["content_version_id"], completeness="complete",
            source_id=job["source_id"], processing="succeeded",
            child_jobs=child_jobs,
        ):
            raise JobLeaseLost()

    def _chained_child_job(self, kind: str, job: dict) -> dict[str, Any]:
        operation = {"video_transcribe": "transcribe", "video_summarize": "summarize", "source_classify": "classify"}[kind]
        return {
            "kind": kind,
            "source_id": job["source_id"],
            "version_id": job["content_version_id"],
            "artifact_sha256": job["artifact_sha256"],
            "config_hash": self.media_ai.config_hash(operation) if self.media_ai else None,
            "payload": {},
            "priority": 100,
            "job_id": derived_identifier("job", job["id"], kind),
        }

    def _update_video_progress(self, job: dict, progress: int, message: str) -> None:
        if not self.repository.update_job(job["id"], job["lease_token"], progress=progress, message=message):
            raise JobLeaseLost()

    def _latest_representation(self, version_id: str, kind: str) -> dict | None:
        matches = [
            item for item in self.repository.representations_for_version(version_id)
            if item["kind"] == kind
        ]
        return matches[-1] if matches else None

    def _auto_pipeline_enabled(self) -> bool:
        return self.repository.get_settings().get("ai_auto_pipeline", "on") == "on"

    def video_transcribe_extra_job(self) -> tuple[str, int] | None:
        """v1.7 入库入队矩阵（REQ-056.1，决策 23）：auto_pipeline 开且任一转写
        路径可用时，返回与 video_analyze 同事务入队的转写作业 (kind, priority)；
        否则返回 None（auto 关 = 保持「分析自动、转写手动」语义；转写器不可用
        = 分析退化为纯信号抽帧路径）。"""
        if self._auto_pipeline_enabled() and self._transcriber_available():
            return ("video_transcribe", TRANSCRIBE_INGEST_PRIORITY)
        return None

    def _chained_child_if_due(self, kind: str, job: dict) -> list[dict[str, Any]]:
        """构造链式后继作业行（加固计划 Task 7/8）：同版本同 kind 已有排队/
        运行中作业时不重复入队（REQ-051 语义）；否则返回确定性 child 行，
        由调用方在 commit_job_success 同事务写入。"""
        for existing in self.repository.list_jobs():
            if (
                existing["kind"] == kind
                and existing["content_version_id"] == job["content_version_id"]
                and existing["state"] in {"queued", "running", "retry_wait"}
            ):
                return []
        return [self._chained_child_job(kind, job)]

    def _enqueue_chained(self, kind: str, job: dict) -> None:
        for child in self._chained_child_if_due(kind, job):
            self.repository.create_job(
                child["kind"], child["source_id"], child["version_id"], child["artifact_sha256"],
                child["config_hash"], child["payload"], priority=child["priority"], job_id=child["job_id"],
            )

    def _apply_classification(self, source_id: str, suggestions: dict[str, Any]) -> None:
        """AI 分类建议自动写入来源元数据，只填空缺：领域/体裁仅在当前为空时写入，
        标签取并集合并；用户已填字段绝不覆盖。无可填内容时不写库、不审计。
        """
        source = self.repository.get_source(source_id)
        if source is None:
            return
        try:
            current_domains = json.loads(source["domains_json"])
            current_genres = json.loads(source["genres_json"])
            current_tags = json.loads(source["tags_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        domains = sorted({item for item in suggestions.get("domains") or [] if isinstance(item, str)})
        genres = sorted({item for item in suggestions.get("genres") or [] if isinstance(item, str)})[:1]
        tags = sorted({item for item in suggestions.get("tags") or [] if isinstance(item, str)})
        values: dict[str, str] = {}
        if not current_domains and domains:
            values["domains_json"] = json.dumps(domains, ensure_ascii=False)
        if not current_genres and genres:
            values["genres_json"] = json.dumps(genres, ensure_ascii=False)
        merged_tags = sorted(set(current_tags) | set(tags))
        if tags and merged_tags != sorted(set(current_tags)):
            values["tags_json"] = json.dumps(merged_tags, ensure_ascii=False)
        if not values:
            return
        self.repository.update_source_metadata(source_id, values)
        # 审计只记写入字段与数量，绝不记建议内容本身。
        self.repository.audit(
            "ai_classify_applied", source_id,
            " ".join(f"{key.removesuffix('_json')}={len(json.loads(value))}" for key, value in sorted(values.items())),
        )

    def _transcriber_available(self) -> bool:
        """任一转写路径可用（本地模型已下载或转写组已配置，REQ-051 修订）。"""
        return any(
            transcriber is not None and bool(transcriber.capability().get("enabled"))
            for transcriber in self.transcribers.values()
        )

    def _transcriber_parser_name(self, transcriber: Any, engine_used: str) -> str:
        if engine_used == "local":
            capability = transcriber.capability()
            return f"local-funasr-{capability.get('model') or 'paraformer-zh'}"
        settings = self.repository.get_settings()
        return (
            f"ai-{settings.get('ai_transcribe_provider', 'off')}"
            f"-{settings.get('ai_transcribe_model', '').strip() or 'whisper-1'}"
        )

    def _stt_model_download(self, job: dict) -> None:
        """本地转写模型显式下载（REQ-054.3，决策 19）：校验后启用，失败脱敏可重试。"""
        if self.stt_manager is None:
            self._finish(job, "blocked", "本地转写模型管理服务不可用", progress=100)
            return
        try:
            payload = json.loads(job["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict) or payload.get("action") != "download":
            self._finish(job, "failed", "本地转写模型操作无效")
            return
        self._update_video_progress(job, 10, "正在下载本地转写模型")
        cancel_event = threading.Event()
        try:
            self._run_with_lease_heartbeat(
                job,
                lambda: self.stt_manager.download(
                    cancelled=self._cancel_predicate(job, cancel_event),
                    heartbeat=lambda: self._heartbeat(job),
                ),
                cancel_event,
            )
        except RuntimeError as exc:
            if self.repository.job_cancel_requested(job["id"]):
                self._finish(job, "cancelled", "本地转写模型下载已取消")
            else:
                self._finish(job, "failed", str(exc))
            return
        if self.repository.job_cancel_requested(job["id"]):
            self._finish(job, "cancelled", "本地转写模型下载已取消")
            return
        if not self.repository.commit_job_success(
            job["id"], job["lease_token"],
            message="本地转写模型下载完成",
            audit_event="stt_model_download",
        ):
            raise JobLeaseLost()

    def _video_transcribe(self, job: dict) -> None:
        """语音转写双路径（REQ-054）：本地 FunASR 默认，auto 失败降级 API。

        音轨提取为作业内独立子步骤（决策 18），本地/远程路径共用；
        降级事实写入表示的 parser_name/config_hash 与作业消息（决策 15/20）。
        REQ-033a：成功/失败/取消均不改版本完整性与来源处理状态。
        """
        if not self.transcribers:
            self._finish(job, "blocked", "语音转写服务不可用", progress=100)
            return
        local = self.transcribers.get("local")
        api = self.transcribers.get("api")
        settings = self.repository.get_settings()
        engine = settings.get("ai_transcriber_engine", "auto")
        cancel_event = threading.Event()
        cancelled = self._cancel_predicate(job, cancel_event)
        self._update_video_progress(job, 5, "正在提取音轨")
        artifact_path = self.artifacts.artifact_path(job["artifact_sha256"])
        workspace = self.artifacts.staging_workspace("video_transcribe")
        try:
            try:
                timeout_seconds = max(60.0, min(86_400.0, float(settings.get("stt_timeout_seconds", "3600"))))
            except (TypeError, ValueError):
                timeout_seconds = 3600.0
            try:
                memory_limit_mb = max(64, min(32_768, int(settings.get("stt_memory_limit_mb", "2048"))))
            except (TypeError, ValueError):
                memory_limit_mb = 2048
            try:
                disk_limit_mb = max(64, min(32_768, int(settings.get("stt_disk_limit_mb", "1024"))))
            except (TypeError, ValueError):
                disk_limit_mb = 1024
            limits = MediaProcessingLimits(
                timeout_seconds=timeout_seconds,
                maximum_memory_bytes=memory_limit_mb * 1024 * 1024,
                maximum_workspace_bytes=disk_limit_mb * 1024 * 1024,
            )
            selection = _select_transcriber(engine, local, api)
            if selection is None:
                self._finish(
                    job, "blocked",
                    "未配置任何可用转写路径：请下载本地转写模型或配置转写 API",
                    progress=100,
                )
                return
            transcriber, engine_used, fallback_reason = selection
            chunks = extract_audio_chunks(artifact_path, workspace, limits, cancelled)
            transcript = None
            try:
                transcript = self._run_with_lease_heartbeat(
                    job, lambda: transcriber.transcribe(chunks, cancelled), cancel_event
                )
            except Exception:
                if cancelled():
                    raise
                if not (engine == "auto" and engine_used == "local" and api is not None and api.capability().get("enabled")):
                    raise
                self._update_video_progress(job, 30, "本地转写不可用，正在降级使用 API 转写")
                transcript = self._run_with_lease_heartbeat(
                    job, lambda: api.transcribe(chunks, cancelled), cancel_event
                )
                transcriber = api
                engine_used = "api"
                fallback_reason = "local_failed"
            if cancelled():
                self._finish(job, "cancelled", "媒体 AI 处理已取消")
                return
            if not transcript.text.strip():
                raise RuntimeError("语音转写未返回可用文本")
            segments = list(transcript.segments)
            if not segments:
                segments = [MediaTranscriptSegment(transcript.text.strip(), 0, 1000)]
            self._update_video_progress(job, 80, "正在写入转写表示与证据")
            evidence: list[dict] = []
            for segment in segments:
                start_ms = max(0, int(segment.start_ms))
                end_ms = max(start_ms + 1, int(segment.end_ms))
                excerpt = segment.text[:300]
                evidence.append({
                    "locator": video_time_range_locator(start_ms, end_ms),
                    "excerpt": excerpt,
                    "excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                    "is_validated": True,
                })
            self.repository.persist_representation_bundle(
                version_id=job["content_version_id"],
                artifact_sha256=job["artifact_sha256"],
                kind="transcription",
                parser_name=self._transcriber_parser_name(transcriber, engine_used),
                config_hash=transcriber.config_hash(),
                text=transcript.text,
                parent_id=None,
                chunks=self.documents.search_chunk_pairs(transcript.text),
                evidence=evidence,
                representation_id=derived_identifier("representation", job["id"], "transcription"),
            )
            message = "语音转写完成"
            if fallback_reason:
                message = "本地转写不可用，已使用 API 转写"
            # 最终租约栅栏：转写完成与链式摘要作业同事务提交（Task 8）。
            children = (
                self._chained_child_if_due("video_summarize", job)
                if self._auto_pipeline_enabled() and self.media_ai is not None and self.media_ai.capability().get("understand_enabled")
                else []
            )
            if not self.repository.commit_job_success(job["id"], job["lease_token"], message=message, child_jobs=children):
                raise JobLeaseLost()
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _video_summarize(self, job: dict) -> None:
        """内容摘要（REQ-017）：转写 → 完整性判断 → 分层（tier1/tier2）→ 摘要表示。

        无转写时终态失败"请先完成语音转写"（不进重试循环）；REQ-033a 同转写。
        """
        if self.media_ai is None or not self.media_ai.capability().get("understand_enabled"):
            self._finish(job, "blocked", "未配置媒体 AI 服务", progress=100)
            return
        cancel_event = threading.Event()
        cancelled = self._cancel_predicate(job, cancel_event)
        try:
            payload = json.loads(job["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        force_tier2 = bool(payload.get("force_tier2")) if isinstance(payload, dict) else False
        version_id = job["content_version_id"]
        transcription = self._latest_representation(version_id, "transcription")
        if transcription is None:
            self._finish(job, "failed", "请先完成语音转写", progress=100)
            return
        transcript_text = transcription["text_content"]
        source = self.repository.get_source(job["source_id"]) if job["source_id"] else None
        analysis = self.repository.video_analysis_for_version(version_id)
        duration_ms = 0
        if analysis is not None:
            try:
                duration_ms = max(0, int(json.loads(analysis["metadata_json"]).get("duration_ms") or 0))
            except (TypeError, ValueError, json.JSONDecodeError):
                duration_ms = 0
        # 最长静音：转写证据时间范围之间的最大空档（含首尾）。
        ranges = self._transcription_ranges(transcription)
        max_silence_ms = 0
        cursor = 0
        for start_ms, end_ms in ranges:
            max_silence_ms = max(max_silence_ms, start_ms - cursor)
            cursor = max(cursor, end_ms)
        if duration_ms > cursor:
            max_silence_ms = max(max_silence_ms, duration_ms - cursor)
        context = {
            "title": (source or {}).get("title") or "",
            "notes": (source or {}).get("notes") or "",
            "duration_ms": duration_ms,
            "coverage_chars_per_sec": len(transcript_text) / (duration_ms / 1000) if duration_ms > 0 else 0.0,
            "max_silence_ms": max_silence_ms,
        }
        self._update_video_progress(job, 15, "正在判断内容完整性")
        assessment = self._run_with_lease_heartbeat(
            job, lambda: self.media_ai.assess_completeness(transcript_text, context), cancel_event
        )
        if cancelled():
            self._finish(job, "cancelled", "媒体 AI 处理已取消")
            return
        want_direct = force_tier2 or assessment.get("verdict") == "likely_incomplete"
        settings = self.repository.get_settings()
        frames_fallback_enabled = settings.get("ai_video_frames_fallback", "on") == "on"
        frames_enrich_enabled = settings.get("ai_video_frames_enrich", "off") == "on"
        adapter = self.video_adapter_provider() if self.video_adapter_provider else None
        image_input = bool(adapter is not None and adapter.capability().get("image_input"))
        video_direct = False
        degraded_reason: str | None = None
        direct_result: dict[str, Any] | None = None
        visual_entries: list[dict[str, Any]] = []
        frame_fallback = False
        enriched = False
        notes: list[str] = []
        if frames_enrich_enabled and not image_input:
            # REQ-057.6：增强开启但供应商不具备图像输入 → 跳过并在作业消息注明
            # （P2-2 处置，不静默）。
            notes.append("画面增强已开启但供应商不具备图像输入，已跳过")
        if want_direct:
            # 三级补充理解（REQ-055.2 v1.7 修订，决策 26，用户裁定 2026-09-04）：
            # ① 视频直送（主路径，偏差 A 三合一）→ ② 联络表帧理解兜底（REQ-057.2）
            # → ③ visual_gap（直送与帧理解皆不可行才标记）。偏差 B（2026-08-16
            # 彻底移除关键帧视觉路径）由本版有意识修订。
            if adapter is not None and adapter.capability().get("video_input"):
                self._update_video_progress(job, 30, "正在直送视频给多模态模型")
                try:
                    direct_result = self._run_with_lease_heartbeat(
                        job,
                        lambda: adapter.understand_video(
                            self.artifacts.artifact_path(job["artifact_sha256"]),
                            transcript_text,
                            (source or {}).get("title") or "",
                            cancelled,
                        ),
                        cancel_event,
                    )
                    video_direct = True
                except MediaAiUnavailable:
                    degraded_reason = "视频直送不可行，画面信息未补充"
                except RuntimeError:
                    if cancelled():
                        self._finish(job, "cancelled", "媒体 AI 处理已取消")
                        return
                    degraded_reason = "视频直送失败，画面信息未补充"
            else:
                degraded_reason = "未配置视频直送，画面信息未补充"
            if not video_direct and frames_fallback_enabled and image_input:
                entries, sheet_note = self._run_frame_understanding(
                    job, adapter, transcription, transcript_text, cancelled, cancel_event, duration_ms,
                )
                if sheet_note:
                    notes.append(sheet_note)
                if entries:
                    visual_entries = entries
                    frame_fallback = True
                    degraded_reason = None
                else:
                    degraded_reason = f"{degraded_reason or '视频直送不可行，画面信息未补充'}（关键帧画面理解兜底亦未成功）"
        elif frames_enrich_enabled and image_input:
            # v1.7 帧理解增强（REQ-057.3）：转写完整时的可选画面补充（tier 1.5），
            # 开关关闭或帧理解不可行时行为与 v1.6 tier1 完全一致。
            entries, sheet_note = self._run_frame_understanding(
                job, adapter, transcription, transcript_text, cancelled, cancel_event, duration_ms,
            )
            if sheet_note:
                notes.append(sheet_note)
            if entries:
                visual_entries = entries
                enriched = True
            else:
                notes.append("画面增强已开启但关键帧画面理解未成功")
        visual_gap = bool(want_direct and not video_direct and not frame_fallback)
        self._update_video_progress(job, 65, "正在生成内容摘要")
        if video_direct:
            assert direct_result is not None
            result = {
                "summary": direct_result["summary"],
                "suggested_domains": direct_result.get("suggested_domains") or [],
                "suggested_genres": direct_result.get("suggested_genres") or [],
                "suggested_tags": direct_result.get("suggested_tags") or [],
            }
            frame_descriptions = direct_result.get("supplements") or []
        else:
            summary_inputs: dict[str, Any] = {
                "transcript_text": transcript_text,
                "title": (source or {}).get("title") or "",
                "taxonomy_domains": list(TAXONOMY_DOMAIN_VALUES),
                "taxonomy_genres": list(TAXONOMY_GENRE_VALUES),
            }
            if visual_entries:
                # v1.7（REQ-057）：画面理解条目作为纯文本合成的参照输入。
                summary_inputs["visual_entries"] = [
                    f"[{_format_ms(int(entry['time_ms']))}–{_format_ms(int(entry['end_ms']))}] {entry['description']}"
                    + (f"（画面文字：{entry['visible_text']}）" if entry.get("visible_text") else "")
                    for entry in visual_entries
                ]
            result = self._run_with_lease_heartbeat(
                job,
                lambda: self.media_ai.summarize(summary_inputs, cancelled),
                cancel_event,
            )
            frame_descriptions = visual_entries or None
        if cancelled():
            self._finish(job, "cancelled", "媒体 AI 处理已取消")
            return
        self._update_video_progress(job, 85, "正在写入摘要表示")
        tier = 2 if (video_direct or frame_fallback) else (1.5 if enriched else 1)
        chat_model = settings.get("ai_chat_model", "").strip() or "qwen-plus"
        video_provider = settings.get("ai_video_provider", "off")
        video_model = settings.get("ai_video_model", "").strip() or "default"
        parser_name = f"ai-{settings.get('ai_understand_provider', 'off')}-{chat_model}"
        if video_direct:
            parser_name += f"+video-{video_provider}-{video_model}"
        elif frame_fallback or enriched:
            parser_name += f"+frames-{video_provider}-{video_model}"
        config_hash = self.media_ai.config_hash("summarize")
        if video_direct or frame_fallback or enriched:
            if adapter is not None:
                extra = f"{adapter.config_hash()}" if video_direct else (
                    f"sheet:{SHEET_PROMPT_VERSION}:{SHEET_CELL_MAX_WIDTH}:{settings.get('ai_video_sheet_frames', '24')}"
                )
                config_hash = hashlib.sha256(f"{config_hash}:{extra}".encode("utf-8")).hexdigest()
        if visual_entries:
            self._persist_visual_understanding(
                job, transcription, visual_entries, parser_name=f"sheet-{video_provider}-{video_model}", config_hash=config_hash,
            )
        text = _summary_text(
            result, assessment, frame_descriptions, tier, visual_gap, applied=True,
            video_direct=video_direct, degraded_reason=degraded_reason,
            frame_fallback=frame_fallback, enriched=enriched,
        )
        # 摘要针对全片内容：证据以整段 video_time_range 定位（REQ-016 同转写纪律）。
        summary_end_ms = duration_ms or max((end for _, end in ranges), default=0) or 1000
        excerpt = str(result["summary"])[:300]
        evidence = [{
            "locator": video_time_range_locator(0, max(1, summary_end_ms)),
            "excerpt": excerpt,
            "excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "is_validated": True,
        }]
        self.repository.persist_representation_bundle(
            version_id=version_id,
            artifact_sha256=job["artifact_sha256"],
            kind="summary",
            parser_name=parser_name,
            config_hash=config_hash,
            text=text,
            parent_id=transcription["id"],
            chunks=self.documents.search_chunk_pairs(text),
            evidence=evidence,
            representation_id=derived_identifier("representation", job["id"], "summary"),
        )
        # AI 建议自动写入来源元数据（REQ-051 修订，只填空缺），用户可事后修改。
        if job["source_id"]:
            self._apply_classification(job["source_id"], {
                "domains": result.get("suggested_domains") or [],
                "genres": result.get("suggested_genres") or [],
                "tags": result.get("suggested_tags") or [],
            })
        message = "内容摘要完成"
        if notes:
            message = f"内容摘要完成（{'；'.join(notes)}）"
        if not self.repository.commit_job_success(job["id"], job["lease_token"], message=message):
            raise JobLeaseLost()

    def _source_classify(self, job: dict) -> None:
        """文档/粘贴 AI 分类（REQ-051 修订）：正文发理解组产出领域/体裁/标签建议，
        按只填空缺规则自动写入来源元数据。REQ-033a 同转写：成败/取消均不改
        版本完整性与来源处理状态。
        """
        if self.media_ai is None or not self.media_ai.capability().get("understand_enabled"):
            self._finish(job, "blocked", "未配置媒体 AI 服务", progress=100)
            return
        cancel_event = threading.Event()
        cancelled = self._cancel_predicate(job, cancel_event)
        version_id = job["content_version_id"]
        extraction = self._latest_representation(version_id, "extraction")
        if extraction is None or not str(extraction["text_content"] or "").strip():
            self._finish(job, "failed", "没有可用于分类的正文", progress=100)
            return
        source = self.repository.get_source(job["source_id"]) if job["source_id"] else None
        # 出网正文截断到前 8000 字符，控制发送体量（适配器侧同上限兜底）。
        text = str(extraction["text_content"]).strip()[:8000]
        self._update_video_progress(job, 20, "正在 AI 分类")
        suggestions = self._run_with_lease_heartbeat(
            job,
            lambda: self.media_ai.classify(
                text,
                {
                    "title": (source or {}).get("title") or "",
                    "taxonomy_domains": list(TAXONOMY_DOMAIN_VALUES),
                    "taxonomy_genres": list(TAXONOMY_GENRE_VALUES),
                },
            ),
            cancel_event,
        )
        if cancelled():
            self._finish(job, "cancelled", "媒体 AI 处理已取消")
            return
        if job["source_id"]:
            self._apply_classification(job["source_id"], suggestions)
        if not self.repository.commit_job_success(job["id"], job["lease_token"], message="AI 分类完成"):
            raise JobLeaseLost()

    def _artifact_cleanup(self, job: dict) -> None:
        """清理队列重试作业（加固计划 Task 11）：幂等 sweeper。

        文件不存在视为成功；unlink 失败保留任务（attempt+1）并使作业失败
        可重试；全部完成后作业成功。成功审计在物理清理完成后由 sweeper 写入。
        """
        if self.videos is None and self.artifacts is None:
            self._finish(job, "blocked", "清理队列服务不可用", progress=100)
            return
        self._update_video_progress(job, 10, "正在重试 artifact 清理")
        pending = self.repository.artifact_cleanup_pending()
        if not pending:
            self._finish(job, "succeeded", "没有待清理的 artifact 任务", progress=100)
            return
        completed = 0
        failed = 0
        for task in pending:
            sha256 = task["sha256"]
            try:
                self.artifacts.delete(sha256)
            except Exception:
                self.repository.fail_artifact_cleanup(sha256)
                failed += 1
                continue
            self.repository.complete_artifact_cleanup(sha256)
            self.repository.audit("artifact_cleanup", sha256, "succeeded")
            completed += 1
            self._update_video_progress(
                job, min(95, 10 + int(85 * (completed + failed) / max(1, len(pending)))),
                "正在重试 artifact 清理",
            )
        if failed:
            self._finish(job, "failed", "部分 artifact 清理未完成，可重试", progress=100)
            return
        self._finish(job, "succeeded", f"artifact 清理完成（{completed} 项）", progress=100)

    def _image_analyze(self, job: dict) -> None:
        if self.images is None:
            if self._finish(job, "blocked", "本地图片分析服务不可用", progress=100):
                self.repository.set_version_completeness(job["content_version_id"], "incomplete")
                self.repository.update_processing(job["source_id"], "blocked")
            return
        settings = self.repository.get_settings()
        try:
            timeout_seconds = max(60.0, min(86_400.0, float(settings.get("image_timeout_seconds", "3600"))))
            memory_limit_mb = max(64, min(32_768, int(settings.get("image_memory_limit_mb", "2048"))))
            disk_limit_mb = max(64, min(32_768, int(settings.get("image_disk_limit_mb", "1024"))))
        except (TypeError, ValueError):
            timeout_seconds = 3600.0
            memory_limit_mb = 2048
            disk_limit_mb = 1024
        limits = MediaProcessingLimits(
            timeout_seconds=timeout_seconds,
            maximum_memory_bytes=memory_limit_mb * 1024 * 1024,
            maximum_workspace_bytes=disk_limit_mb * 1024 * 1024,
        )
        self.images.analyze(
            version_id=job["content_version_id"],
            artifact_sha256=job["artifact_sha256"],
            limits=limits,
            cancelled=lambda: self.repository.job_cancel_requested(job["id"]),
            heartbeat=lambda: self._heartbeat(job),
            progress=lambda value, message: self._update_video_progress(job, value, message),
        )
        if self.repository.job_cancel_requested(job["id"]):
            self._finish(job, "cancelled", "图片分析已取消")
            return
        if not self.repository.commit_job_success(
            job["id"], job["lease_token"],
            message="本地图片分析完成",
            version_id=job["content_version_id"], completeness="complete",
            source_id=job["source_id"], processing="succeeded",
        ):
            raise JobLeaseLost()

    def _video_download(self, job: dict) -> None:
        """Restricted link download flow (REQ-047): payload 校验 → 工具可用性 →
        per-job staging + Cookie 拷贝 → 回环过滤代理 → download → probe（含分辨率
        档位 ≤1080p：短边 ≤1080 且长边 ≤1920，决策 12）→ 容量预检 → artifact →
        同事务 source/version/provenance 与
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
        workspace = self.artifacts.staging_workspace("video_download")
        cancel_event = threading.Event()
        cancelled = self._cancel_predicate(job, cancel_event)
        try:
            cookie_copy: Path | None = None
            if use_cookie:
                # 作业内只读取该平台已导入的 Cookie 文件并拷贝进 staging；原文件不被修改。
                cookie_source = self.artifacts.paths.download_cookie_file(platform)
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
            if metadata.width is not None and metadata.height is not None and (
                min(metadata.width, metadata.height) > 1080 or max(metadata.width, metadata.height) > 1920
            ):
                # 分辨率档位 ≤1080p 后置断言（决策 12）：短边 ≤1080 且长边 ≤1920，
                # 竖屏 1080×1920 属 1080p 档位，2K/4K 拒绝——格式选择 + probe 双保险
                # （REQ-047.9）。宽高任一为 None 时不判定：probe 已保证二者存在。
                raise DownloadInputInvalid("resolution")
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
                    domains=[item for item in payload.get("domains", []) if isinstance(item, str)],
                    genres=[item for item in payload.get("genres", []) if isinstance(item, str)],
                    tags=[item for item in payload.get("tags", []) if isinstance(item, str)],
                    source_date=payload.get("source_date") if isinstance(payload.get("source_date"), str) else None,
                    original_name=result.filename,
                    media_type=result.media_type,
                    source_id=derived_identifier("source", job["id"], "source"),
                    version_id=derived_identifier("source", job["id"], "version"),
                    extra_job=self.video_transcribe_extra_job(),
                )
            self.repository.audit("video_download", ingested["source"]["id"], "succeeded")
            # 最终租约栅栏（Task 8）：来源/版本/provenance/后继作业已由
            # create_ingest 同事务写入；此处仅提交作业终态本身。
            if not self.repository.commit_job_success(
                job["id"], job["lease_token"],
                message="链接下载完成，已排入语音转写与本地分析",
            ):
                raise JobLeaseLost()
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
        # 最终租约栅栏（Task 8）：解析完成、完整性与来源状态、链式分类作业
        # 同事务提交。
        children = (
            self._chained_child_if_due("source_classify", job)
            if (
                self.media_ai is not None
                and self._auto_pipeline_enabled()
                and self.media_ai.capability().get("understand_enabled")
            )
            else []
        )
        if not self.repository.commit_job_success(
            job["id"], job["lease_token"],
            message="本地解析完成",
            version_id=job["content_version_id"], completeness="complete",
            source_id=job["source_id"], processing="succeeded",
            child_jobs=children,
        ):
            raise JobLeaseLost()
