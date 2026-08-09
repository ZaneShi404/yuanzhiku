"""Persist video analysis, keyframe artifacts, and time-located representations."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

from app.domain.media import MediaProcessingLimits, VideoMetadata
from app.ports.media import MediaAnalyzerPort, MediaProcessingCancelled
from app.ports.repository import RepositoryPort
from app.ports.storage import ArtifactStoragePort
from app.services.documents import DocumentService


class VideoService:
    def __init__(
        self,
        repository: RepositoryPort,
        artifacts: ArtifactStoragePort,
        documents: DocumentService,
        analyzer: MediaAnalyzerPort,
    ) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.documents = documents
        self.analyzer = analyzer

    @staticmethod
    def _metadata_payload(metadata: VideoMetadata) -> dict[str, object]:
        return {
            "container_name": metadata.container_name,
            "duration_ms": metadata.duration_ms,
            "width": metadata.width,
            "height": metadata.height,
            "video_codec": metadata.video_codec,
            "audio_codec": metadata.audio_codec,
        }

    @staticmethod
    def _metadata_text(metadata: VideoMetadata) -> str:
        values = [
            "本地视频分析",
            f"时长：{metadata.duration_ms / 1000:.3f} 秒",
            f"尺寸：{metadata.width or '-'} x {metadata.height or '-'}",
            f"容器：{metadata.container_name}",
            f"视频编码：{metadata.video_codec or '-'}",
            f"音频编码：{metadata.audio_codec or '-'}",
        ]
        return "\n".join(values)

    @staticmethod
    def metadata_locator(metadata: VideoMetadata) -> dict[str, object]:
        return {
            "type": "video_metadata",
            "duration_ms": metadata.duration_ms,
            "width": metadata.width,
            "height": metadata.height,
        }

    def analyze(
        self,
        *,
        version_id: str,
        artifact_sha256: str,
        maximum_frames: int,
        limits: MediaProcessingLimits,
        cancelled: Callable[[], bool],
        heartbeat: Callable[[], None],
        progress: Callable[[int, str], None],
    ) -> dict:
        path = self.artifacts.artifact_path(artifact_sha256)
        if not path.is_file():
            raise FileNotFoundError("artifact_missing")
        execution_limits = replace(
            limits,
            deadline_monotonic=time.monotonic() + limits.timeout_seconds,
        )
        progress(10, "正在读取本地视频元数据")
        metadata = self.analyzer.probe(path, execution_limits, cancelled, heartbeat)
        if cancelled():
            raise MediaProcessingCancelled()
        progress(30, "正在提取时间采样关键帧")
        config_hash = self.analyzer.config_hash(maximum_frames)
        workspace = self.artifacts.staging_path().with_suffix("")
        workspace.mkdir(parents=True, exist_ok=False)
        stored_frames: list[dict[str, object]] = []
        try:
            frames = self.analyzer.extract_frames(
                path, metadata, workspace, maximum_frames, execution_limits, cancelled, heartbeat
            )
            total = len(frames)
            for index, frame in enumerate(frames, start=1):
                if cancelled():
                    raise MediaProcessingCancelled()
                with frame.path.open("rb") as stream:
                    stored = self.artifacts.store_stream(stream, frame.path.stat().st_size)
                stored_frames.append({
                    "artifact_sha256": stored.sha256,
                    "byte_size": stored.byte_size,
                    "ordinal": frame.ordinal,
                    "time_ms": frame.time_ms,
                    "width": frame.width,
                    "height": frame.height,
                })
                progress(30 + round(index / max(total, 1) * 45), "正在保存关键帧")
            payload = self._metadata_payload(metadata)
            progress(80, "正在生成可引用的视频元数据")
            text = self._metadata_text(metadata)
            excerpt = text[:300]
            evidence = [{
                "locator": self.metadata_locator(metadata),
                "excerpt": excerpt,
                "excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "is_validated": True,
            }]
            chunks = self.documents.search_chunk_pairs(text)
            self.repository.persist_representation_bundle(
                version_id=version_id,
                artifact_sha256=artifact_sha256,
                kind="extraction",
                parser_name="ffmpeg-local",
                config_hash=config_hash,
                text=text,
                parent_id=None,
                chunks=chunks,
                evidence=evidence,
            )
            progress(88, "正在保存关键帧分析记录")
            output = self.repository.persist_video_analysis(
                version_id=version_id,
                analyzer_name="ffmpeg-local",
                config_hash=config_hash,
                metadata=payload,
                frames=stored_frames,
            )
            progress(95, "正在校验本地视频与关键帧")
            if not self.artifacts.verify(artifact_sha256) or not all(
                self.artifacts.verify(str(frame["artifact_sha256"])) for frame in stored_frames
            ):
                raise RuntimeError("artifact_verification_failed")
            return {"metadata": payload, **output}
        finally:
            for frame in stored_frames:
                # Keep frames referenced by an accepted analysis; compensate only
                # content-addressed files whose database row is unreferenced.
                sha256 = str(frame["artifact_sha256"])
                if self.repository.delete_artifact_if_unreferenced(sha256):
                    self.artifacts.delete(sha256)
            import shutil

            shutil.rmtree(workspace, ignore_errors=True)

    def detail(self, source_id: str, version_id: str | None = None) -> dict | None:
        source = self.repository.get_source(source_id, include_deleted=False)
        if source is None:
            return None
        versions = self.repository.versions_for_source(source_id)
        if not versions:
            return None
        version = next((item for item in versions if item["id"] == version_id), None) if version_id else versions[0]
        if version is None or version.get("media_type") not in {"video/mp4", "video/webm"}:
            return None
        analysis = self.repository.video_analysis_for_version(version["id"])
        if analysis is not None:
            analysis["metadata"] = json.loads(analysis.pop("metadata_json"))
        return {"source_id": source_id, "version": version, "analysis": analysis}
