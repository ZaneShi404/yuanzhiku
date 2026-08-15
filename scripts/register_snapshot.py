#!/usr/bin/env python3
"""Register an archive snapshot and mirror the frozen chain into version summaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


# 与 scripts/archive_v1.py 的 RUN_ID_PATTERN 语义保持一致；按验证器传统不导入构建器。
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[A-Za-z0-9][A-Za-z0-9._-]*)?$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_VERDICTS = frozenset({"accepted", "rejected", "blocked"})
SNAPSHOT_REGISTER_PATH = "docs/v1-archive/snapshot-register.json"
SNAPSHOT_ENTRY_FIELDS = frozenset({
    "run_id", "manifest_sha256", "archive_local_verdict",
    "acceptance_report", "acceptance_report_sha256", "supersedes_run_id",
})


class SnapshotRegisterError(ValueError):
    """Raised when the snapshot registration request is invalid."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise SnapshotRegisterError(f"文件必须为 UTF-8 文本：{path.name}") from error


def _load_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path).lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise SnapshotRegisterError(f"JSON 格式无效：{path.name}") from error


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_safe_relative_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/") or ":" in value:
        return False
    return all(part not in {"", ".", ".."} for part in PurePosixPath(value).parts)


def _resolve_acceptance_report(repository_root: Path, acceptance: str) -> tuple[Path, str]:
    candidate = Path(acceptance)
    resolved = candidate.resolve() if candidate.is_absolute() else (repository_root / candidate).resolve()
    try:
        relative = resolved.relative_to(repository_root).as_posix()
    except ValueError as error:
        raise SnapshotRegisterError("验收报告必须位于仓库内") from error
    if not _is_safe_relative_path(relative) or not relative.startswith("reports/testing/") or not relative.endswith(".md"):
        raise SnapshotRegisterError("验收报告必须是 reports/testing/ 下的 Markdown 文件")
    if not resolved.is_file() or resolved.is_symlink():
        raise SnapshotRegisterError(f"验收报告不存在：{relative}")
    return resolved, relative


def _load_manifest_sha256(repository_root: Path, run_id: str) -> str:
    manifest_path = repository_root / "archives" / f"V1-current-audit-{run_id}" / "manifest.sha256"
    if not manifest_path.is_file():
        raise SnapshotRegisterError(f"未找到归档 manifest，请先构建归档：archives/V1-current-audit-{run_id}/manifest.sha256")
    tokens = _read_text(manifest_path).split()
    if not tokens or not SHA256_PATTERN.fullmatch(tokens[0]):
        raise SnapshotRegisterError(f"归档 manifest 哈希无效：{manifest_path.name}")
    return tokens[0]


def _load_snapshot_register(repository_root: Path) -> tuple[Path, dict[str, Any]]:
    register_path = repository_root / SNAPSHOT_REGISTER_PATH
    if not register_path.is_file():
        raise SnapshotRegisterError("快照登记不存在")
    payload = _load_json(register_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise SnapshotRegisterError("快照登记格式无效")
    seen_run_ids: set[str] = set()
    for item in payload["entries"]:
        if (
            not isinstance(item, dict)
            or set(item) != SNAPSHOT_ENTRY_FIELDS
            or not isinstance(item["run_id"], str)
            or not RUN_ID_PATTERN.fullmatch(item["run_id"])
            or item["run_id"] in seen_run_ids
        ):
            raise SnapshotRegisterError("快照登记条目无效")
        seen_run_ids.add(item["run_id"])
    return register_path, payload


def register_snapshot(repository_root: Path, *, run_id: str, verdict: str, acceptance: str) -> dict[str, Any]:
    """Append one snapshot entry, mirror the chain, and return a summary."""
    repository_root = repository_root.resolve()
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise SnapshotRegisterError("run-id 必须为 UTC 时间戳并且只含安全字符")
    if verdict not in SNAPSHOT_VERDICTS:
        raise SnapshotRegisterError(f"裁定无效：{verdict}（允许 accepted/rejected/blocked）")
    acceptance_path, acceptance_relative = _resolve_acceptance_report(repository_root, acceptance)
    manifest_sha256 = _load_manifest_sha256(repository_root, run_id)
    register_path, register = _load_snapshot_register(repository_root)
    entries: list[dict[str, Any]] = register["entries"]
    if any(item["run_id"] == run_id for item in entries):
        raise SnapshotRegisterError(f"run-id 已在快照登记中，不重复登记：{run_id}")
    acceptance_sha256 = _sha256_file(acceptance_path)
    entry = {
        "run_id": run_id,
        "manifest_sha256": manifest_sha256,
        "archive_local_verdict": verdict,
        "acceptance_report": acceptance_relative,
        "acceptance_report_sha256": acceptance_sha256,
        "supersedes_run_id": entries[-1]["run_id"] if entries else None,
    }
    new_entries = [*entries, entry]
    versions_root = repository_root / "reports/versions"
    summary_paths = sorted(versions_root.glob("*/version-summary.json")) if versions_root.is_dir() else []
    summary_payloads: list[tuple[Path, dict[str, Any]]] = []
    for summary_path in summary_paths:
        payload = _load_json(summary_path)
        if not isinstance(payload, dict) or not isinstance(payload.get("snapshot_chain"), list):
            raise SnapshotRegisterError(f"版本汇总缺少 snapshot_chain 字段：{summary_path.relative_to(repository_root).as_posix()}")
        payload["snapshot_chain"] = new_entries
        summary_payloads.append((summary_path, payload))
    markdown_paths = sorted(versions_root.glob("*/version-summary.md")) if versions_root.is_dir() else []

    # 全量校验完成后才一次性写入。
    register["entries"] = new_entries
    register_path.write_text(_dump_json(register), encoding="utf-8")
    for summary_path, payload in summary_payloads:
        summary_path.write_text(_dump_json(payload), encoding="utf-8")
    supersedes = entry["supersedes_run_id"]
    suggested_row = (
        f"| `{run_id}` | `{verdict}` | `{manifest_sha256}` "
        f"| `{PurePosixPath(acceptance_relative).stem}` | "
        + (f"后继 `{supersedes}` |" if supersedes is not None else "首项（无前项） |")
    )
    return {
        "entry": entry,
        "mirrored_summaries": [path.relative_to(repository_root).as_posix() for path, _ in summary_payloads],
        "suggested_rows": {path.relative_to(repository_root).as_posix(): suggested_row for path in markdown_paths},
    }


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="登记归档快照并把冻结链镜像进版本汇总")
    parser.add_argument("--run-id", required=True, help="归档运行标识，例如 20260730T010203Z")
    parser.add_argument("--verdict", required=True, choices=sorted(SNAPSHOT_VERDICTS), help="archive-local 裁定")
    parser.add_argument("--acceptance", required=True, help="验收报告路径（reports/testing/ 下的 Markdown）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    repository_root = Path(__file__).resolve().parents[1]
    try:
        result = register_snapshot(
            repository_root,
            run_id=arguments.run_id,
            verdict=arguments.verdict,
            acceptance=arguments.acceptance,
        )
    except SnapshotRegisterError as error:
        print(f"快照登记失败：{error}", file=sys.stderr)
        return 2
    entry = result["entry"]
    print(f"已登记快照：{entry['run_id']}（裁定 {entry['archive_local_verdict']}，supersedes {entry['supersedes_run_id']}）")
    if result["mirrored_summaries"]:
        print("已镜像 snapshot_chain 到：")
        for relative in result["mirrored_summaries"]:
            print(f"- {relative}")
    else:
        print("未找到版本汇总 JSON，未执行镜像。")
    if result["suggested_rows"]:
        print("请人工向以下版本汇总 Markdown 追加表格行，并补写说明列：")
        for relative, row in result["suggested_rows"].items():
            print(f"- {relative}：")
            print(f"  {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
