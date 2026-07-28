"""Local backup, export, restore and integrity verification service."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.adapters.sqlite import SqliteRepository
from app.adapters.storage import ArtifactStore
from app.core.config import DataPaths, database_backend
from app.ports.repository import RepositoryPort

ARCHIVE_SCHEMA_VERSION = 1


class ReimportConflict(ValueError):
    """A checked logical-record conflict that must return a 4xx report."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("导入逻辑记录冲突")
        self.report = report


class TransferService:
    def __init__(self, paths: DataPaths, repository: RepositoryPort, artifacts: ArtifactStore) -> None:
        self.paths = paths
        self.repository = repository
        self.artifacts = artifacts
        self._backup_lock = threading.RLock()

    @staticmethod
    def _sha256_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        members = archive.infolist()
        for member in members:
            candidate = Path(member.filename)
            if member.is_dir() or candidate.is_absolute() or ".." in candidate.parts or "\\" in member.filename:
                raise ValueError("归档含不安全路径")
        return members

    def _snapshot_database(self, destination: Path) -> None:
        source = sqlite3.connect(self.paths.database)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def _add_file(self, archive: zipfile.ZipFile, path: Path, archive_name: str, entries: list[dict[str, Any]]) -> None:
        archive.write(path, archive_name)
        entries.append({"path": archive_name, "sha256": self._sha256_path(path), "byte_size": path.stat().st_size})

    def _archive_path(self, directory: Path, prefix: str) -> Path:
        """Reserve a unique archive name before any bytes are written."""
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        while True:
            candidate = directory / f"{prefix}-{timestamp}-{uuid.uuid4().hex}.zip"
            try:
                with candidate.open("xb"):
                    pass
                return candidate
            except FileExistsError:
                continue

    def _build_archive(self, archive_path: Path, archive_type: str) -> tuple[dict[str, Any], str]:
        stamp = datetime.now(UTC).isoformat()
        entries: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(dir=self.paths.staging) as temp_name:
            temp = Path(temp_name)
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                if self.repository.backend == "sqlite":
                    snapshot = temp / "knowledge.db"
                    self._snapshot_database(snapshot)
                    self._add_file(archive, snapshot, "state/knowledge.db", entries)
                records = temp / "records.json"
                records.write_text(
                    json.dumps(
                        {
                            "schema_version": ARCHIVE_SCHEMA_VERSION,
                            "records": self.repository.rows_for_backup() if archive_type == "backup" else self.repository.rows_for_export(),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
                self._add_file(archive, records, "records.json", entries)
                for artifact in sorted(self.paths.artifacts.rglob("*")):
                    if artifact.is_file():
                        relative = artifact.relative_to(self.paths.artifacts).as_posix()
                        self._add_file(archive, artifact, f"artifacts/{relative}", entries)
                manifest = {
                    "schema_version": ARCHIVE_SCHEMA_VERSION,
                    "archive_type": archive_type,
                    "created_at": stamp,
                    "entries": sorted(entries, key=lambda item: item["path"]),
                    "exclusions": ["models", "staging", "log_bodies", "credentials", "cookies", "original_paths", "private_rights_notes"],
                }
                manifest_path = temp / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                archive.write(manifest_path, "manifest.json")
                manifest_sha256 = self._sha256_path(manifest_path)
        return manifest, manifest_sha256

    def create_backup(self) -> dict[str, Any]:
        # A record becomes succeeded only after its own unique archive is complete
        # and verified. Any exception removes the temporary/published archive and
        # its record, so restore never advertises a missing successful backup.
        with self._backup_lock:
            self._reconcile_incomplete_backup_records()
            archive_path = self._archive_path(self.paths.backups, "backup")
            record: dict[str, Any] | None = None
            try:
                _, manifest_sha256 = self._build_archive(archive_path, "backup")
                if not self.verify_archive(archive_path)["valid"]:
                    raise ValueError("备份 SHA-256 验证失败")
                record = self.repository.create_backup_record(archive_path.name, manifest_sha256, state="succeeded")
                self._prune_backups(30, protected_id=record["id"])
                self.repository.audit("backup", record["id"], "succeeded")
                return {**record, "archive_path": str(archive_path)}
            except Exception:
                if record is None:
                    archive_path.unlink(missing_ok=True)
                else:
                    # Keep a non-success record until the archive is deleted.
                    # If unlink fails, the next backup reconciles this record.
                    self.repository.update_backup_state(record["id"], "discarding")
                    try:
                        archive_path.unlink()
                    except FileNotFoundError:
                        self.repository.delete_backup_record(record["id"])
                    except OSError:
                        pass
                    else:
                        self.repository.delete_backup_record(record["id"])
                raise

    def _reconcile_incomplete_backup_records(self) -> None:
        """Repair records left between the database and filesystem steps."""
        for backup in self.repository.list_backups():
            archive_path = self.paths.backups / backup["archive_name"]
            if backup["state"] == "pruning":
                if archive_path.is_file():
                    self.repository.update_backup_state(backup["id"], "succeeded")
                else:
                    self.repository.delete_backup_record(backup["id"])
            elif backup["state"] == "discarding":
                try:
                    archive_path.unlink()
                except FileNotFoundError:
                    self.repository.delete_backup_record(backup["id"])
                except OSError:
                    # Keep a non-success record for a future cleanup attempt.
                    continue
                else:
                    self.repository.delete_backup_record(backup["id"])

    def _prune_backups(self, keep: int, protected_id: str | None = None) -> None:
        backups = [backup for backup in self.repository.list_backups() if backup["state"] == "succeeded"]
        seen_days: set[str] = set()
        stale: list[dict[str, Any]] = []
        for backup in backups:
            day = backup["created_at"][:10]
            if backup["id"] == protected_id:
                if day not in seen_days and len(seen_days) < keep:
                    seen_days.add(day)
                continue
            if day in seen_days or len(seen_days) >= keep:
                stale.append(backup)
            else:
                seen_days.add(day)
        for backup in stale:
            archive_path = self.paths.backups / backup["archive_name"]
            # Mark first so a file-system deletion failure cannot leave a record
            # advertised as restorable. Delete the record only after unlinking.
            self.repository.update_backup_state(backup["id"], "pruning")
            try:
                archive_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                # The archive remains available, so keep it as a successful
                # backup and retry retention on the next backup operation.
                self.repository.update_backup_state(backup["id"], "succeeded")
                continue
            self.repository.delete_backup_record(backup["id"])

    def create_export(self, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("必须在界面确认后才能导出")
        self.paths.exports.mkdir(parents=True, exist_ok=True)
        archive_path = self._archive_path(self.paths.exports, "export")
        manifest, manifest_sha256 = self._build_archive(archive_path, "export")
        verification = self.verify_archive(archive_path)
        if not verification["valid"]:
            archive_path.unlink(missing_ok=True)
            raise ValueError("导出 SHA-256 验证失败")
        self.repository.audit("export", None, "succeeded")
        return {"archive_name": archive_path.name, "archive_path": str(archive_path), "manifest_sha256": manifest_sha256, "entry_count": len(manifest["entries"])}

    def verify_archive(self, archive_path: Path) -> dict[str, Any]:
        if not archive_path.is_file():
            return {"valid": False, "errors": ["归档不存在"]}
        errors: list[str] = []
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = self._safe_members(archive)
                names = {member.filename for member in members}
                if "manifest.json" not in names:
                    return {"valid": False, "errors": ["缺少 manifest"]}
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
                    errors.append("不支持的 manifest schema")
                for entry in manifest.get("entries", []):
                    path = entry.get("path")
                    if not isinstance(path, str) or path not in names:
                        errors.append("manifest 条目缺失")
                        continue
                    actual = hashlib.sha256(archive.read(path)).hexdigest()
                    if actual != entry.get("sha256"):
                        errors.append("条目哈希不匹配")
                if manifest.get("archive_type") == "export" and "records.json" not in names:
                    errors.append("导出缺少逻辑记录")
        except (zipfile.BadZipFile, json.JSONDecodeError, OSError, ValueError):
            errors.append("归档无法验证")
        return {"valid": not errors, "errors": sorted(set(errors))}

    def verify_artifacts(self, full: bool, sample_size: int) -> dict[str, Any]:
        hashes = [row["sha256"] for row in self.repository.rows_for_export()["artifacts"]]
        selected = hashes if full else hashes[:sample_size]
        failures = [sha256 for sha256 in selected if not self.artifacts.verify(sha256)]
        self.repository.audit("integrity_verify", None, "failed" if failures else "succeeded")
        return {"checked": len(selected), "full": full, "valid": not failures, "failures": failures}

    def restore_backup(self, backup_id: str, target_root: str, target_database_url: str | None = None) -> dict[str, Any]:
        record = next((item for item in self.repository.list_backups() if item["id"] == backup_id), None)
        if record is None:
            raise KeyError("备份不存在")
        return self._restore_archive(self.paths.backups / record["archive_name"], target_root, target_database_url)

    def _target_paths(self, target_root: str) -> DataPaths:
        target = DataPaths(Path(target_root).expanduser().resolve())
        if target.root == self.paths.root:
            raise ValueError("还原目标必须是新的数据根")
        if target.root.exists() and any(target.root.iterdir()):
            raise ValueError("还原目标必须不存在或为空")
        return target

    def _restore_archive(self, archive_path: Path, target_root: str, target_database_url: str | None = None) -> dict[str, Any]:
        verification = self.verify_archive(archive_path)
        if not verification["valid"]:
            raise ValueError("归档验证失败")
        target = self._target_paths(target_root)
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest["archive_type"] not in {"backup", "export"}:
                raise ValueError("不支持的归档类型")
            entry_names = {entry["path"] for entry in manifest["entries"]}
            if "state/knowledge.db" not in entry_names:
                if not target_database_url or database_backend(target_database_url) != "postgresql":
                    raise ValueError("PostgreSQL 备份还原需要新的 target_database_url")
                from app.adapters.postgres import PostgresRepository

                target_repository = PostgresRepository(
                    target_database_url,
                    Path(__file__).resolve().parents[2] / "migrations" / "postgresql",
                )
                target_repository.initialize()
                if target_repository.has_user_records():
                    raise ValueError("PostgreSQL 还原目标必须为空")
                records_payload = json.loads(archive.read("records.json"))
                records = records_payload.get("records")
                if records_payload.get("schema_version") != ARCHIVE_SCHEMA_VERSION or not isinstance(records, dict):
                    raise ValueError("备份逻辑记录无效")
                target.create()
                self._extract_artifacts(archive, manifest, target)
                target_repository.prepare_backup_restore()
                target_repository.insert_backup_rows(records)
                artifact_hashes = [row["sha256"] for row in records.get("artifacts", []) if isinstance(row, dict) and isinstance(row.get("sha256"), str)]
            else:
                target.create()
                self._extract_artifacts(archive, manifest, target)
                destination = target.database
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open("state/knowledge.db") as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output)
                restored_repo = SqliteRepository(target.database)
                restored_repo.initialize()
                artifact_hashes = [row["sha256"] for row in restored_repo.rows_for_export()["artifacts"]]
        restored_store = ArtifactStore(target)
        invalid = [sha256 for sha256 in artifact_hashes if not restored_store.verify(sha256)]
        if invalid:
            shutil.rmtree(target.root)
            raise ValueError("还原后的 artifact 哈希不匹配")
        return {"target_data_root": str(target.root), "restored_artifacts": len(artifact_hashes), "archive_type": manifest["archive_type"]}

    @staticmethod
    def _extract_artifacts(archive: zipfile.ZipFile, manifest: dict[str, Any], target: DataPaths) -> None:
        for entry in manifest["entries"]:
            name = entry["path"]
            if not name.startswith("artifacts/"):
                continue
            destination = target.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)

    def reimport(self, archive_path: str) -> dict[str, Any]:
        path = Path(archive_path).expanduser().resolve()
        verification = self.verify_archive(path)
        if not verification["valid"]:
            raise ValueError("导入归档验证失败")
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("archive_type") != "export":
                raise ValueError("仅支持 reimport 导出归档")
            records_payload = json.loads(archive.read("records.json"))
            if records_payload.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
                raise ValueError("逻辑记录 schema 不兼容")
            records = records_payload.get("records")
            if not isinstance(records, dict):
                raise ValueError("逻辑记录无效")
            tables = ["artifacts", "sources", "source_metadata_revisions", "content_versions", "source_relations", "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence", "external_cards", "topics", "topic_sources"]
            primary_keys = {
                "artifacts": ("sha256",), "sources": ("id",), "source_metadata_revisions": ("id",), "content_versions": ("id",), "source_relations": ("id",),
                "representations": ("id",), "search_chunks": ("id",), "evidence": ("id",), "citations": ("id",), "knowledge": ("id",),
                "knowledge_evidence": ("knowledge_id", "evidence_id"), "external_cards": ("id",), "topics": ("id",), "topic_sources": ("topic_id", "source_id"),
            }
            unique_keys = {"external_cards": ("card_type", "url"), "topics": ("name",), "source_metadata_revisions": ("source_id", "ordinal"), "content_versions": ("source_id", "ordinal"), "search_chunks": ("representation_id", "ordinal"), "source_relations": ("source_id", "related_source_id", "relation_type")}
            current = self.repository.rows_for_export()
            conflicts: list[str] = []
            pending: dict[str, list[dict[str, Any]]] = {}
            for table in tables:
                incoming = records.get(table, [])
                if not isinstance(incoming, list):
                    raise ValueError("逻辑记录表无效")
                keys = primary_keys[table]
                existing = {tuple(row[key] for key in keys): row for row in current[table]}
                pending[table] = []
                for row in incoming:
                    if not isinstance(row, dict) or any(key not in row for key in keys):
                        raise ValueError("逻辑记录行无效")
                    key = tuple(row[key] for key in keys)
                    if key in existing:
                        if row != existing[key]:
                            conflicts.append(f"{table}:{':'.join(key)}")
                    else:
                        pending[table].append(row)
            # Natural keys are checked before copying an artifact. Same values are
            # idempotent only if every logical value matches; different values are
            # a reportable conflict even when the primary key is different.
            for table, keys in unique_keys.items():
                existing_by_key = {tuple(row[key] for key in keys): row for row in current[table]}
                pending_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
                for row in pending[table]:
                    key = tuple(row[key] for key in keys)
                    comparable = existing_by_key.get(key) or pending_by_key.get(key)
                    if comparable is not None:
                        if row != comparable:
                            conflicts.append(f"{table}:unique:{':'.join(str(value) for value in key)}")
                    else:
                        pending_by_key[key] = row
            if conflicts:
                raise ReimportConflict({"conflicts": sorted(set(conflicts)), "reason": "逻辑链或唯一约束冲突，已拒绝且未写入"})
            artifact_entries = {entry["path"]: entry for entry in manifest.get("entries", [])}
            created_artifacts: list[str] = []
            try:
                with self.artifacts.operation():
                    for artifact in pending["artifacts"]:
                        sha256 = artifact["sha256"]
                        if self.artifacts.verify(sha256):
                            continue
                        name = f"artifacts/{sha256[:2]}/{sha256}"
                        if name not in artifact_entries:
                            raise ValueError("导入缺少 artifact")
                        destination = self.artifacts.artifact_path(sha256)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        stage = self.paths.staging / f"reimport-{sha256}-{uuid.uuid4().hex}.part"
                        try:
                            with archive.open(name) as source, stage.open("xb") as output:
                                shutil.copyfileobj(source, output)
                            if self._sha256_path(stage) != sha256:
                                raise ValueError("导入 artifact 哈希不匹配")
                            os.replace(stage, destination)
                            created_artifacts.append(sha256)
                        finally:
                            stage.unlink(missing_ok=True)
                    # The repository owns its dialect-specific transaction and
                    # parameter binding while preserving foreign-key order.
                    self.repository.insert_export_rows(pending)
            except Exception:
                for sha256 in created_artifacts:
                    if self.repository.delete_artifact_if_unreferenced(sha256):
                        self.artifacts.delete(sha256)
                raise
        imported_count = sum(len(rows) for rows in pending.values())
        self.repository.audit("reimport", None, "succeeded")
        return {"imported": True, "report": {"conflicts": [], "inserted_records": imported_count, "imported_artifacts": len(pending["artifacts"])}}
