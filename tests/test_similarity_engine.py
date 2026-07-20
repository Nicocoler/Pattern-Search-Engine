# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 多维加权时序对齐相似度搜索算法 TDD 单元测试集
"""

import unittest
import pandas as pd
import numpy as np

from backend.app.indicator_engine.engine import calculate_indicators
from backend.app.feature_engine.engine import calculate_features
from backend.app.similarity_engine.engine import SimilarityEngine

class TestSimilarityEngine(unittest.TestCase):

    def setUp(self):
        """
        初始化标准测试形态时序，构造模板形态、相似的延迟形态、以及破位惩罚形态。
        """
        dates = pd.date_range(start="2026-01-01", periods=60)
        
        # 1. 模拟标准模板形态：经典温和主升浪 -> 缩量回调 -> 回踩中轨 -> 倍量二次启动
        self.df_template = self._generate_simulated_bars(dates, lag=0, broke=False)
        
        # 2. 模拟相似候选（延迟形态）：回调时间多拉长了 3 天，但最终依然完美回踩企稳二次拉升
        # 传统线性欧氏距离会因为这 3 天的平移差错判为完全不相似，但多维 DTW 应该能完美时间对齐并给出高分！
        self.df_similar_lag = self._generate_simulated_bars(dates, lag=3, broke=False)
        
        # 3. 模拟破位候选（惩罚形态）：前 55 天走势极佳，但最后 3 天跌破下轨和生命均线
        self.df_broken = self._generate_simulated_bars(dates, lag=0, broke=True)

    def _generate_simulated_bars(self, dates, lag=0, broke=False) -> pd.DataFrame:
        opens = []
        highs = []
        lows = []
        closes = []
        volumes = []
        
        p = 10.0
        
        for d in range(60):
            # 1. 上涨阶段 (15天)
            if d < 15:
                p_next = p * 1.025
                o, c = p, p_next
                h = c * 1.01
                l = o * 0.99
                v = int(2000 + d * 100)
            # 2. 纯回调阶段 (d从15到30+lag)
            elif d < 30 + lag:
                p_next = p * 0.992
                o, c = p, p_next
                h = o * 1.005
                l = c * 0.995
                v = int(2500 * (0.94 ** (d - 15))) # 缩量
            # 3. 金针探底回踩中轨日 (30+lag天)
            elif d == 30 + lag:
                o = p
                c = p * 1.005
                h = c * 1.005
                l = p * 0.95 # 砸盘探底
                v = 400
                p_next = c
            # 4. 稳步向上盘整日 (d从31+lag到50)
            elif d < 50:
                p_next = p * 1.006
                o, c = p, p_next
                h = c * 1.005
                l = o * 0.995
                v = 700
            # 5. 放量二次突破阶段 (51至60天)
            else:
                if broke and d >= 57:
                    # 模拟最后 3 天严重向下砸盘暴跌破位
                    p_next = p * 0.75 # 连续放量闪崩大跌 -25%
                    o, c = p, p_next
                    h = o * 1.01
                    l = c * 0.98
                    v = 5000 # 破位大阴线放量
                else:
                    # 正常二次启动大阳线
                    p_next = p * 1.035
                    o, c = p, p_next
                    h = c * 1.015
                    l = o * 0.99
                    v = 5000
                p_next = max(1.0, p_next) # 预防跌穿
            p = p_next
            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)

        amounts = [v * c for v, c in zip(volumes, closes)]
        return pd.DataFrame({
            "date": dates.date,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
            "amount": amounts
        })

    def test_identical_match(self):
        """
        验证绝对自相似性：模板和绝对相同的候选进行匹配时，相似度评分应接近 100 分。
        """
        # 计算基础底座
        feat_temp = calculate_features(calculate_indicators(self.df_template), "sh600000")
        
        engine = SimilarityEngine()
        report = engine.compute_composite_similarity(feat_temp, feat_temp, "sh600000")
        
        self.assertGreaterEqual(report["total_score"], 98.0, "完全一致的两个形态，总分应接近 100")
        self.assertEqual(len(report["alignment_path"]), len(feat_temp), "绝对对齐时的 DTW 路径长度应等于窗口长度")

    def test_dtw_time_warping_match(self):
        """
        验证 DTW 时间轴形变弹力对齐技术：
        相较于模板，候选走势因为回调时间多拉长了 3 天，产生相位平移。
        1. 验证加权多维 DTW 能够顺利吸收这 3 天的时间差，成功将其对齐。
        2. 综合形态匹配分值仍应处于非常高的卓越区间（例如 > 85 分）。
        """
        feat_temp = calculate_features(calculate_indicators(self.df_template), "sh600000")
        feat_cand = calculate_features(calculate_indicators(self.df_similar_lag), "sh600000")
        
        engine = SimilarityEngine()
        report = engine.compute_composite_similarity(feat_temp, feat_cand, "sh600000")
        
        print("\n" + "="*50)
        print("     时间轴相位差拉长（平移 3 天）形态对齐评分报告     ")
        print("="*50)
        print(f"🌟 综合相似度得分 (Total Similarity): {report['total_score']}分")
        print(f"   分项得分细则: {report['score_breakdown']}")
        print(f"   对齐路径点样本: {report['alignment_path'][:10]} ...")
        print(f"   正面特征事实: {report['explanation_facts']['positive_facts']}")
        print("="*50 + "\n")
        
        # 验证弹力对齐分值仍旧优秀
        self.assertGreaterEqual(report["total_score"], 80.0, "有平移差、但整体完美的形态，DTW 归一化得分仍应不低于 80 分")

    def test_risk_penalties(self):
        """
        验证破位惩罚阀门：
        个股虽然前 55 天非常相似，但临近窗口结束最后 3 天收盘价严重砸盘跌破下轨和 60 日线支撑。
        1. 验证风险硬性扣分模块成功捕捉到该行为。
        2. 总得分被强制严厉扣分，从而绝缘过滤于推荐标的名单之外。
        """
        feat_temp = calculate_features(calculate_indicators(self.df_template), "sh600000")
        feat_cand = calculate_features(calculate_indicators(self.df_broken), "sh600000")
        
        engine = SimilarityEngine()
        report = engine.compute_composite_similarity(feat_temp, feat_cand, "sh600000")
        
        print("\n" + "="*50)
        print("          个股破位惩罚扣分与风险拦截报告          ")
        print("="*50)
        print(f"🌟 被严厉扣分后的最终总分: {report['total_score']}分")
        print(f"   负面事实陈述: {report['explanation_facts']['negative_facts']}")
        print("="*50 + "\n")
        
        # 由于大跌砸盘，应该有至少 15 到 20 分的负面硬扣分，总分应该极低（远低于 70 分）
        self.assertLess(report["total_score"], 70.0, "最后 3 天严重砸盘破位个股，必须被风险硬项无情扣分降至 70 分以下！")

if __name__ == "__main__":
    unittest.main()
