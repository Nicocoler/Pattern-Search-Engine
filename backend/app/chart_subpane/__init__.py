# -*- coding: utf-8 -*-
"""
Chart Subpane：通达信 MACD / KDJ 纯函数。

不进入 Indicator Engine / Market Pipeline / technical_indicators（ADR 0003）。
公式权威见 docs/research-tdx-macd-kdj.md。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def tdx_ema(series: pd.Series, n: int) -> pd.Series:
    """通达信 EMA(X, N) = (2*X + (N-1)*Y') / (N+1)；首根 Y = X。"""
    if n < 1:
        raise ValueError("EMA period must be >= 1")
    values = series.astype(float).to_numpy(copy=True)
    out = np.full(len(values), np.nan, dtype=float)
    prev = np.nan
    alpha_num = 2.0
    alpha_den = float(n + 1)
    decay = float(n - 1)
    for i, x in enumerate(values):
        if np.isnan(x):
            out[i] = np.nan
            continue
        if np.isnan(prev):
            prev = x
        else:
            prev = (alpha_num * x + decay * prev) / alpha_den
        out[i] = prev
    return pd.Series(out, index=series.index)


def tdx_sma(series: pd.Series, n: int, m: int) -> pd.Series:
    """通达信 SMA(X, N, M) = (M*X + (N-M)*Y') / N；首根 Y = X。"""
    if n < 1:
        raise ValueError("SMA period must be >= 1")
    values = series.astype(float).to_numpy(copy=True)
    out = np.full(len(values), np.nan, dtype=float)
    prev = np.nan
    m_f = float(m)
    n_f = float(n)
    keep = n_f - m_f
    for i, x in enumerate(values):
        if np.isnan(x):
            out[i] = np.nan
            continue
        if np.isnan(prev):
            prev = x
        else:
            prev = (m_f * x + keep * prev) / n_f
        out[i] = prev
    return pd.Series(out, index=series.index)


def apply_macd(
    df: pd.DataFrame,
    short: int = 12,
    long: int = 26,
    mid: int = 9,
) -> pd.DataFrame:
    """写入 dif / dea / macd（柱 = (dif-dea)*2）。原地并返回 df。"""
    close = df["close"]
    dif = tdx_ema(close, short) - tdx_ema(close, long)
    dea = tdx_ema(dif, mid)
    df["dif"] = dif
    df["dea"] = dea
    df["macd"] = (dif - dea) * 2.0
    return df


def apply_kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> pd.DataFrame:
    """写入 k / d / j。原地并返回 df。"""
    low_n = df["low"].rolling(window=n, min_periods=n).min()
    high_n = df["high"].rolling(window=n, min_periods=n).max()
    denom = high_n - low_n
    rsv = np.where(
        denom > 0,
        (df["close"] - low_n) / denom * 100.0,
        np.where(denom.isna(), np.nan, 0.0),
    )
    rsv_s = pd.Series(rsv, index=df.index)
    k = tdx_sma(rsv_s, m1, 1)
    d = tdx_sma(k, m2, 1)
    df["k"] = k
    df["d"] = d
    df["j"] = 3.0 * k - 2.0 * d
    return df


def apply_chart_subpanes(df: pd.DataFrame) -> pd.DataFrame:
    """在完整暖机序列上计算 MACD + KDJ（通达信默认参数）。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    apply_macd(out)
    apply_kdj(out)
    return out
