# 回测引擎（backtest_engine.py）

## 概述

回测引擎是 stdio JSON-RPC 子进程（`bridge/backtest_engine.py`），主进程通过 `main/datasources/backtest-engine.js` spawn 并通信。请求 `backtest.run`，返回 `{metrics, trades, equityCurve, monthlyReturns, skippedSignals, ...}`。

## 数据获取（v0.1.16 起统一走 datafeed）

- **单一数据源**：`fetch_real_bars` 内部调用 `bridge/data/datafeed.py` 的 `fetch_bars()`，口径（volume=股、front 前复权、代码转换）全部由 datafeed 统一保证。**严禁在引擎内直接调 xtquant**
- 优先级：SQLite 缓存（区间完整时）-> xtquant 实时拉取（拉到回写缓存）-> mock 回落（UI 显著标注 `dataSource: "mock"`）
- 周线/月线：先取日线再聚合（`_aggregate_weekly/_aggregate_monthly`）

## 撮合规则（v0.1.16 商业化升级）

| 规则 | 行为 | 参数 |
|---|---|---|
| T+1 | 当日买入次日才可卖，跳过的信号记入 `skippedSignals` | `enableT1`（默认 true） |
| 涨跌停 | 收盘涨停禁买、跌停禁卖（按板块：主板10%/创业科创20%/北交30%） | 自动判定 |
| tick 取整 | 成交价按 0.01 取整 | 自动 |
| 最小佣金 | 单笔佣金不足 5 元按 5 元 | 自动 |
| 成交量约束 | 买入量 ≤ 当日成交量 × 比例 | `maxVolumeRatio`（默认 0.25） |
| 整手 | 100 股整数倍 | 自动 |
| 成本 | 佣金 + 印花税（卖出）+ 过户费（双向）+ 滑点 | `commission/stampTax/transferFee/slippage` |

被跳过的信号（涨跌停/T+1/资金不足）记录在结果 `skippedSignals`（最多 100 条），UI 展示「被跳过信号」计数。

## 绩效指标（metrics）

`totalReturn / benchmarkReturn / excessReturn / annualReturn / maxDrawdown / sharpeRatio / sortinoRatio / calmarRatio / annualVolatility / avgHoldDays / winRate / profitLossRatio / totalTrades / skippedSignals / finalCapital`

另有：
- `monthlyReturns`：月度收益序列 `[{month: "YYYY-MM", return: %}]`（热力图数据）
- `inSampleWarning`：回测区间与模型训练区间（config 的 `data_date_range`）重叠时的警告文案；不重叠为 null
- `backtestId`：本次回测的持久化 ID

## 结果持久化

每次回测在 `data/backtest_results/bt_*.json` 落盘配置快照（策略/参数/区间/成本假设/metrics），供历史 A/B 对比与结果溯源。

## 样本内回测检测

回测时若 `strategy.params.model_id` 存在，读取 `data/models/{id}/config.json` 的 `data_date_range`（训练数据起止日期），与回测区间比对，重叠则结果带 `inSampleWarning`，UI 顶部红条警告「结果会显著偏乐观，仅用于调试」。
