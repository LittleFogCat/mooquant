# 无人值守定时交易（交易日历 / 调度器 / 策略周期）

> 版本基线：v0.1.42
> 相关：`06-trade/0601-trade-channels.md`、`07-data/0701-quote-sources.md`、`docs/.ai/plan/auto-trading-daily.md`

## 解决的问题

v0.1.41 之前执行器只有一道「交易时段守卫」，剩下两个问题会让无人值守跑歪：

1. **不知道今天是不是交易日**：`market-clock` 只判周末，遇到国庆/春节会在假期里空转甚至下单失败；
2. **所有策略统一 60s 轮询**：日线策略一天算 1440 次信号，纯粹浪费算力，而且每次 tick 都可能打到交易接口（额度 1000/天）。

v0.1.42 引入三个模块，形成「三道闸门」：

```
① TradingCalendar  今天是不是交易日（节假日）
② TradingScheduler 交易所开没开市 + 今天在不在交易计划窗口（五时点状态机）
③ strategy-timing  这个周期的这次信号该不该算（日线一天 2 次 / 1m 每根 bar 一次）
        ↓ 三道都过
   StrategyExecutor._tickSymbol()  → 拉行情 → 算信号 → 下单 → 回查
```

三道闸门都是**纯本地时间判断**，被挡掉时零接口开销。

## 交易日历（`main/services/trading-calendar.js`）

数据来源优先级：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | 深交所公开日历 | `GET https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=YYYY-MM`，`jybz="1"` 为交易日；按月查询，启动时逐月拼成整年（12 次请求，约 2s） |
| 2 | 本地缓存 | `data/calendar/{year}.json`，24h 内不重复拉取，断网可用 |
| 3 | 兜底 | 仅按周一~周五判断，`isCalibrated()` 返回 `false`，界面标「节假日未校准」 |

要点：

- **交易所日历按年发布**：12 月查次年常返回空数组，代码遇到空月份直接 `break`，避免 12 次无效请求；此时次年回落兜底，等交易所发布后自动生效。
- **按月记录覆盖情况**（`covered` 集合）：只有被覆盖的月份才用「列表内=交易日」，未覆盖月份回落周一~周五，避免把「没拉到数据」误判成「全是节假日」。
- `calendarOverrides`（`autoTrade.calendarOverrides`）可手动纠偏，例如调休上班 `{"2026-11-28": true}`。
- `isTradingDay` 是**同步热路径**（每 tick 调用），联网只在 `ensureLoaded()` 里做。

## 交易调度器（`main/services/trading-scheduler.js`）

### 事件与窗口

| 时点 | 事件 | 状态 | 说明 |
|---|---|---|---|
| 09:15 | `pre_open` | pre | 盘前体检（行情源 + 账户） |
| 09:30 | `market_open` | open | 允许下单 |
| 11:30 | `lunch_break` | lunch | 午休，不放行 |
| 13:00 | `market_resume` | open | 恢复 |
| 14:55 | `tail` | tail | 尾盘（15:00 前仍可下单） |
| 15:05 | `settle` | settle | 日终结算 |
| 15:30 | `day_end` | idle | 收尾 |

每个事件的窗口是 `[自己的 at, 下一个事件的 at)`，**互相衔接、不重叠**，因此任一时刻最多只有一个「活跃事件」。

### 为什么是「分钟级 tick + 时点比对」

长定时器（`setTimeout(9:30)`）在电脑休眠、时钟漂移、进程重启时会直接错过时点。现在每 20s 比对一次「当前时间落在哪个窗口」，于是：

- **重启补跑**天然成立：09:40 启动时 `market_open` 窗口仍活跃且未触发 → 补发一次，`payload.catchUp = true`；
- **不重放历史**：15:00 重启只会补 `tail`，不会把上午的事件全放一遍（窗口互斥的直接收益）；
- **去重**：按 `日期 + 事件名` 记在 `_fired` 里，跨日自动清空。

### 放行判定用「时间」而不是缓存的 state

```js
canPlaceOrder(now) {
  if (!isTradingDay(now)) return false;
  const idx = activeEventIndex(now);          // 按 now 重新算窗口
  if (!TRADABLE_STATES.has(EVENTS[idx].state)) return false;
  return minutesOfDay(now) >= 9*60+30 && minutesOfDay(now) < 15*60;  // 15:00 后不接受委托
}
```

**坑**：`tail` 窗口是 14:55–15:05，跨越了收盘时间。如果只看 `state === 'tail'` 就放行，15:00–15:05 下的单会被交易所拒绝（501）。所以必须再压一条 `< 15:00` 的硬边界。
另一个坑：电脑休眠 2 小时后 state 可能还停在 `open`，用缓存的 state 判断会误放行——所以 `canPlaceOrder` 一律按传入时间重算。

## 策略周期（`main/services/strategy-timing.js`）

策略 JSON 的 `schedule` 字段：

```json
"schedule": {
  "period": "1d",              // 1m | 5m | 15m | 30m | 60m | 1d；缺省=按 intervalMs
  "at": ["09:31", "14:55"],    // 仅 1d 使用，缺省即这两个时点
  "intervalMs": 60000          // 兜底（老策略不配 period 时沿用）
}
```

| period | 触发时机 | 实现要点 |
|---|---|---|
| `1d` | 每个 `at` 时点一次 | 取「**已过的最新时点**」，所以 15:00 重启只补 1 次，不会连补 2 次 |
| `1m`/`5m`/… | 对应 bar **收盘后 5 秒** | bar 与每个交易时段起点对齐：1m → 09:31…11:30 / 13:01…15:00；60m → 10:30,11:30 / 14:00,15:00 |
| 未配置 | 每 `intervalMs` 一次 | 用「时间桶」去重，行为与 v0.1.41 等价 |

执行器侧：

- 定时器固定 15s（`autoTrade.tickMs`），每次只做三道闸门的本地判断，`_isDue()` 返回 false 就直接返回；
- 去重 key 形如 `2026-11-25#09:31`，**先记账再执行**——宁可漏一次信号，也不重复下单；
- `_tickCount` 只统计真正算了信号的次数（`_pollCount` 才是定时器触发次数），界面显示 `tick#N` 因此变得可读；
- 取哪个周期的 K 线跟着 `period` 走：`kind=bar` 取对应分钟周期（240 根），否则取日线（60 根）。

### 分钟周期的现实限制

**东财公开行情只有日线**：`push2his` 的 `klt=1/5/15/30/60` 返回 `klines: []`（试过 `beg=0`、日期区间、带 Referer 均无效），
所以 `period: "1m"` 的策略在东财行情下拿不到 bar。

处理方式：执行器**明确跳过并写清原因**（`行情源无 1m K 线（请改用日线周期，或切换 QMT 行情）`），
既不拿日线冒充分钟线算错信号，也不计入连续错误（否则 5 次就会误触发熔断）。

分钟周期需要 QMT 行情（`download_history_data` 后本地读取）才能真正跑起来。

### 「跳过原因」不能被覆盖

`tick()` 原来在标的循环结束后无条件 `this._lastSkip = null`，会把循环内设置的跳过原因（目标仓位已达/下单节流/已有未成交委托…）擦掉，
界面上永远看不到——这与「失败不静默」冲突。现在改为**进入执行阶段时先清空、循环内重新赋值、结束后不再覆盖**。

## 配置

```json
"autoTrade": {
  "enabled": true,
  "allowOffHours": false,
  "minOrderIntervalMs": 30000,
  "tickMs": 15000,
  "schedTickMs": 20000,
  "dailyCallBudget": 300,
  "reserveForStopLoss": 100,
  "calendarOverrides": {}
}
```

- `tickMs`：执行器定时器间隔（只做本地判断，与接口调用无关）
- `schedTickMs`：调度器比对间隔
- `dailyCallBudget` / `reserveForStopLoss`：交易接口每日调用预算与为平仓预留的份数（0 = 不启用守门）
- `enabled=false`：只算信号不下单（演练）；调度器仍会跑，界面照常显示状态

## 状态可见性

- IPC：`scheduler:status` / `scheduler:events`（`preload.js` → `facade.scheduler`）
- 界面：
  - 策略页顶部状态条 —— `空闲/盘前/交易中/午休/尾盘/结算` · 是否交易日 · 下一时点 · 日历来源与更新时间和交易日数
  - 交易页「今日表现」卡片 —— 今日成交笔数 / 策略信号次数 / 接口额度占用 / 异常条数 / 报告生成时间
- 每个运行中策略的副标题显示 `调度：日线 09:31 / 14:55（下次 14:55）` 与跳过原因
- 日志：`data/logs/scheduler.log`（只在 `start()` 之后写，单测手动 tick 不会污染）

## 额度守门（`trade-service.js`）

东财妙想模拟组合每天 **1000 次**调用额度，用尽后连止损都下不出去。因此在**唯一交易出口** `TradeService` 计数：

- 所有 `source.*` 调用统一走 `_call()`：先计数再调用（失败的请求同样消耗额度）；
- 计数按本地自然日重置；
- `getQuota()` 返回 `{used, limit, reserve, remaining, openAllowed, closeAllowed, usedRatio}`；
- 分级策略：
  - `used < limit - reserve` → 正常（可开可平）
  - `limit - reserve ≤ used < limit` → **禁止开仓**，只允许平仓/撤单/止损
  - `used ≥ limit` → 全部禁止，界面红字告警
- 被拦下的委托会以 `type: "rejected"` 写入 `data/trades/{date}.json`，进日报的 `rejects`。

`limit = 0` 表示不启用守门（mock / QMT 无硬性额度）。

预算按日线策略实测：一天约 6~20 次（盘前体检 + 2 个信号点 + 日终报告），300 的预算留了 15 倍余量，
其中 100 份专门留给尾盘异常平仓。

## 日终报告（`report-service.js`）

`settle` 事件（15:05）触发 `ReportService.generate()`，落盘 `data/reports/{YYYY-MM-DD}.json`：

```json
{
  "date": "2026-11-25",
  "account": { "totalAssets": 1000001.97, "available": 996456.97, "marketValue": 3527,
               "pnl": 1.97, "pnlPct": 0.0002, "nav": 1.000002 },
  "trades": [{ "orderId": "...", "symbol": "sz000001", "side": "buy", "price": 12.3,
               "quantity": 100, "status": "filled", "strategyId": "st_x", "time": "..." }],
  "positions": [{ "symbol": "sz000001", "quantity": 200, "cost": 12.345, "pnlPct": -0.45 }],
  "signals": 2, "orders": 1, "cancels": 0, "rejects": 0,
  "strategies": [{ "name": "无人值守演示", "signals": 2, "polls": 1601,
                   "schedule": "日线 09:31 / 14:55", "lastFireKey": "2026-11-25#14:55" }],
  "skips": [{ "strategy": "无人值守演示", "reason": "等待调度时点（日线 09:31 / 14:55）" }],
  "quota": { "used": 7, "limit": 300, "reserve": 100 },
  "errors": []
}
```

设计要点：

- **只读聚合**：账户 1 次 + 持仓 1 次，其余全读本地日志/内存状态，避免为了出报告再烧额度；
- **失败不缺席**：接口挂了照样生成报告，把原因塞进 `errors`（报告缺失比数据不全更糟）；
- `nav = totalAssets / (totalAssets - totalPnl)`，即用当前净值倒推初始资产；
- `skips` / `strategies.polls` 是回答「今天为什么没交易」的关键字段；
- IPC：`report:today` / `report:get` / `report:list` / `report:generate`。

**坑**：`LogService` 与 `ReportService` 的日期口径必须都是**本地日期**（原先用 `toISOString().slice(0,10)` 是 UTC，
在 00:00-08:00 之间会串到前一天），否则日报会读不到当天的成交流水。

## 单测

```bash
node tests/node/run.js            # 全部（45 例）
node tests/node/run.js scheduler  # 只跑调度器
node tests/node/run.js full-day   # 只跑全流程模拟
```

| 文件 | 覆盖 |
|---|---|
| `test-trading-calendar.js` | 兜底/节假日/手动覆盖/跨周末/缓存往返 |
| `test-strategy-timing.js` | 三类调度解析、日线一天 2 次、补跑只补一次、1m/30m bar 边界、下次时点 |
| `test-trading-scheduler.js` | 七时点事件序列、去重、重启补跑、非交易日、放行边界、跨日重置、异常隔离 |
| `test-executor-gating.js` | 执行器三道闸门（Mock 时间驱动 `_isDue` / `_isTradingAllowed`） |
| `test-quota-report.js` | 额度分级拦截（开仓/平仓/用尽）、失败调用也计数、日终报告聚合与落盘 |
| `test-full-day.js` | **全流程**：虚拟时钟 08:50→15:30 每 15s 步进，真实模块接线，行情/账户全用桩 |

**全流程模拟**是验收「日线策略一天只算 2 次」最直接的方式（也是唯一不依赖真实等待的验证手段）：
劫持全局 `Date` 造虚拟时钟，串起 `TradingCalendar → TradingScheduler → ExecutorService → StrategyExecutor`，
断言事件序列、`tickCount === 2`、`pollCount === 1601`、收盘后 `canPlaceOrder === false`、盘前体检结果。

**坑**：
- 单测里 `new TradingScheduler({ autoTick: false })` + 手动 `tickAt(now)` 驱动，不要 `start()`，否则会启动真实定时器并写日志；
- 全流程测试必须把 `executor._logTick` / `_updateStatus` 换成空函数，否则会往 `data/` 写文件；
- 桩策略的 `risk` 必须为空对象，否则 `_checkStopLossProfit` 会去调 `getPositions`（就有真实接口调用了）。

## 验收记录（2026-11-25）

**应用内实测**（08:59 启动，真实交易日）：

```
[main] 交易日历: {"ok":true,"year":2026,"source":"szse","calibrated":true}
[main] 调度器状态: {"state":"idle","isTradingDay":true,"nextEventAt":"2026-11-25 09:15",
      "calendar":{"tradingDays":242,"coveredMonths":["01"..."12"]}}
[executor] 启动策略: 无人值守演示（MA交叉） 调度=日线 09:31 / 14:55
```

界面状态条显示：`空闲 · 今日为交易日 · 下一时点 2026-11-25 09:15 · 交易日历：242 个交易日 · 来源 szse`；
策略副标题显示 `调度：日线 09:31 / 14:55 · 盘前 · 跳过：非交易时段（盘前）`。

**全流程模拟**（`node tests/node/run.js full-day`）输出即为当日时间线：

```
调度事件: pre_open        → 盘前体检 ok（行情已连接 / 可用资金 1,000,000）
调度事件: market_open     → tick #1 sz000001 -> hold
调度事件: lunch_break     → （无 tick）
调度事件: market_resume   → （无 tick）
调度事件: tail             → tick #2 sz000001 -> hold
调度事件: settle           → 日终快照
调度事件: day_end
```

整日 `tickCount = 2`、`pollCount = 1601`（每 15s 一次本地判断），**没有一次多余信号计算**。

**窗口内实时实测**（09:12 启动，用 CDP 抓取状态而不是看日志，因为该次启动受宿主沙箱限制无法写文件）：

```json
09:15:12  {"state":"pre","fired":["pre_open"],"next":"2026-11-25 09:30",
           "preflight":{"ok":true,"quote":{"mode":"eastmoney","connected":true},
                        "account":{"totalAssets":1000001.97,"available":996456.97}}}
09:32:22  {"state":"open","fired":["pre_open","market_open"],"next":"2026-11-25 11:30"}
          executor: {"tick":1,"poll":80,"signal":"hold",
                     "fired":"2026-11-25#09:31","next":{"at":"14:55"}}
```

即：09:15 盘前体检（行情已连接、账户可查）、09:30 开盘事件、09:31 只用一次信号计算，
下一次时点正确指向 14:55。

**日终报告真机验证**：用真实东财模拟账户跑 `ReportService.generate()`，落地 `data/reports/2026-11-25.json`：

```
总资产 999,951.97（可用 996,456.97 / 市值 3,495） 净值 0.999952 盈亏 -48.04
持仓 2 只（平安银行 200@12.345 -1.26%、浦发银行 100@10.74 -1.58%）
信号 1 次 / 委托 0 笔 / 额度 2-300 / 异常 0
```

> 说明：09:15/09:30 的窗口内实时观察是通过 CDP 读状态完成的——Agent 当前进程上下文里启动 Electron
> 会被宿主的文件沙箱拒绝写入（`EPERM: operation not permitted`，且 Chromium 自身沙箱初始化失败需 `--no-sandbox`），
> 属于环境限制，不是应用缺陷。人工在桌面终端执行 `npm start` 即可看到同样的时点事件与落盘日志。
