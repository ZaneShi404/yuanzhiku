#!/usr/bin/env python3
"""Scaffold a normalized archive report pair (Markdown + JSON sidecar)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = 1
REPORT_ID_PATTERN = re.compile(r"^RPT-[A-Z0-9][A-Z0-9._-]*$")
SEMVER_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
REQUIREMENT_ID_PATTERN = re.compile(r"\bREQ-\d{3}(?:[a-z])?\b")
REPORT_INDEPENDENCE = frozenset({"independent", "non_independent", "not_applicable"})
REPORT_DEFECT_RELATIONSHIPS = frozenset({"discovered", "reproduced", "repaired", "retested", "accepted", "rejected", "noted"})
REQUIREMENTS_PATH = "docs/requirements.md"
DEFECT_LEDGER_PATH = "docs/v1-archive/defect-ledger.json"
REPORT_KIND_DIRECTORIES = {
    "development": "reports/development",
    "testing": "reports/testing",
    "acceptance": "reports/testing",
    "infrastructure": "reports/infrastructure",
    "review": "reports/review",
}
REPORT_KIND_LABELS = {
    "development": "开发",
    "testing": "测试",
    "acceptance": "验收",
    "infrastructure": "基础设施",
    "review": "复核",
}
RELEASE_GATE_SKELETON = (
    ("GATE-UNIT-INTEGRATION-REGRESSION", "passed"),
    ("GATE-FRONTEND-BUILD", "passed"),
    ("GATE-BROWSER-BLACKBOX", "blocked"),
    ("GATE-COMPOSE-PHYSICAL", "blocked"),
    ("GATE-RELEASE-READINESS", "blocked"),
)


class ReportScaffoldError(ValueError):
    """Raised when the requested report scaffold is invalid."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ReportScaffoldError(f"文件必须为 UTF-8 文本：{path.name}") from error


def _load_known_requirements(repository_root: Path) -> set[str]:
    requirements_path = repository_root / REQUIREMENTS_PATH
    if not requirements_path.is_file():
        raise ReportScaffoldError("冻结需求基线不存在")
    known = set(REQUIREMENT_ID_PATTERN.findall(_read_text(requirements_path)))
    if not known:
        raise ReportScaffoldError("冻结需求基线未定义 REQ 标识")
    return known


def _load_known_defects(repository_root: Path) -> set[str]:
    ledger_path = repository_root / DEFECT_LEDGER_PATH
    if not ledger_path.is_file():
        raise ReportScaffoldError("缺陷台账不存在")
    try:
        payload = json.loads(_read_text(ledger_path).lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise ReportScaffoldError(f"JSON 格式无效：{ledger_path.name}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("defects"), list):
        raise ReportScaffoldError("缺陷台账格式无效")
    known: set[str] = set()
    for item in payload["defects"]:
        if not isinstance(item, dict) or not isinstance(item.get("defect_id"), str):
            raise ReportScaffoldError("缺陷台账条目无效")
        known.add(item["defect_id"])
    if not known:
        raise ReportScaffoldError("缺陷台账未定义缺陷标识")
    return known


def _default_product_version(repository_root: Path) -> str:
    versions_root = repository_root / "reports/versions"
    candidates: list[tuple[int, int, int, str]] = []
    if versions_root.is_dir():
        for child in versions_root.iterdir():
            if child.is_dir() and SEMVER_PATTERN.fullmatch(child.name):
                major, minor, patch = (int(part) for part in child.name[1:].split("."))
                candidates.append((major, minor, patch, child.name))
    if not candidates:
        return "v1.0.0"
    return max(candidates)[3]


def _parse_requirements(raw: str, known: set[str]) -> list[str]:
    requirements = [item.strip() for item in raw.split(",") if item.strip()]
    if not requirements:
        raise ReportScaffoldError("--reqs 至少需要一个 REQ 标识")
    unknown = [item for item in requirements if item not in known]
    if unknown:
        raise ReportScaffoldError(f"需求标识不存在于 docs/requirements.md：{', '.join(unknown)}")
    if len(requirements) != len(set(requirements)):
        raise ReportScaffoldError("--reqs 含重复需求标识")
    return requirements


def _parse_defects(raw: str | None, known: set[str]) -> list[dict[str, str]]:
    if raw is None:
        return []
    defects: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in (part.strip() for part in raw.split(",") if part.strip()):
        defect_id, separator, relationship = item.partition(":")
        if not separator or not defect_id or not relationship:
            raise ReportScaffoldError(f"--defs 条目必须为 ID:relationship 形式：{item}")
        if defect_id not in known:
            raise ReportScaffoldError(f"缺陷标识不存在于缺陷台账：{defect_id}")
        if defect_id in seen:
            raise ReportScaffoldError(f"--defs 含重复缺陷标识：{defect_id}")
        if relationship not in REPORT_DEFECT_RELATIONSHIPS:
            raise ReportScaffoldError(f"缺陷关系无效：{relationship}（允许 {', '.join(sorted(REPORT_DEFECT_RELATIONSHIPS))}）")
        seen.add(defect_id)
        defects.append({"defect_id": defect_id, "relationship": relationship})
    return defects


def _render_markdown(
    *,
    slug: str,
    kind: str,
    report_id: str,
    recorded_at_utc: str,
    author_role: str,
    independence: str,
    product_version: str,
    requirements: list[str],
) -> str:
    requirement_text = "、".join(f"`{item}`" for item in requirements)
    lines = [
        f"# {slug}：{REPORT_KIND_LABELS[kind]}报告",
        "",
        f"- 报告 ID：`{report_id}`",
        f"- 记录时间（UTC）：`{recorded_at_utc}`",
        f"- 报告类型：`{kind}`",
        f"- 作者角色：`{author_role}`",
        f"- 独立性：`{independence}`",
        f"- 产品版本：`{product_version}`",
        "- 裁定范围：`archive_local`",
        "- 裁定：`accepted`",
        "",
        "## 范围",
        "",
        f"（在此填写本报告覆盖的工作范围。关联需求：{requirement_text}。）",
        "",
        "## 验证",
        "",
        "（在此填写验证过程与结果。骨架把 GATE-UNIT-INTEGRATION-REGRESSION 与 GATE-FRONTEND-BUILD 标为 passed，保留前请人工核对；其余三项门禁保持 blocked。）",
        "",
        "## 结论",
        "",
        "（在此填写结论。JSON 侧车的 evidence_refs 目前为空数组，补充证据时只写仓库内相对路径。）",
    ]
    return "\n".join(lines) + "\n"


def create_report(
    repository_root: Path,
    *,
    kind: str,
    slug: str,
    requirements: list[str],
    defects: list[dict[str, str]],
    product_version: str | None = None,
    independence: str | None = None,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Create the Markdown/JSON skeleton pair and return their paths."""
    repository_root = repository_root.resolve()
    if kind == "version_summary":
        raise ReportScaffoldError("version_summary 有独立目录结构，请手动在 reports/versions/<版本>/ 下创建")
    if kind not in REPORT_KIND_DIRECTORIES:
        raise ReportScaffoldError(f"报告类型无效：{kind}")
    if not SLUG_PATTERN.fullmatch(slug):
        raise ReportScaffoldError("slug 只能含小写字母、数字和中划线，且不能以中划线开头或结尾")
    if product_version is None:
        product_version = _default_product_version(repository_root)
    elif not SEMVER_PATTERN.fullmatch(product_version):
        raise ReportScaffoldError("产品版本必须为 v<主>.<次>.<补丁> 形式")
    if independence is None:
        independence = "independent" if kind == "acceptance" else "non_independent"
    elif independence not in REPORT_INDEPENDENCE:
        raise ReportScaffoldError(f"独立性无效：{independence}")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    stem_timestamp = moment.strftime("%Y%m%dT%H%M%SZ")
    recorded_at_utc = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    report_id = f"RPT-{slug.upper()}-{stem_timestamp}-001"
    if not REPORT_ID_PATTERN.fullmatch(report_id):
        raise ReportScaffoldError(f"生成的报告标识无效：{report_id}")
    target_directory = repository_root / REPORT_KIND_DIRECTORIES[kind]
    markdown_path = target_directory / f"{stem_timestamp}-{slug}.md"
    metadata_path = target_directory / f"{stem_timestamp}-{slug}.json"
    if markdown_path.exists() or metadata_path.exists():
        raise ReportScaffoldError(f"目标报告文件已存在，不覆盖：{markdown_path.name}")
    author_role = kind
    metadata: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": report_id,
        "recorded_at_utc": recorded_at_utc,
        "report_kind": kind,
        "author_role": author_role,
        "independence": independence,
        "product_version": product_version,
        "decision_scope": "archive_local",
        "verdict": "accepted",
        "requirements": requirements,
        "defects": defects,
        "evidence_refs": [],
        "release_gates": [
            {"gate_id": gate_id, "status": status, "requirements": requirements if index == 0 else []}
            for index, (gate_id, status) in enumerate(RELEASE_GATE_SKELETON)
        ],
    }
    markdown = _render_markdown(
        slug=slug,
        kind=kind,
        report_id=report_id,
        recorded_at_utc=recorded_at_utc,
        author_role=author_role,
        independence=independence,
        product_version=product_version,
        requirements=requirements,
    )
    target_directory.mkdir(parents=True, exist_ok=True)
    try:
        with markdown_path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(markdown)
    except FileExistsError as error:
        raise ReportScaffoldError(f"目标报告文件已存在，不覆盖：{markdown_path.name}") from error
    try:
        with metadata_path.open("x", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as error:
        markdown_path.unlink(missing_ok=True)
        raise ReportScaffoldError(f"目标报告文件已存在，不覆盖：{metadata_path.name}") from error
    return markdown_path, metadata_path


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成规范化归档报告骨架（Markdown 与 JSON 侧车）")
    parser.add_argument("--kind", required=True, help="报告类型：development/testing/acceptance/infrastructure/review")
    parser.add_argument("--slug", required=True, help="报告短名，小写字母、数字与中划线")
    parser.add_argument("--reqs", required=True, help="逗号分隔的 REQ 标识，必须存在于 docs/requirements.md")
    parser.add_argument("--defs", help="逗号分隔的 缺陷ID:relationship，缺陷须存在于缺陷台账")
    parser.add_argument("--version", help="产品版本，默认取 reports/versions/ 下最高版本目录")
    parser.add_argument("--independence", choices=sorted(REPORT_INDEPENDENCE), help="覆盖默认独立性")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    repository_root = Path(__file__).resolve().parents[1]
    try:
        known_requirements = _load_known_requirements(repository_root)
        known_defects = _load_known_defects(repository_root)
        requirements = _parse_requirements(arguments.reqs, known_requirements)
        defects = _parse_defects(arguments.defs, known_defects)
        markdown_path, metadata_path = create_report(
            repository_root,
            kind=arguments.kind,
            slug=arguments.slug,
            requirements=requirements,
            defects=defects,
            product_version=arguments.version,
            independence=arguments.independence,
        )
    except ReportScaffoldError as error:
        print(f"报告骨架生成失败：{error}", file=sys.stderr)
        return 2
    print("已生成报告骨架：")
    print(f"- {markdown_path.relative_to(repository_root).as_posix()}")
    print(f"- {metadata_path.relative_to(repository_root).as_posix()}")
    print("下一步：填写 Markdown 正文（范围/验证/结论）并在 JSON 侧车补充 evidence_refs（仅仓库内相对路径），然后运行：")
    print("python scripts/archive_v1.py --check-tree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
