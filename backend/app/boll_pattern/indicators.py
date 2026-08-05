# -*- coding: utf-8 -*-
"""
编排指标条件（Pattern Indicator）：
opt-in 硬过滤；作用在 regex 命中整段 span；v1 仅 KDJ 谓词（ADR 0007）。
计算复用 chart_subpane.apply_kdj，不进 Indicator Engine。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

ALLOWED_INDICATOR_TYPES = frozenset({"kdj_golden_cross", "j_above_kd"})
DEFAULT_MAX_BREACH_BARS = 1


def _finite(x: Any) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and not math.isinf(v)


def _date_iso(d: Any) -> str:
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def normalize_indicators(raw: Any) -> list[dict[str, Any]]:
    """
    规范化 indicators；空/None → []。
    每项：{type}；j_above_kd 可带 max_breach_bars（默认 1，>=0）。
    同 type 禁止重复。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        import json
        raw = json.loads(raw)
    if not isinstance(raw, (list, tuple)):
        raise ValueError("indicators 须为数组")

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"indicators[{i}] 须为对象")
        typ = str(item.get("type", "")).strip().lower()
        if typ not in ALLOWED_INDICATOR_TYPES:
            raise ValueError(
                f"indicators[{i}].type 仅支持: {', '.join(sorted(ALLOWED_INDICATOR_TYPES))}"
            )
        if typ in seen:
            raise ValueError(f"indicators 禁止重复 type: {typ}")
        seen.add(typ)

        entry: dict[str, Any] = {"type": typ}
        if typ == "j_above_kd":
            raw_mb = item.get("max_breach_bars", DEFAULT_MAX_BREACH_BARS)
            try:
                mb = int(raw_mb)
            except (TypeError, ValueError) as ex:
                raise ValueError(f"indicators[{i}].max_breach_bars 须为整数") from ex
            if mb < 0:
                raise ValueError(f"indicators[{i}].max_breach_bars 须 >= 0")
            entry["max_breach_bars"] = mb
        out.append(entry)
    return out


def validate_pattern_indicators(indicators_raw: Any) -> list[dict[str, Any]]:
    """normalize；供创建/更新入口调用。"""
    return normalize_indicators(indicators_raw)


def _eval_kdj_golden_cross(
    *,
    ks: Sequence[Any],
    ds: Sequence[Any],
    dates: Sequence[Any],
    start_idx: int,
    end_idx: int,
) -> dict[str, Any] | None:
    """
    span 内相邻两根：K[i-1] <= D[i-1] 且 K[i] > D[i]。
    仅用 span 内 bar；单 bar 或无数值 → 失败。
    """
    if end_idx - start_idx < 2:
        return None
    for i in range(start_idx + 1, end_idx):
        k0, d0 = ks[i - 1], ds[i - 1]
        k1, d1 = ks[i], ds[i]
        if not (_finite(k0) and _finite(d0) and _finite(k1) and _finite(d1)):
            continue
        if float(k0) <= float(d0) and float(k1) > float(d1):
            return {
                "type": "kdj_golden_cross",
                "date": _date_iso(dates[i]),
                "idx": i,
            }
    return None


def _eval_j_above_kd(
    *,
    js: Sequence[Any],
    ks: Sequence[Any],
    ds: Sequence[Any],
    dates: Sequence[Any],
    start_idx: int,
    end_idx: int,
    max_breach_bars: int,
) -> dict[str, Any] | None:
    """全程尽量 J>=K 且 J>=D；NaN 算违约；违约数 > max_breach_bars → 失败。"""
    if end_idx <= start_idx:
        return None
    breach_dates: list[str] = []
    for i in range(start_idx, end_idx):
        j, k, d = js[i], ks[i], ds[i]
        ok = (
            _finite(j)
            and _finite(k)
            and _finite(d)
            and float(j) >= float(k)
            and float(j) >= float(d)
        )
        if not ok:
            breach_dates.append(_date_iso(dates[i]))
    if len(breach_dates) > int(max_breach_bars):
        return None
    return {
        "type": "j_above_kd",
        "max_breach_bars": int(max_breach_bars),
        "breach_count": len(breach_dates),
        "breach_dates": breach_dates,
    }


def find_indicator_hits_in_span(
    *,
    ks: Sequence[Any],
    ds: Sequence[Any],
    js: Sequence[Any],
    dates: Sequence[Any],
    start_idx: int,
    end_idx: int,
    indicators: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    对每条 indicator 求证据；任一失败返回 []（调用方视为整段不通过）。
    """
    if not indicators:
        return []
    n = len(dates)
    if not (0 <= start_idx < end_idx <= n):
        return []
    if len(ks) != n or len(ds) != n or len(js) != n:
        raise ValueError("k / d / j / dates 长度须一致")

    hits: list[dict[str, Any]] = []
    for ind in indicators:
        typ = ind["type"]
        if typ == "kdj_golden_cross":
            found = _eval_kdj_golden_cross(
                ks=ks, ds=ds, dates=dates, start_idx=start_idx, end_idx=end_idx,
            )
        elif typ == "j_above_kd":
            found = _eval_j_above_kd(
                js=js,
                ks=ks,
                ds=ds,
                dates=dates,
                start_idx=start_idx,
                end_idx=end_idx,
                max_breach_bars=int(ind.get("max_breach_bars", DEFAULT_MAX_BREACH_BARS)),
            )
        else:
            return []
        if found is None:
            return []
        hits.append(found)
    return hits


def apply_indicator_filter_to_matches(
    matches: list[dict],
    *,
    ks: Sequence[Any],
    ds: Sequence[Any],
    js: Sequence[Any],
    dates: Sequence[Any],
    indicators: Sequence[Mapping[str, Any]] | None,
) -> list[dict]:
    """
    正则（+edges）命中后的三次过滤：无 indicators 原样返回并补 indicator_hits=[]；
    有则仅保留全部谓词通过的 match，并写入 indicator_hits。
    """
    ind_list = list(indicators or [])
    if not ind_list:
        for m in matches:
            m["indicator_hits"] = []
        return matches

    kept: list[dict] = []
    for m in matches:
        hits = find_indicator_hits_in_span(
            ks=ks,
            ds=ds,
            js=js,
            dates=dates,
            start_idx=int(m["start_idx"]),
            end_idx=int(m["end_idx"]),
            indicators=ind_list,
        )
        if len(hits) < len(ind_list):
            continue
        m["indicator_hits"] = hits
        kept.append(m)
    return kept
