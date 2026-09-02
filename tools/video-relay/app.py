"""源知库视频中转服务（决策 22，独立部署物）。

与知识库应用完全独立：上传经 Bearer 密钥、随机 64 位十六进制 token、
短 TTL 自动清理、无目录列举、无路径穿越。仅两个端点：
    POST /upload        multipart file → {"url": "<base>/f/<token>"}
    GET  /f/<token>     回传对应文件（MiMo/Qwen 拉取）

部署要求：公网可达 + HTTPS（由前置反向代理/1Panel OpenResty 提供），
本服务建议只绑定 127.0.0.1 经反代暴露。凭据/内容绝不写入日志。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

SECRET = os.environ.get("VIDEO_RELAY_SECRET", "")
# 必填（加固计划 Task 4a）：返回 URL 只由该值与随机 token 构造，
# 绝不信任 X-Forwarded-Proto/Host 等客户端可伪造的转发头。
PUBLIC_BASE_URL = os.environ.get("VIDEO_RELAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
MAX_BYTES = int(os.environ.get("VIDEO_RELAY_MAX_BYTES", "314572800"))
TTL_SECONDS = int(os.environ.get("VIDEO_RELAY_TTL_SECONDS", "1800"))
TOKEN_DIR = Path(os.environ.get("VIDEO_RELAY_TOKEN_DIR", "/data/tokens"))

TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32,}$")

if not SECRET:
    raise RuntimeError("必须设置 VIDEO_RELAY_SECRET 环境变量")
if not PUBLIC_BASE_URL:
    raise RuntimeError("必须设置 VIDEO_RELAY_PUBLIC_BASE_URL 环境变量（如 https://relay.example.com）")


def secret_matches(authorization: str) -> bool:
    """恒时比较 Bearer 凭据；绝不提前返回泄露长度差异。"""
    expected = f"Bearer {SECRET}"
    left = hashlib.sha256(authorization.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def _cleanup() -> None:
    now = time.time()
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    for path in TOKEN_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime > TTL_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _cleanup_loop() -> None:
    while True:
        _cleanup()
        time.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _cleanup()
    threading.Thread(target=_cleanup_loop, name="token-cleanup", daemon=True).start()
    yield


app = FastAPI(
    title="源知库视频中转",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)) -> dict[str, str]:
    if not secret_matches(request.headers.get("authorization", "")):
        raise HTTPException(status_code=401, detail="unauthorized")
    token = uuid.uuid4().hex + uuid.uuid4().hex
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    destination = TOKEN_DIR / token
    size = 0
    try:
        with destination.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="视频超过中转大小上限")
                target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {"url": f"{PUBLIC_BASE_URL}/f/{token}"}


@app.get("/f/{token}")
def fetch(token: str) -> FileResponse:
    if not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(status_code=404, detail="not_found")
    path = TOKEN_DIR / token
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(path, media_type="video/mp4", headers={"X-Content-Type-Options": "nosniff"})
