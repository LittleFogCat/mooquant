# 03 策略框架

> 本章节描述 mookquant 策略模块的统一事件驱动框架。回测与实盘共用同一份策略代码，支持灵活扩展与跨平台导出。

## 0301 · 策略框架架构

### 设计目标

1. **策略统一**：回测与实盘走同一个 `on_bar`，消除"回测赚钱实盘亏钱"的逻辑分歧。
2. **灵活扩展**：UI 直接编写策略代码自动注册，或丢 `.py` 到 `data/strategies/user/`，框架代码零修改。
3. **跨平台导出**：一键导出 QMT 单文件脚本，粘到客户端即可运行。

### 与 QMT/xtquant 的边界

- **xtquant 库**：纯数据/交易通道（xtdata 取行情、xttrader 下单），不提供策略框架。
- **QMT 客户端平台 API**（init/handlebar/ContextInfo）：绑死在 QMT 客户端进程，独立 Electron 应用用不了，仅作**设计参考**。
- **我们的策略框架**（`bridge/strategies/`）：自己实现，不依赖 QMT 客户端运行时，吸收 QMT init/after_init/handlebar 范式但用面向对象存状态。

### 目录结构

```
bridge/strategies/
├── base.py            策略基类 StrategyBase + Signal + PortfolioSignal + Context + slice_upto
├── indicators.py      技术指标库（MA/EMA/MACD/RSI/KDJ/BOLL）
├── registry.py        自动扫描注册（builtin/ + data/strategies/user/）
├── builtin/           内置策略（ma_cross/momentum/mean_reversion/equal_weight/intraday_t）
└── exporters/         平台导出器（qmt_exporter.py 适配壳包装）
```

### 核心接口

- `StrategyBase`：策略基类，子类实现 `on_bar(bar, ctx)`。生命周期 `on_init/on_after_init/on_bar/on_tick/on_stop` 对应 QMT init/after_init/handlebar/subscribe/stop。
- `Signal`：单标的信号 `{action, reason, strength, target_position, indicators, qty, lot_tag}`，`target_position` 对应 QMT `order_target_percent`；`qty`/`lot_tag` 为日内做T扩展（期望股数 / 底仓"core"·T仓"t"标签）。
- `Context`：跨平台最大公约数接口（`get_bars/order_target_percent/get_position` 等），策略只依赖它，不碰 xtquant。
- `registry.load_all()`：扫描 builtin/ 与 user/，自动 import 注册策略类。

### 多周期与日内支持（做T场景）

- `ctx.bars_by_period: {period: bars}`：多周期K线（如 `{"1d": [...], "5m": [...], "1m": [...]}`），由日内回测引擎/实盘执行器填充，均经 `slice_upto` 无前视切片。
- `slice_upto(bars_by_period, now_dt)`：按当前 1m bar 时刻切片各周期，只暴露「已完整走完」的 bar（当日进行中日线不暴露、进行中的 5m 桶不暴露），回测与实盘共用，杜绝未来函数。
- `ctx.intraday_vwap()`：当日分时均线（累计成交额/累计成交量，QMT 分时图黄线口径），带增量缓存；无 amount 数据时退化为 (o+h+l+c)/4 近似。
- `ctx.trading_day / ctx.intraday_pos`：当前交易日与当日第几根分钟bar。
- `ctx.account / ctx.position`：引擎注入的账户/持仓快照（cash/total_assets；core_shares/t_shares/sellable_*），策略据此测算仓位。

### 日内做T策略（intraday_t）

内置 `strategies/builtin/intraday_t.py`，trigger_mode="intraday"：

- 趋势门（日线，昨日及以前，无前视）：MA20 之上只做正T（先买后卖），之下只做反T（先卖后买），带缓冲带
- 分时触发（1m + vwap + 5m RSI）：偏离分时均线超阈值且 RSI 超卖/超买
- T仓管理：tRatio 资金比例、maxTCount 日内次数上限、14:50 后不开新仓（撮合层 14:55 强平兜底）
- 信号带 `qty + lot_tag`：`sell+core` 表示先卖底仓（反向T），`buy+t` 表示买回还原

### 数据流

```
回测: bars -> registry.get(type)(params) -> 逐 bar on_bar -> Signal -> 撮合
实盘: executor tick -> K线 -> HTTP /signal（优先）/ RPC strategy.signal（回退）-> 逐 bar on_bar -> Signal -> 下单
  壳策略(type=shell): executor tick -> K线 -> HTTP /signal（不传 strategy，自动用激活模型）-> Signal -> 下单
导出: registry.get(type) -> inspect.getsource -> 适配壳包装 -> QMT 脚本
```

三者调用的是**同一个策略类的同一个 on_bar**。

> **ML 策略**：ML 策略（继承 `MLStrategyBase`）在 `on_after_init` 中加载模型。
> 无显式 `model_id` 时自动使用模型管理中激活的模型，上层无需关心。
> HTTP `/signal` 和 stdio RPC 两条路径统一由 `MLStrategyBase` 处理激活模型逻辑。

## 0302 · 策略开发指南

### 写一个自定义策略

**方式一：UI 编写（推荐）**

1. 策略管理页点「+ 新建策略」
2. 类型下拉选「✚ 编写自定义策略...」
3. 填策略类型名 + 编写 Python 代码（内置模板，含均线策略示例）
4. 保存后自动注册为新的策略类型，即刻可在类型下拉中选用
5. 选用后参数表单根据 `params_schema` 自动生成，无需手写 JSON

> **源码编辑**：所有策略均可在编辑弹窗中查看和修改源码，保存时覆盖原策略类型。

> **复制策略**：列表中可复制当前策略实例，新 ID，草稿状态。

**方式二：手动丢文件**

1. 新建 `data/strategies/user/my_strategy.py`
2. 继承 `StrategyBase`，实现 `on_bar`：

```python
from strategies.base import StrategyBase, Signal
from strategies.indicators import sma

class MyStrategy(StrategyBase):
    name = "my_strategy"           # 唯一标识
    display_name = "我的策略"
    description = "策略说明"
    trigger_mode = "bar"
    params_schema = [              # UI 据此动态生成参数表单
        {"key": "period", "type": "int", "default": 20, "min": 2, "max": 250, "label": "周期"},
    ]

    def on_bar(self, bar, ctx):
        bars = ctx.bars
        period = int(self.params.get("period", 20))
        closes = [b["close"] for b in bars]
        if len(closes) < period + 1:
            return None
        ma = sma(closes, period)[-1]
        if ma is None:
            return None
        if closes[-1] > ma:
            return Signal(action="buy", reason="上穿均线", indicators={"ma": ma})
        if closes[-1] < ma:
            return Signal(action="sell", reason="下穿均线", indicators={"ma": ma})
        return None
```

3. 重启应用，策略自动注册，UI 可见。

> UI 编写方式无需重启，保存即注册。

### 关键约定

- `on_bar` 返回 `Signal` / `PortfolioSignal` / `None`（None 表示 hold）。
- 跨 bar 状态用 `self._state`（面向对象），不用全局变量。
- `on_after_init` 做数据预热/全量预计算（参考 QMT after_init）。
- `target_position` 用于调仓至比例策略（网格/风险平价），普通买卖用 `action=buy/sell`。
- 指标函数返回等长序列，取 `[-1]`（当前）`[-2]`（上一根）。

## 0303 · 导出 QMT 脚本

### 原理

适配壳包装模式：策略类源码原样保留（`inspect.getsource`），外面套 QMT 生命周期壳 + Context 适配层，生成单文件脚本。策略逻辑零修改，回测与 QMT 实盘跑同一份 `on_bar`。

### 使用

UI 策略列表点「导出」-> 弹窗展示脚本 -> 复制 -> 粘到 QMT 客户端「模型研究」新建模型 -> 运行/回测。

### 生成脚本结构

1. 头部 `#coding:gbk` + import
2. 框架兼容层（StrategyBase/Signal/指标内联，脱离 mookquant 包）
3. 策略定义（原样复制）
4. Context 适配层 `_Ctx`（QMT ContextInfo -> mookquant Context）
5. QMT 生命周期壳（init/handlebar/stop，调用 on_bar，信号转 order_target_percent/passorder）

### 限制

- 简单买卖/调仓策略 100% 通用。
- 用到 QMT 特有能力（板块成分股、L2 数据）的策略导出后需手动调整。
- 回测用 `order_target_percent`，实盘用 `passorder`（按 `do_back_test` 自动分支）。

## 0304 · 策略 Bridge 与动态注册

### 策略功能与行情数据源解耦

策略 RPC（`strategy.list/signal/export/add/delete`）通过独立的 **strategyBridge** 调用 Python 桥，不依赖行情数据源：

- **qmt 模式**：strategyBridge 复用行情数据源的 Python 进程
- **mock/auto 模式**：strategyBridge 独立 spawn `qmt_server.py`（策略 RPC 不需要 miniQMT 连接，`ping` 容错不抛异常）
- **Python 不可用**：strategyBridge = null，策略功能降级（返回空列表），行情不受影响

这样无论行情走 mock 还是 qmt，策略类型列表、自定义策略添加、信号计算、导出都能正常工作。

### strategy.add / strategy.delete RPC

| RPC | 参数 | 返回 | 说明 |
|-----|------|------|------|
| `strategy.add` | `{name, code}` | `{strategy: metadata}` | 写源码到 `data/strategies/user/{name}.py` 并即时注册 |
| `strategy.delete` | `{name}` | `{ok: true}` | 删除用户策略文件并注销注册 |
| `strategy.get_code` | `{name}` | `{code: source}` | 获取策略源码（builtin inspect / user file） |

- `name` 须为合法标识符（字母/数字/下划线，不以数字开头）；同名覆盖（含 builtin）
- `code` 须含 `StrategyBase` 子类，且类属性 `name` 与参数 `name` 一致
- 代码加载失败时返回详细错误（含 traceback），UI 直接展示

### IPC 通道

| IPC 通道 | 说明 |
|---------|------|
| `strategy:types` | 列出所有已注册策略类型元数据 |
| `strategy:addType` | 添加自定义策略类型（UI 编写代码） |
| `strategy:deleteType` | 删除用户策略类型 |
| `strategy:export` | 导出策略为目标平台脚本 |
| `strategy:getCode` | 获取策略源码（UI 编辑用） |

### params_schema 参数表单

UI 根据策略类型的 `params_schema` 自动生成参数输入表单：

| type | 控件 |
|------|------|
| `int` / `float` | number input（带 min/max/step） |
| `bool` | select（是/否） |
| `string` / 其他 | text input |

无 `params_schema` 的策略 fallback 到 JSON 编辑器。
