# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 高斯模糊事件识别引擎 (Event Engine)
职责：基于高斯模糊逻辑（Gaussian Fuzzy Logic）将无量纲特征矩阵窗口，柔性映射为平滑的连续置信度交易事件。
"""

import numpy as np
import pandas as pd
from datetime import date, datetime

def sanitize_numpy(obj):
    """
    递归将字典/列表等任何嵌套结构中的 numpy 数据类型（np.int64, np.float64, np.bool_等）
    转换为 Python 标准原生基本类型（int, float, bool），消除 FastAPI / JSON 序列化冲突。
    """
    if isinstance(obj, dict):
        return {k: sanitize_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [sanitize_numpy(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return sanitize_numpy(obj.tolist())
    return obj

class PatternEvent:
    def __init__(self, event_type: str, start_date: date, end_date: date, confidence: float, evidence: dict):
        self.event_type = event_type
        self.start_date = start_date
        self.end_date = end_date
        self.confidence = float(confidence)
        # 存储特征测量值、期望中心值、偏差标准差等证据
        self.evidence = evidence

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "start_date": self.start_date.strftime("%Y-%m-%d") if isinstance(self.start_date, (date, datetime)) else str(self.start_date),
            "end_date": self.end_date.strftime("%Y-%m-%d") if isinstance(self.end_date, (date, datetime)) else str(self.end_date),
            "confidence": round(self.confidence, 4),
            "evidence": sanitize_numpy(self.evidence)
        }

    def __repr__(self):
        return f"<PatternEvent {self.event_type} | Conf: {self.confidence:.2f} | {self.start_date} -> {self.end_date}>"

def calculate_gaussian_confidence(x: float, mu: float, sigma: float) -> float:
    """
    单峰高斯模糊正态置信度计算公式：
    C = exp( - (x - mu)^2 / (2 * sigma^2) )
    当测量测度 x 完美符合最优期望中心值 mu 时，置信度输出 1.0；偏离时平滑衰减，无一刀切跳跃。
    """
    if pd.isna(x):
        return 0.0
    return float(np.exp(-((x - mu) ** 2) / (2.0 * (sigma ** 2))))


def _safe_bool(series: pd.Series) -> pd.Series:
    """将布尔特征列安全转为 bool，NaN（暖机期）视为 False。"""
    return series.fillna(False).astype(bool)

class EventDetector:
    """
    所有特定事件识别器的标准基类
    """
    event_type: str = "BASE_EVENT"
    version: str = "event_v1.1.1"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        """
        子类重写：输入滑动历史特征窗口 (DataFrame)，返回识别出的高斯置信度事件列表。
        """
        raise NotImplementedError


# =============================================================================
# 1. TREND_UP (趋势上涨事件检测器)
# =============================================================================
class TrendUpDetector(EventDetector):
    event_type = "TREND_UP"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 窗口内 MA5 > MA10 且 MA10 > MA20 的多头排列天数占总窗口大小的比例
        total_days = len(df_window)
        if total_days < 10:
            return []

        # 检查多头排布，排除 ma20 处于暖机期时的 NaN 稀释干扰
        valid_mask = df_window['ma20'].notna()
        total_valid = valid_mask.sum()
        if total_valid < 5:
            return []

        ma5_above = _safe_bool(df_window['ma5_above_ma10'])
        ma10_above = _safe_bool(df_window['ma10_above_ma20'])
        aligned_days = ((ma5_above) & (ma10_above) & valid_mask).sum()
        x = aligned_days / total_valid
        
        # 参数：最优目标 mu = 1.0 (全窗口都是多头)，容差系数 sigma = 0.25
        mu, sigma = 1.0, 0.25
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        # 构造事件
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=df_window['date'].iloc[0],
            end_date=df_window['date'].iloc[-1],
            confidence=confidence,
            evidence={
                "aligned_ratio": round(x, 4),
                "aligned_days": aligned_days,
                "total_days": total_days,
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 2. TOUCH_BOLL_UPPER (触碰或贴近布林上轨检测器)
# =============================================================================
class TouchBollUpperDetector(EventDetector):
    event_type = "TOUCH_BOLL_UPPER"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 寻找窗口内 close 距离上轨最近的那一天，x = (close - boll_upper) / close
        # 如果贴合或者向上突破，x >= 0，我们定义最优目标 mu = 0.0，容差标准差 sigma = 0.015
        if df_window.empty or 'boll_upper_dist' not in df_window.columns:
            return []
            
        # 距离上轨的绝对偏离
        abs_dist = df_window['boll_upper_dist'].abs()
        min_idx = abs_dist.idxmin()
        if pd.isna(min_idx):
            return []

        best_row = df_window.loc[min_idx]
        x = float(best_row['boll_upper_dist'])
        
        mu, sigma = 0.0, 0.015
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=best_row['date'],
            end_date=best_row['date'],
            confidence=confidence,
            evidence={
                "min_boll_upper_distance": round(x, 4),
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 3. PULLBACK (上涨后回调事件检测器)
# =============================================================================
class PullbackDetector(EventDetector):
    event_type = "PULLBACK"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 寻找窗口内的最高收盘价日，计算最高价日之后到窗口期末的最大回调幅度
        if len(df_window) < 5:
            return []
            
        # 为了防止窗口末端的放量突破价格（创出新高）将前期的回调轨迹遮蔽，
        # 我们寻找窗口前半部分（前 80% 区域）的最高收盘价作为主趋势高点
        search_limit = int(len(df_window) * 0.8)
        df_search = df_window.iloc[:search_limit]
        if df_search.empty:
            return []
            
        max_idx = df_search['close'].idxmax()
        if pd.isna(max_idx) or max_idx == df_window.index[-1]:
            # 若最高点就在最后一天，表明一直在上涨，根本没有回调，置信度为 0
            return []
            
        high_price = float(df_window.loc[max_idx, 'close'])
        # 截取最高点之后的子序列
        df_after = df_window.loc[max_idx:]
        low_price = float(df_after['close'].min())
        
        # 测度 x = (最高收盘价 - 后续最低收盘价) / 最高收盘价
        x = (high_price - low_price) / high_price
        
        # 参数：最优目标 mu = 0.10 (回调 10% 左右最温和最优)，容差标准差 sigma = 0.05
        mu, sigma = 0.10, 0.05
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=df_window.loc[max_idx, 'date'],
            end_date=df_after['date'].iloc[df_after['close'].argmin()],
            confidence=confidence,
            evidence={
                "max_pullback_ratio": round(x, 4),
                "high_price": high_price,
                "low_price": low_price,
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 4. VOLUME_SHRINK (回调期间地量缩量检测器)
# =============================================================================
class VolumeShrinkDetector(EventDetector):
    event_type = "VOLUME_SHRINK"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 寻找最高点之后的平均 volume_ratio_20，看是否处于缩量态势
        if len(df_window) < 5:
            return []
            
        # 同样寻找窗口前半部分（前 80% 区域）的最高收盘价作为主趋势高点，定位回调期起点
        search_limit = int(len(df_window) * 0.8)
        df_search = df_window.iloc[:search_limit]
        if df_search.empty:
            return []
            
        max_idx = df_search['close'].idxmax()
        if pd.isna(max_idx) or max_idx == df_window.index[-1]:
            return []
            
        # 寻找回踩布林中轨的落底点 (touch_idx)，以此作为回调期的终点
        # 从而将缩量测算区间精准框定在 [趋势高点 -> 回踩落底] 这段“纯回调段”，彻底隔离后期突破巨量的干扰！
        if 'boll_mid' in df_window.columns:
            mid_dists = (df_window['low'] - df_window['boll_mid']) / df_window['boll_mid']
            touch_idx = mid_dists.abs().idxmin()
        else:
            touch_idx = df_window.index[-1]
            
        # 提取真正的纯回调落底区间
        if touch_idx > max_idx:
            df_pullback = df_window.loc[max_idx:touch_idx]
        else:
            df_pullback = df_window.loc[max_idx:]
            
        # 计算该纯回调期的平均量能比率
        x = float(df_pullback['volume_ratio_20'].mean())
        
        # 参数：最优目标 mu = 0.50 (量能缩至 20 日均量的一半左右为极佳地量)，容差标准差 sigma = 0.25
        mu, sigma = 0.50, 0.25
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=df_pullback['date'].iloc[0],
            end_date=df_pullback['date'].iloc[-1],
            confidence=confidence,
            evidence={
                "average_volume_ratio": round(x, 4),
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 5. TOUCH_BOLL_MIDDLE (回踩布林中轨检测器)
# =============================================================================
class TouchBollMiddleDetector(EventDetector):
    event_type = "TOUCH_BOLL_MIDDLE"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 寻找回调期内，单日最低价距离布林中轨最近的那一天
        # x = (low - boll_mid) / boll_mid
        if len(df_window) < 5:
            return []
            
        # 同样寻找窗口前半部分（前 80% 区域）的最高收盘价作为主趋势高点，定位回调回踩搜索起点
        search_limit = int(len(df_window) * 0.8)
        df_search = df_window.iloc[:search_limit]
        if df_search.empty:
            df_after = df_window
        else:
            max_idx = df_search['close'].idxmax()
            if pd.isna(max_idx):
                df_after = df_window
            else:
                df_after = df_window.loc[max_idx:]
            
        if df_after.empty or 'boll_mid' not in df_after.columns:
            return []

        # 计算每日最低价到中轨的比值距离
        mid_dists = (df_after['low'] - df_after['boll_mid']) / df_after['boll_mid']
        abs_mid_dists = mid_dists.abs()
        min_idx = abs_mid_dists.idxmin()
        if pd.isna(min_idx):
            return []
            
        best_row = df_after.loc[min_idx]
        x = float(mid_dists.loc[min_idx])
        
        # 参数：最优目标 mu = 0.0 (精准重踩中轨)，容差标准差 sigma = 0.015 (允许 1.5% 的摆动偏离)
        mu, sigma = 0.0, 0.015
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=best_row['date'],
            end_date=best_row['date'],
            confidence=confidence,
            evidence={
                "min_boll_middle_distance": round(x, 4),
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 6. BOLL_MIDDLE_SUPPORT (中轨支撑力防跌破检测器)
# =============================================================================
class BollMiddleSupportDetector(EventDetector):
    event_type = "BOLL_MIDDLE_SUPPORT"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 在回踩布林中轨那一天（以及之后），收盘价跌破中轨的天数占比
        # 转换为：没有跌破中轨的天数比例。x = 收盘价 >= boll_mid 的天数 / 总天数
        if len(df_window) < 5:
            return []
            
        # 寻找回踩点。为了方便，我们直接寻找全窗口内 low 距离中轨最近的那一天
        mid_dists = (df_window['low'] - df_window['boll_mid']) / df_window['boll_mid']
        min_idx = mid_dists.abs().idxmin()
        if pd.isna(min_idx):
            return []
            
        df_after_touch = df_window.loc[min_idx:]
        total_days = len(df_after_touch)
        
        above_days = (df_after_touch['close'] >= df_after_touch['boll_mid']).sum()
        x = above_days / total_days if total_days > 0 else 0.0
        
        # 参数：最优目标 mu = 1.0 (守住中轨没有任何一天跌破)，容差标准差 sigma = 0.15
        mu, sigma = 1.0, 0.15
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=df_window.loc[min_idx, 'date'],
            end_date=df_window['date'].iloc[-1],
            confidence=confidence,
            evidence={
                "support_days_ratio": round(x, 4),
                "support_days": above_days,
                "total_days": total_days,
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 7. STOP_FALLING_CANDLE (止跌锤头探底 K 线检测器)
# =============================================================================
class StopFallingCandleDetector(EventDetector):
    event_type = "STOP_FALLING_CANDLE"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 在回踩中轨点附近的 3 天内，寻找下影线长、收盘处于振幅高位（止跌信号）的最佳K线
        # x = (close - low) / (high - low)，即 close_position
        if len(df_window) < 5:
            return []
            
        # 寻找最低价距离中轨最近的那一天
        mid_dists = (df_window['low'] - df_window['boll_mid']) / df_window['boll_mid']
        min_idx = mid_dists.abs().idxmin()
        if pd.isna(min_idx):
            return []
            
        # 截取该天前 1 天和后 1 天的 3 天局部窗
        start_idx = max(df_window.index[0], min_idx - 1)
        end_idx = min(df_window.index[-1], min_idx + 1)
        df_local = df_window.loc[start_idx:end_idx]
        
        # 寻找最优的 close_position
        best_idx = df_local['close_position'].idxmax()
        best_row = df_local.loc[best_idx]
        x = float(best_row['close_position'])
        
        # 参数：最优目标 mu = 0.80 (上四分之一处收盘，典型锤子探底线)，容差标准差 sigma = 0.15
        mu, sigma = 0.80, 0.15
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=best_row['date'],
            end_date=best_row['date'],
            confidence=confidence,
            evidence={
                "best_candle_position": round(x, 4),
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 8. VOLUME_BREAKOUT (成交量放量突破检测器)
# =============================================================================
class VolumeBreakoutDetector(EventDetector):
    event_type = "VOLUME_BREAKOUT"

    def detect(self, df_window: pd.DataFrame) -> list[PatternEvent]:
        # 测度 x: 寻找窗口最后 3 天内成交量最大的一天，看是否形成“倍量”突破
        # x = volume / volume_ma20
        if len(df_window) < 3:
            return []
            
        df_last_3 = df_window.tail(3)
        best_idx = df_last_3['volume_ratio_20'].idxmax()
        best_row = df_last_3.loc[best_idx]
        
        x = float(best_row['volume_ratio_20'])
        
        # 参数：最优目标 mu = 2.0 (倍量二次突破最健康)，容差标准差 sigma = 0.50
        mu, sigma = 2.0, 0.50
        confidence = calculate_gaussian_confidence(x, mu, sigma)
        
        evt = PatternEvent(
            event_type=self.event_type,
            start_date=best_row['date'],
            end_date=best_row['date'],
            confidence=confidence,
            evidence={
                "max_breakout_volume_ratio": round(x, 4),
                "target_mu": mu,
                "tolerance_sigma": sigma
            }
        )
        return [evt]


# =============================================================================
# 9. 核心协调匹配引擎 (EventEngine)
# =============================================================================
class EventEngine:
    def __init__(self):
        # 自动装载注册所有的 MVP 核心事件探测器
        self.detectors = [
            TrendUpDetector(),
            TouchBollUpperDetector(),
            PullbackDetector(),
            VolumeShrinkDetector(),
            TouchBollMiddleDetector(),
            BollMiddleSupportDetector(),
            StopFallingCandleDetector(),
            VolumeBreakoutDetector()
        ]

    def detect_all_events(self, df_window: pd.DataFrame) -> dict:
        """
        对输入的特征矩阵窗口片段执行全套事件识别，并返回事件类型到事件对象的字典映射
        """
        results = {}
        for detector in self.detectors:
            events = detector.detect(df_window)
            if events:
                # 仅保留置信度最高的那一个事件结果
                best_event = max(events, key=lambda e: e.confidence)
                results[detector.event_type] = best_event
        return results
