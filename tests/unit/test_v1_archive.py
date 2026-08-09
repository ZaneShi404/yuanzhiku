from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest


RUN_ROOT = Path(os.environ.get("YUANZHIKU_TEST_RUNTIME", Path(__file__).resolve().parents[1] / "runtime")) / "v1-archive"
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FIXTURE_SOURCE_RUN_ID = "20260730T000000Z"
FIXTURE_RUNTIME_SOURCE = f"tests/runtime/fixture-{FIXTURE_SOURCE_RUN_ID}/result.json"
FIXTURE_ARCHIVE_PATH = f"evidence/runtime/fixture-{FIXTURE_SOURCE_RUN_ID}/result.json"
REPLAY_CONTRACT_FILES = (
    "docs/v1-archive/archive-policy.md",
    "archives/README.md",
)
REPLAY_CONTRACT_PHRASES = (
    ".venv",
    "不得安装依赖",
    "PYTHONDONTWRITEBYTECODE",
    "-p no:cacheprovider",
    "tests\\runtime\\archive-replay-<replay-run-id>\\copy",
    "封存目录",
    "data/",
)


def _load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


archive_v1 = _load_module("archive_v1_under_test", SCRIPTS / "archive_v1.py")
verify_v1_archive = _load_module("verify_v1_archive_under_test", SCRIPTS / "verify_v1_archive.py")


@pytest.fixture()
def runtime_root() -> Path:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    root = RUN_ROOT / uuid.uuid4().hex
    root.mkdir()
    yield root
    _unseal_test_archive(root)
    shutil.rmtree(root, ignore_errors=True)


def _write(path: Path, content: str = "content\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _unseal_test_archive(archive: Path) -> None:
    account = os.environ.get("USERNAME")
    assert account
    completed = subprocess.run(
        ["icacls", str(archive), "/inheritance:e", "/grant:r", f"{account}:(M)", "/t", "/c"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert completed.returncode == 0


def _copy_unsealed_archive(archive: Path, target: Path) -> Path:
    shutil.copytree(archive, target)
    _unseal_test_archive(target)
    return target


def _refresh_manifest(archive: Path, *, schema_version: int | None = None) -> None:
    _unseal_test_archive(archive)
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if schema_version is not None:
        manifest["schema_version"] = schema_version
    entries = []
    for path in sorted(archive.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "manifest.sha256"}:
            continue
        relative = path.relative_to(archive).as_posix()
        entries.append({
            "path": relative,
            "byte_size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "tier": "T1" if relative.startswith("evidence/") else "T0" if relative.startswith("baseline/") else "generated",
        })
    manifest["entries"] = entries
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (archive / "manifest.sha256").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "  manifest.json\n",
        encoding="ascii",
    )


def _refresh_manifest_member(archive: Path, relative: str) -> None:
    _unseal_test_archive(archive)
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    content = (archive / relative).read_bytes()
    entry = next(item for item in manifest["entries"] if item["path"] == relative)
    entry["byte_size"] = len(content)
    entry["sha256"] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (archive / "manifest.sha256").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "  manifest.json\n",
        encoding="ascii",
    )


def _write_fixture_repository(
    root: Path,
    *,
    runtime_content: dict[str, object] | None = None,
    declared_report: bool = True,
) -> Path:
    for relative in archive_v1.ROOT_FILES:
        _write(root / relative, "configuration\n")
    for relative in archive_v1.EXACT_BASELINE_FILES:
        _write(root / relative, "baseline\n")
    for relative in archive_v1.SOURCE_DIRECTORIES:
        directory = root / relative
        directory.mkdir(parents=True, exist_ok=True)
        if relative.startswith("reports/"):
            continue
        suffix = ".md" if relative.startswith("docs") else ".py"
        _write(directory / f"sample{suffix}", "safe sample\n")

    _write(root / "docs/requirements.md", "# Requirements\n\n- REQ-001\n- REQ-033a\n- REQ-045\n")
    _write(root / "docs/v1-archive/report-schema-v1.json", json.dumps({
        "schema_version": 1,
        "artifact_type": "normalized_archive_report_metadata",
        "purpose": "Defines the JSON sidecar contract for normalized archive reports. The paired Markdown file is the human-readable record.",
        "file_pair": {
            "markdown_extension": ".md",
            "metadata_extension": ".json",
            "same_stem_required": True,
        },
        "required_fields": [
            "schema_version", "report_id", "recorded_at_utc", "report_kind", "author_role",
            "independence", "product_version", "decision_scope", "verdict", "requirements",
            "defects", "evidence_refs", "release_gates",
        ],
        "optional_fields": [
            "archive_run_id", "archive_manifest_sha256", "supersedes_report_id", "snapshot_chain",
            "recommended_snapshot_run_id", "summary",
        ],
        "enums": {
            "report_kind": sorted(archive_v1.REPORT_KINDS),
            "author_role": sorted(archive_v1.REPORT_AUTHOR_ROLES),
            "independence": sorted(archive_v1.REPORT_INDEPENDENCE),
            "decision_scope": sorted(archive_v1.REPORT_DECISION_SCOPES),
            "verdict": sorted(archive_v1.REPORT_VERDICTS),
            "gate_status": sorted(archive_v1.REPORT_GATE_STATUSES),
        },
        "safety": {
            "relative_references_only": True,
            "forbidden_content": [
                "command lines", "absolute local paths", "runtime output bodies", "request bodies",
                "process identifiers", "credentials", "cookies", "tokens",
            ],
            "historical_reports": "Historical Markdown reports remain unchanged and are registered as legacy_inferred when no sidecar is present.",
        },
    }))

    rejected_run_id = "20260730T000001Z-fixture-rejected"
    accepted_run_id = "20260730T000002Z-fixture-accepted"
    rejected_manifest_sha256 = hashlib.sha256(b"fixture rejected manifest").hexdigest()
    accepted_manifest_sha256 = hashlib.sha256(b"fixture accepted manifest").hexdigest()
    legacy_acceptance_path = "reports/testing/legacy-rejected-acceptance.md"
    legacy_acceptance_content = "# Legacy Rejected Acceptance\n\nIndependent archive-local review rejected the fixture predecessor.\n"
    accepted_acceptance_path = "reports/testing/independent-accepted-acceptance.md"
    accepted_acceptance_content = "# Independent Accepted Acceptance\n\nIndependent archive-local acceptance record for the fixture candidate.\n"
    _write(root / legacy_acceptance_path, legacy_acceptance_content)
    _write(root / accepted_acceptance_path, accepted_acceptance_content)
    legacy_acceptance_sha256 = hashlib.sha256(
        (root / legacy_acceptance_path).read_bytes()
    ).hexdigest()
    accepted_acceptance_sha256 = hashlib.sha256(
        (root / accepted_acceptance_path).read_bytes()
    ).hexdigest()
    snapshot_chain = [
        {
            "run_id": rejected_run_id,
            "manifest_sha256": rejected_manifest_sha256,
            "archive_local_verdict": "rejected",
            "acceptance_report": legacy_acceptance_path,
            "acceptance_report_sha256": legacy_acceptance_sha256,
            "supersedes_run_id": None,
        },
        {
            "run_id": accepted_run_id,
            "manifest_sha256": accepted_manifest_sha256,
            "archive_local_verdict": "accepted",
            "acceptance_report": accepted_acceptance_path,
            "acceptance_report_sha256": accepted_acceptance_sha256,
            "supersedes_run_id": rejected_run_id,
        },
    ]
    _write(root / "reports/testing/independent-accepted-acceptance.json", json.dumps({
        "schema_version": 1,
        "report_id": "RPT-FIXTURE-ARCHIVE-ACCEPTANCE",
        "recorded_at_utc": "2026-07-30T00:00:01Z",
        "report_kind": "acceptance",
        "author_role": "acceptance",
        "independence": "independent",
        "product_version": "v1.0.0",
        "archive_run_id": accepted_run_id,
        "archive_manifest_sha256": accepted_manifest_sha256,
        "decision_scope": "archive_local",
        "verdict": "accepted",
        "requirements": [],
        "defects": [],
        "evidence_refs": [],
        "release_gates": [],
        "summary": "Fixture archive-local acceptance.",
    }))
    _write(root / "docs/v1-archive/legacy-report-register.json", json.dumps({
        "schema_version": 1,
        "purpose": "Allows only the frozen fixture legacy report to remain without a sidecar.",
        "entries": [{
            "source_path": legacy_acceptance_path,
            "source_sha256": legacy_acceptance_sha256,
        }],
    }))
    _write(root / "docs/v1-archive/snapshot-register.json", json.dumps({
        "schema_version": 1,
        "purpose": "Frozen fixture candidate chain.",
        "entries": snapshot_chain,
    }))
    if declared_report:
        _write(root / "reports/versions/v1.0.0/version-summary.md", "# Fixture v1.0.0 Summary\n")
        _write(root / "reports/versions/v1.0.0/version-summary.json", json.dumps({
            "schema_version": 1,
            "report_id": "RPT-FIXTURE-V1-0-0-SUMMARY",
            "recorded_at_utc": "2026-07-30T00:00:02Z",
            "report_kind": "version_summary",
            "author_role": "release_management",
            "independence": "not_applicable",
            "product_version": "v1.0.0",
            "decision_scope": "version_archive",
            "verdict": "accepted",
            "requirements": [],
            "defects": [],
            "evidence_refs": [accepted_acceptance_path],
            "release_gates": [{"gate_id": "GATE-PG-PHYSICAL", "status": "blocked", "requirements": ["REQ-045"]}],
            "recommended_snapshot_run_id": accepted_run_id,
            "snapshot_chain": snapshot_chain,
            "summary": "Fixture version archive remains release-blocked.",
        }))
    _write(root / "docs/v1-archive/evidence-allowlist.json", json.dumps({
        "schema_version": 1,
        "purpose": "synthetic fixture",
        "entries": [{
            "source": FIXTURE_RUNTIME_SOURCE,
            "archive_path": FIXTURE_ARCHIVE_PATH,
            "category": "testing",
            "tier": "T1",
            "source_run_id": FIXTURE_SOURCE_RUN_ID,
            "requirements": ["REQ-001"],
            "defects": [],
            "purpose": "synthetic fixture",
        }],
    }))
    _write(root / "docs/v1-archive/predecessor-register.json", json.dumps({
        "schema_version": 1,
        "entries": [],
    }))
    _write(root / FIXTURE_RUNTIME_SOURCE, json.dumps(runtime_content or {"status": "passed", "path": "relative"}))
    _write(root / "scripts/verify_v1_archive.py", "placeholder\n")
    return root


def _update_declared_report_register(
    archive: Path,
    report_id: str,
    metadata: dict[str, object],
) -> None:
    register_path = archive / "index/report-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    entry = next(item for item in register["entries"] if item["report_id"] == report_id)
    material = entry["metadata"]
    assert isinstance(material, dict)
    metadata_path = archive / material["archive_path"]
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    entry["declared"] = metadata
    material["archive_sha256"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, material["archive_path"])
    _refresh_manifest_member(archive, "index/report-register.json")


def _update_report_markdown_register(
    archive: Path,
    source_path: str,
    content: str,
) -> None:
    register_path = archive / "index/report-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    entry = next(item for item in register["entries"] if item["markdown"]["source_path"] == source_path)
    material = entry["markdown"]
    assert isinstance(material, dict)
    markdown_path = archive / material["archive_path"]
    markdown_path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
    material["archive_sha256"] = digest
    material["source_sha256"] = digest
    register_path.write_text(json.dumps(register), encoding="utf-8")
    inventory_path = archive / "provenance/source-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    inventory_entry = next(item for item in inventory["entries"] if item["source_path"] == source_path)
    inventory_entry["source_sha256"] = digest
    inventory_entry["source_byte_size"] = len(markdown_path.read_bytes())
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    _refresh_manifest_member(archive, material["archive_path"])
    _refresh_manifest_member(archive, "index/report-register.json")
    _refresh_manifest_member(archive, "provenance/source-inventory.json")


def _build_fixture_archive(root: Path, run_id: str) -> tuple[Path, Path]:
    return archive_v1.build_archive(
        root,
        root / "archives",
        run_id,
        verifier_script=SCRIPTS / "verify_v1_archive.py",
    )


def _build_mutable_fixture_archive(root: Path, run_id: str) -> tuple[Path, Path]:
    archive, archive_zip = _build_fixture_archive(root, run_id)
    return _copy_unsealed_archive(archive, archive.with_name(f"{archive.name}-mutable")), archive_zip


def test_archive_replay_contract_is_complete() -> None:
    project_root = Path(__file__).resolve().parents[2]

    for relative in REPLAY_CONTRACT_FILES:
        content = (project_root / relative).read_text(encoding="utf-8")
        for phrase in REPLAY_CONTRACT_PHRASES:
            assert phrase in content, f"{relative} is missing {phrase!r}"


def test_builder_copies_only_allowed_material_and_verifier_accepts(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    _write(repository / "data" / "private.txt", "must not archive\n")
    _write(repository / f"tests/runtime/fixture-{FIXTURE_SOURCE_RUN_ID}/knowledge.db", "database\n")

    archive, archive_zip = _build_fixture_archive(repository, "20260730T010203Z")

    assert (archive / f"baseline/backend/app/sample.py").is_file()
    assert (archive / FIXTURE_ARCHIVE_PATH).is_file()
    assert not (archive / "baseline/data/private.txt").exists()
    assert not any(path.suffix == ".db" for path in archive.rglob("*"))
    inventory = json.loads(
        (archive / "provenance/source-inventory.json").read_text(encoding="utf-8")
    )
    t1_inventory = next(
        item for item in inventory["entries"] if item["archive_path"] == FIXTURE_ARCHIVE_PATH
    )
    assert t1_inventory["source_run_id"] == FIXTURE_SOURCE_RUN_ID
    register = json.loads(
        (archive / "index/evidence-register.json").read_text(encoding="utf-8")
    )
    t1_register = next(
        item for item in register["entries"] if item["archive_path"] == FIXTURE_ARCHIVE_PATH
    )
    assert t1_register["source_run_id"] == FIXTURE_SOURCE_RUN_ID
    assert (archive / "index/report-register.json").is_file()
    report_register = json.loads(
        (archive / "index/report-register.json").read_text(encoding="utf-8")
    )
    declared_summary = next(
        item for item in report_register["entries"]
        if item["report_id"] == "RPT-FIXTURE-V1-0-0-SUMMARY"
    )
    assert declared_summary["normalization_status"] == "declared"
    assert any(
        item["normalization_status"] == "legacy_inferred"
        for item in report_register["entries"]
    )
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"
    assert verify_v1_archive.verify_archive(archive_zip)["status"] == "verified"

    sealed_member = archive / "baseline/backend/app/sample.py"
    with pytest.raises(PermissionError):
        sealed_member.write_text("must not modify published archive\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        (archive / "must-not-create.txt").write_text(
            "must not create published archive member\n",
            encoding="utf-8",
        )



def test_archive_contract_accepts_suffixed_requirement_ids(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "suffixed-requirement")
    summary_path = repository / "reports/versions/v1.0.0/version-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["requirements"] = ["REQ-033a"]
    summary["release_gates"] = [{
        "gate_id": "GATE-VIDEO-JOBS",
        "status": "passed",
        "requirements": ["REQ-033a"],
    }]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    archive, archive_zip = _build_fixture_archive(repository, "20260730T010213Z")

    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"
    assert verify_v1_archive.verify_archive(archive_zip)["status"] == "verified"


def test_v1_manifest_remains_compatible_without_report_register(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010214Z")

    (archive / "index/report-register.json").unlink()
    _refresh_manifest(archive, schema_version=1)

    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_builder_rejects_unpaired_declared_report_metadata(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    _write(
        repository / "reports/testing/orphan.json",
        json.dumps({"schema_version": 1}),
    )

    with pytest.raises(archive_v1.ArchiveError, match="缺少同名 Markdown"):
        _build_fixture_archive(repository, "20260730T010215Z")


def test_builder_rejects_unregistered_legacy_report_and_frozen_hash_drift(runtime_root: Path) -> None:
    unregistered_repository = _write_fixture_repository(runtime_root / "unregistered")
    _write(
        unregistered_repository / "reports/testing/unregistered-legacy.md",
        "# Unregistered Legacy Report\n",
    )

    with pytest.raises(archive_v1.ArchiveError, match="缺少同名 JSON"):
        _build_fixture_archive(unregistered_repository, "20260730T010218Z")

    drift_repository = _write_fixture_repository(runtime_root / "hash-drift")
    legacy_path = drift_repository / "reports/testing/legacy-rejected-acceptance.md"
    legacy_path.write_text("# Drifted Legacy Report\n", encoding="utf-8")

    with pytest.raises(archive_v1.ArchiveError, match="快照登记条目无效"):
        _build_fixture_archive(drift_repository, "20260730T010219Z")


def test_builder_rejects_snapshot_register_contract_drift(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    register_path = repository / "docs/v1-archive/snapshot-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    register["entries"][1]["supersedes_run_id"] = None
    register_path.write_text(json.dumps(register), encoding="utf-8")

    with pytest.raises(archive_v1.ArchiveError, match="快照登记条目无效"):
        _build_fixture_archive(repository, "20260730T010220Z")


def test_builder_rejects_version_summary_chain_and_declared_acceptance_drift(runtime_root: Path) -> None:
    chain_repository = _write_fixture_repository(runtime_root / "chain")
    summary_path = chain_repository / "reports/versions/v1.0.0/version-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["snapshot_chain"] = summary["snapshot_chain"][:-1]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    with pytest.raises(archive_v1.ArchiveError, match="版本汇总快照链未与冻结登记一致"):
        _build_fixture_archive(chain_repository, "20260730T010221Z")

    acceptance_repository = _write_fixture_repository(runtime_root / "acceptance")
    acceptance_path = acceptance_repository / "reports/testing/independent-accepted-acceptance.json"
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["archive_manifest_sha256"] = "0" * 64
    acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")

    with pytest.raises(archive_v1.ArchiveError, match="冻结快照登记一致"):
        _build_fixture_archive(acceptance_repository, "20260730T010222Z")


def test_builder_rejects_report_schema_safety_contract_drift(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    schema_path = repository / "docs/v1-archive/report-schema-v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["safety"]["forbidden_content"].remove("tokens")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(archive_v1.ArchiveError, match="报告侧车 schema"):
        _build_fixture_archive(repository, "20260730T010223Z")


def test_verifier_rejects_tampered_v2_report_register(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010216Z")
    register_path = archive / "index/report-register.json"
    report_register = json.loads(register_path.read_text(encoding="utf-8"))
    summary = next(
        item for item in report_register["entries"]
        if item["report_id"] == "RPT-FIXTURE-V1-0-0-SUMMARY"
    )
    summary["markdown"]["archive_sha256"] = "0" * 64
    register_path.write_text(json.dumps(report_register), encoding="utf-8")
    _refresh_manifest_member(archive, "index/report-register.json")

    with pytest.raises(verify_v1_archive.VerificationError, match="材料无法追溯"):
        verify_v1_archive.verify_archive(archive)

    summary["markdown"]["archive_sha256"] = hashlib.sha256(
        (archive / summary["markdown"]["archive_path"]).read_bytes()
    ).hexdigest()
    metadata_path = archive / summary["metadata"]["archive_path"]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["recommended_snapshot_run_id"] = metadata["snapshot_chain"][0]["run_id"]
    summary["declared"] = metadata
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    summary["metadata"]["archive_sha256"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    register_path.write_text(json.dumps(report_register), encoding="utf-8")
    _refresh_manifest_member(archive, summary["metadata"]["archive_path"])
    _refresh_manifest_member(archive, "index/report-register.json")

    with pytest.raises(verify_v1_archive.VerificationError, match="版本汇总推荐了未接受的快照"):
        verify_v1_archive.verify_archive(archive)


def test_verifier_rejects_legacy_register_hash_drift_and_unused_entry(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010224Z")
    register_path = archive / "baseline/docs/v1-archive/legacy-report-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    register["entries"][0]["source_sha256"] = "0" * 64
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/docs/v1-archive/legacy-report-register.json")

    with pytest.raises(verify_v1_archive.VerificationError, match="历史推断报告关联无效"):
        verify_v1_archive.verify_archive(archive)

    legacy_markdown = archive / "baseline/reports/testing/legacy-rejected-acceptance.md"
    register["entries"][0]["source_sha256"] = hashlib.sha256(legacy_markdown.read_bytes()).hexdigest()
    register["entries"].append({
        "source_path": "reports/testing/unused-legacy.md",
        "source_sha256": "1" * 64,
    })
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/docs/v1-archive/legacy-report-register.json")

    with pytest.raises(verify_v1_archive.VerificationError, match="历史报告登记未与无侧车 Markdown 报告完全一致"):
        verify_v1_archive.verify_archive(archive)


def test_verifier_rejects_snapshot_register_contract_drift(runtime_root: Path) -> None:
    cases = (
        ("duplicate", lambda entries: entries[1].update(run_id=entries[0]["run_id"])),
        ("ordering", lambda entries: entries[1].update(supersedes_run_id=None)),
        ("hash", lambda entries: entries[1].update(acceptance_report_sha256="0" * 64)),
    )
    for index, (_, mutate) in enumerate(cases):
        repository = _write_fixture_repository(runtime_root / f"repository-{index}")
        archive, _ = _build_mutable_fixture_archive(repository, f"20260730T01022{5 + index}Z")
        register_path = archive / "baseline/docs/v1-archive/snapshot-register.json"
        register = json.loads(register_path.read_text(encoding="utf-8"))
        mutate(register["entries"])
        register_path.write_text(json.dumps(register), encoding="utf-8")
        _refresh_manifest_member(archive, "baseline/docs/v1-archive/snapshot-register.json")

        with pytest.raises(verify_v1_archive.VerificationError, match="快照登记条目无效"):
            verify_v1_archive.verify_archive(archive)


def test_verifier_rejects_version_summary_chain_drift(runtime_root: Path) -> None:
    cases = (
        lambda chain: chain.pop(),
        lambda chain: chain.reverse(),
        lambda chain: chain[1].update(manifest_sha256="0" * 64),
    )
    for index, mutate in enumerate(cases):
        repository = _write_fixture_repository(runtime_root / f"repository-{index}")
        archive, _ = _build_mutable_fixture_archive(repository, f"20260730T0102{28 + index:02d}Z")
        metadata_path = archive / "baseline/reports/versions/v1.0.0/version-summary.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        chain = metadata["snapshot_chain"]
        assert isinstance(chain, list)
        mutate(chain)
        _update_declared_report_register(archive, "RPT-FIXTURE-V1-0-0-SUMMARY", metadata)

        with pytest.raises(verify_v1_archive.VerificationError, match="版本汇总快照链未与冻结登记一致"):
            verify_v1_archive.verify_archive(archive)


def test_verifier_rejects_declared_acceptance_identity_drift(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010231Z")
    metadata_path = archive / "baseline/reports/testing/independent-accepted-acceptance.json"
    original = json.loads(metadata_path.read_text(encoding="utf-8"))
    mutations = (
        ("archive_run_id", "20260730T999999Z-unknown"),
        ("archive_manifest_sha256", "0" * 64),
        ("verdict", "rejected"),
    )
    for field, value in mutations:
        metadata = dict(original)
        metadata[field] = value
        _update_declared_report_register(archive, "RPT-FIXTURE-ARCHIVE-ACCEPTANCE", metadata)

        with pytest.raises(verify_v1_archive.VerificationError, match="归档身份未与冻结快照登记一致"):
            verify_v1_archive.verify_archive(archive)

    _update_declared_report_register(archive, "RPT-FIXTURE-ARCHIVE-ACCEPTANCE", original)
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_verifier_rejects_report_schema_contract_drift(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010232Z")
    schema_path = archive / "baseline/docs/v1-archive/report-schema-v1.json"
    original = json.loads(schema_path.read_text(encoding="utf-8"))
    mutations = (
        lambda schema: schema["enums"].__setitem__("verdict", ["accepted"]),
        lambda schema: schema["optional_fields"].pop(),
        lambda schema: schema["file_pair"].__setitem__("same_stem_required", False),
        lambda schema: schema["safety"]["forbidden_content"].pop(),
    )
    for mutate in mutations:
        schema = json.loads(json.dumps(original))
        mutate(schema)
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        _refresh_manifest_member(archive, "baseline/docs/v1-archive/report-schema-v1.json")

        with pytest.raises(verify_v1_archive.VerificationError, match="报告侧车 schema"):
            verify_v1_archive.verify_archive(archive)

    schema_path.write_text(json.dumps(original), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/docs/v1-archive/report-schema-v1.json")
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_verifier_rejects_declared_report_runtime_output_text(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010233Z")
    source_path = "reports/versions/v1.0.0/version-summary.md"
    original = (archive / "baseline" / source_path).read_text(encoding="utf-8")
    for forbidden in ("stdout", "stderr", "traceback", "stacktrace", "response", "pid: 12345"):
        _update_report_markdown_register(archive, source_path, f"{original}\n{forbidden}\n")

        with pytest.raises(verify_v1_archive.VerificationError, match="声明式归档报告包含"):
            verify_v1_archive.verify_archive(archive)

    _update_report_markdown_register(archive, source_path, original)
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_verifier_rejects_release_acceptance_with_blocked_gate(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010217Z")
    metadata_path = archive / "baseline/reports/versions/v1.0.0/version-summary.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["decision_scope"] = "release"
    metadata["report_kind"] = "version_summary"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/reports/versions/v1.0.0/version-summary.json")
    register_path = archive / "index/report-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    summary = next(item for item in register["entries"] if item["report_id"] == "RPT-FIXTURE-V1-0-0-SUMMARY")
    summary["declared"] = metadata
    summary["metadata"]["archive_sha256"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/reports/versions/v1.0.0/version-summary.json")
    _refresh_manifest_member(archive, "index/report-register.json")

    with pytest.raises(verify_v1_archive.VerificationError, match="发布接受报告不得保留阻塞门禁"):
        verify_v1_archive.verify_archive(archive)

    repository = _write_fixture_repository(
        runtime_root / "repository",
        runtime_content={
            "listener_pid_after_second_launch": 12345,
            "original_listener_pid": "12345",
            "pid_worker": None,
            "worker_process_id": {"nested": "not-retained"},
            "launch": {
                "output": ["traceback body"],
                "stdout": "stdout body",
                "stderr": "stderr body",
                "response": "response body",
                "traceback": "traceback body",
                "stacktrace": "stacktrace body",
                "launch_output": "launch output body",
                "output_details": "output details body",
                "summary": "retained",
            },
        },
    )

    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010205Z")

    evidence = json.loads(
        (archive / FIXTURE_ARCHIVE_PATH).read_text(encoding="utf-8")
    )
    assert evidence["listener_pid_after_second_launch"] == "<redacted-process-id>"
    assert evidence["original_listener_pid"] == "<redacted-process-id>"
    assert evidence["pid_worker"] == "<redacted-process-id>"
    assert evidence["worker_process_id"] == "<redacted-process-id>"
    assert evidence["launch"] == {"summary": "retained"}
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_verifier_rejects_runtime_policy_and_unknown_defect_reference(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010206Z")
    evidence_path = archive / FIXTURE_ARCHIVE_PATH

    for name, value, expected_error in (
        ("listener_pid_after_second_launch", 12345, "未脱敏 PID"),
        ("original_listener_pid", "12345", "未脱敏 PID"),
        ("launch_output", "traceback body", "被禁止的运行输出"),
        ("output_details", "traceback body", "被禁止的运行输出"),
    ):
        evidence_path.write_text(json.dumps({name: value}), encoding="utf-8")
        _refresh_manifest_member(archive, FIXTURE_ARCHIVE_PATH)
        with pytest.raises(verify_v1_archive.VerificationError, match=expected_error):
            verify_v1_archive.verify_archive(archive)

    evidence_path.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    _refresh_manifest_member(archive, FIXTURE_ARCHIVE_PATH)
    register_path = archive / "index/evidence-register.json"
    register = json.loads(register_path.read_text(encoding="utf-8"))
    t1_register = next(
        item for item in register["entries"] if item["archive_path"] == FIXTURE_ARCHIVE_PATH
    )
    t1_register["defects"] = ["DEF-UNKNOWN-999"]
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "index/evidence-register.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="缺失的缺陷账本条目"):
        verify_v1_archive.verify_archive(archive)

    t1_register["defects"] = []
    t1_register["source_run_id"] = "20260101T000000Z"
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "index/evidence-register.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="运行标识"):
        verify_v1_archive.verify_archive(archive)

    t1_register["source_run_id"] = FIXTURE_SOURCE_RUN_ID
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "index/evidence-register.json")
    inventory_path = archive / "provenance/source-inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    next(item for item in inventory["entries"] if item["archive_path"] == FIXTURE_ARCHIVE_PATH)["source_run_id"] = "20260101T000000Z"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    _refresh_manifest_member(archive, "provenance/source-inventory.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="运行标识"):
        verify_v1_archive.verify_archive(archive)

    inventory_entry = next(
        item for item in inventory["entries"] if item["archive_path"] == FIXTURE_ARCHIVE_PATH
    )
    inventory_entry["source_run_id"] = FIXTURE_SOURCE_RUN_ID
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    _refresh_manifest_member(archive, "provenance/source-inventory.json")
    allowlist_path = archive / "baseline/docs/v1-archive/evidence-allowlist.json"
    allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
    allowlist["entries"][0]["source_run_id"] = "20260101T000000Z"
    allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/docs/v1-archive/evidence-allowlist.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="白名单运行标识"):
        verify_v1_archive.verify_archive(archive)

    allowlist["entries"][0]["source_run_id"] = FIXTURE_SOURCE_RUN_ID
    allowlist["entries"][0]["purpose"] = "forged purpose"
    allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/docs/v1-archive/evidence-allowlist.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="登记用途"):
        verify_v1_archive.verify_archive(archive)

    allowlist["entries"][0]["purpose"] = "synthetic fixture"
    allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")
    _refresh_manifest_member(archive, "baseline/docs/v1-archive/evidence-allowlist.json")
    register["entries"].append(dict(t1_register))
    register_path.write_text(json.dumps(register), encoding="utf-8")
    _refresh_manifest_member(archive, "index/evidence-register.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="重复归档路径"):
        verify_v1_archive.verify_archive(archive)


def test_verifier_rejects_structured_sensitive_json_key(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010211Z")
    evidence_path = archive / FIXTURE_ARCHIVE_PATH
    evidence_path.write_text('{"to\\u006ben":"not-retained"}', encoding="utf-8")
    _refresh_manifest_member(archive, FIXTURE_ARCHIVE_PATH)

    with pytest.raises(verify_v1_archive.VerificationError, match="敏感内容"):
        verify_v1_archive.verify_archive(archive)


def test_builder_preserves_safe_t0_test_source(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    test_source = (
        "def test_userinfo_fixture():\n"
        "    value = \"https://alice\" + \":synthetic-secret@example.test/path\"\n"
        "    assert value\n"
    )
    _write(repository / "tests/unit/test_safe_fixture.py", test_source)

    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010208Z")

    archived_source = (archive / "baseline/tests/unit/test_safe_fixture.py").read_text(
        encoding="utf-8"
    )
    assert archived_source == test_source
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_builder_records_unaccepted_predecessor(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    predecessor_hash = hashlib.sha256(b"predecessor\n").hexdigest()
    _write(
        repository / archive_v1.PREDECESSOR_REGISTER_PATH,
        json.dumps({
            "schema_version": 1,
            "entries": [{
                "run_id": "20260730T110828Z",
                "manifest_sha256": predecessor_hash,
                "reason": "independent review rejection",
                "status": "not_accepted_under_policy",
            }],
        }),
    )

    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010207Z")

    predecessor = json.loads(
        (archive / "provenance/predecessor.json").read_text(encoding="utf-8")
    )
    assert predecessor["predecessor_status"] == "not_accepted_under_policy"
    assert predecessor["predecessor_run_id"] == "20260730T110828Z"
    assert predecessor["predecessor_manifest_sha256"] == predecessor_hash
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"


def test_builder_records_most_recent_unaccepted_predecessor(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    entries = []
    for predecessor_run_id in (
        "20260730T110828Z",
        "20260730T121500Z-archive-remediated",
        "20260730T130000Z-review-rejected",
    ):
        entries.append({
            "run_id": predecessor_run_id,
            "manifest_sha256": hashlib.sha256(predecessor_run_id.encode("ascii")).hexdigest(),
            "reason": "independent review rejection",
            "status": "not_accepted_under_policy",
        })
    _write(
        repository / archive_v1.PREDECESSOR_REGISTER_PATH,
        json.dumps({"schema_version": 1, "entries": entries}),
    )

    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010209Z")

    predecessor = json.loads(
        (archive / "provenance/predecessor.json").read_text(encoding="utf-8")
    )
    assert predecessor["predecessor_run_id"] == "20260730T130000Z-review-rejected"
    assert verify_v1_archive.verify_archive(archive)["status"] == "verified"

    predecessor["predecessor_run_id"] = "20260730T110828Z"
    predecessor["predecessor_manifest_sha256"] = entries[0]["manifest_sha256"]
    (archive / "provenance/predecessor.json").write_text(
        json.dumps(predecessor),
        encoding="utf-8",
    )
    _refresh_manifest_member(archive, "provenance/predecessor.json")
    with pytest.raises(verify_v1_archive.VerificationError, match="最新拒绝快照"):
        verify_v1_archive.verify_archive(archive)


def test_builder_rejects_unsafe_run_id_and_output_root(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")

    with pytest.raises(archive_v1.ArchiveError, match="run-id"):
        _build_fixture_archive(repository, "../../escape")
    with pytest.raises(archive_v1.ArchiveError, match="archives"):
        archive_v1.build_archive(
            repository,
            repository / "data",
            "20260730T010203Z",
            verifier_script=SCRIPTS / "verify_v1_archive.py",
        )


def test_builder_preserves_existing_targets_on_conflict(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    run_id = "20260730T010212Z"
    archive_root = repository / "archives" / f"V1-current-audit-{run_id}"
    archive_zip = repository / "archives" / f"V1-current-audit-{run_id}.zip"
    _write(archive_root / "preserved.txt", "directory owner\n")
    archive_zip.parent.mkdir(parents=True, exist_ok=True)
    archive_zip.write_bytes(b"zip owner\n")

    with pytest.raises(archive_v1.ArchiveError, match="已存在"):
        _build_fixture_archive(repository, run_id)

    assert (archive_root / "preserved.txt").read_text(encoding="utf-8") == "directory owner\n"
    assert archive_zip.read_bytes() == b"zip owner\n"


def test_builder_rejects_active_run_lock(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    run_id = "20260730T010213Z"
    lock_path = repository / "archives" / f".V1-current-audit-{run_id}.lock"
    _write(lock_path, "active\n")

    with pytest.raises(archive_v1.ArchiveError, match="已在进行"):
        _build_fixture_archive(repository, run_id)

    assert lock_path.read_text(encoding="utf-8") == "active\n"


def test_builder_rejects_missing_sensitive_and_prohibited_runtime_evidence(runtime_root: Path) -> None:
    missing_repository = _write_fixture_repository(runtime_root / "missing")
    (missing_repository / FIXTURE_RUNTIME_SOURCE).unlink()
    with pytest.raises(archive_v1.ArchiveError, match="不存在"):
        _build_fixture_archive(missing_repository, "20260730T010203Z")

    sensitive_repository = _write_fixture_repository(
        runtime_root / "sensitive",
        runtime_content={"url": "https://alice" + ":synthetic-secret@example.test/path"},
    )
    with pytest.raises(archive_v1.ArchiveError, match="敏感规则"):
        _build_fixture_archive(sensitive_repository, "20260730T010204Z")

    escaped_key_repository = _write_fixture_repository(runtime_root / "escaped-key")
    _write(
        escaped_key_repository / FIXTURE_RUNTIME_SOURCE,
        '{"to\\u006ben":"not-retained"}',
    )
    with pytest.raises(archive_v1.ArchiveError, match="敏感规则"):
        _build_fixture_archive(escaped_key_repository, "20260730T010210Z")

    for label, suffix in (("database", ".db"), ("archive", ".zip"), ("artifact", ".json")):
        repository = _write_fixture_repository(runtime_root / label)
        source = f"tests/runtime/fixture-{FIXTURE_SOURCE_RUN_ID}/{'artifacts/result' if label == 'artifact' else 'result'}{suffix}"
        _write(repository / source, "{}")
        allowlist_path = repository / "docs/v1-archive/evidence-allowlist.json"
        allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))
        allowlist["entries"][0]["source"] = source
        allowlist_path.write_text(json.dumps(allowlist), encoding="utf-8")
        with pytest.raises(archive_v1.ArchiveError):
            _build_fixture_archive(repository, f"20260730T01020{len(label)}Z")


def test_verifier_detects_modified_extra_missing_and_forged_manifest_members(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, _ = _build_mutable_fixture_archive(repository, "20260730T010203Z")

    target = archive / "baseline/backend/app/sample.py"
    original_content = target.read_bytes()
    target.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(verify_v1_archive.VerificationError, match="哈希或大小"):
        verify_v1_archive.verify_archive(archive)

    target.write_bytes(original_content)
    _write(archive / "unexpected.txt", "unexpected\n")
    with pytest.raises(verify_v1_archive.VerificationError, match="成员集合"):
        verify_v1_archive.verify_archive(archive)

    (archive / "unexpected.txt").unlink()
    forged_path = archive / "baseline/unapproved.py"
    _write(forged_path, "unexpected\n")
    forged_content = forged_path.read_bytes()
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"].append({
        "path": "baseline/unapproved.py",
        "byte_size": len(forged_content),
        "sha256": hashlib.sha256(forged_content).hexdigest(),
        "tier": "T0",
    })
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (archive / "manifest.sha256").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "  manifest.json\n",
        encoding="ascii",
    )
    with pytest.raises(verify_v1_archive.VerificationError, match="未允许文件"):
        verify_v1_archive.verify_archive(archive)


def test_verifier_rejects_bad_manifest_status_and_zip_traversal(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    archive, archive_zip = _build_mutable_fixture_archive(repository, "20260730T010203Z")

    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["release_readiness"] = "ready"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(verify_v1_archive.VerificationError, match="不得声明"):
        verify_v1_archive.verify_archive(archive)

    traversal_zip = runtime_root / "traversal.zip"
    with ZipFile(archive_zip) as source, ZipFile(traversal_zip, "w", compression=ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
        target.writestr("../escape.txt", b"blocked")
    with pytest.raises(verify_v1_archive.VerificationError, match="不安全路径"):
        verify_v1_archive.verify_archive(traversal_zip)


def test_verifier_rejects_duplicate_zip_member_and_bad_manifest_hash(runtime_root: Path) -> None:
    repository = _write_fixture_repository(runtime_root / "repository")
    _, archive_zip = _build_fixture_archive(repository, "20260730T010203Z")

    duplicate_zip = runtime_root / "duplicate.zip"
    with ZipFile(archive_zip) as source, ZipFile(duplicate_zip, "w", compression=ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, source.read(info))
        target.writestr("manifest.json", b"{}")
    with pytest.raises(verify_v1_archive.VerificationError, match="重复成员"):
        verify_v1_archive.verify_archive(duplicate_zip)

    bad_hash_zip = runtime_root / "bad-hash.zip"
    with ZipFile(archive_zip) as source, ZipFile(bad_hash_zip, "w", compression=ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, b"bad hash\n" if info.filename == "manifest.sha256" else source.read(info))
    with pytest.raises(verify_v1_archive.VerificationError, match="自身哈希"):
        verify_v1_archive.verify_archive(bad_hash_zip)
