"""Media analysis, link download and optional AI provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass
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


class DownloadUnavailable(RuntimeError):
    """Downloader tools are missing or unconfigured; the job becomes blocked."""


class DownloadInputInvalid(ValueError):
    """URL, platform, cookie or product problems; the job fails retryable."""


class DownloadProcessingCancelled(RuntimeError):
    """Cooperative cancellation during the download; the job becomes cancelled."""


@dataclass(frozen=True)
class DownloadedVideo:
    """A staged download product ready for probing and artifact ingestion."""

    filename: str  # 不含路径，仅文件名
    media_type: str  # "video/mp4" | "video/webm"
    byte_size: int
    title: str = ""  # 清洗截断后的平台标题；空表示未捕获（落库侧回退"未命名视频"）


class MediaDownloaderPort(Protocol):
    def capability(self) -> dict[str, object]: ...

    # {"enabled", "adapter": "yt-dlp", "version", "supported_platforms",
    #  "cookie_file_available", "network": True}
    def config_hash(self, platform: str, format_profile: str) -> str: ...

    def download(
        self,
        *,
        url: str,
        platform: str,
        workspace: Path,
        limits: MediaProcessingLimits,
        use_cookie: bool,               # 是否使用已导入 cookies.txt
        cookie_path: Path | None,       # use_cookie=True 时指向 staging 内 Cookie 拷贝
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        progress: Callable[[int, str], None],
    ) -> DownloadedVideo: ...
