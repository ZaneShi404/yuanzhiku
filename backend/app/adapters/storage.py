"""Filesystem adapter for immutable content-addressed artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.core.config import DataPaths, InterprocessLock
from app.domain.artifacts import StoredArtifact

MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
MIN_FREE_AFTER = 10 * 1024 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
STAGING_MARKER = ".yuanzhiku-staging.json"
STAGING_TTL_SECONDS = 86_400


class StorageLimitError(ValueError):
    pass


class ArtifactIntegrityError(RuntimeError):
    """内容寻址命中目标的实际内容与地址不一致且修复失败；消息不含内容。"""


class ArtifactStore:
    def __init__(self, paths: DataPaths) -> None:
        self.paths = paths
        # The process-wide instance lock prevents another application instance from
        # using this data root. This lock additionally serializes file/database
        # compensations between concurrent requests in this instance.
        self._operation_lock = threading.RLock()
        self._maintenance_lock = InterprocessLock(paths.maintenance_lock_file)

    @contextmanager
    def operation(self):
        """Keep file/database orchestration mutually exclusive across workers."""
        with self._operation_lock, self._maintenance_lock:
            yield

    def artifact_path(self, sha256: str) -> Path:
        return self.paths.artifacts / sha256[:2] / sha256

    def staging_path(self) -> Path:
        """Return a short, exclusive-create-ready path below the staging area."""
        self.paths.staging.mkdir(parents=True, exist_ok=True)
        return self.paths.staging / f"{uuid.uuid4().hex}.part"

    def staging_workspace(self, kind: str) -> Path:
        """带 marker 的一次性作业工作区（加固计划 Task 15A）。

        marker 只含 operation_id、创建时间与 kind，不含正文、URL、本地
        路径或凭据；sweep 据此区分可自动清理的目录与必须人工决策的遗留。
        """
        workspace = self.staging_path().with_suffix("")
        workspace.mkdir(parents=True)
        marker = {
            "operation_id": uuid.uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "kind": kind,
        }
        (workspace / STAGING_MARKER).write_text(json.dumps(marker), encoding="utf-8")
        return workspace

    def sweep_staging(self, *, ttl_seconds: int = STAGING_TTL_SECONDS) -> dict[str, list[str]]:
        """marker 驱动的 staging 清理（加固计划 Task 15A）。

        只删除同时满足「合法 marker + 超过 TTL」的目录，且全程持有维护
        锁（与 purge/备份互斥）。无 marker 的遗留内容（如 _dy_probe*）与
        损坏 marker 只报告、绝不自动删除。返回分类清单（不含绝对路径）。
        """
        with self._operation_lock, self._maintenance_lock:
            report: dict[str, list[str]] = {"removed": [], "kept_active": [], "corrupt_marker": [], "unknown": []}
            root = self.paths.staging
            if not root.is_dir():
                return report
            for entry in sorted(root.iterdir()):
                marker_path = entry / STAGING_MARKER if entry.is_dir() else None
                if marker_path is None or not marker_path.is_file():
                    report["unknown"].append(entry.name)
                    continue
                try:
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                    if not isinstance(marker, dict) or not isinstance(marker.get("operation_id"), str):
                        raise ValueError("marker 无效")
                    created_at = datetime.fromisoformat(str(marker["created_at"]))
                except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
                    report["corrupt_marker"].append(entry.name)
                    continue
                age_seconds = (datetime.now(UTC) - created_at).total_seconds()
                if age_seconds < ttl_seconds:
                    report["kept_active"].append(entry.name)
                    continue
                shutil.rmtree(entry, ignore_errors=True)
                if entry.exists():
                    report["corrupt_marker"].append(entry.name)
                else:
                    report["removed"].append(entry.name)
            return report

    def check_capacity(self, expected_bytes: int) -> None:
        if expected_bytes < 0 or expected_bytes > MAX_FILE_BYTES:
            raise StorageLimitError("文件大小超过 2GB 限制")
        free = shutil.disk_usage(self.paths.root).free
        required = expected_bytes * 2 + MIN_FREE_AFTER
        if free < required:
            raise StorageLimitError("可用空间不足：需要文件两倍空间且导入后保留 10GB")

    def store_stream(self, stream: BinaryIO, expected_bytes: int | None = None) -> StoredArtifact:
        with self.operation():
            return self._store_stream(stream, expected_bytes)

    def _hash_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as existing:
            for chunk in iter(lambda: existing.read(COPY_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _verify_target(self, destination: Path, sha256: str, size: int) -> bool:
        """命中目标与（哈希、尺寸）逐字节一致才可视为有效去重。"""
        try:
            if not destination.is_file() or destination.stat().st_size != size:
                return False
            return self._hash_file(destination) == sha256
        except OSError:
            return False

    def _repair_hit(self, stage: Path, destination: Path, sha256: str, size: int) -> None:
        """损坏命中修复（加固计划 Task 6）：隔离旧目标 → 已验证 staging 原子
        替换 → 复验 → 清理隔离副本；失败尽力恢复旧目标并抛受控异常。"""
        quarantine = destination.parent / f"{destination.name}.quarantine-{uuid.uuid4().hex}"
        try:
            os.replace(destination, quarantine)
            os.replace(stage, destination)
            if not self._verify_target(destination, sha256, size):
                raise ArtifactIntegrityError("内容寻址对象校验失败且修复未通过")
        except ArtifactIntegrityError:
            stage.unlink(missing_ok=True)
            self._restore_quarantine(quarantine, destination)
            raise
        except Exception as exc:
            stage.unlink(missing_ok=True)
            self._restore_quarantine(quarantine, destination)
            raise ArtifactIntegrityError("内容寻址对象修复失败") from exc
        finally:
            # 成功或已恢复时清理隔离副本；恢复失败时保留旧目标供检查，
            # 绝不删除仅存的副本。
            if quarantine.exists():
                if quarantine.is_dir():
                    shutil.rmtree(quarantine, ignore_errors=True)
                else:
                    quarantine.unlink(missing_ok=True)

    @staticmethod
    def _restore_quarantine(quarantine: Path, destination: Path) -> bool:
        if not quarantine.exists() or destination.exists():
            return quarantine.exists() and destination.exists()
        try:
            os.replace(quarantine, destination)
            return True
        except OSError:
            return False

    def _store_stream(self, stream: BinaryIO, expected_bytes: int | None = None) -> StoredArtifact:
        if expected_bytes is not None:
            self.check_capacity(expected_bytes)
        stage = self.staging_path()
        digest = hashlib.sha256()
        size = 0
        try:
            with stage.open("xb") as target:
                while True:
                    chunk = stream.read(COPY_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise StorageLimitError("文件大小超过 2GB 限制")
                    if size % (64 * COPY_CHUNK) == 0:
                        self.check_capacity(size)
                    digest.update(chunk)
                    target.write(chunk)
            self.check_capacity(size)
            sha256 = digest.hexdigest()
            destination = self.artifact_path(sha256)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                # CAS 命中不再盲信：逐字节校验；损坏即用本次已验证字节修复。
                if self._verify_target(destination, sha256, size):
                    stage.unlink(missing_ok=True)
                    return StoredArtifact(sha256, size, destination, False)
                self._repair_hit(stage, destination, sha256, size)
                return StoredArtifact(sha256, size, destination, False)
            os.replace(stage, destination)
            return StoredArtifact(sha256, size, destination, True)
        except Exception:
            stage.unlink(missing_ok=True)
            raise

    def read_bytes(self, sha256: str) -> bytes:
        return self.artifact_path(sha256).read_bytes()

    def delete(self, sha256: str) -> None:
        path = self.artifact_path(sha256)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def verify(self, sha256: str) -> bool:
        path = self.artifact_path(sha256)
        if not path.is_file():
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(COPY_CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest() == sha256
