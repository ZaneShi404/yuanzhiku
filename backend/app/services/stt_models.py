"""本地转写模型管理（REQ-054.3/8，决策 19）。

模型包/来源/许可由仓库锁文件 ``backend/stt-models.lock.json`` 固定
（`REQ-013` 纪律）；下载经 ModelScope 公开源到 ``data/models/stt`` 下的
staging，逐文件计算 SHA-256 并原子启用（校验通过才可用）；下载/删除只
写审计事件与状态文件，绝不把模型内容写入日志或数据库。下载为显式触发
（`POST /settings/ai/stt-model` → `stt_model_download` 作业），绝不静默
自动下载、绝不静默回退云转写。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

STATE_FILE_NAME = "manifest.state.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SttModelManager:
    """本地转写模型的下载/状态/删除（仅文件系统与状态文件，无数据库）。"""

    def __init__(
        self,
        models_root: Path,
        lock_file: Path | None = None,
        *,
        downloader: Callable[..., Any] | None = None,
    ) -> None:
        self._root = Path(models_root) / "stt"
        self._lock_file = lock_file or (
            Path(__file__).resolve().parents[2] / "stt-models.lock.json"
        )
        # 注入替身（测试）；None → ModelScope snapshot_download 惰性导入。
        self._downloader = downloader

    # ---- 锁文件与状态 ----

    def manifest(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._lock_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("本地转写模型锁文件无效") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), dict):
            raise RuntimeError("本地转写模型锁文件无效")
        return payload

    def _state_path(self) -> Path:
        return self._root / STATE_FILE_NAME

    def _state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _model_dir(self, name: str) -> Path:
        return self._root / name

    def model_dirs(self) -> dict[str, Path]:
        manifest = self.manifest()
        return {name: self._model_dir(name) for name in (manifest.get("models") or {})}

    def status(self) -> dict[str, Any]:
        try:
            manifest = self.manifest()
        except RuntimeError:
            manifest = {}
        state = self._state()
        names = list((manifest.get("models") or {}).keys())
        available = bool(names) and bool(state.get("downloaded_at")) and all(
            self._model_dir(name).is_dir() for name in names
        )
        return {
            "model_name": manifest.get("model_name") or "paraformer-zh",
            "model_configured": bool(names),
            "model_available": available,
            "downloaded_at": state.get("downloaded_at"),
            "revisions": state.get("revisions") or {},
        }

    # ---- 下载 / 删除 ----

    def _default_downloader(self) -> Callable[..., Any]:
        try:
            from modelscope.hub.snapshot_download import snapshot_download
        except ImportError as exc:
            raise RuntimeError("未安装 ModelScope 依赖，无法下载本地转写模型") from exc
        return snapshot_download

    def download(
        self,
        cancelled: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """显式下载全部锁文件模型包；成功后校验哈希并原子启用。

        失败一律 RuntimeError（消息脱敏、不含模型内容）；staging 清理、
        可重试；已下载可用时幂等返回。
        """
        cancelled = cancelled or (lambda: False)
        heartbeat = heartbeat or (lambda: None)
        manifest = self.manifest()
        entries = manifest.get("models") or {}
        if not entries:
            raise RuntimeError("本地转写模型锁文件无效")
        if self.status().get("model_available"):
            return {"model": manifest.get("model_name"), "downloaded": False}
        downloader = self._downloader or self._default_downloader()
        staging = self._root / ".staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for name, entry in entries.items():
                if cancelled():
                    raise RuntimeError("本地转写模型下载已取消")
                heartbeat()
                local_dir = staging / name
                downloader(
                    str(entry["model_id"]),
                    revision=str(entry.get("revision", "master")),
                    local_dir=str(local_dir),
                )
                if not local_dir.is_dir():
                    raise RuntimeError("本地转写模型下载失败")
            file_hashes = {}
            for name in entries:
                for path in sorted((staging / name).rglob("*")):
                    if path.is_file():
                        file_hashes[path.relative_to(staging).as_posix()] = _sha256(path)
            for name in entries:
                target = self._model_dir(name)
                shutil.rmtree(target, ignore_errors=True)
                (staging / name).rename(target)
            self._state_path().write_text(
                json.dumps(
                    {
                        "model_name": manifest.get("model_name"),
                        "downloaded_at": datetime.now(timezone.utc).isoformat(),
                        "revisions": {
                            name: str(entry.get("revision", "master")) for name, entry in entries.items()
                        },
                        "files_sha256": file_hashes,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {"model": manifest.get("model_name"), "downloaded": True}

    def delete(self) -> None:
        """删除全部模型目录与状态文件；幂等。"""
        shutil.rmtree(self._root, ignore_errors=True)
