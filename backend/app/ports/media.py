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


class ImageInputInvalid(ValueError):
    """本地图片无法识别或解码（损坏/越界），消息一律脱敏。"""

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
    """可选媒体 AI 提供方边界（REQ-017）。

    转写结果落 evidence 时，每条摘录的 locator 只能经
    ``app.domain.media.video_time_range_locator`` 构造（毫秒起止范围，
    REQ-016）；实现方不得返回无法定位到时间范围的转写文本。
    """

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
    #  "cookies": {platform: bool}, "network": True}
    def config_hash(self, platform: str, format_profile: str) -> str: ...

    # REQ-047b 只读元数据探测（--skip-download，不下载、不持久化）；
    # 与 download 同白名单、同回环代理、同无 shell 子进程约束；
    # 返回 {"title", "author", "source_date"}（均可为 None）。
    def probe_metadata(self, url: str, platform: str, use_cookie: bool) -> dict[str, str | None]: ...

    def download(
        self,
        *,
        url: str,
        platform: str,
        workspace: Path,
        limits: MediaProcessingLimits,
        use_cookie: bool,               # 是否使用该平台已导入的 Cookie 文件
        cookie_path: Path | None,       # use_cookie=True 时指向 staging 内 Cookie 拷贝
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        progress: Callable[[int, str], None],
    ) -> DownloadedVideo: ...
