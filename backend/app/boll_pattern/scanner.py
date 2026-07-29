# -*- coding: utf-8 -*-
"""
布林编排全市场/子集扫描器：
daily_bars → indicators → %B/zone → stock_state_daily → regex 匹配 → pattern_match_result upsert
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any, Callable, Sequence

import pandas as pd
from psycopg2.extras import execute_values

from backend.app.boll_pattern.loader import normalize_zone_thresholds
from backend.app.boll_pattern.matcher import find_patterns_with_dates
from backend.app.boll_pattern.repository import (
    ENSURE_PATTERN_TABLES_SQL,
    get_settings,
    list_enabled_effective,
    seed_from_yaml,
)
from backend.app.boll_pattern.scoring import score_match_from_window
from backend.app.boll_pattern.zone import apply_denoise_to_states, state_string, zones_from_series
from backend.app.core import db
from backend.app.core.timeutil import today_beijing
from backend.app.market_pipeline import prepare_stock_frame

logger = logging.getLogger("BollPatternScanner")

# 进程内扫描进度（供前端轮询；与 FastAPI BackgroundTasks 同进程）
_PROGRESS_LOCK = threading.Lock()
_SCAN_PROGRESS: dict[str, Any] = {
    "running": False,
    "phase": "idle",  # idle | preparing | scanning | done | failed
    "current": 0,
    "total": 0,
    "scanned": 0,
    "skipped": 0,
    "errors": 0,
    "matches": 0,
    "window_days": None,
    "scan_date": None,
    "current_code": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "summary": None,
    "error": None,
}


def get_scan_progress() -> dict[str, Any]:
    """返回当前编排扫描进度快照。"""
    with _PROGRESS_LOCK:
        snap = dict(_SCAN_PROGRESS)
    total = int(snap.get("total") or 0)
    current = int(snap.get("current") or 0)
    snap["percent"] = round(100.0 * current / total, 1) if total > 0 else 0.0
    return snap


def _update_scan_progress(**kwargs: Any) -> None:
    with _PROGRESS_LOCK:
        _SCAN_PROGRESS.update(kwargs)


def begin_scan_progress(*, window_days: int, scan_date: str | None = None, message: str = "任务已入队，准备扫描…") -> dict[str, Any]:
    """触发扫描时立即置为 preparing，避免前端读到上一次 done。"""
    from backend.app.core.timeutil import isoformat_beijing, now_beijing

    _update_scan_progress(
        running=True,
        phase="preparing",
        current=0,
        total=0,
        scanned=0,
        skipped=0,
        errors=0,
        matches=0,
        window_days=window_days,
        scan_date=scan_date,
        current_code=None,
        message=message,
        started_at=isoformat_beijing(now_beijing()),
        finished_at=None,
        summary=None,
        error=None,
    )
    return get_scan_progress()

ENSURE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS stock_state_daily (
    code VARCHAR(12) NOT NULL,
    date DATE NOT NULL,
    pct_b NUMERIC(12, 6),
    zone VARCHAR(2) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_stock_state_daily_date ON stock_state_daily (date DESC);

CREATE TABLE IF NOT EXISTS pattern_match_result (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(12) NOT NULL,
    pattern_id VARCHAR(64) NOT NULL,
    pattern_name VARCHAR(128) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    matched_states TEXT NOT NULL,
    score NUMERIC(10, 4),
    scan_date DATE NOT NULL,
    window_days INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (code, pattern_id, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS idx_pattern_match_scan_date ON pattern_match_result (scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_pattern_match_end_pattern ON pattern_match_result (end_date DESC, pattern_id);
""" + "\n" + ENSURE_PATTERN_TABLES_SQL


class BollPatternScanner:
    def __init__(self, yaml_path: str | None = None):
        self.yaml_path = yaml_path
        self.reload_config()

    def reload_config(self) -> None:
        """从 DB 加载全局尺子与 enabled 编排（先 seed 补缺）。"""
        try:
            seed_from_yaml(self.yaml_path)
        except Exception as ex:
            logger.warning("编排种子补缺失败（将继续尝试读库）: %s", ex)
        settings = get_settings()
        self.zone_thresholds = settings["zone_thresholds"]
        self.denoise_min_len = int(settings["denoise_min_len"])
        self.enabled_patterns = list_enabled_effective()

    def ensure_tables(self) -> None:
        with db.db_cursor(dict_cursor=False) as (conn, cur):
            cur.execute(ENSURE_TABLES_SQL)
            conn.commit()
        seed_from_yaml(self.yaml_path)
        self.reload_config()

    def load_stock_bars(self, code: str, end_date: date, lookback_calendar_days: int) -> pd.DataFrame:
        """保留兼容；新逻辑请用 prepare_stock_frame。"""
        start_date = end_date - timedelta(days=lookback_calendar_days)
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute(
                """
                SELECT date, open, high, low, close, volume, amount, factor
                FROM daily_bars
                WHERE code = %s AND date >= %s AND date <= %s
                ORDER BY date ASC;
                """,
                (code, start_date, end_date),
            )
            rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        for col in ("open", "high", "low", "close", "amount", "factor"):
            if col in df.columns:
                df[col] = df[col].astype(float)
        if "volume" in df.columns:
            df["volume"] = df["volume"].astype(int)
        return df

    def build_state_frame(self, df_pattern: pd.DataFrame) -> pd.DataFrame:
        """
        输入 level=pattern 的帧（含 pct_b），用全局尺子写 zone 快照。
        """
        if df_pattern is None or df_pattern.empty:
            return pd.DataFrame()
        if "pct_b" not in df_pattern.columns:
            return pd.DataFrame()
        zones = zones_from_series(df_pattern["pct_b"], self.zone_thresholds)
        return pd.DataFrame({
            "date": df_pattern["date"],
            "pct_b": df_pattern["pct_b"],
            "zone": zones,
        })

    def list_candidate_codes(self, codes: Sequence[str] | None = None) -> list[str]:
        if codes:
            return [c.lower().strip() for c in codes if c]
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute(
                """
                SELECT code FROM stocks
                WHERE is_st = FALSE AND is_suspended = FALSE
                ORDER BY code ASC;
                """
            )
            rows = cursor.fetchall()
        return [r["code"] for r in rows]

    def resolve_scan_date(self, scan_date: date | None = None) -> date:
        if scan_date is not None:
            return scan_date
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute("SELECT MAX(date) AS d FROM daily_bars;")
            row = cursor.fetchone()
        if row and row.get("d"):
            d = row["d"]
            return d if isinstance(d, date) else datetime.strptime(str(d), "%Y-%m-%d").date()
        return today_beijing()

    def upsert_states(self, code: str, state_df: pd.DataFrame) -> int:
        if state_df is None or state_df.empty:
            return 0
        rows = []
        for _, r in state_df.iterrows():
            pct = r["pct_b"]
            if pd.isna(pct):
                pct_val = None
            else:
                pct_val = float(pct)
            rows.append((code, r["date"], pct_val, str(r["zone"])))
        if not rows:
            return 0
        sql = """
            INSERT INTO stock_state_daily (code, date, pct_b, zone, updated_at)
            VALUES %s
            ON CONFLICT (code, date) DO UPDATE SET
                pct_b = EXCLUDED.pct_b,
                zone = EXCLUDED.zone,
                updated_at = NOW();
        """
        with db.db_cursor(dict_cursor=False) as (conn, cur):
            execute_values(
                cur,
                sql,
                rows,
                template="(%s, %s, %s, %s, NOW())",
                page_size=500,
            )
            conn.commit()
        return len(rows)

    def load_states_window(self, code: str, end_date: date, window_days: int) -> pd.DataFrame:
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute(
                """
                SELECT date, pct_b, zone
                FROM stock_state_daily
                WHERE code = %s AND date <= %s
                ORDER BY date DESC
                LIMIT %s;
                """,
                (code, end_date, window_days),
            )
            rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.sort_values("date").reset_index(drop=True)
        return df

    def clear_pattern_matches(
        self,
        pattern_ids: Sequence[str],
        codes: Sequence[str] | None = None,
    ) -> int:
        """
        扫描前清理旧命中，避免改 regex 后残留不满足新规则的历史行。
        - codes 为 None：清这些编排的全市场命中（全量扫描）
        - codes 给定：仅清这些股票上的命中（子集扫描）
        """
        ids = [str(p).strip() for p in pattern_ids if str(p).strip()]
        if not ids:
            return 0
        with db.db_cursor(dict_cursor=False) as (conn, cur):
            if codes is None:
                cur.execute(
                    "DELETE FROM pattern_match_result WHERE pattern_id = ANY(%s);",
                    (ids,),
                )
            else:
                code_list = [str(c).lower().strip() for c in codes if str(c).strip()]
                if not code_list:
                    return 0
                cur.execute(
                    """
                    DELETE FROM pattern_match_result
                    WHERE pattern_id = ANY(%s) AND code = ANY(%s);
                    """,
                    (ids, code_list),
                )
            n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            conn.commit()
        return int(n)

    def upsert_matches(
        self,
        code: str,
        matches: list[dict],
        scan_date: date,
        window_days: int,
    ) -> int:
        if not matches:
            return 0
        rows = []
        for m in matches:
            score = m.get("score")
            rows.append((
                code,
                m["pattern_id"],
                m["pattern_name"],
                m["start_date"],
                m["end_date"],
                m["matched_states"],
                float(score) if score is not None else None,
                scan_date,
                window_days,
            ))
        sql = """
            INSERT INTO pattern_match_result (
                code, pattern_id, pattern_name, start_date, end_date,
                matched_states, score, scan_date, window_days, created_at, updated_at
            )
            VALUES %s
            ON CONFLICT (code, pattern_id, start_date, end_date) DO UPDATE SET
                pattern_name = EXCLUDED.pattern_name,
                matched_states = EXCLUDED.matched_states,
                score = EXCLUDED.score,
                scan_date = EXCLUDED.scan_date,
                window_days = EXCLUDED.window_days,
                updated_at = NOW();
        """
        with db.db_cursor(dict_cursor=False) as (conn, cur):
            execute_values(
                cur,
                sql,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())",
                page_size=200,
            )
            conn.commit()
        return len(rows)

    def process_one(
        self,
        code: str,
        scan_date: date,
        window_days: int = 60,
    ) -> dict[str, Any]:
        # 统一门面：与 compare 相同 250 日历日暖机 + indicators + pct_b
        df_full = prepare_stock_frame(code, scan_date, level="pattern")
        if df_full.empty or len(df_full) < 25:
            return {"code": code, "states": 0, "matches": 0, "skipped": True}

        # 全局尺子写入 zone 快照（验真）；匹配按编排 effective 尺子现算
        state_df = self.build_state_frame(df_full)
        n_states = self.upsert_states(code, state_df)

        win = df_full.tail(int(window_days)).copy().reset_index(drop=True)
        if win.empty:
            return {"code": code, "states": n_states, "matches": 0, "skipped": False}

        dates = list(win["date"].tolist())
        pct_series = win["pct_b"]
        all_matches: list[dict] = []

        for pat in self.enabled_patterns:
            zones = zones_from_series(pct_series, pat["zone_thresholds"])
            zones = apply_denoise_to_states(zones, int(pat["denoise_min_len"]))
            s = state_string(zones)
            hits = find_patterns_with_dates(s, dates, [pat])
            win_for_score = win.copy()
            win_for_score["zone"] = zones
            for m in hits:
                try:
                    m["score"] = score_match_from_window(m, win_for_score, df_full)
                except Exception as ex:
                    logger.warning(
                        "打分失败 code=%s pattern=%s: %s",
                        code, m.get("pattern_id"), ex,
                    )
                    m["score"] = None
                all_matches.append(m)

        n_matches = self.upsert_matches(code, all_matches, scan_date, window_days)
        return {"code": code, "states": n_states, "matches": n_matches, "skipped": False}

    def preview_one(
        self,
        code: str,
        pattern: dict[str, Any],
        *,
        window_days: int = 60,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """
        单票草稿试跑：算法与 process_one 同窗匹配，但不写库。
        pattern 须已是 effective 配置（含 zone_thresholds / denoise_min_len）。
        """
        code = code.lower().strip()
        if window_days <= 0:
            raise ValueError("window_days 必须 > 0")
        regex = (pattern.get("regex") or "").strip()
        if not regex:
            raise ValueError("regex 不能为空")

        end = self.resolve_scan_date(as_of)
        df_full = prepare_stock_frame(code, end, level="pattern")
        if df_full.empty or len(df_full) < 25:
            return {
                "code": code,
                "name": _lookup_stock_name(code),
                "as_of": end.isoformat(),
                "window_days": int(window_days),
                "pattern_id": pattern.get("id") or "draft",
                "pattern_name": pattern.get("name") or pattern.get("id") or "草稿",
                "state_string": "",
                "matches": [],
                "hit": False,
                "skipped": True,
                "message": "行情数据不足，无法试跑",
            }

        win = df_full.tail(int(window_days)).copy().reset_index(drop=True)
        dates = list(win["date"].tolist())
        zt = normalize_zone_thresholds(pattern.get("zone_thresholds"))
        dnl = int(pattern.get("denoise_min_len") or 0)
        pat_cfg = {
            "id": pattern.get("id") or "draft",
            "name": pattern.get("name") or pattern.get("id") or "草稿",
            "regex": regex,
            "min_total_days": int(pattern.get("min_total_days") or 0),
            "zone_thresholds": zt,
            "denoise_min_len": dnl,
        }
        zones = zones_from_series(win["pct_b"], zt)
        zones = apply_denoise_to_states(zones, dnl)
        s = state_string(zones)
        hits = find_patterns_with_dates(s, dates, [pat_cfg])
        win_for_score = win.copy()
        win_for_score["zone"] = zones
        out_matches: list[dict[str, Any]] = []
        for m in hits:
            score = None
            try:
                score = score_match_from_window(m, win_for_score, df_full)
            except Exception as ex:
                logger.warning(
                    "试跑打分失败 code=%s pattern=%s: %s",
                    code, pat_cfg["id"], ex,
                )
            start_d = m["start_date"]
            end_d = m["end_date"]
            out_matches.append({
                "pattern_id": pat_cfg["id"],
                "pattern_name": pat_cfg["name"],
                "start_date": start_d.isoformat() if hasattr(start_d, "isoformat") else str(start_d),
                "end_date": end_d.isoformat() if hasattr(end_d, "isoformat") else str(end_d),
                "matched_states": m["matched_states"],
                "score": score,
                "start_idx": int(m["start_idx"]),
                "end_idx": int(m["end_idx"]),
            })

        out_matches.sort(
            key=lambda x: (
                x["score"] is None,
                -(x["score"] if x["score"] is not None else 0.0),
                x["end_date"],
            ),
        )

        return {
            "code": code,
            "name": _lookup_stock_name(code),
            "as_of": end.isoformat(),
            "window_days": int(window_days),
            "pattern_id": pat_cfg["id"],
            "pattern_name": pat_cfg["name"],
            "state_string": s,
            "matches": out_matches,
            "hit": len(out_matches) > 0,
            "skipped": False,
            "message": (
                f"窗口内命中 {len(out_matches)} 条"
                if out_matches
                else "窗口内无匹配"
            ),
        }


    def run_scan(
        self,
        window_days: int = 60,
        codes: Sequence[str] | None = None,
        scan_date: date | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        from backend.app.core.timeutil import isoformat_beijing, now_beijing

        def _emit(**kwargs: Any) -> None:
            _update_scan_progress(**kwargs)
            if on_progress is not None:
                on_progress(get_scan_progress())

        self.ensure_tables()
        self.reload_config()
        # 扫描前把命中表冗余 pattern_name 对齐到编排现名（改名后即使旧行未清干净也能刷新）
        try:
            from backend.app.boll_pattern.repository import sync_match_pattern_names
            n_synced = sync_match_pattern_names()
            if n_synced:
                logger.info("扫描前已刷新命中编排名 %d 条", n_synced)
        except Exception as ex:
            logger.warning("扫描前刷新命中编排名失败: %s", ex)
        # 计划默认 60，至少支持 120；其它正整数亦允许便于调试
        if window_days <= 0:
            raise ValueError("window_days 必须 > 0")

        started = isoformat_beijing(now_beijing())
        _emit(
            running=True,
            phase="preparing",
            current=0,
            total=0,
            scanned=0,
            skipped=0,
            errors=0,
            matches=0,
            window_days=window_days,
            scan_date=None,
            current_code=None,
            message="准备扫描池与编排配置…",
            started_at=started,
            finished_at=None,
            summary=None,
            error=None,
        )

        end = self.resolve_scan_date(scan_date)
        pool = self.list_candidate_codes(codes)
        pattern_ids = [p["id"] for p in self.enabled_patterns]
        cleared = self.clear_pattern_matches(
            pattern_ids,
            codes=None if codes is None else pool,
        )
        logger.info(
            "布林编排扫描开始: scan_date=%s window_days=%s stocks=%d patterns=%d cleared_old_matches=%d",
            end, window_days, len(pool), len(self.enabled_patterns), cleared,
        )

        total_states = 0
        total_matches = 0
        scanned = 0
        skipped = 0
        errors = 0
        n_pool = len(pool)

        _emit(
            phase="scanning",
            total=n_pool,
            scan_date=end.isoformat(),
            message=f"扫描中 0/{n_pool}",
        )

        try:
            for i, code in enumerate(pool, start=1):
                try:
                    result = self.process_one(code, end, window_days=window_days)
                    if result.get("skipped"):
                        skipped += 1
                    else:
                        scanned += 1
                        total_states += int(result.get("states") or 0)
                        total_matches += int(result.get("matches") or 0)
                except Exception as ex:
                    errors += 1
                    logger.error("布林编排扫描失败 code=%s: %s", code, ex)

                # 每只更新内存进度；日志仍按 200 节流
                if i % 200 == 0 or i == n_pool or i == 1:
                    logger.info("布林编排扫描进度 %d/%d", i, n_pool)
                if i % 5 == 0 or i == n_pool or i == 1:
                    _emit(
                        current=i,
                        scanned=scanned,
                        skipped=skipped,
                        errors=errors,
                        matches=total_matches,
                        current_code=code,
                        message=f"扫描中 {i}/{n_pool}",
                    )

            summary = {
                "scan_date": end.isoformat(),
                "window_days": window_days,
                "universe": n_pool,
                "scanned": scanned,
                "skipped": skipped,
                "errors": errors,
                "state_rows_upserted": total_states,
                "match_rows_upserted": total_matches,
                "match_rows_cleared": cleared,
                "patterns": pattern_ids,
            }
            finished = isoformat_beijing(now_beijing())
            _emit(
                running=False,
                phase="done",
                current=n_pool,
                scanned=scanned,
                skipped=skipped,
                errors=errors,
                matches=total_matches,
                current_code=None,
                message=f"扫描完成 {scanned}/{n_pool}，命中 {total_matches} 条",
                finished_at=finished,
                summary=summary,
                error=None,
            )
            logger.info("布林编排扫描完成: %s", summary)
            return summary
        except Exception as ex:
            finished = isoformat_beijing(now_beijing())
            _emit(
                running=False,
                phase="failed",
                message=f"扫描失败: {ex}",
                finished_at=finished,
                error=str(ex)[:500],
            )
            raise


def _lookup_stock_name(code: str) -> str | None:
    try:
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute("SELECT name FROM stocks WHERE code = %s;", (code,))
            row = cursor.fetchone()
        return row["name"] if row else None
    except Exception:
        return None


def get_stock_boll_states(code: str, limit: int = 60) -> list[dict]:
    """查询单股最近状态（验真 API 用）。"""
    code = code.lower().strip()
    with db.db_cursor(dict_cursor=True) as (conn, cursor):
        cursor.execute(
            """
            SELECT date, pct_b, zone
            FROM stock_state_daily
            WHERE code = %s
            ORDER BY date DESC
            LIMIT %s;
            """,
            (code, limit),
        )
        rows = cursor.fetchall()
    out = []
    for r in reversed(list(rows)):
        pct = r["pct_b"]
        out.append({
            "date": r["date"].isoformat() if hasattr(r["date"], "isoformat") else str(r["date"]),
            "pct_b": float(pct) if pct is not None else None,
            "zone": r["zone"],
        })
    return out


def _write_boll_scan_status(key: str, value: str) -> None:
    """写入 data_sync_status；失败仅打日志。key 须 <= 32 字符。"""
    try:
        with db.db_cursor(dict_cursor=False) as (conn, cur):
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS data_sync_status (
                    key VARCHAR(32) PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                """
            )
            cur.execute(
                """
                INSERT INTO data_sync_status (key, value, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
                """,
                (key[:32], value),
            )
            conn.commit()
    except Exception as ex:
        logger.warning("写入布林编排扫描状态失败 key=%s: %s", key, ex)


def run_post_sync_boll_scan(window_days: int = 60) -> dict[str, Any] | None:
    """
    行情同步成功后的下游编排扫描。
    失败只记日志并写 status=failed，不向外抛（不拖垮同步成功态）。
    """
    import json
    from backend.app.core.timeutil import isoformat_beijing, now_beijing

    _write_boll_scan_status("boll_scan_start", isoformat_beijing(now_beijing()))
    _write_boll_scan_status("boll_scan_status", "running")
    try:
        scanner = BollPatternScanner()
        summary = scanner.run_scan(window_days=window_days)
        _write_boll_scan_status("boll_scan_end", isoformat_beijing(now_beijing()))
        _write_boll_scan_status("boll_scan_status", "success")
        # summary 可能较长，截断写入
        payload = json.dumps(summary, ensure_ascii=False)
        if len(payload) > 2000:
            payload = payload[:2000]
        _write_boll_scan_status("boll_scan_summary", payload)
        logger.info("同步下游布林编排扫描完成: %s", summary)
        return summary
    except Exception as ex:
        logger.error("同步下游布林编排扫描失败（不影响行情同步成功态）: %s", ex)
        _write_boll_scan_status("boll_scan_end", isoformat_beijing(now_beijing()))
        _write_boll_scan_status("boll_scan_status", "failed")
        _write_boll_scan_status("boll_scan_summary", str(ex)[:500])
        return None
