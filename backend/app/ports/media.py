"""Media analysis, link download and optional AI provider boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

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
        transcript_segments: list[tuple[int, int]] | None = None,
    ) -> tuple[ExtractedVideoFrame, ...]:
        """``transcript_segments``（v1.7，REQ-056.2）为同版本转写表示的段级
        video_time_range（毫秒起止）：非空时采样计划融合转写语义锚点；空/None
        时为纯信号抽帧（行为与 v1.6 一致）。"""


class MediaTranscriberPort(Protocol):
    """语音转写适配器边界（REQ-054）：本地引擎与远程端点同一接口。

    输入为作业统一提取的音轨分块（决策 18，``app.services.audio`` 的
    ``extract_audio_chunks``，元素为 (块路径, 偏移毫秒, 时长毫秒)）；
    输出的 MediaTranscript 分段必须映射回视频时间轴（块偏移 + 段内偏移）。
    """

    def capability(self) -> dict[str, object]: ...

    def config_hash(self) -> str: ...

    def transcribe(
        self,
        audio_chunks: list[tuple[Path, int, int]],
        cancelled: Callable[[], bool],
    ) -> MediaTranscript: ...


class VideoUnderstandingPort(Protocol):
    """视频直送补充理解边界（REQ-055，决策 16/17）：可替换供应商适配器。

    输出与关键帧画面理解同一契约：list of {"time_ms": int, "description":
    str, "visible_text": str}，作业层据此写入摘要附录与证据；不可行时抛
    ``MediaAiUnavailable("video_input")`` → 作业层转关键帧兜底。
    """

    def capability(self) -> dict[str, object]: ...

    # {"video_input", "image_input"(v1.7), "max_bytes", "audio_in_video",
    #  "duration_limits", "reencode"}
    def config_hash(self) -> str: ...

    def understand_video(
        self,
        video_path: Path,
        transcript_text: str,
        focus: str,
        cancelled: Callable[[], bool],
    ) -> list[dict[str, Any]]: ...

    def understand_frames(
        self,
        sheet_image: Path,
        cells: list[tuple[int, int]],
        transcript_text: str,
        cancelled: Callable[[], bool],
    ) -> list[dict[str, Any]]:
        """帧级画面理解（v1.7 REQ-057，决策 25）：联络表单次多模态调用。

        ``sheet_image`` 为作业层预构建的缩略图网格（单元格按 1..N 编号）；
        ``cells`` 为每个格子的证据时间窗 (start_ms, end_ms)。输出条目契约：
        {"start_ms": int, "end_ms": int, "description": str, "visible_text":
        str, "time_ms": int(=start_ms，摘要附录复用)}；模型引用的格子号必须
        落在 1..N 内，越界条目一律丢弃（绝不伪造定位）。
        """
        ...


class MediaAiPort(Protocol):
    """可选媒体 AI 提供方边界（REQ-017）。

    转写结果落 evidence 时，每条摘录的 locator 只能经
    ``app.domain.media.video_time_range_locator`` 构造（毫秒起止范围，
    REQ-016）；实现方不得返回无法定位到时间范围的转写文本。
    """

    def capability(self) -> dict[str, object]: ...

    def config_hash(self, operation: str) -> str: ...

    def transcribe(self, artifact_path: Path, media_type: str | None, cancelled: Callable[[], bool]) -> MediaTranscript: ...

    def assess_completeness(self, transcript_text: str, context: dict[str, Any]) -> dict[str, Any]: ...

    def summarize(self, inputs: dict[str, Any], cancelled: Callable[[], bool]) -> dict[str, Any]: ...

    def classify(self, text: str, context: dict[str, Any]) -> dict[str, Any]: ...


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
