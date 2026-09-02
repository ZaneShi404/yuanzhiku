"""Task 12（加固计划）：备份恢复的锚定、单句柄验证与失败清理。

- restore_backup 以备份目录记录的 manifest_sha256 锚定归档：ZIP 内
  manifest.json 的原始字节哈希必须与目录一致（替换攻击整批拒绝）；
- 验证与提取使用同一打开的 ZipFile 句柄（消除验证后重开的 TOCTOU）；
- SQLite 恢复先落到同卷 staging（target.restore-<id>.part），完成 DB
  initialize、外键检查与 artifact 全量校验后原子 rename；任何异常路径
  finally 清理 staging，绝不留半目标。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def _populate(client: TestClient, marker: str) -> None:
    response = client.post(
        "/api/v1/imports/paste",
        json={"title": f"恢复锚定 {marker}", "text": f"# 锚定\n\n{marker} 的合成正文。", "rights": "owned"},
    )
    assert response.status_code == 201
    run = client.post("/api/v1/jobs/run-once").json()
    while isinstance(run.get("job"), dict) and run["job"].get("kind") == "backup":
        run = client.post("/api/v1/jobs/run-once").json()


def _make_backup(client: TestClient, marker: str) -> dict:
    _populate(client, marker)
    backup = client.post("/api/v1/backups", json=None).json()
    assert backup["state"] == "succeeded"
    return backup


def _restore_target(tmp_path: Path, name: str) -> str:
    return str(tmp_path / name)


def test_verify_archive_rejects_wrong_manifest_anchor(tmp_path: Path) -> None:
    app = create_app(tmp_path / "root", acquire_lock=False)
    with TestClient(app) as client:
        backup = _make_backup(client, "锚定A")
        services = app.state.services
        archive_path = services.paths.backups / backup["archive_name"]
        record_sha = backup["manifest_sha256"]

        assert services.transfers.verify_archive(archive_path, expected_manifest_sha256=record_sha)["valid"]
        tampered = services.transfers.verify_archive(archive_path, expected_manifest_sha256="0" * 64)
        assert not tampered["valid"]
        assert any("manifest" in error for error in tampered["errors"])


def test_restore_rejects_swapped_archive(tmp_path: Path) -> None:
    app = create_app(tmp_path / "root", acquire_lock=False)
    with TestClient(app) as client:
        # 同日第二次备份会按“保留 30 个日期项”修剪第一条记录，因此直接保存
        # 第一份归档字节，稍后覆盖到仍然持有目录记录的最新归档上。
        first = _make_backup(client, "第一批")
        services = app.state.services
        original_bytes = (services.paths.backups / first["archive_name"]).read_bytes()

        latest = _make_backup(client, "第二批")
        latest_path = services.paths.backups / latest["archive_name"]
        assert latest_path.name != first["archive_name"]
        latest_path.write_bytes(original_bytes)

        target = _restore_target(tmp_path, "swapped-target")
        with pytest.raises(ValueError):
            services.transfers.restore_backup(latest["id"], target)
        assert not Path(target).exists(), "被替换的归档不得还原出目标"


def test_sqlite_restore_uses_staging_and_leaves_no_residue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(tmp_path / "root", acquire_lock=False)
    with TestClient(app) as client:
        backup = _make_backup(client, "staged")
        services = app.state.services

        # 在提取中途注入失败：目标与 staging 残留都不得出现。
        original_extract = services.transfers._extract_artifacts

        def failing_extract(archive, manifest, target):
            (target.root / "staging-marker.txt").write_text("partial", encoding="utf-8")
            raise OSError("injected extraction failure")

        monkeypatch.setattr(services.transfers, "_extract_artifacts", failing_extract)
        with pytest.raises(Exception):
            services.transfers.restore_backup(backup["id"], _restore_target(tmp_path, "partial-target"))

        root = tmp_path / "root"
        assert not (root / "partial-target").exists()
        assert not list(root.glob("*.restore-*.part")), "异常路径必须清理 staging"
        monkeypatch.setattr(services.transfers, "_extract_artifacts", original_extract)

        target = _restore_target(tmp_path, "ok-target")
        result = services.transfers.restore_backup(backup["id"], target)
        assert Path(result["target_data_root"]).is_dir()
        assert (Path(target) / "state" / "knowledge.db").is_file()
        assert not list(root.glob("*.restore-*.part"))


def test_restore_rejected_when_target_not_empty(tmp_path: Path) -> None:
    app = create_app(tmp_path / "root", acquire_lock=False)
    with TestClient(app) as client:
        backup = _make_backup(client, "目标检查")
        services = app.state.services
        target = Path(_restore_target(tmp_path, "occupied"))
        target.mkdir(parents=True)
        (target / "existing.txt").write_text("占用", encoding="utf-8")

        with pytest.raises(ValueError):
            services.transfers.restore_backup(backup["id"], str(target))
        assert (target / "existing.txt").is_file(), "拒绝还原时不得改动既有目标"


def test_existing_empty_target_dir_is_replaced(tmp_path: Path) -> None:
    app = create_app(tmp_path / "root", acquire_lock=False)
    with TestClient(app) as client:
        backup = _make_backup(client, "空目录")
        services = app.state.services
        target = Path(_restore_target(tmp_path, "empty-dir"))
        target.mkdir(parents=True)

        result = services.transfers.restore_backup(backup["id"], str(target))
        assert Path(result["target_data_root"]).resolve() == target.resolve()
        assert (target / "state" / "knowledge.db").is_file()
