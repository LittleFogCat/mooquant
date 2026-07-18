# 03 策略框架

> 本章节描述 mookquant 策略模块的统一事件驱动框架。回测与实盘共用同一份策略代码，支持灵活扩展与跨平台导出。

## 0301 · 策略框架架构

### 设计目标

1. **策略统一**：回测与实盘走同一个 `on_bar`，消除"回测赚钱实盘亏钱"的逻辑分歧。
2. **灵活扩展**：丢一个 `.py` 到 `data/strategies/user/` 即新增策略，框架代码零修改。
3. **跨平台导出**：一键导出 QMT 单文件脚本，粘到客户端即可运行。

### 与 QMT/xtquant 的边界

- **xtquant 库**：纯数据/交易通道（xtdata 取行情、xttrader 下单），不提供策略框架。
- **QMT 客户端平台 API**（init/handlebar/ContextInfo）：绑死在 QMT 客户端进程，独立 Electron 应用用不了，仅作**设计参考**。
- **我们的策略框架**（`bridge/strategies/`）：自己实现，不依赖 QMT 客户端运行时，吸收 QMT init/after_init/handlebar 范式但用面向对象存状态。

### 目录结构

```
bridge/strategies/
├── base.py            策略基类 StrategyBase + Signal + PortfolioSignal + Context
├── indicators.py      技术指标库（MA/EMA/MACD/RSI/KDJ/BOLL）
├── registry.py        自动扫描注册（builtin/ + data/strategies/user/）
├── builtin/           内置策略（ma_cross/momentum/mean_reversion）
└── exporters/         平台导出器（qmt_exporter.py 适配壳包装）
```

### 核心接口

- `StrategyBase`：策略基类，子类实现 `on_bar(bar, ctx)`。生命周期 `on_init/on_after_init/on_bar/on_tick/on_stop` 对应 QMT init/after_init/handlebar/subscribe/stop。
- `Signal`：单标的信号 `{action, reason, strength, target_position, indicators}`，`target_position` 对应 QMT `order_target_percent`。
- `Context`：跨平台最大公约数接口（`get_bars/order_target_percent/get_position` 等），策略只依赖它，不碰 xtquant。
- `registry.load_all()`：扫描 builtin/ 与 user/，自动 import 注册策略类。

### 数据流

```
回测: bars -> registry.get(type)(params) -> 逐 bar on_bar -> Signal -> 撮合
实盘: executor tick -> K线 -> RPC strategy.signal -> 逐 bar on_bar -> Signal -> 下单
导出: registry.get(type) -> inspect.getsource -> 适配壳包装 -> QMT 脚本
```

三者调用的是**同一个策略类的同一个 on_bar**。

## 0302 · 策略开发指南

### 写一个自定义策略

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
