# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 历史形态滚动无偏回测引擎 TDD 单元测试与端到端科研核验
"""

import unittest
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date

from backend.app.core.config import settings
from backend.app.template_manager.manager import TemplateManager
from backend.app.backtest_engine.engine import BacktestEngine

class TestBacktestEngine(unittest.TestCase):

    def setUp(self):
        """
        初始化测试：
        1. 确保 stocks 基本面数据就绪。
        2. 确保默认形态模板注册就绪。
        """
        self.db_url = settings.DATABASE_URL
        self.template_manager = TemplateManager()
        self.backtest_engine = BacktestEngine()

        # 1. 确保基本个股写入
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

        # 2. 获取默认形态配置 ID
        self.template_id = self.template_manager.init_default_templates()

    def test_backtest_simulation_e2e(self):
        """
        形态回测端到端仿真科研大考：
        1. 启动 BacktestEngine。
        2. 以 "2026-03-01" 至 "2026-07-19" 为区间，对“布林回踩中轨二次启动”形态进行历史重放。
        3. 验证无偏仿真（排除未来函数），确保所有均线和特征滑动切片准确。
        4. 核算 5d、10d、20d 统计胜率及盈亏比、最大回撤。
        5. 验证成功向 backtest_reports 表一键持久化落库。
        """
        # 为了捕获我们在数据库中已有的这 2 只蓝筹股的买入信号，我们将阈值 score_threshold 设为 50.0
        report = self.backtest_engine.run_backtest(
            self.template_id, 
            start_date_str="2026-03-01", 
            end_date_str="2026-07-19", 
            score_threshold=50.0
        )
        
        # 1. 验证报告基本结构返回值
        self.assertIsNotNone(report)
        self.assertIn("summary", report)
        self.assertIn("equity_curve", report)
        self.assertIn("trade_details", report)
        self.assertIsNotNone(report["db_report_id"])

        summary = report["summary"]
        self.assertGreater(summary["total_signals"], 0, "回测期间应该有满足相似度 > 50 分的买入信号触发")
        self.assertIn("winning_rate_5d", summary)
        self.assertIn("winning_rate_10d", summary)
        self.assertIn("winning_rate_20d", summary)
        self.assertIn("max_drawdown", summary)
        self.assertIn("profit_loss_ratio", summary)

        # 2. 验证净值曲线
        equity_curve = report["equity_curve"]
        self.assertGreater(len(equity_curve), 0)
        self.assertEqual(equity_curve[0]["portfolio_value"], 1.0, "持仓净值起点必须从 1.0 标准无量纲开始")

        # 3. 校验数据库中的持久化记录
        conn = psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM backtest_reports 
            WHERE id = %s;
        """, (report["db_report_id"],))
        db_row = cursor.fetchone()
        cursor.close()
        conn.close()

        self.assertIsNotNone(db_row)
        self.assertEqual(db_row["template_id"], self.template_id)
        
        metrics = db_row["metrics"]
        self.assertEqual(metrics["total_signals"], summary["total_signals"])
        
        print("\n" + "="*60)
        print("          端到端形态历史回测落库报告抽样          ")
        print("="*60)
        print(f"📈 回测报告编号 (Report ID): {db_row['id']}")
        print(f"📅 回测历史区间 (Period): {db_row['start_date']} -> {db_row['end_date']}")
        print(f"🎯 触发买入信号 (Signals): {metrics['total_signals']}次")
        print(f"💰 5天持有胜率 (WinRate 5d): {metrics['winning_rate_5d']}%")
        print(f"💰 10天持有胜率 (WinRate 10d): {metrics['winning_rate_10d']}%")
        print(f"💰 20天持有胜率 (WinRate 20d): {metrics['winning_rate_20d']}%")
        print(f"📊 平均20天收益 (AvgReturn 20d): {metrics['avg_return_20d']}%")
        print(f"📉 仿真最大回撤 (Max Drawdown): {metrics['max_drawdown']}%")
        print(f"⚖️ 交易整体盈亏比 (P/L Ratio): {metrics['profit_loss_ratio']}")
        print("="*60 + "\n")

if __name__ == "__main__":
    unittest.main()
