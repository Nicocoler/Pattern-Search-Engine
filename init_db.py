#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 数据库自动化初始化工具
功能：自适应连接 stock_datas 数据库，并根据是否存在 TimescaleDB 插件智能配置分区超表
"""

import os
import sys
import psycopg2
from psycopg2 import sql

def get_db_connection():
    """
    自适应获取数据库连接
    1. 优先尝试从命令行参数读取
    2. 其次尝试从环境变量或 .env 读取
    3. 最后如果处于交互式终端则引导输入，否则自动采用默认参数连接
    """
    import argparse
    parser = argparse.ArgumentParser(description="PSE Database Initializer")
    parser.add_argument("--host", default=None, help="Database host")
    parser.add_argument("--port", default=None, help="Database port")
    parser.add_argument("--user", default=None, help="Database user")
    parser.add_argument("--password", default=None, help="Database password")
    # 解析已知参数，忽略未知参数避免在其它调用时报错
    args, _ = parser.parse_known_args()

    # 尝试加载 python-dotenv 
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # 优先采用命令行参数
    host = args.host
    port = args.port
    user = args.user
    password = args.password

    # 其次采用环境变量
    db_url = os.getenv("DATABASE_URL")
    if db_url and not any([host, port, user, password]):
        print(f"检测到环境变量 DATABASE_URL，正在尝试连接...")
        try:
            return psycopg2.connect(db_url)
        except Exception as e:
            print(f"通过环境变量 DATABASE_URL 连接失败: {e}")
            print("将转为默认/参数模式...")

    # 如果没有通过参数指定，且处于交互式终端，则进行询问
    is_interactive = sys.stdin.isatty()
    database = "stock_datas" # 朔哥哥指定库名

    if is_interactive and not any([host, port, user, password]):
        print("\n" + "="*50)
        print("        PSE 数据库自适应配置向导 (stock_datas)       ")
        print("="*50)
        print("请确认您的本地 PostgreSQL 服务已启动。直接回车将使用默认值。")
        host = input("🔹 主机地址 (默认: localhost): ").strip() or "localhost"
        port = input("🔹 端口号 (默认: 5432): ").strip() or "5432"
        user = input("🔹 用户名 (默认: postgres): ").strip() or "postgres"
        password = input("🔹 密  码 (若为空直接回车): ").strip()
    # 非交互终端或已通过参数指定，采用指定值或默认值
        host = host or os.getenv("DB_HOST") or "localhost"
        port = port or os.getenv("DB_PORT") or "5432"
        user = user or os.getenv("DB_USER") or "postgres"
        password = password or os.getenv("DB_PASSWORD") or ""
        print(f"非交互模式运行：将使用连接配置 {user}@{host}:{port}/{database}")

    # 【神级全自动建库防护层】
    # 在真正连接 stock_datas 库前，先通过 postgres 默认库，全自动在本地创建 stock_datas
    try:
        temp_conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database="postgres"
        )
        temp_conn.autocommit = True
        temp_cursor = temp_conn.cursor()
        try:
            # 动态执行创建库命令
            temp_cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            print(f"🎉【全自动】成功在本地为您创建了形态选股专属数据库 [{database}]！")
        except psycopg2.errors.DuplicateDatabase:
            # 已存在，静默通过
            pass
        finally:
            temp_cursor.close()
            temp_conn.close()
    except Exception as e:
        # 如果无法连接到默认库或无权建库，静默通过，交由后续的真实连接来报错
        pass

    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        # 成功连接后，将该连接串保存至本地 .env 文件，方便后续 Data Center 等模块直接免密读取
        env_content = f"DATABASE_URL=postgresql://{user}:{password}@{host}:{port}/{database}\n"
        with open(".env", "w", encoding="utf-8") as f:
            f.write(env_content)
        print(f"\n✅ 成功连接数据库 [{database}]！连接串已自动写入本地缓存 `.env`。")
        return conn
    except Exception as e:
        print(f"\n❌ 连接失败！请检查：\n1. 您的本地 PostgreSQL 服务是否已启动？\n2. 数据库 [{database}] 是否已经手动创建？")
        print(f"具体配置: host={host}, port={port}, user={user}")
        print(f"具体错误信息: {e}")
        sys.exit(1)

def run_init():
    conn = get_db_connection()
    conn.autocommit = False # 使用事务控制
    cursor = conn.cursor()

    has_timescale = False
    print("\n" + "-"*40)
    print("👉 步骤 1: 检测 TimescaleDB 扩展插件支持情况...")
    print("-"*40)

    try:
        # 尝试创建 timescaledb 扩展
        cursor.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
        conn.commit()
        has_timescale = True
        print("🚀 【特大喜讯】TimescaleDB 插件启用成功！时序海量数据存储已激活。")
    except Exception as e:
        conn.rollback()
        has_timescale = False
        print("⚠️  【降级提示】当前数据库暂未启用 TimescaleDB 扩展插件。")
        print("   系统将自动降级为原生 PostgreSQL 卓越索引方案。这不影响您的功能使用！")

    print("\n" + "-"*40)
    print("👉 步骤 2: 读取并执行 SQL 核心建表脚本...")
    print("-"*40)

    sql_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "init_db.sql")
    if not os.path.exists(sql_file_path):
        print(f"❌ 错误: 未能在当前目录下找到 {sql_file_path} 文件！")
        sys.exit(1)

    try:
        with open(sql_file_path, "r", encoding="utf-8") as f:
            sql_script = f.read()

        cursor.execute(sql_script)
        conn.commit()
        print("✅ 核心关系表及索引创建成功！")
        print("   - 股票信息表 `stocks` (已就绪)")
        print("   - 时序行情表 `daily_bars` (已就绪)")
        print("   - 除权差分任务表 `dirty_factors` (已就绪)")
        print("   - 模板配置表 `feature_templates` (已就绪)")
        print("   - 相似推荐结果表 `scan_results` (已就绪)")
        print("   - 回测报告表 `backtest_reports` (已就绪)")
    except Exception as e:
        conn.rollback()
        print(f"❌ 执行 SQL 建表脚本失败: {e}")
        sys.exit(1)

    print("\n" + "-"*40)
    print("👉 步骤 3: 处理时序大表分区优化...")
    print("-"*40)

    if has_timescale:
        try:
            # 执行 timescaledb 超表转换
            cursor.execute("SELECT create_hypertable('daily_bars', 'date', if_not_exists => TRUE);")
            conn.commit()
            print("🔥 【TimescaleDB】已成功将 daily_bars 时序行情表转化为超表（Hypertable）分区！")
        except Exception as e:
            conn.rollback()
            print(f"⚠️ 无法将行情表转化为超表分区（可能由于数据残留），但普通时序表已就绪: {e}")
    else:
        print("📊 【原生模式】由于不包含 TimescaleDB，daily_bars 已自动建立高检索索引以确保流畅性。")

    cursor.close()
    conn.close()
    
    print("\n" + "="*50)
    print("🎉 恭喜朔哥哥！PSE 数据库初始化全部顺利完成！ 🎉")
    print("="*50 + "\n")

if __name__ == "__main__":
    run_init()
