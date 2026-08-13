# T-VID-005 真实平台独立验收记录（v1.2 链接获取）

- 日期：2026-08-13（UTC `20260813T072039Z`）
- 范围：v1.2 链接获取真实平台验收（B站 + 抖音，用户提供链接）
- 角色说明：由编排代理按用户指令执行的真实环境验收（operator-assisted）；链接由用户本人提供；抖音链接原文未写入任何记录（含签名参数，仅存于作业 payload 的脱敏形式与 provenance 表）
- 环境：Windows、FFmpeg/ffprobe 9.0.1 essentials（gyan.dev，本机显式安装，用户 PATH 已持久化）、yt-dlp 2026.7.4（锁定）、网络为 fake-IP 隧道环境（决策 10 隧道段例外已生效并经独立审核放行）

## 验收结果

### 哔哩哔哩：通过（accepted）

链接形态：b23.tv 短链（无查询参数，脱敏形式即原文形态），平台视频 1080p、26.5 分钟。

| 验收点 | 结果 | 证据 |
|---|---|---|
| 链接提交 | `POST /videos/link` → `201`，`video_download` 作业入队 | job `21ff5167…` |
| 下载+合并 | succeeded；产物 `video.mp4`（hevc 1080p + aac 音频），非纯视频降级 | artifact `5f84ace5…` |
| 平台标题回填 | 用户未填标题，自动回填平台标题成功 | 与平台页面标题一致 |
| provenance 脱敏登记 | `url_sanitized`＝scheme://host/path 无查询参数、`cookie_used=0`、yt-dlp 版本与格式档案齐全 | `video_download_provenance` 行 `a4343656…` |
| 自动入队分析 | `video_analyze` 自动创建 | job `8942e5db…` |
| 分析（真实 FFmpeg） | 首次因内存监测竞态失败（已修复），重试后 succeeded | 元数据 duration 1593887ms、1920×1080、hevc/aac；12 关键帧 |
| 流式播放 | `Range: bytes=0-1023` → `206`，1024 字节 | `GET /videos/{id}/stream` |
| 出站控制 | 全程经回环过滤代理；实测出站主机 ⊆ 注册域清单（b23.tv、www/api.bilibili.com、upos*.bilivideo.com） | 代理 CONNECT 记录 |

### 抖音：待执行（pending，需 Cookie）

链接形态：v.douyin.com 分享短链。无 Cookie 直接探测失败（平台返回 "Fresh cookies (not necessarily logged in) are needed"——不需要登录账号，但需要浏览器访问过抖音产生的新鲜 Cookie）。按 REQ-047a 单通道，需用户导入 cookies.txt 后重跑验收。抖音链接原文（含签名参数）未写入本记录与任何仓库文件。

## 验收过程中发现并修复的真实缺陷（5 项，均为真实硬件环境才暴露）

1. 隧道段例外（决策 10）：fake-IP 环境 DNS 全量解析到 198.18.0.0/15，代理原保留段拒绝阻断全部下载 → 规范修订 + 实施 + 独立审核放行（`5fff506`、`abdd3d6`、`7bfb7b5`）。
2. CONNECT 请求头未排空：残留 `Host:` 头被当 TLS 数据转发，真实 TLS 服务器回 400 → 修复 + 可证伪回归测试（`9e395c5`）。
3. yt-dlp 子进程找不到 FFmpeg → 静默不合并、产出纯视频"成功" → `--ffmpeg-location` + PATH 双保险 + FFmpeg 缺失 blocked 预检 + 未合并残留检测（`d1ef3ef`）。
4. 抽帧滤镜 `scale=min(640,iw):-2` 逗号未转义（v1.1 潜伏）→ `scale=min(640\,iw):-2`（`0409c5c`）。
5. 内存监测 psutil.NoSuchProcess 竞态（psutil.Error 非 OSError）→ 尽力而为语义（`2072150`）。

## 冻结门禁状态更新

- FFmpeg/ffprobe 物理可用验证：**已满足**（本次安装并实测启用）。
- 注册域清单实测比对：**B站组已实证**（b23.tv、bilibili.com、api.bilibili.com、upos*.bilivideo.com 全部命中登记清单）；douyin 组待 Cookie 后比对。
- 真实平台独立验收：B站完成；抖音待 cookies.txt。
- release_readiness：保持 **blocked**（与 v1.1 政策一致；archive-local acceptance 不等于 release approval）。

## 遗留与后续

- 抖音验收待用户提供 cookies.txt（浏览器扩展导出 douyin.com 域，或由编排代理经用户确认后从 Edge/Chrome 提取）。
- 验收期间产生的失败测试数据已软删除（source `04d4a717…`，纯视频半成品）；成功条目保留于用户库中。
- 失败作业记录保留为审计轨迹，未删除。
