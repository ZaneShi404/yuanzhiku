"""Task 15A（加固计划）：marker 驱动的 staging 目录生命周期。

- 新建作业工作区写入 `.yuanzhiku-staging.json`（operation_id/created_at/kind，
  不含正文、URL、路径或凭据）；
- 自动清理只处理：合法 marker + 超过 TTL + 可获得维护锁的目录；旧的无
  marker 内容（如 _dy_probe*）与损坏 marker 只报告，绝不自动删除；
- sweep 幂等可重复；启动 best-effort 调用一次。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.adapters.storage import ArtifactStore, STAGING_MARKER
from app.core.config import data_paths


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(data_paths(tmp_path))


def _age_marker(workspace: Path, hours: float) -> None:
    marker = workspace / STAGING_MARKER
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["created_at"] = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    marker.write_text(json.dumps(payload), encoding="utf-8")


def test_workspace_writes_content_free_marker(store: ArtifactStore) -> None:
    workspace = store.staging_workspace("video_transcribe")
    marker = json.loads((workspace / STAGING_MARKER).read_text(encoding="utf-8"))
    assert set(marker) == {"operation_id", "created_at", "kind"}
    assert marker["kind"] == "video_transcribe"
    assert len(marker["operation_id"]) >= 16
    datetime.fromisoformat(marker["created_at"])


def test_fresh_workspace_within_ttl_is_kept(store: ArtifactStore) -> None:
    workspace = store.staging_workspace("parse")
    (workspace / "partial.bin").write_bytes("中间产物".encode("utf-8"))
    report = store.sweep_staging(ttl_seconds=86400)
    assert workspace.is_dir()
    assert workspace.name in report["kept_active"]


def test_aged_workspace_is_removed(store: ArtifactStore) -> None:
    workspace = store.staging_workspace("video_download")
    (workspace / "video.mp4").write_bytes("残留".encode("utf-8"))
    _age_marker(workspace, hours=25)

    report = store.sweep_staging(ttl_seconds=86400)

    assert workspace.name in report["removed"]
    assert not workspace.exists()


def test_corrupt_marker_reported_never_deleted(store: ArtifactStore) -> None:
    workspace = store.staging_workspace("parse")
    (workspace / STAGING_MARKER).write_text("{not json", encoding="utf-8")
    (workspace / "payload.txt").write_text("内容", encoding="utf-8")

    report = store.sweep_staging(ttl_seconds=1)

    assert workspace.name in report["corrupt_marker"]
    assert workspace.is_dir() and (workspace / "payload.txt").is_file()


def test_unknown_legacy_directory_only_reported(store: ArtifactStore) -> None:
    legacy = store.paths.staging / "_dy_probe_20260801"
    legacy.mkdir(parents=True)
    (legacy / "old.bin").write_bytes("遗留".encode("utf-8"))

    report = store.sweep_staging(ttl_seconds=1)

    assert legacy.name in report["unknown"]
    assert legacy.is_dir() and (legacy / "old.bin").is_file()


def test_sweep_is_repeatable_and_recovers_crashed_workspace(store: ArtifactStore) -> None:
    workspace = store.staging_workspace("parse")
    (workspace / "half.bin").write_bytes("崩溃残留".encode("utf-8"))
    _age_marker(workspace, hours=25)

    first = store.sweep_staging(ttl_seconds=86400)
    second = store.sweep_staging(ttl_seconds=86400)

    assert first["removed"] == [workspace.name]
    assert second["removed"] == [] and not workspace.exists()


def test_staging_part_files_reported_without_deletion(store: ArtifactStore) -> None:
    stray = store.staging_path()  # 旧式 .part 文件（无 marker）
    stray.write_bytes(b"x")
    report = store.sweep_staging(ttl_seconds=1)
    assert stray.name in report["unknown"]
    assert stray.is_file()
