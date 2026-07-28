"""Source soft-delete, restore and permanent purge."""

from __future__ import annotations

from app.ports.repository import RepositoryPort
from app.adapters.storage import ArtifactStore


class LifecycleService:
    def __init__(self, repository: RepositoryPort, artifacts: ArtifactStore) -> None:
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
        orphaned = self.repository.purge_source(source_id)
        for sha256 in orphaned:
            self.artifacts.delete(sha256)
        self.repository.audit("source_permanent_delete", source_id, "succeeded")
        return {"source_id": source_id, "purged": True, "unreferenced_artifacts_removed": len(orphaned)}
