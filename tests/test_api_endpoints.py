# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - FastAPI 核心接口微服务 TDD 单元测试与端到端联调
"""

import unittest
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import RealDictCursor

from backend.app.core.config import settings
from backend.app.main import app

class TestAPIEndpoints(unittest.TestCase):

    def setUp(self):
        """
        初始化 FastAPI 测试客户端，并注入所需的基本面股票。
        """
        self.client = TestClient(app)
        self.db_url = settings.DATABASE_URL
        
        # 确保 stocks 表中有万科A和贵州茅台
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

    def test_01_templates_api(self):
        """
        1. 验证 GET /api/templates: 拉取模板列表
        2. 验证 GET /api/templates/{id}: 拉取模板详情
        """
        # 测试拉取模板列表
        response = self.client.get("/api/templates")
        self.assertEqual(response.status_code, 200)
        res_json = response.json()
        self.assertTrue(res_json["success"])
        self.assertIn("templates", res_json["data"])
        
        templates = res_json["data"]["templates"]
        self.assertGreater(len(templates), 0, "数据库中应至少包含默认注册好的一个经典模板")
        template_id = templates[0]["id"]
        
        # 测试拉取单个模板详情
        response_detail = self.client.get(f"/api/templates/{template_id}")
        self.assertEqual(response_detail.status_code, 200)
        res_detail_json = response_detail.json()
        self.assertTrue(res_detail_json["success"])
        self.assertEqual(res_detail_json["data"]["name"], "布林回踩中轨二次启动")

    def test_02_compare_alignment_api(self):
        """
        验证最核心的同屏比对对齐 API:
        GET /api/compare/template/1/stock/sz000002?end_date=2026-07-19
        测试：对齐路径、各维度分项得分、已匹配事件置信度等契约结构无损输出。
        """
        # 1. 自动查询默认模板 ID
        response_tpls = self.client.get("/api/templates")
        template_id = response_tpls.json()["data"]["templates"][0]["id"]

        # 2. 调用核心比对 API
        response = self.client.get(f"/api/compare/template/{template_id}/stock/sz000002?end_date=2026-07-19")
        self.assertEqual(response.status_code, 200)
        
        res_json = response.json()
        self.assertTrue(res_json["success"])
        
        data = res_json["data"]
        self.assertEqual(data["candidate_symbol"], "sz000002")
        self.assertEqual(data["window_size"], 60)
        self.assertIn("similarity_scores", data)
        self.assertIn("alignment_path", data)
        self.assertIn("matched_events", data)
        self.assertIn("explanation_facts", data)

        print("\n" + "="*60)
        print("          FastAPI [最强同屏比对 API] 深度对齐测试大捷          ")
        print("="*60)
        print(f"🎫 候选股票比对: {data['candidate_symbol']} vs 模板母体 {data['template_symbol']}")
        print(f"🎯 相似度总分值: {data['similarity_scores']['total_score']}分")
        print(f"📈 对齐路径长度: {len(data['alignment_path'])}")
        print(f"✨ 匹配到事件数: {len(data['matched_events'])}")
        print(f"✨ AI 客观解释 : {data['explanation_facts']['positive_facts']}")
        print("="*60 + "\n")

    def test_03_daily_scan_run_and_results_api(self):
        """
        1. 验证 POST /api/search-runs: 自动执行全市场自动形态扫描
        2. 验证 GET /api/search-runs/results: 拉取当日形态扫描成果大PK落地数据
        """
        response_tpls = self.client.get("/api/templates")
        template_id = response_tpls.json()["data"]["templates"][0]["id"]

        # 测试一键触发每日形态扫描
        payload = {
            "template_id": template_id,
            "run_date": "2026-07-19"
        }
        response_scan = self.client.post("/api/search-runs", json=payload)
        self.assertEqual(response_scan.status_code, 200)
        self.assertTrue(response_scan.json()["success"])
        
        # 测试拉取落地记录
        response_results = self.client.get(f"/api/search-runs/results?run_date=2026-07-19&template_id={template_id}")
        self.assertEqual(response_results.status_code, 200)
        res_json = response_results.json()
        self.assertTrue(res_json["success"])
        self.assertGreater(len(res_json["data"]["results"]), 0)

    def test_04_historical_backtest_api(self):
        """
        验证 POST /api/backtests: 触发历史滚动无偏回测并一键落库
        """
        response_tpls = self.client.get("/api/templates")
        template_id = response_tpls.json()["data"]["templates"][0]["id"]

        payload = {
            "template_id": template_id,
            "start_date": "2026-03-01",
            "end_date": "2026-07-19",
            "score_threshold": 50.0
        }
        response_bt = self.client.post("/api/backtests", json=payload)
        self.assertEqual(response_bt.status_code, 200)
        
        res_json = response_bt.json()
        self.assertTrue(res_json["success"])
        self.assertIn("summary", res_json["data"])
        self.assertIn("equity_curve", res_json["data"])
        self.assertIsNotNone(res_json["data"]["db_report_id"])

if __name__ == "__main__":
    unittest.main()
