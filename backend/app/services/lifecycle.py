"""Source soft-delete, restore and permanent purge."""

from __future__ import annotations

from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort


class ArtifactCleanupPending(RuntimeError):
    """逻辑 purge 已提交，但物理 artifact 清理未完成；任务保留待重试。"""


class LifecycleService:
    def __init__(self, repository: RepositoryPort, artifacts: ArtifactStoragePort) -> None:
        self.repository = repository
        self.artifacts = artifacts

    def delete(self, source_id: str) -> dict:
        source = self.repository.soft_delete_source(source_id)
        if source is None:
            raise KeyError("来源不存在")
        self.repository.audit("source_soft_delete", source_id, "succeeded")
        return source

    def restore(self, source_id: str) -> dict:
        source = self.repository.restore_source(source_id)
        if source is None:
            raise KeyError("来源不存在")
        self.repository.audit("source_restore", source_id, "succeeded")
        return source

    def purge(self, source_id: str) -> dict:
        source = self.repository.get_source(source_id, include_deleted=True)
        if source is None:
            raise KeyError("来源不存在")
        if source["deleted_at"] is None:
            raise ValueError("必须先软删除来源")
        # The intent is durable before destructive state changes. If filesystem
        # cleanup fails, a content-free failure record makes it diagnosable.
        self.repository.audit("source_permanent_delete", source_id, "started")
        with self.artifacts.operation():
            orphaned = self.repository.purge_source(source_id)
        # 逻辑提交后执行幂等 sweeper（加固计划 Task 11）：unlink 失败保留
        # 清理任务并抛 ArtifactCleanupPending（端点映射 503），任务由启动
        # 重试与人工重试继续消化；绝不留下无登记的孤儿文件。
        pending = self.sweep_cleanup_tasks()
        self.repository.audit("source_permanent_delete", source_id, "succeeded")
        return {
            "source_id": source_id,
            "purged": True,
            "unreferenced_artifacts_removed": len(orphaned),
            "artifact_cleanup_pending": pending,
        }

    def sweep_cleanup_tasks(self) -> int:
        """幂等清理队列 sweeper：返回仍处于 pending 的任务数。

        文件不存在视为成功；unlink 失败保留任务（attempt+1）并计数，
        成功审计只在物理清理完成后写入。
        """
        failed = 0
        for task in self.repository.artifact_cleanup_pending():
            sha256 = task["sha256"]
            try:
                self.artifacts.delete(sha256)
            except Exception:
                self.repository.fail_artifact_cleanup(sha256)
                failed += 1
                continue
            self.repository.complete_artifact_cleanup(sha256)
            self.repository.audit("artifact_cleanup", sha256, "succeeded")
        if failed:
            raise ArtifactCleanupPending(f"{failed} 个 artifact 清理任务未完成，将在启动重试中继续")
        return 0
