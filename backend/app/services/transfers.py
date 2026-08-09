"""Local backup, export, restore and integrity verification service."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.adapters.sqlite import (
    BACKUP_TABLE_COLUMNS,
    BACKUP_TABLES,
    EXPORT_TABLES,
    SqliteRepository,
    redact_url_userinfo,
)
from app.adapters.storage import ArtifactStore
from app.core.config import DataPaths, database_backend
from app.ports.repository import RepositoryPort

ARCHIVE_SCHEMA_VERSION = 5
SUPPORTED_ARCHIVE_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, ARCHIVE_SCHEMA_VERSION})
BACKUP_CATALOG_STATES = frozenset({"succeeded", "pruning", "discarding"})
SAFE_ARCHIVE_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,250}\.zip\Z")
WINDOWS_RESERVED_BASENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


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
        seen: set[str] = set()
        for member in members:
            candidate = Path(member.filename)
            if (
                member.is_dir()
                or member.filename in seen
                or candidate.is_absolute()
                or ".." in candidate.parts
                or "\\" in member.filename
            ):
                raise ValueError("归档含不安全路径")
            seen.add(member.filename)
        return members

    @staticmethod
    def _ordered_rows(rows: dict[str, list[dict[str, Any]]], tables: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
        """Serialize rows in the portable contract order, never adapter SELECT order."""
        normalized: dict[str, list[dict[str, Any]]] = {}
        for table in tables:
            columns = BACKUP_TABLE_COLUMNS[table]
            normalized[table] = [
                {column: row.get(column) for column in columns}
                for row in rows.get(table, [])
            ]
        return normalized

    @staticmethod
    def _artifact_members(rows: dict[str, list[dict[str, Any]]]) -> list[tuple[str, Path]]:
        members: list[tuple[str, Path]] = []
        for artifact in rows["artifacts"]:
            sha256 = artifact.get("sha256")
            if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise ValueError("artifact 记录无效")
            members.append((f"artifacts/{sha256[:2]}/{sha256}", Path(sha256)))
        return members

    @staticmethod
    def _entry_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            raise ValueError("manifest 条目无效")
        mapped: dict[str, dict[str, Any]] = {}
        for entry in entries:
            path = entry.get("path") if isinstance(entry, dict) else None
            digest = entry.get("sha256") if isinstance(entry, dict) else None
            byte_size = entry.get("byte_size") if isinstance(entry, dict) else None
            if (
                not isinstance(path, str)
                or path in mapped
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(byte_size, int)
                or byte_size < 0
            ):
                raise ValueError("manifest 条目无效")
            mapped[path] = entry
        return mapped

    def _snapshot_database(self, destination: Path) -> None:
        source = sqlite3.connect(self.paths.database)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def _sanitize_snapshot_external_cards(self, snapshot: Path) -> None:
        """Remove legacy URL userinfo from the copied snapshot before it is archived."""
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(snapshot)
            rows = connection.execute("SELECT id,url FROM external_cards").fetchall()
            for card_id, value in rows:
                redacted = redact_url_userinfo(value)
                if redacted != value:
                    connection.execute("UPDATE external_cards SET url=? WHERE id=?", (redacted, card_id))
            connection.commit()
        finally:
            if connection is not None:
                connection.close()

    def _sqlite_snapshot_records(self, snapshot: Path, schema_version: int) -> dict[str, list[dict[str, Any]]]:
        """Read a backup snapshot without initializing or repairing its contents."""
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"{snapshot.resolve().as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            rows = {
                table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall()]
                for table in BACKUP_TABLES
            }
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ValueError("SQLite 状态快照无效") from exc
        finally:
            if connection is not None:
                connection.close()
        for card in rows["external_cards"]:
            if urlsplit(card["url"]).username is not None or urlsplit(card["url"]).password is not None:
                raise ValueError("SQLite 状态快照无效")
        try:
            return self._backup_records({"schema_version": schema_version, "records": rows})
        except ValueError as exc:
            raise ValueError("SQLite 状态快照无效") from exc

    @staticmethod
    def _canonical_records(records: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
        return {
            table: sorted(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for row in records[table]
            )
            for table in BACKUP_TABLES
        }

    def _validate_sqlite_snapshot_records(
        self,
        archive: zipfile.ZipFile,
        records: dict[str, list[dict[str, Any]]],
        schema_version: int,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=self.paths.staging) as temporary:
            snapshot = Path(temporary) / "knowledge.db"
            with archive.open("state/knowledge.db") as source, snapshot.open("xb") as output:
                shutil.copyfileobj(source, output)
            snapshot_records = self._sqlite_snapshot_records(snapshot, schema_version)
        if self._canonical_records(snapshot_records) != self._canonical_records(records):
            raise ValueError("SQLite 状态快照与逻辑记录不一致")

    @staticmethod
    def _sha256_member(archive: zipfile.ZipFile, archive_name: str) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_size = 0
        with archive.open(archive_name) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_size += len(chunk)
        return digest.hexdigest(), byte_size

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
        tables = BACKUP_TABLES if archive_type == "backup" else EXPORT_TABLES
        with self._backup_lock, self.artifacts.operation(), tempfile.TemporaryDirectory(dir=self.paths.staging) as temp_name:
            temp = Path(temp_name)
            # SQLite snapshot and logical records must observe the same database
            # point-in-time. PostgreSQL rows use a repeatable-read adapter snapshot.
            if self.repository.backend == "sqlite" and archive_type == "backup":
                snapshot = temp / "knowledge.db"
                self._snapshot_database(snapshot)
                snapshot_repository = SqliteRepository(snapshot)
                snapshot_repository.initialize()
                self._sanitize_snapshot_external_cards(snapshot)
                rows = snapshot_repository.rows_for_backup()
            else:
                rows = self.repository.rows_for_backup() if archive_type == "backup" else self.repository.rows_for_export()
            records_payload = {
                "schema_version": ARCHIVE_SCHEMA_VERSION,
                "records": self._ordered_rows(rows, tables),
            }
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                if self.repository.backend == "sqlite" and archive_type == "backup":
                    self._add_file(archive, snapshot, "state/knowledge.db", entries)
                records = temp / "records.json"
                records.write_text(json.dumps(records_payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                self._add_file(archive, records, "records.json", entries)
                for archive_name, relative in self._artifact_members(records_payload["records"]):
                    artifact = self.paths.artifacts / relative.as_posix()[:2] / relative.as_posix()
                    if not artifact.is_file():
                        raise ValueError("artifact 文件缺失")
                    self._add_file(archive, artifact, archive_name, entries)
                manifest = {
                    "schema_version": ARCHIVE_SCHEMA_VERSION,
                    "archive_type": archive_type,
                    "database_backend": self.repository.backend,
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
                if not isinstance(manifest, dict):
                    raise ValueError("manifest 无效")
                schema_version = manifest.get("schema_version")
                archive_type = manifest.get("archive_type")
                if schema_version not in SUPPORTED_ARCHIVE_SCHEMA_VERSIONS:
                    errors.append("不支持的 manifest schema")
                if archive_type not in {"backup", "export"}:
                    errors.append("不支持的归档类型")
                if schema_version == ARCHIVE_SCHEMA_VERSION and manifest.get("database_backend") not in {"sqlite", "postgresql"}:
                    errors.append("归档数据库类型无效")
                entries = self._entry_map(manifest)
                declared_names = set(entries)
                if archive_type == "backup" and schema_version == ARCHIVE_SCHEMA_VERSION:
                    if manifest["database_backend"] == "sqlite" and "state/knowledge.db" not in declared_names:
                        errors.append("SQLite 备份缺少状态快照")
                    if manifest["database_backend"] == "postgresql" and "state/knowledge.db" in declared_names:
                        errors.append("PostgreSQL 备份不得包含 SQLite 状态")
                if names != declared_names | {"manifest.json"}:
                    errors.append("归档成员未由 manifest 完整声明")
                if "records.json" not in declared_names:
                    errors.append("归档缺少逻辑记录")
                if archive_type == "export" and "state/knowledge.db" in declared_names:
                    errors.append("便携导出不得包含本地 SQLite 状态")
                for path, entry in entries.items():
                    if path not in names:
                        errors.append("manifest 条目缺失")
                        continue
                    actual_hash, actual_size = self._sha256_member(archive, path)
                    if actual_hash != entry["sha256"]:
                        errors.append("条目哈希不匹配")
                    if actual_size != entry["byte_size"]:
                        errors.append("条目字节数不匹配")
                    if path.startswith("artifacts/"):
                        parts = path.split("/")
                        if len(parts) != 3 or parts[1] != parts[2][:2] or not re.fullmatch(r"[0-9a-f]{64}", parts[2]) or actual_hash != parts[2]:
                            errors.append("artifact 归档路径或哈希无效")
                if not errors:
                    records_payload = self._records_payload(archive)
                    records = self._backup_records(records_payload) if archive_type == "backup" else self._export_records(records_payload)
                    if archive_type == "backup" and "state/knowledge.db" in declared_names:
                        self._validate_sqlite_snapshot_records(archive, records, records_payload["schema_version"])
                    expected_artifacts = {name for name, _ in self._artifact_members(records)}
                    actual_artifacts = {path for path in declared_names if path.startswith("artifacts/")}
                    allowed_names = {"records.json", *expected_artifacts}
                    if archive_type == "backup":
                        allowed_names.add("state/knowledge.db")
                    if declared_names - allowed_names:
                        errors.append("归档包含不允许的成员")
                    if actual_artifacts != expected_artifacts:
                        errors.append("逻辑记录与 artifact 成员不一致")
        except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError, KeyError, TypeError):
            errors.append("归档无法验证")
        except ValueError as exc:
            # Structural and cryptographic checks have already succeeded before
            # logical records are decoded, so preserve controlled record diagnostics.
            if not errors and str(exc) in {
                "逻辑记录无效",
                "逻辑记录不完整",
                "备份目录记录无效",
                "视频记录无效",
                "artifact 记录无效",
                "SQLite 状态快照无效",
                "SQLite 状态快照与逻辑记录不一致",
            }:
                errors.append(str(exc))
            else:
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

    @staticmethod
    def _records_payload(archive: zipfile.ZipFile) -> dict[str, Any]:
        try:
            payload = json.loads(archive.read("records.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("逻辑记录无效") from exc
        if not isinstance(payload, dict):
            raise ValueError("逻辑记录无效")
        return payload

    @staticmethod
    def _logical_records(records_payload: Any, expected_tables: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(records_payload, dict):
            raise ValueError("逻辑记录无效")
        schema_version = records_payload.get("schema_version")
        if schema_version not in SUPPORTED_ARCHIVE_SCHEMA_VERSIONS:
            raise ValueError("逻辑记录无效")
        records = records_payload.get("records")
        expected = set(expected_tables)
        legacy_video_tables = {"video_analyses", "video_frames"}
        legacy_expected = expected - legacy_video_tables
        if not isinstance(records, dict) or (
            set(records) != expected and not (schema_version in {1, 2, 3, 4} and set(records) == legacy_expected)
        ):
            raise ValueError("逻辑记录不完整")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for table in expected_tables:
            if schema_version in {1, 2, 3, 4} and table in legacy_video_tables and table not in records:
                normalized[table] = []
                continue
            rows = records[table]
            expected_columns = BACKUP_TABLE_COLUMNS[table]
            legacy_evidence_columns = tuple(
                column for column in expected_columns if column != "locator_hash"
            )
            legacy_columns = tuple(column for column in expected_columns if column != "source_date")
            legacy_appended_source_date_columns = legacy_columns + ("source_date",)
            legacy_job_columns = tuple(
                column
                for column in expected_columns
                if column not in {"retry_count", "lease_token", "lease_expires_at"}
            )
            legacy_attempt_columns = tuple(
                column for column in expected_columns if column != "lease_token"
            )
            if not isinstance(rows, list):
                raise ValueError("逻辑记录无效")
            normalized_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("逻辑记录无效")
                columns = tuple(row)
                if columns == expected_columns:
                    normalized_rows.append({column: row[column] for column in expected_columns})
                elif schema_version in {1, 2, 3} and table == "evidence" and columns == legacy_evidence_columns:
                    try:
                        locator = json.loads(row["locator_json"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise ValueError("逻辑记录无效") from exc
                    if not isinstance(locator, dict):
                        raise ValueError("逻辑记录无效")
                    locator_json = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    normalized_rows.append({
                        **{column: row[column] for column in legacy_evidence_columns},
                        "locator_json": locator_json,
                        "locator_hash": hashlib.sha256(locator_json.encode("utf-8")).hexdigest(),
                    })
                elif schema_version == 1 and table == "sources" and columns in {legacy_columns, legacy_appended_source_date_columns}:
                    normalized_rows.append({
                        column: row[column] if column in row else None
                        for column in expected_columns
                    })
                elif schema_version in {1, 2} and table == "jobs" and columns == legacy_job_columns:
                    normalized_rows.append({
                        column: (
                            max(int(row.get("attempt_count", 0)) - 1, 0)
                            if column == "retry_count"
                            else row[column] if column in row else None
                        )
                        for column in expected_columns
                    })
                elif schema_version in {1, 2} and table == "job_attempts" and columns == legacy_attempt_columns:
                    normalized_rows.append({
                        column: row[column] if column in row else None
                        for column in expected_columns
                    })
                else:
                    raise ValueError("逻辑记录无效")
            normalized[table] = normalized_rows
        return normalized

    @staticmethod
    def _validate_video_records(records: dict[str, list[dict[str, Any]]]) -> None:
        artifacts = {row["sha256"] for row in records["artifacts"] if isinstance(row.get("sha256"), str)}
        versions = {row["id"]: row for row in records["content_versions"]}
        analyses: dict[str, dict[str, Any]] = {}
        analysis_identities: set[tuple[str, str, str]] = set()
        for analysis in records["video_analyses"]:
            analysis_id = analysis.get("id")
            version_id = analysis.get("content_version_id")
            analyzer_name = analysis.get("analyzer_name")
            config_hash = analysis.get("config_hash")
            if (
                not isinstance(analysis_id, str)
                or not isinstance(version_id, str)
                or not isinstance(analyzer_name, str)
                or not analyzer_name
                or not isinstance(config_hash, str)
                or not config_hash
                or analysis_id in analyses
            ):
                raise ValueError("视频记录无效")
            version = versions.get(version_id)
            if version is None or version.get("media_type") not in {"video/mp4", "video/webm"}:
                raise ValueError("视频记录无效")
            identity = (version_id, analyzer_name, config_hash)
            if identity in analysis_identities:
                raise ValueError("视频记录无效")
            try:
                metadata = json.loads(analysis["metadata_json"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("视频记录无效") from exc
            if (
                not isinstance(metadata, dict)
                or not isinstance(metadata.get("container_name"), str)
                or not metadata["container_name"]
                or not isinstance(metadata.get("duration_ms"), int)
                or metadata["duration_ms"] <= 0
            ):
                raise ValueError("视频记录无效")
            for dimension in ("width", "height"):
                value = metadata.get(dimension)
                if value is not None and (not isinstance(value, int) or value <= 0):
                    raise ValueError("视频记录无效")
            analyses[analysis_id] = analysis
            analysis_identities.add(identity)

        frame_ids: set[str] = set()
        frame_identities: set[tuple[str, int]] = set()
        for frame in records["video_frames"]:
            frame_id = frame.get("id")
            analysis_id = frame.get("video_analysis_id")
            sha256 = frame.get("artifact_sha256")
            ordinal = frame.get("ordinal")
            time_ms = frame.get("time_ms")
            if (
                not isinstance(frame_id, str)
                or frame_id in frame_ids
                or not isinstance(analysis_id, str)
                or analysis_id not in analyses
                or not isinstance(sha256, str)
                or sha256 not in artifacts
                or not isinstance(ordinal, int)
                or ordinal < 0
                or not isinstance(time_ms, int)
                or time_ms < 0
            ):
                raise ValueError("视频记录无效")
            identity = (analysis_id, ordinal)
            if identity in frame_identities:
                raise ValueError("视频记录无效")
            for dimension in ("width", "height"):
                value = frame.get(dimension)
                if value is not None and (not isinstance(value, int) or value <= 0):
                    raise ValueError("视频记录无效")
            frame_ids.add(frame_id)
            frame_identities.add(identity)

    @staticmethod
    def _validate_derived_evidence_chain(records: dict[str, list[dict[str, Any]]]) -> None:
        versions = {row["id"]: row for row in records["content_versions"]}
        representations = {row["id"]: row for row in records["representations"]}
        evidence = {row["id"]: row for row in records["evidence"]}
        chunks_by_representation: dict[str, list[dict[str, Any]]] = {}
        for chunk in records["search_chunks"]:
            chunks_by_representation.setdefault(chunk["representation_id"], []).append(chunk)
        evidence_by_representation: dict[str, list[dict[str, Any]]] = {}
        for item in records["evidence"]:
            representation = representations.get(item["representation_id"])
            version = versions.get(item["content_version_id"])
            if representation is None or version is None:
                raise ValueError("派生证据链无效")
            if (
                representation["content_version_id"] != item["content_version_id"]
                or version["artifact_sha256"] != item["artifact_sha256"]
                or representation["config_hash"] != item["parser_config_hash"]
            ):
                raise ValueError("派生证据链无效")
            try:
                locator = json.loads(item["locator_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("派生证据链无效") from exc
            if not isinstance(locator, dict):
                raise ValueError("派生证据链无效")
            locator_json = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if (
                item["locator_json"] != locator_json
                or item["locator_hash"] != hashlib.sha256(locator_json.encode("utf-8")).hexdigest()
                or item["excerpt_hash"] != hashlib.sha256(item["excerpt"].encode("utf-8")).hexdigest()
            ):
                raise ValueError("派生证据链无效")
            evidence_by_representation.setdefault(item["representation_id"], []).append(item)
        citation_counts: dict[str, int] = {}
        for citation in records["citations"]:
            evidence_id = citation["evidence_id"]
            if evidence_id not in evidence:
                raise ValueError("派生证据链无效")
            citation_counts[evidence_id] = citation_counts.get(evidence_id, 0) + 1
        if any(count > 1 for count in citation_counts.values()):
            raise ValueError("派生证据链无效")

        def extraction_is_complete(representation: dict[str, Any], version: dict[str, Any]) -> bool:
            chunks = sorted(chunks_by_representation.get(representation["id"], []), key=lambda item: item["ordinal"])
            text = representation["text_content"]
            expected_chunks = [text[offset:offset + 1200] for offset in range(0, len(text), 1200)] or [""]
            representation_evidence = evidence_by_representation.get(representation["id"], [])
            return (
                len(chunks) == len(expected_chunks)
                and [item["ordinal"] for item in chunks] == list(range(len(expected_chunks)))
                and [item["text_content"] for item in chunks] == expected_chunks
                and not any(
                    item["source_id"] != version["source_id"]
                    or item["content_version_id"] != version["id"]
                    or item["text_hash"] != hashlib.sha256(item["text_content"].encode("utf-8")).hexdigest()
                    for item in chunks
                )
                and bool(representation_evidence)
                and all(bool(item["is_validated"]) and citation_counts.get(item["id"]) == 1 for item in representation_evidence)
            )

        for version_id, version in versions.items():
            if version["completeness"] != "complete":
                continue
            if not any(
                representation["kind"] == "extraction" and extraction_is_complete(representation, version)
                for representation in representations.values()
                if representation["content_version_id"] == version_id
            ):
                raise ValueError("派生证据链无效")

    @classmethod
    def _backup_records(cls, records_payload: Any) -> dict[str, list[dict[str, Any]]]:
        records = cls._logical_records(records_payload, BACKUP_TABLES)
        cls._validate_backup_catalog(records["backups"])
        cls._validate_video_records(records)
        cls._validate_derived_evidence_chain(records)
        return records

    @staticmethod
    def _validate_backup_catalog(rows: list[dict[str, Any]]) -> None:
        """Reject untrusted backup catalog rows before a PostgreSQL target is touched."""
        identifiers: set[str] = set()
        archive_names: set[str] = set()
        for row in rows:
            identifier = row["id"]
            archive_name = row["archive_name"]
            manifest_sha256 = row["manifest_sha256"]
            state = row["state"]
            created_at = row["created_at"]
            if not all(isinstance(value, str) and value.strip() for value in row.values()):
                raise ValueError("备份目录记录无效")
            if not TransferService._safe_archive_basename(archive_name):
                raise ValueError("备份目录记录无效")
            if len(manifest_sha256) != 64 or any(character not in "0123456789abcdefABCDEF" for character in manifest_sha256):
                raise ValueError("备份目录记录无效")
            if state not in BACKUP_CATALOG_STATES:
                raise ValueError("备份目录记录无效")
            try:
                timestamp = f"{created_at[:-1]}+00:00" if created_at.endswith("Z") else created_at
                if datetime.fromisoformat(timestamp).tzinfo is None:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("备份目录记录无效") from exc
            if identifier in identifiers or archive_name in archive_names:
                raise ValueError("备份目录记录无效")
            identifiers.add(identifier)
            archive_names.add(archive_name)

    @staticmethod
    def _safe_archive_basename(archive_name: str) -> bool:
        """Accept only portable Windows-safe backup archive basenames."""
        if not SAFE_ARCHIVE_BASENAME.fullmatch(archive_name):
            return False
        if any(character in archive_name for character in '<>:"/\\|?*'):
            return False
        if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in archive_name):
            return False
        stem = archive_name.split(".", 1)[0].rstrip(" .").upper()
        return stem not in WINDOWS_RESERVED_BASENAMES

    @classmethod
    def _export_records(cls, records_payload: Any) -> dict[str, list[dict[str, Any]]]:
        records = cls._logical_records(records_payload, EXPORT_TABLES)
        cls._validate_video_records(records)
        cls._validate_derived_evidence_chain(records)
        return records

    def _restore_archive(self, archive_path: Path, target_root: str, target_database_url: str | None = None) -> dict[str, Any]:
        verification = self.verify_archive(archive_path)
        if not verification["valid"]:
            errors = verification["errors"]
            if len(errors) == 1 and errors[0] in {
                "逻辑记录无效",
                "逻辑记录不完整",
                "备份目录记录无效",
                "视频记录无效",
                "artifact 记录无效",
                "SQLite 状态快照无效",
                "SQLite 状态快照与逻辑记录不一致",
            }:
                raise ValueError(errors[0])
            raise ValueError("归档验证失败")
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            archive_type = manifest.get("archive_type")
            if archive_type not in {"backup", "export"}:
                raise ValueError("不支持的归档类型")
            entry_names = {entry["path"] for entry in manifest["entries"]}
            if "state/knowledge.db" not in entry_names:
                records_payload = self._records_payload(archive)
                records = self._backup_records(records_payload) if archive_type == "backup" else self._export_records(records_payload)
                if not target_database_url or database_backend(target_database_url) != "postgresql":
                    raise ValueError("PostgreSQL 备份还原需要新的 target_database_url")
                target = self._target_paths(target_root)
                from app.adapters.postgres import PostgresRepository

                target_repository = PostgresRepository(
                    target_database_url,
                    Path(__file__).resolve().parents[2] / "migrations" / "postgresql",
                )
                target_repository.assert_empty_restore_target()
                target_repository.migrate_to_head()
                target_repository.initialize()
                if target_repository.has_user_records():
                    raise ValueError("PostgreSQL 还原目标必须为空")
                target.create()
                self._extract_artifacts(archive, manifest, target)
                if archive_type == "backup":
                    target_repository.prepare_backup_restore()
                    target_repository.insert_backup_rows(records)
                else:
                    target_repository.insert_export_rows(records)
                artifact_hashes = [row["sha256"] for row in records["artifacts"] if isinstance(row.get("sha256"), str)]
            else:
                target = self._target_paths(target_root)
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
        with self._backup_lock, zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("archive_type") != "export":
                raise ValueError("仅支持 reimport 导出归档")
            records_payload = self._records_payload(archive)
            records = self._export_records(records_payload)
            tables = EXPORT_TABLES
            primary_keys = {
                "artifacts": ("sha256",), "sources": ("id",), "source_metadata_revisions": ("id",), "content_versions": ("id",),
                "video_analyses": ("id",), "video_frames": ("id",), "source_relations": ("id",),
                "representations": ("id",), "search_chunks": ("id",), "evidence": ("id",), "citations": ("id",), "knowledge": ("id",),
                "knowledge_evidence": ("knowledge_id", "evidence_id"), "external_cards": ("id",), "topics": ("id",), "topic_sources": ("topic_id", "source_id"),
            }
            unique_keys = {"external_cards": ("card_type", "url"), "topics": ("name",), "source_metadata_revisions": ("source_id", "ordinal"), "content_versions": ("source_id", "ordinal"), "video_analyses": ("content_version_id", "analyzer_name", "config_hash"), "video_frames": ("video_analysis_id", "ordinal"), "search_chunks": ("representation_id", "ordinal"), "source_relations": ("source_id", "related_source_id", "relation_type")}
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
            artifact_entries = self._entry_map(manifest)
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
                        stage = self.artifacts.staging_path()
                        try:
                            actual_hash, actual_size = self._sha256_member(archive, name)
                            if actual_hash != sha256 or actual_size != artifact_entries[name]["byte_size"]:
                                raise ValueError("导入 artifact 哈希或大小不匹配")
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
