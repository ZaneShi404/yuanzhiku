"""Task 13（加固计划）：reimport 物理修复与日期轮转完整性抽样。

- reimport 在逻辑冲突预检通过后，遍历归档声明的**全部** artifact 记录：
  目标文件缺失则从归档复制；损坏则走 Task 6 的 CAS 修复路径；报告新增
  repaired_artifacts 计数；
- 逻辑冲突时目标文件系统完全不变（不修复、不删除）；
- verify_artifacts 非全量抽样按 sha256(f"{utc_date}:{sha}") 排序取前 N：
  同一天可复现，跨日期轮转。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "root", acquire_lock=False)
    with TestClient(app) as test_client:
        yield test_client


def _import_and_export(client: TestClient) -> tuple[dict, str]:
    response = client.post(
        "/api/v1/imports/paste",
        json={"title": "修复来源", "text": "# 修复\n\n用于 reimport 物理修复验证的合成正文。",
              "rights": "owned", "tags": ["修复"]},
    )
    assert response.status_code == 201
    sha256 = response.json()["artifact"]["sha256"]
    run = client.post("/api/v1/jobs/run-once").json()
    while isinstance(run.get("job"), dict) and run["job"].get("kind") == "backup":
        run = client.post("/api/v1/jobs/run-once").json()
    export = client.post("/api/v1/exports", json={"confirmed": True}).json()
    assert export.get("archive_path"), export
    return {"sha256": sha256}, export["archive_path"]


def _artifact_path(app, sha256: str) -> Path:
    return app.state.services.artifacts.artifact_path(sha256)


def test_reimport_repairs_missing_target_file(client: TestClient) -> None:
    app = client.app
    record, archive_path = _import_and_export(client)
    target = _artifact_path(app, record["sha256"])
    target.unlink()
    assert not target.exists()

    result = app.state.services.transfers.reimport(archive_path)

    assert result["imported"] is True
    assert result["report"]["repaired_artifacts"] >= 1
    assert target.is_file()
    assert app.state.services.artifacts.verify(record["sha256"])


def test_reimport_repairs_corrupted_target_file(client: TestClient) -> None:
    app = client.app
    record, archive_path = _import_and_export(client)
    target = _artifact_path(app, record["sha256"])
    original_size = target.stat().st_size
    target.write_bytes(b"X" * original_size)  # 同尺寸损坏：仅大小校验抓不到

    result = app.state.services.transfers.reimport(archive_path)

    assert result["report"]["repaired_artifacts"] >= 1
    assert app.state.services.artifacts.verify(record["sha256"])


def test_logical_conflict_leaves_filesystem_unchanged(client: TestClient) -> None:
    app = client.app
    record, archive_path = _import_and_export(client)
    source_id = app.state.services.repository.list_sources()[0]["id"]
    client.put(f"/api/v1/sources/{source_id}/metadata", json={"title": "改动后的标题"})
    target = _artifact_path(app, record["sha256"])
    target.unlink()

    with pytest.raises(Exception) as excinfo:
        app.state.services.transfers.reimport(archive_path)
    assert "conflicts" in str(excinfo.value) or getattr(excinfo.value, "args", None)

    assert not target.exists(), "冲突拒绝时不得发生任何文件修复或删除"


def test_verify_artifacts_rotates_selection_by_date(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app = client.app
    services = app.state.services
    for index in range(8):
        response = client.post(
            "/api/v1/imports/paste",
            json={"title": f"抽样 {index}", "text": f"抽样正文 {index} " + "字" * (index + 1), "rights": "owned"},
        )
        assert response.status_code == 201

    # 非全量抽样：数量受 sample_size 约束且可执行。
    result = services.transfers.verify_artifacts(False, 3)
    assert result["checked"] == 3 and result["valid"]

    # 纯函数：同日可复现、跨日轮转、与独立公式一致。
    from app.services.transfers import select_verification_sample

    hashes = [row["sha256"] for row in services.repository.rows_for_export()["artifacts"]]
    day_one = select_verification_sample(hashes, 3, "2030-01-01")
    day_one_again = select_verification_sample(hashes, 3, "2030-01-01")
    day_two = select_verification_sample(hashes, 3, "2030-01-02")
    assert day_one == day_one_again, "同一天抽样必须可复现"
    assert day_one != day_two, "不同日期的抽样顺序必须变化"
    expected = sorted(hashes, key=lambda sha: hashlib.sha256(f"2030-01-01:{sha}".encode("utf-8")).digest())[:3]
    assert day_one == expected
    full = select_verification_sample(hashes, 10_000, "2030-01-01")
    assert sorted(full) == sorted(hashes)
