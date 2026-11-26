# 交易通道（Trade DataSource）

## 概述

交易能力统一收敛在 `main/datasources/trade/`，与行情数据源（`main/datasources/`）**完全分离**：

```
renderer（trade-view / trade-viewmodel）
        ↓ IPC  trade:*
main/ipc/index.js（转发 only）
        ↓
main/services/trade-service.js（校验 + 风控 + 交易日志）
        ↓
main/datasources/trade/index.js（工厂）
        ├── mock-trade.js        内存模拟（开发兜底）
        ├── qmt-trade.js         QMT 实盘（stdio → bridge/qmt_server.py）
        └── eastmoney-sim.js     东财妙想模拟盘（HTTP → mkapi2.dfcfs.com）
```

策略自动交易走 `main/services/executor-service.js`，最终仍调用 `tradeService.placeOrder`，因此**换交易通道不需要改策略或执行器**。

## 接口契约

所有实现必须提供（见 `trade/index.js` 头注释）：

| 方法 | 返回 |
|---|---|
| `placeOrder(order)` | `{ orderId, status }`，`order` 为 `{symbol, side, quantity, price, orderType, risk?, strategyId?}` |
| `cancelOrder(orderId)` | `{ success }` |
| `getPositions()` | `[{symbol, name, quantity, available, costPrice, currentPrice, marketValue, pnl, pnlPct, ...}]` |
| `getOrders()` | `[{orderId, symbol, side, price, quantity, filledQuantity, orderType, status, createdAt}]` |
| `getAccount()` | `{totalAssets, available, marketValue, totalPnl, ...}` |
| `dispose()` | 释放资源 |

`status` 统一为 `pending / partial / filled / cancelled / rejected`；`symbol` 统一用内部格式 `sh600519` / `sz000001`。

## 配置与切换

| 位置 | 键 | 说明 |
|---|---|---|
| `config/default.json` | `tradeSource` | `mock` / `qmt` / `eastmoney`（别名 `mx`）/ `auto` |
| 设置页「交易数据源」 | — | 可视化切换，等价于写 `tradeSource` |
| `.env` | `MX_APIKEY` | 东财妙想 API Key（**不入仓库**，`.env` 已在 `.gitignore`） |
| `.env` | `MX_API_URL` | 默认 `https://mkapi2.dfcfs.com/finskillshub` |
| `.env` | `MX_ACCOUNT_ID` | 绑定的模拟组合 ID（仅用于展示） |

## 东财妙想模拟盘（eastmoney-sim.js）

### 认证与接口

所有请求 `POST {MX_API_URL}/api/claw/mockTrading/<action>`，请求头 `apikey: <MX_APIKEY>`：

| action | 用途 |
|---|---|
| `balance` | 资金账户 |
| `positions` | 持仓 |
| `orders` | 委托（含已成交/已撤） |
| `trade` | 下单 `{type:'buy'|'sell', stockCode, price, quantity, useMarketPrice}` |
| `cancel` | 撤单 `{type:'order', orderId, stockCode}` |

### 字段映射（实测）

| 东财字段 | 处理 |
|---|---|
| 金额（`totalAssets/availBalance/totalPosValue/profit/value`） | 单位是「厘」，`currencyUnit=1000`，÷1000 转元 |
| 价格（`price/costPrice/tradePrice`） | 放大整数，按 `priceDec/costPriceDec` 还原 |
| `secCode` + `secMkt`（0=深 1=沪） | → 内部 `sz000001` / `sh600519` |
| `drt`（1 买 2 卖） | → `buy` / `sell` |
| `status`（1~10） | → `pending/partial/filled/cancelled/rejected` |

### 五个必须知道的坑（都是实测踩出来的）

1. **市价单不撮合**：`useMarketPrice: true` 的委托会长期停在「已报」（`rawStatus 2`）且成交量为 0。
   适配器已改为：先拉东财公开行情（`push2.eastmoney.com`，不占妙想额度）取现价，按 **买 ×1.02 / 卖 ×0.98** 发限价单，柜台按最新价成交。
2. **失败响应没有 success/status 字段**：`/trade`、`/cancel` 失败时只返回 `{code:"501", message:"买入委托失败: 当前时间不可交易"}`，
   若只判 `success===false` 会把失败当成功（**无人值守下会导致「以为下单了」的幽灵委托**）。`_post` 现在同时判 `status` 与 `code`。
3. **下单响应不带委托号**：`/trade` 成功时 `data` 为空。适配器下单后按「同代码 + 同方向 + 同数量 + 最近 2 分钟」回查委托补齐 `orderId`（时间窗避免匹配历史同参数单）。
4. **频控 112**：连续快速调用会返回「请求频率过高」。`_post` 已内置指数退避重试（2s/4s/6s，最多 3 次）。
5. **撤单要 stockCode**：只给 `orderId` 无法撤单，必须先查委托拿 `secCode`。

### 额度与休眠

- 每个 skill 独立限额，模拟组合为 **1000 次/天**（`/apikey/usage` 可查）。
- 新账号默认**休眠**，被压到 **10 次/天**，报错：`113 调用次数已达上限，账户已进入休眠状态`。
- 解除休眠：`POST /skill/activate`（网页接口，无需 App），`GET /skill/hibernate-status` 可查 `dormant`。
- 适配器对读接口（positions/orders/account）做了 **5 秒 TTL 缓存**，避免策略 tick 打穿额度。
- 应用层再加一道 **额度守门**（v0.1.43，`trade-service.js`）：所有 `source.*` 调用统一计数，
  每天 `autoTrade.dailyCallBudget`（默认 300）为上限，其中 `reserveForStopLoss`（默认 100）只留给平仓；
  余量不足时禁止开仓、只允许平仓，用尽后全部拒绝并写 `rejected` 流水。详见 `08-ops/0801-unattended-trading.md`。

#### 技能目录（可自查，2026-11-26 抓取）

`GET {base}/skill/list` 是**公开接口，无需密钥**，可用于确认平台有没有新技能/改额度：

| skillTag | 技能 | 基础额度/天 | Pro 额度/天 | clawhub 包名 |
|---|---|---|---|---|
| `scss` | 妙想资讯搜索 | 150 | 500 | eastmoney-financial-search |
| `cwcs` | 妙想金融数据 | 150 | 500 | eastmoney-financial-data |
| `zncg` | 妙想智能选股 | 150 | 500 | eastmoney-select-stock |
| `mnzh` | **妙想模拟组合管理** | **1000** | **3000** | eastmoney-stock-simulator |
| `zxgl` | 妙想自选股管理 | 200 | 400 | eastmoney-self-select |
| `aisq` | 妙想AI社区 | 20 | 40 | — |

要点：

- 返回字段：`skillName / skillDesc / featureTags / featurePoints / clawhubAddress / dailyLimit / dailyLimitPro / skillTag`；
- **`dailyLimitPro`** 是 Pro 档额度，模拟组合 Pro 为 **3000/天**（基础档 1000），我们的 300 预算即使在
  基础档也只占 30%，Pro 档占 10%；
- 技能通过 `clawhub.ai` 分发（`featureTags` 均为 `OpenClaw`），是"技能包"形态，不是 MCP tool 列表；
- **没有独立的交易类技能**，下单仍然只走 `mnzh` 这一个 skill 的 5 个 REST 接口；
- `/api/claw/mockTrading/history` 路由存在但恒返回 `500 服务繁忙`，不可用，所以历史成交仍以本地流水 + 当日 `orders` 为准；
- `/apikey/usage`、`/skill/hibernate-status` 需要网页登录态头（`ct/ut/uid/deviceid`），无头环境查不到；
- `em_` 前缀的 MCP Key 调 REST 会返回 `114 API密钥不存在或已失效`，两套密钥不通用。

### 已知限制

- 仅支持 A 股 6 位代码；委托数量必须是 100 的整数倍。
- 仅交易时段（9:30-11:30 / 13:00-15:00）可委托，否则返回「当前时间不可交易」。
- T+1：当日买入的持仓 `available = 0`。
- 无推送，只能轮询。

## QMT 交易（qmt-trade.js）

spawn `bridge/qmt_server.py`，走 stdio JSON-RPC（`trade.connect/order/cancel/positions/orders/account`），需要 `.env` 中的 `QMT_PATH`、`QMT_ACCOUNT_ID` 与已登录的 miniQMT 客户端。

## Mock 交易（mock-trade.js）

内存实现，下单即成交（`status: filled`），初始资金 100 万。用于开发与 QMT/东财不可用时兜底。

## 无人值守执行（executor-service.js + market-clock.js）

> v0.1.42 起，时段/节假日/策略周期的调度细节见 **`08-ops/0801-unattended-trading.md`**。
> 这里只列执行器本身的保护机制。

`StrategyExecutor` 把「策略信号」翻译成「委托」，无人值守的关键点：

| 机制 | 说明 |
|---|---|
| **交易日守卫** | `trading-calendar.js` 判节假日（深交所日历 + 本地缓存，失败降级为周一~周五） |
| **交易时段守卫** | `market-clock.js` 判定 `open/lunch/pre/after/weekend`；调度器再判「今天此刻是否在交易计划窗口」。任一不满足直接跳过 tick，**不消耗行情与交易额度** |
| **策略周期守卫** | `schedule.period`（1d/1m/5m/…）决定何时算信号；日线策略一天只算 2 次，不再 60s 空转 |
| **信号语义适配** | 优先级 `signal.qty` > `signal.target_position`（按总资产比例调仓，只补差额）> 默认（买入 90% 可用 / 卖出清仓）；数量统一向下取整到整手 |
| **下单节流** | `autoTrade.minOrderIntervalMs`（默认 30s）内不重复下单 |
| **重复委托去重** | 同标的同方向已有 `pending` 委托时跳过，避免策略抖动刷单 |
| **成交回查** | 下单后轮询 3 次（间隔 1.5s）确认 `filled/cancelled/rejected` 终态并写执行日志 |
| **总开关** | `autoTrade.enabled=false` 时只算信号不下单（安全演练） |

配置示例（`config/default.json`）：

```json
"autoTrade": {
  "enabled": true,
  "allowOffHours": false,
  "minOrderIntervalMs": 30000,
  "tickMs": 15000,
  "schedTickMs": 20000,
  "calendarOverrides": {}
}
```

- `allowOffHours`：调试用，允许非交易时段下单（真实盘口会返回 501，用于验证链路）。
- 应用启动时 `ExecutorService.restoreRunning()` 会自动恢复 `status=running` 的策略，**关掉窗口再打开也会继续跑**，这是「无人值守」的持久化基础。

## 验证方式

```bash
# 1) 数据源单测（不启动 Electron）
node -e "const {EastmoneySimTradeDataSource}=require('./main/datasources/trade/eastmoney-sim');
(async()=>{const ds=new EastmoneySimTradeDataSource({});await ds.init();
console.log(await ds.getAccount());console.log(await ds.getPositions());ds.dispose();})()"

# 2) 界面验证：npm start → 左侧「交易」→ 账户卡片应显示「东财妙想模拟盘」
```

## 风险提示

- 交易通道涉及**真实资金**时（QMT 实盘）必须先确认 `tradeSource` 不是 mock，且策略风控（`maxOrderAmount` / `maxDailyTrades` / `maxPositionRatio`）已配置。
- 东财模拟盘 API Key 等同账号权限，只能放在 `.env`，不得写入 `config/default.json` 或提交到仓库。
