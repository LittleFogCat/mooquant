# 日内做 T 策略架构升级计划

> 日期：2026-08-27
> 版本基线：v0.1.27
> 目标：支持「多周期联动 + 底仓/T 仓分层 + A 股 T+1 约束」的日内做 T 策略，回测先行、实盘可延展
> 文档结构：需求分析 -> 概要设计 -> 详细设计 -> 方案评估 -> 实施计划

---

## 1. 需求分析

### 1.1 什么是「做 T」

A 股实行 T+1 制度：当日买入的股票次日才能卖出。因此日内回转交易只能依靠**已有底仓**实现：

```
正向做 T（先买后卖）：日内低点用 T 资金买入 → 反弹后卖出等量底仓（卖的是昨日前的仓位，合法）
反向做 T（先卖后买）：日内高点先卖出部分底仓 → 回落后买回等量股票（还原底仓数量）
```

核心诉求：**底仓数量日内保持不变（或日内还原），T 仓日内往复，赚取日内波动差价**。

### 1.2 现有架构的 Gap（逐条核对）

| # | 需求 | 现状 | Gap |
|---|------|------|-----|
| G1 | 多周期联动（日线定方向 / 5m 定买点 / 1m 定触发 / 分时均线） | `ctx.bars` 单一序列；回测引擎单循环单周期；`ctx.period` 硬编码 `"1d"`（backtest_engine.py:346） | 无多周期 Context；策略内取多周期无缓存重复计算 |
| G2 | 分钟级 K 线数据 | `datafeed.fetch_bars` 支持 period="1m"（datafeed.py:209-232），`db.py` 主键含 period；qmt_server 已有 5m/15m/30m/60m 聚合 | 分钟链路未被回测使用过；**datafeed `_df_to_bars` 丢弃 amount 字段**（datafeed.py:172-206）；mock 数据只生成日 K |
| G3 | T+1 语义（按「交易日」解禁而非按 bar 解禁） | 现有撮合循环「每根 bar 解禁一次」（backtest_engine.py:435-437），日线语义正确；分钟周期下每根分钟 bar 都解禁 = 灾难性错误 | 需按交易日粒度解禁；分钟回测必须重写解禁逻辑 |
| G3' | 涨跌停判定基准（昨收） | 现撮合循环用「上一根 bar 收盘」当 prev_close（backtest_engine:429-451），日线正确，分钟级完全错误 | 需按「交易日昨收」判定 |
| G4 | 底仓/T 仓分层仓位 | 账户模型只有单一 `position / avail_position / cost_price` | 无仓位批次（lot）概念，无法表达「卖的是哪一部分持仓」 |
| G5 | 做T策略信号 | 内置策略全部单周期（ma_cross/momentum/mean_reversion/portfolio_equal_weight） | 缺少多周期联动 + 分时均线 + T 仓管理的做 T 策略 |
| G6 | 绩效归因（做T收益 vs 底仓收益） | 指标只有整体 totalReturn 等 | 无法回答「做 T 到底有没有跑赢死拿不动」 |
| G7 | 实盘执行 | strategy.signal RPC 逐 bar 喂单序列（qmt_server.py:959-1001） | 无分钟驱动、无底仓同步；属 P2 延展，本期只保证接口不堵死 |
| G8 | UI | 回测页无周期选择 | period 参数已透传（backtest-viewmodel.js:23），需加选择器 |

### 1.3 非目标（本期不做）

- tick 级撮合（数据量与撮合复杂度过高，bar 级近似已够做 T 策略验证）
- 实盘自动做 T（executor 联动）——只预留接口
- ETF/可转债（T+0 品种）——单独场景，后续按需
- 融资融券做 T

### 1.4 关键约束

- A 股 T+1：当日买入不可卖；当日卖出资金可用不可取（模型忽略可取约束，现金按可用计）
- 整手约束：买卖均 100 股整数倍
- 交易时段：09:30-11:30 / 13:00-15:00（撮合循环按 bar 的 date 时间自然处理午休）
- 成本：佣金（最低5元）+ 印花税（卖出）+ 过户费（双向）+ 滑点
- 分钟数据存储量：单标的 60 个交易日 1m ≈ 14400 行（已验证 db 主键支持）
- 复权口径：统一 front_ratio（口径指纹 v3，不改动 → 不触发指纹变更）

---

## 2. 毆要设计

### 2.1 总体思路：**不新建引擎，扩展既有回测引擎为「多周期驱动 + 日内撮合」**

```
                        ┌──────────────────────────────────────────┐
                        │  backtest_engine.run_backtest(params)    │
                        │  period="1m" 时走新分支 _run_intraday_backtest │
                        └──────────────────────────────────────────┘
                                          │
      ┌───────────────────────────────────┼───────────────────────────────────┐
      ▼                                   ▼                                   ▼
┌─────────────┐              ┌──────────────────────────┐          ┌────────────────────┐
│ M1 数据层    │              │ M2 策略框架扩展            │          │ M3 日内撮合引擎      │
│ datafeed    │─ 多周期取数 ─→│ MultiPeriodContext       │          │ IntradayMatcher    │
│ 分钟链路补全  │              │ ctx.bars_by_period       │          │ · lot 批次仓位      │
│ amount 保留  │              │ 分时均线 cum_vol_ratio    │          │ · 交易日 T+1 解禁    │
│ 分钟 mock    │              │ 无前视对齐切片             │          │ · 昨收涨跌停        │
└─────────────┘              └──────────────────────────┘          │ · 分账户分账        │
                                          │                        └────────────────────┘
                                          ▼                                    │
                              ┌──────────────────────────┐                       │
                              │ M4 intraday_t 策略        │                       │
                              │ 日线趋势门 + 5m 触发 +    │                       │
                              │ 1m 分时均线 + T仓管理      │                       │
                              └──────────────────────────┘                       │
                                          │                                       │
                                          ▼                                       ▼
                              ┌──────────────────────────┐          ┌────────────────────┐
                              │ M5 UI：周期选择器 +        │          │ M6 端到端验证        │
                              │ 做T分账指标卡              │          │ mock 链路自测        │
                              └──────────────────────────┘          └────────────────────┘
```

### 2.2 核心设计决策（先给结论，详见 §4 详细设计）

| 决策点 | 结论 | 理由 |
|--------|------|------|
| D1 做T基周期 | **1m**（驱动周期，撮合与主信号） | 做T需观察分时均线细节；5m 太粗、tick 太重 |
| D2 多周期数据暴露方式 | **Context 上新增 `bars_by_period` 字典 + `get_bars(symbol, count, period)` 可覆盖实现**，不破坏旧接口 | 策略写法 `ctx.bars_by_period["1d"]`；旧策略零改动 |
| D3 涨跌停/T+1 时间基准 | 按交易日：`trading_day = date[:10]` | 分钟 bar 的涨跌停基准是**昨日收盘**，T+1 解禁在**次日开盘** |
| D4 底仓/T仓建模 | **lot 批次模型**：持仓 = [lot(底仓, frozen_until=t+1|永不), lot(T仓买入, frozen_until=t+1)]；做T = 买新 lot + 卖旧 lot | 表达力最强，天然支持先卖后买（卖底仓 lot 再买回新 lot） |
| D5 底仓来源 | 回测参数 `basePositionShares`（默认 0），回测第一天以开盘价自动建底仓 | 免去用户手工传持仓明细 |
| D6 做T收益分账 | 账户分两层：core（底仓市值）+ tCash（T资金）；绩效指标输出「做T收益 / 底仓收益 / 总收益」 | G6 归因诉求 |
| D7 无前视对齐 | 多周期切片按 `bar.datetime <= 当前1m bar 时间` 过滤（含当日未完成日线/5m bar 的部分聚合 bar 需丢弃或标记） | 防未来函数是量化回测生命线 |
| D8 与旧引擎关系 | `period="1m"` 时走独立分支 `_run_intraday_backtest`，日线策略与组合路径**零改动** | 隔离风险，老功能回归安全 |
| D9 策略兼容性 | `StrategyBase.on_bar(bar, ctx)` 签名不变；新增 `on_intraday_bar` 可选钩子不必要——统一仍用 on_bar，靠 ctx.period 区分 | 最小侵入 |
| D10 实盘预留 | Signal 增加 `lot_tag`（"core"/"t"/None）与 `qty` 字段（可选下单股数），执行层可据此实现底仓/T仓分别下单 | 不实现执行器，只定契约 |

---

## 3. 需求-设计映射

| Gap | 由哪个模块解决 | 验收口径 |
|-----|---------------|---------|
| G1 多周期 | M2 `bars_by_period` + 无前视切片 | 策略可同时读 1d/5m/1m 且无前视（单测：未来bar不泄漏） |
| G2 分钟数据 | M1 datafeed 补全 | 1m 数据可取/可缓存/amount 在；分钟 mock 可生成 |
| G3 T+1 语义 | M3 lot + 交易日解禁 | 当日买入的 T 仓当日卖 → 拒单并记 skipped |
| G3' 涨跌停 | M3 昨收基准 | 分钟 bar 涨跌停判定与日线口径一致（用昨收） |
| G4 底仓/T仓 | M3 lot 模型 + M4 策略 | 回测后底仓数量还原为初始值（先卖后买场景） |
| G5 做T策略 | M4 | 策略在 registry 可见，params_schema 合法 |
| G6 分账 | M3+M5 | metrics 输出 tPnl / corePnl / totalPnl |
| G7 实盘预留 | M2 Signal 契约 | lot_tag/qty 字段序列化存在 |
| G8 UI | M5 | 回测页可选 1m 周期 + 做T分账卡片渲染 |

---

## 4. 详细设计

### 4.1 M1 数据层（bridge/data/datafeed.py、_shared.py）

**4.1.1 `fetch_bars` 分钟链路补全**

- `_df_to_bars`（datafeed.py:172）增加 amount 保留：`"amount": float(row.get("amount") or 0)`。
- 分钟 bar 的 date 字段格式统一为 `"YYYY-MM-DD HH:MM"`（xtquant 12位数字索引已由 `_format_date_str` 处理，datafeed 内部同样加一段格式归一）。
- `validate_bars` 对分钟周期跳过「>15 自然日缺口」检查（午休/停牌分钟数据天然有洞）。

**4.1.2 分钟 mock 生成器（_shared.py）**

```python
def generate_mock_minute_bars(symbol, start_date, end_date, period="1m"):
    """确定性随机游走分钟K线：每日 240 根 1m bar（09:30-11:30, 13:00-15:00），
    日内 U 型波动（开收盘波动大、午间平缓），日间漂移由日种子决定。
    用于无 miniQMT 环境的回测链路自测。"""
```

**4.1.3 取数口径指纹**

不修改（volume=股、front_ratio、字段不变），`DATA_SPEC_VERSION` 保持 3。理由：新增 amount 字段属可选字段，旧缓存无 amount 时取 0，不构成口径漂移。

### 4.2 M2 策略框架（bridge/strategies/base.py）

**4.2.1 Context 扩展（向后兼容）**

```python
class Context:
    def __init__(self):
        ...
        self.bars_by_period: Dict[str, List[dict]] = {}  # 新增：多周期K线 {period: bars}
        self.trading_day: str = ""        # 新增：当前交易日 "YYYY-MM-DD"（分钟驱动时）
        self.intraday_pos: int = 0        # 新增：当日第几根分钟 bar（0=集合竞价后第一根）

    # 新增：分时均线（当日均价线，QMT 分时图黄线口径）
    def intraday_vwap(self, symbol: str = "") -> Optional[float]:
        """当日累计 amount / 累计 volume（分时均线）。数据无 amount 时退化为 close 均价。"""

    # 新增：持仓查询返回 lot 视图（实盘/回测各自实现）
    def get_position(self, symbol): ...
```

**4.2.2 无前视对齐切片（关键正确性代码）**

```python
def slice_upto(bars_by_period: Dict[str, List[dict]], now_dt: str) -> Dict[str, List[dict]]:
    """按当前 1m bar 时间戳切片多周期数据，严格无前视。

    规则：
      - 1m bars: date <= now
      - 5m/15m/…: 只保留 date < now 所属周期桶起点 的**已完成** bar；
        当前所在桶的进行中聚合 bar 一律不暴露（防止用「未来 5 分钟」信息）
      - 1d: date < 今天（今日进行中的日线 bar 不暴露）；预热期(<N日)返回空
    """
```

实现放 `strategies/base.py` 供回测与实盘共用；实现细节：分钟周期桶起点 = `now - (minutes(now_bucket_idx % n))`。今日日线不暴露 → 策略判断趋势只能用昨日及以前日线（真实世界同样如此）。

**4.2.3 Signal 扩展（向后兼容）**

```python
@dataclass
class Signal:
    action: str            # "buy" | "sell" | "hold"
    ...
    qty: Optional[int] = None      # 新增：期望股数（None=按策略默认仓位逻辑）
    lot_tag: Optional[str] = None  # 新增：仓位标签 "core" | "t" | None（None=不指定）
```

### 4..qty3 M3 日内撮合引擎（backtest_engine.py 新增分支）

**4.3.1 入口分流**

```python
def run_backtest(params):
    period = params.get("period", "1d")
    if period in ("1m", "5m", "15m", "30m", "60m"):
        return _run_intraday_backtest(params)   # 新分支
    ...  # 既有日线/组合路径不动
```

**4.3.2 lot 批次仓位模型**

```python
@dataclass
class Lot:
    shares: int            # 本批股数
    cost: float            # 本批成本价
    entry_dt: str          # 入场时间（分钟bar时间戳）
    available_day: str     # 最早可卖交易日（T+1：次日；底仓=回测起始日，即可立即卖）
    tag: str               # "core"（底仓）| "t"（T仓）
```

- 可卖判定：`lot.available_day <= 当前交易日` 且非跌停。
- 卖出优先级：**core 底仓永远最先卖**（释放 T 资金）、FIFO 其余。理由：底仓是「既定事实」，先卖后买还原底仓数量；若先卖 T 仓可能把当日买入的 T 仓冻结住。
  - 修正：做 T 场景「先卖后买」卖的就是底仓 lot（还原式），「先买后卖」卖出时也优先卖最老可用 lot（含底仓）。实现按 tag 无差别 FIFO + available_day 过滤即可，无需区分谁先谁后——策略层用 lot_tag 表达意图，撮合层按 FIFO+可用性执行。

**4.3.3 账户分账**

```python
cash            # T 资金（含初始资金 - 建底仓花费 + 卖出净得）
core_shares     # 底仓股数（回测首日开盘买入 basePositionShares）
core_cost       # 底仓成本
t_lots: [Lot]   # T 仓批次
```

- 做T收益 = T 仓已实现盈亏 + 卖底仓买回的差价（见 4.3.4 归因公式）。
- 净值曲线 = cash + Σ(lot 市值) 按每根 1m bar 收盘估值。

**4.4.4 涨跌停 / T+1 / 整手 / 成本（复用现有规则，改基准）**

- 涨跌停：`prev_day_close`（交易日 昨收）→ `is_limit_up/down`，涨停禁买、跌停禁卖。
- T+1：当日买入 lot 的 `available_day = 明交易日`；底仓 lot `available_day = 回测起始日`。
- 整手/tick 取整/最小佣金/滑点：复用现有常量与函数。
- 成交量约束：买入量 ≤ 当根 1m bar 成交量 × 25%（做T单笔通常小，不会触顶）。

**4.3.5 信号执行优先级（每个 1m bar）**

```
1. 风控强制退出（止损/止盈，按 lot 或整体成本）——复用 RiskController，作用于 T 仓
2. Signal(action=buy, lot_tag="t")  → 用现金买新 T lot（受现金/涨跌停/整手约束）
3. Signal(action=sell, lot_tag="t"/"core") → 卖可用 lot（FIFO，受 T+1/跌停约束）
4. 收盘前 14:55 强制平 T 仓（参数 forceEodClose=true 默认开启）——日内不隔夜 T 仓
5. 14:57 后不再接受新开 T 仓信号（尾盘流动性差）
```

### 4.4 M4 做T策略（strategies/builtin/intraday_t.py）

**信号框架（教科书式正反 T）**

```
趋势门（日线，用昨日及以前日线）：
  MA20 日线之上 → 只做正 T（先买后卖）
  MA20 之下 → 只做反 T（先卖后买）
  MA20 附近（±band%）→ 观望

分时触发（1m + 分时均线 vwap）：
  正T买点：price < vwap × (1 - buyDev%) 且 5m RSI < 30（超卖）
           或 price 上穿 vwap（回踩确认，可选模式）
  正T卖点：price > vwap × (1 + sellDev%) 或 5m RSI > 70，卖出等量可用 lot
  反T卖点：price > vwap × (1 + sellDev%) 且 5m RSI > 70 → 先卖底仓
  反T买点：price < vwap × (1 - buyDev%) → 买回等量，还原底仓

T仓管理：
  T仓上限 = tRatio × 总资产（默认 30%）
  单次做T量 = min(tQty, 可用现金或可卖底仓)
  日内做T次数上限 maxTCount（默认 4，防过度交易）
  14:55 强制平T仓/还原底仓（由撮合层 forceEodClose 承担，策略只需不再开新仓）

params_schema:
  dailyMaPeriod   20   日线趋势门均线
  trendBand       0.02 趋势门缓冲带
  vwapDevBuy      0.005 低于分时均线买入偏离
  vwapDevSell     0.005 高于分时均线卖出偏离
  rsiPeriod       14   5m RSI 周期
  rsiOversold     30
  rsiOverbought   70
  tRatio          0.3  T资金占总资产比
  maxTCount       4    日内做T次数上限
```

**策略状态机（self._state）**

```
IDLE → (触发买点/卖点) → T_OPEN(正) / T_SOLD(反) → 对冲信号 → IDLE
每个交易日重置 t_count、日初底仓快照（用于还原校验）
策略只发信号，仓位约束由撮合层兜底
``。

### 4.5 M5 UI（renderer）

- 回测配置卡新增「K线周期」下拉（1d / 1m / 5m / 15m / 30m / 60m），默认 1d 不变；分钟周期时展示提示「日内回测：建议区间 ≤ 3 个月」。
- 结果页新增「做T分账」卡片（period=分钟 且策略含 lot_tag 输出时展示）：
  - 做T收益 tPnl / 底仓收益 corePnl / 总收益 / 做T次数 / 日均做T次数 / 每笔平均做T盈亏
- backtest-viewmodel state 增加 period 字段（已有默认值，补 UI 控件与提交字段）。

### period="1m" 参数汇总（backtest.run params 新增字段）

| 字段 | 默认 | 说明 |
|------|------|------|
| period | "1d" | 分钟周期触发日内分支 |
| basePositionShares | 0 | 底仓股数（0=纯日内无底仓，做T需>0） |
| forceEodClose | true | 14:55 强制平T仓 |
| tCapitalRatio | 策略参数 | T资金比例（策略层管理） |

### 4.6 M6 验证方案

1. **单测脚本（无 pytest 依赖，直接 python 跑）** `bridge/_test_intraday.py`：
   - lot 可卖判定（T+1 边界：当日买不可卖、次日可卖）
   - slice_upto 无前视（构造未来数据断言不泄漏）
   - 分时均线计算（手工造 3 根 bar 验证公式）
   - 涨跌停昨收基准（造 10% 涨停分钟序列验证拒单）
2. **mock 端到端**：`generate_mock_minute_bars` + intraday_t 策略 + allowMock 回测跑通，指标齐全、底仓数量还原校验（先卖后买路径）。
3. **回归**：日线 ma_cross 回测结果与改前一致（同一份数据 diff metrics）。
4. **真实数据冒烟**：miniQMT 在线时 sh600519 近 5 个交易日 1m 回测。

---

## 5. 方案评估

### 5.1 技术风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 分钟数据量大（长区间回测慢/缓存膨胀） | 中 | UI 提示区间建议；SQLite 已有主键约束；进度推送已有 |
| 无前视切片 bug（未来函数） | 高 | slice_upto 单测覆盖 + 「今日日线不暴露」规则 + 5m 桶进行中 bar 丢弃 |
| T+1/涨跌停基准错误（沿用日线实现） | 高 | 新代码与旧代码物理隔离（独立分支），T+1/昨收语义单测覆盖 |
| 与旧引擎耦合回归 | 中 | 旧路径零改动原则；回归 diff 验证 |
| lot FIFO 卖出与策略意图不符 | 低 | 策略用 lot_tag 表达意图，撮合按可用性 FIFO，冲突时以「能成交」优先 |
| 分钟 mock 与真实分时形态差异 | 中 | mock 仅用于链路自测，正式回测默认拒绝 mock（D0.3 规则沿用） |
| 回测末尾 T 仓未平（跨夜） | 低 | forceEodClose 默认开；关闭时净值按市值估值并提示 |

### 5.2 备选方案对比

| 方案 | 描述 | 优劣 | 结论 |
|------|------|------|------|
| A（选定） | 既有引擎加日内分支 + lot 模型 | 复用成本/风控/指标体系；隔离性好；改动集中 | ✅ |
| B | 新建独立日内回测引擎文件 | 彻底隔离但复制大量撮合/指标代码，维护双份 | 否 |
| C | 改造现有单标的循环兼容多周期 | 单循环内嵌多周期切换，旧路径被污染，回归风险大 | 否 |
| D | tick 级撮合 | 精度最高但数据/性能/复杂度爆炸，且 D0.1 已声明 tick 未实现 | 本期否 |

### 5.3 工作量估算

| 模块 | 文件 | 预估 |
|------|------|------|
| M1 | datafeed.py / _shared.py | 0.5d |
| M2 | strategies/base.py | 0.5d |
| M3 | backtest_engine.py | 1.5d |
| M4 | strategies/builtin/intraday_t.py | 1d |
| M5 | renderer 3 个文件 | 0.5d |
| M6 | 测试 + 文档 + 版本号 | 0.5d |
| 合计 | | ~4.5d |

### 5.4 兼容性声明

- 旧日线回测：零行为变化（分支隔离）。
- 旧策略：StrategyBase 接口未删未改语义，新增字段全部带默认值。
- 数据缓存：schema 不变；分钟缓存新增行属增量。
- 导出 QMT：intraday_t 策略暂不导出（QMT 导出器面向日线 handlebar），文档注明。

---

## 6. 实施计划（执行顺序）

| 步骤 | 内容 | 产出 | 验证 |
|------|------|------|------|
| S1 | M1 数据层 | datafeed amount/分钟格式、mock 分钟生成器 | python 直跑冒烟 |
| S2 | M2 框架扩展 | bars_by_period / slice_upto / vwap / Signal 扩展 | _test_intraday.py 单测 |
| S3 | M3 撮合引擎 | _run_intraday_backtest + Lot | 单测 + mock 端到端 |
| S4 | M4 策略 | intraday_t.py | mock 回测出信号 |
| S5 | M5 UI | 周期选择 + 分账卡片 | 界面冒烟 |
| S6 | M6 收尾 | 回归验证 + config.js patch +1 + 知识库 03-strategy/05-backtest 更新 | 全链路 |
