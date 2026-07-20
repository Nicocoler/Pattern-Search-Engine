# PSE 开发指南

Version: 1.1  
Date: 2026-07-18

## 1. 推荐技术栈

后端：
- Python 3.11+
- FastAPI、Pydantic v2
- Pandas / Polars、NumPy、SciPy
- scikit-learn
- PyTorch (后续用于向量模型)
- **高性能 DTW 加速**：`dtaidistance` (底层 C 实现) 或 `Numba` (JIT 编译器)

数据：
- PostgreSQL 15+
- **时序存储**：TimescaleDB (首选，转 daily_bars、indicators、features 为超表)
- Redis (热点查询、任务进度、缓存)

任务调度：
- APScheduler 或 Celery

前端：
- React、TypeScript
- **图表渲染**：ECharts / Lightweight Charts

## 2. 推荐项目结构

```text
pse/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── data_center/
│   │   ├── indicators/
│   │   ├── features/
│   │   ├── events/
│   │   ├── templates/
│   │   ├── similarity/
│   │   ├── ranking/
│   │   ├── backtest/       # 形态回测系统
│   │   ├── reports/
│   │   └── workers/
│   ├── tests/
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   └── package.json
├── docs/
├── docker-compose.yml
└── README.md
```

## 3. 核心开发原则

1. **行情唯一隔离**：Data Center 是唯一行情入口，其它模块绝不能直接写 SQL 读写行情表或直连外部 API。
2. **除权自动重算**：每日同步必须比对复权系数差分。若个股发生除权，写入 `dirty_factors` 表，触发后台异步 Worker 自动拉取全新 QFQ 日 K 并重算该股全历史指标与特征，杜绝数据断层。
3. **消除量纲冲突**：送入多维 DTW 匹配前的所有特征子序列，必须在其 60 日滑动窗口内独立进行 **局部 Z-score 标准化**。
4. **性能剪枝加速**：DTW 匹配前必须经过流动性及形态初筛两级过滤（剪枝 90% 冗余）；DTW 计算必须采用 **Sakoe-Chiba 限宽对角线约束**（对角线带宽 $W = floor(0.15 \times \text{window\_size})$），将复杂度压低到 $O(N)$。
5. **高斯模糊事件**：摒弃硬编码判定，所有事件采用**单峰高斯分布置信度公式**输出平滑连续的概率值（0~1.0），增强形态对震荡市场的鲁棒性。
6. **在线反馈自学习**：第一阶段无需重训 Ranker 模型，接收好/坏反馈后直接通过 L1 归一化自适应梯度迭代算法，在线自适应微调当前形态模板的特征维度权重。
7. **防未来函数审计**：相似度扫描及回测引擎必须经过集成测试隔离审计，在任意历史时刻 $t$，绝对无法触及 $t$ 日之后的行情和衍生数据。

## 4. 数据流

```text
18:30 哨兵哨兵验证就绪
  -> 同步行情 (ak.stock_zh_a_spot_em + ak.stock_zh_a_hist)
  -> 检查行情除权差分 -> [发生除权] 触发异步历史重算任务
  -> 计算指标 (MA, BOLL, ATR, 均量等)
  -> 生成特征矩阵 (LNF 归一化修正主创差异, 一字板异常插值)
  -> 识别事件链 (输出高斯模糊置信度与证据 evidence)
  -> 加入候选预过滤 (硬过滤 + 特征初筛)
  -> Z-score 标准化特征子序列
  -> 多维加权限宽 DTW 计算相似度
  -> 自适应排序与风险罚分扣除
  -> 最终结果入库 & 解释性文本生成
  -> [人工标注反馈] -> 触发模板权重自适应 L1 更新
```

---

## 5. 特征插件规范

每个特征插件继承 `FeaturePlugin` 接口，负责生成一类特征。

```python
class FeaturePlugin:
    name: str
    version: str

    def calculate(self, bars: DataFrame, indicators: DataFrame) -> DataFrame:
        """
        返回包含新特征列的 DataFrame
        """
        ...
```

* **TrendFeaturePlugin**：MA5/MA10/MA20 排列，均线角度，近 N 日累计涨幅。
* **BollFeaturePlugin**：价格到布林中/上/下轨的相对距离百分比，布林宽度及宽度变化率。
* **VolumeFeaturePlugin**：成交量相对于 20 日均量的倍率（`volume_ratio_20`），缩量/放量趋势。
* **CandleFeaturePlugin**：实体占振幅比，上下影线占比，K线收盘在振幅内相对位置（支持一字板自适应插值修正）。
* **VolatilityFeaturePlugin**：ATR 波动比例，近20日高点回撤百分比。

---

## 6. 事件识别规范

事件检测器返回包含时间段、置信度（连续概率值）及原始物理证据（evidence）的列表。

```python
class PatternEvent:
    event_type: str
    start_date: date
    end_date: date
    confidence: float  # 由高斯正态分布公式计算，0.0 ~ 1.0 之间
    evidence: dict     # 物理测量值、最优期望值、偏离容忍标准差

class EventDetector:
    event_type: str
    version: str

    def detect(self, feature_window: DataFrame) -> list[PatternEvent]:
        ...
```

---

## 7. 相似度与排序计算规范

### 7.1 两级过滤（剪枝）
* **硬过滤**：直接在数据库中过滤掉当前停牌、ST股、日成交额低于 1000 万的低流动性僵尸股、及上市未满 60 日的新股。
* **初筛**：快速核验个股最近 15 天内是否发生过 `TOUCH_BOLL_MIDDLE` (回踩中轨) 标志或其最小中轨距离在容限内，不符合者直接剪枝，使送入 DTW 的运算量降低 90%。

### 7.2 量纲消除与限宽对角线 DTW
* **Z-score 转换**：多维序列在进行距离比对前，必须在当前 60 日滑动窗口内独立对每个特征维度计算 Z-score 归一化。
* **Sakoe-Chiba 约束**：DTW 转移路径必须限制在对角线带宽 $W$（例如：$W = floor(0.15 \times 60) = 9$）之内。
* **多维距离权重**：多维对齐距离计算：
  $$D(i, j) = \sqrt{\sum_{f=1}^{F} w_f \cdot \left( T'_{i, f} - C'_{j, f} \right)^2}$$

### 7.3 在线反馈权重自更新
* 当接收到正反馈（good_match）时，根据偏离中值，微幅上调高分特征分项的权重。
* 当接收到负反馈（bad_match）时，微幅下调对应特征分项的权重。
* 调整后对特征权重向量进行 **L1 归一化**（保持权重和始终为 1.0）。

---

## 8. 验收标准与开发迭代建议

请严格执行 TDD（测试驱动开发）：
1. 每一层实现（ indicators, features, events, similarity）必须编写独立的单元测试，通过 `tests/fixtures/` 内包含停牌、除权和一字板的 A 股假数据。
2. 集成测试中必须使用 **未来函数切片审计组件** 拦截任何回测穿透异常。
3. 严格参照 **`15_测试验收与开发路线图.md`** 中的 7 大交付要点，不妥协代码健壮性。

---

## 📝 修订日志 (Revision History)

| 版本号 | 修订日期 | 修订人 | 修订内容简述 |
| :--- | :--- | :--- | :--- |
| **v1.1** | 2026-07-18 | Gemini CLI / 助手 | 更新核心技术栈推荐。补全大表分区、除权自动重算、局部 Z-score 标准化、限宽 DTW 剪枝及高斯模糊事件判定的基本开发原则。更新整体同步与算法配合流程图大纲，维持全手册一致性。 |