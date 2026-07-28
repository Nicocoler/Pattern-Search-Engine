# -*- coding: utf-8 -*-
"""%B → Zone 离散化，以及可选的最短持续天数去抖。"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

import pandas as pd

DEFAULT_ZONE_THRESHOLDS: dict[str, tuple[float, float]] = {
    # 贴轨时 %B：下轨=0，中轨=0.5，上轨=1；跌破下轨<0，突破上轨>1
    "L": (-float("inf"), 0.35),   # 中轨下方（%B < 0.35；贴下轨=0）
    "M": (0.35, 0.65),            # 贴中轨（0.35 ≤ %B < 0.65；贴中轨=0.5）
    "H": (0.65, 0.95),            # 中轨与上轨之间过渡态（0.65 ≤ %B < 0.95）
    "U": (0.95, float("inf")),    # 触及/突破上轨（%B ≥ 0.95；贴上轨=1）
}

# 分区遍历顺序（由低到高），保证边界归左闭右开
_ZONE_ORDER = ("L", "M", "H", "U")


def _normalize_thresholds(
    thresholds: Mapping[str, Sequence[float]] | None,
) -> dict[str, tuple[float, float]]:
    if not thresholds:
        return dict(DEFAULT_ZONE_THRESHOLDS)
    out: dict[str, tuple[float, float]] = {}
    for label in _ZONE_ORDER:
        if label not in thresholds:
            raise ValueError(f"zone_thresholds 缺少分区: {label}")
        lo, hi = thresholds[label]
        lo_f = float(lo)
        hi_f = float(hi)
        if math.isinf(lo_f) and lo_f < 0:
            lo_f = -float("inf")
        if math.isinf(hi_f) and hi_f > 0:
            hi_f = float("inf")
        out[label] = (lo_f, hi_f)
    return out


def zone(pct_b: float, thresholds: Mapping[str, Sequence[float]] | None = None) -> str:
    """单个 %B 值映射为 L/M/H/U；NaN/异常 → NA。"""
    if pct_b is None or (isinstance(pct_b, float) and math.isnan(pct_b)) or pd.isna(pct_b):
        return "NA"
    try:
        x = float(pct_b)
    except (TypeError, ValueError):
        return "NA"
    if math.isnan(x) or math.isinf(x):
        return "NA"

    bounds = _normalize_thresholds(thresholds)
    for label in _ZONE_ORDER:
        lo, hi = bounds[label]
        if lo <= x < hi:
            return label
    # 恰好等于最后一档上界（+inf 时不会走到）；兜底 U
    return "U"


def zones_from_series(
    pct_b: pd.Series,
    thresholds: Mapping[str, Sequence[float]] | None = None,
) -> list[str]:
    return [zone(v, thresholds) for v in pct_b.tolist()]


def compress(states: Sequence[str]) -> list[tuple[str, int]]:
    """游程压缩：[('L',5), ('M',3), ...]"""
    groups: list[list] = []
    for s in states:
        if groups and groups[-1][0] == s:
            groups[-1][1] += 1
        else:
            groups.append([s, 1])
    return [tuple(g) for g in groups]


def denoise(runs: list[tuple[str, int]], min_len: int = 2) -> list[tuple[str, int]]:
    """
    短于 min_len 的段并入前一段；若无前段则并入后一段。
    min_len <= 1 时原样返回。
    """
    if min_len <= 1 or not runs:
        return list(runs)

    merged: list[list] = [[r[0], r[1]] for r in runs]
    i = 0
    while i < len(merged):
        if merged[i][1] < min_len:
            if i > 0:
                merged[i - 1][1] += merged[i][1]
                merged.pop(i)
                continue
            if i + 1 < len(merged):
                merged[i + 1][1] += merged[i][1]
                merged.pop(i)
                continue
        i += 1

    # 合并相邻同标签
    out: list[list] = []
    for label, length in merged:
        if out and out[-1][0] == label:
            out[-1][1] += length
        else:
            out.append([label, length])
    return [tuple(g) for g in out]


def expand_runs(runs: Sequence[tuple[str, int]]) -> list[str]:
    states: list[str] = []
    for label, length in runs:
        states.extend([label] * int(length))
    return states


def apply_denoise_to_states(states: Sequence[str], min_len: int) -> list[str]:
    if min_len <= 0:
        return list(states)
    return expand_runs(denoise(compress(states), min_len=min_len))


def state_string(states: Sequence[str]) -> str:
    return "".join(states)
