# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - FastAPI 核心接口微服务
职责：提供面向前端/客户端的模板管理、全市场扫描、形态对齐比对、历史无偏回测等完整的 RESTful API 黄金通道。
"""

from fastapi import FastAPI, HTTPException, Query, Path, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from datetime import datetime, date, timedelta
import json
import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import pandas as pd

from backend.app.core.config import settings
from backend.app.template_manager.manager import TemplateManager
from backend.app.scanner_sentry.sentry import ScannerSentry
from backend.app.backtest_engine.engine import BacktestEngine
from backend.app.indicator_engine.engine import calculate_indicators
from backend.app.feature_engine.engine import calculate_features
from backend.app.similarity_engine.engine import SimilarityEngine
from backend.app.event_engine.engine import sanitize_numpy

# -------------------------------------------------------------------------
# 统一日志文件与控制台滚动输出配置 (完美解决 Windows 平台下的中文乱码)
# -------------------------------------------------------------------------
import os
from logging.handlers import TimedRotatingFileHandler

# 1. 确保 logs 目录存在
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
log_file = os.path.join(log_dir, "backend_app.log")

# 2. 配置格式 (包含时间、日志级别、命名空间、具体消息)
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')

# 3. 控制台输出 Handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# 4. 本地文件追加输出 Handler (每日午夜自动滚动按日期分割，最多保留最近 30 天历史日志)
file_handler = TimedRotatingFileHandler(
    log_file, 
    when="midnight", 
    interval=1, 
    backupCount=30, 
    encoding="utf-8"
)
# 自定义滚动生成的日志文件日期后缀：如 backend_app.log.2026-07-19
file_handler.suffix = "%Y-%m-%d"
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# 5. 配置 Root 根 Logger 确保捕获全子模块传播的日志事实
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = []
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

# 6. 获取 MicroService 命名空间的 Logger
logger = logging.getLogger("MicroService")

app = FastAPI(
    title="Pattern Search Engine (PSE) API Service",
    description="PSE 智能形态选股与滚动回测平台微服务接口",
    version=settings.VERSION
)

# 【神级全域跨域中间件】
# 100% 杜绝前端开发调试、生产部署时因 CORS 域名端口不同引起的跨域拦截，直接起飞！
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------------
# 0. 常用 Pydantic 数据模式
# -------------------------------------------------------------------------
class APIResponse(BaseModel):
    success: bool = True
    data: dict = {}
    error: str = None

class TemplateCreatePayload(BaseModel):
    name: str = Field(..., example="经典布林回踩启动")
    type: str = Field("historical", example="historical")
    config: dict = Field(..., example={"window_size": 60})
    weights: dict = Field(..., example={"close_norm": 0.25})

class ScanTaskPayload(BaseModel):
    template_id: int = Field(..., example=1)
    run_date: str = Field(..., example="2026-07-19")

class BacktestPayload(BaseModel):
    template_id: int = Field(..., example=1)
    start_date: str = Field("2026-03-01", example="2026-03-01")
    end_date: str = Field("2026-07-19", example="2026-07-19")
    score_threshold: float = Field(50.0, example=50.0)

class FeedbackPayload(BaseModel):
    result_id: int = Field(..., example=1)
    label: str = Field(..., example="good_match") # good_match, bad_match, watchlist, ignore
    comment: str = Field(None, example="缩量结构踩线中轨很像")

class SyncMarketDataPayload(BaseModel):
    max_workers: int = Field(default=8, example=8)
    retry_limit: int = Field(default=3, example=3)
    delay_min: int = Field(default=100, example=100)
    delay_max: int = Field(default=300, example=300)

# 辅助数据库连接
def get_db_connection():
    return psycopg2.connect(settings.DATABASE_URL, cursor_factory=RealDictCursor)


# -------------------------------------------------------------------------
# 1. 模板管理 RESTful 接口
# -------------------------------------------------------------------------
@app.get("/api/templates")
def list_templates():
    """
    拉取全量形态模板配置列表
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, type, config, created_at FROM feature_templates ORDER BY id ASC;")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return {"success": True, "data": sanitize_numpy({"templates": rows}), "error": None}

@app.get("/api/templates/{template_id}")
def get_template_details(template_id: int = Path(..., description="模板 ID")):
    """
    获取单个形态模板的详细参数配置 Json
    """
    tpl_manager = TemplateManager()
    tpl = tpl_manager.get_template_by_id(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"模板 {template_id} 不存在")
    # 解包 JSONB 格式
    return {"success": True, "data": sanitize_numpy(tpl), "error": None}

@app.post("/api/templates")
def create_template(payload: TemplateCreatePayload):
    """
    注册并创建一个新的形态匹配/抽象模板
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        INSERT INTO feature_templates (name, type, config, weights)
        VALUES (%s, %s, %s, %s)
        RETURNING id;
    """
    try:
        cursor.execute(query, (
            payload.name,
            payload.type,
            json.dumps(payload.config),
            json.dumps(payload.weights)
        ))
        new_id = cursor.fetchone()["id"]
        conn.commit()
        return {"success": True, "data": {"template_id": new_id}, "error": None}
    except Exception as e:
        conn.rollback()
        return {"success": False, "data": None, "error": f"创建模板失败: {e}"}
    finally:
        cursor.close()
        conn.close()


@app.put("/api/templates/{template_id}")
def update_template(template_id: int, payload: TemplateCreatePayload):
    """
    覆盖/覆写更新当前已有的形态模板参数配置与权重分布
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        UPDATE feature_templates
        SET name = %s, type = %s, config = %s, weights = %s
        WHERE id = %s;
    """
    try:
        cursor.execute(query, (
            payload.name,
            payload.type,
            json.dumps(payload.config),
            json.dumps(payload.weights),
            template_id
        ))
        conn.commit()
        return {"success": True, "message": f"成功修改并覆写形态模板 ID: {template_id}", "error": None}
    except Exception as e:
        conn.rollback()
        return {"success": False, "data": None, "error": f"覆写形态模板失败: {e}"}
    finally:
        cursor.close()
        conn.close()


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: int):
    """
    一键物理拔线清除并销毁指定形态模板，同时幂等清理与其关联的 scan_results 结果
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 先清除关联的扫描落地结果，防御数据库外键报错
        cursor.execute("DELETE FROM scan_results WHERE template_id = %s;", (template_id,))
        # 2. 物理清除特征模板本身
        cursor.execute("DELETE FROM feature_templates WHERE id = %s;", (template_id,))
        conn.commit()
        return {"success": True, "message": f"成功物理清除形态模板 ID: {template_id} 及其扫描落地大账", "error": None}
    except Exception as e:
        conn.rollback()
        return {"success": False, "data": None, "error": f"删除形态模板失败: {e}"}
    finally:
        cursor.close()
        conn.close()


# -------------------------------------------------------------------------
# 2. 全市场每日形态扫描接口
# -------------------------------------------------------------------------
@app.post("/api/search-runs")
def run_market_scan(payload: ScanTaskPayload):
    """
    一键激活全市场自动扫描哨兵，匹配今日 Top 20 极品标的并落库
    """
    sentry = ScannerSentry()
    try:
        top_20 = sentry.run_daily_scan(payload.run_date, payload.template_id)
        if top_20 is None:
            return {"success": False, "data": None, "error": "扫描失败，无法加载模板母体或数据不足"}
        return {"success": True, "data": sanitize_numpy({"status": "completed", "results_count": len(top_20), "results": top_20}), "error": None}
    except Exception as e:
        logger.error(f"每日扫描运行异常: {e}")
        return {"success": False, "data": None, "error": str(e)}

@app.get("/api/search-runs/results")
def get_scan_results(
    run_date: str = Query(..., example="2026-07-19"),
    template_id: int = Query(..., example=1)
):
    """
    查询数据库中某日形态扫描推荐大PK的落地持久化记录
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.date, s.code, s.similarity_score, s.sub_scores, s.explanation, s.risk_tips, st.name
        FROM scan_results s
        JOIN stocks st ON s.code = st.code
        WHERE s.date = %s AND s.template_id = %s
        ORDER BY s.similarity_score DESC;
    """, (run_date, template_id))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return {"success": True, "data": sanitize_numpy({"results": rows}), "error": None}


# -------------------------------------------------------------------------
# 3. 历史滚动无偏回测接口
# -------------------------------------------------------------------------
@app.post("/api/backtests")
def run_historical_backtest(payload: BacktestPayload):
    """
    触发科学无偏形态回测引擎，分析形态历史赚钱胜率、盈亏比及净值走势
    """
    engine = BacktestEngine()
    try:
        report = engine.run_backtest(
            payload.template_id,
            payload.start_date,
            payload.end_date,
            payload.score_threshold
        )
        return {"success": True, "data": sanitize_numpy(report), "error": None}
    except Exception as e:
        logger.error(f"历史回测运行异常: {e}")
        return {"success": False, "data": None, "error": str(e)}


# -------------------------------------------------------------------------
# 3.5 人工标注反馈接口
# -------------------------------------------------------------------------
@app.post("/api/feedback")
def submit_feedback(payload: FeedbackPayload):
    """
    提交人工标注反馈，并自动启动正负反馈特征权重微调 (自适应在线学习 L1 维持 1.0)
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 动态建立 user_feedback 缓存表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id SERIAL PRIMARY KEY,
                result_id BIGINT NOT NULL,
                label VARCHAR(32) NOT NULL,
                comment TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """)
        
        # 2. 插入用户反馈记录
        cursor.execute("""
            INSERT INTO user_feedback (result_id, label, comment)
            VALUES (%s, %s, %s);
        """, (payload.result_id, payload.label, payload.comment))

        # 3. 提取扫描推荐落地结果以匹配权重
        cursor.execute("""
            SELECT template_id, sub_scores FROM scan_results WHERE id = %s;
        """, (payload.result_id,))
        result_row = cursor.fetchone()
        if not result_row:
            conn.commit()
            return {"success": True, "message": "反馈成功落库，但未查询到结果的 sub_scores，略过模板权重微调", "error": None}
            
        template_id = result_row["template_id"]
        sub_scores = result_row["sub_scores"]
        
        # 4. 获取模板原本的 weights
        cursor.execute("""
            SELECT weights FROM feature_templates WHERE id = %s;
        """, (template_id,))
        tpl_row = cursor.fetchone()
        if not tpl_row:
            conn.commit()
            return {"success": True, "message": "反馈成功落库，但未查到特征模板，略过模板权重微调", "error": None}
            
        weights = tpl_row["weights"]
        
        # 5. 映射 5 大类得分到 7 个特征维度
        score_map = {
            "close_norm": sub_scores.get("trend_score", 50.0),
            "return_5d": sub_scores.get("trend_score", 50.0),
            "boll_mid_dist": sub_scores.get("boll_score", 50.0),
            "volume_ratio_20": sub_scores.get("volume_score", 50.0),
            "close_position": sub_scores.get("candle_score", 50.0),
            "range_ratio": sub_scores.get("volatility_score", 50.0),
            "atr_ratio": sub_scores.get("volatility_score", 50.0),
        }
        
        features = ["close_norm", "boll_mid_dist", "volume_ratio_20", "close_position", "return_5d", "range_ratio", "atr_ratio"]
        S_f = {f: float(score_map[f]) for f in features}
        S_mean = sum(S_f.values()) / len(features)
        
        # 6. 计算微调增量
        eta = 0.05
        new_weights = {}
        for f in features:
            w = float(weights.get(f, 1.0 / len(features)))
            sf = S_f[f]
            if payload.label == "good_match":
                w = w + eta * (sf - S_mean) * w * (1.0 - w)
            elif payload.label == "bad_match":
                w = w - eta * (sf - S_mean) * w * (1.0 - w)
            new_weights[f] = max(0.01, min(0.99, w)) # 截断，保证不为 0
            
        # L1 归一化
        w_sum = sum(new_weights.values())
        if w_sum > 0:
            new_weights = {f: round(w / w_sum, 4) for f, w in new_weights.items()}
        else:
            new_weights = {f: round(1.0 / len(features), 4) for f in features}
            
        # 7. 更新特征模板的权重
        cursor.execute("""
            UPDATE feature_templates
            SET weights = %s, updated_at = NOW()
            WHERE id = %s;
        """, (json.dumps(new_weights), template_id))
        
        conn.commit()
        logger.info(f"🎯 成功基于标注更新权重！Template: {template_id}, 新权重: {new_weights}")
        return {"success": True, "data": {"updated_weights": new_weights}, "error": None}
    except Exception as e:
        conn.rollback()
        logger.error(f"标注反馈权重微调异常: {e}")
        return {"success": False, "data": None, "error": str(e)}
    finally:
        cursor.close()
        conn.close()


# -------------------------------------------------------------------------
# 3.8 全市场行情异步强制同步定时任务接口 (支持新参数热重载与手抖重叠过滤)
# -------------------------------------------------------------------------
# 全局同步运行状态锁和上一任参数备份，支持新参数热插拔与连续手抖去重
IS_SYNCING_LOCKED = False
LAST_ACTIVE_PAYLOAD = None

@app.post("/api/jobs/sync-market-data")
def trigger_market_data_sync(payload: SyncMarketDataPayload, background_tasks: BackgroundTasks):
    """
    一键激活全 A 股 5300+ 行情时序增量抓取异步流水线 (Background Tasks 守护模式，不阻塞返回)
    内置 IS_SYNCING_LOCKED 运行状态锁、LAST_ACTIVE_PAYLOAD 触发备份与自适应世代释放套。
    支持参数发生变动时的【世代代差优雅死刑 + 新参数断点热重启】高级热重载机制！
    """
    global IS_SYNCING_LOCKED, LAST_ACTIVE_PAYLOAD
    from backend.app.data_center.sync import DataCenterSync
    
    # 转换为 dict 方便进行深度参数对齐比对
    current_payload_dict = payload.dict()
    
    if IS_SYNCING_LOCKED:
        # 如果上一个同步正在运行，检查参数是否一致
        if LAST_ACTIVE_PAYLOAD == current_payload_dict:
            # 参数完全一致，说明是连续点击，进行手抖拦截
            return {
                "success": False,
                "data": None,
                "error": "🚨 警告：系统当前正有一个参数【完全相同】的同步任务在全速搬运中！为防范频繁重复触发导致您的本地 IP 遭金融网关爬虫拉黑，已自动为您安全拦截。若需要重拉，请微调一下右侧的任何一个反爬延迟或并发数再点击，系统即可自动热切换！"
            }
        else:
            # 参数变了！朔哥哥调整了参数！
            # 宣判上一代死刑：递增全局世代 ID
            DataCenterSync.CURRENT_GENERATION_ID += 1
            logger.info(f"🔄 检测到配置参数发生变更！最新世代已增至 {DataCenterSync.CURRENT_GENERATION_ID}。系统正在向老任务下达优雅退位指令...")
    else:
        # 如果本来就没有运行，直接落锁，并初始化世代
        IS_SYNCING_LOCKED = True
        
    current_task_gen_id = DataCenterSync.CURRENT_GENERATION_ID
    try:
        # 1. 备份当前的运行参数
        LAST_ACTIVE_PAYLOAD = current_payload_dict
        
        # 2. 包装带 finally 自动释锁的后台任务
        def sync_task_wrapper():
            global IS_SYNCING_LOCKED, LAST_ACTIVE_PAYLOAD
            logger.info(f"🔒 [Sync Guard Gen {current_task_gen_id}] 新世代同步实例已安全落锁起飞。")
            try:
                sync_engine = DataCenterSync(
                    max_concurrent=payload.max_workers,
                    retry_limit=payload.retry_limit,
                    delay_min=payload.delay_min,
                    delay_max=payload.delay_max
                )
                sync_engine.sync_all_daily_bars(max_workers=payload.max_workers)
            except Exception as ex:
                logger.error(f"❌ 后台行情拉取异步任务执行发生未捕获异常: {ex}")
            finally:
                # 3. 极其细致的安全防误解锁机制：只有当全局最新世代依然是本任务启动时的世代，才去释放状态锁！
                if DataCenterSync.CURRENT_GENERATION_ID == current_task_gen_id:
                    IS_SYNCING_LOCKED = False
                    LAST_ACTIVE_PAYLOAD = None
                    logger.info(f"🔓 [Sync Guard Gen {current_task_gen_id}] 全局行情增量同步运行锁已安全释放。")

        background_tasks.add_task(sync_task_wrapper)
        
        msg = f"🚀 全市场行情同步进程（第 {current_task_gen_id} 世代）点火起飞成功！"
        if current_task_gen_id > 0:
            msg = f"🔄 成功下达老一代优雅退役指令！新世代（第 {current_task_gen_id} 世代）行情数据巨轮已按最新参数（并发:{payload.max_workers}线程）顺利完成断点热重载！"
            
        return {
            "success": True, 
            "message": msg, 
            "error": None
        }
    except Exception as e:
        # 如果在点火期就产生异常，安全重置锁
        if DataCenterSync.CURRENT_GENERATION_ID == current_task_gen_id:
            IS_SYNCING_LOCKED = False
            LAST_ACTIVE_PAYLOAD = None
        logger.error(f"强制触发全市场同步任务点火异常: {e}")
        return {"success": False, "data": None, "error": str(e)}


# -------------------------------------------------------------------------
# 3.9 当日行情数据增量同步接口（快速更新当日最新数据）
# -------------------------------------------------------------------------
@app.post("/api/jobs/sync-today-data")
def trigger_today_data_sync(payload: SyncMarketDataPayload, background_tasks: BackgroundTasks):
    """
    仅同步当日最新股票行情数据。
    预查询已落库当日数据的股票，仅对缺失的个股执行增量抓取，速度快、API 请求量小。
    内置 IS_SYNCING_LOCKED 运行状态锁，防止与全市场同步任务冲突。
    """
    global IS_SYNCING_LOCKED, LAST_ACTIVE_PAYLOAD
    from backend.app.data_center.sync import DataCenterSync

    current_payload_dict = payload.dict()

    if IS_SYNCING_LOCKED:
        if LAST_ACTIVE_PAYLOAD == current_payload_dict:
            return {
                "success": False,
                "data": None,
                "error": "当前有同步任务正在运行中，请等待完成后再试。"
            }
        else:
            DataCenterSync.CURRENT_GENERATION_ID += 1
            logger.info(f"检测到配置参数发生变更！最新世代已增至 {DataCenterSync.CURRENT_GENERATION_ID}。系统正在向老任务下达优雅退位指令...")
    else:
        IS_SYNCING_LOCKED = True

    current_task_gen_id = DataCenterSync.CURRENT_GENERATION_ID
    try:
        LAST_ACTIVE_PAYLOAD = current_payload_dict

        def sync_task_wrapper():
            global IS_SYNCING_LOCKED, LAST_ACTIVE_PAYLOAD
            logger.info(f"[Sync Guard Gen {current_task_gen_id}] 当日数据同步实例已安全落锁起飞。")
            try:
                sync_engine = DataCenterSync(
                    max_concurrent=payload.max_workers,
                    retry_limit=payload.retry_limit,
                    delay_min=payload.delay_min,
                    delay_max=payload.delay_max
                )
                sync_engine.sync_today_data(max_workers=payload.max_workers)
            except Exception as ex:
                logger.error(f"后台当日数据同步任务执行发生未捕获异常: {ex}")
            finally:
                if DataCenterSync.CURRENT_GENERATION_ID == current_task_gen_id:
                    IS_SYNCING_LOCKED = False
                    LAST_ACTIVE_PAYLOAD = None
                    logger.info(f"[Sync Guard Gen {current_task_gen_id}] 全局当日数据同步运行锁已安全释放。")

        background_tasks.add_task(sync_task_wrapper)

        msg = f"当日行情数据同步进程（第 {current_task_gen_id} 世代）点火起飞成功！"
        if current_task_gen_id > 0:
            msg = f"成功下达老一代优雅退役指令！新世代（第 {current_task_gen_id} 世代）当日数据同步已按最新参数顺利启动。"

        return {
            "success": True,
            "message": msg,
            "error": None
        }
    except Exception as e:
        if DataCenterSync.CURRENT_GENERATION_ID == current_task_gen_id:
            IS_SYNCING_LOCKED = False
            LAST_ACTIVE_PAYLOAD = None
        logger.error(f"强制触发当日数据同步任务点火异常: {e}")
        return {"success": False, "data": None, "error": str(e)}


# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# 4.5 后台同步状态查询接口
# -------------------------------------------------------------------------
@app.get("/api/jobs/sync-status")
def get_sync_status():
    """
    查询后台数据同步状态（支持独立守护进程和 FastAPI BackgroundTasks 两种模式）。
    返回最近一次同步的开始/结束时间、耗时、状态，以及数据库实际最新日K日期。
    """
    try:
        import psycopg2
        from datetime import date, timedelta
        conn = psycopg2.connect(settings.DATABASE_URL)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT key, value FROM data_sync_status;")
            rows = cursor.fetchall()
            status_map = {k: v for k, v in rows}

            cursor.execute("SELECT MAX(date) FROM daily_bars;")
            max_row = cursor.fetchone()
            max_bar_date = max_row[0].strftime("%Y-%m-%d") if max_row and max_row[0] else "N/A"

            today = date.today()
            weekday = today.weekday()
            expected_latest = today
            if weekday == 5:
                expected_latest = today - timedelta(days=1)
            elif weekday == 6:
                expected_latest = today - timedelta(days=2)

            data_is_fresh = (
                isinstance(max_bar_date, str) and
                max_bar_date != "N/A" and
                date.fromisoformat(max_bar_date) >= expected_latest
            )

            return {
                "success": True,
                "data": {
                    "sync_status_table": status_map,
                    "actual_latest_bar_date": max_bar_date,
                    "expected_latest_date": expected_latest.strftime("%Y-%m-%d"),
                    "is_fresh": data_is_fresh,
                    "last_sync_status": status_map.get("last_status", "unknown"),
                    "last_sync_mode": status_map.get("last_mode", "none"),
                    "last_sync_duration_sec": status_map.get("last_duration_sec", "none"),
                },
                "error": None
            }
        finally:
            cursor.close()
            conn.close()
    except Exception as e:
        logger.error(f"查询同步状态异常: {e}")
        return {"success": False, "data": None, "error": str(e)}

# 4. 核心同屏对齐比对比对 API (同屏画图神器)
# -------------------------------------------------------------------------
@app.get("/api/compare/template/{template_id}/stock/{symbol}")
def compare_template_with_stock(
    template_id: int = Path(..., description="模板 ID"),
    symbol: str = Path(..., description="候选股票代码 (如 sz000002)"),
    end_date: str = Query(..., description="形态对齐截止交易日")
):
    """
    【最强同屏比对 API】：
    将个股在指定 end_date 的滑动特征窗口，与模板特征矩阵拉平对齐，
    返回各维度分项得分、DTW 时间扭曲对齐路径、已捕获事件置信度、以及 AI 可解释性研判文本，完美对齐第13章契约！
    """
    tpl_manager = TemplateManager()
    tpl = tpl_manager.get_template_by_id(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"未找到模板 ID: {template_id}")
        
    config = tpl["config"]
    window_size = config.get("window_size", 60)
    
    # 1. 载入模板母体特征窗口
    source_symbol = config.get("source_symbol", "sz000002")
    source_end = datetime.strptime(config.get("source_end", "2026-05-01"), "%Y-%m-%d").date()
    
    sentry = ScannerSentry()
    df_temp_raw = sentry.load_stock_bars(source_symbol, source_end, lookback_days=250)
    if df_temp_raw.empty:
        raise HTTPException(status_code=500, detail="模板母体量价数据加载失败")
        
    df_temp_ind = calculate_indicators(df_temp_raw)
    df_temp_feat = calculate_features(df_temp_ind, source_symbol)
    df_temp_window = df_temp_feat.tail(window_size).copy()
    
    # 2. 载入候选股票特征窗口
    target_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    df_cand_raw = sentry.load_stock_bars(symbol, target_date, lookback_days=250)
    if df_cand_raw.empty or len(df_cand_raw) < 120:
        raise HTTPException(status_code=400, detail=f"候选股 [{symbol}] 数据缺失或未暖机充分")
        
    df_cand_ind = calculate_indicators(df_cand_raw)
    df_cand_feat = calculate_features(df_cand_ind, symbol)
    df_cand_window = df_cand_feat.tail(window_size).copy()
    
    if len(df_cand_window) < window_size:
        raise HTTPException(status_code=400, detail="候选股滑动窗口特征数据不足")

    # 3. 运行多维相似度计算
    sim_engine = SimilarityEngine()
    report = sim_engine.compute_composite_similarity(df_temp_window, df_cand_window, symbol)
    
    # 4. 提取今日已匹配高斯模糊事件置信度详情，完美响应前端气泡渲染
    cand_events = sim_engine.event_engine.detect_all_events(df_cand_window)
    matched_events_resp = []
    for evt_type, evt in cand_events.items():
        matched_events_resp.append({
            "event_type": evt.event_type,
            "date": evt.end_date.strftime("%Y-%m-%d") if isinstance(evt.end_date, (date, datetime)) else str(evt.end_date),
            "confidence": round(evt.confidence, 4),
            "evidence": evt.evidence
        })

    # 5. 组装契约响应
    temp_bars = []
    for _, row in df_temp_window.iterrows():
        temp_bars.append({
            "date": row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], (date, datetime)) else str(row["date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "boll_mid": float(row["boll_mid"]) if "boll_mid" in row and not pd.isna(row["boll_mid"]) else None,
            "boll_upper": float(row["boll_upper"]) if "boll_upper" in row and not pd.isna(row["boll_upper"]) else None,
            "boll_lower": float(row["boll_lower"]) if "boll_lower" in row and not pd.isna(row["boll_lower"]) else None,
        })
        
    cand_bars = []
    for _, row in df_cand_window.iterrows():
        cand_bars.append({
            "date": row["date"].strftime("%Y-%m-%d") if isinstance(row["date"], (date, datetime)) else str(row["date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "boll_mid": float(row["boll_mid"]) if "boll_mid" in row and not pd.isna(row["boll_mid"]) else None,
            "boll_upper": float(row["boll_upper"]) if "boll_upper" in row and not pd.isna(row["boll_upper"]) else None,
            "boll_lower": float(row["boll_lower"]) if "boll_lower" in row and not pd.isna(row["boll_lower"]) else None,
        })

    compare_report = {
        "template_symbol": source_symbol,
        "candidate_symbol": symbol,
        "window_size": window_size,
        "temp_bars": temp_bars,
        "cand_bars": cand_bars,
        "similarity_scores": {
            "total_score": report["total_score"],
            "breakdown": report["score_breakdown"]
        },
        "alignment_path": report["alignment_path"],
        "matched_events": matched_events_resp,
        "explanation_facts": report["explanation_facts"]
    }
    
    return {"success": True, "data": sanitize_numpy(compare_report), "error": None}


# -------------------------------------------------------------------------
# 4.5 数据库与时序行情状态统计接口
# -------------------------------------------------------------------------
@app.get("/api/stats")
def get_database_statistics():
    """
    一键拉取本地数据仓库当前的宏观运行事实（个股总数、日K总行数、以及时序最大最新交易日）
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # 1. 统计 stocks 表中记录的股票总数
        cursor.execute("SELECT COUNT(*) as cnt FROM stocks;")
        total_stocks = cursor.fetchone()["cnt"]
        
        # 2. 统计已实际落库 K 线历史的去重个股数量 (完美契合需求)
        cursor.execute("SELECT COUNT(DISTINCT code) as cnt FROM daily_bars;")
        total_bars = cursor.fetchone()["cnt"]
        
        # 3. 统计 daily_bars 中已落库的最大最新日期
        cursor.execute("SELECT MAX(date) as max_dt FROM daily_bars;")
        max_dt_row = cursor.fetchone()
        latest_date = "N/A"
        if max_dt_row and max_dt_row["max_dt"]:
            latest_date = max_dt_row["max_dt"].strftime("%Y-%m-%d") if isinstance(max_dt_row["max_dt"], (date, datetime)) else str(max_dt_row["max_dt"])
            
        return {
            "success": True,
            "data": {
                "total_stocks": total_stocks,
                "total_bars": total_bars,
                "latest_bar_date": latest_date
            },
            "error": None
        }
    except Exception as e:
        logger.error(f"提取本地数据仓库状态事实异常: {e}")
        return {"success": False, "data": None, "error": str(e)}
    finally:
        cursor.close()
        conn.close()


# -------------------------------------------------------------------------
# 4.6 实时服务器日志监控接口
# -------------------------------------------------------------------------
@app.get("/api/logs")
def get_server_logs(lines: int = Query(20, description="读取的尾部日志行数")):
    """
    提取本地运行日志文件 logs/backend_app.log 的尾部行，全力支持前端极客终端实时渲染
    """
    log_file = "logs/backend_app.log"
    if not os.path.exists(log_file):
        return {"success": True, "data": {"logs": ["📝 暂无本地日志事实记录，请先通过同步按钮触发激活。"]}, "error": None}
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            tail_lines = all_lines[-lines:]
            tail_lines = [line.strip() for line in tail_lines if line.strip()]
        return {"success": True, "data": {"logs": tail_lines}, "error": None}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


# -------------------------------------------------------------------------
# 4.7 单个股基本面字典极速查询接口
# -------------------------------------------------------------------------
@app.get("/api/stocks/{symbol}")
def get_stock_fundamental_details(symbol: str = Path(..., description="股票代码 (如 sz000002)")):
    """
    拉取本地 Stocks 字典表中单只个股的基础信息（如中文名称、板块分类），支持前端实时校验与回显
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT code, name, board, industry FROM stocks WHERE code = %s;", (symbol.lower().strip(),))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "data": None, "error": f"股票池中暂未找到此代码"}
        return {"success": True, "data": dict(row), "error": None}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}
    finally:
        cursor.close()
        conn.close()


# -------------------------------------------------------------------------
# 服务器自动初始化与守护启动
# -------------------------------------------------------------------------
@app.on_event("startup")
def startup_event():
    """
    当服务微启动时，自动调用模板管理器注册“布林回踩”形态模板，免除人工预先建库的痛苦
    """
    logger.info("⚡ PSE API 接口微服务正在启动，启动预先初始化检查...")
    tpl_manager = TemplateManager()
    tpl_manager.init_default_templates()
    logger.info("⚡ 预设特征模板状态检查通过，准备对外倾泄行情计算服务！")
