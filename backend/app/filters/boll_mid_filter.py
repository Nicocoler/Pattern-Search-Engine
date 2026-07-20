# -*- coding: utf-8 -*-
"""
布林中轨硬过滤 (BOLL Mid Hard Filter)
职责：在相似度计算之前，硬性淘汰"回调到中轨后收盘价跌破中轨"的候选标的。
规则：
  1. 窗口内必须曾出现 close >= boll_mid（说明此前在中轨上方运行）
  2. 在此之后，任何一天 close < boll_mid（严格低于）→ 判定为"跌破"，返回 True
  3. 若从未在中轨上方出现过，或从未跌破，返回 False
  4. close == boll_mid 不算跌破（贴合中轨仍视为支撑有效）
  5. NaN 暖机期自动跳过，不影响判断
"""

import pandas as pd


def has_boll_mid_breach(df_window: pd.DataFrame) -> bool:
    """
    状态机检测：是否存在"先在中轨上方，后收盘价跌破中轨"的情形。

    Args:
        df_window: 已包含 boll_mid、close 列的特征窗口 DataFrame，按 date 升序排列。

    Returns:
        True 表示存在跌破行为，应淘汰；False 表示未跌破或无法判断（无有效数据）。
    """
    if df_window is None or df_window.empty:
        return False

    # 提取有效行，按日期排序确保时间顺序正确
    valid = df_window[['date', 'close', 'boll_mid']].dropna(subset=['boll_mid', 'close']).copy()
    valid = valid.sort_values('date').reset_index(drop=True)

    if valid.empty:
        return False

    saw_above = False
    for _, row in valid.iterrows():
        close = row['close']
        mid = row['boll_mid']

        if close >= mid:
            # 曾在中轨上方（含贴合），标记为已见过
            saw_above = True
        elif close < mid and saw_above:
            # 在见过中轨上方的前提下，收盘价严格低于中轨 → 跌破
            return True

    return False
