"""Durable single-worker job execution."""

from __future__ import annotations

import json
from typing import Callable

from app.adapters.parsers import parse_local
from app.adapters.sqlite import SqliteRepository
from app.adapters.storage import ArtifactStore
from app.services.documents import DocumentService


class JobService:
    def __init__(
        self,
        repository: SqliteRepository,
        artifacts: ArtifactStore,
        documents: DocumentService,
        backup_runner: Callable[[], dict] | None = None,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.documents = documents
        self.backup_runner = backup_runner

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
        except Exception:
            # No exception detail is persisted because it may include source paths or content.
            state = "retry_wait" if job["attempt_count"] < job["max_attempts"] else "failed"
            self.repository.update_job(job_id, state=state, message="本地处理失败", done=state == "failed")
            if job["source_id"] and state == "failed":
                self.repository.update_processing(job["source_id"], "failed")
        return self.repository.get_job(job_id)

    def _parse(self, job: dict) -> None:
        if self.repository.job_cancel_requested(job["id"]):
            self.repository.update_job(job["id"], state="cancelled", message="已取消", done=True)
            return
        payload = json.loads(job["payload_json"])
        result = parse_local(self.artifacts.artifact_path(job["artifact_sha256"]), payload["filename"], payload.get("media_type"))
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
        output = self.documents.record_parsed(job["content_version_id"], job["artifact_sha256"], result.text, result.parser_name, result.config_hash, result.format)
        indexed = bool(self.repository.search_chunks_for_representation(output["representation"]["id"]))
        if not output["evidence"]["id"] or not indexed or not self.artifacts.verify(job["artifact_sha256"]):
            raise RuntimeError("输出、证据、索引或 artifact 校验失败")
        self.repository.set_version_completeness(job["content_version_id"], "complete")
        self.repository.update_processing(job["source_id"], "succeeded")
        self.repository.update_job(job["id"], state="succeeded", progress=100, message="本地解析完成", done=True)
