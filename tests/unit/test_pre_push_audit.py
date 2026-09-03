"""Task 15B（加固计划）：发布前 Git index/历史检查脚本。

- 阻止 data/、tests/runtime/、archives/*（README 例外）、.env、Cookie、
  token 文件进入 index；
- 检测暂存新增行中的私钥/Bearer/API key/数据库 URL 模式，报告只含
  文件与行号，匹配值一律脱敏；
- 未跟踪的 docs/user-guide/ 文件列为用户决策项；
- 不重写任何 Git 历史。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pre-push-audit.ps1"


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "audit@example.com")
    _git(repo, "config", "user.name", "audit")
    (repo / "README.md").write_text("clean\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    return repo


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), "-RepoPath", str(repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_clean_repo_passes(repo: Path) -> None:
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("blocked", [
    "data/state/knowledge.db",
    "tests/runtime/run-1/log.txt",
    "archives/V1-current-audit-x.zip",
    ".env",
    "cookies/bilibili.txt",
])
def test_blocked_paths_rejected(repo: Path, blocked: str) -> None:
    target = repo / blocked
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("内容\n", encoding="utf-8")
    _git(repo, "add", blocked)

    result = _run(repo)

    assert result.returncode == 1, result.stdout
    assert "blocked-path" in result.stdout
    # README 是 archives 内唯一允许入库的文件。
    _git(repo, "reset", "-q", blocked)


def test_secret_pattern_reported_with_value_redacted(repo: Path) -> None:
    secret = "sk-live-9f3a7c1e5b2d48f6a0e1c2b3d4e5f6a7"
    (repo / "config.json").write_text(
        '{"api_key": "' + secret + '", "db": "postgres://admin:hunter2@db.example.com/x"}\n',
        encoding="utf-8",
    )
    _git(repo, "add", "config.json")

    result = _run(repo)

    assert result.returncode == 1
    assert "secret-pattern" in result.stdout
    assert "config.json" in result.stdout
    assert secret not in result.stdout, "匹配值必须脱敏"
    assert "hunter2" not in result.stdout, "匹配值必须脱敏"


def test_private_key_pattern_detected(repo: Path) -> None:
    (repo / "key.pem").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----\n", encoding="utf-8")
    _git(repo, "add", "key.pem")

    result = _run(repo)

    assert result.returncode == 1
    assert "private-key" in result.stdout


def test_untracked_user_guide_listed_as_decision(repo: Path) -> None:
    guide = repo / "docs" / "user-guide"
    guide.mkdir(parents=True)
    (guide / "index.html").write_text("<html></html>", encoding="utf-8")

    result = _run(repo)

    assert "docs/user-guide" in result.stdout
    assert "用户明确选择" in result.stdout
