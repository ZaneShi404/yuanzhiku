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
