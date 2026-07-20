# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 高斯模糊事件识别引擎 TDD 单元测试集
"""

import unittest
import pandas as pd
import numpy as np

from backend.app.indicator_engine.engine import calculate_indicators
from backend.app.feature_engine.engine import calculate_features
from backend.app.event_engine.engine import EventEngine, calculate_gaussian_confidence

class TestEventEngine(unittest.TestCase):

    def setUp(self):
        """
        构建一条高度逼真、包含“趋势上涨 -> 贴轨 -> 缩量回调 -> 精准回踩中轨 -> 止跌金针 -> 倍量二次启动”的 60 日闭环形态时序序列。
        """
        dates = pd.date_range(start="2026-01-01", periods=60)
        
        # 建立高仿真价格路径
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []
        
        # 初始基准价
        p = 10.0
        
        for d in range(60):
            # 前 20 天：强烈多头单边主升浪 (MA多头，并贴近布林上轨)
            if d < 20:
                p_next = p * 1.03 # 每日稳步 +3%
                o, c = p, p_next
                h = c * 1.01
                l = o * 0.99
                v = int(2000 + d * 100) # 温和放量
            # 21-40 天：温和缩量回调 (回调 10% 左右)
            elif d < 40:
                p_next = p * 0.993 # 每日温和阴跌 -0.7% (20天共回调约13%)
                o, c = p, p_next
                h = o * 1.005
                l = c * 0.995
                v = int(3000 * (0.95 ** (d - 20))) # 回调期间成交量呈指数级衰减缩量 (地量)
            # 第 41 天：精准重踩布林中轨，长影线金针探底
            elif d == 40:
                # 这一天我们要制造一个大下影线的锤子止跌线
                o = p
                c = p * 1.005 # 小阳实体
                h = c * 1.005
                l = p * 0.95  # 砸出大长腿，砸向此时的布林中轨
                v = 500 # 极度地量
                p_next = c
            # 42-50 天：中轨上方守住盘整
            elif d < 50:
                p_next = p * 1.005 # 极窄幅回升
                o, c = p, p_next
                h = c * 1.005
                l = o * 0.995
                v = 800
            # 51-60 天：倍量突破大阳线二次启动
            else:
                p_next = p * 1.04 # 暴涨 +4%
                o, c = p, p_next
                h = c * 1.015
                l = o * 0.99
                v = 6000 # 巨量突破
                
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)
            p = p_next

        amounts = [v * c for v, c in zip(volumes, closes)]
        self.df_sim = pd.DataFrame({
            "date": dates.date,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "amount": amounts
        })

    def test_gaussian_confidence_math(self):
        """
        验证高斯模糊正态置信度函数的底层数学计算和极端情况。
        """
        # 完美贴合，置信度恒为 1.0
        self.assertEqual(calculate_gaussian_confidence(0.0, mu=0.0, sigma=0.015), 1.0)
        self.assertEqual(calculate_gaussian_confidence(5.0, mu=5.0, sigma=2.0), 1.0)
        
        # 1.0 倍标准差处，置信度 = exp(-0.5) ≈ 0.6065
        conf_1std = calculate_gaussian_confidence(0.015, mu=0.0, sigma=0.015)
        self.assertAlmostEqual(conf_1std, 0.60653, places=4)
        
        # 极端偏差 (3倍标准差之外)，置信度极低
        conf_extreme = calculate_gaussian_confidence(0.10, mu=0.0, sigma=0.015)
        self.assertLess(conf_extreme, 0.0001)
        
        # 容错：x 为 NaN 时应该优雅返回 0.0，不崩溃
        self.assertEqual(calculate_gaussian_confidence(np.nan, mu=0.0, sigma=0.015), 0.0)

    def test_full_fuzzy_event_detection(self):
        """
        将仿真走势带入“指标 -> 特征 -> 事件”，验证 8 大 MVP 交易事件的柔性模糊检测。
        """
        # 1. 运算整套底座
        df_ind = calculate_indicators(self.df_sim)
        df_feat = calculate_features(df_ind, "sh600000")
        
        # 2. 传入事件识别器
        engine = EventEngine()
        events = engine.detect_all_events(df_feat)
        
        # 3. 验证 8 大事件均被成功识别，且置信度均处于合理、良性的概率区间 (0.0 ~ 1.0]
        required_events = [
            "TREND_UP",
            "TOUCH_BOLL_UPPER",
            "PULLBACK",
            "VOLUME_SHRINK",
            "TOUCH_BOLL_MIDDLE",
            "BOLL_MIDDLE_SUPPORT",
            "STOP_FALLING_CANDLE",
            "VOLUME_BREAKOUT"
        ]
        
        print("\n" + "="*50)
        print("     高斯模糊事件柔性概率识别大捷结果清单     ")
        print("="*50)
        for evt_type in required_events:
            self.assertIn(evt_type, events, f"事件引擎漏检了关键事件: {evt_type}")
            evt = events[evt_type]
            print(f"🌟 事件 [{evt.event_type:<20}] | 柔性置信度: {evt.confidence:.4f} | 证据 (evidence): {evt.evidence}")
            
            # 校验连续值合理性
            self.assertGreater(evt.confidence, 0.05, f"事件 {evt_type} 置信度过低，请检查算法适配")
            self.assertLessEqual(evt.confidence, 1.0)
            
        print("="*50 + "\n")

if __name__ == "__main__":
    unittest.main()
