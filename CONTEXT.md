# PSE (Pattern Search Engine)

基于 K 线相对布林带位置演变的形态搜索与研究辅助系统。DTW 相似搜索与布林 zone 编排扫描是两条并行能力。

## Language

**Template（模板）**:
DTW 相似搜索使用的 `feature_templates` 配置（权重、required_events、回测均挂在此上）。
_Avoid_: 用「模板」指正则 zone 编排

**Pattern / 编排**:
基于逐日 Zone 序列、用正则描述的走势编排。权威源为库表 `boll_patterns`；`boll_patterns.yaml` 仅作出厂种子（补缺不覆盖）。
_Avoid_: 形态模板、feature template、Template

**Pattern Settings / 全局尺子**:
编排共用的默认分区阈值与 denoise；存于 `boll_pattern_settings`。单条编排可稀疏覆盖。

**Zone**:
由 %B 按某套分区阈值离散得到的单日位置状态：`L` / `M` / `H` / `U`（异常为 `NA`）。匹配时按该编排 effective 尺子从 %B 现算。

**%B**:
收盘价在布林上轨与下轨之间的相对位置：`(close - lower) / (upper - lower)`。

**Market Pipeline / 行情准备门面**:
统一的「加载 + compare 同款 250 日历日暖机 + 分层计算」入口。
点截止日用 `prepare_stock_frame`；区间预载（回测）用 `prepare_stock_history`（start 前再暖机 250）。
`level` 为 `indicators` / `features` / `pattern`（后者含 pct_b、不含 zone）。
取数底层为 `load_daily_bars` / `load_stock_bars`，sentry/backtest 不再各自拼 SQL 算指标。
_Avoid_: 各模块自行选择短于 250 的计算回看来算布林

**Beijing Time**:
业务时刻一律按 `Asia/Shanghai`：连接池 `SET TIME ZONE`，Python 用 `now_beijing()` / `isoformat_beijing()`。
_Avoid_: 无时区的 `datetime.now().isoformat()` 写入状态表；依赖客户端默认 UTC 展示

**Pattern Match（编排命中）**:
某只股票在某起止交易日区间上对某一 Pattern 的一次匹配结果；自然键为 `(code, pattern_id, start_date, end_date)`。
_Avoid_: scan_results（那是 DTW 模板扫描落库）

**Scan Result**:
DTW 模板全市场扫描写入 `scan_results` 的相似度推荐记录。
_Avoid_: 与 Pattern Match 混称

**Chart Subpane（图表副图）**:
主图之下仅供看盘的 MACD、KDJ 等序列展示；公式权威在后端纯函数并对齐通达信，但不进入 Market Pipeline 的 `indicators` 层，也不写入 `technical_indicators`。
_Avoid_: 把副图叫成 Indicator / 指标层；把副图公式并进脏因子重算链路；在前端另造一套数值真相
