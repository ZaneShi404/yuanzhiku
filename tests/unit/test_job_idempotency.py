"""Task 7（加固计划）：自动作业与派生产物的确定性身份与幂等重放。

- derived_identifier：固定命名空间 UUIDv5，32 位小写十六进制；
- create_job/create_ingest/persist_representation_bundle 支持显式 ID，
  语义为 insert-or-return：已存在且身份一致 → 返回既有行；不一致 →
  完整性错误；并发同 ID 只产生一行；
- 链式后继作业由父作业 ID + kind 派生，重放不产生重复；REQ-051 的
  同版本同类已排队作业不重复入队语义保留。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from app.adapters.media_ai import ConfiguredMediaAi
from app.adapters.sqlite import SqliteRepository
from app.domain.identity import derived_identifier
from app.domain.media import video_time_range_locator
from app.services.jobs import JobService


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


def test_derived_identifier_shape_and_stability() -> None:
    first = derived_identifier("job", "parent", "video_transcribe")
    second = derived_identifier("job", "parent", "video_transcribe")
    assert first == second
    assert len(first) == 32 and first == first.lower()
    assert first != derived_identifier("job", "parent", "video_summarize")
    with pytest.raises(ValueError):
        derived_identifier("", "x")


def test_create_job_deterministic_id_is_insert_or_return(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job_id = derived_identifier("job", "parent-1", "video_transcribe")
    first = repository.create_job("video_transcribe", None, None, None, None, {}, job_id=job_id)
    second = repository.create_job("video_transcribe", None, None, None, None, {}, job_id=job_id)
    assert first["id"] == second["id"] == job_id
    assert len([job for job in repository.list_jobs() if job["id"] == job_id]) == 1
    with pytest.raises(RuntimeError):
        repository.create_job("video_summarize", None, None, None, None, {}, job_id=job_id)


def test_create_job_same_id_concurrent_single_row(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    job_id = derived_identifier("job", "parent-2", "video_summarize")
    results: list[dict] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            job = repository.create_job("video_summarize", None, None, None, None, {}, job_id=job_id)
            with lock:
                results.append(job)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert len(results) == 4
    assert len([job for job in repository.list_jobs() if job["id"] == job_id]) == 1


def test_persist_representation_bundle_deterministic_id_idempotent(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    _, version = _ingest(repository)
    rep_id = derived_identifier("representation", "job-1", "transcription")
    evidence = [{
        "locator": video_time_range_locator(0, 1000),
        "excerpt": "x",
        "excerpt_hash": hashlib.sha256(b"x").hexdigest(),
        "is_validated": True,
    }]
    common = dict(
        version_id=version["id"], artifact_sha256=ARTIFACT, kind="transcription",
        parser_name="p", config_hash="c", parent_id=None,
        chunks=[("正文", hashlib.sha256("正文".encode()).hexdigest())],
        evidence=evidence,
    )
    first = repository.persist_representation_bundle(text="正文", representation_id=rep_id, **common)
    second = repository.persist_representation_bundle(text="正文", representation_id=rep_id, **common)
    assert first["representation"]["id"] == second["representation"]["id"] == rep_id
    assert len([item for item in repository.representations_for_version(version["id"]) if item["kind"] == "transcription"]) == 1
    with pytest.raises(ValueError):
        repository.persist_representation_bundle(text="不同内容", representation_id=rep_id, **common)


def test_create_ingest_deterministic_source_id_is_idempotent(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    source_id = derived_identifier("source", "download-job", "source")
    version_id = derived_identifier("source", "download-job", "version")
    kwargs = dict(
        source_type="video_link", title="视频", author=None, language="zh", notes=None,
        rights="owned", domains=[], genres=[], tags=[], artifact_sha256=ARTIFACT,
        original_name="video.mp4", media_type="video/mp4", byte_size=16,
        job_payload={"filename": "video.mp4"}, priority=100, audit_event="video_download",
        job_kind="video_analyze",
    )
    first_source, first_version, first_job = repository.create_ingest(
        source_id=source_id, version_id=version_id, **kwargs
    )
    second_source, second_version, second_job = repository.create_ingest(
        source_id=source_id, version_id=version_id, **kwargs
    )
    assert first_source["id"] == second_source["id"] == source_id
    assert first_version["id"] == second_version["id"] == version_id
    assert first_job["id"] == second_job["id"]
    assert len([item for item in repository.list_sources(True) if item["id"] == source_id]) == 1
    with pytest.raises(RuntimeError):
        repository.create_ingest(source_id=source_id, version_id=version_id, artifact_sha256="b" * 64, **{
            key: value for key, value in kwargs.items() if key != "artifact_sha256"
        })


def _service(repository: SqliteRepository, tmp_path: Path) -> JobService:
    return JobService(
        repository,
        artifacts=None,
        documents=None,
        parser=object(),
        media_ai=ConfiguredMediaAi(lambda: {}, lambda: {}, tmp_path / "staging"),
    )


def test_enqueue_chained_derives_deterministic_child(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    source, version = _ingest(repository)
    parent = repository.create_job("video_analyze", source["id"], version["id"], ARTIFACT, None, {})
    service = _service(repository, tmp_path)
    service._enqueue_chained("video_transcribe", parent)
    service._enqueue_chained("video_transcribe", parent)
    children = [job for job in repository.list_jobs() if job["kind"] == "video_transcribe"]
    assert len(children) == 1
    assert children[0]["id"] == derived_identifier("job", parent["id"], "video_transcribe")


def test_enqueue_chained_respects_other_queued_job(tmp_path: Path) -> None:
    repository = _repo(tmp_path)
    source, version = _ingest(repository)
    parent = repository.create_job("video_analyze", source["id"], version["id"], ARTIFACT, None, {})
    manual = repository.create_job("video_transcribe", source["id"], version["id"], ARTIFACT, None, {}, priority=100)
    service = _service(repository, tmp_path)
    service._enqueue_chained("video_transcribe", parent)
    children = [job for job in repository.list_jobs() if job["kind"] == "video_transcribe"]
    assert len(children) == 1
    assert children[0]["id"] == manual["id"]
