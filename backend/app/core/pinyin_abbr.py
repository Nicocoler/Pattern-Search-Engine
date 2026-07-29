# -*- coding: utf-8 -*-
"""股票简称 → 拼音首字母缩写（通达信式）。"""

from __future__ import annotations

from pypinyin import Style, lazy_pinyin

from backend.app.core import db

_PINYIN_SCHEMA_READY = False


def name_to_pinyin_abbr(name: str) -> str:
    """
    汉字取拼音首字母；ASCII 字母/数字原样保留（小写）；其余符号丢弃。
    例：思源电器→sydt，*ST宁科→stnk，万科A→wka。
    """
    raw = (name or "").strip()
    if not raw:
        return ""

    parts: list[str] = []
    for ch in raw:
        if "\u4e00" <= ch <= "\u9fff":
            initials = lazy_pinyin(ch, style=Style.FIRST_LETTER)
            if initials and initials[0]:
                parts.append(str(initials[0]).lower())
        elif ch.isascii() and ch.isalnum():
            parts.append(ch.lower())
    return "".join(parts)


def ensure_stocks_pinyin_column(*, backfill: bool = True) -> dict:
    """确保列存在；可选回填 NULL/空串行。进程内回填成功后跳过重复全表扫描。"""
    global _PINYIN_SCHEMA_READY
    updated = 0
    with db.db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            ALTER TABLE stocks
              ADD COLUMN IF NOT EXISTS name_pinyin_abbr VARCHAR(64);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stocks_name_pinyin_abbr
              ON stocks (name_pinyin_abbr);
            """
        )
        if backfill and not _PINYIN_SCHEMA_READY:
            cur.execute(
                """
                SELECT code, name FROM stocks
                WHERE name_pinyin_abbr IS NULL OR name_pinyin_abbr = '';
                """
            )
            rows = cur.fetchall() or []
            for row in rows:
                abbr = name_to_pinyin_abbr(row["name"])
                cur.execute(
                    "UPDATE stocks SET name_pinyin_abbr = %s WHERE code = %s;",
                    (abbr, row["code"]),
                )
                updated += 1
            _PINYIN_SCHEMA_READY = True
        conn.commit()
    return {"backfilled": updated}
