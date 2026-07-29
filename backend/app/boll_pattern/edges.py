# -*- coding: utf-8 -*-
"""
编排转移边条件（Pattern Edge）：
opt-in 硬过滤；Arrival 锚点；原始日 zone 严格相邻；v1 仅 limit_up。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping, Sequence

ALLOWED_ZONES = frozenset({"L", "M", "H", "U"})
ALLOWED_WHEN = frozenset({"limit_up"})

# 涨停判定容差（绝对小数）：主板 10% 用 >= 0.099
LIMIT_UP_EPS = 0.001

_SIMPLE_ZONE_REGEX = re.compile(
    r"^([LMHU](\+|\*|\?|\{\d+,\d+\}|\{\d+,\}|\{\d+\})?)+$"
)
_TOKEN_RE = re.compile(r"([LMHU])(\+|\*|\?|\{\d+,\d+\}|\{\d+,\}|\{\d+\})?")


def get_limit_pct(code: str) -> float:
    """按代码前缀取涨停限额（同 LNF 分档；不看 ST）。"""
    code_lower = (code or "").lower().strip()
    if code_lower.startswith(("sz30", "sh68")):
        return 0.20
    if code_lower.startswith("bj"):
        return 0.30
    return 0.10


def is_limit_up(code: str, close: float, pre_close: float) -> bool:
    """(close/pre_close - 1) >= limit_pct - ε。"""
    try:
        c = float(close)
        p = float(pre_close)
    except (TypeError, ValueError):
        return False
    if p <= 0 or c != c or p != p:  # NaN check
        return False
    return (c / p - 1.0) >= (get_limit_pct(code) - LIMIT_UP_EPS)


def normalize_edges(raw: Any) -> list[dict[str, str]]:
    """
    规范化 edges；空/None → []。
    每项：{from, to, when}，when 仅允许 limit_up。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        import json
        raw = json.loads(raw)
    if not isinstance(raw, (list, tuple)):
        raise ValueError("edges 须为数组")
    out: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"edges[{i}] 须为对象")
        fr = str(item.get("from", "")).strip().upper()
        to = str(item.get("to", "")).strip().upper()
        when = str(item.get("when", "")).strip().lower()
        if fr not in ALLOWED_ZONES or to not in ALLOWED_ZONES:
            raise ValueError(f"edges[{i}] from/to 须为 L/M/H/U")
        if fr == to:
            raise ValueError(f"edges[{i}] from 与 to 不能相同")
        if when not in ALLOWED_WHEN:
            raise ValueError(f"edges[{i}] when 仅支持: {', '.join(sorted(ALLOWED_WHEN))}")
        out.append({"from": fr, "to": to, "when": when})
    return out


def tokenize_simple_zone_regex(regex: str) -> list[str]:
    """将简单 L/M/H/U+量词 连续串拆成 zone 字母序列；复杂写法抛错。"""
    compact = re.sub(r"\s+", "", (regex or "").strip())
    if not compact:
        raise ValueError("regex 不能为空")
    if not _SIMPLE_ZONE_REGEX.fullmatch(compact):
        raise ValueError(
            "含边条件时 regex 仅支持 L/M/H/U + 简单量词连续串（暂不支持分组/或/复杂写法）"
        )
    return [m.group(1) for m in _TOKEN_RE.finditer(compact)]


def validate_edges_against_regex(regex: str, edges: Sequence[Mapping[str, str]]) -> None:
    """
    每条边要求 regex token 序列中存在相邻的 from→to。
    若被第三态隔开（如 L+H*M）或未出现，拒绝。
    """
    if not edges:
        return
    tokens = tokenize_simple_zone_regex(regex)
    for edge in edges:
        fr = edge["from"]
        to = edge["to"]
        adjacent = any(
            tokens[i] == fr and tokens[i + 1] == to
            for i in range(len(tokens) - 1)
        )
        if adjacent:
            continue
        # 区分「被隔开」与「根本没有」
        try:
            i_from = tokens.index(fr)
            # 找 from 之后第一个 to
            j_to = next(
                (j for j in range(i_from + 1, len(tokens)) if tokens[j] == to),
                None,
            )
        except ValueError:
            j_to = None
            i_from = -1
        if j_to is not None and j_to > i_from + 1:
            mid = "".join(tokens[i_from + 1 : j_to])
            raise ValueError(
                f"边 {fr}→{to} 要求严格相邻，但 regex 中被 [{mid}] 隔开；"
                f"请去掉中间态或不要声明该边"
            )
        raise ValueError(
            f"边 {fr}→{to} 在 regex 中未找到相邻阶段（from 后紧跟 to）"
        )


def validate_pattern_edges(regex: str, edges_raw: Any) -> list[dict[str, str]]:
    """normalize + 与 regex 一致性校验；供创建/更新入口调用。"""
    edges = normalize_edges(edges_raw)
    validate_edges_against_regex(regex, edges)
    return edges


def _date_iso(d: Any) -> str:
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def find_edge_hits_in_span(
    *,
    code: str,
    raw_zones: Sequence[str],
    closes: Sequence[float],
    dates: Sequence[Any],
    start_idx: int,
    end_idx: int,
    edges: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    """
    在命中半开区间 [start_idx, end_idx) 内，对每条边找至少一次满足谓词的 Arrival。
    返回证据列表；若某条边一次都没有，返回 []（调用方视为失败）。
    """
    if not edges:
        return []
    n = len(raw_zones)
    if not (0 <= start_idx < end_idx <= n):
        return []
    if len(closes) != n or len(dates) != n:
        raise ValueError("raw_zones / closes / dates 长度须一致")

    hits: list[dict[str, Any]] = []
    for edge in edges:
        fr = edge["from"]
        to = edge["to"]
        when = edge["when"]
        found: dict[str, Any] | None = None
        for i in range(max(start_idx, 1), end_idx):
            if raw_zones[i] != to or raw_zones[i - 1] != fr:
                continue
            if when == "limit_up":
                if not is_limit_up(code, closes[i], closes[i - 1]):
                    continue
            else:
                continue
            found = {
                "from": fr,
                "to": to,
                "when": when,
                "date": _date_iso(dates[i]),
                "idx": i,
            }
            break  # any：取第一次
        if found is None:
            return []
        hits.append(found)
    return hits


def apply_edge_filter_to_matches(
    matches: list[dict],
    *,
    code: str,
    raw_zones: Sequence[str],
    closes: Sequence[float],
    dates: Sequence[Any],
    edges: Sequence[Mapping[str, str]] | None,
) -> list[dict]:
    """
    正则命中二次过滤：无 edges 原样返回并补 edge_hits=[]；
    有 edges 则仅保留全部边均至少命中一次的 match，并写入 edge_hits。
    """
    edge_list = list(edges or [])
    if not edge_list:
        for m in matches:
            m["edge_hits"] = []
        return matches

    kept: list[dict] = []
    for m in matches:
        hits = find_edge_hits_in_span(
            code=code,
            raw_zones=raw_zones,
            closes=closes,
            dates=dates,
            start_idx=int(m["start_idx"]),
            end_idx=int(m["end_idx"]),
            edges=edge_list,
        )
        if len(hits) < len(edge_list):
            continue
        m["edge_hits"] = hits
        kept.append(m)
    return kept
