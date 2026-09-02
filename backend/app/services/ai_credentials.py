"""媒体 AI 凭据的文件存取（state/ai/credentials.json）。

与下载 Cookie 同一纪律：凭据只落在数据根内的凭据文件，绝不进数据库、
备份、导出、操作日志或任何 API 响应；仅本模块读写该文件。

缺失与损坏语义不同（加固计划 Task 1）：缺失按首次配置处理返回空字典；
JSON/类型损坏抛 CredentialStoreCorrupt，调用方返回 503 且绝不覆盖原文件。
写入对 staging 与替换后目标都设置私密权限（app.core.permissions）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.core.permissions import secure_private_file

# 前两项为 AI 双分组；后三项为 v1.5 视频直送/中转（REQ-055，决策 17/22），
# 与分组同纪律：仅文件、掩码回显、原子写入、绝不进数据库/备份/导出/日志。
AI_CREDENTIAL_GROUPS = ("transcribe", "understand", "video_qwen", "video_mimo", "video_relay", "video_cos_secret_id", "video_cos_secret_key")


class CredentialStoreCorrupt(RuntimeError):
    """凭据文件存在但内容损坏（JSON/类型）；原文件保持不变。"""


def read_ai_credentials(path: Path) -> dict[str, str]:
    """读取凭据文件；缺失返回空字典，损坏抛 CredentialStoreCorrupt。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CredentialStoreCorrupt("凭据存储文件损坏") from exc
    if not isinstance(payload, dict):
        raise CredentialStoreCorrupt("凭据存储文件损坏")
    result: dict[str, str] = {}
    for group in AI_CREDENTIAL_GROUPS:
        value = payload.get(group)
        if isinstance(value, str) and value:
            result[group] = value
    return result


def write_ai_credentials(path: Path, values: dict[str, str]) -> None:
    """原子写入凭据文件（仅保留非空键），staging 与目标均设私密权限。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {group: values[group] for group in AI_CREDENTIAL_GROUPS if values.get(group)}
    staging = path.parent / (path.name + ".part")
    try:
        if os.name == "posix":
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(cleaned, target, ensure_ascii=False)
        else:
            staging.write_text(json.dumps(cleaned, ensure_ascii=False), encoding="utf-8")
        secure_private_file(staging)
        os.replace(staging, path)
        secure_private_file(path)
    finally:
        staging.unlink(missing_ok=True)
