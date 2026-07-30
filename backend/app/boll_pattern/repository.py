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

from backend.app.boll_pattern.edges import normalize_edges, validate_pattern_edges
from backend.app.boll_pattern.loader import (
    load_boll_patterns,
    normalize_zone_thresholds,
    thresholds_to_jsonable,
)
from backend.app.boll_pattern.zone import DEFAULT_ZONE_THRESHOLDS
from backend.app.core import db
from backend.app.core.timeutil import isoformat_beijing

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
    edges JSONB NOT NULL DEFAULT '[]'::jsonb,
    period VARCHAR(16) NOT NULL DEFAULT 'daily',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

VALID_BAR_PERIODS = frozenset({"daily", "weekly", "monthly"})


def normalize_bar_period(raw: Any, *, default: str = "daily") -> str:
    p = str(raw or default).strip().lower()
    if p not in VALID_BAR_PERIODS:
        raise ValueError("period 须为 daily | weekly | monthly")
    return p


def ensure_pattern_tables() -> None:
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(ENSURE_PATTERN_TABLES_SQL)
        # 旧库补列（CREATE IF NOT EXISTS 不会加新列）
        cur.execute(
            """
            ALTER TABLE boll_patterns
            ADD COLUMN IF NOT EXISTS edges JSONB NOT NULL DEFAULT '[]'::jsonb;
            """
        )
        cur.execute(
            """
            ALTER TABLE boll_patterns
            ADD COLUMN IF NOT EXISTS period VARCHAR(16) NOT NULL DEFAULT 'daily';
            """
        )
        cur.execute(
            """
            UPDATE boll_patterns
            SET period = 'daily'
            WHERE period IS NULL OR TRIM(period) = '' OR period NOT IN ('daily', 'weekly', 'monthly');
            """
        )
        conn.commit()


def _row_thresholds(raw: Any) -> dict[str, tuple[float, float]] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    return normalize_zone_thresholds(raw)


def _row_edges(raw: Any) -> list[dict[str, str]]:
    """读路径只做轻量规范化；写路径才与 regex 交叉校验。"""
    try:
        return normalize_edges(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _pattern_from_row(row: dict) -> dict[str, Any]:
    zt = row.get("zone_thresholds")
    try:
        period = normalize_bar_period(row.get("period"), default="daily")
    except ValueError:
        period = "daily"
    return {
        "id": row["id"],
        "name": row["name"],
        "regex": row["regex"],
        "min_total_days": int(row["min_total_days"] or 0),
        "enabled": bool(row["enabled"]),
        "period": period,
        "zone_thresholds": _row_thresholds(zt) if zt is not None else None,
        "denoise_min_len": (
            int(row["denoise_min_len"]) if row.get("denoise_min_len") is not None else None
        ),
        "edges": _row_edges(row.get("edges")),
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
        SELECT id, name, regex, min_total_days, enabled, period,
               zone_thresholds, denoise_min_len, edges, created_at, updated_at
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
            SELECT id, name, regex, min_total_days, enabled, period,
                   zone_thresholds, denoise_min_len, edges, created_at, updated_at
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


def _validate_edges_for_period(period: str, regex: str, edges_raw: Any) -> list[dict[str, str]]:
    edges = validate_pattern_edges(regex, edges_raw)
    if period != "daily" and edges:
        raise ValueError("周/月编排禁止配置 edges（ADR 0006）；仅日线支持 limit_up 等边条件")
    return edges


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
    period = normalize_bar_period(payload.get("period"), default="daily")
    edges = _validate_edges_for_period(period, regex, payload.get("edges"))
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
                id, name, regex, min_total_days, enabled, period,
                zone_thresholds, denoise_min_len, edges, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW());
            """,
            (
                pid,
                name,
                regex,
                min_days,
                enabled,
                period,
                Json(thresholds_to_jsonable(zt)) if zt is not None else None,
                dnl_val,
                Json(edges),
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
    if "period" in payload and payload["period"] is not None:
        new_p = normalize_bar_period(payload["period"])
        if new_p != existing["period"]:
            raise ValueError("不允许修改编排 period（ADR 0006）；请复制新建")

    name = str(payload["name"]).strip() if "name" in payload else existing["name"]
    regex = str(payload["regex"]).strip() if "regex" in payload else existing["regex"]
    validate_regex(regex)
    period = existing["period"]
    if "edges" in payload:
        edges = _validate_edges_for_period(period, regex, payload.get("edges"))
    else:
        # regex 变更时仍须与既有 edges 交叉校验
        edges = _validate_edges_for_period(period, regex, existing.get("edges") or [])
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
                edges = %s,
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
                Json(edges),
                pattern_id,
            ),
        )
        conn.commit()
    # 命中表 pattern_name 为冗余展示字段：改名后立刻对齐
    try:
        sync_match_pattern_names(pattern_id)
    except Exception as ex:
        logger.warning("同步命中表编排名失败 pattern_id=%s: %s", pattern_id, ex)
    return get_pattern(pattern_id)  # type: ignore


def sync_match_pattern_names(pattern_id: str | None = None) -> int:
    """
    用 boll_patterns.name 刷新 pattern_match_result.pattern_name。
    pattern_id 给定则只刷该编排；否则刷全部有目录行的命中。
    """
    ensure_pattern_tables()
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        # 命中表可能尚未创建（从未扫描）
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'pattern_match_result';
            """
        )
        if cur.fetchone() is None:
            return 0
        if pattern_id:
            cur.execute(
                """
                UPDATE pattern_match_result AS m
                SET pattern_name = p.name, updated_at = NOW()
                FROM boll_patterns AS p
                WHERE m.pattern_id = p.id
                  AND p.id = %s
                  AND m.pattern_name IS DISTINCT FROM p.name;
                """,
                (pattern_id,),
            )
        else:
            cur.execute(
                """
                UPDATE pattern_match_result AS m
                SET pattern_name = p.name, updated_at = NOW()
                FROM boll_patterns AS p
                WHERE m.pattern_id = p.id
                  AND m.pattern_name IS DISTINCT FROM p.name;
                """
            )
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        conn.commit()
    return int(n)


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
        "period": normalize_bar_period(pattern.get("period"), default="daily"),
        "zone_thresholds": zt,
        "denoise_min_len": dnl,
        "edges": list(pattern.get("edges") or []),
    }


def list_enabled_effective(period: str | None = None) -> list[dict[str, Any]]:
    settings = get_settings()
    patterns = list_patterns(include_disabled=False)
    want = normalize_bar_period(period) if period is not None else None
    out = []
    for p in patterns:
        if not p.get("regex"):
            continue
        eff = effective_config(p, settings)
        if want is not None and eff["period"] != want:
            continue
        out.append(eff)
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
        "period": pattern.get("period") or "daily",
        "zone_thresholds": (
            thresholds_to_jsonable(pattern["zone_thresholds"])
            if pattern.get("zone_thresholds") is not None
            else None
        ),
        "denoise_min_len": pattern.get("denoise_min_len"),
        "edges": list(pattern.get("edges") or []),
        "effective": {
            "zone_thresholds": thresholds_to_jsonable(eff["zone_thresholds"]),
            "denoise_min_len": eff["denoise_min_len"],
        },
        "created_at": isoformat_beijing(pattern.get("created_at")),
        "updated_at": isoformat_beijing(pattern.get("updated_at")),
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
            "period": p.get("period") or "daily",
            "zone_thresholds": None,
            "denoise_min_len": None,
        })
        inserted_patterns.append(p["id"])
        logger.info("已从 YAML 种子补入编排: %s", p["id"])

    return {
        "inserted_settings": inserted_settings,
        "inserted_patterns": inserted_patterns,
    }
