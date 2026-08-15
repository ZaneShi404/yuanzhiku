# Unified Smart Import Page - 20260815T054454Z

## Scope And Status

This is a developer feature and self-test report only. It makes no independent-test or user-acceptance claim.

All import entries (paste text, document file, image, local video, whitelist-platform link download, external card creation) were merged into a single smart-detect import page, per user decision. The separate 「视频」 nav page was removed; the 「外部卡」 page is now a read-only list. No backend change was required — all endpoints (`/imports/paste|file|image|prefill`, `/videos/local|link|link/probe`, `/external/cards|douyin`) were already delivered and tested under REQ-047b/048/049.

## What Was Built

### `frontend/src/App.tsx`

- `Page` union and `nav` array: `'video'` entry removed; `VideoWorkspace` component deleted (~127 lines), its downloader-capability fetch, link probe, cookie switch, and filename-stem prefill moved into the unified page.
- `ImportPage` replaced by a unified smart-detect page (`App.tsx:951-1145`), with detection helpers at :933-949. Real-time derived `detected` kind (no extra state):
  1. File selected → routed by suffix: 文档 (.pdf/.docx/.md/.markdown/.txt) / 图片 (.jpg/.jpeg/.png/.webp) / 视频 (.mp4/.webm); unsupported suffix rejected with a Chinese message. File takes precedence over pasted text.
  2. No file and trimmed text matches `^https?://\S+$` → URL: hostname under `bilibili.com`/`b23.tv` → bilibili link download; under `douyin.com` → douyin link download; platform select removed (derived from hostname). A secondary toggle switches to card-only save (「仅保存为外部卡」, douyin → `/external/douyin`, others → `/external/cards`). Non-platform URLs go straight to external-card mode.
  3. Other non-empty text → paste import; empty → submit disabled.
- Metadata area reuses existing fields and the touched/applyPrefill rules from the prefill feature (only untouched fields are filled; language only while default and untouched). External-card mode shows only title/URL(readonly)/author/notes/tags and no rights control, preserving prior card semantics.
- Submit dispatch: text→`/imports/paste`; doc→`/imports/file`; image→`/imports/image`; video→`/videos/local` (XHR progress); link→`/videos/link` (then jobs page); card→`/external/cards|douyin` (then external-cards page). Rights confirmation remains required and manual for every non-card flow.
- `ExternalCardsPage` reduced to a read-only list of saved cards (open-original-URL link, douyin notice, focused scroll); page header notes that new cards are created from the import page.

### `frontend/src/styles.css`

Appended `.file-row`, `.file-chip`, `.detect-row`, `.detect-badge`, `.link-tools`; removed rules used only by deleted components (`.file-pick`, `.video-workspace`, `.input-action`).

## Commands Actually Run

| Command | Outcome |
| --- | --- |
| `npm run lint`（frontend/，tsc -b） | 零错误零警告 |
| `npm run build`（frontend/，tsc -b && vite build） | 成功，`dist/` 重建（`index-8xiJ18yX.js` 212.20 kB） |
| `curl http://127.0.0.1:8765/`（运行中实例） | 已托管新 bundle `index-8xiJ18yX.js`，刷新页面即生效，无需重启后端 |

## Docs Updated

- `docs/requirements.md` REQ-044 修订：页面清单移除「视频」页；导入页为统一智能识别入口；外部卡页为只读列表；保留全部安全语义（联网告知、权利必选、Cookie 开关、域名自动判定平台、不预览/嗅探）
- `docs/test-plan.md` T-UI-001 措辞同步

## Known Boundaries

- 浏览器端逐路径点验未执行（无头环境）；交互正确性以 typecheck、build 和逐行复查为准，建议用户在真实浏览器过一遍 T-UI-001 烟测
- 未做拖放导入；外部卡仍无权利确认字段（语义不变）
