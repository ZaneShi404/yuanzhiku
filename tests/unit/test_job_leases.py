from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.adapters.sqlite import SqliteRepository
from app.core.config import data_paths
from app.domain.models import PasteImportRequest
from app.main import create_app
from app.services.jobs import JobService


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "job-leases"


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    shutil.rmtree(root, ignore_errors=True)


def test_expired_claim_is_fenced_from_heartbeat_and_completion(runtime_root: Path) -> None:
    repository = SqliteRepository(data_paths(runtime_root).database)
    repository.initialize()
    created = repository.create_job("backup", None, None, None, None, {}, priority=1)
    first_claim = repository.claim_next_job()
    assert first_claim is not None
    first_token = first_claim["lease_token"]

    with repository.connection() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), created["id"]),
        )

    second_claim = repository.claim_next_job()
    assert second_claim is not None
    assert second_claim["id"] == created["id"]
    assert second_claim["lease_token"] != first_token
    assert repository.touch_job(created["id"], first_token) is False
    assert repository.update_job(
        created["id"], first_token, state="succeeded", message="stale", done=True
    ) is False

    current = repository.get_job(created["id"])
    assert current is not None
    assert current["state"] == "running"
    assert current["lease_token"] == second_claim["lease_token"]
    with repository.connection() as connection:
        attempts = connection.execute(
            "SELECT attempt_number,state,outcome,ended_at FROM job_attempts WHERE job_id=? ORDER BY attempt_number",
            (created["id"],),
        ).fetchall()
    assert len(attempts) == 2
    assert dict(attempts[0])["attempt_number"] == 1
    assert dict(attempts[0])["state"] == "failed"
    assert dict(attempts[0])["outcome"] == "lease_expired"
    assert dict(attempts[0])["ended_at"] is not None
    assert dict(attempts[1]) == {
        "attempt_number": 2,
        "state": "running",
        "outcome": None,
        "ended_at": None,
    }


def test_retryable_failure_closes_attempt_and_allows_configured_retries(runtime_root: Path) -> None:
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    services.repository.update_settings({"max_retry_attempts": 2})
    imported = services.imports.paste(PasteImportRequest(title="retry", text="retry text", rights="owned"))

    def fail_parser(*_args, **_kwargs):
        raise RuntimeError("injected")

    worker = JobService(
        services.repository,
        services.artifacts,
        services.documents,
        parse_runner=fail_parser,
    )
    first = worker.run_once()
    second = worker.run_once()
    third = worker.run_once()

    assert first is not None and first["state"] == "retry_wait" and first["retry_count"] == 0
    assert second is not None and second["state"] == "retry_wait" and second["retry_count"] == 1
    assert third is not None and third["state"] == "failed" and third["retry_count"] == 2
    with services.repository.connection() as connection:
        attempts = connection.execute(
            "SELECT attempt_number,state,outcome,ended_at FROM job_attempts WHERE job_id=? ORDER BY attempt_number",
            (imported["job"]["id"],),
        ).fetchall()
    assert [item["attempt_number"] for item in attempts] == [1, 2, 3]
    assert all(item["state"] == "failed" for item in attempts)
    assert all(item["outcome"] == "retryable_failure" for item in attempts)
    assert all(item["ended_at"] is not None for item in attempts)
    version = services.repository.get_version(imported["content_version"]["id"])
    assert version is not None and version["completeness"] == "incomplete"


def test_manual_retry_resets_retry_budget_and_execution_lease(runtime_root: Path) -> None:
    repository = SqliteRepository(data_paths(runtime_root).database)
    repository.initialize()
    repository.update_settings({"max_retry_attempts": 0})
    created = repository.create_job("backup", None, None, None, None, {})
    claimed = repository.claim_next_job()
    assert claimed is not None
    assert repository.update_job(claimed["id"], claimed["lease_token"], state="failed", done=True)

    retried = repository.retry_job(created["id"])
    assert retried is not None
    assert retried["state"] == "queued"
    assert retried["retry_count"] == 0
    assert retried["lease_token"] is None
    assert retried["lease_expires_at"] is None
    assert retried["started_at"] is None
    assert retried["completed_at"] is None

    repository.initialize()
    after_restart = repository.get_job(created["id"])
    assert after_restart is not None
    assert after_restart["retry_count"] == 0


def test_delayed_write_cannot_renew_or_finish_an_expired_lease(runtime_root: Path) -> None:
    repository = SqliteRepository(data_paths(runtime_root).database)
    repository.initialize()
    repository.update_settings({"job_lease_seconds": 60})
    created = repository.create_job("backup", None, None, None, None, {})
    claimed = repository.claim_next_job()
    assert claimed is not None

    entered = threading.Event()
    release = threading.Event()

    original_connection = repository.connection

    @contextmanager
    def delayed_connection():
        entered.set()
        assert release.wait(timeout=5)
        with original_connection() as connection:
            yield connection

    repository.connection = delayed_connection  # type: ignore[method-assign]
    result: list[bool] = []

    def finish() -> None:
        result.append(repository.update_job(
            created["id"], claimed["lease_token"], state="succeeded", message="late", done=True
        ))

    worker = threading.Thread(target=finish)
    worker.start()
    assert entered.wait(timeout=5)
    with original_connection() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), created["id"]),
        )
    release.set()
    worker.join(timeout=5)

    assert result == [False]
    job = repository.get_job(created["id"])
    assert job is not None and job["state"] == "running"


def test_manual_retry_rejects_nonterminal_job(runtime_root: Path) -> None:
    repository = SqliteRepository(data_paths(runtime_root).database)
    repository.initialize()
    created = repository.create_job("backup", None, None, None, None, {})

    with pytest.raises(ValueError, match="仅失败、阻塞或已取消"):
        repository.retry_job(created["id"])


def test_periodic_job_marks_daily_setting_only_after_fenced_completion(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    services.repository.create_job("backup", None, None, None, None, {"date": "2030-01-02"})
    worker = JobService(
        services.repository,
        services.artifacts,
        services.documents,
        backup_runner=lambda: {"state": "complete"},
    )
    monkeypatch.setattr(services.repository, "update_job", lambda *_args, **_kwargs: False)

    result = worker.run_once()

    assert result is not None and result["state"] == "running"
    assert services.repository.get_settings()["last_backup_date"] == ""


def test_periodic_runner_heartbeats_until_its_terminal_fence(runtime_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(runtime_root, acquire_lock=False)
    services = app.state.services
    services.repository.update_settings({"job_lease_seconds": 60})
    services.repository.create_job("backup", None, None, None, None, {"date": "2030-01-02"})
    heartbeat_count = 0
    original_touch = services.repository.touch_job

    def recording_touch(*args, **kwargs):
        nonlocal heartbeat_count
        heartbeat_count += 1
        return original_touch(*args, **kwargs)

    monkeypatch.setattr(services.repository, "touch_job", recording_touch)
    worker = JobService(
        services.repository,
        services.artifacts,
        services.documents,
        backup_runner=lambda: time.sleep(1.05),
    )
    monkeypatch.setattr(worker, "_lease_seconds", lambda: 3)

    result = worker.run_once()

    assert result is not None and result["state"] == "succeeded"
    assert heartbeat_count >= 1
    assert services.repository.get_settings()["last_backup_date"] == "2030-01-02"
