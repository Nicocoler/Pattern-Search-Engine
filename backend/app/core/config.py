# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 核心配置管理模块
"""

import os
import urllib.request

# 代理屏蔽：本地抓取 akshare 行情时，requests/urllib 会读取系统代理（IE/注册表、
# 环境变量）导致 ProxyError。此处主动清空代理获取函数与环境变量，强制直连。
# 注意：这是环境特定的兼容补丁，保留以防本地代理/梯子干扰行情抓取。
try:
    import requests
    import requests.utils
    import requests.sessions

    # 清空 requests 的代理获取函数
    requests.utils.get_environ_proxies = lambda url, no_proxy=None: {}
    requests.sessions.get_environ_proxies = lambda url, no_proxy=None: {}

    requests.Session.trust_env = False  # 关闭 Session 自动信任环境代理，强制直连
except ImportError:
    pass

urllib.request.getproxies = lambda: {}

# 清空本地抓包或梯子相关的代理环境变量，避免 ProxyError 冲突
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("ALL_PROXY", None)
os.environ.pop("all_proxy", None)

from dotenv import load_dotenv

# 自动加载本地 .env 文件
load_dotenv()

class Settings:
    PROJECT_NAME: str = "Pattern Search Engine (PSE)"
    VERSION: str = "1.0.0"

    # 数据库连接配置：仅从环境变量/.env 读取，不再在源码中硬编码默认密码（凭据不进版本库）。
    # 缺失时由连接池层在首次使用时抛出清晰错误，避免阻断 init_db.py 的交互式引导建库流程。
    DATABASE_URL: str = os.getenv("DATABASE_URL")

    # 行情获取相关并发配置
    MAX_CONCURRENT_REQUESTS: int = 5     # 最大并发拉取线程数（防爬封锁限制）
    REQUEST_RETRY_LIMIT: int = 1         # 单只个股请求失败重试上限
    REQUEST_BACKOFF_FACTOR: float = 1.0  # 指数退避倍率

    # 形态剪枝与除权检测阈值（集中管理，避免散落魔法数字）
    BOLL_PRUNE_THRESHOLD: float = 0.045      # 候选窗口内收盘距布林中轨最小绝对偏离，大于此值直接剪枝
    DIRTY_FACTOR_PRICE_DIFF: float = 0.015   # 前复权收盘价自交叉比对差分阈值，超过视为发生除权
    FEEDBACK_DEFAULT_ETA: float = 0.05       # 反馈权重微调默认步长（可被前端 learning_rate 覆盖）

settings = Settings()
