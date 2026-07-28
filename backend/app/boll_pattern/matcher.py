# -*- coding: utf-8 -*-
"""正则编排匹配引擎。"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Sequence


def find_pattern(state_str: str, pattern_cfg: dict) -> list[dict]:
    """
    在状态字符串上 finditer；过滤短于 min_total_days 的命中。
    返回 start_idx/end_idx（Python slice 半开区间）与 matched_states。
    """
    regex = pattern_cfg.get("regex") or ""
    if not regex or not state_str:
        return []

    compiled = re.compile(regex)
    min_days = int(pattern_cfg.get("min_total_days") or 0)
    results = []
    for m in compiled.finditer(state_str):
        start, end = m.span()
        if end - start < min_days:
            continue
        results.append({
            "pattern_id": pattern_cfg["id"],
            "pattern_name": pattern_cfg.get("name") or pattern_cfg["id"],
            "start_idx": start,
            "end_idx": end,
            "matched_states": state_str[start:end],
        })
    return results


def map_match_to_dates(match: dict, dates: Sequence[date | Any]) -> dict:
    """将下标映射为交易日；dates 与状态串等长。"""
    start_idx = int(match["start_idx"])
    end_idx = int(match["end_idx"])
    if start_idx < 0 or end_idx > len(dates) or start_idx >= end_idx:
        raise IndexError("匹配下标超出日期序列范围")
    return {
        **match,
        "start_date": dates[start_idx],
        "end_date": dates[end_idx - 1],
    }


def find_patterns_with_dates(
    state_str: str,
    dates: Sequence[date | Any],
    patterns: Sequence[dict],
) -> list[dict]:
    if len(state_str) != len(dates):
        raise ValueError("state_str 与 dates 长度必须一致")
    out: list[dict] = []
    for cfg in patterns:
        for m in find_pattern(state_str, cfg):
            out.append(map_match_to_dates(m, dates))
    return out
