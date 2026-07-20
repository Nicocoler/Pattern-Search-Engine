# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 模板管理系统 (Template Manager)
职责：实现形态模板的创建、版本管理、以及预存系统默认的“布林回踩中轨二次启动”经典模板。
"""

import json
import psycopg2
from psycopg2.extras import RealDictCursor
from backend.app.core.config import settings

class TemplateManager:
    def __init__(self):
        self.db_url = settings.DATABASE_URL

    def get_db_connection(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def init_default_templates(self):
        """
        初始化系统预设的默认模板：“布林回踩中轨二次启动”。
        作为系统的主力形态模板，直接注册进 feature_templates 数据库中。
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # 1. 检查是否已经存在
        cursor.execute("SELECT id FROM feature_templates WHERE name = %s;", ("布林回踩中轨二次启动",))
        row = cursor.fetchone()
        if row:
            logger_id = row['id']
            cursor.close()
            conn.close()
            return logger_id

        # 2. 构造默认模板配置 (结合高斯事件流必需项与回测契约参数)
        template_name = "布林回踩中轨二次启动"
        template_type = "historical" # 以万科A在历史上的经典回踩时段作为物理对齐参考源
        
        config = {
            "window_size": 60,
            "source_symbol": "sz000002", # 采用万科A作为经典形态母体
            "source_start": "2026-01-01", # 暖机加宽拉取时间
            "source_end": "2026-05-01",
            "hard_filters": {
                "min_amount_20d": 10000000, # 20日均成交额低于 1000w 判定为僵尸股剔除
                "allow_st": False,          # 绝缘 ST/退市整理股
                "max_suspended_days": 3     # 允许最大停牌天数 3 天
            },
            "required_events": [
                "TREND_UP",
                "TOUCH_BOLL_UPPER",
                "PULLBACK",
                "VOLUME_SHRINK",
                "TOUCH_BOLL_MIDDLE",
                "BOLL_MIDDLE_SUPPORT"
            ],
            "default_backtest_config": {
                "holding_periods": [5, 10, 20],
                "benchmark": "sz399300", # 深证沪深300指数作为业绩基准对比
                "score_threshold": 80.0
            }
        }
        
        weights = {
            "close_norm": 0.25,        # 归一化收盘价权重
            "boll_mid_dist": 0.20,     # 中轨偏离度权重
            "volume_ratio_20": 0.15,   # 成交量缩量倍率权重
            "close_position": 0.15,    # K线落脚点相对权重
            "return_5d": 0.10,         # 短期收益率排布
            "range_ratio": 0.10,       # 振幅系数
            "atr_ratio": 0.05          # 波动率对齐
        }

        # 3. 写入数据库
        query = """
            INSERT INTO feature_templates (name, type, config, weights)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
        """
        try:
            cursor.execute(query, (
                template_name,
                template_type,
                json.dumps(config),
                json.dumps(weights)
            ))
            new_id = cursor.fetchone()['id']
            conn.commit()
            print(f"🎉【模板管理系统】系统默认模板 [{template_name}] (ID: {new_id}) 初始化注册就绪！")
            return new_id
        except Exception as e:
            conn.rollback()
            print(f"❌ 注册预设模板失败: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_template_by_id(self, template_id: int) -> dict:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM feature_templates WHERE id = %s;", (template_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None

    def get_template_by_name(self, name: str) -> dict:
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM feature_templates WHERE name = %s;", (name,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(row) if row else None
