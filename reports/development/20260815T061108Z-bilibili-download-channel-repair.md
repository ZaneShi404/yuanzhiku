# Bilibili Download Channel Repair - 20260815T061108Z

## Scope And Status

This is a developer repair and self-test report only. It makes no independent-test or user-acceptance claim.

User report: bilibili link downloads (`video_download` jobs) failed consistently with the sanitized message 「链接失效、平台拒绝或下载产物无效」, both without cookies and with a valid login cookies.txt.

## Diagnosis

Local evidence (job payloads, operation logs) could not distinguish the failure class by design (REQ-047 sanitization). With the user's explicit involvement, a manual diagnostic run of the locked yt-dlp outside the app (user's machine, user's cookies, no app proxy) succeeded fully — extraction, stream download, and ffmpeg merge all worked. The diagnostic log showed the actual CDN target:

```
https://xy119x188x120x16xy.mcdn.bilivideo.cn:8082/v1/resource/...
```

Root cause: the bilibili registered-domain list (`DOWNLOAD_REGISTRY`) contained `bilivideo.com` but **not** `bilivideo.cn`. Bilibili MCDN mirror nodes live under `mcdn.bilivideo.cn`, so the job-scoped loopback filtering proxy correctly denied the stream connection per policy — metadata APIs (under `bilibili.com`) succeeded while every stream fetch was refused. This matched the observed signature exactly: link probe OK, download fails within ~3s.

## Repairs

1. **决策 13 — registry addition** (`backend/app/adapters/downloader.py`): bilibili group gains `bilivideo.cn`, with the real-link evidence recorded inline and in `docs/v1-2-requirements.md` §7.2.1 table + changelog (2026-08-15 entry). Per the registry maintenance rule this is a security-boundary change backed by captured real-link outbound evidence; acceptance review should confirm it.
2. **Relay lifetime fix** (`LoopbackFilterProxy._bidirectional_relay`, found during this investigation): the pump threads were joined with fixed timeouts (`2 × IO_TIMEOUT_SECONDS` each), after which the remote socket was unconditionally closed — any transfer active longer than ~60s would have been cut mid-stream (large files would have hit this next). Joins now wait without a fixed cap; pump termination is driven by the sockets' own IO timeouts (idle connections still converge) and peer close/EOF.

## Developer Regression Coverage

`tests/unit/test_video_download.py`:

- `test_bilibili_registry_includes_bilivideo_cn_media_cdn` — registry contains `bilivideo.cn`; label-boundary matching accepts `*.mcdn.bilivideo.cn`, rejects lookalikes (`evilbilivideo.cn`, `bilivideo.cn.evil.com`).
- `test_proxy_relay_has_no_absolute_lifetime_cap` — with a test proxy whose IO timeout is shrunk to 0.2s, an active transfer lasting ~1.2s (beyond the old ~0.8s teardown point) completes intact; the old implementation tears the connection down mid-transfer.

## Commands Actually Run

| Command | Outcome |
| --- | --- |
| `pytest tests/unit/test_video_download.py::test_bilibili_registry_includes_bilivideo_cn_media_cdn tests/unit/test_video_download.py::test_proxy_relay_has_no_absolute_lifetime_cap -q` | 2 passed |
| `pytest tests/unit/test_video_download.py tests/unit/test_link_probe.py -q` | 93 passed in 378.69s |

## Follow-up For The User

After the fix, the running instance was restarted; the user retries the failed download from the jobs page (platform calls stay user-initiated per the operations rule). The previous douyin-dominated cookies.txt had been replaced by the bilibili login export at the user's choice; douyin downloads will need a fresh douyin cookies.txt import.
