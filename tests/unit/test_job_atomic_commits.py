"""Task 8（加固计划）：作业终态与业务结果的原子提交 + 最终租约栅栏。

- commit_job_success 在同一事务内写入：job 终态（含租约栅栏 WHERE）、
  版本完整性、来源处理状态、链式后继作业、settings、attempt 结束、审计；
- 栅栏条件 = state='running' AND lease_token 匹配 AND 租约未过期；
  租约丢失（接管/过期）时晚到提交必须整体无效并返回 False；
- 事务中途失败（如后继作业外键冲突）不留下任何部分提交；
- 心跳失败时取消事件被置位，协作 runner 立即感知。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.sqlite import SqliteRepository
from app.domain.identity import derived_identifier
from app.services.jobs import JobLeaseLost, JobService


ARTIFACT = "a" * 64


def _repo(tmp_path: Path) -> SqliteRepository:
    repository = SqliteRepository(tmp_path / "knowledge.db")
    repository.initialize()
    return repository


def _ingest(repository: SqliteRepository) -> tuple[dict, dict]:
    source, version, _ = repository.create_ingest(
        source_type="paste", title="t", author=None, language="zh", notes=None,
        rights="owned", domains=[], genres=[], tags=[], artifact_sha256=ARTIFACT,
        original_name="p.md", media_type="text/markdown", byte_size=8,
        job_payload={"filename": "p.md"}, priority=100, audit_event="paste_import",
    )
    return source, version


def _claimed_job(repository: SqliteRepository, kind: str = "video_analyze") -> tuple[dict, dict, dict]:
    source, version = _ingest(repository)
    repository.create_job(kind, source["id"], version["id"], ARTIFACT, None, {})
    claimed = repository.claim_next_job()
    assert claimed is not None
    return claimed, source, version


def test_commit_job_success_applies_all_effects(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    claimed, source, version = _claimed_job(repository)
    child_id = derived_identifier("job", claimed["id"], "video_transcribe")

    ok = repository.commit_job_success(
        claimed["id"], claimed["lease_token"],
        message="本地视频分析完成",
        version_id=version["id"], completeness="complete",
        source_id=source["id"], processing="succeeded",
        child_jobs=[{
            "kind": "video_transcribe", "source_id": source["id"], "version_id": version["id"],
            "artifact_sha256": ARTIFACT, "config_hash": "c", "payload": {},
            "priority": 100, "job_id": child_id,
        }],
    )

    assert ok is True
    job = repository.get_job(claimed["id"])
    assert job["state"] == "succeeded" and job["message"] == "本地视频分析完成"
    assert job["lease_token"] is None
    assert repository.get_version(version["id"])["completeness"] == "complete"
    assert repository.get_source(source["id"])["processing_state"] == "succeeded"
    child = repository.get_job(child_id)
    assert child is not None and child["state"] == "queued"


def test_commit_job_success_rejected_with_wrong_token(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    claimed, source, version = _claimed_job(repository)

    ok = repository.commit_job_success(
        claimed["id"], "wrong-token",
        message="x",
        version_id=version["id"], completeness="complete",
        source_id=source["id"], processing="succeeded",
    )

    assert ok is False
    assert repository.get_job(claimed["id"])["state"] == "running"
    assert repository.get_version(version["id"])["completeness"] == "pending"


def test_commit_job_success_rejected_after_lease_takeover(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    claimed, source, version = _claimed_job(repository)
    stale_token = claimed["lease_token"]
    # 强制租约过期并回收：新 worker 以新 token 接管。
    with repository.connection() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (claimed["id"],),
        )
    takeover = repository.claim_next_job()
    assert takeover is not None and takeover["lease_token"] != stale_token

    ok = repository.commit_job_success(
        claimed["id"], stale_token,
        message="晚到的旧 worker 结果",
        version_id=version["id"], completeness="complete",
        source_id=source["id"], processing="succeeded",
    )

    assert ok is False, "旧 worker 的晚到提交必须被最终栅栏拒绝"
    current = repository.get_job(claimed["id"])
    assert current["state"] == "running" and current["lease_token"] == takeover["lease_token"]
    assert repository.get_version(version["id"])["completeness"] == "pending"


def test_commit_job_success_double_commit_rejected(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    claimed, source, version = _claimed_job(repository)
    assert repository.commit_job_success(claimed["id"], claimed["lease_token"], message="一次")
    assert repository.commit_job_success(claimed["id"], claimed["lease_token"], message="二次") is False


def test_commit_job_success_transaction_failure_leaves_nothing(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    claimed, source, version = _claimed_job(repository)

    with pytest.raises(Exception):
        repository.commit_job_success(
            claimed["id"], claimed["lease_token"],
            message="x",
            version_id=version["id"], completeness="complete",
            source_id=source["id"], processing="succeeded",
            child_jobs=[{
                "kind": "video_transcribe", "source_id": "missing-source", "version_id": "missing-version",
                "artifact_sha256": ARTIFACT, "config_hash": "c", "payload": {},
                "priority": 100, "job_id": derived_identifier("job", claimed["id"], "video_transcribe"),
            }],
        )

    job = repository.get_job(claimed["id"])
    assert job["state"] == "running" and job["lease_token"] == claimed["lease_token"]
    assert repository.get_version(version["id"])["completeness"] == "pending"
    assert repository.get_source(source["id"])["processing_state"] == "queued"


class _StubRepository:
    """最小存根：心跳必失败，用于验证取消事件被置位。"""

    backend = "sqlite"

    def get_settings(self) -> dict[str, str]:
        return {}

    def touch_job(self, job_id: str, lease_token: str) -> bool:
        return False


def test_heartbeat_failure_sets_cancel_event(tmp_path: Path) -> None:
    import threading

    service = JobService(_StubRepository(), artifacts=None, documents=None, parser=object())
    # 默认租约 300 秒会把首次心跳推到 30 秒观察窗之外；缩短到 3 秒。
    monkeypatch_lease = lambda: 3
    service._lease_seconds = monkeypatch_lease  # type: ignore[method-assign]
    event = threading.Event()
    observed = {"cancelled_during_run": False}

    def runner() -> None:
        for _ in range(200):
            if event.is_set():
                observed["cancelled_during_run"] = True
                return
            threading.Event().wait(0.01)

    with pytest.raises(JobLeaseLost):
        service._run_with_lease_heartbeat({"id": "j", "lease_token": "t"}, runner, cancel_event=event)

    # 事件在心跳失败瞬间置位；给 runner 线程最多 1 秒轮询到它。
    for _ in range(100):
        if observed["cancelled_during_run"]:
            break
        threading.Event().wait(0.01)
    assert observed["cancelled_during_run"] is True
    assert event.is_set()
