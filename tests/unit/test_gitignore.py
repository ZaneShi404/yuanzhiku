from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _ignored_path(path: str) -> bool:
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=ROOT,
        check=False,
    )
    return completed.returncode == 0


def test_gitignore_excludes_local_artifacts_without_hiding_archive_policy_or_reports() -> None:
    assert _ignored_path(".zcode/plans/local-plan.md")
    assert _ignored_path("archives/local-audit.zip")
    assert _ignored_path("frontend/dist/assets/generated.js")
    assert _ignored_path("tests/runtime/local-run/result.json")
    assert not _ignored_path("archives/README.md")
    assert not _ignored_path("reports/development/local-video.md")
