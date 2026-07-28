# -*- coding: utf-8 -*-
"""加载 boll_patterns.yaml（仅作种子）；运行时权威源在 repository/DB。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from backend.app.boll_pattern.zone import DEFAULT_ZONE_THRESHOLDS

_DEFAULT_YAML = Path(__file__).with_name("boll_patterns.yaml")


def _parse_bound(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {".inf", "+.inf", "inf", "+inf", "infinity"}:
            return float("inf")
        if s in {"-.inf", "-inf", "-infinity"}:
            return -float("inf")
        return float(s)
    raise TypeError(f"无法解析阈值边界: {value!r}")


def normalize_zone_thresholds(raw: dict | None) -> dict[str, tuple[float, float]]:
    if not raw:
        return dict(DEFAULT_ZONE_THRESHOLDS)
    out: dict[str, tuple[float, float]] = {}
    for label, pair in raw.items():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(f"zone_thresholds[{label}] 必须是 [lo, hi]")
        out[str(label)] = (_parse_bound(pair[0]), _parse_bound(pair[1]))
    for need in ("L", "M", "H", "U"):
        if need not in out:
            raise ValueError(f"zone_thresholds 缺少分区: {need}")
    return out


def thresholds_to_jsonable(thresholds: dict[str, tuple[float, float]]) -> dict[str, list]:
    out: dict[str, list] = {}
    for k, (lo, hi) in thresholds.items():
        lo_v: Any = "-inf" if lo == float("-inf") else lo
        hi_v: Any = "inf" if hi == float("inf") else hi
        out[k] = [lo_v, hi_v]
    return out


def load_boll_patterns(path: str | Path | None = None) -> dict[str, Any]:
    """解析 YAML 种子文件（不读库）。"""
    yaml_path = Path(path) if path else _DEFAULT_YAML
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    thresholds = normalize_zone_thresholds(data.get("zone_thresholds"))
    denoise_min_len = int(data.get("denoise_min_len", 0) or 0)
    patterns = data.get("patterns") or []
    if not isinstance(patterns, list):
        raise ValueError("patterns 必须是列表")

    normalized = []
    for p in patterns:
        if not isinstance(p, dict):
            raise ValueError(f"非法 pattern 项: {p!r}")
        pid = str(p.get("id", "")).strip()
        if not pid:
            raise ValueError("pattern 缺少 id")
        zt_raw = p.get("zone_thresholds")
        normalized.append({
            "id": pid,
            "name": str(p.get("name") or pid),
            "regex": str(p.get("regex") or ""),
            "min_total_days": int(p.get("min_total_days") or 0),
            "enabled": bool(p.get("enabled", True)),
            "zone_thresholds": normalize_zone_thresholds(zt_raw) if zt_raw is not None else None,
            "denoise_min_len": (
                int(p["denoise_min_len"]) if p.get("denoise_min_len") is not None else None
            ),
        })

    enabled = [p for p in normalized if p["enabled"] and p["regex"]]
    return {
        "zone_thresholds": thresholds,
        "denoise_min_len": denoise_min_len,
        "patterns": normalized,
        "enabled_patterns": enabled,
        "yaml_path": str(yaml_path),
    }
