# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 指标与特征计算引擎 TDD 单元测试集
"""

import unittest
import pandas as pd
import numpy as np

from backend.app.indicator_engine.engine import calculate_indicators
from backend.app.feature_engine.engine import calculate_features, get_lnf_multiplier

class TestIndicatorAndFeatureEngine(unittest.TestCase):

    def setUp(self):
        """
        初始化标准测试序列，包含正常波动、一字涨停极端价格以及停牌假数据。
        """
        # 1. 模拟 150 天主板股票价格（基础价 10.0 元，每日轻微波动）
        np.random.seed(42)
        dates = pd.date_range(start="2026-01-01", periods=150)
        
        # 基础平稳震荡价格
        closes = [10.0]
        for i in range(149):
            closes.append(closes[-1] * (1.0 + np.random.uniform(-0.02, 0.02)))
            
        opens = [c * (1.0 + np.random.uniform(-0.01, 0.01)) for c in closes]
        highs = [max(o, c) * (1.0 + np.random.uniform(0.0, 0.015)) for o, c in zip(opens, closes)]
        lows = [min(o, c) * (1.0 - np.random.uniform(0.0, 0.015)) for o, c in zip(opens, closes)]
        volumes = [int(np.random.uniform(1000, 5000)) for _ in range(150)]
        amounts = [v * c for v, c in zip(volumes, closes)]
        
        self.df_main = pd.DataFrame({
            "date": dates.date,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "amount": amounts
        })

    def test_basic_indicators_calc(self):
        """
        测试客观技术指标：120日均线、布林通道宽度、ATR14 以及 RSI14 的正常生成和边界检查。
        """
        df_ind = calculate_indicators(self.df_main)
        
        # 1. 检查指标列是否存在
        required_cols = [
            'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 
            'boll_mid', 'boll_upper', 'boll_lower', 'boll_width', 'boll_width_delta',
            'rsi14', 'atr14', 'volume_ma20', 'volume_ratio_20'
        ]
        for col in required_cols:
            self.assertIn(col, df_ind.columns, f"缺失技术指标字段: {col}")
            
        # 2. 检查数据前期的 NaN 保护机制 (上市不满 120 天的前期 MA120 应该输出 NaN)
        self.assertTrue(pd.isna(df_ind['ma120'].iloc[0]))
        self.assertTrue(pd.isna(df_ind['ma120'].iloc[118]))
        self.assertFalse(pd.isna(df_ind['ma120'].iloc[120])) # 120天之后成功输出

        # 3. 检查停牌日价格平滑容错
        df_suspended = self.df_main.copy()
        # 模拟第 50 到 55 天停牌：成交量为 0
        df_suspended.loc[50:55, 'volume'] = 0
        df_suspended_ind = calculate_indicators(df_suspended)
        
        # 停牌价格不应该出现空，应正常顺延前一日价格
        self.assertFalse(df_suspended_ind['close'].isna().any())
        self.assertEqual(df_suspended_ind['close'].iloc[52], df_suspended_ind['close'].iloc[49])

    def test_lnf_board_normalization(self):
        """
        验证 LNF (板块限幅归一化) 技术：
        主板波动 5% vs 创业板波动 10% 的两个形态，经过 LNF 折算后，
        其特征数值应该完全相等（特征差异距离接近于 0）。
        """
        # 1. 验证 LNF 乘数计算
        self.assertEqual(get_lnf_multiplier("sh600519"), 1.0) # 贵州茅台 (主板)
        self.assertEqual(get_lnf_multiplier("sz300002"), 0.5) # 东方财富 (创业板)
        self.assertEqual(get_lnf_multiplier("sh688001"), 0.5) # 华熙生物 (科创板)
        self.assertAlmostEqual(get_lnf_multiplier("bj830001"), 0.333333, places=5) # 北交所
        
        # 2. 模拟主板和创业板形态一模一样、但由于涨跌幅限制导致振幅正比放大 2 倍的走势
        dates = pd.date_range(start="2026-01-01", periods=50)
        
        # 主板价格变动
        close_main = [10.0]
        high_main = [10.5]
        low_main = [9.5]
        open_main = [10.0]
        # 创业板价格变动 (振幅完美放大 2 倍)
        close_cyb = [10.0]
        high_cyb = [11.0]
        low_cyb = [9.0]
        open_cyb = [10.0]
        
        for _ in range(49):
            close_main.append(close_main[-1] * 1.05) # +5%
            high_main.append(close_main[-1] * 1.06)
            low_main.append(close_main[-1] * 0.94)
            open_main.append(close_main[-1] * 1.0)
            
            close_cyb.append(close_cyb[-1] * 1.10) # +10% (振幅收益率完美放大 2 倍)
            high_cyb.append(close_cyb[-1] * 1.12)
            low_cyb.append(close_cyb[-1] * 0.88)
            open_cyb.append(close_cyb[-1] * 1.0)
            
        df_sim_main = pd.DataFrame({"date": dates.date, "open": open_main, "high": high_main, "low": low_main, "close": close_main, "volume": 1000, "amount": 10000})
        df_sim_cyb = pd.DataFrame({"date": dates.date, "open": open_cyb, "high": high_cyb, "low": low_cyb, "close": close_cyb, "volume": 1000, "amount": 10000})
        
        # 计算特征
        feat_main = calculate_features(calculate_indicators(df_sim_main), "sh600000") # 主板
        feat_cyb = calculate_features(calculate_indicators(df_sim_cyb), "sz300002") # 创业板
        
        # 比对 1日变动率，经过 LNF 折算后应该 100% 对齐相等！
        # 主板 5% * 1.0 = 5%， 创业板 10% * 0.5 = 5%
        for i in range(20, 50):
            self.assertAlmostEqual(feat_main['return_1d'].iloc[i], feat_cyb['return_1d'].iloc[i], places=4)
            self.assertAlmostEqual(feat_main['range_ratio'].iloc[i], feat_cyb['range_ratio'].iloc[i], places=4)

    def test_one_line_limit_board_interpolation(self):
        """
        验证“一字板”极端形态插值修正功能：
        连续 3 天一字涨停（高、低、开、收全相等且单日涨幅到达限额），
        1. 确保不会产生 NaN 或除以零崩溃。
        2. K线实体比例、上下影线等直接从前一日前复权历史中继承。
        3. 成交量比率被强制置为 0.0。
        4. is_limit_one_line 标记为 1.0。
        """
        # 1. 模拟一字板（在正常交易的第 100 天突然连续 3 天一字涨停）
        df_one_line = self.df_main.copy()
        
        # 正常的前一日数据作为特征继承源
        ref_close = df_one_line.loc[99, 'close']
        
        # 连续 3 天一字涨停：High=Low=Open=Close=前收盘*1.1
        for i, idx in enumerate([100, 101, 102], 1):
            limit_price = ref_close * (1.10 ** i)
            df_one_line.loc[idx, 'open'] = limit_price
            df_one_line.loc[idx, 'high'] = limit_price
            df_one_line.loc[idx, 'low'] = limit_price
            df_one_line.loc[idx, 'close'] = limit_price
            df_one_line.loc[idx, 'volume'] = 50 # 象征性的极其微小的过桥成交量

        # 2. 执行计算
        df_ind = calculate_indicators(df_one_line)
        df_feat = calculate_features(df_ind, "sh600000") # 主板

        # 3. 校验容错性
        # 3.1 检查是否存在任何 NaN 空值
        self.assertFalse(df_feat.loc[100:102, ['body_ratio', 'upper_shadow_ratio', 'lower_shadow_ratio', 'close_position']].isna().any().any())

        # 3.2 检查一字板当天是否成功继承了前一交易日（第 99 天）的常规 K 线身体比例
        self.assertEqual(df_feat.loc[100, 'body_ratio'], df_feat.loc[99, 'body_ratio'])
        self.assertEqual(df_feat.loc[101, 'upper_shadow_ratio'], df_feat.loc[99, 'upper_shadow_ratio'])
        self.assertEqual(df_feat.loc[102, 'lower_shadow_ratio'], df_feat.loc[99, 'lower_shadow_ratio'])

        # 3.3 检查成交量倍率是否被强制归零
        self.assertEqual(df_feat.loc[100, 'volume_ratio_20'], 0.0)
        self.assertEqual(df_feat.loc[101, 'amount_ratio_20'], 0.0)

        # 3.4 检查一字板标记是否生效
        self.assertEqual(df_feat.loc[100, 'is_limit_one_line'], 1.0)
        self.assertEqual(df_feat.loc[103, 'is_limit_one_line'], 0.0) # 后续正常日子恢复为 0


    def test_suspended_day_not_misclassified_as_one_line(self):
        """
        P0-1 回归测试：停牌日（volume=0）不应被误判为“一字板”。
        一字板定义要求有成交量且高低价差极小。
        """
        df_suspended = self.df_main.copy()
        # 模拟第 50 天停牌：成交量归零，价格不变（high == low）
        df_suspended.loc[50, 'volume'] = 0
        df_suspended.loc[50, 'amount'] = 0
        ref_close = df_suspended.loc[49, 'close']
        fixed_price = ref_close * 1.10  # 复牌后涨停价
        df_suspended.loc[50, 'open'] = fixed_price
        df_suspended.loc[50, 'high'] = fixed_price
        df_suspended.loc[50, 'low'] = fixed_price
        df_suspended.loc[50, 'close'] = fixed_price
        
        df_ind = calculate_indicators(df_suspended)
        df_feat = calculate_features(df_ind, "sh600000")
        
        # 停牌日 volume=0，即使 high==low，is_limit_one_line 应为 0.0
        self.assertEqual(df_feat.loc[50, 'is_limit_one_line'], 0.0)
        
        # 正常一字板（volume > 0 且 high == low）应标记为 1.0
        df_one_line = self.df_main.copy()
        for i in [100, 101, 102]:
            limit_price = df_one_line.loc[i-1, 'close'] * 1.10
            df_one_line.loc[i, 'open'] = limit_price
            df_one_line.loc[i, 'high'] = limit_price
            df_one_line.loc[i, 'low'] = limit_price
            df_one_line.loc[i, 'close'] = limit_price
            df_one_line.loc[i, 'volume'] = 50  # 象征性微量成交
        df_ind_ol = calculate_indicators(df_one_line)
        df_feat_ol = calculate_features(df_ind_ol, "sh600000")
        self.assertEqual(df_feat_ol.loc[100, 'is_limit_one_line'], 1.0)
        self.assertEqual(df_feat_ol.loc[101, 'is_limit_one_line'], 1.0)
        self.assertEqual(df_feat_ol.loc[102, 'is_limit_one_line'], 1.0)

if __name__ == "__main__":
    unittest.main()
