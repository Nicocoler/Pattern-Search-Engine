# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 全市场形态扫描自动哨兵 (Scanner Sentry)
职责：获取所有活跃成分股名单，应用流动性剪枝，调取多维 DTW 对齐大脑进行并发核分，
并筛选出每日匹配度最高的 Top 20 极品标的一键 Upsert 入库。
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np
from psycopg2.extras import execute_values
import logging

from backend.app.core import db
from backend.app.core.config import settings
from backend.app.filters.boll_mid_filter import has_boll_mid_breach
from backend.app.indicator_engine.engine import calculate_indicators
from backend.app.feature_engine.engine import calculate_features
from backend.app.similarity_engine.engine import SimilarityEngine
from backend.app.template_manager.manager import TemplateManager

logger = logging.getLogger("ScannerSentry")

class ScannerSentry:
    def __init__(self):
        self.similarity_engine = SimilarityEngine()
        self.template_manager = TemplateManager()

    def load_stock_bars(self, code: str, end_date: date, lookback_days: int = 250) -> pd.DataFrame:
        """
        从数据库查询个股到截止日期为止、暖机加宽的历史日 K 序列 (以确保滚动均线指标充分暖机)
        """
        start_date = end_date - timedelta(days=lookback_days)
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            query = """
                SELECT date, open, high, low, close, volume, amount, factor
                FROM daily_bars
                WHERE code = %s AND date >= %s AND date <= %s
                ORDER BY date ASC;
            """
            cursor.execute(query, (code, start_date, end_date))
            rows = cursor.fetchall()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        # 强制将 Decimal 类型列强制转换为 float 与 int，以支持高能 Pandas/Numpy 科学计算
        for col in ['open', 'high', 'low', 'close', 'amount', 'factor']:
            if col in df.columns:
                df[col] = df[col].astype(float)
        if 'volume' in df.columns:
            df['volume'] = df['volume'].astype(int)

        return df

    def load_active_stock_pool(
        self,
        min_amount_20d: float = 10000000.0,
        allow_st: bool = False,
    ) -> list[dict]:
        """
        查询全市场满足基础硬过滤硬指标的候选标的股票池：
        1. stocks 表中：非 ST（除非 allow_st=True）、非停牌。
        2. daily_bars 20日均量成交额：均值必须大于门槛，排除僵尸股。
        """
        if allow_st:
            where_clause = "1=1"
        else:
            where_clause = "s.is_st = FALSE"
        query = f"""
            SELECT s.code, s.name, s.board, AVG(b.amount) as avg_amount_20d
            FROM (
                SELECT code, amount,
                       ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as rn
                FROM daily_bars
            ) b
            JOIN stocks s ON b.code = s.code
            WHERE {where_clause}
              AND s.is_suspended = FALSE
              AND b.rn <= 20
            GROUP BY s.code, s.name, s.board
            HAVING COUNT(b.amount) >= 20
               AND AVG(b.amount) >= %s;
        """
        try:
            with db.db_cursor(dict_cursor=True) as (conn, cursor):
                cursor.execute(query, (min_amount_20d,))
                rows = cursor.fetchall()
            # 将 RealDictRow 转为标准的 dict 以对齐上层协议
            active_pool = [dict(r) for r in rows]
            return active_pool
        except Exception as e:
            logger.error(f"❌ 高能 SQL 提取活跃股票池异常: {e}")
            return []

    def process_single_candidate(
        self,
        cand,
        target_date,
        df_temp_window,
        window_size,
        require_boll_mid_filter=False,
        feature_weights: dict | None = None,
        required_events: list[str] | None = None,
    ):
        """
        高能线程原子弹：承载单只个股日K拉取、滚动指标、特征提取、布林初筛、以及多维 DTW 对齐核分
        - require_boll_mid_filter: 当模板需要 BOLL_MIDDLE_SUPPORT 事件时启用中轨硬过滤
        - feature_weights: 模板特征权重，传入 compute_composite_similarity
        - required_events: 模板必需事件序列，传入 compute_composite_similarity
        """
        code = cand["code"]
        name = cand["name"]
        try:
            # 1. 暖机日K
            df_cand_raw = self.load_stock_bars(code, target_date, lookback_days=250)
            if df_cand_raw.empty or len(df_cand_raw) < 120:
                return None

            # 2. 计算指标特征
            df_cand_ind = calculate_indicators(df_cand_raw)
            df_cand_feat = calculate_features(df_cand_ind, code)

            # 3. 滑动特征窗口
            df_cand_window = df_cand_feat.tail(window_size).copy()
            if len(df_cand_window) < window_size:
                return None

            # 4. 布林空间软剪枝：先确认曾回调到近中轨位置
            min_boll_dist = df_cand_window['boll_mid_dist'].abs().min()
            if min_boll_dist > settings.BOLL_PRUNE_THRESHOLD:
                return None

            # 4b. 布林中轨硬过滤：回调触及中轨后收盘价不可跌破（仅当模板需要时启用）
            if require_boll_mid_filter and has_boll_mid_breach(df_cand_window):
                logger.debug(f"❌ [布林中轨跌破] {code} 被硬过滤：close < boll_mid")
                return None

            # 5. DTW 相似度核分（注入模板权重和事件序列）
            report = self.similarity_engine.compute_composite_similarity(
                df_temp_window,
                df_cand_window,
                code,
                feature_weights=feature_weights,
                required_events=required_events,
            )
            report["name"] = name
            return report
        except Exception:
            return None

    def run_daily_scan(self, target_date_str: str, template_id: int):
        """
        对全 A 股市场进行一键形态扫描匹配哨兵大扫荡：
        - target_date_str: 形态检索对齐的截止基准日（如 "2026-07-19"）
        - template_id: 模板配置 ID
        """
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        logger.info(f"🔮【全市场形态扫描哨兵】启动！基准交易截止日: {target_date} | 模板ID: {template_id}")

        # 1. 加载形态模板配置
        template = self.template_manager.get_template_by_id(template_id)
        if not template:
            logger.error(f"❌ 未找到形态模板 [ID: {template_id}]，扫描终止。")
            return

        config = template["config"]
        window_size = config.get("window_size", 60)
        hard_filters = config.get("hard_filters", {})
        min_amount = float(hard_filters.get("min_amount_20d", 10000000.0))
        allow_st = bool(hard_filters.get("allow_st", False))

        # 模板驱动：如果模板需要 BOLL_MIDDLE_SUPPORT 事件，则启用中轨硬过滤
        required_events = config.get("required_events", [])
        require_boll_mid_filter = "BOLL_MIDDLE_SUPPORT" in required_events
        if require_boll_mid_filter:
            logger.info(f"🔒 [模板驱动] 该模板需要 BOLL_MIDDLE_SUPPORT，已启用中轨硬过滤（回调触中轨后收盘不可跌破）")
        else:
            logger.info(f"ℹ️ [模板驱动] 该模板不需要 BOLL_MIDDLE_SUPPORT，跳过中轨硬过滤")

        # 读取模板特征权重，供相似度引擎使用
        template_weights = template.get("weights", {}) or {}
        if not template_weights:
            template_weights = self.similarity_engine.feature_weights

        # 2. 编译模板母体的"黄金物理特征矩阵"
        source_symbol = config.get("source_symbol", "sz000002")
        source_end = datetime.strptime(config.get("source_end", "2026-05-01"), "%Y-%m-%d").date()

        logger.info(f"👉 正在加载模板母体经典时段数据: {source_symbol} 截止到 {source_end}...")
        df_temp_raw = self.load_stock_bars(source_symbol, source_end, lookback_days=250)
        if df_temp_raw.empty:
            logger.error(f"❌ 无法加载模板母体 [{source_symbol}] 经典日K行情底座，扫描无法对齐！")
            return

        df_temp_ind = calculate_indicators(df_temp_raw)
        df_temp_feat = calculate_features(df_temp_ind, source_symbol)
        # 截取模板最后 N 天特征矩阵
        df_temp_window = df_temp_feat.tail(window_size).copy()
        if len(df_temp_window) < window_size:
            logger.error(f"❌ 模板母体特征序列不足 {window_size} 天，计算终止。")
            return

        # 3. 调取有流动性硬过滤的候选成分股票池 (第一级剪枝)
        logger.info("👉 正在启动第一级 [流动性硬门槛] 候选股筛选...")
        candidate_pool = self.load_active_stock_pool(min_amount_20d=min_amount, allow_st=allow_st)
        logger.info(f"✅ 第一级剪枝完毕！全市场共 {len(candidate_pool)} 只成分活跃股进入特征深度检索。")

        # 4. 并行多线程高并发核分 (8 线程并行碾压，合入高能进度实时日志反馈)
        logger.info(f"👉 正在启动第二级 [多维 DTW 弹性对齐] 多线程高并发核分大PK (并发线程规模: 8)...")
        scored_results = []

        total_candidates = len(candidate_pool)
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=8) as executor:
            # 分发并行 Future 任务池，将模板驱动的中轨过滤标志、特征权重和事件序列传递给每个候选
            future_to_cand = {
                executor.submit(
                    self.process_single_candidate, cand, target_date, df_temp_window, window_size,
                    require_boll_mid_filter, template_weights, required_events
                ): cand
                for cand in candidate_pool
            }

            for i, future in enumerate(as_completed(future_to_cand), 1):
                res = future.result()
                if res is not None:
                    scored_results.append(res)

                # 每 100 只股打印一行极具盯盘视觉快感的实时进度与均速报告
                if i % 100 == 0 or i == total_candidates:
                    elapsed = time.time() - start_time
                    speed = i / elapsed if elapsed > 0 else 0
                    logger.info(f"📊 扫描对齐进度: {i}/{total_candidates} ({i/total_candidates*100:.1f}%) | 成功入围核分: {len(scored_results)} | 耗时: {elapsed:.1f}s | 均速: {speed:.1f}股/秒")

        # 5. 形态对齐综合得分大比拼，高低决选
        # 按总评分从高到低大排行
        scored_results.sort(key=lambda r: r["total_score"], reverse=True)

        # 6. 一键将匹配度最高的 Top 20 选股落地 upsert 写入 scan_results 表
        top_20 = scored_results[:20]

        if scored_results:
            logger.info(f"📊 扫描完毕！形态得分大PK已出炉。前 3 名匹配状元如下：")
            for rank, res in enumerate(scored_results[:3], 1):
                logger.info(f" 🏆【第{rank}名】{res['symbol']} ({res['name']}) | 匹配得分: {res['total_score']}分 | 衍生正面事实: {res['explanation_facts']['positive_facts'][:2]}")
        else:
            logger.warning("📊 本次扫描完成，但没有股票通过所有筛选条件。")

        # 7. 整理并打包落库
        records_to_insert = []
        for res in top_20:
            # 特别注意：scan_results 表中的 similarity_score 对应 NUMERIC(6, 4)
            # 所以需要将我们 0.0 ~ 100.0 分的 total_score 完美除以 100 转换为 0.0000 ~ 1.0000 写入！
            db_similarity_score = float(res["total_score"]) / 100.0

            sub_scores_json = json.dumps(res["score_breakdown"])
            explanation_txt = " | ".join(res["explanation_facts"]["positive_facts"])
            risk_tips_txt = " | ".join(res["explanation_facts"]["negative_facts"])

            records_to_insert.append((
                target_date,
                template_id,
                res["symbol"],
                db_similarity_score,
                sub_scores_json,
                explanation_txt,
                risk_tips_txt
            ))

        if not records_to_insert:
            logger.warning("本次形态扫描没有筛选出符合任何特征硬指标的股票，无数据写入。")
            return []

        conn_write = db.acquire(dict_cursor=True)
        cursor_write = conn_write.cursor()

        # 先清除今日同一模板的历史扫描数据，确保重跑幂等与数据无重复
        cursor_write.execute(
            "DELETE FROM scan_results WHERE date = %s AND template_id = %s;",
            (target_date, template_id)
        )

        insert_query = """
            INSERT INTO scan_results (date, template_id, code, similarity_score, sub_scores, explanation, risk_tips)
            VALUES %s;
        """
        try:
            execute_values(cursor_write, insert_query, records_to_insert)
            conn_write.commit()
            logger.info(f"🚀【形态每日推荐】扫描大捷！成功向 [scan_results] 一键落库写入了 Top {len(records_to_insert)} 只核心匹配标的！")
        except Exception as e:
            conn_write.rollback()
            logger.error(f"批量写入 scan_results 失败: {e}")
        finally:
            cursor_write.close()
            db.release(conn_write)

        return top_20
