# -*- coding: utf-8 -*-
"""
PSE - 统一数据库连接池管理 (DB Pool)
职责：基于 psycopg2.pool.ThreadedConnectionPool 提供进程级连接复用，
消除各模块每次查询新建/关闭 TCP 连接的高昂握手开销，并统一 cursor 语义与 try/finally 资源释放。

设计要点：
1. 懒加载 + 线程安全初始化的进程级单例连接池。
2. acquire(dict_cursor) 在取出的连接上按需设置 cursor_factory，使后续 conn.cursor() 自动返回
   dict cursor 或普通 tuple cursor，向后兼容现有各模块的 row 访问语义（RealDictRow / 索引）。
3. db_cursor 上下文管理器统一封装 acquire / cur / release，finally 保证归还连接，
   一并解决历史代码中缺失 try/finally 导致的连接泄漏。
注意：跨进程不可见，仅适用于单 worker 部署（跨进程需 Redis/DB 行锁，见 docs）。
"""

import logging
import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from backend.app.core.config import settings

logger = logging.getLogger("DBPool")

# 进程级单例池与一次性初始化锁
_pool: ThreadedConnectionPool | None = None
_init_lock = threading.Lock()

# 连接池规模（保守值，可按需在 config 暴露）
_MIN_CONN = 2
_MAX_CONN = 16


def _get_pool() -> ThreadedConnectionPool:
    """懒加载创建进程级连接池（线程安全）。"""
    global _pool
    if _pool is not None:
        return _pool
    with _init_lock:
        if _pool is None:
            if not settings.DATABASE_URL:
                raise RuntimeError(
                    "DATABASE_URL 未配置：请在项目根目录 .env 中设置 DATABASE_URL，"
                    "或运行 `python init_db.py` 完成交互式建库引导。"
                )
            _pool = ThreadedConnectionPool(
                minconn=_MIN_CONN,
                maxconn=_MAX_CONN,
                dsn=settings.DATABASE_URL,
            )
            logger.info("数据库连接池已初始化（min=%d, max=%d）", _MIN_CONN, _MAX_CONN)
    return _pool


def acquire(dict_cursor: bool = False):
    """
    从池中取出一条连接。
    - dict_cursor=True 时设置 conn.cursor_factory=RealDictCursor，使 conn.cursor() 返回字典游标
      （向后兼容 main/sentry/backtest/template_manager 的 dict 访问）。
    - dict_cursor=False 时返回普通 tuple 游标（向后兼容 sync / sync_daemon 的索引访问）。
    调用方必须在 finally 中调用 release(conn) 归还，或直接使用 db_cursor 上下文管理器。
    """
    conn = _get_pool().getconn()
    if dict_cursor:
        conn.cursor_factory = RealDictCursor
    return conn


def release(conn) -> None:
    """归还连接到池。连接异常时关闭后归还，避免脏连接复用。"""
    pool = _get_pool()
    try:
        pool.putconn(conn)
    except Exception:
        # 连接已损坏，直接关闭并丢弃，防止脏连接回到池中
        try:
            conn.close()
        except Exception:
            pass
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass


@contextmanager
def db_cursor(dict_cursor: bool = False):
    """
    统一游标上下文：自动 acquire / release，finally 保证归还连接与关闭游标。
    用法：with db_cursor(dict_cursor=True) as (conn, cur): cur.execute(...)

    若调用方仅需游标执行 SQL 并读取结果，推荐直接使用本管理器；
    若需在同一个连接上执行多个操作或控制事务，可改用 acquire/release 自行管理。
    """
    conn = acquire(dict_cursor=dict_cursor)
    cur = conn.cursor()
    try:
        yield conn, cur
    finally:
        try:
            cur.close()
        except Exception:
            pass
        release(conn)


@contextmanager
def db_conn(dict_cursor: bool = False):
    """
    连接级上下文：yield conn，调用方自行创建/关闭游标，适合一个连接内多次操作的场景。
    finally 保证归还连接。
    """
    conn = acquire(dict_cursor=dict_cursor)
    try:
        yield conn
    finally:
        release(conn)


def closeall() -> None:
    """关闭并清空连接池（用于应用 shutdown）。"""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.info("数据库连接池已关闭并清空")
