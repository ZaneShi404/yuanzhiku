"""Task 11（加固计划）：purge 的持久化 artifact 清理队列。

- purge 事务内：删除 artifact catalog 行**之前**先插入 cleanup task；
- 逻辑提交后执行幂等 sweeper：文件不存在视为成功；unlink 失败保留任务
  （attempt+1）并向上返回 503 artifact_cleanup_pending；
- 启动时以低优先级 artifact_cleanup 作业重试未完成任务；成功审计只在
  物理清理完成后写入；
- 共享 artifact（仍有其他引用）不产生清理任务、文件不受影响。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.sqlite import SqliteRepository
from app.adapters.storage import ArtifactStore
from app.core.config import data_paths
from app.main import create_app
from app.services.lifecycle import LifecycleService


def _ingest(repository: SqliteRepository, artifacts: ArtifactStore, payload: bytes, title: str) -> tuple[dict, dict]:
    import io

    stored = artifacts.store_stream(io.BytesIO(payload))
    source, version, _ = repository.create_ingest(
        source_type="paste", title=title, author=None, language="zh", notes=None,
        rights="owned", domains=[], genres=[], tags=[], artifact_sha256=stored.sha256,
        original_name="p.md", media_type="text/markdown", byte_size=stored.byte_size,
        job_payload={"filename": "p.md"}, priority=100, audit_event="paste_import",
    )
    return source, version


def _setup(tmp_path: Path) -> tuple[SqliteRepository, ArtifactStore, LifecycleService]:
    paths = data_paths(tmp_path)
    repository = SqliteRepository(paths.database)
    repository.initialize()
    artifacts = ArtifactStore(paths)
    return repository, artifacts, LifecycleService(repository, artifacts)


def test_cleanup_table_created_by_initialize(tmp_path: Path) -> None:
    repository, _, _ = _setup(tmp_path)
    with repository.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(artifact_cleanup_tasks)")}
    assert {"sha256", "source_id", "reason", "state", "attempt_count", "created_at", "updated_at"} <= columns


def test_purge_sweeps_file_and_audits_inline(tmp_path: Path) -> None:
    repository, artifacts, lifecycle = _setup(tmp_path)
    source, version = _ingest(repository, artifacts, "唯一引用的内容".encode("utf-8"), "来源一")
    repository.soft_delete_source(source["id"])

    result = lifecycle.purge(source["id"])

    assert result["purged"] is True
    # 成功路径：逻辑提交后内联 sweeper 立即消化任务——文件删除、任务完成、审计落库。
    assert repository.artifact_cleanup_pending() == []
    assert not artifacts.artifact_path(version["artifact_sha256"]).exists()
    assert repository.get_version(version["id"]) is None
    with repository.connection() as connection:
        events = connection.execute(
            "SELECT * FROM audit_events WHERE event_type='artifact_cleanup'"
        ).fetchall()
    assert len(events) == 1 and events[0]["entity_id"] == version["artifact_sha256"]


def test_unlink_failure_keeps_task_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, artifacts, lifecycle = _setup(tmp_path)
    source, version = _ingest(repository, artifacts, "内容丙".encode("utf-8"), "来源三")
    repository.soft_delete_source(source["id"])
    sha256 = version["artifact_sha256"]

    real_delete = ArtifactStore.delete

    def failing_delete(self: ArtifactStore, target: str) -> None:
        if target == sha256:
            raise OSError("injected unlink failure")
        real_delete(self, target)

    monkeypatch.setattr(ArtifactStore, "delete", failing_delete)
    # purge 内联 sweeper 失败：任务保留（attempt+1）、文件绝不丢。
    with pytest.raises(RuntimeError):
        lifecycle.purge(source["id"])
    tasks = repository.artifact_cleanup_pending()
    assert len(tasks) == 1 and tasks[0]["attempt_count"] == 1
    assert artifacts.artifact_path(sha256).is_file(), "失败路径不得丢文件"

    monkeypatch.setattr(ArtifactStore, "delete", real_delete)
    lifecycle.sweep_cleanup_tasks()
    assert repository.artifact_cleanup_pending() == []
    assert not artifacts.artifact_path(sha256).exists()


def test_missing_file_counts_as_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, artifacts, lifecycle = _setup(tmp_path)
    source, version = _ingest(repository, artifacts, "内容乙".encode("utf-8"), "来源二")
    repository.soft_delete_source(source["id"])
    sha256 = version["artifact_sha256"]

    real_delete = ArtifactStore.delete

    def failing_delete(self: ArtifactStore, target: str) -> None:
        if target == sha256:
            raise OSError("injected unlink failure")
        real_delete(self, target)

    monkeypatch.setattr(ArtifactStore, "delete", failing_delete)
    with pytest.raises(RuntimeError):
        lifecycle.purge(source["id"])
    assert len(repository.artifact_cleanup_pending()) == 1

    # 文件已消失（如外部清理）→ sweeper 视为成功并完成任务。
    monkeypatch.setattr(ArtifactStore, "delete", real_delete)
    artifacts.artifact_path(sha256).unlink(missing_ok=True)
    lifecycle.sweep_cleanup_tasks()
    assert repository.artifact_cleanup_pending() == []


def test_startup_retries_pending_tasks_with_low_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, artifacts, lifecycle = _setup(tmp_path)
    source, version = _ingest(repository, artifacts, "待恢复清理".encode("utf-8"), "来源四")
    repository.soft_delete_source(source["id"])
    sha256 = version["artifact_sha256"]

    real_delete = ArtifactStore.delete

    def failing_delete(self: ArtifactStore, target: str) -> None:
        if target == sha256:
            raise OSError("injected unlink failure")
        real_delete(self, target)

    monkeypatch.setattr(ArtifactStore, "delete", failing_delete)
    with pytest.raises(RuntimeError):
        lifecycle.purge(source["id"])
    assert len(repository.artifact_cleanup_pending()) == 1

    monkeypatch.setattr(ArtifactStore, "delete", real_delete)
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app):
        jobs = [job for job in app.state.services.repository.list_jobs() if job["kind"] == "artifact_cleanup"]
        assert len(jobs) == 1 and jobs[0]["state"] == "queued" and jobs[0]["priority"] < 0

        # 单 worker 处理后任务完成、文件被清理。
        processed = app.state.services.jobs.run_once()
        while processed is not None and processed["kind"] != "artifact_cleanup":
            processed = app.state.services.jobs.run_once()
        assert processed is not None and processed["state"] == "succeeded"
    assert repository.artifact_cleanup_pending() == []
    assert not artifacts.artifact_path(sha256).exists()


def test_shared_artifact_not_queued(tmp_path: Path) -> None:
    repository, artifacts, lifecycle = _setup(tmp_path)
    payload = "两个来源共享同一内容寻址文件".encode("utf-8")
    source_one, version_one = _ingest(repository, artifacts, payload, "来源甲")
    source_two, _ = _ingest(repository, artifacts, payload, "来源乙")
    repository.soft_delete_source(source_one["id"])

    lifecycle.purge(source_one["id"])

    assert repository.artifact_cleanup_pending() == [], "共享 artifact 不得进入清理队列"
    assert artifacts.artifact_path(version_one["artifact_sha256"]).is_file()


