"""Source soft-delete, restore and permanent purge."""

from __future__ import annotations

from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort


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
            try:
                for sha256 in orphaned:
                    self.artifacts.delete(sha256)
            except Exception:
                self.repository.audit("source_permanent_delete", source_id, "artifact_cleanup_failed")
                raise
        self.repository.audit("source_permanent_delete", source_id, "succeeded")
        return {"source_id": source_id, "purged": True, "unreferenced_artifacts_removed": len(orphaned)}
