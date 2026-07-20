# PSE 数据中心（Data Center）设计

Version: 1.0  
Date: 2026-07-18

## 1. 模块定位

Data Center 是系统唯一行情入口。任何指标、特征、事件、相似度模块都不允许直接访问外部行情源。

核心职责：

- 统一接入行情源。
- 清洗、校验、复权和落库。
- 提供稳定查询接口。
- 屏蔽不同数据源的字段差异。

## 2. 支持数据范围

第一阶段：

- A 股股票基础信息。
- 日 K。
- 成交量、成交额、换手率。
- 复权因子。

第二阶段：

- 周 K、月 K。
- 行业、概念、指数。
- 涨跌停、停复牌。
- 龙虎榜、资金流。

## 3. 数据源适配器 (AkShare Adapter)

本系统首选并默认使用开源行情库 **AkShare**。为保证接口稳定性，对 AkShare 进行统一包装，定义以下适配接口和机制。

### 3.1 抽象适配器接口

```python
class MarketDataProvider:
    name: str = "AkShare"

    def fetch_all_stock_info(self) -> list[StockInfo]:
        """
        获取全市场 A 股股票基本信息列表
        映射接口：ak.stock_zh_a_spot_em()
        """
        ...

    def fetch_daily_bars(
        self, 
        symbol: str, 
        start: date, 
        end: date, 
        adjust: str = "qfq"
    ) -> list[DailyBar]:
        """
        获取单只股票历史日 K 数据（前复权价格）
        映射接口：ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust=adjust)
        """
        ...
```

### 3.2 AkShare 限流与反爬设计（Rate Limiting & Safety）

由于 A 股上市公司超过 5300 家，每日全量同步时若直接进行高并发请求，极易触发行情源封锁 IP。
* **并发控制**：在 `AkShareProvider` 内部必须使用 `asyncio.Semaphore(5)`，将最大网络并发限制在 5 线程/协程。
* **随机延迟**：每次拉取完一只股票，执行一个 `random.uniform(0.1, 0.3)` 秒的随机微休眠，防止被行情源风控。
* **重试避让**：如果请求报错，等待时间按指数递增：`backoff = 2 ** retry_count`，最大重试 3 次。

## 4. 复权策略与历史重算触发机制

### 4.1 复权机制

系统默认使用 **前复权 (QFQ)** 数据进行相似度比较，以便形态连续、不产生价格断裂缺口。

### 4.2 复权重算触发（Re-adjustment Trigger）

A 股的除权除息（送股、分红等）会导致**前复权历史价格在除权日当天全部发生向下平移突变**。若不重算历史特征，之前入库的指标和特征将发生断层。
* **差分比对表 `dirty_factors`**：
  * Data Center 在每日同步时，会查询个股当前的复权系数（可由 `ak.stock_zh_a_hist` 返回的数据间接核算，或者记录前一天的 close 价格作为基准）。
  * 比较当天获取的昨日 `pre_close` 与数据库中记录的昨日 `close`：
    $$| \text{pre\_close}_{today} - \text{close}_{yesterday} | > 0.01$$
    若满足上述条件，说明该个股在今天发生了除权除息。
  * **处理流程**：
    1. 将该个股 symbol 和触发日期写入 `dirty_factors` 重算任务表。
    2. 触发后台异步 Worker 任务。
    3. 重新拉取该个股近 3 年的日 K 行情（AkShare 强制前复权，拿到的就是全新价格）。
    4. 对 `daily_bars` 表进行 `upsert` 覆盖更新。
    5. 清空并重新计算该个股在 `technical_indicators`、`feature_vectors` 和 `pattern_events` 表中的历史数据。

## 5. 同步流程与哨兵保护网关

### 5.1 哨兵网关 (Sentinel Verification Gateway)

A 股 15:00 收盘，但 AkShare 后台通常在 15:30 以后开始清洗数据，大部分高精行情直至 17:30 - 18:00 才能完全沉淀。
* **规则**：每日定时任务在 **18:30** 启动。
* **哨兵流程**：
  1. 任务触发后，Data Center 首先请求 `sh000001` (上证指数)、`sz000002` (万科A)、`sh600519` (贵州茅台) 3 个代表性标的的当日 K 线。
  2. 检查返回数据中的 `trade_date` 是否为今天，且收盘价大于 0。
  3. 若 3 个哨兵均验证通过，判定当天行情已就绪，全市场启动同步。
  4. 若哨兵未通过，等待 15 分钟重试，最大重试 5 次（直到 20:00 仍失败则发出钉钉/企业微信报警）。

### 5.2 整体同步逻辑

```text
18:30 定时任务启动
  -> 哨兵网关比对（3只核心标的当天日K是否已就绪）
  -> [验证失败] 延迟15分钟重试 (Backoff)
  -> [验证通过] 批量获取 A 股最新基本面 (ak.stock_zh_a_spot_em)
  -> 更新 stocks 状态 (新增、退市、停牌)
  -> 分批（限流并发 5）增量拉取个股日 K 
  -> 行情除权差分核验 (QFQ 比对)
  -> [发生除权] 写入重算队列 -> 全量重算该 symbol 历史指标特征
  -> [正常同步] 仅 upsert 当日 daily_bars
  -> 写入同步质量日志与耗时报告
```

## 6. 缺失值处理

* **停牌处理**：停牌交易日不补齐 K 线。在 A 股市场，停牌日无成交、无价格变动，强行插值会导致形态失真。相似度引擎在匹配 60 日窗口时，若窗口内包含停牌日（通过 `quality_flags.suspended_days` 记录），可根据配置允许有 3 日以内的停牌豁免。
* **缺失校验**：非停牌日若数据源由于网络故障产生缺失，不进行均值填充，直接标记 `data_quality: "incomplete"`。

## 7. 内部查询接口

```python
class DataCenter:
    def get_stocks(self, scope: MarketScope) -> list[StockInfo]:
        """获取全市场有效股票清单"""
        ...

    def get_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        adjusted: bool = True,
    ) -> DataFrame:
        """
        获取前复权日 K 线，直接返回 Pandas DataFrame。
        只允许内部调用，外部指标特征层严禁绕过此接口。
        """
        ...

    def trigger_historical_recalculation(self, symbol: str):
        """触发该股的历史除权全量重算任务"""
        ...
```

## 8. 数据质量报告

每日同步后生成：
* 整体同步状态：成功/失败。
* 同步股票数量：如 5350 只。
* 成功更新数量、发生复权重算数量。
* 出现异常或网络延迟的 Symbol 列表。
* AkShare API 响应稳健度与重试记录。

## 9. 性能要求

* 5300 只股票最近 2 年日 K 查询在 10 秒内完成。
* 每日行情增量同步（无复权发生时）在 15 分钟内完成（含网络限流延迟）。
* 复权重算：单只股票 3 年历史数据全流程重算及指标、特征重新生成在 3 秒内完成。

## 10. 错误处理

若由于三方 AkShare API 接口结构改版、网络封锁导致读取彻底失败，数据中心必须：
1. 立即中断全市场更新，确保**历史行情数据的只读安全性，绝不覆盖已有旧数据**。
2. 记录错误日志，并向运维人员发送即时告警。
3. 自动回滚当天有损的部分更新。

## 11. 验收标准

- 能同步股票基础信息。
- 能同步或导入日 K。
- 能按 symbol 和日期范围稳定查询。
- 缺失、停牌、异常数据有明确标记。
- 上层模块只依赖 Data Center 接口。

---

## 📝 修订日志 (Revision History)

| 版本号 | 修订日期 | 修订人 | 修订内容简述 |
| :--- | :--- | :--- | :--- |
| **v1.1** | 2026-07-18 | Gemini CLI / 助手 | 将数据中心完整适配 AkShare 行情源（映射 spot_em 和 hist 接口）。补充并发限流与随机延迟。引入 `dirty_factors` 差分核算表与历史复权重算触发机制。新增 18:30 定时任务哨兵保护网关。数据库安装C:\Users\Nico\.gemini\tmp\pse\memory\DATABASE_SETUP_AND_GUIDES.md |