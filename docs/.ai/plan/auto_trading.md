# 量化自动交易开发计划

> 日期：2026-07-15
> 目标：实现策略自动执行 + QMT 实盘交易，打通"行情 -> 策略 -> 自动下单"全链路
> 前置条件：行情查询、策略 CRUD、回测引擎、模拟交易均已就绪

## 现状总结

| 模块 | 状态 | 备注 |
|------|------|------|
| 行情查询 | ✅ | QMT 真实行情 + Mock，支持快照/K线 |
| 策略管理 | ✅ | CRUD + JSON 持久化，支持 ma_cross |
| 回测引擎 | ✅ | Python 回测，完整绩效指标 |
| 模拟交易 | ✅ | MockTradeDataSource 内存模拟 |
| QMT 交易桥 | ❌ | qmt_server.py 无交易方法，qmt-trade.js 为骨架 |
| 策略执行引擎 | ❌ | 只有回测，无实盘调度 |
| 实时订阅 | ⚠️ | 订阅了但回调为空，数据未推送 |
| 风控 | ⚠️ | 仅基础校验 |
| 日志持久化 | ❌ | 交易无落盘 |

---

## Phase 1: QMT 交易桥

**目标**：让 qmt_server.py 支持实盘交易，打通 Node -> Python -> miniQMT 的交易链路。

### 1.1 bridge/qmt_server.py 扩展交易方法

引入 `xtquant.xttrader`，新增以下 JSON-RPC 方法：

| 方法 | 功能 | 关键 API |
|------|------|----------|
| `trade.connect` | 连接交易服务器 | `XtQuantTrader(path, session_id)` + `start()` + `login()` |
| `trade.order` | 下单 | `order_stock(account, code, order_type, volume, price_type, price)` |
| `trade.cancel` | 撤单 | `cancel_order_stock(account, order_id)` |
| `trade.positions` | 查持仓 | `query_stock_positions(account)` |
| `trade.orders` | 查当日委托 | `query_stock_orders(account)` |
| `trade.account` | 查资金 | `query_stock_asset(account)` |

关键设计：
- 账号信息从环境变量 `QMT_ACCOUNT_ID` / `QMT_ACCOUNT_TYPE` 读取（追加到 .env）
- 交易连接独立于行情连接，单独管理连接状态
- 回调函数处理订单状态推送（成交/撤单/拒绝）
- 统一字段映射，返回与 MockTradeDataSource 一致的数据结构

### 1.2 main/datasources/trade/qmt-trade.js 补全

- 已有 spawn + JSON-RPC 通信骨架，补全方法映射
- `placeOrder(order)` -> `trade.order`
- `cancelOrder(orderId)` -> `trade.cancel`
- `getPositions()` -> `trade.positions`
- `getOrders()` -> `trade.orders`
- `getAccount()` -> `trade.account`
- 初始化时调用 `trade.connect` 握手

### 1.3 配置扩展

.env 新增：
```
QMT_ACCOUNT_ID=资金账号
QMT_ACCOUNT_TYPE=STOCK
```

config/default.json 的 tradeSource 支持 `"qmt"` 模式。

### 1.4 验收标准

- [ ] UI 交易页面切换到 QMT 模式后能查到真实资金/持仓
- [ ] 能下限价单/市价单，能在 UI 看到委托回报
- [ ] 能撤单

---

## Phase 2: 策略执行引擎

**目标**：让策略从"draft"变为可自动运行的"running"状态，定时拉行情 -> 算信号 -> 自动下单。

### 2.1 策略执行器 main/services/executor-service.js [新增]

核心类 `StrategyExecutor`，每个运行中的策略对应一个实例：

```
StrategyExecutor
  ├── start()    - 启动定时调度
  ├── stop()     - 停止调度，平仓或保留仓位
  ├── tick()     - 单次执行：拉行情 -> 算信号 -> 下单
  └── status     - running / stopped / error
```

调度模式：
- **定时模式**：setInterval 按策略配置的周期（如每日 14:55、每 5 分钟）
- **事件模式**（后续）：订阅实时 tick，每根 K 线收盘触发

tick() 执行流程：
1. 获取策略最新行情（快照 or K线）
2. 调用策略信号计算函数，得到 action: buy / sell / hold
3. 若有信号，调用 TradeService 下单
4. 记录执行日志

### 2.2 策略信号计算

当前只有 ma_cross，将其信号逻辑从回测引擎提取为独立模块，供回测和实盘共用：

```
bridge/strategies/
  ├── __init__.py
  ├── base.py          - 策略基类（输入 bars，输出 signal）
  └── ma_cross.py      - 均线交叉策略
```

Node 侧对应 main/strategies/ 镜像，或直接在 Python 桥中执行信号计算。

### 2.3 ExecutorService 管理器

```js
class ExecutorService {
  async start(strategyId)   // 启动策略执行
  async stop(strategyId)    // 停止策略执行
  getStatus(strategyId)     // 查询运行状态
  listRunning()             // 列出运行中策略
}
```

- 策略状态机：draft -> running -> stopped / error
- 应用重启后自动恢复 running 策略（从 data/strategies/ 读取状态）

### 2.4 IPC 通道

| 通道 | 功能 |
|------|------|
| `executor:start` | 启动策略执行 |
| `executor:stop` | 停止策略执行 |
| `executor:status` | 查询运行状态 |
| `executor:list` | 列出运行中策略 |

### 2.5 验收标准

- [ ] 能在 UI 上启动/停止策略
- [ ] 策略运行时能按设定周期自动下单
- [ ] 应用重启后能恢复运行状态
- [ ] 执行日志可在 UI 查看

---

## Phase 3: 风控体系

**目标**：防止策略失控，保护资金安全。

### 3.1 下单前风控（TradeService 层）

| 规则 | 说明 |
|------|------|
| 单笔最大金额 | 超过设定金额拒绝下单 |
| 最大持仓比例 | 单票持仓不超过总资产 N% |
| 日内交易次数 | 超过 N 次拒绝下单 |
| 涨跌停保护 | 涨停不买、跌停不卖 |
| 总仓位控制 | 总持仓市值不超过总资产 N% |

### 3.2 运行时风控（ExecutorService 层）

| 规则 | 说明 |
|------|------|
| 止损 | 持仓亏损超过 N% 自动平仓 |
| 止盈 | 持仓盈利超过 N% 自动平仓 |
| 日内回撤熔断 | 当日净值回撤超过 N% 停止所有策略 |
| 异常熔断 | 连续下单失败 N 次停止策略 |

### 3.3 风控配置

策略 JSON 中增加 risk 字段：
```json
{
  "params": { "fast": 5, "slow": 20 },
  "risk": {
    "stopLoss": 0.05,
    "stopProfit": 0.15,
    "maxPositionRatio": 0.3,
    "maxOrderAmount": 500000
  }
}
```

### 3.4 验收标准

- [ ] 下单前风控规则生效，违规下单被拒绝并提示原因
- [ ] 止损止盈自动触发
- [ ] 回撤熔断能停止所有策略

---

## Phase 4: 实时行情订阅闭环

**目标**：让前端能收到实时 tick 推送，为事件驱动策略打基础。

### 4.1 Python 桥推送

- qmt_server.py 的 handle_quote_subscribe 回调中，将 tick 数据序列化为 JSON 写入 stdout
- 推送格式：{"id": null, "push": "tick", "data": {...}}（与响应区分）

### 4.2 Node 侧转发

- qmt.js 的 _onLine 识别 push 类型消息
- 通过 mainWindow.webContents.send 转发到渲染进程

### 4.3 渲染层接收

- preload.js 暴露 facade.quote.onTick(callback)
- ViewModel 订阅 tick 更新 UI

### 4.4 验收标准

- [ ] 前端 K线/行情卡片实时刷新
- [ ] 策略可配置为事件驱动模式（tick 触发）

---

## Phase 5: 日志与持久化

**目标**：交易可追溯，策略可复盘。

### 5.1 交易记录

- trade-service.js 下单/成交/撤单记录写入 data/trades/YYYY-MM-DD.json
- 字段：时间、策略ID、代码、方向、数量、价格、状态、备注

### 5.2 策略执行日志

- ExecutorService 每次 tick 记录：时间、行情快照、信号、下单结果
- 写入 data/logs/{strategyId}/YYYY-MM-DD.json

### 5.3 净值曲线

- 每日收盘后记录策略净值到 data/equity/{strategyId}.json
- UI 可查看策略历史净值曲线

### 5.4 验收标准

- [ ] 交易记录可查询、可导出
- [ ] 策略执行日志可查看
- [ ] 净值曲线可在 UI 展示

---

## 开发顺序与依赖

```
Phase 1 (QMT 交易桥)
  └──> Phase 2 (策略执行引擎)  ──> Phase 3 (风控)
                                    │
Phase 4 (实时订阅) <────────────────┘
  │
  └──> Phase 5 (日志持久化)  [可并行，随时插入]
```

- Phase 1 是前置依赖，必须先完成
- Phase 2 依赖 Phase 1（需要实盘下单能力）
- Phase 3 依赖 Phase 2（需要策略执行框架）
- Phase 4 可与 Phase 2-3 并行
- Phase 5 可随时插入，建议 Phase 2 时同步做基础日志
