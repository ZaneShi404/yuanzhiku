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
