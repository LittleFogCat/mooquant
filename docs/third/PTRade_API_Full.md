## Ptrade 量化交易 API 全量接口文档（工程内版本）

### 1. 文档定位

- **用途**：本文件用于工程内统一说明 Ptrade 量化交易接口，供开发、测试、运维共同参考。
- **放置位置建议**：`/docs/` 或项目根目录（当前文件名：`PTRade_API_Full.md`）。
- **覆盖内容**：
  - 运行框架与生命周期
  - 核心对象与持久化规则
  - API 按类别的函数签名、参数与返回结构
  - 常用数据字典与状态枚举

> 说明：接口内容基于官网 `ptradeapi.com`，不同券商 / Python 版本（3.5 / 3.11）在返回结构上可能有细节差异，以线上环境实际返回为准。

---

### 2. 运行框架与生命周期（工程约定）

- **统一约定**：所有 Ptrade 策略必须至少实现：
  - `initialize(context)`
  - `handle_data(context, data)`
- **推荐统一模板**：

```python
def initialize(context):
    """
    策略初始化：
    - 配置股票池 / 基准
    - 配置回测或交易参数
    - 初始化全局对象 g
    - 注册定时任务（如 run_daily / run_interval）
    """
    pass


def before_trading_start(context, data):
    """
    盘前钩子（可选）：
    - 每个交易日开盘前执行一次
    - 常用于：每天重建股票池 / 拉取财务数据 / 盘前风控检查
    """
    pass


def handle_data(context, data):
    """
    盘中主函数（必选）：
    - 日频：每天一次
    - 分钟频：每分钟一次
    - 主要交易逻辑入口
    """
    pass


def after_trading_end(context, data):
    """
    盘后钩子（可选）：
    - 每个交易日收盘后执行一次
    - 常用于：输出当日统计 / 持久化自定义数据
    """
    pass
```

---

### 3. 核心对象与持久化约定

#### 3.1 `context` 对象

- **工程内使用约定**：
  - `context.portfolio`：组合信息
    - `cash: float`：可用资金
    - `positions: dict[str, Position]`：持仓字典
  - `context.blotter.current_dt: datetime`：当前回测/交易时间

#### 3.2 全局对象 `g`

- 用于在各函数间共享状态，例如：

```python
g.security = '600570.SS'
g.flags = {}
g.trade_cb_list = []
```

#### 3.3 核心业务对象速查表（来自 `5、PTrade数据结构.json`）

- **`g` - 全局对象**
  - **使用场景**：仅支持回测 / 交易模块。
  - **作用**：在不同函数（包括自定义函数）之间共享策略级全局数据，例如股票池、状态标记等。
  - **典型用法**：
    - `g.security = "600570.SS"`：记录当前股票池或主交易标的。
    - `g.count = 1`、`g.flag = 0`：保存跨函数使用的计数器 / 标志位等。

- **`Context` - 上下文对象**
  - **使用场景**：仅支持回测 / 交易模块。
  - **说明**：业务上下文对象，承载本次回测 / 交易周期的环境信息。
  - **关键字段**（节选）：
    - `capital_base`：起始资金。
    - `previous_date`：前一个交易日。
    - `sim_params.capital_base / data_frequency`：仿真参数与数据频率。
    - `portfolio`：账户信息，详见下文 `Portfolio` 对象。
    - `blotter.current_dt`：当前单位时间起始时间，`datetime.datetime`（北京时间）。
  - **注意事项**：`portfolio` 的数据更新周期在交易环境下为若干秒级，具体以券商配置为准。

- **`BarData` - K 线数据对象**
  - **使用场景**：仅支持回测 / 交易模块，在 `handle_data(context, data)` 中通过 `data[sid]` 访问。
  - **说明**：表示一个单位时间内的单标的 K 线行情。
  - **常用字段**：
    - `symbol` / `name`：标的代码 / 名称。
    - `dt` / `datetime`：当前周期时间。
    - `is_open`：停牌标志，`0`–停牌，`1`–非停牌。
    - `open / close / high / low / price`：本周期开收盘价、最高价、最低价、最新价。
    - `volume / money`：本周期成交量 / 成交额。
    - `preclose / high_limit / low_limit / unlimited`：昨收、涨跌停价、是否无涨跌停限制（仅日线返回）。
  - **注意事项**：分钟频率下 `preclose / high_limit / low_limit / unlimited` 填充为 `0.0`。

- **`Portfolio` - 资产对象**
  - **使用场景**：仅支持回测 / 交易模块，通过 `context.portfolio` 访问。
  - **说明**：聚合账户当前资金与全部标的持仓的汇总信息。
  - **股票账户常用字段**：
    - `cash`：当前可用资金（不含冻结）。
    - `positions`：当前持有标的字典，`{sid: Position}`。
    - `portfolio_value`：持仓市值 + 现金总价值。
    - `positions_value`：持仓市值。
    - `capital_used`：已使用现金。
    - `returns / pnl / start_date`：收益率、收益额、开始时间等。
  - **期货账户补充字段**：`margin`（保证金）等。
  - **注意事项**：交易中该对象通常以 6 秒为一个更新周期（以券商配置为准），更新时间范围为 `before_trading_start` 至 `after_trading_end`。

- **`Position` - 持仓对象**
  - **使用场景**：仅支持回测 / 交易模块，可通过 `get_position` / `get_positions` 或 `context.portfolio.positions[sid]` 获取。
  - **股票账户常用字段**：
    - `sid`：标的代码。
    - `enable_amount`：可用数量。
    - `amount`：总持仓数量。
    - `last_sale_price`：最新价。
    - `cost_basis`：持仓成本价。
    - `today_amount`：今日开仓数量。
    - `business_type`：持仓类型。
    - `update_time`：最近一次从柜台同步的更新时间。
  - **期货账户扩展字段**：多空仓分拆（`long_amount / short_amount`）、保证金 `margin`、合约乘数 `contract_multiplier` 等。
  - **注意事项**：交易场景下，持仓信息是周期性从柜台同步的，`update_time` 字段可用于判断“是否最新”；回测场景下该字段为 `None`。

- **`Order` - 委托对象**
  - **使用场景**：仅支持回测 / 交易模块。
  - **说明**：封装一次买卖订单信息；可由 `order*` 系列函数返回，也可由 `get_orders()` 等函数获取。
  - **股票账户常用字段**：
    - `id`：订单号（策略内部标识）。
    - `dt / created`：订单产生 / 创建时间。
    - `symbol`：标的代码。
    - `amount`：委托数量（买入为正，卖出为负）。
    - `limit`：委托价（限价单）。
    - `filled`：成交数量。
    - `entrust_no / cancel_entrust_no`：委托编号 / 撤单委托编号。
    - `priceGear`：盘口档位。
    - `status`：委托状态，详见第 9 章“数据字典与状态枚举”。
  - **注意事项**：
    - 回测环境下 `entrust_no / cancel_entrust_no` 为空。
    - 交易环境下，订单状态既会由定时查询更新，也会由柜台主推更新；撤单时 `cancel_entrust_no` 会填入撤单编号。

#### 3.3 持久化规则（务必遵守）

- 框架会自动对 **`g` 中可 pickle 的字段** 做持久化：
  - 持久化触发点：`before_trading_start` / `handle_data` / `after_trading_end` 执行后。
  - 环境重启后：先执行 `initialize`，再用持久化信息覆盖 `g`。
- **工程约定**：
  - 不能被序列化的对象（文件句柄 / 网络连接 / 类实例等）必须挂在以 `__` 开头的字段，如：

    ```python
    class SomeClient: ...

    def initialize(context):
        g.__client = SomeClient()  # 不被持久化
    ```

  - 策略内持久化自定义数据时，统一使用：
    - `get_research_path()` 获取目录
    - `pickle` 进行序列化

---

### 4. 设置类 API（按工程使用场景整理）

#### 4.1 股票池与基准

- **`set_universe(security_list)`**
  - **模块**：回测 / 交易
  - **参数**：
    - `security_list: str | list[str]`：单个代码或代码列表。
  - **返回**：`None`
  - **工程约定**：初始化时必须明确设定，禁止依赖默认 pool。

- **`set_benchmark(sid)`**
  - **模块**：回测 / 交易
  - **参数**：
    - `sid: str`：指数 / 股票 / ETF 代码（如 `'000300.SS'`）。
  - **返回**：`None`
  - **工程约定**：回测策略必须设置基准，否则统一默认 `000300.SS`。

#### 4.2 回测参数（仅回测）

- **`set_commission(commission_ratio=0.0003, min_commission=5.0, type="STOCK")`**
  - **模块**：仅回测
  - **参数**：
    - `commission_ratio: float`：佣金费率
    - `min_commission: float`：单笔最低佣金
    - `type: str`：`"STOCK" | "ETF" | "LOF"`
  - **返回**：`None`
  - **注意事项**：
    - 回测结果与真实成交会因佣金设置有偏差，工程内建议使用与实盘账号一致的费率配置。
    - 经手费由系统固定追加，策略内无需单独计入。

- **`set_fixed_slippage(fixedslippage=0.0)`**
  - **模块**：仅回测
  - 固定滑点，最终成交价 = 委托价 ± `fixedslippage/2`。
  - **注意事项**：
    - 固定滑点与百分比滑点不要同时设置，避免双重放大。
    - 高频策略请优先用固定滑点，低频策略可选择百分比滑点模拟冲击成本。

- **`set_slippage(slippage=0.1)`**
  - **模块**：仅回测
  - 百分比滑点，成交价 = 委托价 ± 委托价 × `slippage/2`。
  - **注意事项**：
    - 百分比按当期价格计算，回测暴涨/暴跌区间会产生非常大的滑点，参数不宜过大。

- **`set_volume_ratio(volume_ratio=0.25)`**
  - **模块**：仅回测
  - 控制单笔最大成交量占比。
  - **注意事项**：
    - 高频大额调仓策略，为避免“虚假可成交量”，工程内建议 `volume_ratio <= 0.3`。

- **`set_limit_mode(limit_mode='LIMIT')`**
  - **模块**：仅回测
  - `'LIMIT'`：成交量不超过真实成交量；
  - `'UNLIMITED'`：不限制。

- **`set_yesterday_position(poslist)`**
  - **模块**：仅回测
  - **参数结构**：

    ```python
    poslist = [
        {
            'sid': '600570.SS',
            'amount': '1000',
            'enable_amount': '800',
            'cost_basis': '50.0',
        },
        ...
    ]
    ```

---

### 5. 交易行为参数（仅交易）

#### 5.1 `set_parameters(**kwargs)`

- **用途**：统一配置策略在交易服务器重启、节假日等场景下的行为。
- **模块**：仅交易
- **常用参数**（字符串）：
  - `holiday_not_do_before`: `"0"`（默认，执行） / `"1"`（节假日不执行 `before_trading_start`）
  - `tick_data_no_l2`: `"0"`（默认，tick_data 含 L2） / `"1"`（不包含 L2，降低负载）
  - `receive_other_response`: `"0"`（默认，不接收） / `"1"`（接收策略外主推）
  - `receive_cancel_response`: `"0"`（默认，不接收） / `"1"`（接收撤单主推）
  - `not_restart_trade`: `"0"`（默认，服务器重启自动重拉策略） / `"1"`（不重拉）
  - `server_restart_not_do_before`: `"0"`（默认，重拉时执行 `before_trading_start`） / `"1"`（同日不再执行）

- **工程建议**：

```python
set_parameters(
    holiday_not_do_before="1",
    tick_data_no_l2="1",
    receive_other_response="1",
    receive_cancel_response="1",
    not_restart_trade="1",
    server_restart_not_do_before="1",
)
```

> **注意事项（强制约定）**：
> - 所有“实盘/模拟盘托管策略”必须配置 `not_restart_trade="1"` 与 `server_restart_not_do_before="1"`，防止服务器重启后重复建仓或重复发单。
> - 当开启 `receive_other_response="1"` 时，需要在 `on_order_response` / `on_trade_response` 中根据 `order_id` 是否为空区分“策略内委托”与“柜台外委托”，否则容易在他人下单时误触发策略逻辑。

#### 5.2 `set_email_info(email_address, smtp_code, email_subject)`

- **用途**：交易异常终止邮件告警（视券商是否开启）。
- **返回**：`bool`（是否设置成功）。
- **模块**：仅交易
- **注意事项**：
  - SMTP 授权码一般与登录密码不同，必须到邮箱设置中单独开通。
  - 邮件发送依赖券商环境配置，若长期未收到，可通过 `permission_test()` 或联系券商确认。

---

### 6. 定时调度 API

#### 6.1 `run_daily(context, func, time='9:31')`

- **说明**：以“日”为单位，在指定时间执行 `func(context)`。
- **模块**：回测 / 交易
- **工程约定**：
  - 只能在 `initialize` 中调用；
  - 所有日级定时任务必须通过 `run_daily` 注册，不允许自行 `while True + sleep`。
  - `func` 签名必须为 `func(context)`，不可额外添加参数。
- **使用示例**：

```python
def initialize(context):
    # 每个交易日 09:45 做一次选股
    run_daily(context, rebalance, time='9:45')


def rebalance(context):
    # 注意：run_daily 内部无 data 参数，如需行情请在函数内部调用 get_price / get_snapshot
    stocks = select_stocks()
    set_universe(stocks)
```

#### 6.2 `run_interval(context, func, seconds=10)`（仅交易）

- **说明**：以秒为单位周期执行，最小 3 秒。
- **模块**：仅交易
- **工程约定**：高频任务必须控制接口调用频率，避免与 `handle_data` 同时高频访问 L2 行情。
  - 一般不推荐多个 `run_interval` 并行访问 L2，建议统一封装为一个调度函数内部再拆分逻辑。
- **使用示例**：

```python
def initialize(context):
    # 每 5 秒轮询一次盘口，尽量不要低于 3 秒
    run_interval(context, watch_book, seconds=5)
    g.security = '600570.SS'
    set_universe(g.security)


def watch_book(context):
    # 注意：run_interval 回调没有 data 参数，如需行情需主动调用 get_snapshot / get_gear_price
    gear = get_gear_price(g.security)
    best_bid = gear['bid_grp'][1][0]
    log.info(f'best bid: {best_bid}')
```

---

### 7. 历史行情与实时行情 API

#### 7.1 `get_trading_day(day=0)`

- **模块**：研究 / 回测 / 交易
- **返回**：`datetime.date`
- **工程用途**：统一用该函数进行交易日偏移，不直接用自然日运算。

#### 7.2 `get_all_trades_days(date=None)` / `get_trade_days(...)`

- 统一以交易日数组形式返回（`numpy.ndarray`），用于构造回看窗口等。

#### 7.3 `get_history(...)`

- **核心签名**：

```python
get_history(
    count: int,
    frequency: str = '1d',
    field: str | list[str] = 'close',
    security_list: str | list[str] | None = None,
    fq: str | None = None,
    include: bool = False,
    fill: str = 'nan',
    is_dict: bool = False,
)
```

- **返回数据形态工程约定**：
  - **单标的（推荐）**：`pandas.DataFrame`，index 为 `datetime`，列为字段。
  - **多标的 + Python 3.11**：`pandas.DataFrame`，必须包含 `code` 列。
  - **多标的大规模调用**：推荐 `is_dict=True` 以提升性能，返回：

    ```python
    OrderedDict[
        code: numpy.ndarray[
            (datetime, open, high, low, close, volume, money, price)
        ]
    ]
    ```
- **注意事项**：
  - `get_history` 与 `get_price` 在同一时间多线程高频调用时，可能因底层锁冲突导致偶发返回空数据；工程内禁止在多个异步入口同时调用这两个函数。
  - 停牌日数据不会被剔除，而是以“前一有效交易日数据 + 成交量=0”形式补齐，需要通过 `volume==0` 自行过滤。
  - `fq` 参数的选择会影响回测信号与实际成交价格的可比性，一般建议回测策略使用前复权（`'pre'`），但下单逻辑必须基于未复权实时价格。
- **使用示例**：

```python
def get_last_n_closes(security: str, n: int) -> list[float]:
    """
    获取某标的最近 n 个交易日的收盘价（不含当日），并剔除停牌日。
    """
    df = get_history(
        count=n + 5,              # 多取几天用于剔除停牌
        frequency='1d',
        field='close',
        security_list=security,
        fq='pre',
        include=False,
    )
    # 剔除 volume 为 0 的停牌日
    vol_df = get_history(
        count=n + 5,
        frequency='1d',
        field='volume',
        security_list=security,
        fq=None,
        include=False,
    )
    merged = df.join(vol_df, lsuffix='_close', rsuffix='_vol')
    valid = merged[merged['volume_vol'] > 0].tail(n)
    return valid['close_close'].tolist()
```

#### 7.4 `get_price(...)`

- 与 `get_history` 的主要差别：支持 `start_date/end_date` 区间与 `count` 组合。
- 工程内统一约定：**日线/分钟线历史区间查询优先使用 `get_price`**，最近 N 根使用 `get_history`。
- **模块**：研究 / 回测 / 交易
- **注意事项**：
  - `start_date` 与 `count` 不能同时传入，否则会抛出参数错误。
  - 日线/分钟线以外（周/月/季/年）频率只支持 `end_date + count` 方式。
  - 返回数据**不包含今天当日尚未收盘的 K 线**，如需“昨天收盘价”应确保 `end_date` 为昨天或留空并在交易中调用。
- **使用示例**：

```python
def get_month_bar(security: str, months: int):
    """
    获取某标的最近 N 个月的月线数据。
    """
    df = get_price(
        security=[security],
        end_date=None,           # 默认为当前日期
        frequency='mo',          # 月线
        fields=['open', 'high', 'low', 'close', 'volume'],
        count=months,
    )
    # Python 3.11 多标返回带 code 列
    return df[df['code'] == security]
```

#### 7.5 L2 / Tick 行情

- **`get_individual_entrust(...)`**：逐笔委托  
- **`get_individual_transaction(...)`**：逐笔成交  
- **`get_tick_direction(...)`**：分时成交  
- **`get_gear_price(sids)`**：买卖档位  
- **`get_snapshot(security)`**：实时快照  

> 工程建议：多标的大规模 L2 查询必须使用 `is_dict=True`，避免 DataFrame/Panel 带来严重性能问题。
>
> Tick 级与 L2 数据的订阅、权限由券商端控制，策略开发前需确认账号是否开通对应行情。

##### `get_snapshot` 使用示例与注意事项

- **示例**：

```python
def is_limit_up(security: str) -> bool:
    """
    判断某标的当前是否涨停。
    """
    snap = get_snapshot(security)
    info = snap.get(security, {})
    last = info.get('last_px')
    up = info.get('up_px')
    return last is not None and up is not None and abs(last - up) < 1e-6
```

- **注意事项**：
  - 在 `before_trading_start` 阶段获取的快照大多数字段为 0（尚未开盘），不应基于该阶段的 `last_px` 做交易决策。
  - `trade_status` 必须参与风控判断（如 `SUSP` / `STOPT` / `DELISTED` 等状态禁止下单）。
  - L2 档位中的委托队列（第四个元素为 dict）只有在 L2 权限开通情况下有效，否则笔数字段为 0。

#### 7.6 获取信息类函数明细（来自 `4、API接口明细-获取信息函数.json`）

> 本小节按“名称 / 描述 / 使用模块 / 参数 / 返回值 / 示例 / 注意事项”完整展开，直接对应 JSON 中的结构，便于查阅与自动生成 IDE 提示。

- **`get_trading_day(day: int = 0)`**
  - **描述**：获取交易日期。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：
    - `day: int`  
      - **类型**：`int`  
      - **说明**：天数偏移，正的为数天后，负的为数天前，`0` 表示获取当前交易日；如果当前日期为非交易日则返回上一交易日。不建议获取交易所还未公布的未来交易日期。
  - **返回值**：
    - **类型**：`datetime.date`  
    - **说明**：交易日期。
  - **示例**：
    - `initialize`：`g.security = ['600670.SS', '000001.SZ']; set_universe(g.security)`
    - `handle_data`：`next_trading_date = get_trading_day(1); ... previous_trading_date = get_trading_day(-1); ...`
  - **注意事项**：
    - 回测中当前时间为 `context.blotter.current_dt`；
    - 研究 / 交易中当前时间为调用当天日期。

- **`get_all_trades_days(date: str | None = None)`**
  - **描述**：获取全部交易日期。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：
    - `date: str`：如 `'2016-02-13'` 或 `'20160213'`。
  - **返回值**：
    - **类型**：`numpy.ndarray`  
    - **说明**：包含所有交易日的数组。
  - **示例**：
    - `initialize`：`all_trades_days = get_all_trades_days(); ... all_trades_days_date = get_all_trades_days('20150312'); ...`
  - **注意事项**：
    - 回测中 `date` 默认为当前回测日；
    - 研究 / 交易中 `date` 默认为调用当天日期。

- **`get_trade_days(start_date: str | None = None, end_date: str | None = None, count: int | None = None)`**
  - **描述**：获取指定范围交易日期。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：
    - `start_date: str`：开始日期，与 `count` 二选一，不可同时使用，如 `'2016-02-13'` 或 `'20160213'`，开始日期最早不超过 1990 年；
    - `end_date: str`：结束日期，如 `'2016-02-13'` 或 `'20160213'`，若大于今年则至多返回到今年；
    - `count: int`：数量，与 `start_date` 二选一，不可同时使用，必须大于 0，表示获取 `end_date` 往前的 `count` 个交易日（包含 `end_date`），建议不大于 3000。
  - **返回值**：
    - **类型**：`numpy.ndarray`  
    - **说明**：指定范围内交易日数组。
  - **示例**：
    - `initialize`：`trade_days = get_trade_days('2016-01-01', '2016-02-01'); ...`
    - `handle_data`：`trading_days = get_trade_days(count=10); ...`
  - **注意事项**：
    - 回测中 `end_date` 默认为当前回测日；
    - 研究 / 交易中 `end_date` 默认为调用当天日期。

- **`get_trading_day_by_date(query_date: str, day: int = 0)`**
  - **描述**：按日期获取指定交易日。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：
    - `query_date: str`：查询日期，如 `'20230501'`（必传）；
    - `day: int`：天数偏移，正的为数天后，负的为数天前，`0` 表示获取当前交易日；如果当前日期为非交易日则返回下一交易日。
  - **返回值**：
    - **类型**：`str`  
    - **说明**：交易日日期。
  - **示例**：
    - `handle_data`：以当前回测日与 `get_trading_day_by_date` 返回值比较，判断“今日是否为某自然日之后首个交易日”。
  - **注意事项**：
    - `query_date` 必传；
    - 典型场景为按固定自然日调仓。

- **`get_market_list()`**
  - **描述**：获取市场列表。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：无。
  - **返回值**：
    - **类型**：`pandas.DataFrame`  
    - **说明**：市场列表目录，字段示例：`finance_mic, finance_name` 等。
  - **示例**：
    - `initialize`：`df = get_market_list()`，返回如：`0, A, 美国证券交易所; ...; 71, YCME, 渝川玉石`。
  - **注意事项**：
    - 回测和交易中仅限 `before_trading_start` / `after_trading_end` 阶段使用。

- **`get_market_detail(finance_mic: str)`**
  - **描述**：获取市场详细信息。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：
    - `finance_mic: str`：市场代码，需参考 `get_market_list` 返回的信息。
  - **返回值**：
    - **类型**：`pandas.DataFrame`  
    - **说明**：市场详细信息，字段示例：`hq_type_code, prod_code, prod_name, trade_time_rule` 等。
  - **注意事项**：
    - 回测和交易中仅限 `before_trading_start` / `after_trading_end` 使用。

- **`get_history(...)`**
  - **描述**：获取历史行情。
  - **模块**：研究 / 回测 / 交易。
  - **参数**（补充 JSON 原始定义）：
    - `count: int`：K 线数量（必填，大于 0）；
    - `frequency: str`：K 线周期，支持 `1m/5m/15m/30m/60m/120m/1d/1w/weekly/mo/monthly/1q/quarter/1y/yearly`，默认 `'1d'`；
    - `field: list[str] | str`：结果集字段，默认 `['open','high','low','close','volume','money','price']`；可包含 `is_open/preclose/high_limit/low_limit/unlimited` 等；
    - `security_list: list[str] | str`：标的列表，`None` 表示当前 `universe` 中所有股票；
    - `fq: str`：复权方式，可选 `pre/post/dypre/None`，默认 `None`；
    - `include: bool`：是否包含当前周期，默认 `False`；
    - `fill: str`：分钟数据缺失时的填充方式，`'pre'`（上一分钟数据）或 `'nan'`，默认 `'nan'`（仅交易有效）；
    - `is_dict: bool`：是否返回字典 `{str: array()}`，默认 `False`。
  - **返回值**：
    - **类型**：`dict | pandas.DataFrame | pandas.Panel`  
    - **说明**：
      - `is_dict=True`：返回 `dict`，key 为股票代码，value 为数组（内含时间、开高低收、量额、最新价等）；
      - `is_dict=False`：返回 `DataFrame/Panel`，具体取决于 Python 版本与是否多股票多字段：
        - Python 3.5/3.11 单股票：`DataFrame`，index 为 `datetime`，columns 为行情字段；
        - Python 3.11 多股票：`DataFrame`，index 为 `datetime`，columns 为 `code + 字段`；
        - Python 3.5 多股票单字段：`DataFrame`，index 为 `datetime`，columns 为股票代码；
        - Python 3.5 多股票多字段：`Panel`，items 为字段，每个 item 为 `DataFrame(datetime x code)`。
  - **示例**：已在 7.3 小节展开（包括多股票、不同字段组合等）。
  - **注意事项**（补充）：
    - 仅能获取 2005 年后的数据；
    - 停牌日不会被跳过，而是以前一有效交易日数据 + `volume=0` 填充；
    - 行业/概念等指数行情为非标准数据，需自行评估合理性；
    - 与 `get_price` 不能在多线程环境下同时高频调用，否则会偶发返回空数据。

- **`get_individual_entrust(stocks=None, data_count=50, start_pos=0, search_direction=1, is_dict=False)`**
  - **描述**：获取当日逐笔委托行情数据。
  - **模块**：交易。
  - **参数**：
    - `stocks: list[str]`：代码列表，默认当前股票池；
    - `data_count: int`：数据条数，默认 50，最大 200；
    - `start_pos: int`：起始位置，默认 0；
    - `search_direction: int`：搜索方向，`1` 向前，`2` 向后，默认 1；
    - `is_dict: bool`：返回类型，`False` 为 `DataFrame/Panel`，`True` 为 `dict`，默认 `False`。
  - **返回值**：`dict` 类型数据，异常时返回 `None`。
  - **注意事项**：需开通 Level2 行情；`dict` 形式速度更快。

- **`get_individual_transaction(...)`**
  - **描述**：获取当日逐笔成交行情数据。
  - **模块**：交易。
  - **参数 / 返回值 / 注意事项**：同 `get_individual_entrust`，语义为“成交”。

- **`get_tick_direction(symbols, query_date=0, start_pos=0, search_direction=1, data_count=50, is_dict=False)`**
  - **描述**：获取当日分时成交行情数据。
  - **模块**：交易。
  - **参数**：
    - `symbols: str | list[str]`：单只或多只标的代码；
    - `query_date: int`：查询日期，格式 `YYYYMMDD`，默认 0（当日）；
    - 其余参数同上。
  - **返回值**：`dict` 或 `OrderedDict`。
  - **注意事项**：`dict` 返回速度更快。

- **`get_sort_msg(sort_type_grp, sort_field_name, sort_type=1, data_count=100)`**
  - **描述**：获取板块、行业的快照信息。
  - **模块**：交易。
  - **参数**：
    - `sort_type_grp: str | list[str]`：板块或行业代码，仅支持 `XBHS.DY/XBHS.GN/XBHS.ZJHHY/XBHS.ZS/XBHS.HY` 等；
    - `sort_field_name: str`：排序字段，如 `preclose_px/open_px/last_px` 等；
    - `sort_type: int`：排序方式，默认降序（`0` 升序，`1` 降序）；
    - `data_count: int`：数据条数，默认 100，最大 10000。
  - **返回值**：`list[dict]`。
  - **注意事项**：证监会行业、聚源行业等为非标准数据，可能与三方数据源不一致。

- **`get_gear_price(sids)`**
  - **描述**：获取指定代码的档位行情价格。
  - **模块**：交易。
  - **参数**：
    - `sids: str | list[str]`：股票代码。
  - **返回值**：`dict` 类型数据。
  - **注意事项**：
    - 获取实时行情快照失败时返回空 `dict`；
    - 无 L2 行情时，委托笔数字段返回 0。

- **`get_snapshot(security)`**
  - **描述**：获取实时行情快照。
  - **模块**：交易。
  - **参数**：
    - `security: str | list[str]`：单只股票或股票列表。
  - **返回值**：`dict` 类型数据，key 为空间代码，value 为快照字段。
  - **注意事项**：行业、概念等为非标准数据。

- **`get_trend_data(date=None, stocks=None, market=None)`**
  - **描述**：获取集中竞价期间代码数据。
  - **模块**：研究 / 回测 / 交易。
  - **参数**：
    - `date: str`：日期，格式 `YYYYmmdd`；
    - `stocks: str | list[str]`：股票代码；
    - `market: str | list[str]`：市场；`stocks` 与 `market` 不能同时入参。
  - **返回值**：`dict` 类型数据。
  - **注意事项**：不传参数时，默认返回当日 `XSHE/XSHG` 所有代码数据。

- **证券与市场静态信息**：
  - `get_stock_name(stocks)`  
    - **返回**：`dict`，代码 → 名称；  
    - **注意**：交易场景下，每个交易日 09:07–09:09 之间完成更新。
  - `get_stock_info(stocks, field)`  
    - **返回**：`dict`，指定字段信息，如 `stock_name/listed_date/de_listed_date` 等；  
    - **注意**：`field` 不传时默认只返回 `stock_name`。
  - `get_stock_status(stocks, query_type='ST', query_date=None)`  
    - **返回**：`dict`，ST/停牌/退市等属性。
  - `get_underlying_code(symbols)`  
    - **返回**：`dict`，证券关联代码信息（正股 ↔ 可转债等）。
  - `get_stock_exrights(stock_code, date=None)`  
    - **返回**：`pandas.DataFrame`，除权除息信息。
  - `get_stock_blocks(stock_code)`  
    - **返回**：`dict`，所属板块信息；  
    - **注意**：获取的是“当下”数据，回测中需注意未来函数问题。

- **指数 / 行业 / A 股 / REITs 列表**：
  - `get_index_stocks(index_code, date=None) -> list[str]`：指数成分股；
  - `get_industry_stocks(industry_code) -> list[str]`：行业成分股（行业编码尾缀 `.XBHS`）；
  - `get_Ashares(date=None) -> list[str]`：沪深 A 股列表；
  - `get_reits_list(date=None) -> list[str]`：公募 REITs 基金代码列表。

- **ETF / 可转债 / IPO 信息**：
  - `get_etf_list() -> list[str]`：柜台返回的 ETF 代码列表；
  - `get_etf_info(etf_code) -> dict`：单支或多支 ETF 信息；
  - `get_etf_stock_list(etf_code) -> list[str]`：目标 ETF 成分券列表；
  - `get_etf_stock_info(etf_code, security) -> dict`：ETF 成分券信息；
  - `get_ipo_stocks() -> dict`：当日 IPO 申购标的信息；
  - `get_cb_list() -> list[str]`：当前可转债市场所有代码列表（含停牌）；
  - `get_cb_info() -> pandas.DataFrame`：可转债基础信息（失败时返回空 DataFrame）。

- **财务与基本面**：
  - `get_fundamentals(security, table, fields, date=None, start_year=None, end_year=None, report_types=None, merge_type=None, is_dataframe=False)`  
    - **返回**：`pandas.DataFrame` 或 `pandas.Panel`；  
    - **注意**：HTTP 在线获取，有流量限制——每秒最多 100 次调用，单次最大 500 条数据。

#### 7.7 财务数据 API 详解（get_fundamentals）

> 本节基于恒生投研平台财务数据接口文档整理，`get_fundamentals` 用于获取三大报表、估值、财务能力指标等数据。

**接口签名**：

```python
get_fundamentals(
    security,                    # str | list[str]，股票代码
    table,                       # str，财务数据表名
    fields,                      # str | list[str]，输出字段
    date=None,                   # str | datetime.date，按日期查询
    start_year=None,             # str，按年份查询：开始年份
    end_year=None,               # str，按年份查询：截止年份
    report_types=None,           # str，财报类型
    merge_type=None,             # int，原始发布/最新发布
    is_dataframe=False,          # bool，True=DataFrame，False=Panel
)
```

**模块**：研究 / 回测 / 交易

**两种查询模式**（注：`valuation` 仅支持按日期查询，见下表各表说明）：

| 模式 | 参数组合 | 说明 |
|------|----------|------|
| 按日期查询 | `date='20160628'` | 返回查询日期**之前**对应的财务数据（默认以**发布日期**为参考时间）。回测/交易中若 `date` 为非交易日，返回 NaN；研究中若为非交易日，则返回往前最近一个交易日的数据。回测/交易可取到未来日期数据，需规避未来函数。 |
| 按年份查询 | `start_year='2013', end_year='2015', report_types='1'` | 返回输入年份范围内对应季度的财务数据 |

**`report_types` 财报类型**：

- `'1'`：一季度财报
- `'2'`：半年报
- `'3'`：三季度财报
- `'4'`：年报

**各表说明**：

- `valuation`：日频估值数据，**仅支持按日期查询**（不支持 `start_year`/`end_year`/`report_types`/`date_type`/`merge_type`）。
- `balance_statement` / `income_statement` / `cashflow_statement` / `eps` / `growth_ability` / `profit_ability` / `operating_ability`：支持按日期、按年份两种模式。
- `debt_paying_ability`：偿债能力指标，**不支持** `merge_type` 参数。

**属性说明**：固定返回 = 必然返回；自选返回 = 需在 `fields` 中指定才返回。

---

**valuation（估值数据）**

**接口说明**：

```python
get_fundamentals(security, 'valuation', fields=None, date=None)
```

- **参数限制**：此表**仅支持按日期查询**，不支持 `start_year`、`end_year`、`report_types`、`date_type`、`merge_type`。
- **返回值**：返回查询日期对应股票的估值数据。

**关于 `date` 字段**：

| 场景 | 说明 |
|------|------|
| `date` 不入参 | 回测中：获取 `context.blotter.current_dt` 交易日**收盘后**更新的数据（会产生未来函数）；交易/研究中：返回当日数据；盘中调用因数据未更新可能返回 NaN，**建议获取最新数据时用 `date` 传入上一交易日**。 |
| `date` 入参 | 回测/交易：若 `date` 为非交易日，返回 NaN；研究：若 `date` 为非交易日，返回往前最近一个交易日的数据。**注意**：回测和交易中可取得未来日期数据，需规避未来函数。 |

**字段格式注意**：`turnover_rate`（换手率）、`dividend_ratio`（滚动股息率）返回带 `%` 的字符串（如 `"20%"`），需自行转换为 `float`（如 `0.2`）。

表数据具体字段：

| 字段名称 | 字段类型 | 字段说明 | 属性 |
|----------|----------|----------|------|
| secu_code | str | 证券代码 | 固定返回 |
| trading_day | str | 交易日期 | 固定返回 |
| total_value | str | A股总市值(元) | 固定返回 |
| float_value | str | A股流通市值(元) | 自选返回 |
| naps | numpy.float64 | 每股净资产(元/股) | 自选返回 |
| pcf | str | 市现率 | 自选返回 |
| secu_abbr | str | 证券简称 | 自选返回 |
| ps | numpy.float64 | 市销率PS | 自选返回 |
| ps_ttm | numpy.float64 | 市销率PS(TTM) | 自选返回 |
| pe_ttm | numpy.float64 | 市盈率PE(TTM) | 自选返回 |
| a_shares | str | A股股本 | 自选返回 |
| a_floats | numpy.float64 | 可流通A股 | 自选返回 |
| pe_dynamic | str | 动态市盈率 | 自选返回 |
| pe_static | str | 静态市盈率 | 自选返回 |
| b_floats | str | 可流通B股 | 自选返回 |
| b_shares | numpy.float64 | B股股本 | 自选返回 |
| h_shares | numpy.float64 | H股股本 | 自选返回 |
| total_shares | int | 总股本 | 自选返回 |
| turnover_rate | str | 换手率 | 自选返回 |
| dividend_ratio | str | 滚动股息率 | 自选返回 |
| pb | numpy.float64 | 市净率 | 自选返回 |
| roe | numpy.float64 | 净资产收益率 | 自选返回 |

---

**eps（每股指标）** — 表数据具体字段

**接口说明**：支持按日期、按年份两种查询模式，参数与 `balance_statement`、`income_statement` 相同。按日期返回输入日期之前对应数据（以发布日期为参考），按年份返回指定年份范围内对应季度数据。

| 字段名称 | 字段类型 | 字段说明 | 属性 |
|----------|----------|----------|------|
| secu_code | str | 股票代码 | 固定返回 |
| secu_abbr | str | 股票简称 | 固定返回 |
| publ_date | str | 公告日期 | 固定返回 |
| end_date | str | 截止日期 | 固定返回 |
| basic_eps | numpy.float64 | 基本每股收益（元/股） | 自选返回 |
| diluted_eps | numpy.float64 | 稀释每股收益（元/股） | 自选返回 |
| eps | numpy.float64 | 每股收益_期末股本摊薄（元/股） | 自选返回 |
| eps_ttm | numpy.float64 | 每股收益_TTM（元/股） | 自选返回 |
| naps | numpy.float64 | 每股净资产（元/股） | 自选返回 |
| total_operating_revenue_ps | numpy.float64 | 每股营业总收入（元/股） | 自选返回 |
| main_income_ps | numpy.float64 | 每股营业收入（元/股） | 自选返回 |
| operating_revenue_ps_ttm | numpy.float64 | 每股营业收入_TTM（元/股） | 自选返回 |
| oper_profit_ps | numpy.float64 | 每股营业利润（元/股） | 自选返回 |
| ebitps | numpy.float64 | 每股息税前利润（元/股） | 自选返回 |
| capital_surplus_fund_ps | numpy.float64 | 每股资本公积金（元/股） | 自选返回 |
| surplus_reserve_fund_ps | numpy.float64 | 每股盈余公积（元/股） | 自选返回 |
| accumulation_fund_ps | numpy.float64 | 每股公积金（元/股） | 自选返回 |
| undivided_profit | numpy.float64 | 每股未分配利润（元/股） | 自选返回 |
| retained_earnings_ps | numpy.float64 | 每股留存收益（元/股） | 自选返回 |
| net_operate_cash_flow_ps | numpy.float64 | 每股经营活动产生的现金流量净额（元/股） | 自选返回 |
| net_operate_cash_flow_ps_ttm | numpy.float64 | 每股经营活动产生的现金流量净额_TTM（元/股） | 自选返回 |
| cash_flow_ps | numpy.float64 | 每股现金流量净额（元/股） | 自选返回 |
| cash_flow_ps_ttm | numpy.float64 | 每股现金流量净额_TTM（元/股） | 自选返回 |
| enterprise_fcf_ps | numpy.float64 | 每股企业自由现金流量（元/股） | 自选返回 |
| shareholder_fcf_ps | numpy.float64 | 每股股东自由现金流量（元/股） | 自选返回 |

---

**debt_paying_ability（偿债能力）** — 表数据具体字段

**接口说明**：

```python
get_fundamentals(security, 'debt_paying_ability', fields, date=None, start_year=None, end_year=None, report_types=None, date_type=None)
```

- **参数限制**：获取此表数据**不支持** `merge_type` 参数。
- 支持按日期、按年份两种查询模式，规则同上。

| 字段名称 | 字段类型 | 字段说明 | 属性 |
|----------|----------|----------|------|
| secu_code | str | 股票代码 | 固定返回 |
| secu_abbr | str | 股票简称 | 固定返回 |
| publ_date | str | 公告日期 | 固定返回 |
| end_date | str | 截止日期 | 固定返回 |
| current_ratio | numpy.float64 | 流动比率 | 自选返回 |
| quick_ratio | numpy.float64 | 速动比率 | 自选返回 |
| super_quick_ratio | numpy.float64 | 超速动比率 | 自选返回 |
| debt_equity_ratio | numpy.float64 | 产权比率（%） | 自选返回 |
| sewmi_to_total_liability | numpy.float64 | 归属母公司股东的权益／负债合计（%） | 自选返回 |
| sewmi_to_interest_bear_debt | numpy.float64 | 归属母公司股东的权益／带息债务（%） | 自选返回 |
| debt_tangible_equity_ratio | numpy.float64 | 有形净值债务率（%） | 自选返回 |
| tangible_a_to_interest_bear_debt | numpy.float64 | 有形净值／带息债务（%） | 自选返回 |
| tangible_a_to_net_debt | numpy.float64 | 有形净值／净债务（%） | 自选返回 |
| ebitda_to_t_liability | numpy.float64 | 息税折旧摊销前利润／负债合计 | 自选返回 |
| nocf_to_t_liability | numpy.float64 | 经营活动产生现金流量净额/负债合计 | 自选返回 |
| nocf_to_interest_bear_debt | numpy.float64 | 经营活动产生现金流量净额/带息债务 | 自选返回 |
| nocf_to_current_liability | numpy.float64 | 经营活动产生现金流量净额/流动负债 | 自选返回 |
| nocf_to_net_debt | numpy.float64 | 经营活动产生现金流量净额/净债务 | 自选返回 |
| interest_cover | numpy.float64 | 利息保障倍数（倍） | 自选返回 |
| long_debt_to_working_capital | numpy.float64 | 长期负债与营运资金比率 | 自选返回 |
| opercashinto_current_debt | numpy.float64 | 现金流动负债比 | 自选返回 |

---

**balance_statement（资产负债表）** — 表数据具体字段（节选）

**接口说明**：

```python
get_fundamentals(security, 'balance_statement', fields, date=None, start_year=None, end_year=None, report_types=None, date_type=None, merge_type=None)
```

- **按日期查询**：`date='20160628'`，返回输入日期**之前**对应的财务数据（默认以发布日期为参考时间）。
- **按年份查询**：`start_year='2013', end_year='2015', report_types='1'`，返回年份范围内对应季度的财务数据。

| 字段名称 | 字段类型 | 字段说明 | 属性 |
|----------|----------|----------|------|
| secu_code | str | 股票代码 | 固定返回 |
| secu_abbr | str | 股票简称 | 固定返回 |
| company_type | str | 公司类型 | 固定返回 |
| end_date | str | 截止日期 | 固定返回 |
| publ_date | str | 公告日期 | 固定返回 |
| settlement_provi | numpy.float64 | 结算备付金 | 自选返回 |
| client_provi | numpy.float64 | 客户备付金 | 自选返回 |
| deposit_in_interbank | numpy.float64 | 存放同业款项 | 自选返回 |
| lend_capital | numpy.float64 | 拆出资金 | 自选返回 |
| derivative_assets | numpy.float64 | 衍生金融资产 | 自选返回 |
| bought_sellback_assets | numpy.float64 | 买入返售金融资产 | 自选返回 |
| loan_and_advance | numpy.float64 | 发放贷款和垫款 | 自选返回 |
| intangible_assets | numpy.float64 | 无形资产 | 自选返回 |
| good_will | numpy.float64 | 商誉 | 自选返回 |
| longterm_loan | numpy.float64 | 长期借款 | 自选返回 |
| bonds_payable | numpy.float64 | 应付债券 | 自选返回 |
| paidin_capital | numpy.float64 | 实收资本（或股本） | 自选返回 |
| capital_reserve_fund | numpy.float64 | 资本公积 | 自选返回 |
| surplus_reserve_fund | numpy.float64 | 盈余公积 | 自选返回 |
| retained_profit | numpy.float64 | 未分配利润 | 自选返回 |
| total_shareholder_equity | numpy.float64 | 所有者权益合计 | 自选返回 |
| total_assets | numpy.float64 | 资产总计 | 自选返回 |

> 资产负债表字段较多，完整字段列表见原始文档。

---

**income_statement（利润表）** — 表数据主要字段（节选）

**接口说明**：

```python
get_fundamentals(security, 'income_statement', fields, date=None, start_year=None, end_year=None, report_types=None, date_type=None, merge_type=None)
```

- **按日期查询**：返回输入日期**之前**对应的财务数据（默认以发布日期为参考时间）。
- **按年份查询**：返回输入年份范围内对应季度的财务数据。

| 字段名称 | 字段类型 | 字段说明 | 属性 |
|----------|----------|----------|------|
| secu_code | str | 股票代码 | 固定返回 |
| secu_abbr | str | 股票简称 | 固定返回 |
| end_date | str | 截止日期 | 固定返回 |
| publ_date | str | 公告日期 | 固定返回 |
| net_profit | numpy.float64 | 净利润 | 自选返回 |
| total_operating_revenue | numpy.float64 | 营业总收入 | 自选返回 |
| operating_revenue | numpy.float64 | 营业收入 | 自选返回 |
| operating_profit | numpy.float64 | 营业利润 | 自选返回 |
| basic_eps | numpy.float64 | 基本每股收益 | 自选返回 |
| diluted_eps | numpy.float64 | 稀释每股收益 | 自选返回 |
| np_parent_company_owners | numpy.float64 | 归属于母公司所有者的净利润 | 自选返回 |
| operating_cost | numpy.float64 | 营业成本 | 自选返回 |
| operating_expense | numpy.float64 | 销售费用 | 自选返回 |
| administration_expense | numpy.float64 | 管理费用 | 自选返回 |
| financial_expense | numpy.float64 | 财务费用 | 自选返回 |

**cashflow_statement（现金流量表）**：支持按日期、按年份查询，参数规则同 `balance_statement`。主要字段含 `net_operate_cash_flow`（经营活动产生的现金流量净额）、`goods_sale_service_render_cash`、`invest_withdrawal_cash`、`invest_proceeds` 等。完整字段见原始文档。

**growth_ability / profit_ability / operating_ability**：成长能力、盈利能力、营运能力指标表，参数与查询模式同上，字段结构类似，均含 `secu_code`、`secu_abbr`、`end_date`、`publ_date` 等。

**使用示例**：

```python
# 0. valuation 估值（仅支持按日期，不支持 start_year/end_year/report_types）
get_fundamentals(stocks, 'valuation')  # 返回前一交易日估值
get_fundamentals(stocks, 'valuation', date='20180410', fields='pb')
get_fundamentals(stocks, 'valuation', date='2018-04-24',
                 fields=['total_value', 'pe_dynamic', 'turnover_rate', 'pb'])

# 1. 按日期查询：回测中获取对应回测日期前一季报的资产总计
get_fundamentals('600570.SS', 'balance_statement', 'total_assets', date='20160628')

# 2. 按年份查询：获取 2013–2015 年第一季度资产负债表中资产总计
get_fundamentals('600570.SS', 'balance_statement', 'total_assets',
                 start_year='2013', end_year='2015', report_types='1')

# 3. 按日期查询：获取利润表净利润
get_fundamentals('600570.SS', 'income_statement', 'net_profit', date='20160628')

# 4. 按年份查询：获取利润表净利润（多年度一季报）
get_fundamentals('600570.SS', 'income_statement', 'net_profit',
                 start_year='2013', end_year='2015', report_types='1')

# 5. 多字段查询
get_fundamentals('600570.SS', 'income_statement',
                 ['net_profit', 'total_operating_revenue', 'operating_profit'],
                 start_year='2011', end_year='2020', report_types='1')

# 6. 偿债能力
get_fundamentals('600570.SS', 'debt_paying_ability', 'current_ratio', date='20160628')
get_fundamentals('600570.SS', 'debt_paying_ability', 'current_ratio',
                 start_year='2013', end_year='2015', report_types='1')
```

**财务数据通用注意事项与说明**：

| 项目 | 说明 |
|------|------|
| 流量限制 | HTTP 在线获取，**每秒最多 100 次调用，单次最大 500 条数据** |
| 未来函数 | 回测/交易中可按日期取得“未来”数据，策略中必须规避；财报类按日期查询返回的是“输入日期之前”的数据，默认以**发布日期**为参考，回测时注意披露时效 |
| 非交易日 | 按日期查询时：回测/交易中若 `date` 为非交易日，返回 NaN；研究中若为非交易日，返回往前最近一个交易日的数据 |
| 日期格式 | `date` 支持 `'20160628'` 或 `'2016-06-28'` 格式 |
| 原始文档 | `http://180.169.107.9:7766/hub/data/finance` |

- **账户 / 持仓 / 对账**：
  - `get_position(security) -> Position`：单标的持仓；
  - `get_positions(security=None) -> dict[str, Position]`：多个标的持仓；
  - `get_all_positions() -> list[dict]`：当前账户持仓（柜台定时同步缓存）；
  - `get_trades_file(save_path) -> str`：回测对账数据文件路径（目录名长度 ≤ 256 且不能含特殊字符）；
  - `convert_position_from_csv(path) -> list[dict]`：从 CSV 获取底仓参数列表（路径规则同上）；
  - `get_user_name(login_account=True) -> str`：登录终端资金账号或当前策略绑定账号（回测中不区分参数，始终为登录终端账号）；
  - `get_deliver(start_date, end_date) -> list[dict]`：账户历史交割单信息（仅支持查询上一个交易日及以前，返回柜台原数据）；
  - `get_fundjour(start_date, end_date) -> list[dict]`：账户历史资金流水信息（规则同上）；
  - `get_lucky_info(start_date, end_date) -> list[dict]`：指定时间范围内中签信息（同一分钟多次调用返回首次查询缓存）。

- **路径与环境**：
  - `get_research_path() -> str`：研究界面根目录路径；
  - `get_trade_name() -> str`：当前交易名称。

> 若需生成更细字段级的返回结构（例如 DataFrame 每一列的含义），可直接以 `4、API接口明细-获取信息函数.json` 为机器可读源，由脚本生成相应的类型定义或更细化文档。

---

### 8. 交易 API（按工程使用频率整理）

#### 8.1 核心下单接口

- **`order(security, amount, ...)`**
  - **含义**：按数量买卖，`amount > 0` 买入，`amount < 0` 卖出。
  - **返回**：`Order` 对象或 `order_id`（依环境）。
  - **注意事项**：
    - `amount` 必须为整股/整张，不允许传入浮点或非整数数量。
    - 若因价格笼子等原因导致废单，`order` 仍可能返回非空 `order_id`，需在 `on_order_response` / `on_trade_response` 中根据 `status` 判断。
    - 高频下单前必须检查 `context.portfolio.cash` 与 `get_open_orders()`，避免资金不足或重复挂单。
  - **使用示例**：

```python
def buy_on_open(context, data):
    """
    示例：开盘第一次触发时买入 1000 股。
    """
    if not getattr(g, 'bought', False):
        order('600570.SS', 1000)
        g.bought = True
```

- **`order_target(security, amount)`**
  - **含义**：目标持仓股数。
  - **注意事项**：
    - 函数会自动读取当前持仓进行“增减仓”，不需要自行计算差量。
    - 若当前持仓量与目标量差异很小，可能由于最小交易单位导致实际成交略有偏差。
  - **使用示例**：

```python
def clear_position(context, data):
    """
    清空指定标的持仓。
    """
    order_target('600570.SS', 0)
```

- **`order_value(security, value)`**
  - **含义**：按金额下单。
  - **注意事项**：
    - `value` 为正则买入，为负则卖出（减持市值），内部会按当前价格换算为股数并取整。
    - 若账户可用资金不足，实际成交金额会小于期望值。
  - **使用示例**：

```python
def buy_with_half_cash(context, data):
    """
    使用一半现金买入指定标的。
    """
    cash = context.portfolio.cash
    order_value('600570.SS', cash * 0.5)
```

- **`order_target_value(security, value)`**
  - **含义**：目标持仓市值。
  - **注意事项**：
    - 常用于多资产组合中按权重分配市值，需结合总资产计算。
    - 当前价格剧烈波动时，目标市值可能出现来回调仓，建议设置最小调仓阈值。

- **`order_market(security, amount, limit_price=None, ...)`**
  - **含义**：市价委托（可设置保护限价）。

> 工程约束：所有限价相关入参，必须遵守：
> - 股票：2 位小数
> - 可转债 / ETF / LOF：3 位
> - 股指期货：1 位
> - ETF 期权：4 位
>
> 市价单下单前需评估标的流动性与盘口深度，否则可能产生远离盘口的成交价。

#### 8.2 订单与成交查询

- **`get_position(security)` / `get_positions()`**
  - 返回 `Position` 对象及其字典，字段包括：
    - `sid: str`
    - `amount: float`
    - `enable_amount: float`
    - `cost_basis: float`

- **`get_open_orders()` / `get_order(order_id)` / `get_orders()` / `get_all_orders()`**
  - 统一依赖 `Order` 对象结构（见下文数据字典）。

- **`get_trades()`**
  - 返回当日成交列表，用于统计成交明细与滑点分析。

#### 8.3 撤单

- **`cancel_order(order_id)`**
- **`cancel_order_ex(...)`**

> 工程约定：撤单操作必须先确认订单状态为可撤（`status` 不在已成/废单/已撤状态）。

---

### 9. 数据字典与状态枚举（工程统一引用，已对齐 PTrade 数据结构）

> 本章对照 `5、PTrade数据结构.json` 中的“数据字典”整理，建议在业务代码中统一引用这些枚举常量，而不要写死 magic number。

#### 9.1 `status` —— 委托状态

- `0`：未报  
- `1`：待报  
- `2`：已报  
- `3`：已报待撤  
- `4`：部成待撤  
- `5`：部撤  
- `6`：已撤  
- `7`：部成  
- `8`：已成  
- `9`：废单  
- `+`：已受理  
- `-`：已确认  
- `C`：正报  
- `V`：已确认  

#### 9.2 `entrust_type` —— 委托类别

- `0`：委托  
- `2`：撤单  
- `4`：确认  
- `6`：信用融资  
- `7`：信用融券  
- `9`：信用交易  

#### 9.3 `entrust_prop` —— 委托属性

- `0`：买卖  
- `1`：配股  
- `3`：申购  
- `4`：回购  
- `7`：转股  
- `9`：股息  
- `N`：ETF 申赎  
- `Q`：对手方最优价格  
- `R`：最优五档即时成交剩余转限价  
- `S`：本方最优价格  
- `T`：即时成交剩余撤销  
- `U`：最优五档即时成交剩余撤销  
- `V`：全成交或撤销  
- `b`：定价委托  
- `c`：确认委托  
- `d`：限价委托  
- `HKN`：港股订单申报  
- `HKO`：零股订单申报  

#### 9.4 `business_direction` —— 成交方向

- `0`：卖  
- `1`：买  
- `2`：借入  
- `3`：出借  

#### 9.5 `trans_kind` —— 委托类型（按市场区分）

- **深圳市场**：
  - `1`：市价委托  
  - `2`：限价委托  
  - `3`：本方最优  
- **上海市场**：
  - `4`：增加订单  
  - `5`：删除订单  

#### 9.6 `trade_status` —— 交易状态

- `START`：市场启动（初始化之后，集合竞价前）  
- `PRETR`：盘前  
- `OCALL`：开始集合竞价  
- `TRADE`：交易（连续撮合）  
- `HALT`：暂停交易  
- `SUSP`：停盘  
- `BREAK`：休市  
- `POSTR`：盘后  
- `ENDTR`：交易结束  
- `STOPT`：长期停盘（停盘 n 天，n ≥ 1）  
- `DELISTED`：退市  
- `POSMT`：盘后交易  
- `PCALL`：盘后集合竞价  
- `INIT`：盘后固定价格启动前  
- `ENDPT`：盘后固定价格闭市阶段  
- `POSSP`：盘后固定价格停牌  

#### 9.7 `trans_flag` —— 成交标记

- `0`：普通成交  
- `1`：撤单成交  

#### 9.8 `trans_identify_am` —— 盘后逐笔成交序号标识

- `0`：盘中  
- `1`：盘后  

#### 9.9 `entrust_bs` —— 委托方向

- `1`：买  
- `2`：卖  

#### 9.10 `cash_replace_flag` —— 现金替代标志

- `0`：禁止替代  
- `1`：允许替代  
- `2`：必须替代  
- `3`：非沪市退补现金替代  
- `4`：非沪市必须现金替代  
- `5`：非沪深退补现金替代  
- `6`：非沪深必须现金替代  

#### 9.11 `exchange_type` / `futu_exch_type` —— 交易类别

- `0`：资金  
- `1`：上海  
- `2`：深圳  
- `9`：特转 A  
- `A`：特转 B  
- `D`：沪 B  
- `G`：沪港通  
- `H`：深 B  
- `Q`：青岛产权  
- `S`：深港通  
- `T`：场外 OTC 市场  
- `U`：转融通  
- `J`：金华基金  
- `K`：香港市场  
- `X`：固定收益  
- `F1`：郑州交易所  
- `F2`：大连交易所  
- `F3`：上海交易所  
- `F4`：金融交易所  
- `F5`：能源交易所  
- `Z1`：业务受理  
- `R`：H 股全流通  

#### 9.12 `delist_flag` —— 退市标志

- `0`：正常  
- `1`：退市  

#### 9.13 `hedge_type` —— 投机 / 套保类型（含期权扩展）

- 期货 / 两融方向：
  - `0`：投机  
  - `1`：套保  
  - `2`：套利  
  - `3`：做市商  
  - `4`：备兑  
- 期权角色与方向：
  - `0`：权利方  
  - `1`：义务方  
  - `2`：备兑方  
  - `C`：看涨期权  
  - `P`：看跌期权  

#### 9.14 `market_type` —— 市价委托类型

- `0`：对手方最优价格  
- `1`：最优五档即时成交剩余转限价  
- `2`：本方最优价格  
- `3`：即时成交剩余撤销  
- `4`：最优五档即时成交剩余撤销  
- `5`：全额成交或撤单  

#### 9.15 `submarket_type` —— 申购代码所属市场

- `0`：上证普通代码  
- `1`：上证科创板代码  
- `2`：深证普通代码  
- `3`：深证创业板代码  
- `4`：可转债代码  

#### 9.16 `cash_group` —— 两融头寸性质

- `0`：核心头寸  
- `1`：普通业务头寸  
- `2`：专项业务头寸  

#### 9.17 `compact_type` —— 合约类别

- `0`：融资  
- `1`：融券  
- `2`：其他负债  

#### 9.18 `compact_status` —— 合约状态

- `0`：开仓未归还  
- `1`：部分归还  
- `2`：合约已过期  
- `3`：客户自行归还  
- `4`：手工了结  
- `5`：未形成负债  

#### 9.19 `underlying_type` —— 关联类型

- `0`：A 股  
- `1`：B 股  
- `2`：H 股  
- `3`：期货  
- `4`：期权  
- `5`：港股-认购  
- `6`：港股-认沽  
- `7`：港股-牛证  
- `8`：港股-熊证  
- `9`：港股-界内证  
- `10`：英股关联关系  
- `11`：美股关联代码  
- `12`：股本认股权证认购证  
- `13`：股本认股权证认沽证  
- `14`：可转债关联关系正向——正股关联可转债  
- `15`：可转债关联关系反向——可转债关联正股  

#### 9.20 `real_type` —— 成交类型

- `0`：买卖  
- `1`：查询  
- `2`：撤单  
- `6`：融资  
- `7`：融券  
- `8`：平仓  
- `9`：信用  
- `G`：期权强制平仓  

#### 9.21 `real_status` —— 成交状态

- `0`：成交  
- `2`：废单  
- `4`：确认  

---

### 10. 工程内使用建议总结

- **统一封装一层本地 `ptrade_client`**：
  - 对外暴露工程内部语义清晰的函数；
  - 内部调用上述原始 API，处理好数据结构差异（Python 3.5 / 3.11）。
- **禁止在策略代码中直接写死 magic number**：
  - 对所有订单状态、交易状态、成交方向等，统一引用本文件的数据字典。
- **对 L2 / 高频策略**：
  - 统一要求使用 `is_dict=True`；
  - 严格控制 `get_history/get_price` 的调用频率（可通过本地缓存）。

