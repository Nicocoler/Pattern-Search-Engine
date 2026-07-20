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
    list_date DATE,                                     -- 上市日期
    board VARCHAR(32),                                  -- 板块（主板/创业板/科创板/北交所）
    industry VARCHAR(64),                               -- 行业分类
    is_st BOOLEAN DEFAULT FALSE,                        -- 是否为ST/*ST股票
    is_suspended BOOLEAN DEFAULT FALSE,                 -- 是否处于停牌状态
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()   -- 信息更新时间
);

-- 创建索引以加速基础筛选
CREATE INDEX IF NOT EXISTS idx_stocks_board_st ON stocks(board, is_st) WHERE is_suspended = FALSE;

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

-- 如果是普通 PostgreSQL 降级环境，创建日K线的极致检索联合索引以加速 DTW 切片提取
CREATE INDEX IF NOT EXISTS idx_daily_bars_date_code ON daily_bars (date DESC, code);
