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
    # 测试缓存与代理会话目录显式忽略（不依赖 pytest 自建内部 .gitignore）
    assert _ignored_path(".pytest_cache/unit/cache.json")
    assert _ignored_path(".superpowers/plans/local-plan.md")
    # Cookie 文件绝不进版本库的显式回归锚点（data/ 根已整体忽略）
    assert _ignored_path("data/state/download/cookies.txt")
    assert not _ignored_path("archives/README.md")
    assert not _ignored_path("reports/development/local-video.md")
