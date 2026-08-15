# Share-Text Link Extraction For Unified Import - 20260815T073837Z

## Scope And Status

This is a developer UX repair and self-test report only. It makes no independent-test or user-acceptance claim.

User report: douyin share text (口令 prefix + description + hashtags + `v.douyin.com` short link + trailing instructions) pasted into the unified import page was misdetected as plain text instead of a video link download.

## Repair

Frontend-only change in `frontend/src/App.tsx` (unified `ImportPage`):

- New `extractUrl(text)` helper: finds the first `https?://\S+` token inside mixed text and trims trailing CJK/ASCII punctuation; new `shareNotesOf(text, url)` helper: removes the URL, the leading 口令 token run (latin/digit/symbol run before the first CJK character), and the trailing 「复制此链接…观看视频」 instruction, collapsed as a notes suggestion.
- Detection rule revised: bare platform URL **or mixed text containing a platform URL** (douyin share blobs) → `link` flow with the extracted URL; a bare non-platform URL → `card`; ordinary text containing a non-platform URL stays `text` (no behavior change for articles with links).
- The link flow shows the extracted URL in a read-only field so the user can verify what will be submitted; probe (`/videos/link/probe`) and submit (`/videos/link`) both send the extracted URL; card submission likewise uses the extracted URL.
- When a blob yields a platform link, the cleaned leftover text prefills 备注 (only while notes is empty/unedited).
- Textarea placeholder updated: 「粘贴文本、视频链接或网页链接（支持抖音分享口令整段粘贴）」。

Backend untouched: `/videos/link` keeps its strict single-URL validation; extraction is a presentation-layer concern.

## Verification

| Command | Outcome |
| --- | --- |
| `npm run lint` | 零错误 |
| `npm run build` | 成功（`index-SBdsb4OP.js` 213.17 kB），dist 由运行中实例即时托管 |
| `node` 提取逻辑抽查（用户真实口令样本） | 提取 `https://v.douyin.com/jKRrd9uH6_A/`；备注正确剥离子口令前缀与引导语；裸链/纯文本行为不变 |

## Docs

- `docs/requirements.md` REQ-044：统一导入页描述补充分享口令混合文本的链接提取与备注带入。

## Known Boundaries

- 浏览器端未逐路径点验；建议用户用真实抖音分享文本走一遍 T-UI-001 链接路径
- 非平台 URL 混在长文中仍按文本导入（刻意保守，避免误把文章变外部卡）
