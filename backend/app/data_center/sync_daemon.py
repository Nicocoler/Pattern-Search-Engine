# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 独立后台行情同步守护进程
=====================================================
职责：
  1. 每天收盘后（默认 16:00）自动执行当日增量同步
  2. 支持首次全量同步 (--full)
  3. 通过 PID 文件防重入、数据库状态表追踪、独立日志
  4. 完全独立于 FastAPI 主后端，重启主后端不影响同步

运行方式：
  python -m backend.app.data_center.sync_daemon              # 增量同步当天数据
  python -m backend.app.data_center.sync_daemon --full       # 首次全量同步
  python -m backend.app.data_center.sync_daemon --status     # 查看同步状态
  python -m backend.app.data_center.sync_daemon --install-scheduler   # 安装 Windows 计划任务
  python -m backend.app.data_center.sync_daemon --uninstall-scheduler # 卸载计划任务

注意：本脚本必须在项目根目录 E:\\IDEAProject\\形态选股软件\\PSE 下运行，
     或通过 PYTHONPATH 指向该目录。
"""

import os
import sys
import json
import time
import signal
import argparse
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler

# 确保项目根目录在 sys.path 中（兼容从任意目录启动）
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.core.config import settings
from backend.app.data_center.sync import DataCenterSync

BASE_DIR = _PROJECT_ROOT
PID_FILE = BASE_DIR / "logs" / "sync_daemon.pid"
LOG_DIR = BASE_DIR / "logs"
STATUS_DB_TABLE = "data_sync_status"


def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "sync_daemon.log"
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    logger = logging.getLogger("SyncDaemon")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


logger = setup_logger()


class DaemonLock:
    def __init__(self, pid_file):
        self.pid_file = pid_file

    def acquire(self):
        if self.pid_file.exists():
            old_pid = self.pid_file.read_text().strip()
            if old_pid and self._pid_alive(int(old_pid)):
                logger.error(f"锁定文件中已有运行中的进程 PID={old_pid}，拒绝重复启动。")
                return False
            if old_pid:
                try:
                    self.pid_file.unlink()
                except OSError:
                    pass
        try:
            self.pid_file.write_text(str(os.getpid()))
            logger.info(f"守护进程 PID={os.getpid()} 锁定文件已创建。")
            return True
        except OSError as e:
            logger.error(f"无法创建 PID 文件：{e}")
            return False

    def release(self):
        try:
            if self.pid_file.exists() and self.pid_file.read_text().strip() == str(os.getpid()):
                self.pid_file.unlink()
                logger.info(f"守护进程 PID={os.getpid()} 已释放锁定文件。")
        except OSError:
            pass

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def ensure_sync_status_table(db_url):
    import psycopg2
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {STATUS_DB_TABLE} (
                key VARCHAR(32) PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """)
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def get_sync_status(db_url):
    """读取同步状态，表不存在时返回空字典。"""
    import psycopg2
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s);", (STATUS_DB_TABLE,))
        exists = cursor.fetchone()[0]
        if not exists:
            return {}
        cursor.execute(f"SELECT key, value FROM {STATUS_DB_TABLE};")
        rows = cursor.fetchall()
        return {k: v for k, v in rows}
    finally:
        cursor.close()
        conn.close()


def set_sync_status(db_url, key, value):
    import psycopg2
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    try:
        cursor.execute(f"""
            INSERT INTO {STATUS_DB_TABLE} (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
        """, (key, value))
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def print_sync_status(db_url):
    status = get_sync_status(db_url)
    if not status:
        print(json.dumps({"success": True, "data": {"message": "尚未有任何同步记录"}, "error": None}, ensure_ascii=False, indent=2))
        return
    import psycopg2
    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT MAX(date) FROM daily_bars;")
        row = cursor.fetchone()
        max_bar_date = row[0].strftime("%Y-%m-%d") if row and row[0] else "N/A"
    finally:
        cursor.close()
        conn.close()
    result = {"success": True, "data": {"status_table": status, "actual_latest_bar_date": max_bar_date}, "error": None}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def install_scheduler(task_name="PSE_DailyDataSync", run_hour=16, run_minute=0):
    python_exe = sys.executable
    script_module = "backend.app.data_center.sync_daemon"
    cmd_parts = [
        "schtasks", "/Create", "/TN", task_name,
        "/TR", f"\'{python_exe}\' -m {script_module}",
        "/SC", "DAILY", "/ST", f"{run_hour:02d}:{run_minute:02d}",
        "/RU", "SYSTEM", "/RL", "HIGHEST", "/F",
        "/DESC", "PSE daily sync",
    ]
    cmd = " ".join(cmd_parts)
    logger.info(f"正在创建 Windows 计划任务：{task_name}")
    ret = os.system(cmd)
    if ret == 0:
        logger.info(f"计划任务 '{task_name}' 创建成功！")
        print(json.dumps({"success": True, "data": {"task_name": task_name, "schedule": f"每天 {run_hour:02d}:{run_minute:02d}"}, "error": None}, ensure_ascii=False, indent=2))
    else:
        logger.error(f"计划任务创建失败，返回码：{ret}")
        print(json.dumps({"success": False, "data": None, "error": f"schtasks 返回码 {ret}"}, ensure_ascii=False))


def uninstall_scheduler(task_name="PSE_DailyDataSync"):
    cmd_parts = ["schtasks", "/Delete", "/TN", task_name, "/F"]
    cmd = " ".join(cmd_parts)
    ret = os.system(cmd)
    if ret == 0:
        logger.info(f"计划任务 '{task_name}' 已卸载。")
        print(json.dumps({"success": True, "data": {"task_name": task_name}, "error": None}, ensure_ascii=False))
    else:
        print(json.dumps({"success": False, "error": f"schtasks 返回码 {ret}"}, ensure_ascii=False))


def list_scheduler_status(task_name="PSE_DailyDataSync"):
    cmd_parts = ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"]
    cmd = " ".join(cmd_parts)
    print(os.popen(cmd).read())


def run_sync(full=False, max_workers=8):
    lock = DaemonLock(PID_FILE)
    if not lock.acquire():
        return 1

    sync_engine = None
    start_time = time.time()

    def _handle_signal(signum, frame):
        logger.warning(f"收到信号 {signum}，正在优雅停止...")
        if sync_engine:
            DataCenterSync.CURRENT_GENERATION_ID += 1

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        db_url = settings.DATABASE_URL
        ensure_sync_status_table(db_url)
        set_sync_status(db_url, "last_start_time", datetime.now().isoformat())
        set_sync_status(db_url, "last_status", "running")

        mode = "全量同步" if full else "增量同步"
        logger.info("=" * 60)
        logger.info(f"🚀 守护进程启动 | 模式: {mode} | PID: {os.getpid()}")
        logger.info(f"   并发线程池: {max_workers}")
        logger.info("=" * 60)

        sync_engine = DataCenterSync()
        if full:
            sync_engine.sync_all_daily_bars(max_workers=max_workers)
        else:
            sync_engine.sync_today_data(max_workers=max_workers)

        elapsed = time.time() - start_time
        set_sync_status(db_url, "last_end_time", datetime.now().isoformat())
        set_sync_status(db_url, "last_duration_sec", str(round(elapsed, 2)))
        set_sync_status(db_url, "last_status", "success")
        set_sync_status(db_url, "last_mode", "full" if full else "incremental")

        logger.info("=" * 60)
        logger.info(f"✅ 同步完成 | 耗时: {elapsed:.1f}s")
        logger.info("=" * 60)
        summary = {
            "success": True, "pid": os.getpid(),
            "mode": "full" if full else "incremental",
            "duration_sec": round(elapsed, 2),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        logger.exception(f"❌ 同步异常: {e}")
        if sync_engine:
            set_sync_status(settings.DATABASE_URL, "last_status", "failed")
            set_sync_status(settings.DATABASE_URL, "last_error", str(e))
        return 1
    finally:
        lock.release()


def main():
    parser = argparse.ArgumentParser(description="PSE 后台行情同步守护进程")
    parser.add_argument("--full", action="store_true", help="全量同步所有股票历史日K（仅首次使用）")
    parser.add_argument("--max-workers", type=int, default=8, help="同步线程池大小 (默认: 8)")
    parser.add_argument("--status", action="store_true", help="查看同步状态并退出")
    parser.add_argument("--install-scheduler", action="store_true", help="安装 Windows 每日计划任务（默认 16:00）")
    parser.add_argument("--uninstall-scheduler", action="store_true", help="卸载 Windows 计划任务")
    parser.add_argument("--query-scheduler", action="store_true", help="查询计划任务状态")
    parser.add_argument("--scheduler-time", type=str, default="16:00", help="计划任务执行时间 HH:MM")
    args = parser.parse_args()

    if args.status:
        print_sync_status(settings.DATABASE_URL)
        return
    if args.install_scheduler:
        hour, minute = map(int, args.scheduler_time.split(":"))
        install_scheduler(run_hour=hour, run_minute=minute)
        return
    if args.uninstall_scheduler:
        uninstall_scheduler()
        return
    if args.query_scheduler:
        list_scheduler_status()
        return
    exit_code = run_sync(full=args.full, max_workers=args.max_workers)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
