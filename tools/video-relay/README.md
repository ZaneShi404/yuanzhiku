# 源知库视频中转服务（tools/video-relay）

v1.5 视频直送的「自备中转」部署物（需求 `docs/v1-5-requirements.md` 决策 22）。
作用：知识库应用把待直送视频上传到这里，换取一个**随机 token 的临时公开
URL** 交给 MiMo/Qwen 拉取；TTL 到期自动删除。与知识库应用完全独立，
不持有任何库内容或凭据（除上传密钥）。

## 安全模型

- 上传必须带 `Authorization: Bearer <VIDEO_RELAY_SECRET>`（恒时比较）；
- 返回的拉取 URL 只由必填的 `VIDEO_RELAY_PUBLIC_BASE_URL` 与随机 token
  构造，**不信任 `X-Forwarded-*` 等客户端可伪造的转发头**；
- token 为 64 位十六进制随机串（能力 URL），仅能定位本服务存储的单个文件，
  无目录列举、无路径穿越（token 只允许 `[0-9a-f]{32,}` 后按文件名映射）；
- 文件 TTL（默认 30 分钟）到期由后台线程清理；服务重启即清理过期文件；
- 大小上限 `VIDEO_RELAY_MAX_BYTES`（默认 300MB，对齐 MiMo URL 上限）；
- 凭据与文件内容绝不写入日志。

## 部署（Docker）

前置：服务器公网可达 + **域名与 HTTPS**（MiMo 拉取要求 HTTPS；本服务本身
只监听 127.0.0.1:8000，HTTPS 由前置反代终结）。

```bash
cd tools/video-relay
VIDEO_RELAY_SECRET='<生成一个高强度随机串>' \
VIDEO_RELAY_PUBLIC_BASE_URL='https://你的域名' \
  docker compose up -d --build
```

- 生成密钥：`openssl rand -hex 32`；
- `VIDEO_RELAY_PUBLIC_BASE_URL` 必填：对外可达的固定 HTTPS 地址（与前置反代域名一致）；改它会立即使已发出去的旧 URL 失效（旧文件仍按 TTL 清理）；
- 修改环境变量：`docker compose down && VIDEO_RELAY_SECRET=... VIDEO_RELAY_PUBLIC_BASE_URL=... docker compose up -d`（改密钥会使旧 token 全部失效——上传鉴权密钥变了，旧文件仍按 TTL 清理）。

### 反向代理（1Panel / OpenResty）

1Panel 中安装 OpenResty → 创建网站 → 绑定你的域名 → 反向代理
`https://你的域名` → `http://127.0.0.1:8000`，并转发以下头：

```
X-Forwarded-Proto: https
X-Forwarded-Host: 你的域名
```

→ 用 1Panel 一键申请 Let's Encrypt 证书。完成后浏览器访问
`https://你的域名/f/0000` 应返回 404（表示服务可达）。

## 应用侧配置

知识库设置页「媒体 AI → 视频直送 → 自备中转」：

- `ai_video_relay_base_url` = `https://你的域名`
- `ai_video_relay_secret` = 与 `VIDEO_RELAY_SECRET` 相同

配置后视频直送优先经中转 URL（MiMo 可吃满 300MB 上限，免 base64 重编码）。

## 验证

```bash
# 上传（本机/服务器均可）：
curl -X POST https://你的域名/upload \
  -H "Authorization: Bearer $VIDEO_RELAY_SECRET" \
  -F "file=@sample.mp4"
# → {"url": "https://你的域名/f/<64位hex>"}

# 拉取：
curl -I https://你的域名/f/<token>   # 200 + video/mp4

# 未授权上传应 401；过期后拉取应 404。
```
