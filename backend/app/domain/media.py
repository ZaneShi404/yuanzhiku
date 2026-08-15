"""Technology-neutral values for locally analyzed video artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaProcessingLimits:
    timeout_seconds: float
    maximum_memory_bytes: int
    maximum_workspace_bytes: int
    deadline_monotonic: float | None = None


@dataclass(frozen=True)
class VideoMetadata:
    container_name: str
    duration_ms: int
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None


@dataclass(frozen=True)
class ExtractedVideoFrame:
    ordinal: int
    time_ms: int
    path: Path
    width: int | None
    height: int | None


@dataclass(frozen=True)
class MediaTranscriptSegment:
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class MediaTranscript:
    text: str
    segments: tuple[MediaTranscriptSegment, ...]


VIDEO_METADATA_LOCATOR_TYPE = "video_metadata"
VIDEO_TIME_RANGE_LOCATOR_TYPE = "video_time_range"
IMAGE_METADATA_LOCATOR = "image_metadata"


def image_metadata_locator(
    width: int,
    height: int,
    image_format: str,
    datetime_original: str | None = None,
) -> dict[str, int | str | None]:
    """图片元数据证据唯一允许的 locator：尺寸与格式等只读字段（REQ-048）。

    图片分析落 evidence 时只能用本工厂构造的 locator；非法尺寸或格式直接
    拒绝，避免无法定位回 artifact 的元数据进入证据链。
    """
    if (
        isinstance(width, bool) or isinstance(height, bool)
        or not isinstance(width, int) or not isinstance(height, int)
    ):
        raise ValueError("image_metadata locator 的宽高必须为整数像素")
    if width <= 0 or height <= 0:
        raise ValueError("image_metadata locator 的宽高必须为正整数")
    if not isinstance(image_format, str) or not image_format.strip():
        raise ValueError("image_metadata locator 的格式必须为非空字符串")
    if datetime_original is not None and not isinstance(datetime_original, str):
        raise ValueError("image_metadata locator 的拍摄时间必须为字符串或 None")
    return {
        "type": IMAGE_METADATA_LOCATOR,
        "width": width,
        "height": height,
        "format": image_format.strip(),
        "datetime_original": datetime_original,
    }


def video_time_range_locator(start_ms: int, end_ms: int) -> dict[str, int | str]:
    """视频转写证据唯一允许的 locator：带毫秒起止范围（REQ-016）。

    未来转写/摘要落 evidence 时只能用本工厂构造的 locator；非法范围直接
    拒绝，避免无定位能力的转写摘录进入证据链。
    """
    if isinstance(start_ms, bool) or isinstance(end_ms, bool) or not isinstance(start_ms, int) or not isinstance(end_ms, int):
        raise ValueError("video_time_range locator 的起止必须为整数毫秒")
    if start_ms < 0 or start_ms >= end_ms:
        raise ValueError("video_time_range locator 必须满足 0 <= start_ms < end_ms")
    return {"type": VIDEO_TIME_RANGE_LOCATOR_TYPE, "start_ms": start_ms, "end_ms": end_ms}
