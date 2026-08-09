# Local Video v1.1.0 Upgrade Development Record

## Scope

This record covers the v1.1.0 local-video upgrade. It adds local MP4/WebM import with rights declaration, immutable SHA-256 original and JPEG-frame artifacts, local metadata probing, bounded frame extraction, durable video jobs, local byte-range playback, version-scoped video detail, and portable transfer and cleanup handling.

The video workspace adds local upload controls, persisted metadata and keyframes, and a non-submitting link-acquisition reservation. The reservation does not accept or submit URLs and does not create network work.

## Security Boundaries

- Only local MP4/WebM import is in scope. No webpage link is fetched, downloaded, proxied, parsed, previewed, cached, or automated.
- Douyin and Bilibili acquisition are not implemented. Douyin remains a literal-only external card: no downloading, crawling, media extraction, parsing, login, cookies, proxying, preview, cache, automation, or backend request.
- FFmpeg and ffprobe are explicit local dependencies. Their adapter runs without a shell, with disabled standard input, isolated staging, bounded output, cancellation, deadline, workspace, memory, and frame-count controls.
- Media AI remains a disabled port. Transcription and summary jobs become blocked until an explicitly configured provider exists; the default implementation has no external network traffic and creates no synthetic text, summary, or evidence.
- Original local paths, media bytes, command arguments, credentials, tokens, cookies, and provider responses are outside the persisted, API, export, log, and archive record boundaries.

## Implementation Record

- Added media domain types and analyzer/AI ports, plus a local FFmpeg adapter and inert AI adapter.
- Added video analysis and frame persistence for SQLite and PostgreSQL, including the PostgreSQL video-media migration.
- Added local video import, durable analysis, disabled future AI job entry points, range-safe playback, frame delivery, and selected-version isolation.
- Added content-addressed frame artifact lifecycle handling for backup, export, reimport validation, and permanent purge.
- Added the video navigation/workspace, player, keyframe strip, AI-disabled state, and local analysis settings.
- Added requirement, architecture, API, installation, operations, threat-model, acceptance-matrix, and test-plan updates.

## Decision

The implementation is recorded as a v1.1.0 local-validation candidate. It is not a release decision. Release remains blocked pending real FFmpeg/ffprobe media validation and the existing physical PostgreSQL, Docker Compose, and Edge/Chrome gates.

## Evidence

- `docs/requirements.md`
- `docs/acceptance-matrix.md`
- `docs/api-contract.md`
- `docs/threat-model.md`
- `backend/app/adapters/media.py`
- `backend/app/services/videos.py`
- `backend/alembic/versions/007_video_media.py`
- `frontend/src/App.tsx`
- `tests/unit/test_video_media.py`
