"""结构化与文件内容输入上限（加固计划 Task 5）。

纯域层校验器：只用标准库、不做 I/O 编排。上限对写入生效；数据库中超出
新上限的历史记录仍可读取、检索与导出（校验只发生在 API 写入路径）。
"""

from __future__ import annotations

from typing import Sequence

MAX_TAGS = 100
MAX_TAG_LENGTH = 100
MAX_ID_LIST = 500
MAX_ID_LENGTH = 128
MAX_JSON_BODY_BYTES = 12 * 1024 * 1024

DOCX_MAX_MEMBERS = 10_000
DOCX_MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
DOCX_MAX_MEMBER_UNCOMPRESSED = 256 * 1024 * 1024
DOCX_MAX_COMPRESSION_RATIO = 200

PDF_MAGIC = b"%PDF-"
DOCX_REQUIRED_MEMBERS = ("[Content_Types].xml", "word/document.xml")


class InputContentInvalid(ValueError):
    """文件内容与声明类型不符或结构损坏；错误消息不含文件内容。"""


class InputTooLarge(ValueError):
    """输入超出体积上限；错误消息不含文件内容。"""


def normalize_tags(value: list[str]) -> list[str]:
    """strip、丢弃空项、稳定去重；超出数量/长度上限抛 ValueError。"""
    if not isinstance(value, list):
        raise ValueError("标签必须是字符串数组")
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("标签必须是字符串数组")
        stripped = item.strip()
        if not stripped:
            continue
        if len(stripped) > MAX_TAG_LENGTH:
            raise ValueError(f"单个标签不能超过 {MAX_TAG_LENGTH} 个字符")
        cleaned.append(stripped)
        if len(cleaned) > MAX_TAGS:
            raise ValueError(f"标签数量不能超过 {MAX_TAGS} 项")
    return list(dict.fromkeys(cleaned))


def validate_id_list(value: list[str], *, field: str) -> list[str]:
    """引用/成员 ID 列表上限：≤500 项、每项 1–128 字符。"""
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是字符串数组")
    if len(value) > MAX_ID_LIST:
        raise ValueError(f"{field} 数量不能超过 {MAX_ID_LIST} 项")
    for item in value:
        if not isinstance(item, str) or not (1 <= len(item) <= MAX_ID_LENGTH):
            raise ValueError(f"{field} 中每项必须是 1 到 {MAX_ID_LENGTH} 个字符")
    return value


def validate_document_head(suffix: str, head: bytes) -> None:
    """按声明后缀校验文件头：PDF 魔数、TXT/Markdown 拒绝 NUL。"""
    if suffix == ".pdf":
        if not head.startswith(PDF_MAGIC):
            raise InputContentInvalid("文件内容与 PDF 格式不符")
        return
    if suffix in {".txt", ".md", ".markdown"}:
        if b"\x00" in head:
            raise InputContentInvalid("文本文件包含非法空字节")


def validate_docx_members(infos: Sequence[object]) -> None:
    """DOCX zip 中央目录元数据校验（成员数/解压总量/单成员/压缩比/必需成员）。

    元数据可伪造，真正的解压炸弹由解析作业的内存/磁盘断路器兜底；此处
    拦截的是超限声明与结构缺失。入参为 zipfile.ZipInfo 或同形对象。
    """
    filenames: list[str] = []
    total_uncompressed = 0
    for info in infos:
        name = str(getattr(info, "filename", ""))
        file_size = int(getattr(info, "file_size", 0))
        compress_size = int(getattr(info, "compress_size", 0))
        filenames.append(name)
        if len(filenames) > DOCX_MAX_MEMBERS:
            raise InputTooLarge("DOCX 成员数量超过上限")
        if file_size > DOCX_MAX_MEMBER_UNCOMPRESSED:
            raise InputTooLarge("DOCX 单个成员解压体积超过上限")
        total_uncompressed += file_size
        if total_uncompressed > DOCX_MAX_TOTAL_UNCOMPRESSED:
            raise InputTooLarge("DOCX 解压总体积超过上限")
        if compress_size > 0 and file_size > DOCX_MAX_COMPRESSION_RATIO * compress_size:
            raise InputContentInvalid("DOCX 成员压缩比异常")
    for required in DOCX_REQUIRED_MEMBERS:
        if required not in filenames:
            raise InputContentInvalid("DOCX 结构不完整")
