# yt-dlp-upgrade-download-diagnostics：开发报告

- 报告 ID：`RPT-YT-DLP-UPGRADE-DOWNLOAD-DIAGNOSTICS-20260904T054706Z-001`
- 记录时间（UTC）：`2026-09-04T05:47:06Z`
- 报告类型：`development`
- 作者角色：`development`
- 独立性：`non_independent`
- 产品版本：`v1.7.0`
- 裁定范围：`archive_local`
- 裁定：`accepted`

## 范围

抖音链接下载全量故障（2026-09-04 用户报告，作业消息为脱敏通用文案）的根因诊断、修复与长效机制：yt-dlp 升级（`REQ-046` 预授权条款）、失败消息指引、固化诊断工具 `tools/diagnose_download.py`（`REQ-047` 受限通道内）。

## 根因诊断（逐层复现，全部走应用受限通道）

1. 作业记录核查：失败码 `DownloadInputInvalid("failed")`（yt-dlp 子进程非零退出，stderr 按纪律不落日志）。
2. 元数据探测复现：**成功**——链接有效、标题正确，代理白名单无缺口。
3. 完整下载复现（保留 stderr）：抖音 web detail 接口 **403**，yt-dlp 报 `Fresh cookies (not necessarily logged in) are needed`。
4. Cookie 排除：键名/有效期核查（不含值）——登录态 sessionid 剩 38 天、ttwid 剩 340 天，非过期问题。
5. 决定性对照实验：同一链接、同一代理、同一 Cookie，仅将 yt-dlp 2026.7.4（锁定版）换为上游最新 2026.8.19 → **下载立即成功**（6.07MB MP4）。

**根因**：抖音服务端调整反爬参数，锁定版 yt-dlp 的 Douyin 抽取器签名算法过时；Cookie 与代理白名单均无问题。

## 已实现

- `backend/requirements.lock`：`yt-dlp==2026.7.4` → `2026.8.19`（用户批准，`REQ-046` 预授权条款）。
- `REQ-046` 修订（用户预授权 2026-09-04）：yt-dlp 为唯一允许跟随上游日历版本升级的依赖，每次升级须更新 lock 并通过下载链路回归 + 全量回归；其余依赖维持锁定。
- `tools/diagnose_download.py`：四分类诊断工具（链接校验 / Cookie 状态与过期键名 / 代理拦截域名 / yt-dlp 底层复现含 stderr + 上游版本对比），与下载作业走同一受限通道，支持 `--cookie`/`--download`。
- 下载失败作业消息补通用指引（反爬策略更新 → 升级 yt-dlp / 重导 Cookie / 使用诊断工具）。
- 文档同步：`docs/dependency-installation.md`（版本、例外条款、实证记录、诊断工具）、`docs/requirements.md`（`REQ-046` 修订）。

## 验证

| 验证 | 结果 |
| --- | --- |
| 诊断工具端到端（探测 + `--download` 完整下载） | 探测成功、下载成功（video.mp4）、代理零拦截、版本 2026.8.19 |
| 下载链路回归 `tests/unit/test_video_download.py tests/unit/test_link_probe.py tests/unit/test_api.py` | `114 passed` |
| 全量回归（升级 + 消息变更后） | 见下表补登 |

## 已知限制

- yt-dlp 对抖音的修复本质是追赶式：上游每次发版修复与抖音下一次反爬调整之间存在时间窗，期间抖音链接可能再次失败（作业消息已指引使用诊断工具与升级路径）。
- 抖音反爬具有概率性（同一链接元数据探测偶发 403、重试即成功），属平台侧噪声，作业层重试可覆盖。
