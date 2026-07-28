# -*- coding: utf-8 -*-
"""
布林编排二次打分：不影响是否命中，仅用于排序。
分量：突破强度 0.45 + 横盘平整 0.35 + 量能确认 0.20 → 总分 0～100。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _linear_map(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def score_breakout_strength(zones: Sequence[str], pct_b: Sequence[float | None]) -> float:
    """U 段 max(%B)：[0.95, 1.2] → [40, 100]。"""
    vals = []
    for z, p in zip(zones, pct_b):
        if z == "U" and p is not None and not (isinstance(p, float) and np.isnan(p)):
            vals.append(float(p))
    if not vals:
        return 40.0
    mx = max(vals)
    return _clip(_linear_map(mx, 0.95, 1.2, 40.0, 100.0))


def _longest_m_slice(zones: Sequence[str]) -> slice | None:
    best_start, best_len, i = None, 0, 0
    n = len(zones)
    while i < n:
        if zones[i] != "M":
            i += 1
            continue
        j = i
        while j < n and zones[j] == "M":
            j += 1
        if j - i > best_len:
            best_len = j - i
            best_start = i
        i = j
    if best_start is None or best_len <= 0:
        return None
    return slice(best_start, best_start + best_len)


def score_mid_flatness(zones: Sequence[str], pct_b: Sequence[float | None]) -> float:
    """
    最长连续 M 段 %B 标准差：越小越好。
    std 0 → 100；std 0.08 → 40；更大继续降到 20。
    """
    sl = _longest_m_slice(zones)
    if sl is None:
        return 50.0
    vals = []
    for p in pct_b[sl]:
        if p is not None and not (isinstance(p, float) and np.isnan(p)):
            vals.append(float(p))
    if len(vals) < 2:
        return 60.0
    std = float(np.std(vals, ddof=0))
    # std=0 → 100; std=0.08 → 40; std>=0.15 → 20
    if std <= 0:
        return 100.0
    if std >= 0.15:
        return 20.0
    if std <= 0.08:
        return _clip(_linear_map(std, 0.0, 0.08, 100.0, 40.0))
    return _clip(_linear_map(std, 0.08, 0.15, 40.0, 20.0))


def score_volume_confirm(
    zones: Sequence[str],
    volumes: Sequence[float] | None,
    lookback_volumes: Sequence[float] | None = None,
) -> float:
    """
    首段 U 均量 / 前 20 日均量：[0.8, 2.0] → [30, 100]。
    无量数据 → 50 中性分。
    """
    if not volumes or len(volumes) != len(zones):
        return 50.0

    # 找首段连续 U
    start = None
    for i, z in enumerate(zones):
        if z == "U":
            start = i
            break
    if start is None:
        return 50.0
    end = start
    while end < len(zones) and zones[end] == "U":
        end += 1
    u_vols = [float(v) for v in volumes[start:end] if v is not None]
    if not u_vols:
        return 50.0
    u_avg = sum(u_vols) / len(u_vols)

    baseline = None
    if lookback_volumes:
        base = [float(v) for v in lookback_volumes if v is not None and float(v) > 0]
        if base:
            baseline = sum(base) / len(base)
    if baseline is None or baseline <= 0:
        return 50.0

    ratio = u_avg / baseline
    return _clip(_linear_map(ratio, 0.8, 2.0, 30.0, 100.0))


def compute_pattern_score(
    zones: Sequence[str],
    pct_b: Sequence[float | None],
    volumes: Sequence[float] | None = None,
    lookback_volumes: Sequence[float] | None = None,
) -> dict:
    """
    返回 {"score", "breakout", "flatness", "volume"}，总分 0～100。
    """
    if len(zones) != len(pct_b):
        raise ValueError("zones 与 pct_b 长度必须一致")
    b = score_breakout_strength(zones, pct_b)
    f = score_mid_flatness(zones, pct_b)
    v = score_volume_confirm(zones, volumes, lookback_volumes)
    total = 0.45 * b + 0.35 * f + 0.20 * v
    return {
        "score": round(_clip(total), 4),
        "breakout": round(b, 4),
        "flatness": round(f, 4),
        "volume": round(v, 4),
    }


def score_match_from_window(
    match: dict,
    window_df: pd.DataFrame,
    bars_df: pd.DataFrame | None = None,
) -> float:
    """
    用扫描窗口 DataFrame（含 date/zone/pct_b）为单次命中打分。
    bars_df 可选，需含 date/volume，用于量能分量。
    """
    start_idx = int(match["start_idx"])
    end_idx = int(match["end_idx"])
    sub = window_df.iloc[start_idx:end_idx]
    zones = [str(z) for z in sub["zone"].tolist()]
    pct_list = []
    for p in sub["pct_b"].tolist():
        if p is None or (isinstance(p, float) and np.isnan(p)) or pd.isna(p):
            pct_list.append(None)
        else:
            pct_list.append(float(p))

    volumes = None
    lookback = None
    if bars_df is not None and not bars_df.empty and "volume" in bars_df.columns:
        date_to_vol = {}
        for _, r in bars_df.iterrows():
            date_to_vol[r["date"]] = float(r["volume"])

        volumes = [date_to_vol.get(d) for d in sub["date"].tolist()]

        all_dates = list(window_df["date"].tolist())
        pre_dates = all_dates[:start_idx][-20:]
        lookback = [date_to_vol.get(d) for d in pre_dates]
        if len([x for x in lookback if x]) < 5:
            bars_sorted = bars_df.sort_values("date")
            start_date = sub["date"].iloc[0]
            prior = bars_sorted[bars_sorted["date"] < start_date].tail(20)
            lookback = [float(v) for v in prior["volume"].tolist()]

    result = compute_pattern_score(zones, pct_list, volumes, lookback)
    return float(result["score"])
