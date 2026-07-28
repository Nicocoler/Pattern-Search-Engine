# -*- coding: utf-8 -*-
"""
统一行情准备门面：加载 + 与 compare 一致的暖机回看 + 分层计算。

各模块（compare / bars / 编排扫描 / sentry / backtest）应通过本模块取数，
避免各自选择 lookback 导致布林三轨不一致。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import pandas as pd

from backend.app.boll_pattern.pct_b import calc_pct_b
from backend.app.core import db
from backend.app.feature_engine.engine import calculate_features
from backend.app.indicator_engine.engine import calculate_indicators

# 与 /api/compare/template/.../stock/... 中 lookback_days=250 锁死一致
COMPARE_LOOKBACK_DAYS = 250

PipelineLevel = Literal["indicators", "features", "pattern"]


def load_daily_bars(code: str, start_date: date, end_date: date) -> pd.DataFrame:
    """按日历区间从 daily_bars 加载 OHLCV（含 factor）。"""
    code = code.lower().strip()
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


def load_stock_bars(code: str, end_date: date, lookback_days: int = COMPARE_LOOKBACK_DAYS) -> pd.DataFrame:
    """截止 end_date 向前 lookback_days 日历日加载（暖机常用入口）。"""
    start_date = end_date - timedelta(days=int(lookback_days))
    return load_daily_bars(code, start_date, end_date)


def _apply_level(df: pd.DataFrame, code: str, level: PipelineLevel) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = calculate_indicators(df)
    if out.empty:
        return pd.DataFrame()

    if level == "features":
        return calculate_features(out, code)
    if level == "pattern":
        out = out.copy()
        out["pct_b"] = calc_pct_b(out["close"], out["boll_upper"], out["boll_lower"])
        return out
    if level == "indicators":
        return out
    raise ValueError(f"未知 level: {level}")


def prepare_stock_frame(
    code: str,
    end_date: date,
    *,
    level: PipelineLevel = "features",
    window_days: int | None = None,
    display_calendar_days: int | None = None,
    lookback_days: int = COMPARE_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """
    准备单股计算帧（点截止日）。

    - 计算加载日历回看：固定 max(lookback_days, COMPARE_LOOKBACK_DAYS)，默认即 250（compare）。
    - level:
        indicators — OHLCV + 指标（含布林三轨）
        features   — 再跑特征工程（DTW/compare/sentry 主路径）
        pattern    — indicators + pct_b（不含 zone；zone 由编排 effective 尺子现算）
    - window_days: 若给定，返回末尾 N 根交易日（与 compare 的 tail(window_size) 一致）
    - display_calendar_days: 若给定，再按日历截展示窗
    """
    code = code.lower().strip()
    if lookback_days < COMPARE_LOOKBACK_DAYS:
        lookback_days = COMPARE_LOOKBACK_DAYS

    df_raw = load_stock_bars(code, end_date, lookback_days=lookback_days)
    df = _apply_level(df_raw, code, level)

    if window_days is not None and window_days > 0:
        df = df.tail(int(window_days)).copy()

    if display_calendar_days is not None and display_calendar_days > 0:
        display_start = end_date - timedelta(days=int(display_calendar_days))
        dates = pd.to_datetime(df["date"]).dt.date
        df = df.loc[dates >= display_start].copy()

    return df.reset_index(drop=True)


def prepare_stock_history(
    code: str,
    start_date: date,
    end_date: date,
    *,
    level: PipelineLevel = "features",
    warmup_days: int = COMPARE_LOOKBACK_DAYS,
) -> pd.DataFrame:
    """
    准备区间全历史帧（回测预载）：在 start 前再暖机 warmup_days（默认 250）日历日，
    一次算完 indicators/features，调用方按日切片防未来函数。
    """
    code = code.lower().strip()
    if warmup_days < COMPARE_LOOKBACK_DAYS:
        warmup_days = COMPARE_LOOKBACK_DAYS
    padded_start = start_date - timedelta(days=int(warmup_days))
    df_raw = load_daily_bars(code, padded_start, end_date)
    return _apply_level(df_raw, code, level).reset_index(drop=True)
