"""Task 1（加固计划）：私密文件权限原语与凭据损坏语义。

- 凭据文件缺失 → 按首次配置处理；JSON/类型损坏 → CredentialStoreCorrupt，
  PUT /settings/ai 返回 503（code=credential_store_corrupt）且原文件字节不变。
- secure_private_directory/file：POSIX 0700/0600；Windows 仅 SYSTEM、
  Administrators 与当前账户持有 FullControl，继承已断开。
- DataPaths.create() 只加固 state/ai 与 state/download/cookies 两个私密目录。
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import data_paths
from app.core.permissions import (
    SecretPermissionError,
    secure_private_directory,
    secure_private_file,
)
from app.main import create_app
from app.services.ai_credentials import (
    CredentialStoreCorrupt,
    read_ai_credentials,
    write_ai_credentials,
)

CORRUPT_BYTES = b'{"transcribe": "key-1", "understand": '  # 截断的 JSON


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


# --- read/write 语义 -------------------------------------------------------


def test_missing_credentials_read_as_empty(tmp_path: Path) -> None:
    assert read_ai_credentials(tmp_path / "credentials.json") == {}


def test_corrupt_credentials_raise_and_preserve_bytes(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    _write_bytes(path, CORRUPT_BYTES)
    with pytest.raises(CredentialStoreCorrupt):
        read_ai_credentials(path)
    assert path.read_bytes() == CORRUPT_BYTES


def test_non_dict_credentials_raise(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    _write_bytes(path, b'["transcribe"]')
    with pytest.raises(CredentialStoreCorrupt):
        read_ai_credentials(path)


def test_write_roundtrip_and_staging_cleanup(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_ai_credentials(path, {"transcribe": "k-1", "ignored": "x"})
    assert read_ai_credentials(path) == {"transcribe": "k-1"}
    assert not list(tmp_path.glob("*.part"))


# --- 权限原语 --------------------------------------------------------------


def test_secure_private_file_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SecretPermissionError):
        secure_private_file(tmp_path / "absent.json")


@pytest.mark.skipif(os.name == "posix", reason="Windows ACL 检查仅限 Windows")
def test_windows_file_acl_restricted_to_admin_and_user(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    write_ai_credentials(path, {"transcribe": "k-1"})
    trustees = _sddl_trustees(path)
    forbidden = {"AU", "BU", "WD", "AN", "ED", "BG", "NU"}
    assert not (set(trustees) & forbidden), trustees
    assert all(
        trustee in {"SY", "BA"} or trustee.startswith("S-1-5-21-")
        for trustee in trustees
    ), trustees
    assert _sddl_is_protected(path)


@pytest.mark.skipif(os.name == "posix", reason="Windows ACL 检查仅限 Windows")
def test_windows_directory_acl_restricted(tmp_path: Path) -> None:
    directory = tmp_path / "state" / "ai"
    secure_private_directory(directory)
    trustees = _sddl_trustees(directory)
    forbidden = {"AU", "BU", "WD", "AN", "ED", "BG", "NU"}
    assert not (set(trustees) & forbidden), trustees
    assert _sddl_is_protected(directory)


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位检查")
def test_posix_file_and_directory_modes(tmp_path: Path) -> None:
    directory = tmp_path / "state" / "ai"
    secure_private_directory(directory)
    path = directory / "credentials.json"
    write_ai_credentials(path, {"transcribe": "k-1"})
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "posix", reason="Windows ACL 检查仅限 Windows")
def test_data_paths_create_hardens_private_dirs_only(tmp_path: Path) -> None:
    paths = data_paths(tmp_path)
    paths.create()
    assert _sddl_is_protected(paths.ai_state)
    assert _sddl_is_protected(paths.download_cookies)
    # 非私密目录不做 ACL 收紧（artifacts 保持默认继承）。
    assert not _sddl_is_protected(paths.artifacts)


@pytest.mark.skipif(os.name != "posix", reason="POSIX 权限位检查")
def test_data_paths_create_hardens_private_dirs_posix(tmp_path: Path) -> None:
    paths = data_paths(tmp_path)
    paths.create()
    assert stat.S_IMODE(paths.ai_state.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.download_cookies.stat().st_mode) == 0o700


# --- API 语义 --------------------------------------------------------------


def test_settings_ai_corrupt_store_returns_503_and_preserves_bytes(tmp_path: Path) -> None:
    paths = data_paths(tmp_path)
    _write_bytes(paths.ai_credentials_file, CORRUPT_BYTES)
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/settings/ai",
            json={"transcribe": {"provider": "openai_compatible", "api_key": "replacement"}},
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "credential_store_corrupt"
        # GET 同语义：损坏时明确 503，而不是伪装为未配置。
        assert client.get("/api/v1/settings/ai").status_code == 503
    assert paths.ai_credentials_file.read_bytes() == CORRUPT_BYTES


def test_settings_ai_missing_store_behaves_as_first_setup(tmp_path: Path) -> None:
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/settings/ai",
            json={"transcribe": {"provider": "openai_compatible", "api_key": "first-key"}},
        )
        assert response.status_code == 200
        assert response.json()["transcribe"]["has_key"] is True
    paths = data_paths(tmp_path)
    assert read_ai_credentials(paths.ai_credentials_file) == {"transcribe": "first-key"}


def test_cookie_upload_secures_file(tmp_path: Path) -> None:
    app = create_app(tmp_path, acquire_lock=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/settings/download-cookies/bilibili",
            files={"file": ("cookies.txt", b"# Netscape HTTP Cookie File\n", "text/plain")},
        )
        assert response.status_code == 204
    paths = data_paths(tmp_path)
    cookie_file = paths.download_cookie_file("bilibili")
    assert cookie_file.is_file()
    if os.name == "nt":
        assert _sddl_is_protected(cookie_file)
        assert not ({"AU", "BU", "WD"} & set(_sddl_trustees(cookie_file)))
    else:
        assert stat.S_IMODE(cookie_file.stat().st_mode) == 0o600


# --- Windows SDDL 解析辅助 --------------------------------------------------


def _sddl_dump(path: Path) -> str:
    dump = path.parent / f".{path.name}.acl-dump"
    # icacls 的 stdout 为本地编码（中文 Windows 为 GBK），只取 returncode、
    # 不解码输出；SDDL 内容全部来自 /save 写出的 UTF-16 文件。
    result = subprocess.run(
        ["icacls", str(path), "/save", str(dump), "/q"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    assert result.returncode == 0, f"icacls /save failed for {path.name}"
    try:
        return dump.read_text(encoding="utf-16")
    except UnicodeError:
        return dump.read_text(encoding="utf-8", errors="replace")
    finally:
        dump.unlink(missing_ok=True)


def _sddl_string(path: Path) -> str:
    lines = [line.strip() for line in _sddl_dump(path).splitlines() if line.strip()]
    # /save 输出：首行为相对路径，其余行拼接为 SDDL D-字符串。
    return "".join(lines[1:])


def _sddl_trustees(path: Path) -> list[str]:
    sddl = _sddl_string(path)
    aces = re.findall(r"\(([A-Z]);([^;]*);([^;]*);([^;]*);([^;]*);([^;)]*)\)", sddl)
    return [ace[5] for ace in aces if ace[0] == "A"]


def _sddl_is_protected(path: Path) -> bool:
    header = _sddl_string(path).split("(", 1)[0]
    return "P" in header.replace("D:", "", 1)
