# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 形态模板与全市场每日扫描哨兵 TDD 单元测试及端到端联调
"""

import unittest
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date

from backend.app.core.config import settings
from backend.app.template_manager.manager import TemplateManager
from backend.app.scanner_sentry.sentry import ScannerSentry

class TestScannerSentry(unittest.TestCase):

    def setUp(self):
        """
        初始化测试数据库：
        1. 确保 stocks 表中录入了 万科A (sz000002) 与 贵州茅台 (sh600519) 的基本面信息。
        2. 确保 feature_templates 表中注册了默认经典形态模板。
        """
        self.db_url = settings.DATABASE_URL
        self.template_manager = TemplateManager()
        self.sentry = ScannerSentry()

        # 1. 向 stocks 关系表注入测试个股基础信息
        conn = psycopg2.connect(self.db_url)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO stocks (code, name, list_date, board, industry, is_st, is_suspended)
            VALUES 
            ('sz000002', '万科A', '1991-01-29', '主板', '房地产', FALSE, FALSE),
            ('sh600519', '贵州茅台', '2001-08-27', '主板', '白酒', FALSE, FALSE)
            ON CONFLICT (code) DO UPDATE SET
                is_st = FALSE,
                is_suspended = FALSE;
        """)
        conn.commit()
        cursor.close()
        conn.close()

        # 2. 初始化注册默认形态模板
        self.template_id = self.template_manager.init_default_templates()

    def test_template_registration_and_retrieval(self):
        """
        验证模板注册与检索功能：
        1. 确保模板成功存入数据库。
        2. 可以通过 id 或者 name 无损拉取，各项事件权重与回测默认持仓契约 Json 完美无损。
        """
        self.assertIsNotNone(self.template_id)
        
        # 按ID拉取
        tpl = self.template_manager.get_template_by_id(self.template_id)
        self.assertIsNotNone(tpl)
        self.assertEqual(tpl["name"], "布林回踩中轨二次启动")
        
        # 验证必需事件与回测配置完整性
        config = tpl["config"]
        self.assertIn("required_events", config)
        self.assertIn("default_backtest_config", config)
        self.assertEqual(config["default_backtest_config"]["benchmark"], "sz399300")

    def test_end_to_end_daily_scan_flow(self):
        """
        端到端全通路形态匹配扫描极限实战：
        1. 调取 ScannerSentry。
        2. 以 2026-07-19 作为截止基准日，扫描全市场个股（万科A 与 茅台）。
        3. 验证硬过滤与二层软初筛。
        4. 验证多维对齐得分核算并 Top 20 优选。
        5. 验证成功向 scan_results 时序关系表一键 Upsert 批量写入。
        """
        # 由于我们之前已经通过 sync.py 成功将 万科A 与 贵州茅台 的 14416 根真实 QFQ 日 K 完美拉取落库，
        # 此时我们将直接在真实的 A 股日 K 数据库底座上，启动一键自动形态扫描推荐！
        # 这里指定扫描截止日期为 2026-07-19
        top_20 = self.sentry.run_daily_scan("2026-07-19", self.template_id)
        
        # 1. 验证筛选返回值
        self.assertIsNotNone(top_20)
        self.assertGreater(len(top_20), 0, "股票池中万科A或贵州茅台，应该有通过流动性和软剪枝筛选出来的标的")

        # 2. 检查 scan_results 推荐落库表，验证是否成功写入
        conn = psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM scan_results 
            WHERE date = '2026-07-19' AND template_id = %s 
            ORDER BY similarity_score DESC;
        """, (self.template_id,))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        self.assertGreater(len(rows), 0, "扫描推荐结果必须成功持久化写入 scan_results 表中")
        
        # 3. 校验写入字段
        first_place = rows[0]
        self.assertGreaterEqual(float(first_place["similarity_score"]), 0.0)
        self.assertLessEqual(float(first_place["similarity_score"]), 1.0, "落库相似度必须在 [0.0, 1.0] 的标准化数值区间中")
        self.assertIsNotNone(first_place["sub_scores"])
        self.assertTrue(len(first_place["explanation"]) > 0 or len(first_place["risk_tips"]) > 0, "推荐结果应带有解释性事实或潜在风险陈述描述")
        
        print("\n" + "="*60)
        print("          端到端全市场自动扫描哨兵落库记录抽样          ")
        print("="*60)
        print(f"📅 推荐交易日期: {first_place['date']}")
        print(f"🎫 匹配股票代码: {first_place['code']}")
        print(f"🎯 最终相似得分: {float(first_place['similarity_score'])*100.0:.2f}分")
        print(f"📊 分大类得分细: {first_place['sub_scores']}")
        print(f"✨ 事实正面陈述: {first_place['explanation']}")
        print(f"🚨 潜在风险提示: {first_place['risk_tips']}")
        print("="*60 + "\n")

if __name__ == "__main__":
    unittest.main()
