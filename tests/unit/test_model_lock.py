"""REQ-013：模型锁文件逐条强制 版本/来源/许可证/哈希 等必需字段。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.adapters.parsers import LocalDocumentParser


def _write_lock(lockfile: Path, models: list[dict]) -> None:
    lockfile.write_text(json.dumps({"schema_version": 1, "policy": "test", "models": models}, ensure_ascii=False), encoding="utf-8")


def _complete_entry(digest: str) -> dict:
    return {
        "name": "docling-layout",
        "version": "1.0.0",
        "source_url": "https://example.invalid/models/docling-layout",
        "license": "MIT",
        "cache_path": "docling-layout.bin",
        "sha256": digest,
    }


def test_lock_entry_missing_required_fields_is_rejected(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    payload = b"synthetic-model-bytes"
    (models_dir / "docling-layout.bin").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    lockfile = tmp_path / "models.lock.json"
    parser = LocalDocumentParser(models_dir, lockfile)

    entry = _complete_entry(digest)
    for field in ("name", "version", "source_url", "license", "cache_path", "sha256"):
        incomplete = {key: value for key, value in entry.items() if key != field}
        _write_lock(lockfile, [incomplete])
        ready, reason, _ = parser._model_status()
        assert not ready, field
        assert "必需字段" in reason, field


def test_lock_entry_complete_and_hash_verified_is_ready(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    payload = b"synthetic-model-bytes"
    (models_dir / "docling-layout.bin").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    lockfile = tmp_path / "models.lock.json"
    _write_lock(lockfile, [_complete_entry(digest)])

    ready, reason, _ = LocalDocumentParser(models_dir, lockfile)._model_status()
    assert ready, reason

    _write_lock(lockfile, [_complete_entry("0" * 64)])
    ready, reason, _ = LocalDocumentParser(models_dir, lockfile)._model_status()
    assert not ready
    assert "哈希不匹配" in reason
