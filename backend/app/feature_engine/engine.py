# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 特征工程计算系统 (Feature Engine)
职责：将基础技术指标与原始 K 线进行无量纲归一化，支持 A 股 LNF 板块涨跌幅拉平、一字板无感插值容错。
"""

import pandas as pd
import numpy as np

def get_lnf_multiplier(code: str) -> float:
    """
    根据 A 股证券编码，自动计算主创板块波动归一化系数 (LNF)
    - 主板 (Limit: 10%) -> LNF = 1.0
    - 创业板 (Limit: 20%) / 科创板 (Limit: 20%) -> LNF = 0.5
    - 北交所 (Limit: 30%) -> LNF = 0.333333
    任何价格波动、收益率、振幅、ATR、波动率特征乘以 LNF，统一换算为“主板等效波动率”
    """
    code_lower = code.lower().strip()
    # 创业板 30 开头，科创板 68 开头
    if code_lower.startswith(("sz30", "sh68")):
        return 0.5
    # 北交所 bj 开头
    elif code_lower.startswith("bj"):
        return 10.0 / 30.0
    # 其余主板默认 1.0
    return 1.0

def calculate_features(df_indicators: pd.DataFrame, code: str) -> pd.DataFrame:
    """
    对已计算好客观技术指标的单股 DataFrame 进行无量纲特征矩阵提取
    输入 DataFrame 需包含：date, open, high, low, close, volume, amount 
    以及客观指标：ma5, ma10, ma20, ma60, ma120, boll_mid, boll_upper, boll_lower, boll_width, boll_width_delta, rsi14, atr14, volume_ma20, volume_ratio_20
    
    返回包含了多维特征字段的 DataFrame：
    - 趋势特征：return_1d, return_5d, return_20d, ma5_above_ma10, ma10_above_ma20, ma20_slope, ma60_slope
    - 布林带特征：boll_upper_dist, boll_mid_dist, boll_lower_dist, boll_width, boll_width_delta, boll_mid_slope
    - 成交量特征：volume_ratio_20, amount_ratio_20
    - K线结构特征：body_ratio, upper_shadow_ratio, lower_shadow_ratio, close_position, is_limit_one_line
    - 波动率特征：atr_ratio, range_ratio, volatility_20d
    """
    if df_indicators is None or df_indicators.empty:
        return pd.DataFrame()

    df = df_indicators.sort_values("date").copy()
    lnf = get_lnf_multiplier(code)

    # -------------------------------------------------------------------------
    # 1. 一字板极端形态检测
    # -------------------------------------------------------------------------
    # A股一字板定义：最高价等于最低价 (high == low)
    # 本地即使微小波动（如高低相差一分钱），亦由于舍入误差或分红价格，可用 0.0001 来界定
    df['is_limit_one_line'] = np.where(df['high'] - df['low'] < 0.0002, 1.0, 0.0)

    # -------------------------------------------------------------------------
    # 2. 收益率与趋势特征 (均乘以 LNF 消除板块波幅差异)
    # -------------------------------------------------------------------------
    df['return_1d'] = df['close'].pct_change(1) * lnf
    df['return_5d'] = df['close'].pct_change(5) * lnf
    df['return_20d'] = df['close'].pct_change(20) * lnf

    # 均线多头相对排布 (bool 特征，存储为 1.0/0.0)
    df['ma5_above_ma10'] = np.where(df['ma5'] > df['ma10'], 1.0, 0.0)
    df['ma10_above_ma20'] = np.where(df['ma10'] > df['ma20'], 1.0, 0.0)

    # 均线斜率特征 (基于 5 日百分比变动，衡量趋势强弱，乘以 LNF 归一化)
    df['ma20_slope'] = df['ma20'].pct_change(5) * lnf
    df['ma60_slope'] = df['ma60'].pct_change(5) * lnf

    # -------------------------------------------------------------------------
    # 3. 布林带特征 (无量纲归一化，不含价格绝对值)
    # -------------------------------------------------------------------------
    # 距离计算：(收盘价 - 轨道价) / 收盘价
    df['boll_upper_dist'] = (df['close'] - df['boll_upper']) / df['close']
    df['boll_mid_dist'] = (df['close'] - df['boll_mid']) / df['close']
    df['boll_lower_dist'] = (df['close'] - df['boll_lower']) / df['close']
    
    # 布林带斜率
    df['boll_mid_slope'] = df['boll_mid'].pct_change(5) * lnf

    # -------------------------------------------------------------------------
    # 4. 成交量特征 (无量纲倍率，不含成交量绝对值)
    # -------------------------------------------------------------------------
    # 成交额 20日均额比率
    amount_ma20 = df['amount'].rolling(window=20, min_periods=20).mean()
    df['amount_ratio_20'] = np.where(
        amount_ma20 > 0.0,
        df['amount'] / amount_ma20,
        0.0
    )

    # -------------------------------------------------------------------------
    # 5. K 线结构特征 (含有除零容错与一字板插值继承)
    # -------------------------------------------------------------------------
    high_low_range = df['high'] - df['low']
    
    # 计算未处理一字板时的常规 K 线比例
    raw_body_ratio = (df['close'] - df['open']).abs() / high_low_range
    # 上影线：对于阳线是 high - close，对于阴线是 high - open
    raw_upper_shadow = (df['high'] - df[['open', 'close']].max(axis=1)) / high_low_range
    # 下影线：对于阳线是 open - low，对于阴线是 close - low
    raw_lower_shadow = (df[['open', 'close']].min(axis=1) - df['low']) / high_low_range
    # 收盘价在全天振幅中的相对落点百分比 (0.0 代表最低，1.0 代表最高)
    raw_close_position = (df['close'] - df['low']) / high_low_range

    # 5.1 组装并应用一字板插值修正 (一字板时，K线实体/影线直接继承上一交易日的有效值，避免除0崩溃)
    df['body_ratio'] = np.where(df['is_limit_one_line'] > 0.5, np.nan, raw_body_ratio)
    df['upper_shadow_ratio'] = np.where(df['is_limit_one_line'] > 0.5, np.nan, raw_upper_shadow)
    df['lower_shadow_ratio'] = np.where(df['is_limit_one_line'] > 0.5, np.nan, raw_lower_shadow)
    df['close_position'] = np.where(df['is_limit_one_line'] > 0.5, np.nan, raw_close_position)

    # 对 NaN 的部分（即一字板的那天）使用 ffill 强制平滑继承
    df['body_ratio'] = df['body_ratio'].ffill().fillna(0.0)
    df['upper_shadow_ratio'] = df['upper_shadow_ratio'].ffill().fillna(0.0)
    df['lower_shadow_ratio'] = df['lower_shadow_ratio'].ffill().fillna(0.0)
    df['close_position'] = df['close_position'].ffill().fillna(0.5) # 默认处于中间

    # 5.2 一字板特殊成交量规避
    # 根据 PRD/SAD，一字板当天几乎无交易摩擦，成交量相对均量直接归零
    df['volume_ratio_20'] = np.where(df['is_limit_one_line'] > 0.5, 0.0, df['volume_ratio_20'])
    df['amount_ratio_20'] = np.where(df['is_limit_one_line'] > 0.5, 0.0, df['amount_ratio_20'])

    # -------------------------------------------------------------------------
    # 6. 波动率与回撤特征 (乘以 LNF 进行标准化)
    # -------------------------------------------------------------------------
    # ATR 与价格比值，统一缩放到主板波动等效值
    df['atr_ratio'] = (df['atr14'] / df['close']) * lnf
    # 振幅比值：(High - Low) / Close
    df['range_ratio'] = (high_low_range / df['close']) * lnf
    
    # 20 日历史收益波动率
    df['volatility_20d'] = df['return_1d'].rolling(window=20, min_periods=20).std()

    # -------------------------------------------------------------------------
    # 7. 全历史回撤特征 (自窗口高点回撤)
    # -------------------------------------------------------------------------
    # 使用 20 日滑动高点，计算自高点以来的回撤深度 (无量纲)
    rolling_high = df['close'].rolling(window=20, min_periods=20).max()
    df['drawdown_20d'] = (rolling_high - df['close']) / rolling_high

    return df
