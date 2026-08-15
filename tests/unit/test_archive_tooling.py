from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FIXED_NOW = datetime(2026, 8, 15, 1, 2, 3, tzinfo=UTC)
EXISTING_RUN_ID = "20260815T000000Z"
NEW_RUN_ID = "20260815T010000Z-fixture"
EXISTING_MANIFEST_CONTENT = b"existing fixture manifest"
NEW_MANIFEST_CONTENT = b"new fixture manifest"
EXISTING_ACCEPTANCE = "reports/testing/20260815T000100Z-fixture-acceptance.md"
NEW_ACCEPTANCE = "reports/testing/20260815T010100Z-new-acceptance.md"


def _load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


archive_v1 = _load_module("archive_v1_for_tooling", SCRIPTS / "archive_v1.py")
new_report = _load_module("new_report_under_test", SCRIPTS / "new_report.py")
register_snapshot = _load_module("register_snapshot_under_test", SCRIPTS / "register_snapshot.py")


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_entry(
    run_id: str,
    manifest_sha256: str,
    verdict: str,
    acceptance_report: str,
    acceptance_report_sha256: str,
    supersedes_run_id: str | None,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "manifest_sha256": manifest_sha256,
        "archive_local_verdict": verdict,
        "acceptance_report": acceptance_report,
        "acceptance_report_sha256": acceptance_report_sha256,
        "supersedes_run_id": supersedes_run_id,
    }


@pytest.fixture()
def tooling_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    _write(
        root / "docs/requirements.md",
        "# 需求\n\n- REQ-001：本地化。\n- REQ-042：hash 校验。\n- REQ-044：UI 页面。\n- REQ-045：物理门禁。\n- REQ-049：导入预填。\n",
    )
    _write_json(root / "docs/v1-archive/defect-ledger.json", {
        "schema_version": 1,
        "defects": [
            {
                "defect_id": "DEF-PG-001",
                "severity": "P1",
                "summary": "真实 PostgreSQL 源库到独立空目标还原未物理验证",
                "discovery": "reports/testing/20260815T000100Z-fixture-acceptance.md",
                "retest": "无；需要独立的物理 PostgreSQL 环境",
                "disposition": "blocked",
            },
            {
                "defect_id": "DEF-ARCH-001",
                "severity": "P1",
                "summary": "归档 T1 PID 语义键未完整脱敏",
                "discovery": "reports/testing/20260815T000100Z-fixture-acceptance.md",
                "retest": "scripts/archive_v1.py",
                "disposition": "resolved_in_successor_snapshot",
            },
        ],
    })
    _write(root / EXISTING_ACCEPTANCE, "# 既有验收\n")
    _write(root / NEW_ACCEPTANCE, "# 新验收\n")
    existing_entry = _snapshot_entry(
        EXISTING_RUN_ID,
        hashlib.sha256(EXISTING_MANIFEST_CONTENT).hexdigest(),
        "accepted",
        EXISTING_ACCEPTANCE,
        _sha256(root / EXISTING_ACCEPTANCE),
        None,
    )
    _write_json(root / "docs/v1-archive/snapshot-register.json", {
        "schema_version": 1,
        "purpose": "Fixture snapshot chain.",
        "entries": [existing_entry],
    })
    for run_id, content in ((EXISTING_RUN_ID, EXISTING_MANIFEST_CONTENT), (NEW_RUN_ID, NEW_MANIFEST_CONTENT)):
        _write(
            root / f"archives/V1-current-audit-{run_id}/manifest.sha256",
            f"{hashlib.sha256(content).hexdigest()}  manifest.json\n",
        )
    for version in ("v1.0.0", "v1.2.0"):
        _write(root / f"reports/versions/{version}/version-summary.md", f"# {version} 汇总\n")
        _write_json(root / f"reports/versions/{version}/version-summary.json", {
            "schema_version": 1,
            "report_id": f"RPT-FIXTURE-{version.upper().replace('.', '-')}-SUMMARY",
            "recorded_at_utc": "2026-08-15T00:02:00Z",
            "report_kind": "version_summary",
            "author_role": "release_management",
            "independence": "not_applicable",
            "product_version": version,
            "decision_scope": "version_archive",
            "verdict": "accepted",
            "requirements": [],
            "defects": [],
            "evidence_refs": [],
            "release_gates": [],
            "snapshot_chain": [existing_entry],
            "recommended_snapshot_run_id": EXISTING_RUN_ID,
            "summary": "Fixture summary.",
        })
    return root


def _create_report(repo: Path, **overrides: object) -> tuple[Path, Path]:
    parameters = {
        "kind": "development",
        "slug": "my-change",
        "requirements": ["REQ-042", "REQ-049"],
        "defects": [{"defect_id": "DEF-PG-001", "relationship": "repaired"}],
        "now": FIXED_NOW,
    }
    parameters.update(overrides)
    return new_report.create_report(repo, **parameters)


def test_new_report_generates_pair_with_expected_metadata(tooling_repo: Path) -> None:
    markdown_path, metadata_path = _create_report(tooling_repo)

    assert markdown_path.name == "20260815T010203Z-my-change.md"
    assert metadata_path.name == "20260815T010203Z-my-change.json"
    assert markdown_path.parent == tooling_repo / "reports/development"
    assert markdown_path.is_file() and metadata_path.is_file()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["schema_version"] == 1
    assert metadata["report_id"] == "RPT-MY-CHANGE-20260815T010203Z-001"
    assert metadata["recorded_at_utc"] == "2026-08-15T01:02:03Z"
    assert metadata["report_kind"] == "development"
    assert metadata["author_role"] == "development"
    assert metadata["independence"] == "non_independent"
    assert metadata["product_version"] == "v1.2.0"
    assert metadata["decision_scope"] == "archive_local"
    assert metadata["verdict"] == "accepted"
    assert metadata["requirements"] == ["REQ-042", "REQ-049"]
    assert metadata["defects"] == [{"defect_id": "DEF-PG-001", "relationship": "repaired"}]
    assert metadata["evidence_refs"] == []
    gates = metadata["release_gates"]
    assert [gate["gate_id"] for gate in gates] == [
        "GATE-UNIT-INTEGRATION-REGRESSION",
        "GATE-FRONTEND-BUILD",
        "GATE-BROWSER-BLACKBOX",
        "GATE-COMPOSE-PHYSICAL",
        "GATE-RELEASE-READINESS",
    ]
    assert [gate["status"] for gate in gates] == ["passed", "passed", "blocked", "blocked", "blocked"]
    assert gates[0]["requirements"] == ["REQ-042", "REQ-049"]
    assert all(gate["requirements"] == [] for gate in gates[1:])
    markdown = markdown_path.read_text(encoding="utf-8")
    assert markdown.startswith("# my-change：开发报告\n")
    assert "RPT-MY-CHANGE-20260815T010203Z-001" in markdown
    for section in ("## 范围", "## 验证", "## 结论"):
        assert section in markdown


def test_new_report_kind_defaults_and_version_override(tooling_repo: Path) -> None:
    markdown_path, metadata_path = _create_report(
        tooling_repo,
        kind="acceptance",
        slug="acceptance-check",
        now=datetime(2026, 8, 15, 2, 3, 4, tzinfo=UTC),
    )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert markdown_path.parent == tooling_repo / "reports/testing"
    assert metadata["report_kind"] == "acceptance"
    assert metadata["author_role"] == "acceptance"
    assert metadata["independence"] == "independent"

    _, override_metadata_path = _create_report(
        tooling_repo,
        slug="pinned-version",
        product_version="v9.9.9",
        independence="not_applicable",
        now=datetime(2026, 8, 15, 3, 4, 5, tzinfo=UTC),
    )
    override_metadata = json.loads(override_metadata_path.read_text(encoding="utf-8"))
    assert override_metadata["product_version"] == "v9.9.9"
    assert override_metadata["independence"] == "not_applicable"


def test_new_report_rejects_unknown_requirement(tooling_repo: Path) -> None:
    known = new_report._load_known_requirements(tooling_repo)
    assert {"REQ-001", "REQ-042", "REQ-044", "REQ-045", "REQ-049"} <= known
    with pytest.raises(new_report.ReportScaffoldError, match="REQ-999"):
        new_report._parse_requirements("REQ-042,REQ-999", known)
    with pytest.raises(new_report.ReportScaffoldError, match="重复"):
        new_report._parse_requirements("REQ-042,REQ-042", known)


def test_new_report_rejects_unknown_defect_and_bad_relationship(tooling_repo: Path) -> None:
    known = new_report._load_known_defects(tooling_repo)
    assert known == {"DEF-PG-001", "DEF-ARCH-001"}
    with pytest.raises(new_report.ReportScaffoldError, match="DEF-NOPE-001"):
        new_report._parse_defects("DEF-NOPE-001:repaired", known)
    with pytest.raises(new_report.ReportScaffoldError, match="缺陷关系无效"):
        new_report._parse_defects("DEF-PG-001:bogus", known)
    with pytest.raises(new_report.ReportScaffoldError, match="ID:relationship"):
        new_report._parse_defects("DEF-PG-001", known)
    parsed = new_report._parse_defects("DEF-PG-001:repaired,DEF-ARCH-001:retested", known)
    assert parsed == [
        {"defect_id": "DEF-PG-001", "relationship": "repaired"},
        {"defect_id": "DEF-ARCH-001", "relationship": "retested"},
    ]


def test_new_report_rejects_invalid_kind_slug_version(tooling_repo: Path) -> None:
    with pytest.raises(new_report.ReportScaffoldError, match="version_summary"):
        _create_report(tooling_repo, kind="version_summary")
    with pytest.raises(new_report.ReportScaffoldError, match="slug"):
        _create_report(tooling_repo, slug="Bad_Slug")
    with pytest.raises(new_report.ReportScaffoldError, match="产品版本"):
        _create_report(tooling_repo, slug="bad-version", product_version="1.2.3")


def test_new_report_never_overwrites(tooling_repo: Path) -> None:
    markdown_path, metadata_path = _create_report(tooling_repo)
    original = markdown_path.read_text(encoding="utf-8")
    with pytest.raises(new_report.ReportScaffoldError, match="不覆盖"):
        _create_report(tooling_repo)
    assert markdown_path.read_text(encoding="utf-8") == original
    assert metadata_path.is_file()


def test_new_report_sidecar_passes_builder_validation(tooling_repo: Path) -> None:
    markdown_path, metadata_path = _create_report(tooling_repo)

    archive_v1._validate_report_text(markdown_path.read_text(encoding="utf-8"))
    archive_v1._validate_report_text(metadata_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    required_fields = {
        "schema_version", "report_id", "recorded_at_utc", "report_kind", "author_role",
        "independence", "product_version", "decision_scope", "verdict", "requirements",
        "defects", "evidence_refs", "release_gates",
    }
    optional_fields = {
        "archive_run_id", "archive_manifest_sha256", "supersedes_report_id", "snapshot_chain",
        "recommended_snapshot_run_id", "summary",
    }
    assert required_fields <= set(metadata) <= required_fields | optional_fields
    archive_v1._validate_report_value(metadata)
    assert metadata["schema_version"] == archive_v1.REPORT_SCHEMA_VERSION
    assert archive_v1.REPORT_ID_PATTERN.fullmatch(metadata["report_id"])
    archive_v1._validate_utc_timestamp(metadata["recorded_at_utc"])
    assert archive_v1.SEMVER_PATTERN.fullmatch(metadata["product_version"])
    for field, allowed in (
        ("report_kind", archive_v1.REPORT_KINDS),
        ("author_role", archive_v1.REPORT_AUTHOR_ROLES),
        ("independence", archive_v1.REPORT_INDEPENDENCE),
        ("decision_scope", archive_v1.REPORT_DECISION_SCOPES),
        ("verdict", archive_v1.REPORT_VERDICTS),
    ):
        assert metadata[field] in allowed
    known_requirements = archive_v1._known_requirement_ids(tooling_repo)
    known_defects = {defect_id for defect_id, *_ in archive_v1._load_defect_ledger(tooling_repo)}
    archive_v1._validate_report_references(metadata, known_requirements, known_defects, set(), set())
    archive_v1._validate_snapshot_chain(metadata, [])


def test_register_snapshot_appends_entry_and_mirrors_chain(tooling_repo: Path) -> None:
    result = register_snapshot.register_snapshot(
        tooling_repo,
        run_id=NEW_RUN_ID,
        verdict="accepted",
        acceptance=NEW_ACCEPTANCE,
    )

    entry = result["entry"]
    assert entry == {
        "run_id": NEW_RUN_ID,
        "manifest_sha256": hashlib.sha256(NEW_MANIFEST_CONTENT).hexdigest(),
        "archive_local_verdict": "accepted",
        "acceptance_report": NEW_ACCEPTANCE,
        "acceptance_report_sha256": _sha256(tooling_repo / NEW_ACCEPTANCE),
        "supersedes_run_id": EXISTING_RUN_ID,
    }
    register_path = tooling_repo / "docs/v1-archive/snapshot-register.json"
    register_text = register_path.read_text(encoding="utf-8")
    register = json.loads(register_text)
    assert len(register["entries"]) == 2
    assert register["entries"][-1] == entry
    assert register["entries"][0]["run_id"] == EXISTING_RUN_ID
    assert register_text == json.dumps(register, ensure_ascii=False, indent=2) + "\n"
    for version in ("v1.0.0", "v1.2.0"):
        summary_path = tooling_repo / f"reports/versions/{version}/version-summary.json"
        summary_text = summary_path.read_text(encoding="utf-8")
        summary = json.loads(summary_text)
        assert len(summary["snapshot_chain"]) == len(register["entries"])
        for mirrored, registered in zip(summary["snapshot_chain"], register["entries"], strict=True):
            assert mirrored == registered
        assert summary_text == json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    assert result["mirrored_summaries"] == [
        "reports/versions/v1.0.0/version-summary.json",
        "reports/versions/v1.2.0/version-summary.json",
    ]
    assert set(result["suggested_rows"]) == {
        "reports/versions/v1.0.0/version-summary.md",
        "reports/versions/v1.2.0/version-summary.md",
    }
    row = result["suggested_rows"]["reports/versions/v1.0.0/version-summary.md"]
    assert row == (
        f"| `{NEW_RUN_ID}` | `accepted` | `{hashlib.sha256(NEW_MANIFEST_CONTENT).hexdigest()}` "
        f"| `20260815T010100Z-new-acceptance` | 后继 `{EXISTING_RUN_ID}` |"
    )


def test_register_snapshot_rejects_duplicate_run_id_without_writing(tooling_repo: Path) -> None:
    register_path = tooling_repo / "docs/v1-archive/snapshot-register.json"
    register_before = register_path.read_bytes()
    summary_before = {
        version: (tooling_repo / f"reports/versions/{version}/version-summary.json").read_bytes()
        for version in ("v1.0.0", "v1.2.0")
    }

    with pytest.raises(register_snapshot.SnapshotRegisterError, match="不重复登记"):
        register_snapshot.register_snapshot(
            tooling_repo,
            run_id=EXISTING_RUN_ID,
            verdict="accepted",
            acceptance=EXISTING_ACCEPTANCE,
        )

    assert register_path.read_bytes() == register_before
    for version, content in summary_before.items():
        assert (tooling_repo / f"reports/versions/{version}/version-summary.json").read_bytes() == content


def test_register_snapshot_requires_manifest(tooling_repo: Path) -> None:
    with pytest.raises(register_snapshot.SnapshotRegisterError, match="先构建归档"):
        register_snapshot.register_snapshot(
            tooling_repo,
            run_id="20260815T020000Z",
            verdict="accepted",
            acceptance=NEW_ACCEPTANCE,
        )
    register = json.loads((tooling_repo / "docs/v1-archive/snapshot-register.json").read_text(encoding="utf-8"))
    assert len(register["entries"]) == 1


def test_register_snapshot_rejects_invalid_arguments(tooling_repo: Path) -> None:
    with pytest.raises(register_snapshot.SnapshotRegisterError, match="run-id"):
        register_snapshot.register_snapshot(
            tooling_repo, run_id="not-a-run-id", verdict="accepted", acceptance=NEW_ACCEPTANCE,
        )
    with pytest.raises(register_snapshot.SnapshotRegisterError, match="裁定无效"):
        register_snapshot.register_snapshot(
            tooling_repo, run_id=NEW_RUN_ID, verdict="not_applicable", acceptance=NEW_ACCEPTANCE,
        )
    _write(tooling_repo / "reports/development/not-testing.md", "# 位置错误\n")
    with pytest.raises(register_snapshot.SnapshotRegisterError, match="reports/testing/"):
        register_snapshot.register_snapshot(
            tooling_repo, run_id=NEW_RUN_ID, verdict="accepted", acceptance="reports/development/not-testing.md",
        )
    with pytest.raises(register_snapshot.SnapshotRegisterError, match="不存在"):
        register_snapshot.register_snapshot(
            tooling_repo, run_id=NEW_RUN_ID, verdict="accepted", acceptance="reports/testing/missing.md",
        )


def test_register_snapshot_first_entry_has_no_predecessor(tooling_repo: Path) -> None:
    register_path = tooling_repo / "docs/v1-archive/snapshot-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    register["entries"] = []
    register_path.write_text(json.dumps(register, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    result = register_snapshot.register_snapshot(
        tooling_repo,
        run_id=NEW_RUN_ID,
        verdict="rejected",
        acceptance=NEW_ACCEPTANCE,
    )

    assert result["entry"]["supersedes_run_id"] is None
    row = result["suggested_rows"]["reports/versions/v1.2.0/version-summary.md"]
    assert "首项" in row
    summary = json.loads((tooling_repo / "reports/versions/v1.2.0/version-summary.json").read_text(encoding="utf-8"))
    assert summary["snapshot_chain"] == [result["entry"]]
