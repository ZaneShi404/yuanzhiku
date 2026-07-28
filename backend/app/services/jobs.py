"""Durable single-worker job execution."""

from __future__ import annotations

import json
import multiprocessing
import queue
import time
from pathlib import Path
from typing import Callable

from app.adapters.parsers import ParsedDocument, parse_local
from app.ports.repository import RepositoryPort
from app.adapters.storage import ArtifactStore
from app.services.documents import DocumentService


def _parse_worker(result_queue, artifact_path: str, filename: str, media_type: str | None) -> None:
    try:
        result_queue.put(("result", parse_local(Path(artifact_path), filename, media_type)))
    except BaseException:
        # Parent deliberately keeps parser details out of durable logs/messages.
        result_queue.put(("error", None))


class ParserCircuitBreaker(RuntimeError):
    pass


class ParserCancelled(RuntimeError):
    pass


class JobService:
    def __init__(
        self,
        repository: RepositoryPort,
        artifacts: ArtifactStore,
        documents: DocumentService,
        backup_runner: Callable[[], dict] | None = None,
        parse_runner: Callable[[Path, str, str | None, float, float, Callable[[], bool], Callable[[], None]], ParsedDocument] | None = None,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.documents = documents
        self.backup_runner = backup_runner
        self.parse_runner = parse_runner or self._run_parser_with_circuit_breakers

    def run_once(self) -> dict | None:
        job = self.repository.claim_next_job()
        if job is None:
            return None
        job_id = job["id"]
        try:
            if self.repository.job_cancel_requested(job_id):
                self.repository.update_job(job_id, state="cancelled", message="已在执行前取消", done=True)
                return self.repository.get_job(job_id)
            if job["kind"] == "parse":
                self._parse(job)
            elif job["kind"] == "backup":
                if self.backup_runner is None:
                    self.repository.update_job(job_id, state="blocked", message="备份服务不可用", done=True)
                else:
                    self.backup_runner()
                    self.repository.update_job(job_id, state="succeeded", progress=100, message="备份完成", done=True)
            else:
                self.repository.update_job(job_id, state="failed", message="未知作业类型", done=True)
        except ParserCancelled:
            self.repository.update_job(job_id, state="cancelled", message="解析已取消", done=True)
        except ParserCircuitBreaker as exc:
            self.repository.set_version_completeness(job["content_version_id"], "incomplete")
            self.repository.update_processing(job["source_id"], "failed")
            self.repository.update_job(job_id, state="failed", message=str(exc), done=True)
        except Exception:
            # No exception detail is persisted because it may include source paths or content.
            state = "retry_wait" if job["attempt_count"] < job["max_attempts"] else "failed"
            self.repository.update_job(job_id, state=state, message="本地处理失败", done=state == "failed")
            if job["source_id"] and state == "failed":
                self.repository.update_processing(job["source_id"], "failed")
        return self.repository.get_job(job_id)

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
        """Execute local parsing in a child process that can be stopped safely."""
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(target=_parse_worker, args=(result_queue, str(artifact_path), filename, media_type), daemon=True)
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

    def _parse(self, job: dict) -> None:
        if self.repository.job_cancel_requested(job["id"]):
            self.repository.update_job(job["id"], state="cancelled", message="已取消", done=True)
            return
        payload = json.loads(job["payload_json"])
        settings = self.repository.get_settings()
        timeout_seconds = float(settings.get("parser_timeout_seconds", "86400"))
        no_progress_seconds = float(settings.get("parser_no_progress_seconds", "86400"))
        result = self.parse_runner(
            self.artifacts.artifact_path(job["artifact_sha256"]),
            payload["filename"],
            payload.get("media_type"),
            timeout_seconds,
            no_progress_seconds,
            lambda: self.repository.job_cancel_requested(job["id"]),
            lambda: self.repository.touch_job(job["id"]),
        )
        if result.parser_name != "docling-local":
            self.repository.audit("parser_fallback", job["content_version_id"], result.parser_name)
        if result.blocked_reason:
            state = "blocked" if result.blocked_reason == "awaiting_ocr" or "加密" in result.blocked_reason else "failed"
            self.repository.set_version_completeness(job["content_version_id"], "incomplete")
            self.repository.update_processing(job["source_id"], "awaiting_ocr" if result.blocked_reason == "awaiting_ocr" else state)
            self.repository.update_job(job["id"], state=state, progress=100, message=result.blocked_reason, done=True)
            return
        if self.repository.job_cancel_requested(job["id"]):
            self.repository.update_job(job["id"], state="cancelled", message="已取消", done=True)
            return
        output = self.documents.record_parsed(job["content_version_id"], job["artifact_sha256"], result.text, result.parser_name, result.config_hash, result.format, result.segments)
        indexed = bool(self.repository.search_chunks_for_representation(output["representation"]["id"]))
        if not output["evidence"]["id"] or not indexed or not self.artifacts.verify(job["artifact_sha256"]):
            raise RuntimeError("输出、证据、索引或 artifact 校验失败")
        self.repository.set_version_completeness(job["content_version_id"], "complete")
        self.repository.update_processing(job["source_id"], "succeeded")
        self.repository.update_job(job["id"], state="succeeded", progress=100, message="本地解析完成", done=True)
