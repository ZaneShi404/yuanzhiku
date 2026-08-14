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

### 抖音：通过（accepted，2026-08-14 补验）

链接形态：v.douyin.com 分享短链（原文含签名参数，未写入任何记录；provenance 登记脱敏形式）。前置条件：用户经 Chrome 扩展导出的 cookies.txt（仅 douyin.com 域，9.6KB）导入应用。

| 验收点 | 结果 | 证据 |
|---|---|---|
| Cookie 导入 | `POST /settings/download-cookie` → `204`；`cookie_file_available=true` | capabilities |
| 链接提交（use_cookie=true） | `201`，`video_download` 作业入队 | job `97112ff5…`（首轮因注册域缺失与竖屏档位语义失败，修复后重试成功） |
| 下载 | succeeded；产物 mp4（hevc 720×1280 竖屏 + aac 音频） | artifact，probe 元数据 |
| 平台标题回填 | 成功（与平台页面标题一致） | sources.title |
| provenance | `platform=douyin`、`url_sanitized` 脱敏、`cookie_used=1`、yt-dlp 版本齐全 | `video_download_provenance` 行 |
| 自动入队分析 | `video_analyze` succeeded | job `d03cd686…` |
| 流式播放 | `Range: bytes=0-1023` → `206` | `GET /videos/{id}/stream` |
| 出站控制 | 全程经回环代理；实测出站 ⊆ 注册域清单 | 代理 CONNECT 记录 |

补验过程中按维护规则登记并修复的 3 项：

1. **注册域登记 `365yg.com`（决策 11）**：实测抖音媒体 CDN 出站主机 `v95-aw-default.365yg.com` 未登记被代理 fail-closed 拒绝。证据：真实链接全量下载的出站抓取（回环代理 CONNECT 记录，含 v.douyin.com/www.iesdouyin.com/www.douyin.com/v95-aw-default.365yg.com），锁定版本 `yt-dlp==2026.7.4`。经独立审核门禁**有条件放行**（F-1 要求本附记归档实测证据——本表即该附记）。提交 `606823e`/`dc354a3`/`71636c4`。
2. **分辨率档位语义修正（决策 12）**：竖屏 1080×1920 被"高度 ≤1080"后置校验误拒；修正为"短边 ≤1080 且长边 ≤1920"。实测本视频为 720×1280 竖屏（1080p 档位内）。提交 `d5f1baa`/`854be84`。
3. Cookie 来源合规：用户显式导出、文件域纯净（仅 douyin.com）、大小 9.6KB < 1MB。

## 验收过程中发现并修复的真实缺陷（5 项，均为真实硬件环境才暴露）

1. 隧道段例外（决策 10）：fake-IP 环境 DNS 全量解析到 198.18.0.0/15，代理原保留段拒绝阻断全部下载 → 规范修订 + 实施 + 独立审核放行（`5fff506`、`abdd3d6`、`7bfb7b5`）。
2. CONNECT 请求头未排空：残留 `Host:` 头被当 TLS 数据转发，真实 TLS 服务器回 400 → 修复 + 可证伪回归测试（`9e395c5`）。
3. yt-dlp 子进程找不到 FFmpeg → 静默不合并、产出纯视频"成功" → `--ffmpeg-location` + PATH 双保险 + FFmpeg 缺失 blocked 预检 + 未合并残留检测（`d1ef3ef`）。
4. 抽帧滤镜 `scale=min(640,iw):-2` 逗号未转义（v1.1 潜伏）→ `scale=min(640\,iw):-2`（`0409c5c`）。
5. 内存监测 psutil.NoSuchProcess 竞态（psutil.Error 非 OSError）→ 尽力而为语义（`2072150`）。

## 冻结门禁状态更新

- FFmpeg/ffprobe 物理可用验证：**已满足**（本次安装并实测启用）。
- 注册域清单实测比对：**B站组已实证**（b23.tv、bilibili.com、api.bilibili.com、upos*.bilivideo.com）；**douyin 组已实证**（v.douyin.com、www.iesdouyin.com、www.douyin.com、v95-aw-default.365yg.com——后者触发决策 11 登记）。
- 真实平台独立验收：**B站与抖音均通过**（2026-08-14 抖音补验完成）。
- release_readiness：保持 **blocked**（与 v1.1 政策一致；archive-local acceptance 不等于 release approval）。

## 遗留与后续

- 抖音链接依赖用户 Cookie（反爬机制随平台变化，成功率不保证；失败如实登记）。
- 验收期间产生的失败测试数据已软删除（source `04d4a717…`，纯视频半成品）；成功条目保留于用户库中（B站 1 条、抖音 1 条）。
- 失败作业记录保留为审计轨迹，未删除。
