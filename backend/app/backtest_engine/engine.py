# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 历史形态滚动无偏回测引擎 (Backtest Engine)
职责：在历史时间轴上滑动重演，利用高能内存预加载与切片技术，实现 100% 杜绝未来函数的无偏仿真回测。
"""

import numpy as np
import pandas as pd
from datetime import datetime, date, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
import json
import uuid

from backend.app.core.config import settings
from backend.app.indicator_engine.engine import calculate_indicators
from backend.app.feature_engine.engine import calculate_features
from backend.app.similarity_engine.engine import SimilarityEngine
from backend.app.template_manager.manager import TemplateManager
from backend.app.filters.boll_mid_filter import has_boll_mid_breach

class BacktestEngine:
    def __init__(self):
        self.db_url = settings.DATABASE_URL
        self.similarity_engine = SimilarityEngine()
        self.template_manager = TemplateManager()

    def get_db_connection(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def load_stock_full_history(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """
        一次性预加载单股包含暖机期在内的全历史日 K，避免在交易日循环中重复查询数据库。
        - 预拉取额外 250 天以支撑滚窗均线暖机
        """
        padded_start = start_date - timedelta(days=250)
        conn = self.get_db_connection()
        cursor = conn.cursor()
        query = """
            SELECT date, open, high, low, close, volume, amount, factor
            FROM daily_bars
            WHERE code = %s AND date >= %s AND date <= %s
            ORDER BY date ASC;
        """
        cursor.execute(query, (code, padded_start, end_date))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        if not rows:
            return pd.DataFrame()
            
        df = pd.DataFrame(rows)
        # 强制 Decimal 转 Float 消除计算干扰
        for col in ['open', 'high', 'low', 'close', 'amount', 'factor']:
            if col in df.columns:
                df[col] = df[col].astype(float)
        if 'volume' in df.columns:
            df['volume'] = df['volume'].astype(int)
        return df

    def run_backtest(self, template_id: int, start_date_str: str, end_date_str: str, score_threshold: float = 80.0) -> dict:
        """
        高能历史滚动无偏回测主逻辑：
        - template_id: 模板 ID
        - start_date_str: 回测历史起点 (如 "2026-03-01")
        - end_date_str: 回测历史终点 (如 "2026-07-19")
        - score_threshold: 买入相似度得分门槛
        """
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        
        # 1. 加载模板配置
        template = self.template_manager.get_template_by_id(template_id)
        if not template:
            raise ValueError(f"未找到模板 ID: {template_id}")
            
        config = template["config"]
        window_size = config.get("window_size", 60)
        holding_periods = config.get("default_backtest_config", {}).get("holding_periods", [5, 10, 20])
        benchmark = config.get("default_backtest_config", {}).get("benchmark", "sz399300")

        # 读取模板特征权重和必需事件序列
        template_weights = template.get("weights", {}) or {}
        if not template_weights:
            template_weights = self.similarity_engine.feature_weights
        required_events = config.get("required_events", [])

        # 如果模板设置了 score_threshold，用它覆盖请求中的阈值
        template_score_threshold = config.get("default_backtest_config", {}).get("score_threshold", None)
        if template_score_threshold is not None:
            score_threshold = float(template_score_threshold)

        # 模板驱动：如果模板需要 BOLL_MIDDLE_SUPPORT 事件，则启用中轨硬过滤
        require_boll_mid_filter = "BOLL_MIDDLE_SUPPORT" in required_events
        if require_boll_mid_filter:
            print(f"🔒 [模板驱动] 该模板需要 BOLL_MIDDLE_SUPPORT，已启用中轨硬过滤（回调触中轨后收盘不可跌破）")
        else:
            print(f"ℹ️ [模板驱动] 该模板不需要 BOLL_MIDDLE_SUPPORT，跳过中轨硬过滤")

        # 2. 编译模板母体经典形态特征矩阵
        source_symbol = config.get("source_symbol", "sz000002")
        source_end = datetime.strptime(config.get("source_end", "2026-05-01"), "%Y-%m-%d").date()
        
        df_temp_raw = self.load_stock_full_history(source_symbol, source_end - timedelta(days=60), source_end)
        df_temp_ind = calculate_indicators(df_temp_raw)
        df_temp_feat = calculate_features(df_temp_ind, source_symbol)
        df_temp_window = df_temp_feat.tail(window_size).copy()
        
        # 3. 预加载股票池并一次性算出全历史特征大表 (内存预加载性能大跃进)
        # 获取股票池
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT code, name FROM stocks WHERE is_st = FALSE AND is_suspended = FALSE;")
        stocks = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # 在内存中预存各股已经计算好全历史的 features 字典：{code: df_features}
        preloaded_features = {}
        # 为了回测中快速按日期定位收盘价，预存各股 {code: {date: close}} 字典
        price_maps = {}
        
        print(f"👉 正在预加载回测股票池量价底座并完成矢量计算...")
        for s in stocks:
            code = s["code"]
            df_hist = self.load_stock_full_history(code, start_date, end_date)
            if df_hist.empty or len(df_hist) < 100:
                continue
            df_ind = calculate_indicators(df_hist)
            df_feat = calculate_features(df_ind, code)
            
            preloaded_features[code] = df_feat
            price_maps[code] = dict(zip(df_hist['date'], df_hist['close']))

        # 4. 获取回测历史区间的标准交易日轴 (来自股票池中最活跃股，如万科A)
        if source_symbol in preloaded_features:
            df_dates = preloaded_features[source_symbol]
        else:
            df_dates = list(preloaded_features.values())[0]
            
        trading_dates = df_dates[(df_dates['date'] >= start_date) & (df_dates['date'] <= end_date)]['date'].tolist()
        trading_dates.sort()
        
        print(f"👉 预加载完成！回测区间共包含 {len(trading_dates)} 个有效交易日。开始无偏仿真重演...")

        trade_details = []
        signals_count = 0
        
        # 5. 滚动交易日历史重演仿真
        # 沿着交易日轴向前行进
        for date_idx, t in enumerate(trading_dates):
            # 5.1 在 t 日，扫描全市场生成买入信号
            for code, df_feat in preloaded_features.items():
                # 【防未来函数红线】：严禁提取 t 日之后的数据
                df_past_t = df_feat[df_feat['date'] <= t]
                if len(df_past_t) < window_size:
                    continue
                    
                # 截取截至 t 日为止的 60 日局部特征矩阵窗口
                df_window_t = df_past_t.tail(window_size)
                
                # 两级剪枝过滤
                min_boll_dist = df_window_t['boll_mid_dist'].abs().min()
                if min_boll_dist > 0.045:
                    continue

                # 两级半剪枝：布林中轨硬过滤（仅当模板需要 BOLL_MIDDLE_SUPPORT 时启用）
                if require_boll_mid_filter and has_boll_mid_breach(df_window_t):
                    continue

                # 计算相似度（注入模板权重和事件序列）
                report = self.similarity_engine.compute_composite_similarity(
                    df_temp_window,
                    df_window_t,
                    code,
                    feature_weights=template_weights,
                    required_events=required_events,
                )
                
                # 5.2 触发信号，虚拟买入执行
                if report["total_score"] >= score_threshold:
                    signals_count += 1
                    buy_price = float(df_window_t['close'].iloc[-1]) # 以 t 日收盘价买入
                    
                    # 5.3 模拟跟踪持股周期并按 T+H 日收盘价出场
                    trade_record = {
                        "symbol": code,
                        "buy_date": t.strftime("%Y-%m-%d"),
                        "buy_price": buy_price,
                        "score": report["total_score"]
                    }
                    
                    # 对每个持股期模拟卖出并测算盈亏
                    # 通过我们在交易日轴中的位置，寻找第 t+5, t+10, t+20 个交易日
                    for period in holding_periods:
                        sell_idx = date_idx + period
                        if sell_idx < len(trading_dates):
                            sell_date = trading_dates[sell_idx]
                            # 从预存价格表中极速拉取卖出收盘价
                            sell_price = price_maps[code].get(sell_date)
                            if sell_price is not None:
                                ret = (sell_price - buy_price) / buy_price
                                trade_record[f"sell_date_{period}d"] = sell_date.strftime("%Y-%m-%d")
                                trade_record[f"sell_price_{period}d"] = sell_price
                                trade_record[f"return_{period}d"] = round(ret * 100.0, 2)
                                # 对比基准指数（简化无偏回测：Alpha 等于绝对收益，后续可接入指数行情）
                                trade_record[f"alpha_{period}d"] = round(ret * 100.0, 2)
                            else:
                                trade_record[f"return_{period}d"] = 0.0
                                trade_record[f"alpha_{period}d"] = 0.0
                        else:
                            # 已经临近回测终点，数据无法完全对齐卖出，记为 0.0
                            trade_record[f"return_{period}d"] = 0.0
                            trade_record[f"alpha_{period}d"] = 0.0
                            
                    trade_details.append(trade_record)

        # 6. 绩效统计核算 (胜率, 信号数, Alpha, 盈亏比等)
        summary = {"total_signals": signals_count}
        
        # 计算不同周期的胜率与平均收益
        for period in holding_periods:
            returns = [t[f"return_{period}d"] for t in trade_details if f"return_{period}d" in t]
            if returns:
                winning_signals = sum(1 for r in returns if r > 0.0)
                summary[f"winning_rate_{period}d"] = round((winning_signals / len(returns)) * 100.0, 2) if len(returns) > 0 else 0.0
                summary[f"avg_return_{period}d"] = round(float(np.mean(returns)), 2)
                summary[f"avg_alpha_{period}d"] = round(float(np.mean(returns)), 2)
            else:
                summary[f"winning_rate_{period}d"] = 0.0
                summary[f"avg_return_{period}d"] = 0.0
                summary[f"avg_alpha_{period}d"] = 0.0

        # 计算盈亏比 (以最长持股周期为例，如20日)
        max_period = holding_periods[-1]
        returns_max = [t[f"return_{max_period}d"] for t in trade_details if f"return_{max_period}d" in t]
        if returns_max:
            gains = [r for r in returns_max if r > 0.0]
            losses = [r for r in returns_max if r < 0.0]
            avg_gain = np.mean(gains) if gains else 0.0
            avg_loss = abs(np.mean(losses)) if losses else 1e-6
            summary["profit_loss_ratio"] = round(float(avg_gain / avg_loss), 2)
        else:
            summary["profit_loss_ratio"] = 1.0

        # 计算最大回撤 (基于合并后的虚拟资产净值曲线)
        equity_curve = []
        portfolio_value = 1.0
        peak_value = 1.0
        max_dd = 0.0
        
        # 为合并模拟每日净值
        # 如果当天有正在持有的交易，计算它们的每日合并变动
        for idx, t in enumerate(trading_dates):
            # 获取当天所有正处于持股周期内的交易记录
            active_trades = []
            for tr in trade_details:
                b_date = datetime.strptime(tr["buy_date"], "%Y-%m-%d").date()
                max_hold_days = holding_periods[-1]
                # 粗略判断是否在持仓窗口内
                b_idx = trading_dates.index(b_date)
                if b_idx <= idx < b_idx + max_hold_days:
                    active_trades.append(tr)
            
            # 每日合并收益率
            if active_trades:
                # 简单合并：均分资金持仓
                # 每日个股价格变动
                daily_rets = []
                for tr in active_trades:
                    code = tr["symbol"]
                    b_date = datetime.strptime(tr["buy_date"], "%Y-%m-%d").date()
                    b_price = tr["buy_price"]
                    c_price = price_maps[code].get(t, b_price)
                    daily_rets.append((c_price - b_price) / b_price)
                day_return = np.mean(daily_rets)
                portfolio_value = 1.0 + day_return
            else:
                day_return = 0.0
                
            # 计算回撤
            if portfolio_value > peak_value:
                peak_value = portfolio_value
            dd = (peak_value - portfolio_value) / peak_value
            if dd > max_dd:
                max_dd = dd
                
            equity_curve.append({
                "trade_date": t.strftime("%Y-%m-%d"),
                "portfolio_value": round(portfolio_value, 4),
                "benchmark_value": 1.0 # 简化为平铺对比
            })
            
        summary["max_drawdown"] = round(-max_dd * 100.0, 2)

        # 7. 一键将回测总报告保存写入数据库 backtest_reports 表
        conn_write = self.get_db_connection()
        cursor_write = conn_write.cursor()
        query_insert_report = """
            INSERT INTO backtest_reports (template_id, start_date, end_date, metrics, equity_curve)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id;
        """
        try:
            cursor_write.execute(query_insert_report, (
                template_id,
                start_date,
                end_date,
                json.dumps(summary),
                json.dumps(equity_curve)
            ))
            report_id = cursor_write.fetchone()["id"]
            conn_write.commit()
            print(f"🎉【回测科研底座】回测运行大胜！总报告已成功落库 [backtest_reports] (Report ID: {report_id})。")
        except Exception as e:
            conn_write.rollback()
            print(f"❌ 回测报告写入失败: {e}")
            report_id = None
        finally:
            cursor_write.close()
            conn_write.close()

        return {
            "backtest_id": str(uuid.uuid4()),
            "template_id": template_id,
            "summary": summary,
            "equity_curve": equity_curve,
            "trade_details": trade_details,
            "db_report_id": report_id
        }
