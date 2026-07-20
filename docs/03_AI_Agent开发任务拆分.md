# PSE AI Agent 开发任务拆分

Version: 1.1  
Date: 2026-07-18

## 1. 协作原则

本项目适合由多个 AI Agent 或多个开发角色并行推进。所有 Agent 必须遵循：
- 先读 [01_系统设计方案(PRD+SAD).md](./01_系统设计方案(PRD+SAD).md)。
- 再读 [02_开发指南(Implementation Guide).md](./02_开发指南(Implementation%20Guide).md)。
- 每个 Agent 只改自己负责的模块，严格遵守物理和逻辑边界。
- 修改公共时序大表结构或 API 前，先更新迁移脚本、文档和单元测试。
- 所有运行结果必须满足 **可复现、高抗噪、可回测、无未来函数** 四大红线。

---

## 2. 各 Agent 职责划分

### 2.1 Data Agent (行情与时序清洗)
* **职责**：
  - 基于 **AkShare** 行情源（映射 spot_em 和 hist 接口），开发股票基础数据、前复权日 K 的批量/增量同步脚本。
  - 编写 $Semaphore(5)$ 并发限制、指数退避重试和 100ms~300ms 随机休眠反爬控制程序。
  - 实现每日 18:30 “哨兵网关保护验证”（校验 3 只代表股当日数据就绪后才触发全市场同步）。
  - 编写 `dirty_factors` 差分核算机制：识别今日与昨日除权突变，写入任务表并触发该股历史行情 `upsert` 覆写与指标/特征重算任务。
* **交付物**：Data Center 接口模块、增量同步与除权重算任务后台 Worker。

### 2.2 Indicator Agent (基础时序计算)
* **职责**：
  - 基于 Pandas 或 Polars 开发 MA5/10/20/60/120, BOLL, RSI14, ATR14, 均量等指标的向量化高性能批量计算。
  - 对停牌日数据不强行插值、对数据不足时输出 `null` 的边界测试。
* **交付物**：Indicator Engine 计算包、指标自动化测试组件。

### 2.3 Feature Agent (特征提取与 LNF 归一化)
* **职责**：
  - 开发 `FeaturePlugin` 接口，内置 Trend, Boll, Volume, Candle, Volatility 等特征插件。
  - 编写 **主创板块涨跌幅限制归一化系数 (LNF)** 乘数修正逻辑，拉平板块波幅失真。
  - 实现涨跌停一字板（振幅为0）的实体/影线特征自适应上交易日继承及 `is_limit_one_line` 质量标签插值逻辑。
* **交付物**：Feature Engine、五大内置特征生成插件。

### 2.4 Event Agent (高斯置信度事件识别)
* **职责**：
  - 开发 `EventDetector` 接口，定义高置信度事件链。
  - 废除 0/1 硬编码，将 8 个 MVP 核心事件重构为基于 **高斯单峰正态分布函数** 的柔性置信度平滑计算（输出 $0 \sim 1.0$ 的概率）。
* **交付物**：Event Engine 识别器、高斯模糊逻辑算法包。

### 2.5 Algorithm Agent (高性能 DTW 与自适应相似度)
* **职责**：
  - 实现多维特征子序列在 60 日滑动窗口内的 **局部 Z-score 标准化**。
  - 基于 `dtaidistance` (C底) 或 Numba 加速，实现带有 **Sakoe-Chiba 带宽限宽对角线约束（带宽 $W$）** 的多维加权 DTW 相似度计算。
  - 开发在线正负反馈自适应 L1 特征权重微调梯度迭代模块。
* **交付物**：Similarity Engine、自学习反馈微调模块。

### 2.6 Backtest Agent (形态无偏滚动回测) (全新增设！💥)
* **职责**：
  - 开发 **Backtest Engine（形态回测引擎）**，支持滚动仿真仿真及持股 5d, 10d, 20d 的统计。
  - 编写 **防未来函数切片审计拦截器**（在历史 $t$ 日时截断并销毁所有其后数据，拦截任何未来数据穿透）。
  - 统计形态在历史时间轴上的总信号数、胜率、期望收益、Alpha超额（减指数 benchmark 收益率）及盈亏比，生成净值曲线（Equity Curve）数据。
* **交付物**：Backtest Engine、未来函数自动化审计组件。

### 2.7 Backend Agent (API 契约与异步队列)
* **职责**：
  - 采用 FastAPI 封装模板、扫描任务、人工反馈及形态回测的 REST API。
  - 使用 APScheduler / Celery 调度晚间 18:30 扫描，并将耗时较长的扫描和回测任务转换为异步队列挂起运行。
* **交付物**：RESTful API 模块、后台异步任务调度器。

### 2.8 Frontend Agent (研盘工作台 UI)
* **职责**：
  - 编写 Web UI 盯盘工作台（列表、雷达图、极速反馈标注按组）。
  - 实现 `KlineCompareChart`：起始日归零折算的 **百分比归一化纵坐标同屏重叠对比图表**，并在对应 K 线天数上挂载高斯模糊 **可视化事件气泡标签 (Event Markers)** 交互。
  - 编写回测分析面板，同屏比对净值曲线与沪深 300 业绩走势。
* **交付物**：React 前端页面、百分比 K 线重叠拟合组件。

---

## 3. 推荐开发顺序

1. **Data Agent + PostgreSQL**：完成行情数据库表结构设计及 AkShare 网关开发（打通数据入口，实现除权重算）。
2. **Indicator + Feature + Event Agents**：编写指标、LNF 特征插件、及高斯模糊事件概率判定（构建形态的物理骨架）。
3. **Algorithm Agent**：打通多维 Z-score 及限宽 DTW 算法（实现最核心的对齐搜索与反馈自适应权重）。
4. **Backtest Agent**：编写回测引擎（利用防未来函数切片，对上述匹配算法进行历史无偏胜率检验）。
5. **Backend + Frontend Agents**：封包 API 和异步 Celery 任务，交付 Web 端研盘百分比同屏重合工作台。
6. **Test & Doc Agents**：全程编写 TDD 单元测试，补充 CI 与开发规范文档。

---

## 📝 修订日志 (Revision History)

| 版本号 | 修订日期 | 修订人 | 修订内容简述 |
| :--- | :--- | :--- | :--- |
| **v1.1** | 2026-07-18 | Gemini CLI / 助手 | **全新增设 Backtest Agent**。将除权差分、高斯模糊置信度、局部 Z-score 标准化、限宽对角线加速、自适应 SGD 微调和前端百分比重叠比对，科学合理地划定到 8 大开发 Agent 的职责边界与交付标准中。 |