# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 模板管理系统 (Template Manager)
职责：实现形态模板的创建、版本管理、以及预存系统默认的“布林回踩中轨二次启动”经典模板。
"""

import json
import logging

from backend.app.core import db

logger = logging.getLogger("TemplateManager")

# 模板查询显式列名（避免 SELECT * 在加列时破坏映射）
_TEMPLATE_COLUMNS = "id, name, type, config, weights, created_at, updated_at"


class TemplateManager:
    def init_default_templates(self):
        """
        初始化系统预设的默认模板：“布林回踩中轨二次启动”。
        作为系统的主力形态模板，直接注册进 feature_templates 数据库中。
        """
        with db.db_conn(dict_cursor=True) as conn:
            cursor = conn.cursor()
            try:
                # 1. 检查是否已经存在
                cursor.execute("SELECT id FROM feature_templates WHERE name = %s;", ("布林回踩中轨二次启动",))
                row = cursor.fetchone()
                if row:
                    return row['id']

                # 2. 构造默认模板配置 (结合高斯事件流必需项与回测契约参数)
                template_name = "布林回踩中轨二次启动"
                template_type = "historical"  # 以万科A在历史上的经典回踩时段作为物理对齐参考源

                config = {
                    "window_size": 60,
                    "source_symbol": "sz000002",  # 采用万科A作为经典形态母体
                    "source_start": "2026-01-01",  # 暖机加宽拉取时间
                    "source_end": "2026-05-01",
                    "hard_filters": {
                        "min_amount_20d": 10000000,  # 20日均成交额低于 1000w 判定为僵尸股剔除
                        "allow_st": False,           # 绝缘 ST/退市整理股
                        "max_suspended_days": 3      # 允许最大停牌天数 3 天
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
                        "benchmark": "sz399300",  # 深证沪深300指数作为业绩基准对比
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
                cursor.execute(query, (
                    template_name,
                    template_type,
                    json.dumps(config),
                    json.dumps(weights)
                ))
                new_id = cursor.fetchone()['id']
                conn.commit()
                logger.info("系统默认模板 [%s] (ID: %s) 初始化注册就绪！", template_name, new_id)
                return new_id
            except Exception as e:
                conn.rollback()
                logger.error("注册预设模板失败: %s", e)
                return None
            finally:
                cursor.close()

    def get_template_by_id(self, template_id: int) -> dict:
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute(
                f"SELECT {_TEMPLATE_COLUMNS} FROM feature_templates WHERE id = %s;",
                (template_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_template_by_name(self, name: str) -> dict:
        with db.db_cursor(dict_cursor=True) as (conn, cursor):
            cursor.execute(
                f"SELECT {_TEMPLATE_COLUMNS} FROM feature_templates WHERE name = %s;",
                (name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
