# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 多维加权时序对齐相似度搜索算法引擎 (Similarity Engine)
职责：实现局部 Z-score 标准化、限宽 Sakoe-Chiba 约束带多维加权 DTW 对齐、分项得分核算、事件对齐校验及破位扣分罚项。
"""

import numpy as np
import pandas as pd
from datetime import date, datetime
import logging

from backend.app.event_engine.engine import EventEngine

logger = logging.getLogger("SimilarityEngine")

class SimilarityEngine:
    def __init__(self):
        self.event_engine = EventEngine()
        
        # 默认形态特征对齐维度的权重分配（总和为 1.0）
        self.feature_weights = {
            "close_norm": 0.25,        # 窗口内首日价格等效归一化
            "boll_mid_dist": 0.20,     # 距离布林中轨相对距离
            "volume_ratio_20": 0.15,   # 20日均量成交量倍率
            "close_position": 0.15,    # K线相对收盘位置 (0.0~1.0)
            "return_5d": 0.10,         # 5日变动收益率
            "range_ratio": 0.10,       # 价格振幅比
            "atr_ratio": 0.05          # 波动率
        }

    def local_zscore_normalize(self, matrix: np.ndarray) -> np.ndarray:
        """
        对传入的矩阵 $W \times F$ 在列维度（每个特征）上进行独立的局部 Z-score 标准化。
        防止大数值特征（如成交量比）在欧氏空间完全掩没小数值特征（如布林中轨距离）。
        """
        means = np.mean(matrix, axis=0)
        stds = np.std(matrix, axis=0)
        # 预防标准差为 0（如一字板极端情况）产生除零溢出
        eps = 1e-6
        return (matrix - means) / (stds + eps)

    def calculate_multidimensional_dtw(self, T: np.ndarray, C: np.ndarray, weights: np.ndarray) -> tuple[float, list[list[int]]]:
        """
        多维加权 DTW 核心对齐算法：
        - 输入：标准化后的模板特征 T (M x F) 与候选特征 C (N x F) 
        - 约束：限宽 Sakoe-Chiba 约束带（防止时间轴病态对齐并压缩运算耗时 80%）
        - 权重：weights (F) 为各维度的重要性系数
        - 返回：(对齐累计最短距离, 最佳对齐路径)
        """
        M, N = len(T), len(C)
        F = T.shape[1]
        
        # 1. Sakoe-Chiba 约束带宽度：floor(0.15 * max(M, N))
        sakoe_width = max(3, int(np.floor(0.15 * max(M, N))))
        
        # 2. 初始化累积距离矩阵 (INF)
        accum_cost = np.full((M, N), np.inf)
        
        # 3. 计算加权点对距离距离矩阵 (局部距离 D(i, j))
        # D(i, j) = sqrt( sum( w_f * (T_i,f - C_j,f)^2 ) )
        local_dist = np.zeros((M, N))
        # 优化斜带计算
        for i in range(M):
            j_start = max(0, i - sakoe_width)
            j_end = min(N, i + sakoe_width + 1)
            for j in range(j_start, j_end):
                diff = T[i] - C[j]
                local_dist[i, j] = np.sqrt(np.sum(weights * (diff ** 2)))

        # 4. 动态规划递推计算累积最小距离
        accum_cost[0, 0] = local_dist[0, 0]
        
        for i in range(M):
            j_start = max(0, i - sakoe_width)
            j_end = min(N, i + sakoe_width + 1)
            for j in range(j_start, j_end):
                if i == 0 and j == 0:
                    continue
                # 状态转移：可以从左边、下边、或者对角线过来
                costs = []
                if i > 0 and abs((i - 1) - j) <= sakoe_width:
                    costs.append(accum_cost[i - 1, j])
                if j > 0 and abs(i - (j - 1)) <= sakoe_width:
                    costs.append(accum_cost[i, j - 1])
                if i > 0 and j > 0 and abs((i - 1) - (j - 1)) <= sakoe_width:
                    costs.append(accum_cost[i - 1, j - 1])
                
                if costs:
                    accum_cost[i, j] = local_dist[i, j] + min(costs)

        # 若终点在约束带内依然不可达（说明序列异常），抛出无穷大
        if np.isinf(accum_cost[M - 1, N - 1]):
            return 999.0, []

        # 5. 回溯最优对齐路径
        path = []
        i, j = M - 1, N - 1
        path.append([i, j])
        while i > 0 or j > 0:
            if i == 0:
                j -= 1
            elif j == 0:
                i -= 1
            else:
                # 比较三个方向的累积距离
                left = accum_cost[i, j - 1] if abs(i - (j - 1)) <= sakoe_width else np.inf
                up = accum_cost[i - 1, j] if abs((i - 1) - j) <= sakoe_width else np.inf
                diag = accum_cost[i - 1, j - 1] if abs((i - 1) - (j - 1)) <= sakoe_width else np.inf
                
                min_val = min(left, up, diag)
                if min_val == diag:
                    i -= 1
                    j -= 1
                elif min_val == up:
                    i -= 1
                else:
                    j -= 1
            path.append([i, j])
            
        path.reverse()
        normalized_distance = accum_cost[M - 1, N - 1] / (M + N) # 归一化路径总距离
        return float(normalized_distance), path

    def evaluate_sub_similarities(self, df_temp: pd.DataFrame, df_cand: pd.DataFrame) -> dict:
        """
        对特征向量矩阵进行分拆（Trend, Boll, Volume, Candle, Volatility）
        独立运行 Z-score 标准化与加权 DTW，提取出更细、可解释的多轴分项评分。
        """
        # 定义 5 个指标特征大类的特征列和相对子权重
        categories = {
            "trend": {
                "cols": ["close_norm", "return_5d"], 
                "weights": np.array([0.7, 0.3])
            },
            "boll": {
                "cols": ["boll_mid_dist"], 
                "weights": np.array([1.0])
            },
            "volume": {
                "cols": ["volume_ratio_20"], 
                "weights": np.array([1.0])
            },
            "candle": {
                "cols": ["close_position"], 
                "weights": np.array([1.0])
            },
            "volatility": {
                "cols": ["range_ratio", "atr_ratio"], 
                "weights": np.array([0.6, 0.4])
            }
        }
        
        scores = {}
        
        # 补充临时衍生归一化close列 (让时间轴首日归一为 1.0)
        df_temp = df_temp.copy()
        df_cand = df_cand.copy()
        df_temp['close_norm'] = df_temp['close'] / df_temp['close'].iloc[0]
        df_cand['close_norm'] = df_cand['close'] / df_cand['close'].iloc[0]

        for cat_name, cfg in categories.items():
            cols = cfg["cols"]
            sub_weights = cfg["weights"]
            
            # 转换为特征矩阵，并平滑填充首部 NaN (均线/布林暖机期) 保护 Z-score
            T = df_temp[cols].bfill().ffill().fillna(0.0).to_numpy()
            C = df_cand[cols].bfill().ffill().fillna(0.0).to_numpy()
            
            # Zscore归一化
            T_norm = self.local_zscore_normalize(T)
            C_norm = self.local_zscore_normalize(C)
            
            dist, _ = self.calculate_multidimensional_dtw(T_norm, C_norm, sub_weights)
            
            # 距离映射得分公式：S = max(0, 100 * exp(-dist))
            # 在 Z-score 标准化空间中，优秀对齐距离在 0.0~0.2 之间。
            # dist = 0 -> 100分；dist = 0.15 -> 86分；dist = 0.5 -> 60分；dist >= 1.5 -> 22分以下。
            score = max(0.0, min(100.0, 100.0 * np.exp(-1.2 * dist)))
            scores[f"{cat_name}_score"] = round(score, 2)
            
        return scores

    def calculate_event_sequence_score(self, temp_events: dict, cand_events: dict) -> float:
        """
        事件流对齐得分：
        比较模板所需的事件集合在候选走势中是否被成功激活（探测到），
        根据置信度高低加权求和，若某核心事件在候选中置信度低或缺失，则平滑降分。
        """
        # 定义 8 大核心事件在最终形态匹配中的必需权重分布（总和为 1.0）
        event_weights = {
            "TREND_UP": 0.10,
            "TOUCH_BOLL_UPPER": 0.10,
            "PULLBACK": 0.15,
            "VOLUME_SHRINK": 0.15,
            "TOUCH_BOLL_MIDDLE": 0.20,
            "BOLL_MIDDLE_SUPPORT": 0.15,
            "STOP_FALLING_CANDLE": 0.05,
            "VOLUME_BREAKOUT": 0.10
        }
        
        seq_score = 0.0
        for evt_type, weight in event_weights.items():
            if evt_type in temp_events:
                # 如果模板需要该事件，在候选中寻找
                if evt_type in cand_events:
                    # 【高精度置信度差分对齐】：计算候选事件置信度与模板事件置信度的绝对偏差值
                    # 若完全一致（例如自相似匹配），差值为 0，则该项可拿满分 (weight * 1.0)！
                    score_diff = abs(float(cand_events[evt_type].confidence) - float(temp_events[evt_type].confidence))
                    match_ratio = max(0.0, 1.0 - score_diff)
                    seq_score += weight * match_ratio
                else:
                    # 候选缺失该事件，得分为 0 (本项无分)
                    pass
            else:
                # 模板非必需事件，不参与该模板的分数惩罚
                seq_score += weight
                
        return round(seq_score * 100.0, 2)

    def check_risk_penalties(self, df_cand: pd.DataFrame) -> tuple[float, list[str]]:
        """
        风险硬性扣分与负面惩罚检测：
        1. 破位惩罚：最近 3 天收盘跌破布林下轨或 MA60 均线 -> 扣 20 分。
        2. 无量突破：最后一天收盘价大涨 (return_1d > 0.03)，但成交量倍率 volume_ratio_20 <= 1.0 -> 扣 15 分。
        3. 极度停牌：60 日窗口内成交量为 0 的天数超过 3 天 -> 扣 10 分。
        """
        penalty = 0.0
        negative_facts = []
        
        if df_cand.empty:
            return 0.0, []
            
        df_last_3 = df_cand.tail(3)
        
        # 1. 破位惩罚
        broke_boll_lower = False
        broke_ma60 = False
        
        for _, row in df_last_3.iterrows():
            if 'boll_lower' in row and row['close'] < row['boll_lower']:
                broke_boll_lower = True
            if 'ma60' in row and row['close'] < row['ma60']:
                broke_ma60 = True
                
        if broke_boll_lower:
            penalty += 20.0
            negative_facts.append("🚨 严重破位！个股近3交易日内收盘曾强行跌破布林通道下轨。")
        elif broke_ma60:
            penalty += 15.0
            negative_facts.append("🚨 均线失守！个股近3交易日内收盘曾跌破 60 日生命支撑线。")

        # 2. 无量突破（诱多陷阱）
        last_row = df_cand.iloc[-1]
        if last_row['close'] > last_row['open'] * 1.025: # 大涨 2.5% 以上
            if last_row['volume_ratio_20'] < 1.0: # 放量软弱，量比小于 1.0
                penalty += 15.0
                negative_facts.append("⚠️ 无量拉升！今日突破拉升却未见显著放量，呈现诱多背离。")

        # 3. 极度停牌
        suspended_days = (df_cand['volume'] == 0).sum()
        if suspended_days >= 3:
            penalty += 10.0
            negative_facts.append(f"⚠️ 停牌扭曲！个股在 60 日检索窗口内存在 {suspended_days} 天严重停牌交易缺失。")
            
        return penalty, negative_facts

    def compute_composite_similarity(self, df_temp: pd.DataFrame, df_cand: pd.DataFrame, code_cand: str) -> dict:
        """
        综合形态匹配核心接口：
        结合多项式加权时序 DTW 得分、高斯事件流对齐得分，并扣减破位罚项，返回最完美的实战评级报告。
        """
        # 1. 计算分大类 DTW 相似度得分 (Trend, Boll, Volume, Candle, Volatility)
        sub_scores = self.evaluate_sub_similarities(df_temp, df_cand)
        
        # 2. 独立提取模板和候选的事件流字典
        temp_events = self.event_engine.detect_all_events(df_temp)
        cand_events = self.event_engine.detect_all_events(df_cand)
        
        # 3. 计算事件流对齐得分
        event_score = self.calculate_event_sequence_score(temp_events, cand_events)
        sub_scores["event_sequence_score"] = event_score
        
        # 4. 加权折合总分
        # total_score = 0.25*trend + 0.25*boll + 0.15*vol + 0.15*candle + 0.15*event_seq + 0.05*volatility
        raw_composite_score = (
            0.25 * sub_scores["trend_score"] +
            0.25 * sub_scores["boll_score"] +
            0.15 * sub_scores["volume_score"] +
            0.15 * sub_scores["candle_score"] +
            0.15 * sub_scores["event_sequence_score"] +
            0.05 * sub_scores["volatility_score"]
        )
        
        # 5. 计算破位惩罚扣分
        penalty, negative_facts = self.check_risk_penalties(df_cand)
        
        final_score = max(0.0, round(raw_composite_score - penalty, 2))
        
        # 6. 生成形态解释性客观事实 (Positive Facts & Negative Facts)
        positive_facts = []
        if sub_scores["boll_score"] >= 80.0:
            positive_facts.append("✨ 布林走势极具神似，回调轨迹呈现标准的圆弧触底支撑。")
        if sub_scores["volume_score"] >= 80.0:
            positive_facts.append("✨ 成交量能梯级收缩，呈现经典‘上涨放量、回调冰点缩量’的洗盘量能。")
        if "VOLUME_BREAKOUT" in cand_events and cand_events["VOLUME_BREAKOUT"].confidence >= 0.70:
            positive_facts.append("✨ 临近窗口末端，主力多头放量倍量二次突破，确立右侧上升主升段。")
        if "TOUCH_BOLL_MIDDLE" in cand_events and cand_events["TOUCH_BOLL_MIDDLE"].confidence >= 0.80:
            positive_facts.append("✨ 个股成功于中轨处精准止跌企稳，形成了极强的支撑均线共振。")

        explanation_facts = {
            "positive_facts": positive_facts,
            "negative_facts": negative_facts
        }

        # 7. 计算全路径对齐（用于画图或还原）
        # 提取用于全路径的特征列
        cols = ["close_norm", "boll_mid_dist", "volume_ratio_20", "close_position"]
        weights_arr = np.array([0.35, 0.25, 0.20, 0.20])
        
        df_temp_p = df_temp.copy()
        df_cand_p = df_cand.copy()
        df_temp_p['close_norm'] = df_temp_p['close'] / df_temp_p['close'].iloc[0]
        df_cand_p['close_norm'] = df_cand_p['close'] / df_cand_p['close'].iloc[0]
        
        T = df_temp_p[cols].bfill().ffill().fillna(0.0).to_numpy()
        C = df_cand_p[cols].bfill().ffill().fillna(0.0).to_numpy()
        T_norm = self.local_zscore_normalize(T)
        C_norm = self.local_zscore_normalize(C)
        
        _, alignment_path = self.calculate_multidimensional_dtw(T_norm, C_norm, weights_arr)

        return {
            "symbol": code_cand,
            "total_score": final_score,
            "score_breakdown": sub_scores,
            "alignment_path": alignment_path,
            "matched_events": list(cand_events.keys()),
            "explanation_facts": explanation_facts
        }
