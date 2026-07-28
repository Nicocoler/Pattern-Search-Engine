# -*- coding: utf-8 -*-
"""
布林编排仓储：DB 为权威源；YAML 仅补缺种子。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from psycopg2.extras import Json

from backend.app.boll_pattern.loader import (
    load_boll_patterns,
    normalize_zone_thresholds,
    thresholds_to_jsonable,
)
from backend.app.boll_pattern.zone import DEFAULT_ZONE_THRESHOLDS
from backend.app.core import db

logger = logging.getLogger("BollPatternRepo")

ENSURE_PATTERN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS boll_pattern_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    zone_thresholds JSONB NOT NULL,
    denoise_min_len INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS boll_patterns (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    regex TEXT NOT NULL,
    min_total_days INT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    zone_thresholds JSONB,
    denoise_min_len INT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""


def ensure_pattern_tables() -> None:
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(ENSURE_PATTERN_TABLES_SQL)
        conn.commit()


def _row_thresholds(raw: Any) -> dict[str, tuple[float, float]] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    return normalize_zone_thresholds(raw)


def _pattern_from_row(row: dict) -> dict[str, Any]:
    zt = row.get("zone_thresholds")
    return {
        "id": row["id"],
        "name": row["name"],
        "regex": row["regex"],
        "min_total_days": int(row["min_total_days"] or 0),
        "enabled": bool(row["enabled"]),
        "zone_thresholds": _row_thresholds(zt) if zt is not None else None,
        "denoise_min_len": (
            int(row["denoise_min_len"]) if row.get("denoise_min_len") is not None else None
        ),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def get_settings() -> dict[str, Any]:
    ensure_pattern_tables()
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            "SELECT zone_thresholds, denoise_min_len, updated_at FROM boll_pattern_settings WHERE id = 1;"
        )
        row = cur.fetchone()
    if not row:
        # 极端空库：返回代码默认，并落库
        settings = {
            "zone_thresholds": dict(DEFAULT_ZONE_THRESHOLDS),
            "denoise_min_len": 0,
        }
        update_settings(settings["zone_thresholds"], settings["denoise_min_len"])
        return {**settings, "updated_at": None}
    return {
        "zone_thresholds": normalize_zone_thresholds(row["zone_thresholds"]),
        "denoise_min_len": int(row["denoise_min_len"] or 0),
        "updated_at": row.get("updated_at"),
    }


def update_settings(
    zone_thresholds: dict | None,
    denoise_min_len: int | None,
) -> dict[str, Any]:
    ensure_pattern_tables()
    current = None
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            "SELECT zone_thresholds, denoise_min_len FROM boll_pattern_settings WHERE id = 1;"
        )
        current = cur.fetchone()

    if current:
        zt = (
            normalize_zone_thresholds(zone_thresholds)
            if zone_thresholds is not None
            else normalize_zone_thresholds(current["zone_thresholds"])
        )
        dnl = (
            int(denoise_min_len)
            if denoise_min_len is not None
            else int(current["denoise_min_len"] or 0)
        )
    else:
        zt = normalize_zone_thresholds(zone_thresholds) if zone_thresholds else dict(DEFAULT_ZONE_THRESHOLDS)
        dnl = int(denoise_min_len or 0)

    payload = Json(thresholds_to_jsonable(zt))
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(
            """
            INSERT INTO boll_pattern_settings (id, zone_thresholds, denoise_min_len, updated_at)
            VALUES (1, %s, %s, NOW())
            ON CONFLICT (id) DO UPDATE SET
                zone_thresholds = EXCLUDED.zone_thresholds,
                denoise_min_len = EXCLUDED.denoise_min_len,
                updated_at = NOW();
            """,
            (payload, dnl),
        )
        conn.commit()
    return get_settings()


def list_patterns(include_disabled: bool = True) -> list[dict[str, Any]]:
    ensure_pattern_tables()
    sql = """
        SELECT id, name, regex, min_total_days, enabled,
               zone_thresholds, denoise_min_len, created_at, updated_at
        FROM boll_patterns
    """
    if not include_disabled:
        sql += " WHERE enabled = TRUE"
    sql += " ORDER BY id ASC;"
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(sql)
        rows = cur.fetchall()
    return [_pattern_from_row(dict(r)) for r in rows]


def get_pattern(pattern_id: str) -> dict[str, Any] | None:
    ensure_pattern_tables()
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            SELECT id, name, regex, min_total_days, enabled,
                   zone_thresholds, denoise_min_len, created_at, updated_at
            FROM boll_patterns WHERE id = %s;
            """,
            (pattern_id,),
        )
        row = cur.fetchone()
    return _pattern_from_row(dict(row)) if row else None


def validate_regex(regex: str) -> None:
    if not regex or not str(regex).strip():
        raise ValueError("regex 不能为空")
    try:
        re.compile(regex)
    except re.error as e:
        raise ValueError(f"非法正则: {e}") from e


def create_pattern(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_pattern_tables()
    pid = str(payload.get("id", "")).strip()
    if not pid:
        raise ValueError("id 不能为空")
    if get_pattern(pid):
        raise ValueError(f"编排 id 已存在: {pid}")
    name = str(payload.get("name") or pid).strip()
    regex = str(payload.get("regex") or "").strip()
    validate_regex(regex)
    min_days = int(payload.get("min_total_days") or 0)
    enabled = bool(payload.get("enabled", True))
    zt_raw = payload.get("zone_thresholds")
    zt = normalize_zone_thresholds(zt_raw) if zt_raw is not None else None
    dnl = payload.get("denoise_min_len")
    dnl_val = int(dnl) if dnl is not None else None

    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(
            """
            INSERT INTO boll_patterns (
                id, name, regex, min_total_days, enabled,
                zone_thresholds, denoise_min_len, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW());
            """,
            (
                pid,
                name,
                regex,
                min_days,
                enabled,
                Json(thresholds_to_jsonable(zt)) if zt is not None else None,
                dnl_val,
            ),
        )
        conn.commit()
    return get_pattern(pid)  # type: ignore


def update_pattern(pattern_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    ensure_pattern_tables()
    existing = get_pattern(pattern_id)
    if not existing:
        raise ValueError(f"编排不存在: {pattern_id}")
    if "id" in payload and str(payload["id"]).strip() != pattern_id:
        raise ValueError("不允许修改编排 id")

    name = str(payload["name"]).strip() if "name" in payload else existing["name"]
    regex = str(payload["regex"]).strip() if "regex" in payload else existing["regex"]
    validate_regex(regex)
    min_days = (
        int(payload["min_total_days"])
        if "min_total_days" in payload
        else existing["min_total_days"]
    )
    enabled = bool(payload["enabled"]) if "enabled" in payload else existing["enabled"]

    if "zone_thresholds" in payload:
        zt_raw = payload["zone_thresholds"]
        zt = normalize_zone_thresholds(zt_raw) if zt_raw is not None else None
    else:
        zt = existing["zone_thresholds"]

    if "denoise_min_len" in payload:
        dnl = payload["denoise_min_len"]
        dnl_val = int(dnl) if dnl is not None else None
    else:
        dnl_val = existing["denoise_min_len"]

    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(
            """
            UPDATE boll_patterns SET
                name = %s,
                regex = %s,
                min_total_days = %s,
                enabled = %s,
                zone_thresholds = %s,
                denoise_min_len = %s,
                updated_at = NOW()
            WHERE id = %s;
            """,
            (
                name,
                regex,
                min_days,
                enabled,
                Json(thresholds_to_jsonable(zt)) if zt is not None else None,
                dnl_val,
                pattern_id,
            ),
        )
        conn.commit()
    return get_pattern(pattern_id)  # type: ignore


def disable_pattern(pattern_id: str) -> dict[str, Any]:
    return update_pattern(pattern_id, {"enabled": False})


def effective_config(pattern: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """稀疏合并：编排覆盖为空则用全局默认。"""
    settings = settings or get_settings()
    zt = pattern.get("zone_thresholds")
    if zt is None:
        zt = settings["zone_thresholds"]
    else:
        zt = normalize_zone_thresholds(zt)
    dnl = pattern.get("denoise_min_len")
    if dnl is None:
        dnl = int(settings["denoise_min_len"])
    else:
        dnl = int(dnl)
    return {
        "id": pattern["id"],
        "name": pattern["name"],
        "regex": pattern["regex"],
        "min_total_days": int(pattern.get("min_total_days") or 0),
        "enabled": bool(pattern.get("enabled", True)),
        "zone_thresholds": zt,
        "denoise_min_len": dnl,
    }


def list_enabled_effective() -> list[dict[str, Any]]:
    settings = get_settings()
    patterns = list_patterns(include_disabled=False)
    out = []
    for p in patterns:
        if not p.get("regex"):
            continue
        out.append(effective_config(p, settings))
    return out


def serialize_pattern_for_api(pattern: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    eff = effective_config(pattern, settings)
    return {
        "id": pattern["id"],
        "name": pattern["name"],
        "regex": pattern["regex"],
        "min_total_days": pattern["min_total_days"],
        "enabled": pattern["enabled"],
        "zone_thresholds": (
            thresholds_to_jsonable(pattern["zone_thresholds"])
            if pattern.get("zone_thresholds") is not None
            else None
        ),
        "denoise_min_len": pattern.get("denoise_min_len"),
        "effective": {
            "zone_thresholds": thresholds_to_jsonable(eff["zone_thresholds"]),
            "denoise_min_len": eff["denoise_min_len"],
        },
        "created_at": (
            pattern["created_at"].isoformat()
            if hasattr(pattern.get("created_at"), "isoformat")
            else pattern.get("created_at")
        ),
        "updated_at": (
            pattern["updated_at"].isoformat()
            if hasattr(pattern.get("updated_at"), "isoformat")
            else pattern.get("updated_at")
        ),
    }


def seed_from_yaml(yaml_path: str | None = None) -> dict[str, Any]:
    """
    补缺不覆盖：
    - settings 仅当不存在时从 YAML 灌入
    - patterns 仅插入库中缺失的 id
    """
    ensure_pattern_tables()
    cfg = load_boll_patterns(yaml_path)
    inserted_settings = False
    inserted_patterns: list[str] = []

    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT 1 FROM boll_pattern_settings WHERE id = 1;")
        has_settings = cur.fetchone() is not None

    if not has_settings:
        update_settings(cfg["zone_thresholds"], cfg["denoise_min_len"])
        inserted_settings = True
        logger.info("已从 YAML 种子灌入全局编排尺子")

    existing_ids = {p["id"] for p in list_patterns(include_disabled=True)}
    for p in cfg["patterns"]:
        if p["id"] in existing_ids:
            continue
        create_pattern({
            "id": p["id"],
            "name": p["name"],
            "regex": p["regex"],
            "min_total_days": p["min_total_days"],
            "enabled": p["enabled"],
            "zone_thresholds": None,
            "denoise_min_len": None,
        })
        inserted_patterns.append(p["id"])
        logger.info("已从 YAML 种子补入编排: %s", p["id"])

    return {
        "inserted_settings": inserted_settings,
        "inserted_patterns": inserted_patterns,
    }
