#!/usr/bin/env python3
"""Build a local-only, auditable V1 process archive using the standard library."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ARCHIVE_SCHEMA_VERSION = 2
ARCHIVE_TYPE = "v1_current_audit"
ARCHIVE_STATUS = "V1 Candidate / BLOCKED"
REPORT_SCHEMA_VERSION = 1
REPORT_SCHEMA_PATH = "docs/v1-archive/report-schema-v1.json"
LEGACY_REPORT_REGISTER_PATH = "docs/v1-archive/legacy-report-register.json"
SNAPSHOT_REGISTER_PATH = "docs/v1-archive/snapshot-register.json"
REPORT_ID_PATTERN = re.compile(r"^RPT-[A-Z0-9][A-Z0-9._-]*$")
UTC_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
SEMVER_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
REPORT_KINDS = frozenset({"archive_snapshot", "version_summary", "development", "testing", "acceptance", "infrastructure"})
REPORT_AUTHOR_ROLES = frozenset({"development", "testing", "acceptance", "release_management", "infrastructure"})
REPORT_INDEPENDENCE = frozenset({"independent", "non_independent", "not_applicable"})
REPORT_DECISION_SCOPES = frozenset({"archive_local", "version_archive", "release"})
REPORT_VERDICTS = frozenset({"accepted", "rejected", "blocked", "not_applicable"})
REPORT_GATE_STATUSES = frozenset({"passed", "blocked", "not_applicable"})
REPORT_DEFECT_RELATIONSHIPS = frozenset({"discovered", "reproduced", "repaired", "retested", "accepted", "rejected", "noted"})
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?$")
SOURCE_RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z?$")
URL_USERINFO_PATTERN = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(?i)\b[a-z]:[\\/][^\s\"'<>|]*")
UNIX_HOME_PATH_PATTERN = re.compile(r"(?:(?<=\s)|^)/(?:Users|home)/[^\s\"'<>|]*")
USER_CONFIG_PATH_PATTERN = re.compile(r"(?i)\b[a-z]:[\\/]Users[\\/]")
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:[\"']\s*(?:password|passwd|secret|token|api[-_ ]?key|authorization)\s*[\"']\s*:|\b(?:password|passwd|secret|token|api[-_ ]?key|authorization)\b\s*[:=])"
)
COOKIE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:[\"']\s*(?:set-cookie|cookie)\s*[\"']\s*:|\b(?:set-cookie|cookie)\b\s*[:=])"
)
RUNTIME_OUTPUT_KEYS = frozenset({"output", "stdout", "stderr", "response", "traceback", "stacktrace"})
PROHIBITED_COMPONENTS = frozenset({"data", ".venv", "node_modules", "__pycache__"})
PROHIBITED_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3", ".zip", ".part"})

ROOT_FILES = ("Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore")
EXACT_BASELINE_FILES = (
    "backend/alembic.ini",
    "backend/requirements.lock",
    "backend/models.lock.json",
    "frontend/index.html",
    "frontend/nginx.conf",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
    "archives/README.md",
)
SOURCE_DIRECTORIES = (
    "backend/app",
    "backend/alembic",
    "backend/migrations",
    "frontend/src",
    "scripts",
    "docs",
    "tests/unit",
    "tests/fixtures",
    "reports/development",
    "reports/testing",
    "reports/infrastructure",
    "reports/versions",
)
ALLOWED_SUFFIXES = frozenset({".py", ".ps1", ".md", ".json", ".sql", ".ts", ".tsx", ".css", ".yml", ".yaml", ".ini", ".lock"})
PREDECESSOR_REGISTER_PATH = "docs/v1-archive/predecessor-register.json"
DEFECT_LEDGER_PATH = "docs/v1-archive/defect-ledger.json"
DEFECT_LEDGER_KEYS = ("defect_id", "severity", "summary", "discovery", "retest", "disposition")
DEFECT_ID_PATTERN = re.compile(r"^DEF-[A-Z0-9][A-Z0-9-]*$")
REPORT_DEFECTS = {
    "reports/testing/20260728T225152Z-independent-test-report.md": [
        "DEF-ING-001", "DEF-BACK-001", "DEF-LOC-001", "DEF-REIMPORT-001", "DEF-LIFE-001", "DEF-JOB-001", "DEF-SEC-001", "DEF-JOB-002", "DEF-META-001", "DEF-SEARCH-001", "DEF-PORT-001",
    ],
    "reports/testing/20260728T172345Z-independent-retest-report.md": ["DEF-ING-001", "DEF-BACK-001", "DEF-LOC-001", "DEF-REIMPORT-001", "DEF-LIFE-001", "DEF-JOB-001", "DEF-SEC-001", "DEF-JOB-002", "DEF-PG-ROUTING-001"],
    "reports/testing/20260728T181807Z-database-frontend-independent-retest.md": ["DEF-PG-ROUTING-001", "DEF-PG-001"],
    "reports/testing/20260729-1127-postgres-regression-check.md": ["DEF-PG-CATALOG-001"],
    "reports/testing/20260729T050712Z-independent-pg-backup-retest.md": ["DEF-PG-CATALOG-001", "DEF-EXPORT-001"],
    "reports/testing/20260729T061118Z-independent-backup-export-retest.md": ["DEF-EXPORT-001", "DEF-BACK-WIN-001", "DEF-BACK-002", "DEF-REIMPORT-WIN-001"],
    "reports/testing/20260729T070015Z-independent-windows-backup-retest.md": ["DEF-BACK-WIN-001", "DEF-BACK-002", "DEF-REIMPORT-WIN-001", "DEF-PG-001"],
    "reports/testing/20260729T164836Z-independent-port-lifecycle-retest.md": ["DEF-PG-001"],
    "reports/testing/20260730T120300Z-independent-archive-review-remediation.md": ["DEF-ARCH-001", "DEF-ARCH-002", "DEF-ARCH-003", "DEF-ARCH-004"],
    "reports/testing/20260730T123000Z-independent-successor-archive-rejection.md": ["DEF-ARCH-005", "DEF-ARCH-006"],
    "reports/testing/20260730T131500Z-independent-archive-contract-review.md": ["DEF-ARCH-007", "DEF-ARCH-008", "DEF-ARCH-009"],
    "reports/testing/20260730T134500Z-independent-archive-contract-rerun.md": ["DEF-ARCH-004", "DEF-ARCH-005", "DEF-ARCH-006", "DEF-ARCH-007", "DEF-ARCH-008", "DEF-ARCH-009"],
    "reports/testing/20260730T141000Z-independent-successor-archive-acceptance-rejection.md": ["DEF-ARCH-010"],
    "reports/development/20260728T165623Z-defect-fix-report.md": ["DEF-ING-001", "DEF-BACK-001", "DEF-LOC-001", "DEF-REIMPORT-001", "DEF-LIFE-001", "DEF-JOB-001", "DEF-SEC-001", "DEF-JOB-002", "DEF-META-001", "DEF-SEARCH-001"],
    "reports/development/20260728T173841Z-postgresql-url-selection-repair.md": ["DEF-PG-ROUTING-001"],
    "reports/development/20260728T-postgres-repository-repair.md": ["DEF-PG-001", "DEF-PORT-001"],
    "reports/development/20260729-041016-postgres-backup-catalog-repair.md": ["DEF-PG-CATALOG-001"],
    "reports/development/20260729T053724Z-pg-backup-catalog-portable-export-repair.md": ["DEF-PG-CATALOG-001", "DEF-EXPORT-001"],
    "reports/development/20260729T062534Z-backup-export-repair.md": ["DEF-BACK-WIN-001", "DEF-REIMPORT-WIN-001"],
}


class ArchiveError(ValueError):
    """Raised when the archive boundary or source material is invalid."""


@dataclass(frozen=True)
class SourceRecord:
    archive_path: str
    source_path: str
    source_sha256: str
    source_byte_size: int
    tier: str
    category: str
    transformation: str
    source_run_id: str | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ArchiveError(f"归档基线必须为 UTF-8 文本：{path.name}") from error


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _safe_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path).lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ArchiveError(f"JSON 格式无效：{path.name}") from error


def _validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ArchiveError("run-id 必须为 UTC 时间戳并且只含安全字符")


def _validate_output_root(repository_root: Path, output_root: Path, run_id: str) -> tuple[Path, Path]:
    expected_root = (repository_root / "archives").resolve()
    resolved_output = output_root.resolve()
    if resolved_output != expected_root:
        raise ArchiveError("输出目录必须是仓库内的 archives 目录")
    if any(part.casefold() == "data" for part in resolved_output.parts):
        raise ArchiveError("不得向 data 目录写入归档")
    archive = resolved_output / f"V1-current-audit-{run_id}"
    archive_zip = resolved_output / f"V1-current-audit-{run_id}.zip"
    if archive.exists() or archive_zip.exists():
        raise ArchiveError("目标归档目录或 ZIP 已存在，归档不可原地覆盖")
    return archive, archive_zip


def _is_allowed_source_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.suffix.casefold() in ALLOWED_SUFFIXES


def _collect_t0_sources(repository_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for relative in ROOT_FILES + EXACT_BASELINE_FILES:
        path = repository_root / relative
        if not path.is_file() or path.is_symlink():
            raise ArchiveError(f"缺少必需的归档基线文件：{relative}")
        candidates.add(path)
    for relative in SOURCE_DIRECTORIES:
        directory = repository_root / relative
        if not directory.is_dir() or directory.is_symlink():
            raise ArchiveError(f"缺少必需的归档基线目录：{relative}")
        for path in directory.rglob("*"):
            if _is_allowed_source_file(path):
                candidates.add(path)
    sources = sorted(candidates, key=lambda item: _posix_relative(item, repository_root))
    if not sources:
        raise ArchiveError("归档基线为空")
    return sources


def _sanitize_text(value: str, repository_root: Path) -> str:
    root_text = str(repository_root.resolve())
    result = value.replace(root_text, "<repo>")
    result = result.replace(root_text.replace("/", "\\"), "<repo>")
    result = URL_USERINFO_PATTERN.sub(r"\1", result)
    result = WINDOWS_ABSOLUTE_PATH_PATTERN.sub("<local-path>", result)
    return UNIX_HOME_PATH_PATTERN.sub("<local-path>", result)


def _scan_t1_text(value: str) -> dict[str, int]:
    matches: dict[str, int] = {}
    for rule, pattern in (
        ("url_userinfo", URL_USERINFO_PATTERN),
        ("sensitive_assignment", SENSITIVE_ASSIGNMENT_PATTERN),
        ("cookie_assignment", COOKIE_ASSIGNMENT_PATTERN),
    ):
        count = len(pattern.findall(value))
        if count:
            matches[rule] = count
    return matches


def _sensitive_json_key_rule(key: str) -> str | None:
    normalized = re.sub(r"[-_ ]", "", key.casefold())
    if normalized in {"cookie", "setcookie"} or normalized.endswith("cookie"):
        return "cookie_assignment"
    if normalized in {"password", "passwd", "secret", "token", "apikey", "authorization"}:
        return "sensitive_assignment"
    if normalized.endswith(("password", "passwd", "secret", "token", "apikey", "authorization")):
        return "sensitive_assignment"
    return None


def _scan_t1_json(value: Any) -> dict[str, int]:
    matches: dict[str, int] = {}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, nested_value in item.items():
                rule = _sensitive_json_key_rule(str(key))
                if rule is not None:
                    matches[rule] = matches.get(rule, 0) + 1
                visit(nested_value)
        elif isinstance(item, list):
            for nested_value in item:
                visit(nested_value)

    visit(value)
    return matches


def _is_runtime_output_key(key: str | None) -> bool:
    normalized = (key or "").casefold()
    return normalized in RUNTIME_OUTPUT_KEYS or any(
        normalized.endswith(f"_{output_key}") or normalized.startswith(f"{output_key}_")
        for output_key in RUNTIME_OUTPUT_KEYS
    )


def _is_pid_key(key: str | None) -> bool:
    normalized = (key or "").casefold()
    return normalized == "pid" or normalized.endswith("_pid") or normalized.startswith("pid_") or "_pid_" in normalized or "process_id" in normalized


def _sanitize_runtime_value(value: Any, repository_root: Path, key: str | None = None) -> Any:
    if _is_pid_key(key):
        return "<redacted-process-id>"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_runtime_value(item_value, repository_root, str(item_key))
            for item_key, item_value in value.items()
            if not _is_runtime_output_key(str(item_key))
        }
    if isinstance(value, list):
        return [_sanitize_runtime_value(item, repository_root, key) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value, repository_root)
    return value


def _validate_t1_source(path: Path, source_relative: str, repository_root: Path) -> Any:
    source_parts = PurePosixPath(source_relative).parts
    if not _is_safe_archive_path(source_relative) or not source_relative.startswith("tests/runtime/"):
        raise ArchiveError(f"运行证据来源不允许：{source_relative}")
    if any(part.casefold() in PROHIBITED_COMPONENTS | {"artifacts", "logs"} for part in source_parts):
        raise ArchiveError(f"运行证据来源不允许：{source_relative}")
    if not path.is_file() or path.is_symlink():
        raise ArchiveError(f"白名单运行证据不存在或不可读取：{source_relative}")
    if path.suffix.casefold() != ".json" or path.suffix.casefold() in PROHIBITED_SUFFIXES:
        raise ArchiveError(f"运行证据类型不允许：{source_relative}")
    raw = _read_text(path)
    findings = _scan_t1_text(raw)
    payload = _safe_json(path)
    for rule, count in _scan_t1_json(payload).items():
        findings[rule] = findings.get(rule, 0) + count
    if findings:
        rules = ", ".join(sorted(findings))
        raise ArchiveError(f"运行证据触发敏感规则：{source_relative}（{rules}）")
    return _sanitize_runtime_value(payload, repository_root)


def _is_safe_archive_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or ":" in value:
        return False
    path = PurePosixPath(value)
    return all(part not in {"", ".", ".."} for part in path.parts)


def _load_allowlist(repository_root: Path) -> list[dict[str, Any]]:
    payload = _safe_json(repository_root / "docs/v1-archive/evidence-allowlist.json")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), list):
        raise ArchiveError("运行证据白名单格式无效")
    entries: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_destinations: set[str] = set()
    for item in payload["entries"]:
        if not isinstance(item, dict):
            raise ArchiveError("运行证据白名单条目无效")
        source = item.get("source")
        destination = item.get("archive_path")
        if not isinstance(source, str) or not isinstance(destination, str):
            raise ArchiveError("运行证据白名单缺少路径")
        if not source.startswith("tests/runtime/") or not destination.startswith("evidence/runtime/"):
            raise ArchiveError("运行证据白名单路径越界")
        if source in seen_sources or destination in seen_destinations or not _is_safe_archive_path(destination):
            raise ArchiveError("运行证据白名单含重复或不安全路径")
        if (
            item.get("tier") != "T1"
            or not isinstance(item.get("requirements"), list)
            or not isinstance(item.get("defects"), list)
            or not isinstance(item.get("purpose"), str)
            or not item["purpose"].strip()
        ):
            raise ArchiveError("运行证据白名单元数据无效")
        source_run_id = item.get("source_run_id")
        if not isinstance(source_run_id, str) or not SOURCE_RUN_ID_PATTERN.fullmatch(source_run_id):
            raise ArchiveError("运行证据白名单缺少有效 source_run_id")
        if source_run_id not in source:
            raise ArchiveError("运行证据 source_run_id 与来源路径不一致")
        seen_sources.add(source)
        seen_destinations.add(destination)
        entries.append(item)
    return entries


def _category_for_source(relative: str) -> str:
    if relative.startswith("reports/versions/"):
        return "version"
    if relative.startswith("reports/development/"):
        return "development"
    if relative.startswith("reports/testing/"):
        return "testing"
    if relative.startswith("reports/infrastructure/"):
        return "infrastructure"
    if relative.startswith("docs/"):
        return "documentation"
    if relative.startswith("tests/"):
        return "test_source"
    if relative.startswith("backend/"):
        return "backend_source"
    if relative.startswith("frontend/"):
        return "frontend_source"
    if relative.startswith("scripts/"):
        return "build_script"
    return "project_configuration"


def _copy_baseline_source(source: Path, repository_root: Path, archive_root: Path) -> SourceRecord:
    relative = _posix_relative(source, repository_root)
    destination_relative = f"baseline/{relative}"
    text = _read_text(source)
    sanitized = _sanitize_text(text, repository_root)
    content = sanitized.encode("utf-8")
    transformation = "sanitized_text" if sanitized != text else "copied"
    _write_bytes(archive_root / destination_relative, content)
    return SourceRecord(destination_relative, relative, _sha256_file(source), source.stat().st_size, "T0", _category_for_source(relative), transformation)


def _copy_t1_source(item: dict[str, Any], repository_root: Path, archive_root: Path) -> SourceRecord:
    source_relative = str(item["source"])
    source = repository_root / source_relative
    destination_relative = str(item["archive_path"])
    _write_json(archive_root / destination_relative, _validate_t1_source(source, source_relative, repository_root))
    return SourceRecord(
        destination_relative,
        source_relative,
        _sha256_file(source),
        source.stat().st_size,
        "T1",
        str(item["category"]),
        "sanitized_json",
        str(item["source_run_id"]),
    )


def _git_provenance(repository_root: Path) -> str:
    commands = (
        ("branch", ["git", "-C", str(repository_root), "branch", "--show-current"]),
        ("head", ["git", "-C", str(repository_root), "rev-parse", "HEAD"]),
        ("status", ["git", "-C", str(repository_root), "status", "--short", "--branch"]),
    )
    lines = ["# Git provenance", "", "Raw repository paths, remotes, and commit messages are intentionally not recorded.", ""]
    for label, command in commands:
        completed = subprocess.run(command, cwd=repository_root, capture_output=True, text=True, check=False)
        output = completed.stdout.strip() if completed.returncode == 0 else "unavailable"
        if label == "status":
            output = "\n".join(line.replace(str(repository_root), "<repo>") for line in output.splitlines()) or "clean"
        lines.extend((f"## {label}", "```text", output, "```", ""))
    return "\n".join(lines)


def _git_state(repository_root: Path) -> dict[str, Any]:
    """Record commit head and dirty worktree entries; degrade to dirty when git is unavailable."""
    unavailable = {"dirty": True, "dirty_entries": [], "head": None}
    try:
        head_result = subprocess.run(
            ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
            cwd=repository_root, capture_output=True, encoding="utf-8", errors="replace", check=False,
        )
        status_result = subprocess.run(
            ["git", "-c", "core.quotePath=false", "-C", str(repository_root), "status", "--porcelain"],
            cwd=repository_root, capture_output=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError:
        return unavailable
    if head_result.returncode != 0 or status_result.returncode != 0:
        return unavailable
    head = head_result.stdout.strip() or None
    dirty_entries: list[str] = []
    for line in status_result.stdout.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].split(" -> ")[-1].strip().strip('"')
        if entry:
            dirty_entries.append(entry)
    return {"dirty": bool(dirty_entries), "dirty_entries": sorted(dirty_entries), "head": head}


def _requirements_from_text(value: str) -> list[str]:
    return sorted(set(re.findall(r"\bREQ-\d{3}(?:[a-z])?\b", value)))


def _load_report_schema(repository_root: Path) -> None:
    schema = _safe_json(repository_root / REPORT_SCHEMA_PATH)
    expected_required_fields = {
        "schema_version", "report_id", "recorded_at_utc", "report_kind", "author_role",
        "independence", "product_version", "decision_scope", "verdict", "requirements",
        "defects", "evidence_refs", "release_gates",
    }
    expected_optional_fields = {
        "archive_run_id", "archive_manifest_sha256", "supersedes_report_id", "snapshot_chain",
        "recommended_snapshot_run_id", "summary",
    }
    expected_enums = {
        "report_kind": sorted(REPORT_KINDS),
        "author_role": sorted(REPORT_AUTHOR_ROLES),
        "independence": sorted(REPORT_INDEPENDENCE),
        "decision_scope": sorted(REPORT_DECISION_SCOPES),
        "verdict": sorted(REPORT_VERDICTS),
        "gate_status": sorted(REPORT_GATE_STATUSES),
    }
    expected_safety = {
        "relative_references_only": True,
        "forbidden_content": [
            "command lines",
            "absolute local paths",
            "runtime output bodies",
            "request bodies",
            "process identifiers",
            "credentials",
            "cookies",
            "tokens",
        ],
        "historical_reports": "Historical Markdown reports remain unchanged and are registered as legacy_inferred when no sidecar is present.",
    }
    if (
        not isinstance(schema, dict)
        or schema.get("schema_version") != REPORT_SCHEMA_VERSION
        or schema.get("artifact_type") != "normalized_archive_report_metadata"
        or schema.get("purpose") != "Defines the JSON sidecar contract for normalized archive reports. The paired Markdown file is the human-readable record."
        or set(schema.get("required_fields", [])) != expected_required_fields
        or set(schema.get("optional_fields", [])) != expected_optional_fields
        or schema.get("file_pair") != {
            "markdown_extension": ".md",
            "metadata_extension": ".json",
            "same_stem_required": True,
        }
        or not isinstance(schema.get("enums"), dict)
        or {key: sorted(value) for key, value in schema["enums"].items() if isinstance(value, list)} != expected_enums
        or schema.get("safety") != expected_safety
        or set(schema) != {
            "schema_version", "artifact_type", "purpose", "file_pair", "required_fields",
            "optional_fields", "enums", "safety",
        }
    ):
        raise ArchiveError("归档报告侧车 schema 无效或与执行契约不一致")


def _known_requirement_ids(repository_root: Path) -> set[str]:
    requirements_path = repository_root / "docs/requirements.md"
    if not requirements_path.is_file():
        raise ArchiveError("冻结需求基线不存在")
    requirement_ids = set(_requirements_from_text(_read_text(requirements_path)))
    if not requirement_ids:
        raise ArchiveError("冻结需求基线未定义 REQ 标识")
    return requirement_ids


def _validate_report_text(value: str) -> None:
    if (
        _scan_t1_text(value)
        or WINDOWS_ABSOLUTE_PATH_PATTERN.search(value)
        or UNIX_HOME_PATH_PATTERN.search(value)
        or re.search(r"(?i)\b(?:stdout|stderr|traceback|stacktrace|response)\b", value)
        or re.search(r"(?i)\b(?:pid|process[ _-]?id)\b\s*[:=]?\s*\d+", value)
    ):
        raise ArchiveError("声明式归档报告包含敏感内容、绝对路径或运行输出")


def _validate_report_value(value: Any, key: str | None = None) -> None:
    if key is not None and (
        _sensitive_json_key_rule(key) is not None
        or _is_runtime_output_key(key)
        or _is_pid_key(key)
    ):
        raise ArchiveError("声明式归档报告包含禁止字段")
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise ArchiveError("声明式归档报告键无效")
            _validate_report_value(nested_value, nested_key)
    elif isinstance(value, list):
        for nested_value in value:
            _validate_report_value(nested_value, key)
    elif isinstance(value, str):
        _validate_report_text(value)


def _require_report_string(metadata: dict[str, Any], field: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ArchiveError(f"声明式归档报告缺少有效 {field}")
    return value


def _validate_utc_timestamp(value: str) -> None:
    if not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise ArchiveError("声明式归档报告时间必须为 UTC ISO 8601")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ArchiveError("声明式归档报告时间无效") from error


def _validate_report_references(
    metadata: dict[str, Any],
    known_requirements: set[str],
    known_defects: set[str],
    known_source_paths: set[str],
    known_archive_paths: set[str],
) -> None:
    requirements = metadata.get("requirements")
    if not isinstance(requirements, list) or any(not isinstance(item, str) or item not in known_requirements for item in requirements):
        raise ArchiveError("声明式归档报告引用了未知需求")
    if len(requirements) != len(set(requirements)):
        raise ArchiveError("声明式归档报告含重复需求")

    defects = metadata.get("defects")
    if not isinstance(defects, list):
        raise ArchiveError("声明式归档报告缺少缺陷关系")
    defect_ids: set[str] = set()
    for item in defects:
        if not isinstance(item, dict):
            raise ArchiveError("声明式归档报告缺陷关系无效")
        defect_id = item.get("defect_id")
        relationship = item.get("relationship")
        if (
            not isinstance(defect_id, str)
            or defect_id not in known_defects
            or defect_id in defect_ids
            or relationship not in REPORT_DEFECT_RELATIONSHIPS
        ):
            raise ArchiveError("声明式归档报告引用了未知或重复缺陷")
        defect_ids.add(defect_id)

    evidence_refs = metadata.get("evidence_refs")
    if not isinstance(evidence_refs, list) or any(not isinstance(item, str) for item in evidence_refs):
        raise ArchiveError("声明式归档报告证据引用无效")
    if len(evidence_refs) != len(set(evidence_refs)):
        raise ArchiveError("声明式归档报告含重复证据引用")
    for reference in evidence_refs:
        if not _is_safe_archive_path(reference) or reference not in known_source_paths | known_archive_paths:
            raise ArchiveError("声明式归档报告证据引用越出当前档案")

    release_gates = metadata.get("release_gates")
    if not isinstance(release_gates, list):
        raise ArchiveError("声明式归档报告门禁无效")
    gate_ids: set[str] = set()
    has_blocked_gate = False
    for gate in release_gates:
        if not isinstance(gate, dict):
            raise ArchiveError("声明式归档报告门禁条目无效")
        gate_id = gate.get("gate_id")
        status = gate.get("status")
        gate_requirements = gate.get("requirements")
        if (
            not isinstance(gate_id, str)
            or not re.fullmatch(r"GATE-[A-Z0-9][A-Z0-9._-]*", gate_id)
            or gate_id in gate_ids
            or status not in REPORT_GATE_STATUSES
            or not isinstance(gate_requirements, list)
            or any(not isinstance(item, str) or item not in known_requirements for item in gate_requirements)
            or len(gate_requirements) != len(set(gate_requirements))
        ):
            raise ArchiveError("声明式归档报告门禁条目无效")
        gate_ids.add(gate_id)
        has_blocked_gate = has_blocked_gate or status == "blocked"
    if metadata["decision_scope"] == "release" and metadata["verdict"] == "accepted" and has_blocked_gate:
        raise ArchiveError("发布接受报告不得保留阻塞门禁")


def _load_legacy_report_register(repository_root: Path) -> dict[str, str]:
    payload = _safe_json(repository_root / LEGACY_REPORT_REGISTER_PATH)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "purpose", "entries"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("purpose"), str)
        or not payload["purpose"].strip()
        or not isinstance(payload.get("entries"), list)
    ):
        raise ArchiveError("历史报告登记格式无效")
    registered: dict[str, str] = {}
    for item in payload["entries"]:
        if not isinstance(item, dict) or set(item) != {"source_path", "source_sha256"}:
            raise ArchiveError("历史报告登记条目无效")
        source_path = item["source_path"]
        source_sha256 = item["source_sha256"]
        if (
            not isinstance(source_path, str)
            or not source_path.startswith("reports/")
            or not source_path.endswith(".md")
            or not _is_safe_archive_path(source_path)
            or source_path in registered
            or not isinstance(source_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        ):
            raise ArchiveError("历史报告登记条目无效")
        registered[source_path] = source_sha256
    return registered


def _load_snapshot_register(repository_root: Path, source_records: dict[str, SourceRecord]) -> list[dict[str, Any]]:
    payload = _safe_json(repository_root / SNAPSHOT_REGISTER_PATH)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "purpose", "entries"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("purpose"), str)
        or not payload["purpose"].strip()
        or not isinstance(payload.get("entries"), list)
        or not payload["entries"]
    ):
        raise ArchiveError("快照登记格式无效")
    expected_fields = {
        "run_id", "manifest_sha256", "archive_local_verdict", "acceptance_report",
        "acceptance_report_sha256", "supersedes_run_id",
    }
    entries: list[dict[str, Any]] = []
    seen_runs: set[str] = set()
    previous_run_id: str | None = None
    for item in payload["entries"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise ArchiveError("快照登记条目无效")
        run_id = item["run_id"]
        manifest_sha256 = item["manifest_sha256"]
        verdict = item["archive_local_verdict"]
        acceptance_report = item["acceptance_report"]
        acceptance_report_sha256 = item["acceptance_report_sha256"]
        supersedes_run_id = item["supersedes_run_id"]
        report_record = source_records.get(acceptance_report) if isinstance(acceptance_report, str) else None
        if (
            not isinstance(run_id, str)
            or not RUN_ID_PATTERN.fullmatch(run_id)
            or run_id in seen_runs
            or not isinstance(manifest_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256)
            or verdict not in REPORT_VERDICTS - {"not_applicable"}
            or not isinstance(acceptance_report, str)
            or not acceptance_report.startswith("reports/testing/")
            or report_record is None
            or not acceptance_report.endswith(".md")
            or report_record.source_sha256 != acceptance_report_sha256
            or not isinstance(acceptance_report_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", acceptance_report_sha256)
            or (supersedes_run_id is not None and (not isinstance(supersedes_run_id, str) or not RUN_ID_PATTERN.fullmatch(supersedes_run_id)))
            or supersedes_run_id != previous_run_id
        ):
            raise ArchiveError("快照登记条目无效")
        seen_runs.add(run_id)
        previous_run_id = run_id
        entries.append(item)
    return entries


def _validate_snapshot_chain(metadata: dict[str, Any], snapshot_register: list[dict[str, Any]]) -> None:
    if metadata["report_kind"] != "version_summary":
        if "snapshot_chain" in metadata or "recommended_snapshot_run_id" in metadata:
            raise ArchiveError("只有版本汇总可以声明快照链")
        return
    if metadata["decision_scope"] != "version_archive":
        raise ArchiveError("版本汇总必须使用 version_archive 裁定范围")
    chain = metadata.get("snapshot_chain")
    recommended_run_id = metadata.get("recommended_snapshot_run_id")
    if chain != snapshot_register or not isinstance(recommended_run_id, str):
        raise ArchiveError("版本汇总快照链未与冻结登记一致")
    recommended_entry = next((item for item in snapshot_register if item["run_id"] == recommended_run_id), None)
    if recommended_entry is None or recommended_entry["archive_local_verdict"] != "accepted":
        raise ArchiveError("版本汇总推荐了未接受的快照")


def _validate_declared_archive_identity(metadata: dict[str, Any], snapshot_register: list[dict[str, Any]]) -> None:
    if metadata["report_kind"] != "acceptance":
        return
    run_id = metadata.get("archive_run_id")
    manifest_sha256 = metadata.get("archive_manifest_sha256")
    if (run_id is None) != (manifest_sha256 is None):
        raise ArchiveError("验收报告的归档身份必须完整")
    if run_id is None:
        return
    matching = next((item for item in snapshot_register if item["run_id"] == run_id), None)
    if matching is None or matching["manifest_sha256"] != manifest_sha256 or matching["archive_local_verdict"] != metadata["verdict"]:
        raise ArchiveError("验收报告归档身份未与冻结快照登记一致")


def _report_material(record: SourceRecord, archive_root: Path) -> dict[str, Any]:
    archive_file = archive_root / record.archive_path
    return {
        "archive_path": record.archive_path,
        "archive_sha256": _sha256_file(archive_file),
        "source_path": record.source_path,
        "source_sha256": record.source_sha256,
    }


def _legacy_report_id(source_path: str) -> str:
    return f"LEGACY-{hashlib.sha256(source_path.encode('utf-8')).hexdigest()[:16].upper()}"


def _has_independent_archive_acceptance(entry: dict[str, Any], source_text_by_path: dict[str, str]) -> bool:
    markdown = entry.get("markdown")
    if not isinstance(markdown, dict) or not isinstance(markdown.get("source_path"), str):
        return False
    source_path = markdown["source_path"]
    if entry.get("normalization_status") == "declared":
        metadata = entry.get("declared")
        return (
            isinstance(metadata, dict)
            and metadata.get("report_kind") == "acceptance"
            and metadata.get("decision_scope") == "archive_local"
            and metadata.get("verdict") == "accepted"
            and metadata.get("independence") == "independent"
        )
    text = source_text_by_path[source_path]
    return (
        ("独立" in text or "independent" in text.casefold())
        and re.search(r"(?i)\baccept\b[\s*]+for archive-local acceptance only\.", text) is not None
    )


def _build_report_register(
    archive_root: Path,
    repository_root: Path,
    records: list[SourceRecord],
    known_defects: set[str],
) -> list[dict[str, Any]]:
    _load_report_schema(repository_root)
    known_requirements = _known_requirement_ids(repository_root)
    report_records = [record for record in records if record.source_path.startswith("reports/")]
    source_records = {record.source_path: record for record in records}
    legacy_report_hashes = _load_legacy_report_register(repository_root)
    snapshot_register = _load_snapshot_register(repository_root, source_records)
    markdown_records = {record.source_path: record for record in report_records if record.source_path.endswith(".md")}
    metadata_records = {record.source_path: record for record in report_records if record.source_path.endswith(".json")}
    if len(metadata_records) != sum(record.source_path.endswith(".json") for record in report_records):
        raise ArchiveError("归档报告目录含不支持的文件")
    expected_metadata_paths = {f"{source_path[:-3]}.json" for source_path in markdown_records}
    if set(metadata_records) - expected_metadata_paths:
        raise ArchiveError("声明式归档报告 JSON 缺少同名 Markdown")

    known_source_paths = {record.source_path for record in records}
    known_archive_paths = {record.archive_path for record in records}
    source_text_by_path = {source_path: _read_text(repository_root / source_path) for source_path in markdown_records}
    entries: list[dict[str, Any]] = []
    legacy_source_paths: set[str] = set()
    declared_report_ids: set[str] = set()
    for source_path, markdown_record in sorted(markdown_records.items()):
        markdown_text = source_text_by_path[source_path]
        title = next((line[2:].strip() for line in markdown_text.splitlines() if line.startswith("# ")), source_path)
        metadata_path = f"{source_path[:-3]}.json"
        entry: dict[str, Any] = {
            "category": markdown_record.category,
            "markdown": _report_material(markdown_record, archive_root),
            "title": title,
        }
        if metadata_path not in metadata_records:
            if legacy_report_hashes.get(source_path) != markdown_record.source_sha256:
                raise ArchiveError("新归档报告缺少同名 JSON 侧车")
            entry.update({
                "defects": REPORT_DEFECTS.get(source_path, []),
                "normalization_status": "legacy_inferred",
                "report_id": _legacy_report_id(source_path),
                "requirements": _requirements_from_text(markdown_text),
            })
            legacy_source_paths.add(source_path)
            entries.append(entry)
            continue

        metadata_record = metadata_records[metadata_path]
        metadata_text = _read_text(repository_root / metadata_path)
        _validate_report_text(markdown_text)
        _validate_report_text(metadata_text)
        metadata = _safe_json(repository_root / metadata_path)
        if not isinstance(metadata, dict):
            raise ArchiveError("声明式归档报告 JSON 必须为对象")
        required_fields = {
            "schema_version", "report_id", "recorded_at_utc", "report_kind", "author_role",
            "independence", "product_version", "decision_scope", "verdict", "requirements",
            "defects", "evidence_refs", "release_gates",
        }
        optional_fields = {
            "archive_run_id", "archive_manifest_sha256", "supersedes_report_id", "snapshot_chain",
            "recommended_snapshot_run_id", "summary",
        }
        if set(metadata) - required_fields - optional_fields or not required_fields.issubset(metadata):
            raise ArchiveError("声明式归档报告字段无效")
        _validate_report_value(metadata)
        if metadata.get("schema_version") != REPORT_SCHEMA_VERSION:
            raise ArchiveError("声明式归档报告 schema 版本无效")
        report_id = _require_report_string(metadata, "report_id")
        if not REPORT_ID_PATTERN.fullmatch(report_id) or report_id in declared_report_ids:
            raise ArchiveError("声明式归档报告标识无效或重复")
        declared_report_ids.add(report_id)
        recorded_at_utc = _require_report_string(metadata, "recorded_at_utc")
        _validate_utc_timestamp(recorded_at_utc)
        product_version = _require_report_string(metadata, "product_version")
        if not SEMVER_PATTERN.fullmatch(product_version):
            raise ArchiveError("声明式归档报告产品版本无效")
        for field, allowed_values in (
            ("report_kind", REPORT_KINDS),
            ("author_role", REPORT_AUTHOR_ROLES),
            ("independence", REPORT_INDEPENDENCE),
            ("decision_scope", REPORT_DECISION_SCOPES),
            ("verdict", REPORT_VERDICTS),
        ):
            if metadata.get(field) not in allowed_values:
                raise ArchiveError(f"声明式归档报告 {field} 无效")
        if source_path.startswith("reports/development/") and metadata["report_kind"] != "development":
            raise ArchiveError("开发报告路径与类别不一致")
        if source_path.startswith("reports/testing/") and metadata["report_kind"] not in {"testing", "acceptance"}:
            raise ArchiveError("测试报告路径与类别不一致")
        if source_path.startswith("reports/infrastructure/") and metadata["report_kind"] != "infrastructure":
            raise ArchiveError("基础设施报告路径与类别不一致")
        if source_path.startswith("reports/versions/") and metadata["report_kind"] != "version_summary":
            raise ArchiveError("版本报告路径与类别不一致")
        archive_run_id = metadata.get("archive_run_id")
        if archive_run_id is not None and (not isinstance(archive_run_id, str) or not RUN_ID_PATTERN.fullmatch(archive_run_id)):
            raise ArchiveError("声明式归档报告 run-id 无效")
        manifest_sha256 = metadata.get("archive_manifest_sha256")
        if manifest_sha256 is not None and (not isinstance(manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256)):
            raise ArchiveError("声明式归档报告 manifest 哈希无效")
        supersedes_report_id = metadata.get("supersedes_report_id")
        if supersedes_report_id is not None and (not isinstance(supersedes_report_id, str) or not REPORT_ID_PATTERN.fullmatch(supersedes_report_id)):
            raise ArchiveError("声明式归档报告更正引用无效")
        if "summary" in metadata and (not isinstance(metadata["summary"], str) or not metadata["summary"].strip()):
            raise ArchiveError("声明式归档报告摘要无效")
        _validate_report_references(metadata, known_requirements, known_defects, known_source_paths, known_archive_paths)
        _validate_snapshot_chain(metadata, snapshot_register)
        _validate_declared_archive_identity(metadata, snapshot_register)
        entry.update({
            "declared": metadata,
            "metadata": _report_material(metadata_record, archive_root),
            "normalization_status": "declared",
            "report_id": report_id,
        })
        entries.append(entry)

    if set(legacy_report_hashes) != legacy_source_paths:
        raise ArchiveError("历史报告登记未与无侧车 Markdown 报告完全一致")
    if len({entry["report_id"] for entry in entries}) != len(entries):
        raise ArchiveError("归档报告登记含重复标识")
    entries_by_source = {entry["markdown"]["source_path"]: entry for entry in entries}
    for snapshot in snapshot_register:
        acceptance_entry = entries_by_source.get(snapshot["acceptance_report"])
        if acceptance_entry is None or acceptance_entry["markdown"]["source_sha256"] != snapshot["acceptance_report_sha256"]:
            raise ArchiveError("冻结快照登记验收报告无法追溯")
        metadata = acceptance_entry.get("declared")
        if isinstance(metadata, dict) and (
            metadata.get("report_kind") != "acceptance"
            or metadata.get("archive_run_id") != snapshot["run_id"]
            or metadata.get("archive_manifest_sha256") != snapshot["manifest_sha256"]
            or metadata.get("verdict") != snapshot["archive_local_verdict"]
        ):
            raise ArchiveError("声明式验收报告未与冻结快照登记一致")
    for entry in entries:
        metadata = entry.get("declared")
        if not isinstance(metadata, dict) or metadata.get("report_kind") != "version_summary":
            continue
        recommended_run_id = metadata["recommended_snapshot_run_id"]
        recommended = next(item for item in metadata["snapshot_chain"] if item["run_id"] == recommended_run_id)
        acceptance_entry = entries_by_source[recommended["acceptance_report"]]
        if not _has_independent_archive_acceptance(acceptance_entry, source_text_by_path):
            raise ArchiveError("版本汇总推荐快照缺少独立 archive-local 接受记录")
    return sorted(entries, key=lambda item: item["markdown"]["source_path"])


def _load_defect_ledger(repository_root: Path) -> list[tuple[str, str, str, str, str, str]]:
    ledger_path = repository_root / DEFECT_LEDGER_PATH
    if not ledger_path.is_file():
        raise ArchiveError("缺陷台账不存在")
    payload = _safe_json(ledger_path)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "defects"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("defects"), list)
        or not payload["defects"]
    ):
        raise ArchiveError("缺陷台账格式无效")
    defects: list[tuple[str, str, str, str, str, str]] = []
    seen_ids: set[str] = set()
    for item in payload["defects"]:
        if not isinstance(item, dict) or set(item) != set(DEFECT_LEDGER_KEYS):
            raise ArchiveError("缺陷台账条目无效")
        values = tuple(str(item[key]) for key in DEFECT_LEDGER_KEYS)
        if any(not isinstance(item[key], str) or not item[key].strip() for key in DEFECT_LEDGER_KEYS):
            raise ArchiveError("缺陷台账条目无效")
        defect_id = values[0]
        if not DEFECT_ID_PATTERN.fullmatch(defect_id) or defect_id in seen_ids:
            raise ArchiveError("缺陷台账标识无效或重复")
        seen_ids.add(defect_id)
        defects.append(values)
    return defects


def _write_process_indexes(archive_root: Path, repository_root: Path, records: list[SourceRecord], allowlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    report_records = sorted((record for record in records if record.source_path.startswith("reports/") and record.source_path.endswith(".md")), key=lambda record: record.source_path)
    timeline_lines = [
        "# V1 过程时间线", "", "本索引只链接可审计的工作树文件；未落盘的对话或临时操作不被虚构为完整过程记录。", "",
        "历史报告中的文件名时间、正文时间与交叉引用偶有顺序差异；本索引保留记录顺序，不从文件名时间戳单独推断缺陷因果。", "",
        "## 冻结基线", "", "- [冻结需求](../baseline/docs/requirements.md)、[架构](../baseline/docs/architecture.md)、[API 合约](../baseline/docs/api-contract.md)、[测试计划](../baseline/docs/test-plan.md)。",
        "- [ADR-001](../baseline/docs/decisions/ADR-001-local-first.md) 与 [ADR-002](../baseline/docs/decisions/ADR-002-evidence-immutability.md)。", "", "## 已归档过程报告", "",
    ]
    for record in report_records:
        title = next((line[2:].strip() for line in _read_text(repository_root / record.source_path).splitlines() if line.startswith("# ")), record.source_path)
        timeline_lines.append(f"- [{title}](../{record.archive_path}) (`{record.category}`；来源 `{record.source_path}`)。")
    timeline_lines.extend(("", "## 当前封存", "", "- 本档案记录的是当前工作树快照，不等同于最终发布批准。"))
    _write_bytes(archive_root / "index/process-timeline.md", ("\n".join(timeline_lines) + "\n").encode("utf-8"))

    register: list[dict[str, Any]] = []
    for record in report_records:
        register.append({"archive_path": record.archive_path, "category": record.category, "defects": REPORT_DEFECTS.get(record.source_path, []), "requirements": _requirements_from_text(_read_text(repository_root / record.source_path)), "source_path": record.source_path, "tier": record.tier})
    for item in allowlist:
        register.append({"archive_path": item["archive_path"], "category": item["category"], "defects": item["defects"], "purpose": item["purpose"], "requirements": item["requirements"], "source_path": item["source"], "source_run_id": item["source_run_id"], "tier": item["tier"]})
    _write_json(archive_root / "index/evidence-register.json", {"schema_version": 1, "entries": register})

    defects = _load_defect_ledger(repository_root)
    known_defects = {defect_id for defect_id, _, _, _, _, _ in defects}
    ledger_lines = ["# V1 缺陷账本", "", "每项保留发现、修复和复测线索。`resolved_locally` 仅表示已有代码和本地复测证据，不等同于发布批准。", "", "| ID | 初始等级 | 问题 | 发现报告 | 修复/复测报告 | 当前状态 |", "| --- | --- | --- | --- | --- | --- |"]
    for defect_id, severity, summary, discovery, retest, disposition in defects:
        ledger_lines.append(f"| {defect_id} | {severity} | {summary} | `{discovery}` | {retest} | `{disposition}` |")
    _write_bytes(archive_root / "index/defect-ledger.md", ("\n".join(ledger_lines) + "\n").encode("utf-8"))

    report_register = _build_report_register(archive_root, repository_root, records, known_defects)
    _write_json(archive_root / "index/report-register.json", {"schema_version": 1, "entries": report_register})

    status_lines = [
        "# 当前状态", "", "## 封存结论", "", "**V1 Candidate / BLOCKED**。本档案是可审计的当前工作树快照，不是最终发布包。", "",
        "## 已有本地证据", "", "- SQLite 默认路径、API、作业租约、派生证据链、备份/导出/再导入和 Windows 启动器的本地范围已有开发、独立测试和复测记录。",
        "- `evidence/runtime/` 仅收录白名单中的合成结构化结果；原始隔离目录、数据库、ZIP、日志和原件均未收录。", "", "## 发布门禁", "",
        "- `BLOCKED`：真实 PostgreSQL 源库到独立空目标库的迁移、逻辑还原和查询验证。", "- `BLOCKED`：物理 Docker Compose migrate、API、worker、PostgreSQL、Redis 与 loopback 拓扑验证。", "- `BLOCKED`：Edge 和 Chrome 的完整黑盒 GUI 验收。", "- `OPEN`：真实磁盘/内存阈值、对抗性长运行解析器、扫描 PDF/OCR 与批准模型解析的内容覆盖。", "",
        "完整限制和证据边界见 [归档政策](../baseline/docs/v1-archive/archive-policy.md)。",
    ]
    _write_bytes(archive_root / "index/current-status.md", ("\n".join(status_lines) + "\n").encode("utf-8"))
    return report_register


def _load_validation(validation_path: Path | None, repository_root: Path) -> dict[str, Any]:
    if validation_path is None:
        return {"schema_version": 1, "overall_status": "not_recorded", "results": []}
    if not validation_path.is_file():
        raise ArchiveError("本地验证记录不存在")
    payload = _safe_json(validation_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ArchiveError("本地验证记录格式无效")
    sanitized = _sanitize_runtime_value(payload, repository_root)
    if sanitized.get("overall_status") not in {"passed", "failed", "not_recorded"}:
        raise ArchiveError("本地验证状态无效")
    return sanitized


def _write_validation(archive_root: Path, validation: dict[str, Any]) -> None:
    _write_json(archive_root / "verification/local-validation.json", validation)
    lines = ["# 本地软件验证", "", f"总体状态：`{validation['overall_status']}`。", "", "| 检查 | 状态 | 摘要 |", "| --- | --- | --- |"]
    for result in validation["results"]:
        if not isinstance(result, dict):
            raise ArchiveError("本地验证结果条目无效")
        name = str(result.get("name", "unnamed"))
        status = str(result.get("status", "unknown"))
        summary = str(result.get("summary", ""))
        if _scan_t1_text(name + "\n" + summary):
            raise ArchiveError("本地验证记录包含敏感内容")
        lines.append(f"| {name} | `{status}` | {summary} |")
    if not validation["results"]:
        lines.append("| 尚未提供新验证记录 | `not_recorded` | 归档构建不把缺失的验证伪装为通过。 |")
    _write_bytes(archive_root / "verification/local-validation.md", ("\n".join(lines) + "\n").encode("utf-8"))


def _load_predecessor(repository_root: Path) -> dict[str, Any]:
    register_path = repository_root / PREDECESSOR_REGISTER_PATH
    if not register_path.is_file():
        return {"schema_version": 1, "predecessor_status": "none"}
    register = _safe_json(register_path)
    if not isinstance(register, dict) or register.get("schema_version") != 1:
        raise ArchiveError("前序档案登记格式无效")
    entries = register.get("entries")
    if not isinstance(entries, list):
        raise ArchiveError("前序档案登记条目无效")
    rejected_entries = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("status") == "not_accepted_under_policy"
    ]
    if not rejected_entries:
        return {"schema_version": 1, "predecessor_status": "none"}
    latest = max(rejected_entries, key=lambda entry: str(entry.get("run_id", "")))
    run_id = latest.get("run_id")
    manifest_sha256 = latest.get("manifest_sha256")
    reason = latest.get("reason")
    if (
        not isinstance(run_id, str)
        or not RUN_ID_PATTERN.fullmatch(run_id)
        or not isinstance(manifest_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256)
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ArchiveError("前序档案登记条目无效")
    return {
        "schema_version": 1,
        "predecessor_status": "not_accepted_under_policy",
        "predecessor_run_id": run_id,
        "predecessor_manifest_sha256": manifest_sha256,
        "reason": reason,
    }


def _write_source_inventory(archive_root: Path, records: Iterable[SourceRecord]) -> None:
    entries = [
        {
            "archive_path": record.archive_path,
            "category": record.category,
            "source_byte_size": record.source_byte_size,
            "source_path": record.source_path,
            "source_run_id": record.source_run_id,
            "source_sha256": record.source_sha256,
            "tier": record.tier,
            "transformation": record.transformation,
        }
        for record in sorted(records, key=lambda item: item.archive_path)
    ]
    _write_json(archive_root / "provenance/source-inventory.json", {"schema_version": 1, "entries": entries})


def _iter_archive_files(archive_root: Path) -> list[Path]:
    return sorted((path for path in archive_root.rglob("*") if path.is_file() and not path.is_symlink()), key=lambda path: _posix_relative(path, archive_root))


def _write_manifest(archive_root: Path, run_id: str, validation: dict[str, Any], git_state: dict[str, Any]) -> None:
    entries = []
    for path in _iter_archive_files(archive_root):
        relative = _posix_relative(path, archive_root)
        if relative in {"manifest.json", "manifest.sha256"}:
            continue
        entries.append({"byte_size": path.stat().st_size, "path": relative, "sha256": _sha256_file(path), "tier": "T1" if relative.startswith("evidence/") else "generated" if not relative.startswith("baseline/") else "T0"})
    manifest = {
        "archive_status": ARCHIVE_STATUS,
        "archive_type": ARCHIVE_TYPE,
        "builder": "scripts/archive_v1.py",
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "entries": entries,
        "exclusions": ["data/", "tests/runtime/ except allowlisted synthetic JSON", "*.db", "*.sqlite", "*.zip", "artifacts/", "logs/", ".venv/", "frontend/node_modules/"],
        "git_state": git_state,
        "integrity": {"archive_integrity": "verified"},
        "local_software_validation": validation["overall_status"],
        "release_readiness": "blocked",
        "run_id": run_id,
        "schema_version": ARCHIVE_SCHEMA_VERSION,
    }
    _write_json(archive_root / "manifest.json", manifest)
    _write_bytes(archive_root / "manifest.sha256", f"{_sha256_file(archive_root / 'manifest.json')}  manifest.json\n".encode("ascii"))


def _verify_with_independent_script(verifier_script: Path, archive_path: Path, repository_root: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(verifier_script), "--archive", str(archive_path), "--quiet"],
        cwd=repository_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ArchiveError("独立归档验证器拒绝构建结果")


def _acquire_build_lock(output_root: Path, run_id: str) -> Path:
    lock_path = output_root / f".V1-current-audit-{run_id}.lock"
    try:
        with lock_path.open("x", encoding="ascii") as lock_file:
            lock_file.write("archive-build-lock\n")
    except FileExistsError as error:
        raise ArchiveError("相同 run-id 的归档构建已在进行") from error
    return lock_path


def _publish_archive_without_overwrite(staging: Path, archive_root: Path) -> None:
    try:
        staging.rename(archive_root)
    except FileExistsError as error:
        raise ArchiveError("目标归档目录已被其他构建创建") from error


def _seal_archive_directory(archive_root: Path) -> None:
    if os.name != "nt":
        raise ArchiveError("当前归档策略要求 Windows ACL 封存")
    account = os.environ.get("USERNAME")
    if not account:
        raise ArchiveError("无法确定归档封存账户")
    completed = subprocess.run(
        [
            "icacls",
            str(archive_root),
            "/inheritance:r",
            "/remove:g",
            "OWNER RIGHTS",
            "/grant:r",
            f"{account}:(RX)",
            "/t",
            "/c",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ArchiveError("归档目录 ACL 封存失败")


def _build_zip_without_overwrite(archive_root: Path, archive_zip: Path) -> None:
    staging_zip = archive_zip.with_name(f".{archive_zip.name}.{uuid.uuid4().hex}.staging")
    try:
        with ZipFile(staging_zip, "x", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for path in _iter_archive_files(archive_root):
                relative = _posix_relative(path, archive_root)
                info = ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
        try:
            with archive_zip.open("xb") as destination:
                destination.write(staging_zip.read_bytes())
        except FileExistsError as error:
            raise ArchiveError("目标归档 ZIP 已被其他构建创建") from error
    finally:
        staging_zip.unlink(missing_ok=True)


def build_archive(repository_root: Path, output_root: Path, run_id: str, *, validation_path: Path | None = None, verifier_script: Path | None = None) -> tuple[Path, Path]:
    """Build a V1 archive and independently validate both directory and ZIP outputs."""
    repository_root = repository_root.resolve()
    _validate_run_id(run_id)
    archive_root, archive_zip = _validate_output_root(repository_root, output_root, run_id)
    verifier_script = verifier_script or repository_root / "scripts/verify_v1_archive.py"
    if not verifier_script.is_file():
        raise ArchiveError("独立归档验证器不存在")
    allowlist = _load_allowlist(repository_root)
    validation = _load_validation(validation_path, repository_root)
    git_provenance = _git_provenance(repository_root)
    git_state = _git_state(repository_root)
    if git_state["dirty"]:
        if git_state["head"] is None:
            print("警告：无法读取 Git 状态，归档 manifest 的 git_state 将按 dirty 记录。")
        else:
            print(f"警告：工作树存在 {len(git_state['dirty_entries'])} 项未提交变更，归档 manifest 的 git_state 将标记 dirty。")
    archive_root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _acquire_build_lock(archive_root.parent, run_id)
    staging_parent = Path(tempfile.mkdtemp(prefix="v1-archive-", dir=archive_root.parent))
    staging = staging_parent / archive_root.name
    try:
        staging.mkdir()
        records = [_copy_baseline_source(source, repository_root, staging) for source in _collect_t0_sources(repository_root)]
        records.extend(_copy_t1_source(item, repository_root, staging) for item in allowlist)
        _write_source_inventory(staging, records)
        _write_bytes(staging / "provenance/git-state.txt", git_provenance.encode("utf-8"))
        _write_json(staging / "provenance/predecessor.json", _load_predecessor(repository_root))
        _write_process_indexes(staging, repository_root, records, allowlist)
        _write_validation(staging, validation)
        _write_manifest(staging, run_id, validation, git_state)
        _verify_with_independent_script(verifier_script, staging, repository_root)
        _publish_archive_without_overwrite(staging, archive_root)
        _seal_archive_directory(archive_root)
        _verify_with_independent_script(verifier_script, archive_root, repository_root)
        _build_zip_without_overwrite(archive_root, archive_zip)
        _verify_with_independent_script(verifier_script, archive_root, repository_root)
        _verify_with_independent_script(verifier_script, archive_zip, repository_root)
        return archive_root, archive_zip
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)
        lock_path.unlink(missing_ok=True)


def check_tree(repository_root: Path) -> dict[str, Any]:
    """Run every pre-build validation against the working tree without writing an archive."""
    repository_root = repository_root.resolve()
    allowlist = _load_allowlist(repository_root)
    sources = _collect_t0_sources(repository_root)
    defects = _load_defect_ledger(repository_root)
    staging_parent = Path(tempfile.mkdtemp(prefix="v1-check-tree-"))
    try:
        staging = staging_parent / "working-tree"
        staging.mkdir()
        records = [_copy_baseline_source(source, repository_root, staging) for source in sources]
        records.extend(_copy_t1_source(item, repository_root, staging) for item in allowlist)
        report_register = _write_process_indexes(staging, repository_root, records, allowlist)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)
    return {
        "baseline_files": len(records),
        "defects": len(defects),
        "evidence_entries": len(allowlist),
        "reports": len(report_register),
        "status": "passed",
    }


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建本机 V1 当前可审计档案")
    parser.add_argument("--output-root", type=Path, help="必须为仓库 archives 目录")
    parser.add_argument("--run-id", help="UTC 运行标识，例如 20260730T010203Z")
    parser.add_argument("--validation-json", type=Path, help="可选的脱敏本地验证记录")
    parser.add_argument("--check-tree", action="store_true", help="仅执行构建前校验，不构建归档")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    repository_root = Path(__file__).resolve().parents[1]
    if arguments.check_tree:
        try:
            summary = check_tree(repository_root)
        except ArchiveError as error:
            print(f"工作树预检失败：{error}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if arguments.output_root is None or arguments.run_id is None:
        print("归档构建失败：缺少必需的 --output-root 或 --run-id", file=sys.stderr)
        return 2
    output_root = arguments.output_root if arguments.output_root.is_absolute() else repository_root / arguments.output_root
    validation_path = arguments.validation_json
    if validation_path is not None and not validation_path.is_absolute():
        validation_path = repository_root / validation_path
    try:
        archive_root, archive_zip = build_archive(repository_root, output_root, arguments.run_id, validation_path=validation_path)
    except ArchiveError as error:
        print(f"归档构建失败：{error}", file=sys.stderr)
        return 2
    print(json.dumps({"archive": str(archive_root), "zip": str(archive_zip), "status": ARCHIVE_STATUS}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
