# Pattern Search Engine（PSE）系统设计方案

Version: 1.0  
Date: 2026-07-18

## 1. 项目定位

Pattern Search Engine（PSE）是一个基于 K 线形态相似搜索的智能选股系统。它解决的不是“明天涨不涨”，而是“当前市场里哪些股票的走势最像某个用户认可的历史模板”。

传统条件选股依赖固定公式，例如“收盘价大于 MA20 且成交量放大”。这类规则表达能力有限，难以描述人眼看盘时更在意的阶段关系：先上涨，触碰布林上轨，随后缩量回踩中轨，中轨支撑成立，再次放量启动。

PSE 把这类盘感拆解为可计算结构：

- 多维特征矩阵：每天不仅有价格，还有布林位置、均线角度、成交量节奏、K 线实体、上下影线、波动率等。
- 事件序列：把连续 K 线翻译成“上涨、触上轨、回调、回踩中轨、止跌、放量启动”等事件。
- 时间弹性匹配：允许模板上涨 10 天，候选股票上涨 15 天，只要阶段结构一致仍可匹配。
- 可解释排序：输出相似度分数时，同时说明为什么相似、哪里不相似。

## 2. 产品目标

### 2.1 核心目标

建立一个每日收盘后自动运行的 A 股形态搜索系统。

输入：

- 一个历史股票走势模板。
- 或一个抽象形态模板，例如“布林上轨 -> 缩量回踩中轨 -> 二次启动”。

输出：

- 今日最符合模板的股票列表。
- 综合相似度评分。
- 趋势、布林、成交量、K 线结构、事件序列等分项评分。
- 当前处于模板哪个阶段。
- 相似原因和主要风险。

### 2.2 非目标

- 不预测明日涨跌。
- 不生成自动交易指令。
- 不替代人工风控。
- 不以单一 DTW 算法作为全部判断。

## 3. 核心用户场景

### 3.1 用历史股票作为模板

用户选择一段历史走势，例如某只股票启动前 60 个交易日。系统提取该窗口的特征矩阵和事件序列，在全市场寻找当前最相似的股票。

适合场景：

- 用户已经有明确的成功案例。
- 想找“现在谁最像当初的它”。

### 3.2 用抽象形态作为模板

用户定义事件链示例，支持拓展：

```text
上涨
  -> 触碰或贴近布林上轨
  -> 缩量回调
  -> 回踩布林中轨
  -> 中轨向上且支撑成立
  -> 止跌 K 线出现
  -> 放量二次启动
```

适合场景：

- 用户有看盘逻辑，但不想绑定某只股票。
- 后续扩展杯柄、平台突破、圆弧底、强势股回踩等模板。

## 4. 系统总体架构

PSE 分为九个核心模块：

```text
用户 / AI Agent
    ↓
Web Dashboard / API
    ↓
模板管理系统
    ↓
核心分析引擎
    ↓
数据中心 -> 指标计算 -> 特征工程 -> 事件识别 -> 相似度搜索 -> AI 排序
    ↓
结果解释与报告
    ↓
人工反馈学习
```

### 4.1 Data Center 数据中心

职责：

- 获取 A 股股票基础信息。
- 获取日 K、周 K、月 K。
- 管理复权、停牌、缺失值、异常行情。
- 为上层模块提供统一数据接口。

原则：

- 其它模块不能直接访问外部行情源。
- 其它模块不能绕过 Data Center 直接读写行情表。

#### 4.1.1 A 股特殊交易机制与异常容错

为确保系统能稳健运行在真实的 A 股复杂交易环境中，本系统专门针对 A 股交易制度设计了以下异常容错与匹配修正机制：

1. **主创板块涨跌幅限制归一化（Limit Normalization Factor, LNF）**：
   * **背景**：A 股不同板块涨跌幅限制差异大（主板 ±10%，创业板/科创板 ±20%，北交所 ±30%）。若直接计算趋势或单日涨幅特征，高波幅板块天然会扭曲形态得分。
   * **公式设计**：引入涨跌幅限制归一化系数：
     $$LNF = \frac{10.0}{LimitRatio}$$
     其中主板 $LimitRatio = 10$，创业板 $LimitRatio = 20$。
   * **处理**：所有单日价格波幅、振幅、ATR 以及波动率特征，在计入特征矩阵前均乘以 $LNF$，统一折算为“主板等效波动率”，消除板块制度差异对形态相似度的干扰。
2. **涨跌停“一字板”形态畸变插值（One-Line Limit Board Interpolation）**：
   * **背景**：一字板时，最高价 = 最低价 = 收盘价 = 开盘价，当日振幅为 0，且成交量极度萎缩。这会导致常规 K 线实体占比、上下影线特征、成交量倍率等指标发生异常突变，扭曲 DTW 的形态特征匹配。
   * **处理**：特征工程中如果识别到“一字板”（振幅 $BodyRatio = 0$ 且当日处于涨跌停阈值内），启动自适应插值：
     * 其实体、影线等结构特征直接继承上一交易日的有效值。
     * 波动率及均线角度采用前一交易日有效值，但成交量倍率（相对于20日均量）直接归零。
     * 在特征矩阵中附加标记 `is_limit_one_line: 1`。相似度搜索引擎在识别到该标记时，对 DTW 距离计算进行弹性权重豁免，避免其由于缩量而导致匹配失真。
3. **收盘数据晚间同步网关与自动容错（Market Close Synchronization Gateway）**：
   * **背景**：A 股 15:00 收盘，但 AkShare 等数据源通常在 15:30 - 18:00 之间逐步清洗当天行情。15:10 直接拉取会大面积落空或缺失。
   * **处理**：系统定时任务设定在晚间 18:30 启动，并通过“哨兵验证机制”保护系统：
     * 定时触发后，Data Center 首先查询全市场 3 个极具代表性、高流动性的标的（例如：上证指数 `000001`、万科A `000002`、贵州茅台 `600519`）的当天日 K 行情。
     * 若 3 个哨兵股票的当天收盘价均已非空且不为 0，视为全市场行情源清洗完毕，启动同步。
     * 若哨兵股票未更新，执行指数衰减重试（Exponential Backoff）：延迟 15 分钟、30 分钟、60 分钟重试，最大重试 5 次。

### 4.2 Indicator Engine 指标计算引擎

计算：

- MA5、MA10、MA20、MA60、MA120。
- BOLL 中轨、上轨、下轨、带宽、开口变化。
- MACD、RSI、ATR。
- 成交量倍率、20 日均量、缩量/放量状态。

### 4.3 Feature Engineering 特征工程

把每天行情转换为特征向量，把连续窗口转换为特征矩阵。

单日特征示例：

```json
{
  "close_norm": 1.037,
  "boll_upper_distance": -0.018,
  "boll_middle_distance": 0.042,
  "boll_width": 0.126,
  "boll_middle_slope": 0.006,
  "ma20_angle": 8.5,
  "volume_ratio_20": 0.72,
  "atr_ratio": 0.031,
  "body_ratio": 0.44,
  "upper_shadow_ratio": 0.12,
  "lower_shadow_ratio": 0.28
}
```

连续 60 天形成：

```text
60 x N Feature Matrix
```

### 4.4 Event Engine 事件识别引擎

把连续特征转换为事件：

- TREND_UP：趋势上涨。
- TOUCH_BOLL_UPPER：触碰或贴近布林上轨。
- PULLBACK：进入回调。
- TOUCH_BOLL_MIDDLE：回踩布林中轨。
- BOLL_MIDDLE_SUPPORT：中轨支撑成立。
- VOLUME_SHRINK：回调缩量。
- STOP_FALLING_CANDLE：止跌 K 线。
- VOLUME_BREAKOUT：放量二次启动。

事件识别结果保留时间范围、置信度和证据。

### 4.5 Template Engine 模板管理系统

模板有两类：

- Historical Template：来自一段历史股票走势。
- Abstract Template：来自用户定义的事件序列和权重。

模板应支持插件化，新增形态时只添加模板配置和事件规则，不修改核心搜索框架。

### 4.6 Similarity Engine 相似度搜索引擎

使用多算法融合：

- DTW：解决时间长度不同、阶段节奏不同。
- Shape Similarity：比较归一化价格形状。
- Feature Similarity：比较多维特征矩阵。
- Event Sequence Similarity：比较事件顺序、阶段完整性和事件置信度。
- Rule Gate：用于剔除明显不符合硬条件的候选。

### 4.7 Ranking Engine AI 排序系统

第一阶段使用可解释加权评分。

后续升级：

- 使用人工反馈训练 Learning-to-Rank 模型。
- 使用深度学习把窗口映射成向量 embedding。
- 建立向量索引，实现更快的大规模相似搜索。

### 4.8 Web Dashboard

核心页面：

- 每日扫描结果。
- 模板管理.
- 股票详情与模板对比图。
- 分项评分解释。
- 人工反馈标注。

### 4.9 Scheduler 自动任务

每日收盘后执行：

```text
同步行情
  -> 指标计算
  -> 特征生成
  -> 事件识别
  -> 全市场模板扫描
  -> 结果入库
  -> 报告生成
```

## 5. 数据库设计

推荐数据库：

- PostgreSQL：业务主库。
- TimescaleDB：K 线和指标等时序数据。
- Redis：缓存任务状态、热点查询和临时结果。

### 5.1 核心表

#### stocks

股票基础信息。

```sql
create table stocks (
  stock_id bigserial primary key,
  symbol varchar(16) not null unique,
  name varchar(64) not null,
  exchange varchar(16) not null,
  list_date date,
  status varchar(16) not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

#### daily_bars

日 K 行情。

```sql
create table daily_bars (
  symbol varchar(16) not null,
  trade_date date not null,
  open numeric(18,4) not null,
  high numeric(18,4) not null,
  low numeric(18,4) not null,
  close numeric(18,4) not null,
  pre_close numeric(18,4),
  volume numeric(20,2),
  amount numeric(20,2),
  turnover_rate numeric(10,4),
  adj_factor numeric(18,8),
  created_at timestamptz not null default now(),
  primary key (symbol, trade_date)
);
```

#### technical_indicators

技术指标结果。

```sql
create table technical_indicators (
  symbol varchar(16) not null,
  trade_date date not null,
  ma5 numeric(18,4),
  ma10 numeric(18,4),
  ma20 numeric(18,4),
  ma60 numeric(18,4),
  ma120 numeric(18,4),
  boll_mid numeric(18,4),
  boll_upper numeric(18,4),
  boll_lower numeric(18,4),
  boll_width numeric(18,6),
  macd numeric(18,6),
  rsi14 numeric(18,6),
  atr14 numeric(18,6),
  volume_ratio_20 numeric(18,6),
  primary key (symbol, trade_date)
);
```

#### feature_vectors

单日特征向量。

```sql
create table feature_vectors (
  symbol varchar(16) not null,
  trade_date date not null,
  version varchar(32) not null,
  features jsonb not null,
  created_at timestamptz not null default now(),
  primary key (symbol, trade_date, version)
);
```

#### pattern_templates

形态模板。

```sql
create table pattern_templates (
  template_id uuid primary key,
  name varchar(128) not null,
  template_type varchar(32) not null,
  description text,
  source_symbol varchar(16),
  start_date date,
  end_date date,
  event_schema jsonb not null,
  scoring_weights jsonb not null,
  status varchar(16) not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
```

#### pattern_events

事件识别结果。

```sql
create table pattern_events (
  event_id uuid primary key,
  symbol varchar(16) not null,
  event_type varchar(64) not null,
  start_date date not null,
  end_date date not null,
  confidence numeric(8,6) not null,
  evidence jsonb not null,
  feature_version varchar(32) not null,
  created_at timestamptz not null default now()
);
```

#### search_runs

扫描任务。

```sql
create table search_runs (
  run_id uuid primary key,
  template_id uuid not null references pattern_templates(template_id),
  run_date date not null,
  market_scope varchar(64) not null,
  status varchar(32) not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  config jsonb not null,
  error_message text
);
```

#### search_results

搜索结果。

```sql
create table search_results (
  result_id uuid primary key,
  run_id uuid not null references search_runs(run_id),
  symbol varchar(16) not null,
  window_start date not null,
  window_end date not null,
  total_score numeric(8,4) not null,
  rank_no integer not null,
  score_breakdown jsonb not null,
  matched_events jsonb not null,
  explanation text,
  risk_notes text,
  created_at timestamptz not null default now()
);
```

#### user_feedback

人工反馈。

```sql
create table user_feedback (
  feedback_id uuid primary key,
  result_id uuid not null references search_results(result_id),
  user_id varchar(64),
  label varchar(32) not null,
  comment text,
  created_at timestamptz not null default now()
);
```

## 6. API 设计

### 6.1 模板接口

```text
POST   /api/templates
GET    /api/templates
GET    /api/templates/{template_id}
PUT    /api/templates/{template_id}
DELETE /api/templates/{template_id}
```

### 6.2 扫描接口

```text
POST /api/search-runs
GET  /api/search-runs
GET  /api/search-runs/{run_id}
GET  /api/search-runs/{run_id}/results
```

### 6.3 股票与对比接口

```text
GET /api/stocks
GET /api/stocks/{symbol}/bars
GET /api/stocks/{symbol}/features
GET /api/compare/template/{template_id}/stock/{symbol}
```

### 6.4 反馈接口

```text
POST /api/feedback
GET  /api/feedback/stats
```

## 7. 评分模型

第一阶段建议使用可解释加权模型：

```text
total_score =
  0.25 * trend_score
+ 0.25 * boll_score
+ 0.15 * volume_score
+ 0.15 * candle_score
+ 0.15 * event_sequence_score
+ 0.05 * volatility_score
```

默认权重可按模板覆盖。

## 8. MVP 范围

MVP 只做一个核心模板：

```text
上涨 -> 触布林上轨 -> 缩量回踩中轨 -> 中轨支撑 -> 放量二次启动
```

MVP 验收标准：

- 能导入或拉取全市场日 K。
- 能计算基础指标。
- 能生成 60 日特征矩阵。
- 能识别核心事件。
- 能每天跑出 Top 50 候选。
- 每个候选有评分拆解和文字解释。

## 9. 后续扩展

- 多模板库。
- 用户自定义权重。
- 向量 embedding 相似搜索。
- 回测模块。
- 多周期共振：日 K、周 K、月 K。
- 行业和概念过滤。
- 与通达信、同花顺或本地 CSV 数据联动。

---

## 📝 修订日志 (Revision History)

| 版本号 | 修订日期 | 修订人 | 修订内容简述 |
| :--- | :--- | :--- | :--- |
| **v1.1** | 2026-07-18 | Gemini CLI / 助手 | 补充了A股涨跌幅限制归一化系数(LNF)计算、一字板形态畸变插值逻辑、数据晚间同步哨兵验证机制。并在各子系统推进此规范设计。 |
