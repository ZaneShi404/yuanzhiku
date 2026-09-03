#!/usr/bin/env python3
"""Independently verify a V1 local audit archive without builder imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile


SUPPORTED_ARCHIVE_SCHEMA_VERSIONS = frozenset({1, 2})
ARCHIVE_TYPE = "v1_current_audit"
ARCHIVE_STATUS = "V1 Candidate / BLOCKED"
REPORT_SCHEMA_VERSION = 1
LEGACY_REPORT_REGISTER_PATH = "baseline/docs/v1-archive/legacy-report-register.json"
SNAPSHOT_REGISTER_PATH = "baseline/docs/v1-archive/snapshot-register.json"
REPORT_ID_PATTERN = re.compile(r"^RPT-[A-Z0-9][A-Z0-9._-]*$")
UTC_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$")
SEMVER_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
REPORT_KINDS = frozenset({"archive_snapshot", "version_summary", "development", "testing", "acceptance", "infrastructure", "review"})
REPORT_AUTHOR_ROLES = frozenset({"development", "testing", "acceptance", "review", "release_management", "infrastructure"})
REPORT_INDEPENDENCE = frozenset({"independent", "non_independent", "not_applicable"})
REPORT_DECISION_SCOPES = frozenset({"archive_local", "version_archive", "release"})
REPORT_VERDICTS = frozenset({"accepted", "rejected", "blocked", "not_applicable"})
REPORT_GATE_STATUSES = frozenset({"passed", "blocked", "not_applicable"})
REPORT_DEFECT_RELATIONSHIPS = frozenset({"discovered", "reproduced", "repaired", "retested", "accepted", "rejected", "noted"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_HEAD_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
GIT_STATE_KEYS = frozenset({"head", "dirty", "dirty_entries"})
SOURCE_RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z?$")
URL_USERINFO_PATTERN = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@]+@")
WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"(?i)\b[a-z]:[\\/][^\s\"'<>|]*")
UNIX_HOME_PATH_PATTERN = re.compile(r"(?:(?<=\s)|^)/(?:Users|home)/[^\s\"'<>|]*")
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:[\"']\s*(?:password|passwd|secret|token|api[-_ ]?key|authorization)\s*[\"']\s*:|\b(?:password|passwd|secret|token|api[-_ ]?key|authorization)\b\s*[:=])"
)
COOKIE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:[\"']\s*(?:set-cookie|cookie)\s*[\"']\s*:|\b(?:set-cookie|cookie)\b\s*[:=])"
)
RUNTIME_OUTPUT_KEYS = frozenset({"output", "stdout", "stderr", "response", "traceback", "stacktrace"})
PROHIBITED_PREFIXES = ("data/", "artifacts/", "logs/", "baseline/data/", "baseline/.venv/", "baseline/frontend/node_modules/")
PROHIBITED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".zip", ".part", ".pyc")
REQUIRED_GENERATED_PATHS = frozenset({
    "index/current-status.md",
    "index/defect-ledger.md",
    "index/evidence-register.json",
    "index/process-timeline.md",
    "provenance/git-state.txt",
    "provenance/source-inventory.json",
    "verification/local-validation.json",
    "verification/local-validation.md",
})
REQUIRED_GENERATED_PATHS_V2 = REQUIRED_GENERATED_PATHS | frozenset({"index/report-register.json"})
OPTIONAL_GENERATED_PATHS = frozenset({"provenance/predecessor.json"})
ROOT_BASELINE_FILES = frozenset({"Dockerfile", "docker-compose.yml", ".dockerignore", ".gitignore"})
EXACT_BASELINE_FILES = frozenset({
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
})
BASELINE_DIRECTORIES = (
    "backend/app/",
    "backend/alembic/",
    "backend/migrations/",
    "frontend/src/",
    "scripts/",
    "docs/",
    "tests/unit/",
    "tests/fixtures/",
    "reports/development/",
    "reports/testing/",
    "reports/infrastructure/",
    "reports/versions/",
)
ALLOWED_BASELINE_SUFFIXES = frozenset({".py", ".ps1", ".md", ".json", ".sql", ".ts", ".tsx", ".css", ".yml", ".yaml", ".ini", ".lock"})


class VerificationError(ValueError):
    """Raised when an archive cannot satisfy the V1 audit contract."""


@dataclass(frozen=True)
class Member:
    path: str
    size: int
    content: bytes


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_safe_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or ":" in value:
        return False
    path = PurePosixPath(value)
    return all(part not in {"", ".", ".."} for part in path.parts)


def _is_prohibited_path(value: str) -> bool:
    lowered = value.casefold()
    if lowered.startswith(PROHIBITED_PREFIXES):
        return True
    if lowered.endswith(PROHIBITED_SUFFIXES):
        return True
    return any(part in {".venv", "node_modules", "__pycache__"} for part in PurePosixPath(lowered).parts)


def _decode_json(member: Member) -> object:
    try:
        return json.loads(member.content.decode("utf-8").lstrip("\ufeff"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"JSON 无效：{member.path}") from error


def _read_directory(archive: Path) -> list[Member]:
    if not archive.is_dir() or archive.is_symlink():
        raise VerificationError("归档目录不存在或不是普通目录")
    members: list[Member] = []
    for path in archive.rglob("*"):
        if path.is_symlink():
            raise VerificationError("归档不得包含符号链接")
        if path.is_file():
            relative = path.relative_to(archive).as_posix()
            members.append(Member(relative, path.stat().st_size, path.read_bytes()))
    return members


def _read_zip(archive: Path) -> list[Member]:
    try:
        with ZipFile(archive) as package:
            members: list[Member] = []
            for info in package.infolist():
                if info.is_dir():
                    raise VerificationError("ZIP 不得包含目录成员")
                members.append(Member(info.filename, info.file_size, package.read(info)))
            return members
    except BadZipFile as error:
        raise VerificationError("ZIP 文件无效") from error


def _member_map(members: list[Member]) -> dict[str, Member]:
    result: dict[str, Member] = {}
    for member in members:
        if not _is_safe_path(member.path):
            raise VerificationError("归档包含不安全路径")
        if _is_prohibited_path(member.path):
            raise VerificationError("归档包含被禁止的文件")
        if member.path in result:
            raise VerificationError("归档包含重复成员")
        result[member.path] = member
    return result


def _validate_git_state(manifest: dict[str, object]) -> None:
    git_state = manifest.get("git_state")
    if git_state is None:
        return
    if not isinstance(git_state, dict) or set(git_state) != GIT_STATE_KEYS:
        raise VerificationError("manifest git_state 形状无效")
    head = git_state["head"]
    if head is not None and (not isinstance(head, str) or not GIT_HEAD_PATTERN.fullmatch(head)):
        raise VerificationError("manifest git_state 形状无效")
    if not isinstance(git_state["dirty"], bool):
        raise VerificationError("manifest git_state 形状无效")
    dirty_entries = git_state["dirty_entries"]
    if not isinstance(dirty_entries, list) or any(not isinstance(item, str) for item in dirty_entries):
        raise VerificationError("manifest git_state 形状无效")


def _validate_manifest(member_map: dict[str, Member]) -> dict[str, object]:
    if "manifest.json" not in member_map or "manifest.sha256" not in member_map:
        raise VerificationError("归档缺少 manifest")
    manifest = _decode_json(member_map["manifest.json"])
    if not isinstance(manifest, dict):
        raise VerificationError("manifest 格式无效")
    schema_version = manifest.get("schema_version")
    if schema_version not in SUPPORTED_ARCHIVE_SCHEMA_VERSIONS:
        raise VerificationError("manifest schema 版本无效")
    if manifest.get("archive_type") != ARCHIVE_TYPE or manifest.get("archive_status") != ARCHIVE_STATUS:
        raise VerificationError("归档状态声明无效")
    if manifest.get("release_readiness") != "blocked":
        raise VerificationError("当前 V1 档案不得声明为可发布")
    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict) or integrity.get("archive_integrity") != "verified":
        raise VerificationError("归档完整性状态无效")
    if manifest.get("local_software_validation") not in {"passed", "failed", "not_recorded"}:
        raise VerificationError("本地验证状态无效")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?", run_id):
        raise VerificationError("run-id 无效")
    declared_hash = member_map["manifest.sha256"].content.decode("ascii", errors="strict").strip()
    expected_hash = f"{_sha256(member_map['manifest.json'].content)}  manifest.json"
    if declared_hash != expected_hash:
        raise VerificationError("manifest 自身哈希不匹配")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise VerificationError("manifest entries 无效")
    _validate_git_state(manifest)
    return manifest


def _validate_entries(manifest: dict[str, object], member_map: dict[str, Member]) -> None:
    entries = manifest["entries"]
    assert isinstance(entries, list)
    expected: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise VerificationError("manifest 条目无效")
        path = entry.get("path")
        sha256 = entry.get("sha256")
        size = entry.get("byte_size")
        tier = entry.get("tier")
        if not isinstance(path, str) or not _is_safe_path(path) or _is_prohibited_path(path):
            raise VerificationError("manifest 含不安全条目")
        if path in expected or path in {"manifest.json", "manifest.sha256"}:
            raise VerificationError("manifest 含重复或保留条目")
        if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
            raise VerificationError("manifest 条目哈希无效")
        if not isinstance(size, int) or size < 0 or tier not in {"T0", "T1", "generated"}:
            raise VerificationError("manifest 条目元数据无效")
        expected.add(path)
        actual = member_map.get(path)
        if actual is None or actual.size != size or _sha256(actual.content) != sha256:
            raise VerificationError("归档成员哈希或大小不匹配")
    actual_members = set(member_map) - {"manifest.json", "manifest.sha256"}
    if actual_members != expected:
        raise VerificationError("归档成员集合与 manifest 不一致")
    required_generated_paths = REQUIRED_GENERATED_PATHS_V2 if manifest["schema_version"] == 2 else REQUIRED_GENERATED_PATHS
    if not required_generated_paths.issubset(expected):
        raise VerificationError("归档缺少必需索引或验证记录")
    for path in expected:
        if path.startswith("evidence/") and next(entry for entry in entries if isinstance(entry, dict) and entry.get("path") == path).get("tier") != "T1":
            raise VerificationError("运行证据等级无效")
        if path.startswith("baseline/") and next(entry for entry in entries if isinstance(entry, dict) and entry.get("path") == path).get("tier") != "T0":
            raise VerificationError("基线证据等级无效")


def _validate_archive_layout(manifest: dict[str, object], member_map: dict[str, Member]) -> None:
    entries = manifest["entries"]
    assert isinstance(entries, list)
    entry_by_path = {entry["path"]: entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("path"), str)}
    required_generated_paths = REQUIRED_GENERATED_PATHS_V2 if manifest["schema_version"] == 2 else REQUIRED_GENERATED_PATHS
    allowed_generated_paths = required_generated_paths | OPTIONAL_GENERATED_PATHS
    for path in entry_by_path:
        if path.startswith("baseline/"):
            relative = path.removeprefix("baseline/")
            allowed_root = relative in ROOT_BASELINE_FILES or relative in EXACT_BASELINE_FILES
            allowed_directory = relative.startswith(BASELINE_DIRECTORIES) and PurePosixPath(relative).suffix.casefold() in ALLOWED_BASELINE_SUFFIXES
            if not allowed_root and not allowed_directory:
                raise VerificationError("归档基线含未允许文件")
        elif path.startswith("evidence/runtime/"):
            if not path.endswith(".json"):
                raise VerificationError("运行证据类型无效")
        elif path not in allowed_generated_paths:
            raise VerificationError("归档含未允许的派生文件")
    inventory = _decode_json(member_map["provenance/source-inventory.json"])
    if not isinstance(inventory, dict) or not isinstance(inventory.get("entries"), list):
        raise VerificationError("来源清单格式无效")
    inventory_by_path: dict[str, dict[str, object]] = {}
    for item in inventory["entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("archive_path"), str):
            raise VerificationError("来源清单条目无效")
        archive_path = item["archive_path"]
        if archive_path in inventory_by_path or archive_path not in entry_by_path:
            raise VerificationError("来源清单与 manifest 不一致")
        source_path = item.get("source_path")
        source_hash = item.get("source_sha256")
        source_size = item.get("source_byte_size")
        source_run_id = item.get("source_run_id")
        transformation = item.get("transformation")
        if not isinstance(source_path, str) or not _is_safe_path(source_path):
            raise VerificationError("来源清单路径无效")
        if not isinstance(source_hash, str) or not SHA256_PATTERN.fullmatch(source_hash):
            raise VerificationError("来源清单哈希无效")
        if not isinstance(source_size, int) or source_size < 0 or transformation not in {"copied", "sanitized_text", "sanitized_json"}:
            raise VerificationError("来源清单元数据无效")
        if archive_path.startswith("evidence/runtime/"):
            if not isinstance(source_run_id, str) or not SOURCE_RUN_ID_PATTERN.fullmatch(source_run_id):
                raise VerificationError("运行证据来源运行标识无效")
            if source_run_id not in source_path:
                raise VerificationError("运行证据来源运行标识与路径不一致")
        elif source_run_id is not None:
            raise VerificationError("基线来源不得登记运行标识")
        inventory_by_path[archive_path] = item
    source_entries = {path for path in entry_by_path if path.startswith(("baseline/", "evidence/"))}
    if set(inventory_by_path) != source_entries:
        raise VerificationError("来源清单未覆盖全部源材料")
    for path in source_entries:
        item = inventory_by_path[path]
        if path.startswith("evidence/runtime/"):
            if item.get("tier") != "T1" or item.get("transformation") != "sanitized_json" or not str(item.get("source_path")).startswith("tests/runtime/"):
                raise VerificationError("运行证据来源登记无效")
        elif item.get("tier") != "T0":
            raise VerificationError("基线来源等级无效")
    allowlist_member = member_map.get("baseline/docs/v1-archive/evidence-allowlist.json")
    if allowlist_member is None:
        raise VerificationError("归档缺少运行证据白名单")
    allowlist = _decode_json(allowlist_member)
    if not isinstance(allowlist, dict) or allowlist.get("schema_version") != 1 or not isinstance(allowlist.get("entries"), list):
        raise VerificationError("归档运行证据白名单无效")
    allowlist_by_archive_path: dict[str, dict[str, object]] = {}
    for item in allowlist["entries"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("archive_path"), str)
            or not isinstance(item.get("source"), str)
            or not isinstance(item.get("source_run_id"), str)
            or not isinstance(item.get("purpose"), str)
            or not item["purpose"].strip()
        ):
            raise VerificationError("归档运行证据白名单条目无效")
        archive_path = item["archive_path"]
        source_run_id = item["source_run_id"]
        if archive_path in allowlist_by_archive_path or not archive_path.startswith("evidence/runtime/") or not _is_safe_path(archive_path):
            raise VerificationError("归档运行证据白名单路径无效")
        if not SOURCE_RUN_ID_PATTERN.fullmatch(source_run_id) or source_run_id not in item["source"]:
            raise VerificationError("归档运行证据白名单运行标识无效")
        allowlist_by_archive_path[archive_path] = item
    t1_paths = {path for path in source_entries if path.startswith("evidence/runtime/")}
    if set(allowlist_by_archive_path) != t1_paths:
        raise VerificationError("运行证据未与白名单一致")
    for path in t1_paths:
        inventory_item = inventory_by_path[path]
        allowlist_item = allowlist_by_archive_path[path]
        if inventory_item.get("source_path") != allowlist_item.get("source"):
            raise VerificationError("运行证据来源与白名单不一致")
        if inventory_item.get("source_run_id") != allowlist_item.get("source_run_id"):
            raise VerificationError("运行证据来源运行标识与白名单不一致")

    register = _decode_json(member_map["index/evidence-register.json"])
    if not isinstance(register, dict) or not isinstance(register.get("entries"), list):
        raise VerificationError("证据登记格式无效")
    register_by_path: dict[str, dict[str, object]] = {}
    for item in register["entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("archive_path"), str):
            raise VerificationError("证据登记条目无效")
        archive_path = item["archive_path"]
        if archive_path in register_by_path:
            raise VerificationError("证据登记包含重复归档路径")
        register_by_path[archive_path] = item
    for path in t1_paths:
        register_item = register_by_path.get(path)
        allowlist_item = allowlist_by_archive_path[path]
        if not isinstance(register_item, dict):
            raise VerificationError("运行证据缺少证据登记")
        if register_item.get("source_path") != allowlist_item.get("source"):
            raise VerificationError("运行证据登记来源与白名单不一致")
        if register_item.get("source_run_id") != allowlist_item.get("source_run_id"):
            raise VerificationError("运行证据登记运行标识与白名单不一致")
        if register_item.get("purpose") != allowlist_item.get("purpose"):
            raise VerificationError("运行证据登记用途与白名单不一致")


def _is_runtime_output_key(key: str) -> bool:
    normalized = key.casefold()
    return normalized in RUNTIME_OUTPUT_KEYS or any(
        normalized.endswith(f"_{output_key}") or normalized.startswith(f"{output_key}_")
        for output_key in RUNTIME_OUTPUT_KEYS
    )


def _is_pid_key(key: str | None) -> bool:
    normalized = (key or "").casefold()
    return normalized == "pid" or normalized.endswith("_pid") or normalized.startswith("pid_") or "_pid_" in normalized or "process_id" in normalized


def _validate_runtime_evidence_value(value: object, key: str | None = None) -> None:
    if _is_pid_key(key):
        if value != "<redacted-process-id>":
            raise VerificationError("运行证据包含未脱敏 PID")
        return
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise VerificationError("运行证据键无效")
            if _is_runtime_output_key(nested_key):
                raise VerificationError("运行证据包含被禁止的运行输出")
            _validate_runtime_evidence_value(nested_value, nested_key)
        return
    if isinstance(value, list):
        for item in value:
            _validate_runtime_evidence_value(item, key)


def _sensitive_json_key_rule(key: str) -> str | None:
    normalized = re.sub(r"[-_ ]", "", key.casefold())
    if normalized in {"cookie", "setcookie"} or normalized.endswith("cookie"):
        return "cookie_assignment"
    if normalized in {"password", "passwd", "secret", "token", "apikey", "authorization"}:
        return "sensitive_assignment"
    if normalized.endswith(("password", "passwd", "secret", "token", "apikey", "authorization")):
        return "sensitive_assignment"
    return None


def _validate_no_sensitive_json_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise VerificationError("结构化证据键无效")
            if _sensitive_json_key_rule(key) is not None:
                raise VerificationError("派生档案成员包含敏感内容")
            _validate_no_sensitive_json_keys(nested_value)
    elif isinstance(value, list):
        for item in value:
            _validate_no_sensitive_json_keys(item)


def _validate_sensitive_content(member_map: dict[str, Member]) -> None:
    strict_prefixes = ("evidence/", "index/", "provenance/", "verification/")
    for path, member in member_map.items():
        if path in {"manifest.json", "manifest.sha256"}:
            continue
        try:
            content = member.content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise VerificationError("归档成员必须为 UTF-8 文本") from error
        for pattern in (URL_USERINFO_PATTERN, WINDOWS_ABSOLUTE_PATH_PATTERN, UNIX_HOME_PATH_PATTERN):
            if pattern.search(content):
                raise VerificationError("归档成员包含路径或 URL userinfo")
        if path.startswith("evidence/runtime/"):
            _validate_runtime_evidence_value(_decode_json(member))
        if path.startswith(strict_prefixes):
            for pattern in (SENSITIVE_ASSIGNMENT_PATTERN, COOKIE_ASSIGNMENT_PATTERN):
                if pattern.search(content):
                    raise VerificationError("派生档案成员包含敏感内容")
            if path.endswith(".json"):
                _validate_no_sensitive_json_keys(_decode_json(member))

def _validate_predecessor_register(member_map: dict[str, Member]) -> dict[str, object] | None:
    register_member = member_map.get("baseline/docs/v1-archive/predecessor-register.json")
    if register_member is None:
        return None
    register = _decode_json(register_member)
    if not isinstance(register, dict) or register.get("schema_version") != 1:
        raise VerificationError("前序档案登记格式无效")
    entries = register.get("entries")
    if not isinstance(entries, list):
        raise VerificationError("前序档案登记条目无效")
    run_ids: set[str] = set()
    rejected_entries: list[dict[str, object]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise VerificationError("前序档案登记条目无效")
        run_id = entry.get("run_id")
        manifest_sha256 = entry.get("manifest_sha256")
        reason = entry.get("reason")
        if (
            not isinstance(run_id, str)
            or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?", run_id)
            or run_id in run_ids
            or not isinstance(manifest_sha256, str)
            or not SHA256_PATTERN.fullmatch(manifest_sha256)
            or not isinstance(reason, str)
            or not reason.strip()
            or entry.get("status") != "not_accepted_under_policy"
        ):
            raise VerificationError("前序档案登记条目无效")
        run_ids.add(run_id)
        rejected_entries.append(entry)
    if not rejected_entries:
        return None
    return max(rejected_entries, key=lambda entry: str(entry["run_id"]))


def _report_category(source_path: str) -> str:
    if source_path.startswith("reports/development/"):
        return "development"
    if source_path.startswith("reports/testing/"):
        return "testing"
    if source_path.startswith("reports/infrastructure/"):
        return "infrastructure"
    if source_path.startswith("reports/versions/"):
        return "version"
    raise VerificationError("报告来源路径无效")


def _validate_report_text(value: str) -> None:
    if (
        any(pattern.search(value) for pattern in (
            URL_USERINFO_PATTERN,
            WINDOWS_ABSOLUTE_PATH_PATTERN,
            UNIX_HOME_PATH_PATTERN,
            SENSITIVE_ASSIGNMENT_PATTERN,
            COOKIE_ASSIGNMENT_PATTERN,
        ))
        or re.search(r"(?i)\b(?:stdout|stderr|traceback|stacktrace|response)\b", value)
        or re.search(r"(?i)\b(?:pid|process[ _-]?id)\b\s*[:=]?\s*\d+", value)
    ):
        raise VerificationError("声明式归档报告包含敏感内容、绝对路径或运行输出")


def _validate_report_value(value: object, key: str | None = None) -> None:
    if key is not None and (
        _sensitive_json_key_rule(key) is not None
        or _is_runtime_output_key(key)
        or _is_pid_key(key)
    ):
        raise VerificationError("声明式归档报告包含禁止字段")
    if isinstance(value, dict):
        for nested_key, nested_value in value.items():
            if not isinstance(nested_key, str):
                raise VerificationError("声明式归档报告键无效")
            _validate_report_value(nested_value, nested_key)
    elif isinstance(value, list):
        for nested_value in value:
            _validate_report_value(nested_value, key)
    elif isinstance(value, str):
        _validate_report_text(value)


def _require_report_string(metadata: dict[str, object], field: str) -> str:
    value = metadata.get(field)
    if not isinstance(value, str) or not value.strip():
        raise VerificationError(f"声明式归档报告缺少有效 {field}")
    return value


def _validate_report_timestamp(value: str) -> None:
    if not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise VerificationError("声明式归档报告时间必须为 UTC ISO 8601")
    try:
        datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise VerificationError("声明式归档报告时间无效") from error


def _validate_report_material(
    value: object,
    inventory_by_source: dict[str, dict[str, object]],
    member_map: dict[str, Member],
    expected_source_path: str,
) -> Member:
    if not isinstance(value, dict) or set(value) != {"archive_path", "archive_sha256", "source_path", "source_sha256"}:
        raise VerificationError("报告登记材料引用无效")
    archive_path = value.get("archive_path")
    archive_sha256 = value.get("archive_sha256")
    source_path = value.get("source_path")
    source_sha256 = value.get("source_sha256")
    if (
        source_path != expected_source_path
        or not isinstance(archive_path, str)
        or not isinstance(archive_sha256, str)
        or not isinstance(source_sha256, str)
        or not SHA256_PATTERN.fullmatch(archive_sha256)
        or not SHA256_PATTERN.fullmatch(source_sha256)
    ):
        raise VerificationError("报告登记材料路径或哈希无效")
    inventory = inventory_by_source.get(expected_source_path)
    member = member_map.get(archive_path)
    if (
        inventory is None
        or member is None
        or inventory.get("archive_path") != archive_path
        or inventory.get("source_sha256") != source_sha256
        or _sha256(member.content) != archive_sha256
    ):
        raise VerificationError("报告登记材料无法追溯到来源清单")
    return member


def _validate_declared_report(
    metadata: object,
    known_requirements: set[str],
    known_defects: set[str],
    known_source_paths: set[str],
    known_archive_paths: set[str],
) -> dict[str, object]:
    if not isinstance(metadata, dict):
        raise VerificationError("声明式归档报告 JSON 必须为对象")
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
        raise VerificationError("声明式归档报告字段无效")
    _validate_report_value(metadata)
    if metadata.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise VerificationError("声明式归档报告 schema 版本无效")
    report_id = _require_report_string(metadata, "report_id")
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise VerificationError("声明式归档报告标识无效")
    recorded_at_utc = _require_report_string(metadata, "recorded_at_utc")
    _validate_report_timestamp(recorded_at_utc)
    product_version = _require_report_string(metadata, "product_version")
    if not SEMVER_PATTERN.fullmatch(product_version):
        raise VerificationError("声明式归档报告产品版本无效")
    for field, allowed_values in (
        ("report_kind", REPORT_KINDS),
        ("author_role", REPORT_AUTHOR_ROLES),
        ("independence", REPORT_INDEPENDENCE),
        ("decision_scope", REPORT_DECISION_SCOPES),
        ("verdict", REPORT_VERDICTS),
    ):
        if metadata.get(field) not in allowed_values:
            raise VerificationError(f"声明式归档报告 {field} 无效")
    archive_run_id = metadata.get("archive_run_id")
    if archive_run_id is not None and (not isinstance(archive_run_id, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?", archive_run_id)):
        raise VerificationError("声明式归档报告 run-id 无效")
    archive_manifest_sha256 = metadata.get("archive_manifest_sha256")
    if archive_manifest_sha256 is not None and (not isinstance(archive_manifest_sha256, str) or not SHA256_PATTERN.fullmatch(archive_manifest_sha256)):
        raise VerificationError("声明式归档报告 manifest 哈希无效")
    supersedes_report_id = metadata.get("supersedes_report_id")
    if supersedes_report_id is not None and (not isinstance(supersedes_report_id, str) or not REPORT_ID_PATTERN.fullmatch(supersedes_report_id)):
        raise VerificationError("声明式归档报告更正引用无效")
    summary = metadata.get("summary")
    if summary is not None and (not isinstance(summary, str) or not summary.strip()):
        raise VerificationError("声明式归档报告摘要无效")

    requirements = metadata.get("requirements")
    if not isinstance(requirements, list) or any(not isinstance(item, str) or item not in known_requirements for item in requirements):
        raise VerificationError("声明式归档报告引用了未知需求")
    if len(requirements) != len(set(requirements)):
        raise VerificationError("声明式归档报告含重复需求")

    defects = metadata.get("defects")
    if not isinstance(defects, list):
        raise VerificationError("声明式归档报告缺陷关系无效")
    defect_ids: set[str] = set()
    for item in defects:
        if not isinstance(item, dict):
            raise VerificationError("声明式归档报告缺陷关系无效")
        defect_id = item.get("defect_id")
        relationship = item.get("relationship")
        if (
            not isinstance(defect_id, str)
            or defect_id not in known_defects
            or defect_id in defect_ids
            or relationship not in REPORT_DEFECT_RELATIONSHIPS
        ):
            raise VerificationError("声明式归档报告引用了未知或重复缺陷")
        defect_ids.add(defect_id)

    evidence_refs = metadata.get("evidence_refs")
    if not isinstance(evidence_refs, list) or any(not isinstance(item, str) for item in evidence_refs):
        raise VerificationError("声明式归档报告证据引用无效")
    if len(evidence_refs) != len(set(evidence_refs)):
        raise VerificationError("声明式归档报告含重复证据引用")
    for reference in evidence_refs:
        if not _is_safe_path(reference) or reference not in known_source_paths | known_archive_paths:
            raise VerificationError("声明式归档报告证据引用越出当前档案")

    release_gates = metadata.get("release_gates")
    if not isinstance(release_gates, list):
        raise VerificationError("声明式归档报告门禁无效")
    gate_ids: set[str] = set()
    has_blocked_gate = False
    for gate in release_gates:
        if not isinstance(gate, dict):
            raise VerificationError("声明式归档报告门禁条目无效")
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
            raise VerificationError("声明式归档报告门禁条目无效")
        gate_ids.add(gate_id)
        has_blocked_gate = has_blocked_gate or status == "blocked"
    if metadata["decision_scope"] == "release" and metadata["verdict"] == "accepted" and has_blocked_gate:
        raise VerificationError("发布接受报告不得保留阻塞门禁")
    return metadata


def _load_legacy_report_hashes(member_map: dict[str, Member]) -> dict[str, str]:
    legacy_member = member_map.get(LEGACY_REPORT_REGISTER_PATH)
    if legacy_member is None:
        raise VerificationError("v2 归档缺少历史报告登记")
    payload = _decode_json(legacy_member)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "purpose", "entries"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("purpose"), str)
        or not payload["purpose"].strip()
        or not isinstance(payload.get("entries"), list)
    ):
        raise VerificationError("历史报告登记格式无效")
    registered: dict[str, str] = {}
    for item in payload["entries"]:
        if not isinstance(item, dict) or set(item) != {"source_path", "source_sha256"}:
            raise VerificationError("历史报告登记条目无效")
        source_path = item.get("source_path")
        source_sha256 = item.get("source_sha256")
        if (
            not isinstance(source_path, str)
            or not source_path.startswith("reports/")
            or not source_path.endswith(".md")
            or not _is_safe_path(source_path)
            or source_path in registered
            or not isinstance(source_sha256, str)
            or not SHA256_PATTERN.fullmatch(source_sha256)
        ):
            raise VerificationError("历史报告登记条目无效")
        registered[source_path] = source_sha256
    return registered


def _load_snapshot_register(
    member_map: dict[str, Member],
    inventory_by_source: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    snapshot_member = member_map.get(SNAPSHOT_REGISTER_PATH)
    if snapshot_member is None:
        raise VerificationError("v2 归档缺少冻结快照登记")
    payload = _decode_json(snapshot_member)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "purpose", "entries"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("purpose"), str)
        or not payload["purpose"].strip()
        or not isinstance(payload.get("entries"), list)
        or not payload["entries"]
    ):
        raise VerificationError("快照登记格式无效")
    expected_fields = {
        "run_id", "manifest_sha256", "archive_local_verdict", "acceptance_report",
        "acceptance_report_sha256", "supersedes_run_id",
    }
    entries: list[dict[str, object]] = []
    seen_runs: set[str] = set()
    previous_run_id: str | None = None
    for item in payload["entries"]:
        if not isinstance(item, dict) or set(item) != expected_fields:
            raise VerificationError("快照登记条目无效")
        run_id = item.get("run_id")
        manifest_sha256 = item.get("manifest_sha256")
        verdict = item.get("archive_local_verdict")
        acceptance_report = item.get("acceptance_report")
        acceptance_report_sha256 = item.get("acceptance_report_sha256")
        supersedes_run_id = item.get("supersedes_run_id")
        report_inventory = inventory_by_source.get(acceptance_report) if isinstance(acceptance_report, str) else None
        if (
            not isinstance(run_id, str)
            or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?", run_id)
            or run_id in seen_runs
            or not isinstance(manifest_sha256, str)
            or not SHA256_PATTERN.fullmatch(manifest_sha256)
            or verdict not in REPORT_VERDICTS - {"not_applicable"}
            or not isinstance(acceptance_report, str)
            or not acceptance_report.startswith("reports/testing/")
            or not acceptance_report.endswith(".md")
            or report_inventory is None
            or not isinstance(acceptance_report_sha256, str)
            or not SHA256_PATTERN.fullmatch(acceptance_report_sha256)
            or report_inventory.get("source_sha256") != acceptance_report_sha256
            or (supersedes_run_id is not None and (not isinstance(supersedes_run_id, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?", supersedes_run_id)))
            or supersedes_run_id != previous_run_id
        ):
            raise VerificationError("快照登记条目无效")
        seen_runs.add(run_id)
        previous_run_id = run_id
        entries.append(item)
    return entries


def _validate_declared_archive_identity(
    metadata: dict[str, object],
    snapshot_register: list[dict[str, object]],
) -> None:
    if metadata["report_kind"] != "acceptance":
        return
    run_id = metadata.get("archive_run_id")
    manifest_sha256 = metadata.get("archive_manifest_sha256")
    if (run_id is None) != (manifest_sha256 is None):
        raise VerificationError("验收报告的归档身份必须完整")
    if run_id is None:
        return
    matching = next((item for item in snapshot_register if item["run_id"] == run_id), None)
    if (
        matching is None
        or matching["manifest_sha256"] != manifest_sha256
        or matching["archive_local_verdict"] != metadata["verdict"]
    ):
        raise VerificationError("验收报告归档身份未与冻结快照登记一致")


def _validate_version_summary_chain(
    metadata: dict[str, object],
    snapshot_register: list[dict[str, object]],
    report_entries_by_source: dict[str, dict[str, object]],
    markdown_content_by_source: dict[str, str],
) -> None:
    if metadata["report_kind"] != "version_summary":
        if "snapshot_chain" in metadata or "recommended_snapshot_run_id" in metadata:
            raise VerificationError("只有版本汇总可以声明快照链")
        return
    if metadata["decision_scope"] != "version_archive":
        raise VerificationError("版本汇总必须使用 version_archive 裁定范围")
    chain = metadata.get("snapshot_chain")
    recommended_run_id = metadata.get("recommended_snapshot_run_id")
    if chain != snapshot_register or not isinstance(recommended_run_id, str):
        raise VerificationError("版本汇总快照链未与冻结登记一致")
    recommended_entry = next((item for item in snapshot_register if item["run_id"] == recommended_run_id), None)
    if recommended_entry is None or recommended_entry["archive_local_verdict"] != "accepted":
        raise VerificationError("版本汇总推荐了未接受的快照")
    acceptance_path = recommended_entry["acceptance_report"]
    assert isinstance(acceptance_path, str)
    acceptance_entry = report_entries_by_source.get(acceptance_path)
    if acceptance_entry is None:
        raise VerificationError("版本汇总推荐快照缺少验收报告")
    declared = acceptance_entry.get("declared")
    if isinstance(declared, dict):
        independent_acceptance = (
            declared.get("report_kind") == "acceptance"
            and declared.get("decision_scope") == "archive_local"
            and declared.get("verdict") == "accepted"
            and declared.get("independence") == "independent"
            and declared.get("archive_run_id") == recommended_entry["run_id"]
            and declared.get("archive_manifest_sha256") == recommended_entry["manifest_sha256"]
        )
    else:
        acceptance_text = markdown_content_by_source.get(acceptance_path)
        if acceptance_text is None:
            raise VerificationError("版本汇总推荐快照缺少验收报告内容")
        independent_acceptance = (
            ("独立" in acceptance_text or "independent" in acceptance_text.casefold())
            and re.search(r"(?i)\baccept\b[\s*]+for archive-local acceptance only\.", acceptance_text) is not None
        )
    if not independent_acceptance:
        raise VerificationError("版本汇总推荐快照缺少独立 archive-local 接受记录")


def _validate_report_register(member_map: dict[str, Member]) -> None:
    schema_member = member_map.get("baseline/docs/v1-archive/report-schema-v1.json")
    requirements_member = member_map.get("baseline/docs/requirements.md")
    register_member = member_map.get("index/report-register.json")
    if schema_member is None or requirements_member is None or register_member is None:
        raise VerificationError("v2 归档缺少报告契约基线或报告登记")
    schema = _decode_json(schema_member)
    if (
        not isinstance(schema, dict)
        or schema.get("schema_version") != REPORT_SCHEMA_VERSION
        or schema.get("artifact_type") != "normalized_archive_report_metadata"
        or schema.get("purpose") != "Defines the JSON sidecar contract for normalized archive reports. The paired Markdown file is the human-readable record."
        or not isinstance(schema.get("required_fields"), list)
    ):
        raise VerificationError("归档报告侧车 schema 无效")
    expected_schema_fields = {
        "schema_version", "report_id", "recorded_at_utc", "report_kind", "author_role",
        "independence", "product_version", "decision_scope", "verdict", "requirements",
        "defects", "evidence_refs", "release_gates",
    }
    expected_optional_fields = {
        "archive_run_id", "archive_manifest_sha256", "supersedes_report_id", "snapshot_chain",
        "recommended_snapshot_run_id", "summary",
    }
    def _archived_enums_within_contract(archived: Any, contract: dict) -> bool:
        if not isinstance(archived, dict) or set(archived) != set(contract):
            return False
        for key, current_values in contract.items():
            archived_values = archived.get(key)
            if not isinstance(archived_values, list):
                return False
            if not set(archived_values).issubset(set(current_values)):
                return False
        return True

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
        set(schema.get("required_fields", [])) != expected_schema_fields
        or set(schema.get("optional_fields", [])) != expected_optional_fields
        or schema.get("file_pair") != {
            "markdown_extension": ".md",
            "metadata_extension": ".json",
            "same_stem_required": True,
        }
        or not isinstance(schema.get("enums"), dict)
        # 封存归档冻结其构建时的 schema：枚举演进是追加式的，因此归档枚举
        # 只须为当前执行枚举的子集（新报告类型加入后，旧封存档案依旧有效；
        # 归档若含当前不认识的枚举值则仍然拒绝）。
        or not _archived_enums_within_contract(schema["enums"], expected_enums)
        or schema.get("safety") != expected_safety
        or set(schema) != {
            "schema_version", "artifact_type", "purpose", "file_pair", "required_fields",
            "optional_fields", "enums", "safety",
        }
    ):
        raise VerificationError("归档报告侧车 schema 无效或与执行契约不一致")
    known_requirements = set(re.findall(r"\bREQ-\d{3}(?:[a-z])?\b", requirements_member.content.decode("utf-8", errors="strict")))
    if not known_requirements:
        raise VerificationError("冻结需求基线未定义 REQ 标识")
    ledger = member_map["index/defect-ledger.md"].content.decode("utf-8", errors="strict")
    known_defects = set(re.findall(r"\bDEF-[A-Z0-9-]+\b", ledger))
    if not known_defects:
        raise VerificationError("缺陷账本未定义 DEF 标识")
    inventory = _decode_json(member_map["provenance/source-inventory.json"])
    evidence_register = _decode_json(member_map["index/evidence-register.json"])
    register = _decode_json(register_member)
    if (
        not isinstance(inventory, dict)
        or not isinstance(inventory.get("entries"), list)
        or not isinstance(evidence_register, dict)
        or not isinstance(evidence_register.get("entries"), list)
        or not isinstance(register, dict)
        or register.get("schema_version") != 1
        or not isinstance(register.get("entries"), list)
    ):
        raise VerificationError("报告登记依赖索引无效")
    inventory_by_source: dict[str, dict[str, object]] = {}
    for item in inventory["entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("source_path"), str):
            raise VerificationError("来源清单条目无效")
        source_path = item["source_path"]
        if source_path in inventory_by_source:
            raise VerificationError("来源清单包含重复来源路径")
        inventory_by_source[source_path] = item
    legacy_report_hashes = _load_legacy_report_hashes(member_map)
    snapshot_register = _load_snapshot_register(member_map, inventory_by_source)
    known_source_paths = set(inventory_by_source)
    known_archive_paths = {str(item.get("archive_path")) for item in inventory_by_source.values() if isinstance(item.get("archive_path"), str)}
    evidence_source_paths: set[str] = set()
    evidence_archive_paths: set[str] = set()
    for item in evidence_register["entries"]:
        if not isinstance(item, dict):
            raise VerificationError("证据登记条目无效")
        source_path = item.get("source_path")
        archive_path = item.get("archive_path")
        if isinstance(source_path, str):
            evidence_source_paths.add(source_path)
        if isinstance(archive_path, str):
            evidence_archive_paths.add(archive_path)

    expected_markdown_sources = {
        source_path
        for source_path in inventory_by_source
        if source_path.startswith("reports/") and source_path.endswith(".md")
    }
    expected_metadata_sources = {
        source_path
        for source_path in inventory_by_source
        if source_path.startswith("reports/") and source_path.endswith(".json")
    }
    declared_metadata_sources = {
        source_path
        for source_path in expected_metadata_sources
        if f"{source_path[:-5]}.md" in expected_markdown_sources
    }
    legacy_markdown_sources = expected_markdown_sources - {
        f"{source_path[:-5]}.md" for source_path in declared_metadata_sources
    }
    if set(legacy_report_hashes) != legacy_markdown_sources:
        raise VerificationError("历史报告登记未与无侧车 Markdown 报告完全一致")
    entries_by_source: dict[str, dict[str, object]] = {}
    declared_report_ids: set[str] = set()
    metadata_sources: set[str] = set()
    markdown_content_by_source: dict[str, str] = {}
    for entry in register["entries"]:
        if not isinstance(entry, dict):
            raise VerificationError("报告登记条目无效")
        normalization_status = entry.get("normalization_status")
        report_id = entry.get("report_id")
        markdown = entry.get("markdown")
        if normalization_status not in {"declared", "legacy_inferred"} or not isinstance(report_id, str) or not isinstance(markdown, dict):
            raise VerificationError("报告登记状态或标识无效")
        source_path = markdown.get("source_path")
        if not isinstance(source_path, str) or source_path in entries_by_source or source_path not in expected_markdown_sources:
            raise VerificationError("报告登记 Markdown 路径无效或重复")
        markdown_member = _validate_report_material(markdown, inventory_by_source, member_map, source_path)
        markdown_content = markdown_member.content.decode("utf-8", errors="strict")
        markdown_content_by_source[source_path] = markdown_content
        category = entry.get("category")
        title = entry.get("title")
        if category != _report_category(source_path) or not isinstance(title, str) or not title.strip():
            raise VerificationError("报告登记类别或标题无效")
        expected_title = next((line[2:].strip() for line in markdown_content.splitlines() if line.startswith("# ")), source_path)
        if title != expected_title:
            raise VerificationError("报告登记标题与 Markdown 不一致")
        if normalization_status == "legacy_inferred":
            if set(entry) != {"category", "defects", "markdown", "normalization_status", "report_id", "requirements", "title"}:
                raise VerificationError("历史推断报告字段无效")
            expected_report_id = f"LEGACY-{hashlib.sha256(source_path.encode('utf-8')).hexdigest()[:16].upper()}"
            requirements = entry.get("requirements")
            defects = entry.get("defects")
            if (
                report_id != expected_report_id
                or legacy_report_hashes.get(source_path) != markdown.get("source_sha256")
                or not isinstance(requirements, list)
                or requirements != sorted(set(re.findall(r"\bREQ-\d{3}(?:[a-z])?\b", markdown_content)))
                or any(item not in known_requirements for item in requirements)
                or not isinstance(defects, list)
                or any(not isinstance(item, str) or item not in known_defects for item in defects)
                or len(defects) != len(set(defects))
            ):
                raise VerificationError("历史推断报告关联无效")
        else:
            if set(entry) != {"category", "declared", "markdown", "metadata", "normalization_status", "report_id", "title"}:
                raise VerificationError("声明式报告登记字段无效")
            metadata_material = entry.get("metadata")
            expected_metadata_path = f"{source_path[:-3]}.json"
            metadata_member = _validate_report_material(metadata_material, inventory_by_source, member_map, expected_metadata_path)
            metadata_sources.add(expected_metadata_path)
            _validate_report_text(markdown_content)
            metadata = _validate_declared_report(entry.get("declared"), known_requirements, known_defects, known_source_paths, known_archive_paths)
            metadata_sidecar = _decode_json(metadata_member)
            if metadata_sidecar != metadata:
                raise VerificationError("报告登记与 JSON 侧车不一致")
            if report_id != metadata["report_id"] or report_id in declared_report_ids:
                raise VerificationError("声明式归档报告标识无效或重复")
            declared_report_ids.add(report_id)
            if source_path.startswith("reports/development/") and metadata["report_kind"] != "development":
                raise VerificationError("开发报告路径与类别不一致")
            if source_path.startswith("reports/testing/") and metadata["report_kind"] not in {"testing", "acceptance"}:
                raise VerificationError("测试报告路径与类别不一致")
            if source_path.startswith("reports/infrastructure/") and metadata["report_kind"] != "infrastructure":
                raise VerificationError("基础设施报告路径与类别不一致")
            if source_path.startswith("reports/versions/") and metadata["report_kind"] != "version_summary":
                raise VerificationError("版本报告路径与类别不一致")
            for reference in metadata["evidence_refs"]:
                if reference not in evidence_source_paths and reference not in evidence_archive_paths:
                    raise VerificationError("声明式归档报告证据未登记")
            _validate_declared_archive_identity(metadata, snapshot_register)
            entry["declared"] = metadata
        entries_by_source[source_path] = entry
    if set(entries_by_source) != expected_markdown_sources:
        raise VerificationError("报告登记未覆盖全部 Markdown 报告")
    legacy_source_paths = {
        source_path
        for source_path, entry in entries_by_source.items()
        if entry["normalization_status"] == "legacy_inferred"
    }
    if set(legacy_report_hashes) != legacy_source_paths:
        raise VerificationError("历史报告登记未与无侧车 Markdown 报告完全一致")
    if metadata_sources != expected_metadata_sources:
        raise VerificationError("声明式归档报告缺少同名 Markdown 或登记")
    if len({entry["report_id"] for entry in entries_by_source.values()}) != len(entries_by_source):
        raise VerificationError("报告登记含重复标识")
    for snapshot in snapshot_register:
        acceptance_path = snapshot["acceptance_report"]
        assert isinstance(acceptance_path, str)
        acceptance_entry = entries_by_source.get(acceptance_path)
        if (
            acceptance_entry is None
            or acceptance_entry["markdown"].get("source_sha256") != snapshot["acceptance_report_sha256"]
        ):
            raise VerificationError("冻结快照登记验收报告无法追溯")
        declared = acceptance_entry.get("declared")
        # 登记一致性只要求验收报告身份可追溯；独立性口径（推荐快照必须 independent）
        # 由 _validate_version_summary_chain 对推荐位单独强制，政策允许 non_independent 登记。
        if isinstance(declared, dict) and (
            declared.get("report_kind") != "acceptance"
            or declared.get("decision_scope") != "archive_local"
            or declared.get("independence") not in REPORT_INDEPENDENCE
            or declared.get("archive_run_id") != snapshot["run_id"]
            or declared.get("archive_manifest_sha256") != snapshot["manifest_sha256"]
            or declared.get("verdict") != snapshot["archive_local_verdict"]
        ):
            raise VerificationError("声明式验收报告未与冻结快照登记一致")
    for entry in entries_by_source.values():
        declared = entry.get("declared")
        if not isinstance(declared, dict):
            continue
        supersedes_report_id = declared.get("supersedes_report_id")
        if supersedes_report_id is not None and supersedes_report_id not in declared_report_ids:
            raise VerificationError("声明式归档报告更正引用未在当前档案登记")
        _validate_version_summary_chain(
            declared,
            snapshot_register,
            entries_by_source,
            markdown_content_by_source,
        )


def _validate_indexes(member_map: dict[str, Member], manifest: dict[str, object]) -> None:
    latest_predecessor = _validate_predecessor_register(member_map)
    predecessor_member = member_map.get("provenance/predecessor.json")
    if predecessor_member is not None:
        predecessor = _decode_json(predecessor_member)
        if not isinstance(predecessor, dict):
            raise VerificationError("前序档案登记格式无效")
        if predecessor.get("predecessor_status") not in {"none", "not_accepted_under_policy"}:
            raise VerificationError("前序档案状态无效")
        if predecessor.get("predecessor_status") == "not_accepted_under_policy":
            run_id = predecessor.get("predecessor_run_id")
            manifest_sha256 = predecessor.get("predecessor_manifest_sha256")
            if not isinstance(run_id, str) or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?", run_id):
                raise VerificationError("前序档案 run-id 无效")
            if not isinstance(manifest_sha256, str) or not SHA256_PATTERN.fullmatch(manifest_sha256):
                raise VerificationError("前序档案 manifest 哈希无效")
            if latest_predecessor is not None and (
                run_id != latest_predecessor["run_id"]
                or manifest_sha256 != latest_predecessor["manifest_sha256"]
                or predecessor.get("reason") != latest_predecessor["reason"]
            ):
                raise VerificationError("前序档案未引用最新拒绝快照")
        elif latest_predecessor is not None:
            raise VerificationError("前序档案缺少最新拒绝快照")

    current_status = member_map["index/current-status.md"].content.decode("utf-8", errors="strict")
    if "V1 Candidate / BLOCKED" not in current_status or "PostgreSQL" not in current_status:
        raise VerificationError("当前状态索引缺少阻塞结论")
    ledger = member_map["index/defect-ledger.md"].content.decode("utf-8", errors="strict")
    ledger_ids = set(re.findall(r"\bDEF-[A-Z0-9-]+\b", ledger))
    if "DEF-PG-001" not in ledger_ids or "`blocked`" not in ledger:
        raise VerificationError("缺陷账本缺少发布阻塞项")
    register = _decode_json(member_map["index/evidence-register.json"])
    if not isinstance(register, dict) or not isinstance(register.get("entries"), list):
        raise VerificationError("证据登记格式无效")
    for item in register["entries"]:
        if not isinstance(item, dict) or not isinstance(item.get("defects"), list):
            raise VerificationError("证据登记缺陷字段无效")
        if any(not isinstance(defect_id, str) or defect_id not in ledger_ids for defect_id in item["defects"]):
            raise VerificationError("证据登记引用了缺失的缺陷账本条目")
        if str(item.get("archive_path", "")).startswith("evidence/runtime/"):
            source_run_id = item.get("source_run_id")
            source_path = item.get("source_path")
            if not isinstance(source_run_id, str) or not SOURCE_RUN_ID_PATTERN.fullmatch(source_run_id):
                raise VerificationError("证据登记运行标识无效")
            if not isinstance(source_path, str) or source_run_id not in source_path:
                raise VerificationError("证据登记运行标识与来源不一致")
    inventory = _decode_json(member_map["provenance/source-inventory.json"])
    if not isinstance(inventory, dict) or not isinstance(inventory.get("entries"), list):
        raise VerificationError("来源清单格式无效")
    if manifest["schema_version"] == 2:
        _validate_report_register(member_map)


def verify_archive(archive: Path) -> dict[str, object]:
    """Verify a directory or ZIP archive without invoking the archive builder."""
    members = _read_directory(archive) if archive.is_dir() else _read_zip(archive)
    member_map = _member_map(members)
    manifest = _validate_manifest(member_map)
    _validate_entries(manifest, member_map)
    _validate_archive_layout(manifest, member_map)
    _validate_sensitive_content(member_map)
    _validate_indexes(member_map, manifest)
    return {"archive": str(archive), "entries": len(manifest["entries"]), "status": "verified"}


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证本机 V1 当前可审计档案")
    parser.add_argument("--archive", required=True, type=Path, help="归档目录或 ZIP 文件")
    parser.add_argument("--quiet", action="store_true", help="仅以退出码表示结果")
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()
    try:
        result = verify_archive(arguments.archive)
    except (OSError, UnicodeError, VerificationError) as error:
        if not arguments.quiet:
            print(f"归档验证失败：{error}", file=sys.stderr)
        return 2
    if not arguments.quiet:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
