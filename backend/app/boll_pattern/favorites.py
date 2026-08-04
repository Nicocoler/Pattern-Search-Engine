# -*- coding: utf-8 -*-
"""布林编排命中收藏（快照表，与扫描结果脱钩）。"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from psycopg2.extras import Json

from backend.app.core import db

ENSURE_FAVORITE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pattern_match_favorite (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(12) NOT NULL,
    name VARCHAR(64),
    pattern_id VARCHAR(64) NOT NULL,
    pattern_name VARCHAR(128) NOT NULL,
    period VARCHAR(16) NOT NULL DEFAULT 'daily',
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    matched_states TEXT NOT NULL,
    score NUMERIC(10, 4),
    edge_hits JSONB NOT NULL DEFAULT '[]'::jsonb,
    scan_date DATE,
    window_days INT,
    source_match_id BIGINT,
    note TEXT NOT NULL DEFAULT '',
    favorited_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (code, pattern_id, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS idx_pattern_match_favorite_at
    ON pattern_match_favorite (favorited_at DESC);
"""


def ensure_favorite_table() -> None:
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(ENSURE_FAVORITE_TABLE_SQL)
        conn.commit()


def _parse_edge_hits(raw: Any) -> list:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if raw is None:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else []


def _row_to_item(r: dict) -> dict:
    return {
        "id": r["id"],
        "code": r["code"],
        "name": r.get("name"),
        "pattern_id": r["pattern_id"],
        "pattern_name": r["pattern_name"],
        "period": r.get("period") or "daily",
        "start_date": r["start_date"].isoformat() if r.get("start_date") else None,
        "end_date": r["end_date"].isoformat() if r.get("end_date") else None,
        "matched_states": r.get("matched_states") or "",
        "score": float(r["score"]) if r.get("score") is not None else None,
        "edge_hits": _parse_edge_hits(r.get("edge_hits")),
        "scan_date": r["scan_date"].isoformat() if r.get("scan_date") else None,
        "window_days": r.get("window_days"),
        "source_match_id": r.get("source_match_id"),
        "note": r.get("note") or "",
        "favorited_at": r["favorited_at"].isoformat()
        if isinstance(r.get("favorited_at"), (datetime, date))
        else (str(r["favorited_at"]) if r.get("favorited_at") else None),
        "updated_at": r["updated_at"].isoformat()
        if isinstance(r.get("updated_at"), (datetime, date))
        else (str(r["updated_at"]) if r.get("updated_at") else None),
    }


def list_favorites(*, limit: int = 200, offset: int = 0) -> tuple[list[dict], int]:
    ensure_favorite_table()
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT COUNT(*) AS cnt FROM pattern_match_favorite;")
        total = int(cur.fetchone()["cnt"])
        cur.execute(
            """
            SELECT id, code, name, pattern_id, pattern_name, period,
                   start_date, end_date, matched_states, score, edge_hits,
                   scan_date, window_days, source_match_id, note,
                   favorited_at, updated_at
            FROM pattern_match_favorite
            ORDER BY favorited_at DESC, id DESC
            LIMIT %s OFFSET %s;
            """,
            (limit, offset),
        )
        rows = cur.fetchall() or []
    return [_row_to_item(dict(r)) for r in rows], total


def add_favorite(payload: dict) -> dict:
    """按自然键 upsert 快照；已存在则刷新快照字段，保留原 note（除非本次显式传 note）。"""
    ensure_favorite_table()
    code = str(payload.get("code") or "").lower().strip()
    pattern_id = str(payload.get("pattern_id") or "").strip()
    start_date = payload.get("start_date")
    end_date = payload.get("end_date")
    if not code or not pattern_id or not start_date or not end_date:
        raise ValueError("code / pattern_id / start_date / end_date 必填")

    period = str(payload.get("period") or "daily").strip().lower()
    if period not in ("daily", "weekly", "monthly"):
        period = "daily"

    edge_hits = payload.get("edge_hits") or []
    note = payload.get("note")
    score = payload.get("score")

    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            INSERT INTO pattern_match_favorite (
                code, name, pattern_id, pattern_name, period,
                start_date, end_date, matched_states, score, edge_hits,
                scan_date, window_days, source_match_id, note,
                favorited_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, COALESCE(%s, ''),
                NOW(), NOW()
            )
            ON CONFLICT (code, pattern_id, start_date, end_date) DO UPDATE SET
                name = EXCLUDED.name,
                pattern_name = EXCLUDED.pattern_name,
                period = EXCLUDED.period,
                matched_states = EXCLUDED.matched_states,
                score = EXCLUDED.score,
                edge_hits = EXCLUDED.edge_hits,
                scan_date = EXCLUDED.scan_date,
                window_days = EXCLUDED.window_days,
                source_match_id = EXCLUDED.source_match_id,
                note = CASE
                    WHEN EXCLUDED.note IS NOT NULL AND EXCLUDED.note <> ''
                    THEN EXCLUDED.note
                    ELSE pattern_match_favorite.note
                END,
                updated_at = NOW()
            RETURNING id, code, name, pattern_id, pattern_name, period,
                      start_date, end_date, matched_states, score, edge_hits,
                      scan_date, window_days, source_match_id, note,
                      favorited_at, updated_at;
            """,
            (
                code,
                payload.get("name"),
                pattern_id,
                str(payload.get("pattern_name") or pattern_id)[:128],
                period,
                start_date,
                end_date,
                str(payload.get("matched_states") or ""),
                float(score) if score is not None else None,
                Json(edge_hits),
                payload.get("scan_date"),
                payload.get("window_days"),
                payload.get("source_match_id") or payload.get("id"),
                note if note is not None else None,
            ),
        )
        row = cur.fetchone()
        conn.commit()
    return _row_to_item(dict(row))


def delete_favorite(favorite_id: int) -> bool:
    ensure_favorite_table()
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute("DELETE FROM pattern_match_favorite WHERE id = %s;", (int(favorite_id),))
        n = cur.rowcount or 0
        conn.commit()
    return n > 0


def delete_favorite_by_key(
    code: str,
    pattern_id: str,
    start_date: str,
    end_date: str,
) -> bool:
    ensure_favorite_table()
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(
            """
            DELETE FROM pattern_match_favorite
            WHERE code = %s AND pattern_id = %s AND start_date = %s AND end_date = %s;
            """,
            (code.lower().strip(), pattern_id.strip(), start_date, end_date),
        )
        n = cur.rowcount or 0
        conn.commit()
    return n > 0


def update_favorite_note(favorite_id: int, note: str) -> dict | None:
    ensure_favorite_table()
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            UPDATE pattern_match_favorite
            SET note = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING id, code, name, pattern_id, pattern_name, period,
                      start_date, end_date, matched_states, score, edge_hits,
                      scan_date, window_days, source_match_id, note,
                      favorited_at, updated_at;
            """,
            (note if note is not None else "", int(favorite_id)),
        )
        row = cur.fetchone()
        conn.commit()
    return _row_to_item(dict(row)) if row else None
