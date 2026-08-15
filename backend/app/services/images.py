"""Pillow-based local image metadata analysis persisted as representation/evidence."""

from __future__ import annotations

import hashlib
import time
from typing import Callable

import PIL
from PIL import ExifTags, Image

from app.domain.media import MediaProcessingLimits, image_metadata_locator
from app.ports.media import ImageInputInvalid, MediaProcessingCancelled
from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort
from app.services.documents import DocumentService

_EXIF_DATETIME_ORIGINAL = 0x9003  # 36867 DateTimeOriginal
_EXIF_ARTIST = 0x013B  # 315 Artist
_EXIF_DESCRIPTION = 0x010E  # 270 ImageDescription


class ImageService:
    """REQ-048 图片分析：只用 Pillow 本地读取尺寸/格式/EXIF，不做 OCR、AI 描述或网络调用。"""

    def __init__(
        self,
        repository: RepositoryPort,
        artifacts: ArtifactStoragePort,
        documents: DocumentService,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.documents = documents

    @staticmethod
    def _clean_exif_text(value: object) -> str | None:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if not isinstance(value, str):
            return None
        text = "".join(ch for ch in value.strip() if ch.isprintable())[:300]
        return text or None

    @staticmethod
    def _normalize_datetime(value: str) -> str:
        # EXIF 标准格式 "YYYY:MM:DD HH:MM:SS" → 展示用 "YYYY-MM-DD HH:MM:SS"。
        if len(value) >= 10 and value[4] == ":" and value[7] == ":":
            return f"{value[:4]}-{value[5:7]}-{value[8:10]}{value[10:]}"
        return value

    def _exif_metadata(self, image: Image.Image) -> dict[str, str | None]:
        empty = {"datetime_original": None, "artist": None, "description": None}
        try:
            exif = image.getexif()
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
        except Exception:
            return empty
        if not isinstance(exif_ifd, dict):
            exif_ifd = {}
        datetime_original = self._clean_exif_text(exif.get(_EXIF_DATETIME_ORIGINAL) or exif_ifd.get(_EXIF_DATETIME_ORIGINAL))
        if datetime_original:
            datetime_original = self._normalize_datetime(datetime_original)
        return {
            "datetime_original": datetime_original,
            "artist": self._clean_exif_text(exif.get(_EXIF_ARTIST) or exif_ifd.get(_EXIF_ARTIST)),
            "description": self._clean_exif_text(exif.get(_EXIF_DESCRIPTION)),
        }

    @staticmethod
    def _metadata_text(metadata: dict[str, object]) -> str:
        values = [
            "本地图片分析",
            f"尺寸：{metadata['width']} x {metadata['height']}",
            f"格式：{metadata['format']}",
        ]
        if metadata["datetime_original"]:
            values.append(f"拍摄时间：{metadata['datetime_original']}")
        if metadata["artist"]:
            values.append(f"EXIF 作者：{metadata['artist']}")
        if metadata["description"]:
            values.append(f"EXIF 描述：{metadata['description']}")
        return "\n".join(values)

    def analyze(
        self,
        *,
        version_id: str,
        artifact_sha256: str,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        progress: Callable[[int, str], None],
    ) -> dict:
        path = self.artifacts.artifact_path(artifact_sha256)
        if not path.is_file():
            raise FileNotFoundError("artifact_missing")
        deadline = time.monotonic() + limits.timeout_seconds
        progress(10, "正在读取本地图片")
        try:
            with Image.open(path) as probe:
                probe.verify()
            with Image.open(path) as image:
                width, height = image.size
                image_format = str(image.format or "").upper()
                # 解压炸弹护栏：解码前按 RGBA 展开估计占用的内存上限（沿用媒体内存断路器设置）。
                if width * height * 4 > limits.maximum_memory_bytes:
                    raise ImageInputInvalid("image_pixels_over_memory_limit")
                image.load()
                exif = self._exif_metadata(image)
        except ImageInputInvalid:
            raise
        except Exception:
            # 损坏/伪造/越界图片：解析器细节（含路径）绝不进入持久化消息。
            raise ImageInputInvalid("image_unreadable") from None
        if cancelled():
            raise MediaProcessingCancelled()
        if time.monotonic() > deadline:
            raise RuntimeError("image_timeout")
        heartbeat()
        progress(60, "正在生成可引用的图片元数据")
        metadata: dict[str, object] = {
            "width": width,
            "height": height,
            "format": image_format,
            **exif,
        }
        text = self._metadata_text(metadata)
        excerpt = text[:300]
        evidence = [{
            "locator": image_metadata_locator(width, height, image_format, exif["datetime_original"]),
            "excerpt": excerpt,
            "excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
            "is_validated": True,
        }]
        chunks = self.documents.search_chunk_pairs(text)
        config_hash = hashlib.sha256(f"pillow-local:{PIL.__version__}".encode("utf-8")).hexdigest()
        self.repository.persist_representation_bundle(
            version_id=version_id,
            artifact_sha256=artifact_sha256,
            kind="extraction",
            parser_name="pillow-local",
            config_hash=config_hash,
            text=text,
            parent_id=None,
            chunks=chunks,
            evidence=evidence,
        )
        progress(90, "正在校验本地图片 artifact")
        if not self.artifacts.verify(artifact_sha256):
            raise RuntimeError("artifact_verification_failed")
        return {"metadata": metadata}
