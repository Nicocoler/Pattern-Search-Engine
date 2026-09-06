# -*- coding: utf-8 -*-
"""通达信云端桥接：网页 push 任务，本机助手 pull 后写入 .blk。"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

# 单租户默认口令；云部署请在 .env 设置 TDX_BRIDGE_TOKEN
_DEFAULT_TOKEN = "pse-tdx-bridge"
_TTL_SEC = 30 * 60

_lock = threading.Lock()
_pending: dict[str, Any] | None = None


def bridge_token() -> str:
    return (os.getenv("TDX_BRIDGE_TOKEN") or _DEFAULT_TOKEN).strip()


def check_token(token: str | None) -> bool:
    return bool(token) and token.strip() == bridge_token()


def push_job(
    codes: list[str],
    *,
    block_name: str = "PSE布林",
    block_abbr: str = "PSE",
) -> dict[str, Any]:
    cleaned = [c for c in (str(x).strip() for x in codes) if c]
    if not cleaned:
        raise ValueError("codes 不能为空")
    job = {
        "id": uuid.uuid4().hex[:12],
        "codes": cleaned,
        "block_name": (block_name or "PSE布林").strip(),
        "block_abbr": (block_abbr or "PSE").strip().upper(),
        "created_at": time.time(),
        "code_count": len(dict.fromkeys(cleaned)),
    }
    with _lock:
        global _pending
        _pending = job
    return {
        "id": job["id"],
        "code_count": job["code_count"],
        "block_name": job["block_name"],
        "block_abbr": job["block_abbr"],
    }


def peek_job() -> dict[str, Any] | None:
    global _pending
    with _lock:
        job = _pending
        if not job:
            return None
        if time.time() - float(job["created_at"]) > _TTL_SEC:
            _pending = None
            return None
        return {
            "id": job["id"],
            "codes": list(job["codes"]),
            "block_name": job["block_name"],
            "block_abbr": job["block_abbr"],
            "code_count": job["code_count"],
            "created_at": job["created_at"],
        }


def ack_job(job_id: str) -> bool:
    with _lock:
        global _pending
        if _pending and _pending.get("id") == job_id:
            _pending = None
            return True
        return False


def status() -> dict[str, Any]:
    with _lock:
        job = _pending
        pending = None
        if job and time.time() - float(job["created_at"]) <= _TTL_SEC:
            pending = {
                "id": job["id"],
                "code_count": job["code_count"],
                "block_name": job["block_name"],
                "block_abbr": job["block_abbr"],
                "age_sec": int(time.time() - float(job["created_at"])),
            }
        return {
            "has_pending": pending is not None,
            "pending": pending,
            "token_configured": bool(os.getenv("TDX_BRIDGE_TOKEN")),
            "ttl_sec": _TTL_SEC,
        }
