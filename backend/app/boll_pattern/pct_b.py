# -*- coding: utf-8 -*-
"""%B = (close - lower) / (upper - lower)。带宽为 0 时记为 NaN。"""

import pandas as pd


def calc_pct_b(close: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """返回与输入等长的 %B 序列。"""
    close = pd.to_numeric(close, errors="coerce")
    upper = pd.to_numeric(upper, errors="coerce")
    lower = pd.to_numeric(lower, errors="coerce")
    band_width = upper - lower
    pct_b = (close - lower) / band_width
    pct_b = pct_b.where(band_width != 0)
    return pct_b.astype(float)
