# 无人值守每日自动交易 · 实施计划

> 状态：**P0 已实施（v0.1.42）**，P1/P2 待办
> 版本基线：mookquant v0.1.41 → v0.1.42
> 关联文档：`docs/.ai/knowledge-library/06-trade/0601-trade-channels.md`、`07-data/0701-quote-sources.md`、`08-ops/0801-unattended-trading.md`

## 实施进度

（P1-A 于同日完成：日终报告 + 额度守门）

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0-1 | `trading-calendar.js` + 缓存（深交所日历，242 交易日） | ✅ 2026-11-25 |
| P0-2 | `trading-scheduler.js` + 七时点事件（含 day_end） | ✅ 2026-11-25 |
| P0-3 | 策略按 `schedule.period` 调度（`strategy-timing.js`） | ✅ 2026-11-25 |
| P0-4 | 单测 38 例（`node tests/node/run.js`，含全流程模拟） | ✅ 38 passed |
| P1-5 | 开机自启 + 托盘常驻 | ⬜ |
| P1-6 | 日终报告 + 界面卡片 | ✅ 2026-11-25 |
| P1-7 | 额度守门 | ✅ 2026-11-25 |
| P1-8 | 行情双源切换（`ths.js`） | ⬜ |
| P2 | 尾盘撤单 / 多策略资金 / 复盘模式 | ⬜ |

实测记录见 `docs/.ai/knowledge-library/08-ops/0801-unattended-trading.md`「验收记录」。

---

## 任务描述

让 mookquant 在**无人干预**的前提下，每个交易日自动完成「盘前检查 → 开盘交易 → 尾盘处理 → 日终结算」，
策略按自身周期出信号并下单，异常可自愈，全过程可审计。

---

## 一、目标与边界

### 1.1 目标

1. 交易日 **09:15 自动就绪**，09:30 开始交易，15:05 自动出日报；
2. 应用**开机自启 + 常驻**，崩溃重启后自动恢复运行中的策略；
3. 策略**按周期调度**（日线策略一天 1-2 次，分钟策略每 bar 一次），不做无意义轮询；
4. 每日交易接口调用控制在 **50 次以内**（额度 1000/天，占用 <5%）；
5. 全链路可审计：每个决策点都有日志，日终出一份 JSON 报告。

### 1.2 范围

**做：**

- 交易日历、交易时段状态机、五个调度时点
- 策略周期化调度（替换当前统一 60s tick）
- 开机自启、托盘常驻、崩溃恢复
- 日终报告（净值 / 成交 / 额度）
- 额度守门与降级（只平仓不开仓）
- 行情双源切换（东财 ↔ 同花顺）

**不做：**

- 真实资金实盘（当前只有东财模拟盘 + QMT）
- 多账户/多策略资金分配
- tick 级高频（需 QMT，另议）
- 交易信号的机器学习自动调参

### 1.3 设计原则

1. **时间与执行分离**：调度器只管「什么时候允许跑」，执行器只管「跑什么」；
2. **失败不静默**：任何接口失败必须记录到日志 + 日终报告，绝不伪造成交；
3. **额度优先保止损**：额度接近上限时，停止开仓、保留平仓能力；
4. **可回滚**：所有新增能力通过 `autoTrade` 配置块开关，关掉即回到 v0.1.41 行为。

---

## 二、现状盘点

| 能力 | 状态 | 位置 |
|---|---|---|
| 交易时段判定（open/lunch/pre/after/weekend） | ✅ 已有 | `main/services/market-clock.js` |
| 非交易时段跳过 tick（不耗额度） | ✅ 已有 | `executor-service.js` `tick()` |
| 信号语义适配（qty / target_position / 默认仓位） | ✅ 已有 | `executor-service.js` `_executeSignal()` |
| 下单节流（30s）+ 重复委托去重 | ✅ 已有 | 同上 |
| 成交回查（15s 内确认终态） | ✅ 已有 | `_confirmOrder()` |
| 异常熔断（连续 5 次错误停策略） | ✅ 已有 | `_checkCircuitBreaker()` |
| 应用启动恢复 running 策略 | ✅ 已有 | `ExecutorService.restoreRunning()` |
| 东财模拟盘交易通道 | ✅ 已有 | `main/datasources/trade/eastmoney-sim.js` |
| 东财公开行情源 | ✅ 已有 | `main/datasources/eastmoney.js` |
| **交易日历（节假日）** | ❌ 缺 | 只有周一~周五判断 |
| **五时点调度** | ❌ 缺 | 目前策略自行 60s tick |
| **策略按周期调度** | ❌ 缺 | `schedule.intervalMs` 统一轮询 |
| **开机自启 + 托盘** | ❌ 缺 | 关窗即退出 |
| **日终报告** | ❌ 缺 | — |
| **额度守门** | ❌ 缺 | 仅在 datasource 内有 5s 缓存 |
| **行情双源切换** | ❌ 缺 | 单源，失败即报错 |

---

## 三、每日时间线（状态机）

```
08:50  启动          → 加载配置 / 恢复策略 / 预热行情源
09:15  盘前检查(Pre) → 交易日历校验 / 账户可用 / 额度余量 / 行情连通
09:30  开盘(Open)    → 允许下单，启动交易窗口
10:00+ 盘中(Trading) → 按策略周期调度信号
11:30  午休(Lunch)   → 挂起，tick 直接返回
13:00  复盘(Open)    → 恢复
14:55  尾盘(Tail)    → 撤未成交挂单 / 仓位偏离检查 / 可选调仓
15:05  结算(Settle)  → 拉成交与持仓 → 生成日报 → 归档
15:30  收尾(Idle)    → 关闭当日窗口，等待次日
```

状态机枚举：`idle | pre | open | lunch | tail | settle`，与 `market-clock.js` 的时段状态互补
（`market-clock` 判「交易所是否开市」，调度器判「我们今天打算做什么」）。

---

## 四、组件设计

### 4.1 `main/services/trading-scheduler.js`（新增，P0）

职责：按日推进状态机，向订阅者发事件；不直接调用交易接口。

```js
class TradingScheduler {
  constructor({ config, calendar, onEvent })
  start()          // 注册定时器（每分钟检查一次是否到点）
  stop()
  state            // 'idle' | 'pre' | 'open' | 'lunch' | 'tail' | 'settle'
  getStatus()      // { state, isTradingDay, nextEvent, nextEventAt, lastError }
}
```

事件：`pre_open`、`market_open`、`lunch_break`、`market_resume`、`tail`, `settle`。

设计要点：

- 用**分钟级 tick + 时点比对**，不用长定时器（避免休眠/时钟漂移导致错过）；
- 每次触发后写 `data/logs/scheduler.log`，便于事后排查"今天为什么没交易"；
- 进程重启后根据当前时间**补跑**已错过但仍在有效窗口内的事件（例如 09:40 重启仍应触发 `market_open`）。

### 4.2 `main/services/trading-calendar.js`（新增，P0）

```js
async load(year)        // 拉取交易日列表，缓存到 data/calendar/{year}.json
isTradingDay(date)      // 周一~周五 且 不在节假日列表
nextTradingDay(date)
```

数据来源优先级：

1. 东财/同花顺公开接口（一次拉全年，缓存 24h）；
2. 本地缓存（离线可用）；
3. 兜底：仅按周一到周五判断，并在界面标注「未校准节假日」。

### 4.3 策略周期化调度（改造 `executor-service.js`，P0）

当前：`schedule.intervalMs || 60000` 固定轮询。

目标：策略声明周期，执行器按周期决定是否计算信号。

```json
"schedule": {
  "period": "1d",          // 1m | 5m | 15m | 30m | 60m | 1d
  "at": ["09:31", "14:55"],// 日线策略的固定信号点（可选）
  "intervalMs": 60000      // 兜底（period 缺失时使用）
}
```

规则：

| period | 触发时机 |
|---|---|
| `1d` | 每个 `at` 时点各一次；缺省 09:31 + 14:55 |
| `1m`/`5m`/… | 对应 bar **收盘后** 5 秒触发一次（避免半根 bar 抖动） |
| 未配置 | 沿用 `intervalMs`（兼容旧策略） |

同一策略在一个 bar 内只允许一次信号计算（用 `_lastBarKey` 去重）。

### 4.4 开机自启 + 托盘（P1）

- `app.setLoginItemSettings({ openAtLogin: true, args: ["--hidden"] })`
- `--hidden` 启动时不显示窗口，只驻留托盘；
- 关闭窗口 → 隐藏到托盘（`event.preventDefault()` + `win.hide()`），退出走托盘菜单；
- 托盘图标显示颜色状态：绿=交易中、黄=非交易时段、红=异常。

### 4.5 日终报告（P1）

`data/reports/{YYYY-MM-DD}.json`：

```json
{
  "date": "2026-11-25",
  "account": { "totalAssets": 999983.97, "available": 996456.97, "marketValue": 3527, "pnl": -16.04, "nav": 1.0 },
  "trades": [{ "orderId": "...", "symbol": "sz000001", "side": "buy", "price": 12.3, "quantity": 100, "status": "filled" }],
  "positions": [{ "symbol": "sz000001", "quantity": 200, "cost": 12.345, "pnlPct": -0.45 }],
  "signals": 2,
  "orders": 1,
  "skips": [{ "reason": "目标仓位已达", "at": "14:55" }],
  "quota": { "used": 7, "limit": 1000 },
  "errors": []
}
```

界面：交易页顶部加「今日表现」卡片（净值、成交笔数、额度占用、异常数）。

### 4.6 行情双源切换（P1）

```
quoteService.getQuote()
   ├── 主源（配置 dataSource）
   ├── 失败 → 备用源（eastmoney ↔ ths）并记录切换
   └── 都失败 → 本次 tick 跳过下单，只记日志
```

新增 `main/datasources/ths.js`（同花顺公开接口：日线 / 1m / 30m / 实时 realhead / 分时）。

### 4.7 额度守门（P1）

- `tradeService` 内加计数器：每次调用 `+1`，按自然日重置；
- `autoTrade.dailyCallBudget`（默认 300）、`autoTrade.reserveForStopLoss`（默认 100）；
- 已用 ≥ budget − reserve：**禁止开仓**，仅允许平仓/撤单；
- 已用 ≥ budget：全部禁止，界面红字告警。

---

## 五、数据与存储

| 路径 | 内容 | 保留 |
|---|---|---|
| `data/reports/{date}.json` | 日终报告 | 永久 |
| `data/calendar/{year}.json` | 交易日历 | 永久 |
| `data/logs/scheduler.log` | 调度事件 | 30 天 |
| `logs/trades-{date}.log` | 交易流水（已有） | 永久 |

---

## 六、额度预算

日线策略（2 信号点 + 1 笔成交）：

| 动作 | 次数/天 |
|---|---|
| 盘前账户检查 | 1 |
| 信号计算（不耗交易额度） | 0 |
| 下单 + 回查 + 持仓确认 | 3 |
| 日终对账 | 2 |
| **合计** | **约 6 次（0.6%）** |

对比改造前（60s 轮询查持仓）：**240 次/天/标的** —— 这是本次改造最大的收益点。

---

## 七、异常与恢复矩阵

| 异常 | 检测方式 | 处理 | 恢复 |
|---|---|---|---|
| 行情源断 | 请求失败/超时 | 切备用源 | 下次 tick 重试主源 |
| 交易接口 112 频控 | 响应码 | 退避重试 3 次 | 自动 |
| 非交易时段下单 | 501 | 记 skipped，不重试 | 下一交易时段 |
| 连续错误 ≥5 | 计数器 | 熔断停策略 | 人工重启或次日自启 |
| 应用崩溃 | 进程消失 | 依赖开机自启/手动重启 | `restoreRunning` 恢复 |
| 电脑休眠错过时点 | 启动时比对时间 | 补跑仍在窗口内的事件 | 自动 |
| 额度耗尽 | 计数器 | 只平仓不开仓 | 次日重置 |

---

## 八、分阶段落地

### P0（约 2 小时，当天可完成）✅ 已实施

1. `trading-calendar.js` + 缓存
2. `trading-scheduler.js` + 五时点事件
3. executor 按 `schedule.period` 调度（保留 `intervalMs` 兼容）
4. 单测：时点触发、跨日、重启补跑、日线策略一天只算 2 次

**验收**：手动改系统时间模拟 09:15/09:30/14:55/15:05，日志四个事件齐全；日线策略当天信号计算次数 = 2。

> 实际验收方式：不改系统时间（会打乱真实调度节奏），改为 ①单测覆盖全部时点与边界；②在真实交易日（2026-11-25）观察 09:15 `pre_open` 与 09:30 `market_open` 的自然触发。

### P1（约半天）

5. 开机自启 + 托盘常驻
6. 日终报告 + 界面卡片
7. 额度守门
8. 行情双源切换（含 `ths.js`）

**验收**：重启电脑后应用自动启动、策略自动恢复；15:05 生成报告；断网 30 秒后自动切源。

### P2（可选）

9. 尾盘撤单策略（未成交挂单 14:55 自动撤）
10. 多策略资金分配
11. 交易时段外的"复盘模式"（只算信号不下单）

---

## 九、前置条件（需用户确认）

| # | 事项 | 影响 | 结论 |
|---|---|---|---|
| 1 | 电脑是否 7×24 开机 | 关机则改为「开机后补跑」，需要 `catch-up` 逻辑 | ✅ 用户确认：**本机 24 小时开机**；catch-up 已随调度器实现 |
| 2 | 是否允许开机自启 | 影响 P1 实现方式 | ⬜ 待确认 |
| 3 | 电源策略是否禁用休眠 | 休眠会错过时点 | ⬜ 待确认（调度器已支持休眠后补跑，但仍建议禁用休眠） |
| 4 | 交易通道固定为东财模拟盘？ | 决定风控阈值与额度预算 | ⬜ 待确认（当前 `tradeSource=eastmoney`） |
| 5 | 哪台机器作为交易主机 | 多机部署需去重（避免双开重复下单） | ⬜ 待确认 |

> 第 5 点尤其重要：**同一策略在两台机器同时跑会重复下单**，需加文件锁或实例心跳。

---

## 十、风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| 调度器误判交易日 | 日历缓存 + 手动覆盖开关 | `autoTrade.enabled=false` |
| 重复下单 | 委托去重 + 单实例锁 | 关闭策略 |
| 额度误算 | 本地计数 + 每日 `/apikey/usage` 校准 | — |
| 行情源被限流 | 双源 + 3s 缓存 + 批量合并 | 切回 QMT/mock |
| 自动交易失控 | 熔断 + 单笔/单日上限 + 只模拟盘 | 关闭 `autoTrade` |

**总开关**：`config/default.json` → `autoTrade.enabled = false`，重启即回到纯手工模式。

---

## 附录 A：配置示例

```json
"autoTrade": {
  "enabled": true,
  "allowOffHours": false,
  "minOrderIntervalMs": 30000,
  "dailyCallBudget": 300,
  "reserveForStopLoss": 100,
  "autoStart": true,
  "minimizeToTray": true,
  "reportAt": "15:05"
}
```

## 附录 B：调度器伪代码

```js
tickEveryMinute(now) {
  if (!calendar.isTradingDay(now)) return;
  const hhmm = fmt(now);
  for (const evt of EVENTS) {            // pre 09:15 / open 09:30 / lunch 11:30 / resume 13:00 / tail 14:55 / settle 15:05
    if (hhmm === evt.at && !this.firedToday(evt)) {
      this.firedToday.mark(evt);
      this.emit(evt.name);
    }
  }
}
```
