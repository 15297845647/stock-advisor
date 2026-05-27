"""简易 token 认证 — 密码换 token，token 校验中间件"""

import hashlib
import secrets
import time

from fastapi import HTTPException, Request

from agent.config import ADMIN_PASSWORD

# 内存 token 存储（进程级，重启失效 → 重新登录）
_active_tokens: dict[str, float] = {}
_TOKEN_TTL = 86400  # 24h


def login(password: str) -> str:
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="密码错误")
    token = secrets.token_hex(32)
    _active_tokens[token] = time.time()
    return token


def verify_token(request: Request) -> None:
    """从 header 或 cookie 提取 token 并校验"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        token = request.cookies.get("admin_token", "")
    if not token or token not in _active_tokens:
        raise HTTPException(status_code=401, detail="未登录或 token 已过期")
    if time.time() - _active_tokens[token] > _TOKEN_TTL:
        _active_tokens.pop(token, None)
        raise HTTPException(status_code=401, detail="token 已过期，请重新登录")
