# 开发方案 - 侧滑弹窗增加 BOLL 主图 K 线烛台指标图

## 🎯 需求 Objective
根据朔哥哥提供的专业 A 股看盘截图事实，在右侧侧滑抽屉面板（`slide-over-drawer`）中，升级并新增一套 **“带有 BOLL(20, 2) 经典三轨主图指标的红绿日 K 线烛台走势图” (BollKlineChart)**。布林线的算法必须严格与当前后端的特征工程计算方法（20 日收盘均价为中轨，2 倍标准差为上、下轨）100% 对齐。

---

## 🏗️ 视觉交互与排版设计 (Visual & Interaction Design)

### 1. 抽屉内“双视角图表”Tabs 选项卡
为了不破坏原先百分比对齐图（KlineCompareChart）对形态神似度的终极诊断：
我们将抽屉内部的图表区，改写为 **“双视角图表选项卡（Chart View Tabs）”**，提供秒级切换：
*   **[ 🧩 异时空形态百分比归一对比图 ]**：原有的百分比对齐走势。
*   **[ 🕯️ 真实日K线 + BOLL三轨通道图 ]** `[NEW!]`：展示真实的物理价格烛台和布林运行轨道。

### 2. K 线与布林轨道图表技术规格对齐
基于 `ReactECharts`，对齐同花顺/通达信专业看盘指标的视觉习惯：
*   **红绿阳阴烛台 (Candlestick)**：
    *   收盘 > 开盘（阳线）：采用经典 A 股亮红色（`#ef4444`），设置 `itemStyle.color: '#ef4444', itemStyle.borderColor: '#ef4444'`。
    *   收盘 < 开盘（阴线）：采用经典 A 股晶莹绿色（`#10b981`），设置 `itemStyle.color: '#10b981', itemStyle.borderColor: '#10b981'`。
    *   上下影线颜色与烛身严格保持一致。
*   **BOLL(20, 2) 经典三轨线 (Lines)**：
    *   **BOLL-M 中轨线 (20日均线)**：白色线 (`#e2e8f0`)，线宽 `1.5`，不带 symbol。
    *   **UB 上轨线 (中轨 + 2倍标准差)**：黄色线 (`#f59e0b`)，线宽 `1.5`。
    *   **LB 下轨线 (中轨 - 2倍标准差)**：粉紫色线 (`#d946ef`)，线宽 `1.5`。
*   **主图最新指标参数数显 (BOLL Text Header)**：
    在图表的 Title 区域或者上方文字区，实时渲染出该股 60 天内最后一天的布林价格值：
    `BOLL(20) MID: 9.47 UB: 11.13 LB: 7.80`（字体颜色与三轨线一一对应，彰显专业度）。
*   **最高/最低绝对价格标注 (Extreme Price Markers)**：
    在 Candlestick 烛台 series 的 `markPoint` 属性中，利用 ECharts 自动提取 `max`（最高价）和 `min`（最低价），在其上方/下方拉出指向横线，回显如 `10.17` 价格文字，完美对齐截图事实。

---

## 🛠️ Implementation Steps (开发实施步骤)

### Step 1: 后端比对对齐接口数据扩充 (main.py)
在 `backend/app/main.py` 的 `/api/compare/template/{id}/stock/{symbol}` 接口中：
*   目前，我们在返回的 `cand_bars` 中只提取了 `open`, `high`, `low`, `close`, `volume`。
*   **重构**：追加提取 `boll_mid` (中轨), `boll_upper` (上轨), `boll_lower` (下轨) 这三列绝对价格数值，直接打入 `cand_bars` 并发送给前端！
*   *优点*：无需前端重复写 JS 计算指标，100% 杜绝了浮点误差，保证前后端指标绝对一致。

### Step 2: 前端 App.tsx 实现 BollKlineChart 选项生成器
*   新增一个 Tab 视角状态：`const [chartView, setChartView] = useState<'compare' | 'boll_kline'>('compare');`
*   在 `handleSelectStockForCompare` 切换股票时，默认重置 `chartView` 状态为 `'compare'`。
*   在 `App.tsx` 内部，编写 `getBollKlineOption()` 函数，生成具有高保真 K 线烛台、最高最低点 markPoint、BOLL 三轨折线、暗黑色背景格、最新指标 Title 提示的 ECharts 完整配置。

### Step 3: 前端抽屉布局改写与 HMR 渲染
*   在抽屉 Body 内部，在比对图上方插入两个极简、精美的 Chart view Tabs：
    `<div className="chart-view-tabs">...</div>`
*   当选中 `'boll_kline'` 时，原位渲染 `option={getBollKlineOption()}`。

---

## 🧪 Verification & Testing (测试验收方案)

1.  **打包校验**：在 `frontend` 运行 `npm run build`，确保加入全新的 ECharts 烛台和三轨数据绑定后，TypeScript 编译 **0 error** 通过。
2.  **真机指标校验**：点击任意个股，切换到 [BOLL三轨通道图]，比对图表顶端显示的 MID、UB、LB 绝对数值，核对是否与您电脑同花顺软件上的前复权日 K 数据完全一致。
3.  **最高/最低价测试**：确认图表上自动拉出的最高、最低价格标签指向正确。
