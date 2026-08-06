# 通达信 MACD / KDJ（Chart Subpane）计算方式调研

调研日期：2026-07-29  
范围：副图指标「MACD」「KDJ」默认参数与系统公式复刻；用于 Chart Subpane，不进 Indicator Engine（见 ADR 0003）。

## 结论摘要

1. **MACD 默认参数**：`(SHORT, LONG, MID) = (12, 26, 9)`。
2. **DIF** = `EMA(CLOSE, 12) − EMA(CLOSE, 26)`。
3. **DEA** = `EMA(DIF, 9)`。
4. **MACD 柱** = `(DIF − DEA) × 2`（通达信放大；西方教材常不乘 2）。
5. **KDJ 默认参数**：`(N, M1, M2) = (9, 3, 3)`。
6. **RSV** = `(CLOSE − LLV(LOW, N)) / (HHV(HIGH, N) − LLV(LOW, N)) × 100`；分母为 0 时 RSV 取 0。
7. **K** = `SMA(RSV, M1, 1)`，**D** = `SMA(K, M2, 1)`，**J** = `3×K − 2×D`。
8. **EMA / SMA** 均为通达信定义（见下），不是 pandas `ewm` / 简单均线。
9. **置信度**：系统内置公式多为加密；上表为通达信公式编辑器与社区一致的公开复刻。需同股同日对表验收；本仓库 golden test 按该复刻逐步手算锁定。

## 通达信 EMA / SMA

```text
EMA(X, N) = (2·X + (N−1)·EMA') / (N+1)
  首根：EMA = X

SMA(X, N, M) = (M·X + (N−M)·SMA') / N
  首根：SMA = X
```

## 复刻公式（系统默认）

```text
DIF:  EMA(CLOSE,12) - EMA(CLOSE,26);
DEA:  EMA(DIF,9);
MACD: (DIF-DEA)*2, COLORSTICK;

RSV:= (CLOSE-LLV(LOW,9))/(HHV(HIGH,9)-LLV(LOW,9))*100;
K:    SMA(RSV,3,1);
D:    SMA(K,3,1);
J:    3*K-2*D;
```

## 与 Market Pipeline 的边界

- 在 **完整暖机序列**（与 bars 同源的 250 日历日）上计算后，再切展示窗；禁止只在展示窗上现算 EMA（种子错误）。
- 不调用 `calculate_indicators`、不写 `technical_indicators`。
- 仅由 `/api/stocks/{symbol}/bars` 序列化带出 `dif/dea/macd/k/d/j`。

## 开放风险

1. 停牌平滑：bars 路径经 `calculate_indicators` 的停牌 ffill 后再算副图，与通达信对停牌日的处理可能仍有细微差别。
2. 复权：本仓库 bars 为前复权 close；须与通达信同一复权设置对表。
