# PSE (Pattern Search Engine)

基于 K 线相对布林带位置演变的形态搜索与研究辅助系统。DTW 相似搜索与布林 zone 编排扫描是两条并行能力。

## Language

**Template（模板）**:
DTW 相似搜索使用的 `feature_templates` 配置（权重、required_events、回测均挂在此上）。
_Avoid_: 用「模板」指正则 zone 编排

**Pattern / 编排**:
基于某一 **Bar Period** 上逐根 Zone 序列、用正则描述的走势编排。每条编排绑定且仅绑定一个周期（日 / 周 / 月）；不同周期是不同编排，不可混扫。`period` 在创建时选定，**之后不可修改**（改周期应复制新建）。存量编排迁移为 `daily`。权威源为库表 `boll_patterns`；`boll_patterns.yaml` 仅作出厂种子（补缺不覆盖）。
_Avoid_: 形态模板、feature template、Template；一条编排跨日/周/月复用；原地修改 period 并保留旧命中

**Bar Period / K 线周期**:
编排与看盘共用的 K 线粒度：日 / 周 / 月。周/月由日 K 按自然周（含未完成周）或自然月聚合；一根 Bar 的日期标签为该段最后交易日。
_Avoid_: 用「交易日」笼统指周 K/月 K 的一根；把看盘周期与编排周期当成两套无关枚举

**Pattern Settings / 全局尺子**:
编排共用的默认分区阈值与 denoise；存于 `boll_pattern_settings`。单条编排可稀疏覆盖。

**Zone**:
由 %B 按某套分区阈值离散得到的**单根 Bar**位置状态：`L` / `M` / `H` / `U`（异常为 `NA`）。所论 Bar 属于该编排的 Bar Period（日编排=日 K，周编排=周 K，……）。匹配时按该编排 effective 尺子从 %B 现算。
_Avoid_: 把 Zone 说死成「仅单日」；在周/月序列上复用未说明周期的「日 zone」

**%B**:
收盘价在布林上轨与下轨之间的相对位置：`(close - lower) / (upper - lower)`。轨与收盘均取自当前 Bar Period 的 K 序列。

**Market Pipeline / 行情准备门面**:
统一的「加载 + compare 同款 250 日历日暖机 + 分层计算」入口。
点截止日用 `prepare_stock_frame`；区间预载（回测）用 `prepare_stock_history`（start 前再暖机 250）。
看盘/多周期聚合另见 `prepare_chart_bars` / `aggregate_ohlcv`。
`level` 为 `indicators` / `features` / `pattern`（后者含 pct_b、不含 zone）。
取数底层为 `load_daily_bars` / `load_stock_bars`，sentry/backtest 不再各自拼 SQL 算指标。
_Avoid_: 各模块自行选择短于 250 的计算回看来算布林

**Beijing Time**:
业务时刻一律按 `Asia/Shanghai`：连接池 `SET TIME ZONE`，Python 用 `now_beijing()` / `isoformat_beijing()`。
_Avoid_: 无时区的 `datetime.now().isoformat()` 写入状态表；依赖客户端默认 UTC 展示

**Pattern Match（编排命中）**:
某只股票在某一编排之 Bar Period 上、某起止 Bar 区间对该 Pattern 的一次匹配结果；自然键需区分周期（与编排绑定的 period 一致）。
_Avoid_: scan_results（那是 DTW 模板扫描落库）；跨周期复用同一条命中

**Pattern Scan（编排扫描任务）**:
一次全市场（或池内）编排匹配作业；**每次只跑一个 Bar Period**，窗口根数属于该周期；只匹配 `period` 等于该任务周期的启用编排。同步成功后的自动扫描仅跑**日线**；周/月由用户手动按周期触发。试跑使用**当前编辑编排的 period**，不落库。
_Avoid_: 一次任务混扫日/周/月并用同一个「60」当窗口；同步后默认同跑三轮周期

**End-within（近端结束过滤）**:
命中列表过滤器：只保留结束 Bar 落在「距扫描截止向前 N **根同周期 Bar**」内的命中（进行中/刚完成）。日/周/月各自按该周期根数计，不是统一日历日。
_Avoid_: 对周/月命中仍用「近 3 个日历日」导致列表被滤空

**Pattern Edge（转移边条件）**:
编排上可选的硬过滤：在命中区间内要求原始 Zone 序列出现相邻 `from→to`，且 Arrival **Bar** 满足 `when`（v1 仅日线 `limit_up`）。**仅日线编排可配置 edges**；周/月编排禁止边条件。配置为 `edges` 数组；空=不启用。
_Avoid_: 把边条件编进 zone 字母表；把边条件当成打分加权；在周/月上沿用未重定义的日线涨停语义

**Edge Hit（边命中证据）**:
一次 Pattern Match 上满足某条边条件的 Arrival 日记录（`from/to/when/date`），存于 `pattern_match_result.edge_hits`。

**Scan Result**:
DTW 模板全市场扫描写入 `scan_results` 的相似度推荐记录。
_Avoid_: 与 Pattern Match 混称

**Chart Subpane（图表副图）**:
主图之下仅供看盘的 MACD、KDJ 等序列展示；公式权威在后端纯函数并对齐通达信，但不进入 Market Pipeline 的 `indicators` 层，也不写入 `technical_indicators`。
_Avoid_: 把副图叫成 Indicator / 指标层；把副图公式并进脏因子重算链路；在前端另造一套数值真相
