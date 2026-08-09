"""Media analysis and optional AI provider boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

from app.domain.media import ExtractedVideoFrame, MediaProcessingLimits, MediaTranscript, VideoMetadata


class MediaToolUnavailable(RuntimeError):
    pass


class MediaInputInvalid(ValueError):
    pass


class MediaProcessingCancelled(RuntimeError):
    pass


class MediaAiUnavailable(RuntimeError):
    pass


class MediaAnalyzerPort(Protocol):
    def capability(self) -> dict[str, object]: ...

    def config_hash(self, maximum_frames: int) -> str: ...

    def probe(
        self,
        artifact_path: Path,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> VideoMetadata: ...

    def extract_frames(
        self,
        artifact_path: Path,
        metadata: VideoMetadata,
        workspace: Path,
        maximum_frames: int,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
    ) -> tuple[ExtractedVideoFrame, ...]: ...


class MediaAiPort(Protocol):
    def capability(self) -> dict[str, object]: ...

    def config_hash(self, operation: str) -> str: ...

    def transcribe(self, artifact_path: Path, media_type: str | None, cancelled: Callable[[], bool]) -> MediaTranscript: ...

    def summarize(self, transcript: str, cancelled: Callable[[], bool]) -> str: ...
