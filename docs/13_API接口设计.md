# PSE API 接口设计

Version: 1.1  
Date: 2026-07-18

## 1. 通用规范

基础路径：
```text
/api
```

成功响应：
```json
{
  "success": true,
  "data": {},
  "error": null
}
```

错误响应：
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "template_id is required"
  }
}
```

---

## 2. 模板接口

### 2.1 创建模板
* **路径**：`POST /api/templates`
* **请求**：
```json
{
  "name": "布林回踩中轨二次启动",
  "template_type": "abstract",
  "window_size": 60,
  "event_schema": [
    {"event_type": "TREND_UP", "required": true, "tolerance_sigma": 0.20},
    {"event_type": "TOUCH_BOLL_MIDDLE", "required": true, "tolerance_sigma": 0.015}
  ],
  "scoring_weights": {
    "trend_score": 0.25,
    "boll_score": 0.25,
    "volume_score": 0.15,
    "candle_score": 0.15,
    "event_sequence_score": 0.15,
    "volatility_score": 0.05
  },
  "hard_filters": {
    "min_amount_20d": 10000000,
    "allow_st": false
  }
}
```

### 2.2 查询模板列表
* **路径**：`GET /api/templates?status=active`

### 2.3 查询模板详情
* **路径**：`GET /api/templates/{template_id}`

### 2.4 更新模板 (新建递增版本)
* **路径**：`PUT /api/templates/{template_id}`

### 2.5 归档模板
* **路径**：`DELETE /api/templates/{template_id}`

---

## 3. 扫描接口

### 3.1 创建扫描任务
* **路径**：`POST /api/search-runs`
* **请求**：
```json
{
  "template_id": "uuid",
  "run_date": "2026-07-18",
  "market_scope": "A_SHARE_ALL",
  "top_n": 50
}
```
* **响应**：
```json
{
  "run_id": "uuid",
  "status": "pending"
}
```

### 3.2 查询扫描任务状态
* **路径**：`GET /api/search-runs/{run_id}`

### 3.3 查询扫描排序结果列表
* **路径**：`GET /api/search-runs/{run_id}/results?limit=50&offset=0`

---

## 4. 股票与多维对比接口

```text
GET /api/stocks
GET /api/stocks/{symbol}
GET /api/stocks/{symbol}/bars?start=2026-01-01&end=2026-07-18
GET /api/stocks/{symbol}/events?start=2026-01-01&end=2026-07-18
```

### 4.1 核心同屏比对接口
* **路径**：`GET /api/compare/template/{template_id}/stock/{symbol}?end_date=2026-07-18`
* **响应**：直接输出包含基准折算所需的多维时序特征（供前端进行百分比归零对齐绘制），以及事件在 K 线上渲染的气泡标注：
```json
{
  "template_symbol": "600519",
  "candidate_symbol": "000002",
  "window_size": 60,
  "similarity_scores": {
    "total_score": 84.3,
    "breakdown": {
      "trend_score": 82.0,
      "boll_score": 90.0,
      "volume_score": 76.0
    }
  },
  "alignment_path": [[0,0], [1,1], [2,2], [3,4]],
  "matched_events": [
    {
      "event_type": "TOUCH_BOLL_MIDDLE",
      "date": "2026-07-02",
      "confidence": 0.88,
      "evidence": {"min_boll_middle_distance": 0.007}
    }
  ],
  "explanation_facts": {
    "positive_facts": ["价格精准踩线中轨，距离仅为 0.75%"],
    "negative_facts": ["启动日成交量放量一般"]
  }
}
```

---

## 5. 形态回测接口 (全新增设！💥)

提供与第 16 章《形态回测系统设计.md》无缝对接的 API 契约：

### 5.1 创建形态历史回测任务
* **路径**：`POST /api/backtests`
* **请求**：
```json
{
  "template_id": "uuid",
  "start_date": "2021-01-01",
  "end_date": "2025-12-31",
  "score_threshold": 80.0,
  "holding_periods": [5, 10, 20],
  "benchmark": "000300.SH",
  "max_portfolio_size": 10
}
```
* **响应**：
```json
{
  "backtest_id": "uuid",
  "status": "pending" -- pending, running, success, failed
}
```

### 5.2 获取回测任务状态与报告
* **路径**：`GET /api/backtests/{backtest_id}`
* **响应**：
```json
{
  "backtest_id": "uuid",
  "status": "success",
  "summary": {
    "total_signals": 184,
    "winning_rate_10d": 68.5,
    "avg_return_10d": 5.82,
    "avg_alpha_10d": 3.44,
    "max_drawdown": -12.4
  },
  "equity_curve": [
    {"trade_date": "2021-01-04", "portfolio_value": 1.0, "benchmark_value": 1.0},
    {"trade_date": "2021-01-05", "portfolio_value": 1.015, "benchmark_value": 1.002}
  ]
}
```

### 5.3 查询回测买卖成交信号明细
* **路径**：`GET /api/backtests/{backtest_id}/trades?limit=50&offset=0`

---

## 6. 反馈与任务接口

### 6.1 提交人工标注反馈
* **路径**：`POST /api/feedback`
* **请求**：
```json
{
  "result_id": "uuid",
  "label": "good_match", -- good_match, bad_match, watchlist, ignore
  "comment": "缩量结构踩线中轨很像"
}
```

### 6.2 定时任务强制触发接口 (生产环境需鉴权)
```text
POST /api/jobs/sync-market-data
POST /api/jobs/calculate-indicators
POST /api/jobs/build-features
POST /api/jobs/detect-events
```

## 7. 验收标准

- 所有核心业务及形态回测具备完整的 REST API 契约和 OpenAPI 文档。
- 数据模型请求和响应基于 Pydantic 强校验。
- 扫描和回测任务作为异步任务在后台队列（APScheduler/Celery）托管，接口提供轮询状态机字段。

---

## 📝 修订日志 (Revision History)

| 版本号 | 修订日期 | 修订人 | 修订内容简述 |
| :--- | :--- | :--- | :--- |
| **v1.1** | 2026-07-18 | Gemini CLI / 助手 | **全新增设 5. 形态回测接口契约定义**（含发起回测、查询状态/累计净值、获取单笔成交明细）。细化对比接口中返回前段百分比对齐所需的时序序列及气泡可视化事件，保持全局一致。 |