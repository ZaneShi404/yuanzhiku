"""Task 6（加固计划）：CAS 命中时验证并安全修复损坏目标。

- 命中已存在的 artifact 地址时校验大小与 SHA-256：一致才按普通去重处理；
- 不一致时把旧目标隔离到随机 quarantine、用刚验证过的 staging 原子替换、
  再次验证后清理 quarantine（was_new 仍为 False：数据库行早已存在）；
- 修复失败时尽力恢复旧目标并抛 ArtifactIntegrityError，不留半修复状态。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from app.adapters.storage import ArtifactIntegrityError, ArtifactStore
from app.core.config import data_paths


CONTENT_A = b"artifact-A-payload" * 1000


def sha_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture()
def store(tmp_path) -> ArtifactStore:
    return ArtifactStore(data_paths(tmp_path))


def test_normal_dedupe_unchanged(store: ArtifactStore) -> None:
    first = store.store_stream(__import__("io").BytesIO(CONTENT_A))
    assert first.was_new is True
    second = store.store_stream(__import__("io").BytesIO(CONTENT_A))
    assert second.was_new is False
    assert second.sha256 == first.sha256
    assert store.artifact_path(first.sha256).read_bytes() == CONTENT_A
    assert not list(store.paths.artifacts.rglob("*quarantine*"))


def test_corrupt_target_same_size_repaired(store: ArtifactStore) -> None:
    stored = store.store_stream(__import__("io").BytesIO(CONTENT_A))
    target = store.artifact_path(stored.sha256)
    corrupt = b"X" * len(CONTENT_A)  # 同尺寸不同内容：仅大小校验抓不到
    target.write_bytes(corrupt)

    result = store.store_stream(__import__("io").BytesIO(CONTENT_A))

    assert result.sha256 == stored.sha256
    assert result.was_new is False
    assert target.read_bytes() == CONTENT_A
    assert not list(store.paths.artifacts.rglob("*quarantine*"))


def test_corrupt_target_truncated_repaired(store: ArtifactStore) -> None:
    stored = store.store_stream(__import__("io").BytesIO(CONTENT_A))
    target = store.artifact_path(stored.sha256)
    target.write_bytes(CONTENT_A[:100])

    result = store.store_stream(__import__("io").BytesIO(CONTENT_A))
    assert target.read_bytes() == CONTENT_A
    assert result.sha256 == stored.sha256


def test_repair_failure_restores_old_target(store: ArtifactStore, monkeypatch: pytest.MonkeyPatch) -> None:
    stored = store.store_stream(__import__("io").BytesIO(CONTENT_A))
    target = store.artifact_path(stored.sha256)
    corrupt = b"Z" * len(CONTENT_A)
    target.write_bytes(corrupt)

    real_replace = __import__("os").replace

    def failing_replace(src, dst, *args, **kwargs):
        # 仅在“已验证 staging → 目标”这一步失败（staging 文件名以 .part 结尾）。
        if Path(str(dst)) == target and Path(str(src)).name.endswith(".part"):
            raise OSError("injected replace failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr("app.adapters.storage.os.replace", failing_replace)

    with pytest.raises(ArtifactIntegrityError):
        store.store_stream(__import__("io").BytesIO(CONTENT_A))

    assert target.read_bytes() == corrupt, "失败路径必须恢复旧目标"
    assert not list(store.paths.artifacts.rglob("*.part"))


def test_concurrent_identical_imports(store: ArtifactStore) -> None:
    results: list = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            results.append(store.store_stream(__import__("io").BytesIO(CONTENT_A)))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    sha = sha_of(CONTENT_A)
    assert all(result.sha256 == sha for result in results)
    assert store.artifact_path(sha).read_bytes() == CONTENT_A
    assert sum(1 for result in results if result.was_new) == 1
