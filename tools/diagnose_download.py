"""下载链路诊断工具（tools/diagnose_download.py）

用法（仓库根目录，使用应用 venv）：
    .venv/Scripts/python tools/diagnose_download.py <URL> <platform> [--cookie] [--download]

对受限链接下载通道做四分类定位（v1.7.0 起，源于 2026-09-04 抖音反爬更新
导致的下载故障排查）：
  1. 链接/平台问题   —— URL 校验不通过（白名单、格式）
  2. Cookie 问题     —— Cookie 文件缺失/超限，或 yt-dlp 报"需要新鲜 Cookie"
  3. 代理拦截        —— 回环过滤代理拒绝了某个域名（注册域白名单缺口）
  4. 版本过时        —— yt-dlp 版本落后上游（抖音反爬参数频繁变化，
                        yt-dlp 按日历版本高频修复，需跟随升级）

本工具与下载作业走完全相同的受限通道（回环过滤代理 + 锁定版 yt-dlp +
平台 Cookie），stderr 仅输出到本机终端、不落日志；诊断结论不含 Cookie 值。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.adapters.downloader import DownloadInputInvalid, DownloadUnavailable, YtDlpDownloader  # noqa: E402
from app.domain.models import validate_download_url  # noqa: E402

PROXY_FOR_UPSTREAM_CHECK = "http://127.0.0.1:7897"


def _cookie_names_and_expiry(path: Path) -> list[tuple[str, int]]:
    import time

    rows: list[tuple[str, int]] = []
    now = int(time.time())
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            try:
                rows.append((parts[5], int(parts[4]) if parts[4] != "0" else 0))
            except ValueError:
                continue
    expired = [name for name, expiry in rows if 0 < expiry < now]
    return sorted({name for name, _ in rows}), expired


def check_version(python: str = sys.executable) -> tuple[str, str | None]:
    """返回 (已安装版本, 上游最新版本或 None)。先直连后代理查询 PyPI。"""
    installed = subprocess.run(
        [python, "-m", "pip", "show", "yt-dlp"], capture_output=True, text=True
    ).stdout
    version = next((line.split(":", 1)[1].strip() for line in installed.splitlines() if line.startswith("Version:")), "?")
    latest = None
    for proxies in (None, {"https": PROXY_FOR_UPSTREAM_CHECK, "http": PROXY_FOR_UPSTREAM_CHECK}):
        try:
            request = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json", headers={"User-Agent": "yuanzhiku-diagnose"})
            with urllib.request.urlopen(request, timeout=15, proxies=proxies) as response:
                latest = json.load(response)["info"]["version"]
            break
        except Exception:
            continue
    return version, latest


def main() -> int:
    parser = argparse.ArgumentParser(description="受限链接下载通道四分类诊断")
    parser.add_argument("url")
    parser.add_argument("platform", choices=("bilibili", "douyin"))
    parser.add_argument("--cookie", action="store_true", help="使用已导入的平台 Cookie")
    parser.add_argument("--download", action="store_true", help="额外做一次完整下载测试（默认只探测元数据）")
    args = parser.parse_args()

    print("== 1. 链接与平台校验 ==")
    try:
        validate_download_url(args.url, args.platform)
        print("通过：URL 命中平台白名单且格式合法")
    except ValueError as exc:
        print(f"诊断：链接/平台问题 —— {exc}")
        return 1

    downloader = YtDlpDownloader(cookie_resolver=lambda platform: REPO_ROOT / f"data/state/download/cookies/{platform}.txt")
    cookie_path: Path | None = None
    if args.cookie:
        cookie_path = REPO_ROOT / f"data/state/download/cookies/{args.platform}.txt"
        if not cookie_path.is_file() or cookie_path.stat().st_size > 1024 * 1024:
            print("诊断：Cookie 问题 —— 平台 Cookie 文件缺失或超 1MB，请先在设置页导入")
            return 1
        names, expired = _cookie_names_and_expiry(cookie_path)
        print(f"Cookie 文件存在（{len(names)} 个键，含登录态={'sessionid' in names}）")
        if expired:
            print(f"注意：以下 Cookie 已过期（不含值）：{', '.join(expired)}")

    print("== 2. 元数据探测（与下载同受限通道） ==")
    proxy_denied: dict[str, int] = {}
    probe_ok = False
    try:
        metadata = downloader.probe_metadata(args.url, args.platform, use_cookie=args.cookie)
        probe_ok = True
        print(f"通过：title={metadata.get('title')!r} author={metadata.get('author')!r}")
    except DownloadInputInvalid as exc:
        reason = exc.args[0] if exc.args else "failed"
        print(f"探测失败（内部码 {reason}）——继续用底层 yt-dlp 复现取真实原因")
    except DownloadUnavailable as exc:
        print(f"诊断：工具不可用 —— {exc}（yt-dlp/FFmpeg 缺失或代理启动失败）")
        return 1

    print("== 3. 底层复现（保留 yt-dlp stderr，观察代理拦截） ==")
    downloader = YtDlpDownloader(cookie_resolver=lambda platform: REPO_ROOT / f"data/state/download/cookies/{platform}.txt")
    proxy = downloader._proxy_factory(args.platform)
    port = proxy.start()
    workspace = Path(tempfile.mkdtemp(prefix="diagnose-download-"))
    try:
        command = downloader._base_command(port) + [
            *(["--cookies", str(cookie_path)] if cookie_path is not None else []),
            *(["--skip-download"] if not args.download else ["--merge-output-format", "mp4", "--remux-video", "mp4", "-S", "res:1080", "-o", str(workspace / "video.%(ext)s")]),
            args.url,
        ]
        env = downloader._subprocess_environment()
        process = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, env=env, timeout=900,
        )
        stderr = process.stderr or ""
        proxy_denied = dict(proxy.denied_hosts())
        print(f"yt-dlp 退出码 {process.returncode}")
        if proxy_denied:
            print(f"诊断：代理拦截 —— 以下域名不在注册域白名单，需扩充 {args.platform} 注册域清单：{sorted(proxy_denied)}")
            return 1
        print("代理未拦截任何域名（白名单无缺口）")
        if process.returncode == 0:
            produced = [item.name for item in workspace.iterdir()]
            print(f"通过：{'下载成功，产物 ' + str(produced) if args.download else '元数据提取成功'}")
            if not probe_ok:
                print("注意：步骤 2 的探测偶发失败而本次复现成功——抖音反爬具有概率性，")
                print("作业层遇到时重试即可；若高频出现请跟随升级 yt-dlp。")
                probe_ok = True
        else:
            tail = "\n".join(stderr.splitlines()[-8:])
            print("yt-dlp stderr 尾部：")
            print(tail)
            lowered = stderr.lower()
            if "fresh cookies" in lowered or "403" in stderr:
                print("诊断：反爬/Cookie 问题 —— 平台接口拒绝当前请求。Cookie 未过期时通常是")
                print("平台更新了反爬策略，而 yt-dlp 版本过时：跟随升级 yt-dlp 后重试。")
            elif "unsupported url" in lowered or "no video" in lowered:
                print("诊断：链接问题 —— 平台页面结构变化或链接指向不支持的内容")
            else:
                print("诊断：请把上述 stderr 提交维护者分析")
    finally:
        proxy.close()

    print("== 4. 版本检查 ==")
    installed, latest = check_version()
    print(f"已安装 yt-dlp {installed}；上游最新 {latest or '未知（查询失败）'}")
    if latest and installed != latest:
        print(f"诊断：版本过时 —— yt-dlp 按日历版本高频修复平台反爬，建议升级到 {latest}")
        return 1
    if probe_ok:
        print("结论：链路各环节正常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
