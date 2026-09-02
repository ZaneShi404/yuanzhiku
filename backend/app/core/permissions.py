"""跨平台私密目录/文件 ACL 原语（加固计划 Task 1）。

凭据文件、按平台 Cookie 文件及其父目录只允许当前运行账户、SYSTEM 与
Administrators 访问：POSIX 用 chmod（0700/0600）；Windows 用 icacls
（shell=False）关闭继承并仅授予三者 FullControl。命令失败抛
SecretPermissionError；错误消息不含凭据或 Cookie 内容。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class SecretPermissionError(RuntimeError):
    """无法设置私密文件/目录权限；消息不含凭据或 Cookie 内容。"""


def _windows_identity() -> str:
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = os.environ.get("USERNAME", "").strip()
    if domain and user:
        return f"{domain}\\{user}"
    if user:
        return user
    try:
        login = os.getlogin().strip()
    except OSError:
        login = ""
    if login:
        return login
    raise SecretPermissionError("无法确定当前运行账户")


def _run_icacls(arguments: list[str]) -> None:
    # SYSTEM/Administrators 以 well-known SID 表达，不受系统显示语言影响；
    # 当前账户经 USERDOMAIN\USERNAME 解析。icacls 输出为本地编码（中文
    # Windows 为 GBK），只取 returncode、不解码输出，避免 reader 线程崩溃。
    try:
        result = subprocess.run(
            ["icacls", *arguments],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
    except OSError as exc:
        raise SecretPermissionError("无法设置私密文件权限") from exc
    if result.returncode != 0:
        raise SecretPermissionError("无法设置私密文件权限")


def _grant_arguments(target: str) -> list[str]:
    identity = _windows_identity()
    return [
        target,
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-18:F",
        "*S-1-5-32-544:F",
        f"{identity}:F",
    ]


def secure_private_directory(path: Path) -> None:
    """确保目录存在并收紧为仅当前账户/SYSTEM/Administrators 可访问。"""
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)
        return
    _run_icacls(_grant_arguments(str(path)))


def secure_private_file(path: Path) -> None:
    """收紧既有文件的权限；文件不存在视为调用方错误。"""
    if not path.is_file():
        raise SecretPermissionError("私密文件不存在")
    if os.name == "posix":
        os.chmod(path, 0o600)
        return
    _run_icacls(_grant_arguments(str(path)))
