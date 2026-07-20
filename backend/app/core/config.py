# -*- coding: utf-8 -*-
"""
Pattern Search Engine (PSE) - 核心配置管理模块
"""

import os
import urllib.request

# 【终极神级猴子补丁：物理屏蔽 Windows 注册表与 requests 系统代理】
# 彻底杜绝 requests/urllib3 绕过环境变量偷读系统代理导致的 ProxyError
try:
    import requests
    import requests.utils
    import requests.sessions
    
    # 彻底擦除 requests 模块内两个核心位置的代理获取函数
    requests.utils.get_environ_proxies = lambda url, no_proxy=None: {}
    requests.sessions.get_environ_proxies = lambda url, no_proxy=None: {}
    
    requests.Session.trust_env = False  # 强行关闭 Session 自动信任环境代理的行为，确保绝对直接连接！
except ImportError:
    pass

urllib.request.getproxies = lambda: {}

# 彻底拦截由于本地抓包或梯子环境变量引起的 ProxyError 冲突
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
    
    # 数据库连接配置
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://postgres:admin123!@127.0.0.1:5432/stock_datas"
    )
    
    # 行情获取相关并发配置
    MAX_CONCURRENT_REQUESTS: int = 5     # 最大并发拉取线程数（防爬封锁限制）
    REQUEST_RETRY_LIMIT: int = 1         # 单只个股请求失败重试上限
    REQUEST_BACKOFF_FACTOR: float = 1.0  # 指数退避倍率
    
settings = Settings()
