# 回测引擎（backtest_engine.py）

## 概述

回测引擎是 stdio JSON-RPC 子进程（`bridge/backtest_engine.py`），主进程通过 `main/datasources/backtest-engine.js` spawn 并通信。请求 `backtest.run`，返回 `{metrics, trades, equityCurve, monthlyReturns, skippedSignals, ...}`。

## 回测进度推送（backtest.progress）

回测过程中引擎通过 **JSON-RPC notification（`id: null`）** 推送进度事件，让 UI 实时显示进度条：

- **协议**：`{"id": null, "method": "backtest.progress", "params": {"progress": 0-100, "stage": "fetch"|"signal"|"match"|"finish", "detail": "中文说明"}}`
- **阶段与进度区间**：fetch 取数（5%）→ signal 生成信号（10%~50%）→ match 撮合（50%~95%）→ finish 计算指标（96%）。信号与撮合阶段每 10 根 bar 推送一次（`emit_progress`，位于 `backtest_engine.py`）
- **转发链路**：`backtest-engine.js` 识别无 id 的 `backtest.progress` 消息 → `BacktestService.onProgress` 回调 → `main/ipc/index.js` 用 `webContents.send("backtest:progress")` 推到渲染进程 → `preload.js` 暴露 `facade.backtest.onProgress(cb)`（返回退订函数）→ `BacktestViewModel.subscribeProgress()/unsubscribeProgress()`（Router `onLeave("backtest")` 时退订）→ `backtest-view.js` 渲染进度条
- **UI**：回测配置卡下方显示 `.progress-bar-wrap` 进度条 + 阶段中文标签 + 明细（如「撮合交易 171 / 242」）；`state.running && state.progress` 时展示，完成后清空
- **mock 模式**：`facade.js` 的浏览器 backtest mock 也模拟推送进度，纯浏览器演示同样可见

## 数据获取（v0.1.16 起统一走 datafeed）

- **单一数据源**：`fetch_real_bars` 内部调用 `bridge/data/datafeed.py` 的 `fetch_bars()`，口径（volume=股、front_ratio 前复权比例版、代码转换）全部由 datafeed 统一保证。**严禁在引擎内直接调 xtquant**
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
| **撮合口径（D0.1）** | 信号与成交价的时序关系三档可选 | `fillModel`（默认 `next_open`） |
| **模拟数据阻断（D0.3）** | 真实数据不可用时默认拒绝回测，需显式允许 | `allowMock`（默认 false） |

被跳过的信号（涨跌停/T+1/资金不足）记录在结果 `skippedSignals`（最多 100 条），UI 展示「被跳过信号」计数。

### 撮合口径 fillModel（D0.1，v0.1.17）

| 值 | 语义 | 说明 |
|---|---|---|
| `close` | 信号与本根 bar 收盘价同时成交 | 乐观口径：收盘看到信号即以收盘价成交（原行为） |
| `next_open`（默认） | 上一根 bar 收盘生成的信号在**下一根 bar 开盘**成交 | 保守口径：真实世界只能次日开盘买入，更贴近实盘 |
| `tick` | 盘中 tick 撮合 | 需分钟级数据，尚未实现（传入报错提示） |

- 撮合循环按 `exec_price`（close→收盘价 / next_open→开盘价）执行，T+1/涨跌停判定也基于执行价与前一 bar 收盘。
- 回测结果回显 `fillModel`，快照持久化含 `fillModel`；UI 配置区可选，默认 `next_open`。
- **为何默认 next_open**：当前市场"收盘信号收盘成交"无法真实实现，属乐观偏差；保守口径让回测结果对实盘更有预测力。

### 模拟数据阻断（D0.3，v0.1.17）

- 真实数据不可用（xtquant 未连接/无数据）时，**默认拒绝回测**并报错提示连接 miniQMT 或显式勾选「允许使用模拟数据」。
- `allowMock=True` 时才回落 mock 随机游走数据，结果页强制红条 + 快照 `allowMock: true` 留痕。
- 训练路径本就不回落 mock（防模拟数据污染训练集），行为一致。

### 缓存口径校验（D0.4，v0.1.17）

- `db.py` 新增 `kline_meta` 表，为每个 (code, period, dividend_type) 记录 `spec_version`（与 `datafeed.DATA_SPEC_VERSION` / `db.CACHE_SPEC_VERSION` 同步，改口径必须 +1）。
- 查询缓存时版本不匹配（或旧缓存无版本记录）→ **忽略缓存并删除旧行**，触发重新拉取（自愈式）。
- 防止旧口径（如 volume 手/股、复权方式）的脏缓存污染回测/训练结果。

## 统一风控层（D2，v0.1.18）

`bridge/risk_control.py` 的 `RiskController` 是**回测与实盘共用的统一风控**（纯逻辑、无副作用，由撮合循环/执行器驱动）。参数默认全部关闭/全仓，保持既有行为兼容；开启后对**所有策略自动生效**。

### D2.1 退出风控

| 参数 | 默认 | 语义 |
|---|---|---|
| `stopLoss` | 0（关） | 持仓中当日 `low` 触及 成本×(1-比例) → 按**触发价**强制卖出 |
| `takeProfit` | 0（关） | 当日 `high` 触及 成本×(1+比例) → 按**触发价**卖出 |
| `maxHoldDays` | 0（关） | 持有 N 个交易日后按当日收盘价强制退出（时间止损） |

- 触发价只做 tick 取整、不再叠加滑点（止损单按触发价成交是市场惯例）。
- 强制退出尊重 **T+1**（当日买入不可当日止损）与**涨跌停**（跌停日不可卖出），被挡时记入 `skippedSignals`。
- 成交记录带 `reason`（止损/止盈/时间止损/信号卖出/目标仓位减仓），UI 交易表可溯源。

### D2.1 连续亏损熔断

| 参数 | 默认 | 语义 |
|---|---|---|
| `maxConsecLosses` | 0（关） | 连续亏损达到 N 笔后冻结开新仓 |
| `cooldownDays` | 5 | 熔断冻结的交易日数 |

- 每笔**全部平仓**（position→0）后更新：亏损则计数 +1，盈利则清零；达到阈值后 `cooldown_until = 平仓index + cooldownDays`，期间买入信号被跳过并记入 `skippedSignals`（reason 含「熔断」）。
- 部分减仓不重置计数（只在全平时结算）。

### D2.2 仓位管理

| 参数 | 默认 | 语义 |
|---|---|---|
| `maxPositionPct` | 1.0 | 单笔买入市值上限 = 总资产 × 比例（默认全仓） |
| `strengthScaling` | false | 按信号 `strength` 缩放仓位：有效比例 = `maxPositionPct × clip(strength, 0, 1)` |

- 买入数量 = `min(资金上限, 仓位上限, 成交量上限)`，整手取整；`strength` 之前只作展示，现在可驱动仓位。

### 结果/快照

- 回测结果与持久化快照均含 `risk` 摘要（`risk.summary()`），UI 结果页展示已启用的风控项。
- 实测注意：止损在**无漂移随机游走/高波动**数据上会因滑点与假突破放大亏损，风控是"安全带"而非"印钞机"；正期望仍依赖策略/组合/regime（见路线图 D2.3/D2.4）。

### D2.3 市场状态过滤（regime，v0.1.19）

`risk_control.regime_mask(bars, index_bars, fast=20, use_own_ma=False)` 返回逐日做多许可掩码 `{date: bool}`：

| 参数 | 默认 | 语义 |
|---|---|---|
| `regimeEnabled` | false | 是否启用市场状态过滤 |
| `regimeIndex` | `000300.SH` | 指数代码（沪深300，走 datafeed 取数） |
| `regimeFast` | 20 | 指数均线周期 |
| `regimeOwnMa` | false | 是否同时要求个股自身收盘 ≥ 自身 MA(fast) |

- 规则：**指数收盘 ≥ 指数 MA(fast) 才允许做多**（趋势过滤，避免下跌市做多被反复收割）；`use_own_ma` 叠加个股自身均线。
- **无前视**：撮合循环用「前一交易日」的指数状态判定本日买入许可（`prev_date` 查掩码）。
- 指数数据不足（< fast 根）→ 降级为不生效并写 `regimeWarning`（不阻断回测，UI 显示"已降级"）。
- 指数缺个股某交易日 → 该日保守禁止做多。
- 结果 `regime` 摘要：`{enabled, index, fast, ownMa, allowedDays, blockedDays, warning}`；快照持久化 `{index, fast, ownMa, warning}`。
- 被阻断的买入记入 `skippedSignals`（reason 含「市场状态过滤」）。

> **这是直接改善"回测经常负收益"的关键开关**：默认关闭保持兼容，推荐与 D2.1 止损、后续 D2.4 组合一起开启。

## 组合回测（D2.4，v0.1.20）

多标的组合回测：`run_backtest` 检测到 `symbols` 数 > 1 且策略类带 `is_portfolio=True`（`on_bar` 返回 `PortfolioSignal`）时，自动走组合路径 `_run_portfolio_backtest`；单标的策略传多标的会明确报错。

### 内置组合策略：portfolio_equal_weight（`strategies/builtin/equal_weight.py`）

| 参数 | 默认 | 语义 |
|---|---|---|
| `rebalanceDays` | 5 | 每 N 个交易日等权再平衡 |
| `rebalanceDrift` | 0.05 | 任一个股权重偏离目标超过阈值时提前再平衡（0=关闭） |

返回 `PortfolioSignal`（`{symbol: target_pct}`，等权 = 1/N）。

### 组合撮合路径特性

- **取数**：逐标的经 `datafeed` 取数（真实优先，`allowMock` 回落）；统一日期 + forward-fill 估值（停牌日不可交易但按最后收盘估值）。
- **再平衡**：组合级现金、**先卖后买**（卖释放现金再买）；成本/滑点/tick 取整/整手/成交量约束/T+1/涨跌停与单标的口径一致。
- **风控复用**：regime 门控（组合级，i=0 按当日指数状态、之后按前一日）、逐标的止损/止盈/时间止损、**组合级连续亏损熔断**。
- **绩效**：组合净值曲线、等权买入持有基准、分标的贡献（`perSymbol`：区间涨幅/已实现/浮动盈亏/交易数）。
- 结果带 `portfolio: true`、`symbols`、`perSymbol`；快照持久化组合配置。

> 组合化是散户能吃到的最简单稳健结构（等权吃市场 beta + 分散单标的黑天鹅），与 regime 过滤叠加可显著降低"回测经常负"的概率。已知简化：停牌整篮暂停交易该标的、基准用各标的首末收盘近似。

## 稳健性分析（D4.2，v0.1.21）

回测请求带 `robustness: true` 时，主结果附带 `robustness` 报告（在统一引擎之上以修改后的参数**静默、不落盘**重跑多次）：

| 维度 | 内容 | 判定 |
|---|---|---|
| 成本敏感性 | 佣金/印花税/过户费 × 0.5 / 1 / 2（滑点不变） | 成本×2 仍正收益 → 对成本不敏感（稳健） |
| 参数敏感性 | 数值策略参数 ±20% 邻域（×0.8/1.0/1.2） | 邻域正收益占比 ≥60% → 非参数巧合 |
| 时间切片 | 区间跨度 ≥120 天时对半切片 | 多数时间段正收益 → 非单段依赖 |
| 综合判定 | 红黄绿三档（任一红 → 红；≥2 绿 → 绿） | `verdict.overall` + 逐项 grade/msg |

- 子运行通过 `noPersist` 跳过结果落盘、`_SILENT_PROGRESS` 跳过进度推送（避免刷屏）。
- 单标的与组合回测路径均支持。
- **用法**：这是判断"策略是真行还是参数/区间巧合"的直接工具——回测收益为正且成本×2 仍正、参数邻域多数正、多段时间正，才值得进入实盘验证。

## 回测历史管理（D4.3，v0.1.22）

基于 D0 已建立的结果持久化（`data/backtest_results/bt_*.json`），提供历史查询与 A/B 对比：

| RPC | 功能 |
|---|---|
| `backtest.list` | 列出历史回测摘要（按时间倒序，limit 默认 50）：id/时间/策略/标的/撮合口径/关键指标 |
| `backtest.compare` | 按 id 列表返回完整配置 + 指标（缺失 id 静默跳过），供 A/B 对比 |

- 引擎函数：`list_backtest_results(limit)` / `compare_backtest_results(ids)`（`_results_dir()` 定位目录，损坏文件跳过）。
- 链路：引擎 RPC → `BacktestEngine.list/compare` → `BacktestService.listResults/compareResults` → IPC `backtest:list/compare` → preload → facade。
- UI：回测页底部「历史回测」折叠卡片——勾选 ≥2 个历史回测点击「对比所选」，并排展示策略/标的/区间/撮合口径/总收益/回撤/夏普/胜率/交易数等。
- **用途**：同一策略不同参数、不同策略同区间的迭代调优闭环（配合 D4.2 稳健性判定）。

## 日内回测 / 做T（v0.1.28）

`backtest.run` 的 `period` 为分钟周期（`1m/5m/15m/30m/60m`）时自动走日内路径 `_run_intraday_backtest`（日线路径零改动）。

### 与日线路径的关键差异

| 维度 | 日线路径 | 日内路径（做T） |
|---|---|---|
| T+1 | 按 bar 解禁（每根日线=一天） | **按交易日解禁**（lot.available_day，当日买入次日可卖） |
| 涨跌停基准 | 上一根 bar 收盘 | **昨日收盘**（分钟 bar 前收盘无意义） |
| 仓位模型 | 单一 position/avail | **lot 批次列表**：底仓(core) + T仓(t) 分层 |
| 驱动 | 逐日线 bar | 逐 1m bar，ctx 填 `bars_by_period`（1d/5m/1m 无前视切片）+ 账户快照 |
| 撮合口径 | close/next_open | 收盘价即时撮合（fillModel="intraday_1m"） |
| 尾盘 | - | `forceEodClose`（默认开）：14:55 强平 T 仓 + 买回还原底仓；14:57 后禁开新仓 |

### 做T语义与分账

- **底仓**：`basePositionShares` 参数指定股数，回测首日以开盘价自动建立（占用初始资金）；底仓当日即可卖（视为 T-1 前已有）。
- **正向做T**（先买后卖）：`buy + lot_tag="t"` 建T仓 -> `sell + lot_tag="t"` 卖出，差价入 tPnl。
- **反向做T**（先卖后买）：`sell + lot_tag="core"` 卖底仓（挂 `_pending_core_restore`）-> `buy + lot_tag="t"` 买回 -> `_restore_core_check` 把 T lot 转成 core lot（底仓真正还原），差价结转入 tPnl。
- **lot.cost 为含买入费用的摊薄成本**（(价×量+佣金+过户费)/量），保证分账自洽。
- **corePnl 锚定原始建仓成本**（做T还原不改变底仓成本基准）；期末未还原底仓缺口按原始成本结转入 tPnl。
- **分账自洽不变量**：`finalCapital - initialCapital ≈ tPnl + corePnl`（端到端测试断言）。

### 绩效指标扩展

`metrics.intraday`：`{tPnl(做T收益), corePnl(底仓收益), coreShares, coreCost, tRounds(做T次数), tPerDay(日均), avgTRoundPnl(单次均盈亏), pendingRestoreShares(未还原底仓), tradingDays, finalTShares}`。UI 结果页展示「做T分账」卡片。净值曲线按 1m 粒度（`equityCurveGranularity: "1m"`），bars 为日线聚合（图表兼容）。

### 分钟数据链路（重要修复）

- `datafeed.fetch_bars`：分钟周期 count 模式也会先 `download_history_data`（xtquant 分钟数据必须显式下载，否则 `get_market_data_ex` 返回空 -- **此前"分钟建模/回测失败"的根因**）。
- 分钟缓存完整性判断按 `date[:10]` 归一比较（分钟 date 是 "YYYY-MM-DD HH:MM"，直接字符串比较恒 False 导致缓存永不命中）。
- `_df_to_bars` 保留 `amount`（分时均线 vwap 依赖）；分钟 date 归一 "YYYY-MM-DD HH:MM"。
- `validate_bars(period)` 分钟周期跳过「>15 自然日缺口」检查（午休/停牌天然有洞）。
- mock：`generate_mock_minute_bars`（确定性、每日 240 根、U 型波动、含 amount），仅 allowMock 链路自测用。

### 测试

`bridge/_test_intraday.py`（直接 python 运行）：slice_upto 无前视 / vwap / lot T+1 / 分钟 mock / mock 端到端（含底仓还原 + 分账自洽断言）/ 日线路径回归。

## 绩效指标（metrics）

`totalReturn / benchmarkReturn / excessReturn / annualReturn / maxDrawdown / sharpeRatio / sortinoRatio / calmarRatio / annualVolatility / avgHoldDays / winRate / profitLossRatio / totalTrades / skippedSignals / finalCapital`（日内路径另有 `metrics.intraday` 做T分账，见上文）

另有：
- `monthlyReturns`：月度收益序列 `[{month: "YYYY-MM", return: %}]`（热力图数据）
- `inSampleWarning`：回测区间与模型训练区间（config 的 `data_date_range`）重叠时的警告文案；不重叠为 null
- `backtestId`：本次回测的持久化 ID

### 指标口径修复（D0.2，v0.1.17）

- `annualReturnNote`：回测区间 <60 个交易日时不年化（年化指数放大失真），`annualReturn` 置 0 并给出提示文案；>=60 根时为 null。
- `winRateInclOpen`：**含未平仓浮盈亏口径的胜率**——末尾仍持仓时，将浮动盈亏按一笔虚拟交易计入（与 `totalReturn` 含未实现收益的口径对齐）。
- `realizedPnl` / `openPnl`：已实现盈亏 / 未平仓浮动盈亏分离展示。

## 结果持久化

每次回测在 `data/backtest_results/bt_*.json` 落盘配置快照（策略/参数/区间/成本假设/**撮合口径 fillModel/allowMock**/metrics），供历史 A/B 对比与结果溯源。

## 样本内回测检测

回测时若 `strategy.params.model_id` 存在，读取 `data/models/{id}/config.json` 的 `data_date_range`（训练数据起止日期），与回测区间比对，重叠则结果带 `inSampleWarning`，UI 顶部红条警告「结果会显著偏乐观，仅用于调试」。
