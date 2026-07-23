# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 多维加权时序对齐相似度搜索算法引擎 (Similarity Engine)
职责：实现局部 Z-score 标准化、限宽 Sakoe-Chiba 约束带多维加权 DTW 对齐、分项得分核算、事件对齐校验及破位扣分罚项。
支持从模板配置中动态读取特征权重和必需事件序列，替代硬编码评分公式。
"""

import numpy as np
import pandas as pd
from datetime import date, datetime
import logging

from backend.app.event_engine.engine import EventEngine

logger = logging.getLogger("SimilarityEngine")

# numba 可选加速：可用则 JIT 编译 DTW 累积代价矩阵计算（提速数倍），不可用时回退纯 Python 实现
try:
    from numba import njit
    _NUMBA_AVAILABLE = True
except Exception:  # numba 未安装或与 numpy 版本不兼容时优雅降级
    njit = None
    _NUMBA_AVAILABLE = False


def _dtw_accum_cost(T: np.ndarray, C: np.ndarray, weights: np.ndarray, sakoe_width: int) -> np.ndarray:
    """
    计算带 Sakoe-Chiba 限宽约束的多维加权 DTW 累积代价矩阵。
    将局部距离 D(i,j)=sqrt(sum(w*(T_i-C_j)^2)) 折叠进动态规划递推，避免单独物化距离矩阵。
    逻辑与原纯 Python 实现等价，仅做 JIT 编译加速。返回 (M x N) 累积代价矩阵（带外为 inf）。
    """
    if _NUMBA_AVAILABLE:
        return _dtw_accum_cost_jit(T, C, weights, sakoe_width)
    return _dtw_accum_cost_py(T, C, weights, sakoe_width)


if _NUMBA_AVAILABLE:
    @njit(cache=True)
    def _dtw_accum_cost_jit(T, C, weights, sakoe_width):
        M = T.shape[0]
        N = C.shape[0]
        accum_cost = np.full((M, N), np.inf)
        accum_cost[0, 0] = np.sqrt(np.sum(weights * (T[0] - C[0]) ** 2))
        for i in range(M):
            j_start = i - sakoe_width
            if j_start < 0:
                j_start = 0
            j_end = i + sakoe_width + 1
            if j_end > N:
                j_end = N
            for j in range(j_start, j_end):
                if i == 0 and j == 0:
                    continue
                best = np.inf
                if i > 0:
                    if (i - 1) - j <= sakoe_width and j - (i - 1) <= sakoe_width:
                        v = accum_cost[i - 1, j]
                        if v < best:
                            best = v
                if j > 0:
                    if i - (j - 1) <= sakoe_width and (j - 1) - i <= sakoe_width:
                        v = accum_cost[i, j - 1]
                        if v < best:
                            best = v
                if i > 0 and j > 0:
                    if (i - 1) - (j - 1) <= sakoe_width and (j - 1) - (i - 1) <= sakoe_width:
                        v = accum_cost[i - 1, j - 1]
                        if v < best:
                            best = v
                ld = np.sqrt(np.sum(weights * (T[i] - C[j]) ** 2))
                accum_cost[i, j] = ld + best
        return accum_cost


def _dtw_accum_cost_py(T, C, weights, sakoe_width):
    """纯 Python 回退实现（numba 不可用时使用），逻辑与 JIT 版完全一致。"""
    M = T.shape[0]
    N = C.shape[0]
    accum_cost = np.full((M, N), np.inf)
    accum_cost[0, 0] = np.sqrt(np.sum(weights * (T[0] - C[0]) ** 2))
    for i in range(M):
        j_start = max(0, i - sakoe_width)
        j_end = min(N, i + sakoe_width + 1)
        for j in range(j_start, j_end):
            if i == 0 and j == 0:
                continue
            best = np.inf
            if i > 0 and abs((i - 1) - j) <= sakoe_width:
                v = accum_cost[i - 1, j]
                if v < best:
                    best = v
            if j > 0 and abs(i - (j - 1)) <= sakoe_width:
                v = accum_cost[i, j - 1]
                if v < best:
                    best = v
            if i > 0 and j > 0 and abs((i - 1) - (j - 1)) <= sakoe_width:
                v = accum_cost[i - 1, j - 1]
                if v < best:
                    best = v
            ld = np.sqrt(np.sum(weights * (T[i] - C[j]) ** 2))
            accum_cost[i, j] = ld + best
    return accum_cost

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

    # -------------------------------------------------------------------------
    # 静态元数据：特征列到指标大类的映射、事件默认权重
    # -------------------------------------------------------------------------

    CATEGORY_FEATURE_MAP = {
        "trend": ["close_norm", "return_5d"],
        "boll": ["boll_mid_dist"],
        "volume": ["volume_ratio_20"],
        "candle": ["close_position"],
        "volatility": ["range_ratio", "atr_ratio"],
    }

    DEFAULT_EVENT_WEIGHTS = {
        "TREND_UP": 0.10,
        "TOUCH_BOLL_UPPER": 0.10,
        "PULLBACK": 0.15,
        "VOLUME_SHRINK": 0.15,
        "TOUCH_BOLL_MIDDLE": 0.20,
        "BOLL_MIDDLE_SUPPORT": 0.15,
        "STOP_FALLING_CANDLE": 0.05,
        "VOLUME_BREAKOUT": 0.10,
    }

    # -------------------------------------------------------------------------
    # 权重派生工具方法
    # -------------------------------------------------------------------------

    def _derive_category_weights(self, feature_weights: dict) -> dict:
        """
        从模板的 7 大特征权重动态派生 5 个指标大类的类别总权重。
        每个类别的权重 = 该类别下所有特征权重之和，再归一化为 L1。
        """
        cat_weights = {}
        for cat_name, feat_keys in self.CATEGORY_FEATURE_MAP.items():
            raw = [float(feature_weights.get(k, 0.0)) for k in feat_keys]
            total = sum(raw)
            if total > 0:
                cat_weights[cat_name] = total
            else:
                cat_weights[cat_name] = 1.0 / len(feat_keys)
        cat_total = sum(cat_weights.values())
        if cat_total > 0:
            cat_weights = {k: v / cat_total for k, v in cat_weights.items()}
        return cat_weights

    def _derive_sub_weights(self, feature_weights: dict) -> dict:
        """
        在类别内部，按模板特征权重比例分配子权重。
        返回格式：{cat_name: {"cols": [...], "weights": np.array([...])}}
        """
        categories = {}
        for cat_name, feat_keys in self.CATEGORY_FEATURE_MAP.items():
            raw = [float(feature_weights.get(k, 0.0)) for k in feat_keys]
            total = sum(raw)
            if total > 0:
                sub_w = [v / total for v in raw]
            else:
                sub_w = [1.0 / len(raw)] * len(raw)
            categories[cat_name] = {
                "cols": feat_keys,
                "weights": np.array(sub_w),
            }
        return categories

    # -------------------------------------------------------------------------
    # 基础计算原语
    # -------------------------------------------------------------------------

    def _prepare_feature_matrix(self, df: pd.DataFrame, cols: list[str]) -> np.ndarray:
        """
        准备 DTW 输入矩阵，正确处理暖机期 NaN：
        - 先 bfill+ffill 利用相邻有效值传播
        - 再用各列均值填充仍为 NaN 的位置（避免 warmup NaN 被误当作 0.0）
        """
        mat = df[cols].bfill().ffill()
        for col in cols:
            mat[col] = mat[col].fillna(mat[col].mean())
        return mat.fillna(0.0).to_numpy()

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

    def calculate_multidimensional_dtw(
        self, T: np.ndarray, C: np.ndarray, weights: np.ndarray
    ) -> tuple[float, list[list[int]]]:
        """
        多维加权 DTW 核心对齐算法：
        - 输入：标准化后的模板特征 T (M x F) 与候选特征 C (N x F)
        - 约束：限宽 Sakoe-Chiba 约束带（防止时间轴病态对齐并压缩运算耗时 80%）
        - 权重：weights (F) 为各维度的重要性系数
        - 返回：(对齐累计最短距离, 最佳对齐路径)
        """
        M, N = len(T), len(C)

        # 0. 统一为连续 float64 数组，便于 JIT 编译与稳定数值计算
        T = np.ascontiguousarray(T, dtype=np.float64)
        C = np.ascontiguousarray(C, dtype=np.float64)
        weights = np.ascontiguousarray(weights, dtype=np.float64)

        # 1. Sakoe-Chiba 约束带宽度：floor(0.15 * max(M, N))
        sakoe_width = max(3, int(np.floor(0.15 * max(M, N))))

        # 2. 动态规划递推计算累积最小距离（局部距离折叠进递推，numba 加速）
        accum_cost = _dtw_accum_cost(T, C, weights, sakoe_width)

        if np.isinf(accum_cost[M - 1, N - 1]):
            return 999.0, []

        # 3. 回溯最优对齐路径
        path = []
        i, j = M - 1, N - 1
        path.append([i, j])
        while i > 0 or j > 0:
            if i == 0:
                j -= 1
            elif j == 0:
                i -= 1
            else:
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
        normalized_distance = accum_cost[M - 1, N - 1] / (M + N)
        return float(normalized_distance), path

    # -------------------------------------------------------------------------
    # 分项 DTW 评分（权重由模板动态驱动）
    # -------------------------------------------------------------------------

    def evaluate_sub_similarities(
        self,
        df_temp: pd.DataFrame,
        df_cand: pd.DataFrame,
        feature_weights: dict | None = None,
    ) -> dict:
        """
        对特征向量矩阵进行分拆（Trend, Boll, Volume, Candle, Volatility）
        独立运行 Z-score 标准化与加权 DTW，提取出更细、可解释的多轴分项评分。
        权重由模板 feature_weights 动态派生。
        """
        if feature_weights is None:
            feature_weights = self.feature_weights

        categories = self._derive_sub_weights(feature_weights)

        scores = {}

        # 补充临时衍生归一化 close 列（让时间轴首日归一为 1.0）
        df_temp = df_temp.copy()
        df_cand = df_cand.copy()
        df_temp["close_norm"] = df_temp["close"] / df_temp["close"].iloc[0]
        df_cand["close_norm"] = df_cand["close"] / df_cand["close"].iloc[0]

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

    # -------------------------------------------------------------------------
    # 事件流对齐评分（required_events 由模板动态驱动）
    # -------------------------------------------------------------------------

    def calculate_event_sequence_score(
        self,
        temp_events: dict,
        cand_events: dict,
        required_events: list[str] | None = None,
    ) -> float:
        """
        事件流对齐得分：
        比较模板所需的事件集合在候选走势中是否被成功激活（探测到），
        根据置信度高低加权求和。
        - required_events=None 时回退到全部 8 大事件的默认权重
        - required_events 非空时仅对声明的事件打分，等比归一化
        """
        if required_events is None:
            event_weights = dict(self.DEFAULT_EVENT_WEIGHTS)
        else:
            event_weights = {
                k: v for k, v in self.DEFAULT_EVENT_WEIGHTS.items()
                if k in required_events
            }
            total = sum(event_weights.values())
            if total > 0:
                event_weights = {k: v / total for k, v in event_weights.items()}

        seq_score = 0.0
        for evt_type, weight in event_weights.items():
            if evt_type not in temp_events:
                continue
            if evt_type not in cand_events:
                continue
            score_diff = abs(
                float(cand_events[evt_type].confidence)
                - float(temp_events[evt_type].confidence)
            )
            match_ratio = max(0.0, 1.0 - score_diff)
            seq_score += weight * match_ratio

        return round(seq_score * 100.0, 2)

    # -------------------------------------------------------------------------
    # 风险惩罚
    # -------------------------------------------------------------------------

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
            if "boll_lower" in row and row["close"] < row["boll_lower"]:
                broke_boll_lower = True
            if "ma60" in row and row["close"] < row["ma60"]:
                broke_ma60 = True
                
        if broke_boll_lower:
            penalty += 20.0
            negative_facts.append("🚨 严重破位！个股近3交易日内收盘曾强行跌破布林通道下轨。")
        elif broke_ma60:
            penalty += 15.0
            negative_facts.append("🚨 均线失守！个股近3交易日内收盘曾跌破 60 日生命支撑线。")

        # 2. 无量突破（诱多陷阱）
        last_row = df_cand.iloc[-1]
        if last_row["close"] > last_row["open"] * 1.025: # 大涨 2.5% 以上
            if last_row["volume_ratio_20"] < 1.0: # 放量软弱，量比小于 1.0
                penalty += 15.0
                negative_facts.append("⚠️ 无量拉升！今日突破拉升却未见显著放量，呈现诱多背离。")

        # 3. 极度停牌
        suspended_days = (df_cand['volume'] == 0).sum()
        if suspended_days >= 3:
            penalty += 10.0
            negative_facts.append(
                f"⚠️ 停牌扭曲！个股在 60 日检索窗口内存在 {suspended_days} 天严重停牌交易缺失。"
            )

        return penalty, negative_facts

    # -------------------------------------------------------------------------
    # 综合评分入口
    # -------------------------------------------------------------------------

    def compute_composite_similarity(
        self,
        df_temp: pd.DataFrame,
        df_cand: pd.DataFrame,
        code_cand: str,
        feature_weights: dict | None = None,
        required_events: list[str] | None = None,
    ) -> dict:
        """
        综合形态匹配核心接口：
        结合多项式加权时序 DTW 得分、高斯事件流对齐得分，并扣减破位罚项。

        - feature_weights: 模板的 7 大特征权重（来自 feature_templates.weights），
          None 时使用内置默认值，向后兼容旧调用方。
        - required_events: 模板声明的必需事件序列（来自 config.required_events），
          None 时回退到全部 8 大事件。
        """
        if feature_weights is None:
            feature_weights = self.feature_weights

        # 1. 分大类 DTW 相似度得分（权重由模板驱动）
        sub_scores = self.evaluate_sub_similarities(df_temp, df_cand, feature_weights)

        # 2. 提取模板和候选的事件流字典
        temp_events = self.event_engine.detect_all_events(df_temp)
        cand_events = self.event_engine.detect_all_events(df_cand)

        # 3. 事件流对齐得分（required_events 由模板驱动）
        event_score = self.calculate_event_sequence_score(
            temp_events, cand_events, required_events
        )
        sub_scores["event_sequence_score"] = event_score

        # 4. 按模板权重动态派生类别权重，加权折合总分
        #    event_sequence 固定占 15%，其余 85% 按特征权重比例分配到 5 个类别
        cat_weights = self._derive_category_weights(feature_weights)
        raw_composite_score = (
            cat_weights["trend"] * 0.85 * sub_scores["trend_score"]
            + cat_weights["boll"] * 0.85 * sub_scores["boll_score"]
            + cat_weights["volume"] * 0.85 * sub_scores["volume_score"]
            + cat_weights["candle"] * 0.85 * sub_scores["candle_score"]
            + 0.15 * sub_scores["event_sequence_score"]
            + cat_weights["volatility"] * 0.85 * sub_scores["volatility_score"]
        )
        
        # 5. 计算破位惩罚扣分
        penalty, negative_facts = self.check_risk_penalties(df_cand)
        
        final_score = max(0.0, round(raw_composite_score - penalty, 2))

        # 6. 生成形态解释性客观事实
        positive_facts = []
        if sub_scores["boll_score"] >= 80.0:
            positive_facts.append(
                "✨ 布林走势极具神似，回调轨迹呈现标准的圆弧触底支撑。"
            )
        if sub_scores["volume_score"] >= 80.0:
            positive_facts.append(
                "✨ 成交量能梯级收缩，呈现经典'上涨放量、回调冰点缩量'的洗盘量能。"
            )
        if (
            "VOLUME_BREAKOUT" in cand_events
            and cand_events["VOLUME_BREAKOUT"].confidence >= 0.70
        ):
            positive_facts.append(
                "✨ 临近窗口末端，主力多头放量倍量二次突破，确立右侧上升主升段。"
            )
        if (
            "TOUCH_BOLL_MIDDLE" in cand_events
            and cand_events["TOUCH_BOLL_MIDDLE"].confidence >= 0.80
        ):
            positive_facts.append(
                "✨ 个股成功于中轨处精准止跌企稳，形成了极强的支撑均线共振。"
            )

        explanation_facts = {
            "positive_facts": positive_facts,
            "negative_facts": negative_facts,
        }

        # 7. 全路径对齐（用于画图/还原），权重由模板 feature_weights 派生（与评分基准一致）
        cols = ["close_norm", "boll_mid_dist", "volume_ratio_20", "close_position"]
        raw_align_w = [float(feature_weights.get(c, 0.0)) for c in cols]
        w_sum_align = sum(raw_align_w)
        if w_sum_align > 0:
            weights_arr = np.array([w / w_sum_align for w in raw_align_w])
        else:
            weights_arr = np.array([0.25, 0.25, 0.25, 0.25])  # 均匀回退

        df_temp_p = df_temp.copy()
        df_cand_p = df_cand.copy()
        df_temp_p["close_norm"] = df_temp_p["close"] / df_temp_p["close"].iloc[0]
        df_cand_p["close_norm"] = df_cand_p["close"] / df_cand_p["close"].iloc[0]

        T = self._prepare_feature_matrix(df_temp_p, cols)
        C = self._prepare_feature_matrix(df_cand_p, cols)
        T_norm = self.local_zscore_normalize(T)
        C_norm = self.local_zscore_normalize(C)

        _, alignment_path = self.calculate_multidimensional_dtw(T_norm, C_norm, weights_arr)

        return {
            "symbol": code_cand,
            "total_score": final_score,
            "score_breakdown": sub_scores,
            "alignment_path": alignment_path,
            "matched_events": list(cand_events.keys()),
            "explanation_facts": explanation_facts,
        }
