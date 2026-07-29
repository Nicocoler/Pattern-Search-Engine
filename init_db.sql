-- =============================================================================
-- Pattern Search Engine (PSE) 数据库初始化脚本
-- 支持：TimescaleDB 超表时序数据库 / 原生 PostgreSQL 索引无缝降级
-- 数据库名：stock_datas
-- =============================================================================

-- 1. 尝试安装 TimescaleDB 扩展（如果不可用，后续 Python 驱动会捕获异常并降级为原生 PostgreSQL）
-- CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- 2. 股票基本面信息表
CREATE TABLE IF NOT EXISTS stocks (
    code VARCHAR(12) PRIMARY KEY,                       -- 股票代码（如: sh600519, sz000002）
    name VARCHAR(64) NOT NULL,                          -- 股票名称
    name_pinyin_abbr VARCHAR(64),                       -- 名称拼音首字母缩写（如: 思源电器→sydt，万科A→wka）
    list_date DATE,                                     -- 上市日期
    board VARCHAR(32),                                  -- 板块（主板/创业板/科创板/北交所）
    industry VARCHAR(64),                               -- 行业分类
    is_st BOOLEAN DEFAULT FALSE,                        -- 是否为ST/*ST股票
    is_suspended BOOLEAN DEFAULT FALSE,                 -- 是否处于停牌状态
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()   -- 信息更新时间
);

-- 创建索引以加速基础筛选
CREATE INDEX IF NOT EXISTS idx_stocks_board_st ON stocks(board, is_st) WHERE is_suspended = FALSE;
CREATE INDEX IF NOT EXISTS idx_stocks_name_pinyin_abbr ON stocks(name_pinyin_abbr);

-- 3. 时序日K行情表（如果是 TimescaleDB，后续将被转化为分区超表）
CREATE TABLE IF NOT EXISTS daily_bars (
    code VARCHAR(12) NOT NULL,                          -- 股票代码（外键关联 stocks）
    date DATE NOT NULL,                                 -- 交易日期
    open NUMERIC(10, 4) NOT NULL,                       -- 开盘价（前复权）
    high NUMERIC(10, 4) NOT NULL,                       -- 最高价（前复权）
    low NUMERIC(10, 4) NOT NULL,                        -- 最低价（前复权）
    close NUMERIC(10, 4) NOT NULL,                      -- 收盘价（前复权）
    volume BIGINT NOT NULL,                             -- 成交量（股）
    amount NUMERIC(20, 4) NOT NULL,                     -- 成交额（元）
    factor NUMERIC(16, 6) NOT NULL,                     -- 复权因子（当日的前复权乘数系数）
    PRIMARY KEY (code, date)                            -- 联合主键（在 TimescaleDB 中超表必须包含时间列作为主键之一）
);

-- 4. 脏因子重算缓冲池表（捕获除权除息差分）
CREATE TABLE IF NOT EXISTS dirty_factors (
    code VARCHAR(12) NOT NULL,                          -- 发生除权除息的股票代码
    dirty_date DATE NOT NULL,                           -- 触发重算的时戳日期
    is_processed BOOLEAN DEFAULT FALSE,                 -- 是否已重算处理完毕
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),  -- 记录写入时间
    PRIMARY KEY (code, dirty_date)
);

-- 5. 形态/抽象模板配置表
CREATE TABLE IF NOT EXISTS feature_templates (
    id SERIAL PRIMARY KEY,                              -- 模板ID
    name VARCHAR(128) NOT NULL UNIQUE,                  -- 模板名称（如: "经典布林回踩启动"）
    type VARCHAR(32) NOT NULL,                          -- 模板类型: historical (历史股票切片) / abstract (抽象规则配置)
    config JSONB NOT NULL,                              -- 具体的事件序列与筛选特征配置 Json
    weights JSONB NOT NULL,                             -- 多维特征维度权重 Json（进行 L1 归一化自适应调整）
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 6. 形态每日推荐落库表（用于前端研盘快速查询）
CREATE TABLE IF NOT EXISTS scan_results (
    id BIGSERIAL PRIMARY KEY,
    date DATE NOT NULL,                                 -- 扫描推荐结果的对应交易日期
    template_id INT NOT NULL,                           -- 对应的模板 ID
    code VARCHAR(12) NOT NULL,                          -- 匹配的股票代码
    similarity_score NUMERIC(6, 4) NOT NULL,            -- 匹配综合相似度评分 (0.0 ~ 1.0)
    sub_scores JSONB NOT NULL,                          -- 各项指标与事件切片的分项评分 Json
    explanation TEXT,                                   -- AI/系统可解释性对齐说明文本
    risk_tips TEXT,                                     -- 该形态对应的潜在风险提示
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 为推荐结果表建立高性能联合检索索引
CREATE INDEX IF NOT EXISTS idx_scan_results_date_score ON scan_results(date, template_id, similarity_score DESC);

-- 7. 历史形态滚动回测报告表
CREATE TABLE IF NOT EXISTS backtest_reports (
    id SERIAL PRIMARY KEY,
    template_id INT NOT NULL,                           -- 回测的模板 ID
    start_date DATE NOT NULL,                           -- 回测的起止历史时间
    end_date DATE NOT NULL,
    metrics JSONB NOT NULL,                             -- 汇总绩效指标 Json (胜率, 信号数, Alpha, 最大回撤等)
    equity_curve JSONB NOT NULL,                        -- 持股组合累计净值与 benchmark 对比时序 Json
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 8. 后台行情同步状态 KV 表（守护进程与 FastAPI 后台任务共用，幂等建表）
CREATE TABLE IF NOT EXISTS data_sync_status (
    key VARCHAR(32) PRIMARY KEY,                       -- 状态键（last_start_time / last_status / last_mode 等）
    value TEXT,                                         -- 状态值
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 9. 人工标注反馈记录表（API 端点内 CREATE TABLE IF NOT EXISTS 幂等兜底，此处一并声明）
CREATE TABLE IF NOT EXISTS user_feedback (
    id SERIAL PRIMARY KEY,
    result_id BIGINT NOT NULL,                          -- 关联的 scan_results.id（应用层保证，未声明外键）
    label VARCHAR(32) NOT NULL,                        -- 标注标签: good_match / bad_match / watchlist / ignore
    comment TEXT,                                       -- 人工备注
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 10. 布林编排逐日状态表（%B + Zone，与 DTW 特征链路隔离）
CREATE TABLE IF NOT EXISTS stock_state_daily (
    code VARCHAR(12) NOT NULL,                          -- 股票代码
    date DATE NOT NULL,                                 -- 交易日
    pct_b NUMERIC(12, 6),                               -- %B = (close-lower)/(upper-lower)，带宽为0时为 NULL
    zone VARCHAR(2) NOT NULL,                           -- 离散状态 L/M/H/U/NA
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    PRIMARY KEY (code, date)
);

CREATE INDEX IF NOT EXISTS idx_stock_state_daily_date ON stock_state_daily (date DESC);

-- 11. 布林编排命中结果表（自然键幂等 upsert）
CREATE TABLE IF NOT EXISTS pattern_match_result (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(12) NOT NULL,                          -- 股票代码
    pattern_id VARCHAR(64) NOT NULL,                    -- 编排 YAML id
    pattern_name VARCHAR(128) NOT NULL,                 -- 编排可读名称
    start_date DATE NOT NULL,                           -- 匹配区间起
    end_date DATE NOT NULL,                             -- 匹配区间止
    matched_states TEXT NOT NULL,                       -- 命中的状态字符串（人工复核）
    score NUMERIC(10, 4),                               -- 二次打分（第一期预留，可为 NULL）
    scan_date DATE NOT NULL,                            -- 最近一次写入/更新的扫描日
    window_days INT NOT NULL,                           -- 本次扫描使用的窗口长度
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (code, pattern_id, start_date, end_date)
);

CREATE INDEX IF NOT EXISTS idx_pattern_match_scan_date ON pattern_match_result (scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_pattern_match_end_pattern ON pattern_match_result (end_date DESC, pattern_id);

-- 12. 布林编排全局尺子（单行）
CREATE TABLE IF NOT EXISTS boll_pattern_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    zone_thresholds JSONB NOT NULL,
    denoise_min_len INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 13. 布林编排定义表（权威源；YAML 仅补缺种子）
CREATE TABLE IF NOT EXISTS boll_patterns (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    regex TEXT NOT NULL,
    min_total_days INT NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    zone_thresholds JSONB,                              -- 稀疏覆盖，NULL=用全局
    denoise_min_len INT,                                -- 稀疏覆盖，NULL=用全局
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 如果是普通 PostgreSQL 降级环境，创建日K线的极致检索联合索引以加速 DTW 切片提取
CREATE INDEX IF NOT EXISTS idx_daily_bars_date_code ON daily_bars (date DESC, code);
