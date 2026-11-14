# -*- coding: utf-8 -*-
"""个股收藏：按代码收藏，供通达信 PSE收藏板块同步。"""

from __future__ import annotations

from datetime import date, datetime

from backend.app.core import db

ENSURE_STOCK_FAVORITE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_favorite (
    code VARCHAR(12) PRIMARY KEY,
    name VARCHAR(64),
    note TEXT NOT NULL DEFAULT '',
    favorited_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stock_favorite_at
    ON stock_favorite (favorited_at DESC);
"""


def ensure_stock_favorite_table() -> None:
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute(ENSURE_STOCK_FAVORITE_TABLE_SQL)
        conn.commit()


def _row_to_item(r: dict) -> dict:
    return {
        "code": r["code"],
        "name": r.get("name") or "",
        "note": r.get("note") or "",
        "favorited_at": r["favorited_at"].isoformat()
        if isinstance(r.get("favorited_at"), (datetime, date))
        else (str(r["favorited_at"]) if r.get("favorited_at") else None),
        "updated_at": r["updated_at"].isoformat()
        if isinstance(r.get("updated_at"), (datetime, date))
        else (str(r["updated_at"]) if r.get("updated_at") else None),
    }


def list_stock_favorites(*, limit: int = 500, offset: int = 0) -> tuple[list[dict], int]:
    ensure_stock_favorite_table()
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT COUNT(*) AS cnt FROM stock_favorite;")
        total = int(cur.fetchone()["cnt"])
        cur.execute(
            """
            SELECT code, name, note, favorited_at, updated_at
            FROM stock_favorite
            ORDER BY favorited_at DESC, code ASC
            LIMIT %s OFFSET %s;
            """,
            (limit, offset),
        )
        rows = cur.fetchall() or []
    return [_row_to_item(dict(r)) for r in rows], total


def get_stock_favorite(code: str) -> dict | None:
    ensure_stock_favorite_table()
    c = str(code or "").lower().strip()
    if not c:
        return None
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            SELECT code, name, note, favorited_at, updated_at
            FROM stock_favorite WHERE code = %s;
            """,
            (c,),
        )
        row = cur.fetchone()
    return _row_to_item(dict(row)) if row else None


def add_stock_favorite(*, code: str, name: str | None = None, note: str | None = None) -> dict:
    ensure_stock_favorite_table()
    c = str(code or "").lower().strip()
    if not c:
        raise ValueError("code 必填")
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            INSERT INTO stock_favorite (code, name, note, favorited_at, updated_at)
            VALUES (%s, %s, COALESCE(%s, ''), NOW(), NOW())
            ON CONFLICT (code) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, stock_favorite.name),
                note = CASE
                    WHEN EXCLUDED.note IS NOT NULL AND EXCLUDED.note <> ''
                    THEN EXCLUDED.note
                    ELSE stock_favorite.note
                END,
                updated_at = NOW()
            RETURNING code, name, note, favorited_at, updated_at;
            """,
            (c, (name or "").strip() or None, note if note is not None else None),
        )
        row = cur.fetchone()
        conn.commit()
    return _row_to_item(dict(row))


def delete_stock_favorite(code: str) -> bool:
    ensure_stock_favorite_table()
    c = str(code or "").lower().strip()
    if not c:
        return False
    with db.db_cursor(dict_cursor=False) as (conn, cur):
        cur.execute("DELETE FROM stock_favorite WHERE code = %s;", (c,))
        n = cur.rowcount or 0
        conn.commit()
    return n > 0


def list_stock_favorite_codes(*, limit: int = 500) -> list[str]:
    items, _ = list_stock_favorites(limit=limit, offset=0)
    return [str(x["code"]) for x in items if x.get("code")]
