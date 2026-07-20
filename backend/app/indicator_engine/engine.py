# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 基础指标计算引擎 (Indicator Engine)
职责：将日K时序行情数据计算为标准技术指标，内置完美的停牌、新股极值容错机制。
"""

import pandas as pd
import numpy as np

def calculate_indicators(df_bars: pd.DataFrame) -> pd.DataFrame:
    """
    对单只股票的历史日K线 DataFrame 批量计算客观指标
    输入 DataFrame 包含列：date, open, high, low, close, volume, amount
    返回 DataFrame 包含原有列以及计算派生的技术指标列：
    MA5, MA10, MA20, MA60, MA120, BOLL 中/上/下轨, RSI14, ATR14, volume_ma20, volume_ratio_20
    
    容错机制：
    1. 遇到停牌日（成交量为0或价格不波动），成交量设为0，价格沿用前一交易日（ffill 填充价格）。
    2. 新股上市不满计算窗口（如MA120），数据不足的行统一输出 NaN / None。
    """
    if df_bars is None or df_bars.empty:
        return pd.DataFrame()

    # 1. 确保按日期严格升序排列，并重置索引确保 0-based，拷贝数据避免原地修改污染
    df = df_bars.sort_values("date").reset_index(drop=True).copy()
    
    # 2. 停牌日极值容错与价格平滑
    # 若成交量为 0，说明处于停牌状态，此时价格不产生波动。我们将其设为 NaN，并通过 ffill / bfill 无缝顺延前一日价格
    is_suspended = (df['volume'] == 0)
    df.loc[is_suspended, ['open', 'high', 'low', 'close']] = np.nan
    
    df['close'] = df['close'].ffill().bfill()
    df['open'] = df['open'].ffill().bfill()
    df['high'] = df['high'].ffill().bfill()
    df['low'] = df['low'].ffill().bfill()
    
    # 3. 基础均线计算 (MA5、MA10、MA20、MA60、MA120)
    df['ma5'] = df['close'].rolling(window=5, min_periods=5).mean()
    df['ma10'] = df['close'].rolling(window=10, min_periods=10).mean()
    df['ma20'] = df['close'].rolling(window=20, min_periods=20).mean()
    df['ma60'] = df['close'].rolling(window=60, min_periods=60).mean()
    df['ma120'] = df['close'].rolling(window=120, min_periods=120).mean()

    # 4. 布林带精准对齐计算 (BOLL)
    # 严格对齐通达信公式：
    #   N:=20;
    #   MID:=MA(CLOSE,N);                                    -- 20日简单移动平均
    #   VART1:=POW((CLOSE-MID),2); VART2:=MA(VART1,N);      -- 总体方差（除以N）
    #   VART3:=SQRT(VART2);                                  -- 总体标准差
    #   UPPER:=MID + 2*VART3; LOWER:=MID - 2*VART3;
    #   BOLL:REF(MID,1); UB:REF(UPPER,1); LB:REF(LOWER,1);  -- 输出滞后1根K线

    # 4.1 中轨：20日简单移动平均 (SMA)，非 EMA
    mid = df['close'].rolling(window=20, min_periods=20).mean()

    # 4.2 标准差：基于中轨的总体标准差（除以N，非样本标准差除以N-1）
    vart1 = (df['close'] - mid) ** 2
    vart2 = vart1.rolling(window=20, min_periods=20).mean()
    vart3 = np.sqrt(vart2)

    # 4.3 上轨 / 下轨
    upper = mid + 2.0 * vart3
    lower = mid - 2.0 * vart3

    # 4.4 REF(MID,1) / REF(UPPER,1) / REF(LOWER,1) —— 滞后1根K线输出
    # 与通达信一致：当日显示的BOLL值 = 前一日基于截至前一日收盘价计算的轨道
    df['boll_mid'] = mid.shift(1)
    df['boll_upper'] = upper.shift(1)
    df['boll_lower'] = lower.shift(1)

    # 截断暖机期：rolling需20日数据，shift再丢1行，故前20行无效
    df['boll_mid'] = np.where(df.index >= 20, df['boll_mid'], np.nan)
    df['boll_upper'] = np.where(df.index >= 20, df['boll_upper'], np.nan)
    df['boll_lower'] = np.where(df.index >= 20, df['boll_lower'], np.nan)
    
    # 4.1 计算布林带宽度及带宽变化 Width = (Upper - Lower) / Middle
    # 预防 middle 出现 0 导致零除异常
    df['boll_width'] = np.where(
        df['boll_mid'] > 0.001,
        (df['boll_upper'] - df['boll_lower']) / df['boll_mid'],
        np.nan
    )
    # 带宽 1 日变化率，表征通道是扩张（放量启动）还是收敛（横盘窄幅休整）
    df['boll_width_delta'] = df['boll_width'].diff()

    # 5. 波动率 ATR14 计算
    # TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
    prev_close = df['close'].shift(1)
    tr_1 = df['high'] - df['low']
    tr_2 = (df['high'] - prev_close).abs()
    tr_3 = (df['low'] - prev_close).abs()
    tr = pd.concat([tr_1, tr_2, tr_3], axis=1).max(axis=1)
    
    # 首行 TR 若因无 prev_close 为 NaN，使用 high - low 填充
    tr.iloc[0] = df['high'].iloc[0] - df['low'].iloc[0]
    
    df['atr14'] = tr.rolling(window=14, min_periods=14).mean()

    # 6. 经典 RSI14 计算 (使用标准 Simple Moving Average 计算以完美对齐规则)
    change = df['close'].diff()
    gain = change.clip(lower=0.0)
    loss = -change.clip(upper=0.0)
    
    avg_gain = gain.rolling(window=14, min_periods=14).mean()
    avg_loss = loss.rolling(window=14, min_periods=14).mean()
    
    # RSI = avg_gain / (avg_gain + avg_loss) * 100
    total_movement = avg_gain + avg_loss
    df['rsi14'] = np.where(
        total_movement > 0.0,
        (avg_gain / total_movement) * 100.0,
        50.0 # 若无任何涨跌幅波动，中性 RSI 默认为 50
    )

    # 7. 成交量特征指标计算 (volume_ma20, volume_ratio_20)
    df['volume_ma20'] = df['volume'].rolling(window=20, min_periods=20).mean()
    df['volume_ratio_20'] = np.where(
        df['volume_ma20'] > 0.0,
        df['volume'] / df['volume_ma20'],
        0.0 # 20日均量为 0（如新上市或极度停牌），倍率设为 0
    )

    return df
