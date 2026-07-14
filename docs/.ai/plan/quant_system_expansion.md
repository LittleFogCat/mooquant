# 量化交易系统扩展计划

> 日期：2026-07-13
> 目标：将 mookquant 从单一行情查询工具扩展为完整的量化交易系统

## 一、总体架构

在现有 MVVM + 分层架构基础上扩展，保持严格分层不变。

```
渲染层 (renderer/)
  ├── 导航栏 (navbar)              [新增]
  ├── 页面：行情 (quote)            [现有，重构为页面模块]
  ├── 页面：策略 (strategy)         [新增]
  ├── 页面：回测 (backtest)         [新增]
  └── 页面：交易 (trade)            [新增]

主进程 (main/)
  ├── ipc/index.js                 [扩展，注册新路由]
  ├── services/
  │   ├── quote-service.js         [现有]
  │   ├── strategy-service.js      [新增] 策略 CRUD + 文件持久化
  │   ├── backtest-service.js      [新增] 回测编排
  │   └── trade-service.js         [新增] 交易编排
  └── datasources/
      ├── (现有行情数据源)
      ├── backtest-engine.js       [新增] spawn Python 回测引擎
      └── trade/                   [新增]
          ├── index.js             工厂
          ├── mock-trade.js        模拟交易
          └── qmt-trade.js         QMT 交易（spawn Python）

Python 桥 (bridge/)
  ├── qmt_server.py                [现有，扩展交易方法]
  └── backtest_engine.py           [新增] 回测引擎

数据持久化
  ├── data/strategies/             策略文件 (JSON)
  └── data/backtest_results/       回测结果 (JSON)
```

## 二、模块详细设计

### Phase 1: 导航基础设施

**目标**：将单页应用改为多页面导航结构。

- `renderer/index.html` 重构：侧边栏 + 内容区
- `renderer/css/app.css` 扩展：导航栏样式、页面切换
- `renderer/js/bootstrap.js` 重构：页面路由 + 各页面初始化
- `renderer/js/router.js` [新增]：简易 hash 路由

### Phase 2: 策略管理

**策略数据模型**：
```json
{
  "id": "uuid",
  "name": "双均线策略",
  "description": "MA5 上穿 MA20 买入",
  "type": "ma_cross | momentum | mean_reversion | custom",
  "symbols": ["sh600519"],
  "params": { "fast": 5, "slow": 20, "stopLoss": 0.05 },
  "status": "draft | active | archived",
  "createdAt": "ISO",
  "updatedAt": "ISO"
}
```

**实现**：
- `main/services/strategy-service.js` - CRUD + JSON 文件持久化到 `data/strategies/`
- `main/ipc/index.js` 注册：`strategy:list/create/update/delete`
- `renderer/js/viewmodels/strategy-viewmodel.js`
- `renderer/js/views/strategy-view.js` - 策略列表 + 编辑面板
- `preload.js` 暴露 `facade.strategy.*`

### Phase 3: 回测系统

**回测引擎 (Python)**：
- `bridge/backtest_engine.py` - stdio JSON-RPC
- 方法：`backtest.run`（输入策略+参数+时间范围，输出绩效指标+交易记录+净值曲线）
- 内置策略实现：双均线、动量、均值回归
- 撮合模拟：按日 K 线收盘价撮合，支持滑点、手续费

**回测绩效指标**：
- 总收益率、年化收益率、最大回撤、夏普比率、胜率、盈亏比
- 净值曲线数据、每笔交易记录

**实现**：
- `main/datasources/backtest-engine.js` - spawn Python 回测引擎
- `main/services/backtest-service.js` - 回测编排
- `renderer/js/viewmodels/backtest-viewmodel.js`
- `renderer/js/views/backtest-view.js` - 配置面板 + 结果展示

### Phase 4: 交易接口

**交易数据源抽象**：
```
interface TradeDataSource {
  mode: string
  description: string
  async placeOrder(order): { orderId }
  async cancelOrder(orderId): { success }
  async getPositions(): [position]
  async getOrders(): [order]
  async getAccount(): { balance, available }
  dispose(): void
}
```

**实现**：
- `main/datasources/trade/mock-trade.js` - 内存模拟交易
- `main/datasources/trade/qmt-trade.js` - 通过 qmt_server.py 交易
- `bridge/qmt_server.py` 扩展：`trade.order/cancel/positions/orders/account`
- `main/services/trade-service.js` - 交易编排 + 风控
- `renderer/js/viewmodels/trade-viewmodel.js`
- `renderer/js/views/trade-view.js` - 下单面板 + 持仓/委托/成交列表

## 三、实施顺序

1. Phase 1: 导航基础设施 ← 立即执行
2. Phase 2: 策略管理 ← 立即执行
3. Phase 3: 回测系统 ← 立即执行
4. Phase 4: 交易接口 ← 立即执行

每个 Phase 完成后验证可运行，不留半成品。
