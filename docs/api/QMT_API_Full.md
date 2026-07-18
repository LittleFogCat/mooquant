## QMT 量化交易 API 全量接口文档（工程内版本）

### 1. 文档定位

- **用途**：本文件用于工程内统一说明 QMT（迅投极速策略交易系统）Python API 接口，供开发、测试、运维共同参考。
- **放置位置建议**：`/docs/` 或项目根目录（当前文件名：`QMT_API_Full.md`）。
- **覆盖内容**：
  - 运行框架与生命周期
  - 变量约定（symbol、mode、ContextInfo）
  - 核心对象与数据结构（Tick、Bar、Account、Order、Deal、Position）
  - 系统函数（init、after_init、handlebar、run_time、schedule_run 等）
  - 行情数据（本地/全推/订阅）概念与行情 API
  - 交易下单与查询 API
  - 引用函数、绘图函数
  - 财务数据、合约信息等补充 API
  - 数据字典与枚举常量
  - **参数联动与选择提示**：下单、行情、定时等函数中，不同参数组合会相互影响（如 prType 影响 price、orderType 影响 volume 含义），各函数均配有「参数联动与选择提示」表及 13.11 快速索引
  - **VIP 权限说明**：Level2 行情数据、高级数据等需要 VIP 权限，详见 13.12 节汇总

> 说明：接口内容基于迅投知识库（https://dict.thinktrader.net/innerApi/），QMT 内置 Python 3.6 环境，不同券商/客户端版本可能有细节差异，以线上环境实际返回为准。

---

### 2. 运行框架与生命周期

#### 2.1 概述

QMT 系统支持**回测模型**与**实盘模型**：

- **回测模型**：在历史 K 线上自左向右逐根遍历，以模拟资金账号记录买卖信号、持仓盈亏，最终展示净值走势。
- **实盘模型**：盘中收取最新行情，即时发送买卖信号到交易所，判断委托状态，支持实时重复报撤。

#### 2.2 运行机制（三种）

| 机制 | 分类 | 特点 | 典型场景 |
|------|------|------|----------|
| **逐 K 线驱动（handlebar）** | 事件驱动 | 历史回测 + 盘中，可模拟逐 K 线效果 | 实盘中模拟逐 K 线运行 |
| **订阅推送（subscribe）** | 事件驱动 | 盘中分笔触发回调 | 随分笔行情判断交易 |
| **定时任务（run_time）** | 定时任务 | 固定间隔触发 | 固定时间间隔判断交易 |

#### 2.3 统一模板

**编码要求**：脚本第一行必须写 `#coding:gbk`；缩进统一（全部 4 空格或 Tab）；QMT 内置 Python 3.6。

**逐 K 线驱动（handlebar）**：

```python
#coding:gbk

def init(ContextInfo):
    """
    策略初始化，入参为 ContextInfo（可缩写为 C）
    """
    ContextInfo.stock = ContextInfo.stockcode + '.' + ContextInfo.market

def handlebar(ContextInfo):
    """
    每根 K 线触发；盘中每个分笔会触发，但逐 K 线生效
    """
    if not ContextInfo.is_last_bar():
        return  # 跳过历史 K 线
    # 交易逻辑
```

**事件驱动（subscribe）**：

```python
#coding:gbk

def callback_func(data):
    for stock in data:
        # 处理 data[stock] 中的行情
        pass

def init(ContextInfo):
    ContextInfo.subID = ContextInfo.subscribe_quote("000001.SZ", "1d", callback=callback_func)
```

**定时任务（run_time）**：

```python
#coding:gbk

def init(ContextInfo):
    ContextInfo.run_time("f", "1nSecond", "2019-10-14 13:20:00")

def f(ContextInfo):
    # 定时回调逻辑
    pass
```

---

### 3. 变量约定与核心对象

> **参考**：迅投变量约定 https://dict.thinktrader.net/innerApi/variable_convention.html

#### 3.1 函数命名规则（变量约定）

| 前缀 | 含义 | 数据来源 | 说明 |
|------|------|----------|------|
| `get_` | 取数 | 客户端内存/缓存 | 如 get_market_data_ex、get_full_tick、get_trade_detail_data |
| `query_` | 查询 | 向服务器/柜台查询 | 如 query_credit_account、query_credit_opvolume |

**变量名称约定**：接口中账号类型参数名为 `strAccountType`（字符串）；资金账号为 `accountID`/`accountid`；合约代码为 `orderCode`/`stock_code`/`stockcode`；周期为 `period`；复权方式为 `dividend_type`。

#### 3.2 账号类型（strAccountType，数据字典）

| 变量名/值 | 类型 | 说明 |
|-----------|------|------|
| FUTURE | str | 期货账号 |
| STOCK | str | 股票账号 |
| CREDIT | str | 信用账号 |
| FUTURE_OPTION | str | 期货期权 |
| STOCK_OPTION | str | 股票期权 |
| HUGANGTONG | str | 沪港通 |
| SHENGANGTONG | str | 深港通 |

#### 3.3 symbol_code（代码表示，变量约定）

格式：**交易标的代码.交易所代码**，如 `000001.SZ`、`600000.SH`。

**交易所代码**：

| 交易所 | 迅投简称 |
|--------|----------|
| 上海证券交易所 | SH |
| 深圳证券交易所 | SZ |
| 北京证券交易所 | BJ |
| 中国金融期货交易所 | IF |
| 上海期货交易所 | SF |
| 大连商品交易所 | DF |
| 郑州商品交易所 | ZF |
| 上海国际能源交易中心 | INE |
| 广州期货交易所 | GF |
| 上证期权 | SHO |
| 深证期权 | SZO |

期货合约代码**严格区分大小写**，如 `AP401.ZF` 不能写成 `ap401.ZF`。

#### 3.4 mode - 运行模式

| 模式 | 说明 |
|------|------|
| 调试运行模式 | 策略编辑界面点「运行」，实时行情运算，**不记录交易信号** |
| 回测模式 | 策略编辑界面点「回测」，按回测周期运算，交易记录在回测结果 |
| 模拟信号模式 | 模型交易界面选「模拟」，下单函数**不产生实际委托**，仅记录策略信号 |
| 实盘交易模式 | 模型交易界面选「实盘」，下单函数**实际发出委托** |

#### 3.5 ContextInfo（上下文对象）

- **使用场景**：回测 / 实盘均可用。
- **说明**：由底层维护并传递给 `init`、`handlebar` 等系统函数；**不建议在 ContextInfo 中添加自定义属性**，因其会随 bar 切换回退。

**逐 K 线保存机制**：同一 bar 内修改只对本次 `handlebar` 下文有效；若新分笔不是新 K 线首个分笔，则回退为之前拷贝。适用于 `quickTrade=0`，不适宜 `quickTrade=2` 时存委托状态。

**常用属性**：

| 属性 | 类型 | 说明 |
|------|------|------|
| start / end | str | 回测开始/结束时间（仅回测），`%Y-%m-%d %H:%M:%S`，init 中设置生效 |
| capital | float | 回测初始资金，默认 1000000 |
| period | str | 当前周期（1d、1m、5m 等） |
| barpos | int | 当前 K 线索引号（从 0 开始） |
| time_tick_size | int | 当前图 K 线 bar 数量 |
| stockcode | str | 当前主图代码 |
| market | str | 当前主图市场 |
| dividend_type | str | 复权方式（none、front、back、front_ratio、back_ratio） |
| benchmark | str | 回测基准标的（仅回测） |
| do_back_test | bool | 当前是否为回测模式 |

#### 3.6 全局变量（推荐用法）

**禁止在 ContextInfo 中存跨 bar 状态**，应使用自定义类实例：

```python
class G():
    pass

g = G()

def init(ContextInfo):
    g.stock_list = ['000001.SZ']

def handlebar(ContextInfo):
    g.stock_list.append('600000.SH')
```

#### 3.7 线程与进程

- QMT 中 Python**不能**使用多线程、多进程。
- **所有策略在同一线程执行**，避免 `sleep`、死循环、加锁等阻塞写法。

#### 3.8 数据结构完整定义（字段名与解释）

以下为各函数**参数**或**返回值**中涉及的数据结构；表中**字段名**即代码中访问用的属性名，**解释**为字段含义说明。

---

**Tick 对象**（分笔行情）

- **出现位置**：`get_full_tick` 返回值 value、`get_market_data_ex(period='tick')` 返回的 DataFrame 列、`subscribe_whole_quote` 回调参数 value。
- **返回类型**：`dict`（key 为 stock_code 时，value 为 Tick 字段组成的 dict）或 `pd.DataFrame` 的列（列名为字段名）。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| time | int / str | 时间戳或时间字符串，该笔行情时间 |
| lastPrice | float | 最新价，当前成交价 |
| open | float | 今开，当日开盘价 |
| high | float | 最高，当日最高价 |
| low | float | 最低，当日最低价 |
| lastClose | float | 昨收，前一交易日收盘价 |
| volume | int | 成交量，当日累计成交量 |
| amount | float | 成交额，当日累计成交金额 |
| openInt | int | 股票为交易状态码（见 13.1 openInt）；期货为持仓量 |
| stockStatus | int | 证券状态，与 openInt 含义一致 |
| askPrice1 | float | 卖一价 |
| askPrice2~5 | float | 卖二至卖五价 |
| askVol1~5 | int | 卖一至卖五量 |
| bidPrice1~5 | float | 买一至买五价 |
| bidVol1~5 | int | 买一至买五量 |

- **注意**：全推/订阅分笔默认**无五档**时，ask/bid 相关字段可能为空或 0，需在行情源设置「全推行情」为五档级别。

**Bar 对象**（K 线）

- **出现位置**：`get_market_data_ex` 返回的 `dict[stock_code -> DataFrame]` 中 DataFrame 的 **columns**（每列即一字段）；行索引为 time，每行对应一根 K 线。
- **返回类型**：`pd.DataFrame`，columns 为下表字段名，index 为 K 线时间。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| time | int / str | K 线时间，该根 K 线的起始或结束时间 |
| open | float | 开盘价，该周期内首笔成交价 |
| high | float | 最高价，该周期内最高成交价 |
| low | float | 最低价，该周期内最低成交价 |
| close | float | 收盘价，该周期内最后一笔成交价 |
| volume | int | 成交量，该周期内累计成交量 |
| amount | float | 成交额，该周期内累计成交金额 |
| preClose | float | 昨收价，前一周期收盘价（日线为前一交易日） |
| suspendFlag | int | 停牌标志，0 正常交易，1 停牌 |
| highLimit | float | 涨停价，仅日线等有涨跌停时有效 |
| lowLimit | float | 跌停价，仅日线等有涨跌停时有效 |

**Account 对象**（资金账号）

- **出现位置**：`get_trade_detail_data(accountID, strAccountType, 'ACCOUNT')` 返回的 list 元素、主推 `account_callback(ContextInfo, accountInfo)` 的 **accountInfo** 参数。
- **返回类型**：list 中元素为对象，通过属性访问，如 `obj.m_dBalance`。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| m_dBalance | float | 总资产，资金+持仓市值等 |
| m_dAvailable | float | 可用资金，可用于买入的现金 |
| m_dFrozenCash | float | 冻结资金，委托占用等 |
| m_dInstrumentValue | float | 持仓市值，当前持仓按市价计算的总市值 |
| m_dPositionProfit | float | 持仓盈亏，浮动盈亏 |
| m_dTodayProfit | float | 当日盈亏，当日已实现+浮动 |
| m_strAccountID | str | 资金账号，与 passorder 的 accountid 一致 |
| m_strAccountType | str | 账号类型，见 3.2（STOCK/CREDIT/FUTURE 等） |
| m_dAssetBalance | float | 资产余额（部分客户端） |
| m_dFetchBalance | float | 可取资金（部分客户端） |
| m_strStatus | str | 账号状态描述（如「准备登录」） |
| m_strTradingDate | str | 当前交易日期，如 20240220 |

**Order 对象**（委托）

- **出现位置**：`get_trade_detail_data(..., 'ORDER')` 返回的 list 元素、主推 `order_callback(ContextInfo, orderInfo)` 的 **orderInfo** 参数；`cancel(orderId, ...)` 的 **orderId** 取本对象的 `m_strOrderSysID`。
- **返回类型**：list 中元素为委托对象，属性见下表。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| m_strOrderSysID | str | 委托编号，柜台回报的委托号，撤单时传入 cancel 的 orderId |
| m_strInstrumentID | str | 合约代码（不含交易所后缀时需自行拼接，如 000001） |
| m_strExchangeID | str | 交易所代码，如 SZ、SH |
| m_nVolumeTotalOriginal | int | 委托数量，原始委托股数/手数 |
| m_nVolumeTraded | int | 成交数量，已成交股数/手数 |
| m_dPriceLimit | float | 委托价格，限价单价格 |
| m_dTradedPrice | float | 成交均价，已成交部分的平均价 |
| m_nOrderStatus | int | 委托状态，见 13.6 EEntrustStatus（49 待报、50 已报、56 已成等） |
| m_strRemark | str | 投资备注，即 passorder 的 userOrderId |
| m_strAccountID | str | 资金账号 |
| m_strAccountType | str | 账号类型 |
| m_strSource | str | 策略/来源名称 |
| m_strInsertDate | str | 委托日期，如 20240222 |
| m_strInsertTime | str | 委托时间，如 091259 |
| m_strInstrumentName | str | 合约名称，如 平安银行 |

**Deal 对象**（成交）

- **出现位置**：`get_trade_detail_data(..., 'DEAL')` 返回的 list 元素、主推 `deal_callback(ContextInfo, dealInfo)` 的 **dealInfo** 参数。
- **返回类型**：list 中元素为成交对象，属性见下表。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| m_strOrderSysID | str | 委托编号，对应的委托号 |
| m_strInstrumentID | str | 合约代码 |
| m_strInstrumentName | str | 合约名称 |
| m_dPrice | float | 成交价格，该笔成交单价 |
| m_nVolume | int | 成交数量，该笔成交股数/手数 |
| m_dTradeAmount | float | 成交金额，该笔成交金额（价格×数量） |
| m_strRemark | str | 投资备注，与委托的 userOrderId 一致 |
| m_nDirection | int | 买卖方向，买/卖标识 |
| m_strAccountID | str | 资金账号 |
| m_strTradeID | str | 成交编号，柜台成交号 |
| m_strTradeDate | str | 成交日期，如 20240220 |
| m_strTradeTime | str | 成交时间，如 172341 |
| m_strOptName | str | 操作名称，如 限价买入 |

**Position 对象**（持仓）

- **出现位置**：`get_trade_detail_data(..., 'POSITION')` 返回的 list 元素、主推 `position_callback(ContextInfo, positionInfo)` 的 **positionInfo** 参数。
- **返回类型**：list 中元素为持仓对象，属性见下表。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| m_strInstrumentID | str | 合约代码 |
| m_strInstrumentName | str | 合约名称 |
| m_nVolume | int | 持仓数量，总持仓股数/手数 |
| m_nCanUseVolume | int | 可用数量，可卖数量（T+1 等限制后） |
| m_dOpenPrice | float | 开仓价/成本价，持仓成本 |
| m_dInstrumentValue | float | 持仓市值，按最新价计算的市值 |
| m_dPositionProfit | float | 持仓盈亏，浮动盈亏 |
| m_dTodayProfit | float | 当日盈亏，该标的当日盈亏 |
| m_strAccountID | str | 资金账号 |
| m_strAccountType | str | 账号类型 |
| m_dLastPrice | float | 最新价（部分客户端） |
| m_nYesterdayVolume | int | 昨仓数量（期货等） |
| m_nFrozenVolume | int | 冻结数量（部分客户端） |

**CTaskDetail 对象**（智能算法任务）

- **出现位置**：`get_trade_detail_data(..., 'TASK')` 返回的 list 元素、主推 `task_callback(ContextInfo, taskInfo)` 的 **taskInfo** 参数；`cancel_task(taskId, ...)` 的 **taskId** 取本对象的 `m_nTaskId`。
- **返回类型**：list 中元素为任务对象，属性见下表。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| m_nTaskId | str / int | 任务号，cancel_task / pause_task / resume_task 时传入 |
| m_stockCode | str | 合约代码，如 000001.SZ |
| m_strInstrumentID | str | 合约代码（部分客户端） |
| m_nNum | int | 任务总数量，目标委托数量 |
| m_nBusinessNum | int | 已成交数量，已执行数量 |
| m_nVolumeTotal | int | 总委托数量（部分客户端） |
| m_nVolumeTraded | int | 已成交数量（部分客户端） |
| m_eStatus | int | 任务状态，如 3 执行中、7 完成 |
| m_strMsg | str | 状态描述，如「任务完成」「全部委托」 |
| m_strRemark | str | 投资备注 |
| m_strAccountID | str | 资金账号 |
| m_dFixPrice | float | 固定价格（部分算法） |
| m_startTime | int | 任务开始时间戳 |
| m_endTime | int | 任务结束时间戳 |

**PassOrderArguments 对象**（下单参数，异常时回传）

- **出现位置**：主推 `orderError_callback(ContextInfo, orderArgs, errMsg)` 的 **orderArgs** 参数；表示失败的那笔 passorder 的参数快照。
- **返回类型**：对象，属性见下表（与 passorder 参数对应）。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| accountID | str | 资金账号，即 passorder 的 accountid |
| orderCode | str | 合约代码，如 SZ000001（可能带交易所前缀） |
| opType | int | 操作类型，见 13.2 |
| orderType | int | 下单方式，见 13.3 |
| prType | int | 选价类型，见 13.4 |
| strategyName | str | 策略名，可能与 remark 拼接 |
| modelVolume | float | 模型数量（部分场景） |
| modelPrice | float | 模型价格（部分场景） |

**InstrumentDetail 对象**（合约详情，dict 形式）

- **出现位置**：`ContextInfo.get_instrument_detail(stockcode)` 的**返回值**（dict），key 为字段名。
- **返回类型**：`dict`，key 为下表字段名，value 为对应类型。

| 字段名 | 数据类型 | 解释 |
|--------|----------|------|
| InstrumentID | str | 合约代码 |
| InstrumentName | str | 合约名称 |
| UpperLimitPrice | float | 涨停价/上限价 |
| LowerLimitPrice | float | 跌停价/下限价 |
| VolumeMultiple | int / float | 合约乘数（期货）或 1（股票） |
| CirculatingShare | float | 流通股本 |
| TotalShare | float | 总股本 |
| LongMarginRatio | float | 多头保证金比例（期货） |
| ShortMarginRatio | float | 空头保证金比例（期货） |
| PriceTick | float | 最小变动价位（部分客户端） |
| ExchangeID | str | 交易所代码 |

**get_market_data_ex 返回值结构**：`dict`，key 为 `stock_code`（str），value 为 `pd.DataFrame`。DataFrame 的 **columns** 即请求的 fields（K 线时为 Bar 字段名，tick 时为 Tick 字段名），**index** 为 time。字段名及解释见上文 **Bar 对象**（K 线）与 **Tick 对象**（分笔）。

**get_financial_data 返回值结构**：`dict`，key 为股票代码（str），value 为 `pd.DataFrame` 或类似结构。DataFrame 的列由 `fieldList` 指定，格式为 `'表名.字段名'`，如 `'ASHAREINCOME.revenue'`。各表字段名及解释见第 9 节「主要表与字段」。

---

### 4. 行情数据概念（必读）

QMT 行情分为三类：

| 类型 | 说明 | 对应接口 |
|------|------|----------|
| **本地数据** | 下载到本地的历史行情，盘中不更新 | `get_market_data_ex(subscribe=False)` |
| **全推数据** | 客户端启动后自动接收的全市场最新快照，**无历史**，盘中约 50ms 更新 | `get_full_tick`、`subscribe_whole_quote` |
| **订阅数据** | 向服务器订阅指定品种，有数量限制，可获取当日实时 K 线 | `subscribe_quote`、`get_market_data_ex(subscribe=True)` |

**行情调用对比**：

- `download_history_data`：下载到本地，不订阅。
- `get_local_data`：取本地数据，盘中不更新，回测适用。
- `get_full_tick`：取全推最新值，无历史，无品种数限制，速度快。
- `subscribe_quote`：订阅指定品种，有最大数量限制，同一品种多周期累加计数。
- `get_market_data_ex`：`subscribe=True` 时取订阅+本地；`subscribe=False` 时仅取本地。

> **注意**：`get_market_data_ex` 等 gmd 系列函数在 `init` 中运行时**只能读到本地数据**，不建议在 `init` 中调用。  
> **不再推荐**：`set_universe`、`get_history_data`、`get_market_data`（订阅无订阅号，无法反订阅）。

---

### 5. 系统函数

#### 5.1 `init(ContextInfo)` - 初始化（系统函数）

- **用法**：`init(ContextInfo)`，入参为上下文对象 ContextInfo。
- **释义**：策略启动时执行一次，用于订阅行情与账号、设置回测参数、初始化全局变量等；部分接口需在 `init` 完成后（如 `after_init`）才可用。
- **参数**：仅 `ContextInfo`，由系统传入。
- **返回**：无（None）。
- **警告**：gmd 系列函数在 `init` 中**只能读到本地数据**，不会取到最新行情；`get_trading_dates` 等部分接口在 `init` 执行完成前不可用。
- **注意事项**：订阅行情（subscribe_quote）、订阅账号（set_account）建议在 `init` 中完成；设置回测参数（start、end、capital、benchmark）在 `init` 中设置生效；`account` 在模型交易界面由策略配置自动赋值，编辑器界面需手动赋值。
- **示例**：
```python
def init(ContextInfo):
    ContextInfo.start = '2020-01-01 09:30:00'
    ContextInfo.end = '2024-12-31 15:00:00'
    ContextInfo.capital = 1000000
    ContextInfo.subID = ContextInfo.subscribe_quote("000001.SZ", "1d")
    ContextInfo.set_account(account)  # 订阅账号，成交回报主推函数才能生效
```
- **ContextInfo.set_account(account)**：订阅资金账号，使 `order_callback`、`deal_callback` 等主推回调生效；`account` 在模型交易界面由策略配置自动赋值，编辑器界面需手动赋值。

#### 5.2 `after_init(ContextInfo)` - 初始化后（系统函数）

- **用法**：`after_init(ContextInfo)`。
- **释义**：`init` 执行完成后、`handlebar` 之前执行一次，用于需要“初始化完成后再执行”的一次性逻辑（如取交易日、一次性下单）。
- **参数**：仅 `ContextInfo`。
- **返回**：无（None）。
- **警告**：`after_init` 中下单**必须**传 `quickTrade=2`，否则信号会被丢弃。
- **注意事项**：`get_trading_dates` 等可在 `after_init` 中调用；编辑器界面运行时的下单不会产生实际委托。

#### 5.3 `handlebar(ContextInfo)` - 行情事件（系统函数）

- **用法**：`handlebar(ContextInfo)`。
- **释义**：每根 K 线或每个 tick 到达时由系统调用，是回测与实盘的主逻辑入口；回测时按 K 线逐根调用，实盘时每个 tick 触发一次，但逐 K 线模式下信号会在下一根 K 线首个 tick 时发出。
- **参数**：仅 `ContextInfo`。
- **返回**：无（None）。
- **注意事项**：可用 `ContextInfo.is_last_bar()` 过滤历史 K 线，只处理最后一根；避免阻塞写法，否则影响其他策略。

#### 5.4 `stop(ContextInfo)` - 停止处理（系统函数）

- **用法**：`stop(ContextInfo)`。
- **释义**：策略关闭、停止前由系统调用一次，用于释放资源、写日志等；此时交易连接已断开。
- **参数**：仅 `ContextInfo`。
- **返回**：无（None）。
- **警告**：`stop` 被调用时**不能**做报单/撤单，仅适合清理资源或记录状态。
- **注意事项**：不要依赖 `stop` 内执行下单或查询委托；可做文件关闭、状态保存等轻量操作。

#### 5.5 `ContextInfo.run_time(funcName, period, startTime)` - 定时器（旧版）

- **用法**：`ContextInfo.run_time(funcName, period, startTime)`。
- **释义**：按固定周期或时间点触发指定回调函数，用于定时执行逻辑；回测环境下不生效。
- **参数**：
  - `funcName: str` 回调函数名
  - `period: str` 周期，如 `'1nSecond'`、`'5nSecond'`、`'1nMinute'`、`'1nHour'`、`'1nDay'`
  - `startTime: str` 首次启动时间，如 `'20191014132000'` 或 `'2019-10-14 13:20:00'`
- **返回**：无（None）。
- **警告**：回测时无效；无取消/结束方法，随策略结束而结束。
- **注意事项**：**推荐**用 `schedule_run` 替代，以便用 `cancel_schedule_run` 取消。

#### 5.6 `ContextInfo.schedule_run(...)` - 定时器（新版）

- **用法**：`ContextInfo.schedule_run(func, time_point, repeat_times=0, interval=None, name='')`。
- **释义**：在指定时间点执行回调，可设置重复次数与间隔；用于盘前/盘后或按日/按分钟定时任务。
- **参数**：
  - `func`：回调函数名（str）或函数对象
  - `time_point`：`datetime` 或 `'yyyymmddHHMMSS'` 首次执行时间
  - `repeat_times`：重复次数，`0` 仅一次，`-1` 不限次数
  - `interval`：重复间隔，如 `'1nDay'`、`'1nMinute'`
  - `name`：任务组名，用于 `cancel_schedule_run` 按组取消
- **返回**：`int` 任务号。

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **repeat_times ↔ interval** | `repeat_times=0` 时**仅执行一次**，`interval` 可省略；`repeat_times>0` 或 `-1` 时**必须**传 `interval`，否则无法按周期重复 |
| **time_point → 时区** | `time_point` 为客户端本地时间；跨日任务需确保格式正确（如 `'20241014150000'` 表示 15:00） |
| **cancel_schedule_run 的 key** | 可按 `schedule_run` 返回的**任务号**（int）取消，或按传入的 `name`（str）取消**同名任务组** |

- **注意事项**：回测时行为以实际客户端为准；定时回调中下单需传 `quickTrade=2`。
- **示例**：
```python
def init(ContextInfo):
    ContextInfo.schedule_run("my_func", "20241014150000", -1, "1nDay", name="daily")
def my_func(ContextInfo):
    pass
```

#### 5.7 `ContextInfo.cancel_schedule_run(key)` - 取消定时任务

- **用法**：`ContextInfo.cancel_schedule_run(key)`。
- **释义**：按任务号或任务组名取消已注册的定时任务。
- **参数**：`key` 为任务号（int）或任务组名（str），与 `schedule_run` 返回或传入的 `name` 对应。
- **返回**：无（None）。
- **注意事项**：传入不存在的 key 可能静默忽略，以客户端为准。

#### 5.8 `ContextInfo.is_last_bar()` / `ContextInfo.is_new_bar()`（系统函数）

- **用法**：`ContextInfo.is_last_bar()`、`ContextInfo.is_new_bar()`，无参数。
- **返回类型**：`bool`。
- **函数解释**：`is_last_bar()` 是否为最后一根 K 线（用于过滤历史，只处理最新 bar）；`is_new_bar()` 是否为当前 K 线的首个 tick。
- **注意事项**：回测/实盘均可用。

#### 5.9 `ContextInfo.get_stock_name(stockcode)` / `ContextInfo.get_open_date(stockcode)`（系统函数）

- **用法**：`ContextInfo.get_stock_name(stockcode)`、`ContextInfo.get_open_date(stockcode)`；参数 `stockcode: str`，如 `'000001.SZ'`。
- **返回类型**：`str`。前者为标的名称，后者为上市日期。
- **注意事项**：建议用 `get_instrument_detail(stockcode)` 替代，可获取更全合约信息。

#### 5.10 板块管理

| 函数 | 说明 | 参数 |
|------|------|------|
| `create_sector(name, parent)` | 创建板块 | name 板块名，parent 父板块 |
| `create_sector_folder(name, parent)` | 创建板块目录 | 同上 |
| `get_sector_list(node)` | 获取板块/目录列表 | node 父节点，空为根 |
| `reset_sector_stock_list(sector, stock_list)` | 重置成分股 | sector 板块名，stock_list 代码列表 |
| `add_stock_to_sector(sector, stock)` | 添加成分股 | 单个代码 |
| `remove_stock_from_sector(sector, stock)` | 移除成分股 | 同上 |

#### 5.11 `ContextInfo.set_output_index_property(index_name[, draw_style, color, noaxis, nodraw, noshow])`

- **用法**：`ContextInfo.set_output_index_property(index_name[, draw_style, color, noaxis, nodraw, noshow])`。
- **释义**：设定副图指标线的绘制属性（线型、颜色、是否参与坐标、是否画线、是否显示），覆盖指标默认属性。
- **参数**：`index_name` 指标名（str）；`draw_style` 线型；`color` 颜色；`noaxis` 不参与坐标；`nodraw` 不画线；`noshow` 不显示。
- **返回**：无（None）。
- **注意事项**：`nodraw=True` 时该指标不画线，仅参与计算；指标名需与策略中输出指标一致。

---

### 6. 数据下载 API

#### 6.1 `download_history_data(stockcode, period, startTime, endTime[, incrementally])`（数据下载）

- **用法**：`download_history_data(stockcode, period, startTime, endTime[, incrementally])`。
- **参数（变量名与类型）**：

| 参数（变量名） | 类型 | 必填 | 说明 |
|----------------|------|------|------|
| stockcode | str | 是 | 合约代码，如 `'000001.SZ'`、`'沪深A股'`（板块） |
| period | str | 是 | `'tick'`、`'1d'`、`'1m'`、`'5m'`；3m 由 1m 合成，15m/30m 由 5m 合成 |
| startTime | str | 否 | 起始时间，如 `'20230101'` 或 `'20200101093000'`，空为增量 |
| endTime | str | 否 | 结束时间，可为空 |
| incrementally | bool | 否 | 是否增量下载（部分版本支持） |

- **返回**：无（None）。
- **释义**：下载指定合约、周期、时间范围的行情到本地，与界面「数据管理 - 行情数据下载」效果一致；`startTime` 为空时为**增量下载**（以本地最后一天为基准）。

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **period → startTime/endTime 格式** | 日线/分钟可用 `'20230101'`；**tick** 需精确到秒，如 `'20200101093000'`；部分版本格式要求不同，以客户端为准 |
| **startTime 为空 → incrementally** | `startTime` 为空时按**增量下载**（以本地已有数据为基准续传）；**首次下载必须**指定 `startTime` 完整拉取 |
| **period → 合成周期** | `3m` 由 `1m` 合成、`15m`/`30m` 由 `5m` 合成；需先下载基础周期，否则合成数据为空 |

- **警告**：盘中下载较慢，可能影响策略性能；增量下载依赖本地已有数据，首次需指定 startTime 完整下载。
- **注意事项**：建议盘前/盘后下载；可设置界面「批量下载 - 定时下载」每日自动更新；期货合约代码区分大小写。

---

### 7. 行情获取 API

#### 7.1 `ContextInfo.get_market_data_ex(...)`（推荐）

- **用法**：见下方原型；在 `handlebar` 或 `after_init` 中调用，避免在 `init` 中依赖“最新行情”。
- **释义**：取订阅或本地的 K 线/分笔数据，可指定品种、周期、复权、起止时间；`subscribe=True` 时会拉取订阅的最新行情并可与本地历史拼接。
- **模块**：回测 / 实盘
- **原型**：

```python
ContextInfo.get_market_data_ex(
    fields=[],           # 数据字段列表，[] 表示默认全部
    stock_code=[],       # 合约代码列表，[] 表示主图标的
    period='follow',     # 周期，'follow' 跟随主图
    start_time='',       # 起始时间 %Y%m%d 或 %Y%m%d%H%M%S
    end_time='',         # 结束时间
    count=-1,            # 数据个数，-1 表示不按个数限制
    dividend_type='follow',  # 复权方式
    fill_data=True,      # 是否填充停牌
    subscribe=True       # 是否订阅（回测建议 False）
)
```

- **period 取值**：`tick`、`1m`、`5m`、`15m`、`30m`、`1h`、`1d`、`1w`、`1mon`、`1q`、`1hy`、`1y`，及 `l2quote`、`l2order`、`l2transaction` 等 Level2 周期
- **dividend_type**：`none` 不复权、`front` 前复权、`back` 后复权、`front_ratio` 前复权比例、`back_ratio` 后复权比例
- **VIP 权限要求**：`period='l2quote'`（委托队列）、`'l2order'`（逐笔委托）、`'l2transaction'`（逐笔成交）**需要开通 Level2 行情权限（VIP）**，否则返回空数据或报错

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **period → fields** | `period='tick'` 时返回 **Tick 字段**（lastPrice、askPrice1~5、bidPrice1~5 等）；`period` 为 K 线时返回 **Bar 字段**（open、high、low、close、volume 等），两者**不可混用** |
| **period → dividend_type** | `period='tick'` 时**无复权概念**，`dividend_type` 不生效；K 线周期下才需指定复权方式 |
| **subscribe → 场景** | **回测**时建议 `subscribe=False`（仅用本地数据）；**实盘**取最新行情时用 `subscribe=True`，会占用订阅额度 |
| **stock_code=[] → 主图** | `stock_code=[]` 时取**主图标的**；多标的时需明确传入列表，且订阅数会累加 |
| **count 与 start_time/end_time** | `count=-1` 表示不按个数限制；指定 `count` 时与 `start_time`/`end_time` 的优先级以客户端为准，一般**二选一** |

**返回值数据结构**：返回 `dict`，key 为 `stock_code`（str），value 为 `pd.DataFrame`。DataFrame 的 **index** 为 time（K 线时间或分笔时间），**columns** 为请求的 `fields`；未指定 fields 时返回默认全部列。列名（字段名）及解释如下。

**K 线周期（period 非 tick）时，返回值列名与解释**：

| 字段名 | 类型 | 解释 |
|--------|------|------|
| time | int/str | K 线时间 |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | int | 成交量 |
| amount | float | 成交额 |
| preClose | float | 昨收价 |
| suspendFlag | int | 停牌标志，0 正常，1 停牌 |
| highLimit | float | 涨停价（日线） |
| lowLimit | float | 跌停价（日线） |

**period=tick 时，返回值列名与解释**：与 3.8 **Tick 对象**一致，常用列名：`time`、`lastPrice`、`open`、`high`、`low`、`lastClose`、`volume`、`amount`、`stockStatus`（openInt）、`askPrice1~5`、`askVol1~5`、`bidPrice1~5`、`bidVol1~5` 等，详见 3.8 Tick。

- **返回**：`dict`，key 为 `stock_code`（str），value 为 `pd.DataFrame`（index 为 time，columns 为 fields）。
- **警告**：`init` 中**只能读到本地数据**，不会取到最新行情；订阅超过数量限制时返回数据会**前值填充**，非正确行情。
- **注意事项**：回测建议 `subscribe=False`；同一品种多周期会累加订阅数；gmd 系列函数不建议在 `init` 中调用。

#### 7.2 `ContextInfo.get_full_tick(stock_code=[])`（行情函数）

- **用法**：`ContextInfo.get_full_tick(stock_code=[])`。
- **释义**：取客户端缓存中的全推最新快照，**无历史**，盘中约 50ms 更新，无品种数限制；适合盘中取全市场或大量品种最新价、量等。
- **参数**：`stock_code: list` 代码列表，`[]` 表示全部；不传则默认 `[]`。
- **返回值数据结构**：返回 `dict`，key 为 `stock_code`（str），value 为 **Tick 字段组成的 dict**。value 中的**字段名**及**解释**见 3.8 **Tick 对象**，常用字段名：`time`、`lastPrice`、`open`、`high`、`low`、`lastClose`、`volume`、`amount`、`openInt`（stockStatus）、`askPrice1`~`askPrice5`、`askVol1`~`askVol5`、`bidPrice1`~`bidPrice5`、`bidVol1`~`bidVol5`。
- **返回**：同上。
- **警告**：仅**实盘**有效，回测中无全推数据；分笔行情默认**无五档**，需在行情源设置「全推行情」为五档级别。
- **注意事项**：行情中心控制订阅，交易中心影响全推；与 `get_market_data_ex(subscribe=True)` 的订阅机制不同。

#### 7.3 `ContextInfo.subscribe_quote(stock_code, period[, dividend_type, result_type, callback])`（行情函数）

- **用法**：`ContextInfo.subscribe_quote(stock_code, period[, dividend_type, result_type, callback])`。
- **释义**：向行情服务器订阅指定品种、周期的行情，可获当日实时 K 线或分笔；共有四种基础周期（分笔、1 分钟、5 分钟、日线），同一品种多周期累加计数。
- **参数**：`stock_code` str|list；`period` str（`'tick'`、`'1m'`、`'5m'`、`'1d'`）；`dividend_type` str 复权方式；`result_type` str（`'DataFrame'`/`'dict'`/`'list'`）；`callback` callable 推送回调。
- **返回**：`int`，订阅号，用于 `unsubscribe_quote(subId)` 反订阅。

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **period →  subscription 额度** | 同一品种订阅 `tick`、`1m`、`5m`、`1d` 四种**基础周期**会**累加**占用订阅数；超出限制后 `get_market_data_ex(subscribe=True)` 返回前值填充 |
| **callback ↔ result_type** | 传 `callback` 时由回调接收推送数据；不传 callback 时需用 `get_market_data_ex(subscribe=True)` 主动拉取，返回格式受 `result_type` 影响 |
| **stock_code 为 list** | 多品种订阅时，订阅数 = 品种数 × 周期数，需留意总量限制 |
- **警告**：仅实盘；订阅有**最大数量限制**，超过后返回数据会**前值填充**，非正确行情；`get_market_data_ex(subscribe=True)` 触发的自动订阅**无订阅号**，无法反订阅。
- **注意事项**：复数策略订阅同一品种不累加；建议在 `init` 中订阅并在策略结束时用 `unsubscribe_quote` 释放。

#### 7.4 `ContextInfo.subscribe_whole_quote(code_list, callback)`（行情函数）

- **用法**：`ContextInfo.subscribe_whole_quote(code_list, callback)`。
- **释义**：订阅全推行情，服务器将增量有变化的品种推送到回调；无需按品种数占用订阅额度，适合全市场或大量品种监控。
- **参数**：`code_list: list` 代码列表，可为空表示全市场；`callback: callable` 回调，参数为 `dict`（key 为 stock_code，value 为 tick 字典）。
- **返回**：无（None）；回调由客户端在收到推送时调用。
- **注意事项**：仅实盘；全推与订阅是两套机制，行情中心控制订阅，交易中心影响全推；分笔无五档时需在行情源设置五档级别。

#### 7.5 `ContextInfo.unsubscribe_quote(subId)`（行情函数）

- **用法**：`ContextInfo.unsubscribe_quote(subId)`。
- **释义**：按 `subscribe_quote` 返回的订阅号反订阅，释放该订阅占用的数量，便于策略动态调整订阅列表。
- **参数**：`subId: int`，由 `subscribe_quote` 返回的订阅号。
- **返回**：无（None）。
- **注意事项**：仅实盘；传入无效 subId 可能静默忽略；`get_market_data_ex(subscribe=True)` 触发的自动订阅无订阅号，无法通过本函数反订阅。

#### 7.6 其他行情与指标

| 函数 | 说明 | 参数/返回 |
|------|------|----------|
| `ContextInfo.get_svol(stockcode)` | 内盘成交量 | 返回 int |
| `ContextInfo.get_bvol(stockcode)` | 外盘成交量 | 返回 int |
| `ContextInfo.get_turnover_rate(stock_list, startTime, endTime)` | 换手率 | 需下载财务+日线 |
| `ContextInfo.get_longhubang(stock_list, startTime, endTime)` | 龙虎榜数据 | 返回 list/dict；**需 VIP 权限** |
| `ContextInfo.get_north_finance_change(period)` | 北向资金数据 | period 如 `'1d'`；**需 VIP 权限** |
| `ContextInfo.get_hkt_details(stockcode)` | 沪深港通持股明细 | 返回 list；**需 VIP 权限** |
| `ContextInfo.get_hkt_statistics(stockcode)` | 沪深港通持股统计 | 返回 dict；**需 VIP 权限** |
| `get_etf_info(stockcode)` | ETF 申赎清单 | 返回 dict |
| `get_etf_iopv(stockcode)` | ETF 盘中净值 IOPV | 返回 float |

**替代方案**：股票池超订阅数时，可用 `download_history_data` + `get_local_data` + `get_full_tick` 拼接历史与最新数据。

#### 7.7 模型调用（VBA 因子）

| 函数 | 说明 | 返回 |
|------|------|------|
| `subscribe_formula(formula_name, stock_code, period, start_time, end_time, count, dividend_type, extend_param, callback)` | 订阅 VBA 模型结果 | int 订阅号 |
| `unsubscribe_formula(subID)` | 反订阅 | None |
| `call_formula(formula_name, stock_code, period, ...)` | 调用 VBA 模型 | 模型返回值 |
| `call_formula_batch(formula_names, stock_codes, period, extend_params)` | 批量调用 | list |

#### 7.8 板块与交易日

| 函数 | 说明 | 参数/返回 |
|------|------|----------|
| `ContextInfo.get_stock_list_in_sector(sector)` | 板块成分股 | sector 如 `'沪深A股'`，返回 list[str] |
| `ContextInfo.get_trading_dates(start, end)` | 交易日列表 | start/end 如 `'20230101'`，返回 list[str] |
| `ContextInfo.get_instrument_detail(stockcode[, iscomplete])` | 合约详情 | 返回 dict，字段名及解释见 3.8 **InstrumentDetail 对象** |
| `ContextInfo.get_weight_in_index(stock, index)` | 股票在指数中的权重 | 返回 float |

**get_instrument_detail 返回值数据结构**：返回 **InstrumentDetail 对象**（dict 形式），key 为字段名，value 为对应值。**字段名及解释**见 3.8 **InstrumentDetail 对象**（InstrumentID、InstrumentName、UpperLimitPrice、LowerLimitPrice、VolumeMultiple、CirculatingShare、TotalShare、LongMarginRatio、ShortMarginRatio 等）。

#### 7.9 合约与期权

| 函数 | 说明 | 参数/返回 |
|------|------|----------|
| `get_st_status(stockcode)` | 当前 ST 状态 | 返回 str |
| `ContextInfo.get_his_st_data(stockcode)` | 历史 ST 状态 | 返回 list/dict |
| `ContextInfo.get_main_contract(codemarket[, date])` | 期货主力合约 | codemarket 如 `'AP.ZF'`，date 可选 |
| `ContextInfo.get_contract_multiplier(stockcode)` | 合约乘数 | 返回 float |
| `get_contract_expire_date(stockcode)` | 到期日 | 返回 str |
| `ContextInfo.get_his_contract_list(market)` | 已退市合约列表 | market 如 `'ZF'` |
| `ContextInfo.get_option_detail_data(optioncode)` | 期权详情 | 返回 dict |
| `get_option_list(underlying[, exchange])` | 期权列表 | 返回 list |
| `ContextInfo.bsm_price(option_type, S, K, T, r, sigma)` | BS 期权理论价 | option_type `'C'`/`'P'` |
| `ContextInfo.bsm_iv(option_type, S, K, T, r, price)` | BS 隐含波动率 | 返回 float |
| `ContextInfo.get_divid_factors(stockcode)` | 除权除息日与复权因子 | 返回 list |

---

### 8. 交易 API

#### 8.1 `passorder`（综合下单，交易函数）

- **用法（函数解释）**：综合下单接口，根据 opType/orderType 完成股票或期货的买卖、两融等委托。
- **模块**：回测 / 实盘
- **原型**：

```python
passorder(opType, orderType, accountid, orderCode, prType, price, volume,
          strategyName, quickTrade, userOrderId, ContextInfo)
```

- **参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| opType | int | 是 | 操作类型（见 13.2）：23 买入、24 卖出；期货 0~5 六键 |
| orderType | int | 是 | 下单方式（见 13.3）：1101 股数、1102 金额等 |
| accountid | str | 是 | 资金账号 |
| orderCode | str | 是 | 合约代码，如 `'000001.SZ'` |
| prType | int | 是 | 选价类型（见 13.4）：5 最新价、11 限价、14 对手价等 |
| price | float | 是 | 限价时填委托价，否则可填 `-1` |
| volume | int | 是 | 数量（股/手/元，由 orderType 末位决定） |
| strategyName | str | 是 | 策略名，用于区分委托来源、过滤 get_trade_detail_data |
| quickTrade | int | 是 | 0=逐 K 线生效，1=非历史 bar 立即，2=任何情况立即 |
| userOrderId | str | 否 | 投资备注，对应 Order.m_strRemark |
| ContextInfo | object | 是 | 上下文对象 |

- **返回**：无（None）。

**参数联动与选择提示**（不同参数组合会相互影响，选择时需一并考虑）：

| 联动关系 | 说明 |
|----------|------|
| **prType → price** | `prType=11`（限价）时 **price 必须填有效委托价**，否则报「指定价无效」；`prType=5/13/14/15/16` 时 price 可填 `-1`，系统按对应规则取价 |
| **prType → 行情** | `prType=14`（对手价）、`15`（最优五档剩余转限价）**依赖五档行情**，全推需为五档级别，否则报「对手价无效，无法下单」 |
| **orderType → volume** | `orderType` 末位 **1**（如 1101、1201）表示 volume 为**数量**（股/手）；末位 **2**（如 1102、1202）表示 volume 为**金额**（元） |
| **opType → orderType** | 股票（opType 23/24）可用 1101/1102；**期货**（opType 0~5）仅支持 **1101**（按手数），无金额方式；信用（27/28 等）以券商支持为准 |
| **opType → orderCode / account** | 股票用 `strAccountType='STOCK'`、代码如 `000001.SZ`；期货用 `'FUTURE'`、代码如 `AP401.ZF`；两融用 `'CREDIT'`，需对应信用账号 |
| **quickTrade → 调用场景** | 在 **handlebar** 逐 K 线下单用 `0`；handlebar 中**盘中立即**下单用 `1`；**定时器、subscribe 回调、after_init** 中**必须**用 `2`，否则信号被丢弃 |
| **quickTrade=2 → 状态存储** | 必须用全局变量（如 `g`）存委托状态，**不能**存 ContextInfo（会随 bar 回退丢失） |

- **警告**：对手价（prType=14）需全推行情为**五档级别**，否则会报「对手价无效，无法下单」；限价（prType=11）且 price 为 0 时可能报「指定价无效」。定时器/行情回调中未传 `quickTrade=2` 时，信号可能被丢弃。
- **注意事项**：定时器/行情回调中下单**必须**传 `quickTrade=2`；handlebar 逐 K 线传 `0`，盘中立刻下单传 `1`。`quickTrade=2` 时需用全局变量（如 `g`）存委托状态，不能存 ContextInfo；否则因 ContextInfo 回退导致状态丢失。
- **示例**：
```python
passorder(23, 1101, "12345678", "000001.SZ", 5, -1, 100, "my_strategy", 2, "tag1", ContextInfo)
```

#### 8.2 `cancel(orderId, accountId, accountType, ContextInfo)`（交易函数）

- **用法**：`cancel(orderId, accountId, accountType, ContextInfo)`。
- **释义**：按委托号向柜台发出撤单请求；仅表示请求是否成功发出，不保证最终已撤。
- **参数**：`orderId` str（取 Order.m_strOrderSysID）；`accountId` str；`accountType` str（见 3.2）；`ContextInfo` 对象。

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **orderId 来源** | 必须取 **Order.m_strOrderSysID**（柜台委托号），不能用自定义 `userOrderId`（对应 Order.m_strRemark） |
| **accountId + accountType** | 必须与**下单时的账号**一致；委托是在哪个账号下的，撤单就用哪个 accountId 和 accountType |
- **返回**：`bool`，是否成功发出撤单信号。
- **警告**：仅实盘有效；回测中无撤单概念。对已成交或已撤的委托再撤可能返回 False 或柜台报错。
- **注意事项**：orderId 必须与当前账号、市场一致；撤单结果以柜台回报或 `order_callback` 状态为准。

#### 8.3 `get_trade_detail_data(accountID, strAccountType, strDatatype[, strategyName])`（交易函数）

- **用法**：`get_trade_detail_data(accountID, strAccountType, strDatatype[, strategyName])`；参数类型：`accountID` str，`strAccountType` str（见 3.2），`strDatatype` str，`strategyName` str 可选。
- **strDatatype 取值（数据字典）**：`ACCOUNT`、`POSITION`、`POSITION_STATISTICS`、`ORDER`、`DEAL`、`TASK`。

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **strDatatype → 返回值字段** | `strDatatype` 决定返回 list 中元素类型：`ORDER`→Order 对象（撤单取 `m_strOrderSysID`）、`DEAL`→Deal、`TASK`→CTaskDetail（撤任务取 `m_nTaskId`）、`POSITION`→Position、`ACCOUNT`→Account |
| **strategyName → 过滤范围** | 传 `strategyName` 可**仅查该策略**的委托/成交；不传则返回该账号下**全部**委托/成交 |
| **accountID + strAccountType** | 必须与 `passorder` 使用的账号、类型一致；期货账号用 `FUTURE`，股票用 `STOCK`，两融用 `CREDIT` |
- **返回值数据结构**：返回 `list`，元素类型由 `strDatatype` 决定，各类型的**字段名**及**解释**见 3.8 数据结构。
  - `strDatatype='ACCOUNT'`：list 中元素为 **Account 对象**，字段见 3.8 Account。
  - `strDatatype='ORDER'`：list 中元素为 **Order 对象**，字段见 3.8 Order；`cancel(orderId, ...)` 的 orderId 取 `Order.m_strOrderSysID`。
  - `strDatatype='DEAL'`：list 中元素为 **Deal 对象**，字段见 3.8 Deal。
  - `strDatatype='POSITION'`：list 中元素为 **Position 对象**，字段见 3.8 Position。
  - `strDatatype='TASK'`：list 中元素为 **CTaskDetail 对象**，字段见 3.8 CTaskDetail；`cancel_task(taskId, ...)` 的 taskId 取 `CTaskDetail.m_nTaskId`。
  - `strDatatype='POSITION_STATISTICS'`：list 中元素为持仓统计对象，字段以客户端为准。
- **返回**：同上。
- **警告**：数据来自客户端**本地缓存**，非实时查柜台；**下单后需等待**才能查到新委托/成交，不宜立即用本接口校验下单是否成功。
- **注意事项**：传 `strategyName` 可仅查该策略的委托/成交。

#### 8.4 `algo_passorder` / `smart_algo_passorder`（交易函数）

- **algo_passorder**  
  - **用法**：参数与 `passorder` 一致，可多传 `userOrderParam` 字典。  
  - **释义**：算法拆单，将大单拆成多笔小单按策略执行。  
  - **参数**：同 passorder；可选 `userOrderParam` 字典：`OrderType`、`MaxOrderCount`、`SuperPriceType`、`SliceTime` 等。  
  - **返回**：无（None）；任务号通过主推 `task_callback` 或 `get_trade_detail_data(..., 'TASK')` 获取。  
  - **参数联动**：继承 passorder 的 opType/orderType/prType/price/volume 联动规则；`userOrderParam` 中的 `SuperPriceType` 与 passorder 的 prType 可能冲突，以客户端为准。  
  - **警告**：需开通算法交易权限；拆单结果以柜台与主推为准。  
  - **注意事项**：撤单/暂停用 `cancel_task`、`pause_task`，taskId 取 CTaskDetail.m_nTaskId。

- **smart_algo_passorder**  
  - **用法**：参数含 passorder 相关参数及智能算法参数。  
  - **释义**：智能算法下单（VWAP/TWAP 等），按时间或成交量分布执行。  
  - **参数**：含 `smartAlgoType`、`limitOverRate`、`minAmountPerOrder`、`startTime`、`endTime` 等。  
  - **返回**：无（None）。  
  - **参数联动**：`startTime`/`endTime` 需在交易时段内；`limitOverRate` 与限价范围相关；`minAmountPerOrder` 影响拆单粒度。  
  - **警告**：需开通智能算法交易权限；参数非法或时间范围不合理可能报错。  
  - **注意事项**：任务状态通过 `task_callback` 或 `get_trade_detail_data(..., 'TASK')` 查询。

#### 8.5 任务控制（交易函数）

| 函数 | 用法 | 释义 | 参数 | 返回 | 警告及注意事项 |
|------|------|------|------|------|----------------|
| `cancel_task(taskId, accountId, accountType, ContextInfo)` | 见左 | 撤销智能算法任务 | taskId 取 CTaskDetail.m_nTaskId（str/int），accountId/accountType 同 passorder，ContextInfo | 无 / bool | **参数联动**：taskId 必须来自 `get_trade_detail_data(..., 'TASK')` 或 `task_callback` 的 CTaskDetail.m_nTaskId；accountId/accountType 需与下单账号一致。仅对 algo/smart_algo 任务有效 |
| `pause_task(taskId, accountId, accountType, ContextInfo)` | 见左 | 暂停智能算法任务 | 同上 | 无 / bool | 同上；暂停后可 `resume_task` 继续 |
| `resume_task(taskId, accountId, accountType, ContextInfo)` | 见左 | 继续已暂停的智能算法任务 | 同上 | 无 / bool | 同上 |

#### 8.6 交易查询（补充）

| 函数 | 用法 | 释义 | 参数 | 返回 | 警告及注意事项 |
|------|------|------|------|------|----------------|
| `get_last_order_id(accountID, strAccountType, strDatatype[, strategyName])` | 见左 | 获取该账号该类型下最新一条委托的委托号 | accountID, strAccountType, strDatatype, strategyName 可选 | str | **下单后需等待**才能查到新委托；数据来自本地缓存 |
| `get_value_by_order_id(orderId, accountID, strAccountType, strDatatype)` | 见左 | 按委托号查询该委托及对应成交 | orderId 取 Order.m_strOrderSysID，其余同上 | list | 同上；orderId 需与账号一致 |
| `get_history_trade_detail_data(accountID, strAccountType, strDatatype, strStartDate, strEndDate)` | 见左 | 查询历史区间内的委托/成交/持仓等 | strStartDate/strEndDate 如 `'20230101'` | list | 仅支持历史区间查询，数据来源以客户端为准 |
| `get_ipo_data([type])` | 见左 | 当日可申购的新股/新债列表 | type 可选 `'STOCK'`/`'BOND'` | list | 仅当日有效；type 过滤股票或债券 |
| `get_new_purchase_limit(accid)` | 见左 | 该账号新股申购额度 | accid: str | dict/list | 需有对应权限；返回值结构以客户端为准 |

#### 8.7 两融与期权

| 函数 | 说明 |
|------|------|
| `get_assure_contract(accId)` | 担保标的明细 |
| `get_enable_short_contract(accId)` | 可融券明细 |
| `query_credit_account(accountId, seq, ContextInfo)` | 查询信用账户，结果经 `credit_account_callback` 回调 |
| `query_credit_opvolume(accountId, stockCode, opType, prType, price, seq, ContextInfo)` | 两融最大可下单量，结果经 `credit_opvolume_callback` 回调；**参数联动**：opType/prType/price 需与拟下单的 passorder 参数一致，否则可操作量可能不准确 |
| `get_unclosed_compacts(accountID, 'CREDIT')` | 未了结负债合约 |
| `get_closed_compacts(accountID, 'CREDIT')` | 已了结负债合约 |
| `get_option_subject_position(accountID)` | 期权标的持仓 |
| `get_comb_option(accountID)` | 期权组合持仓 |
| `get_hkt_exchange_rate(accountID, accountType)` | 沪深港通汇率 |

#### 8.8 篮子

- **get_basket(basketName)** / **set_basket(basketDict)**：获取/设置股票篮子  
  - basketDict 含 `name`、`stocks`（每项含 `stock`、`weight`、`quantity`、`optType`）

**set_basket 参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **quantity ↔ weight** | 每只成分股可指定 `quantity`（数量）或 `weight`（权重），二者择一或按客户端约定配合使用；组合下单时决定按数量还是按权重分配 |
| **optType → 买卖方向** | `optType` 区分买入/卖出，需与 passorder 的 opType 对应（如 23 买、24 卖） |
| **stock 格式** | 需与 passorder 的 orderCode 一致，如 `000001.SZ` |

#### 8.9 成交回报主推函数（实时主推）

> **重要**：1. 仅在**实盘运行模式**下生效；2. 必须在 `init` 里调用 **`ContextInfo.set_account(account)`** 订阅资金账号，否则所有主推回调**不生效**。

**订阅账号**：

```python
def init(ContextInfo):
    account = '123456'  # 策略交易界面运行时由策略配置自动赋值
    ContextInfo.set_account(account)
```

**主推函数列表**（成交回报实时主推函数）：

| 函数名 | 说明 | 触发时机 | 参数（数据类型） | 返回类型 |
|--------|------|----------|------------------|----------|
| `account_callback` | 资金账号状态变化 | 账号资金/资产变动 | (ContextInfo, accountInfo: Account) | 无 |
| `order_callback` | 委托状态变化 | 委托报出、部成、已成、已撤等 | (ContextInfo, orderInfo: Order) | 无 |
| `deal_callback` | 成交状态变化 | 委托成交时 | (ContextInfo, dealInfo: Deal) | 无 |
| `position_callback` | 持仓状态变化 | 持仓变动时 | (ContextInfo, positionInfo: Position) | 无 |
| `task_callback` | 任务状态变化 | 智能算法任务状态变化 | (ContextInfo, taskInfo: CTaskDetail) | 无 |
| `orderError_callback` | 异常下单 | 下单失败时 | (ContextInfo, orderArgs: PassOrderArguments, errMsg: str) | 无 |
| `credit_account_callback` | 信用账户明细 | query_credit_account 查询结果 | (ContextInfo, seq, result) | 无 |
| `credit_opvolume_callback` | 两融最大可下单量 | query_credit_opvolume 查询结果 | (ContextInfo, accid: str, seq, ret: int, result) | 无 |

**8.9.1 account_callback**

- **用法**：`account_callback(ContextInfo, accountInfo)`。
- **释义**：资金账号状态变化时由客户端调用，用于同步资产、可用、冻结等。
- **参数**：`ContextInfo` 上下文；`accountInfo` 为账号对象（Account）或信用账号对象。
- **返回**：无（None）；无需返回值。
- **警告**：仅在**实盘运行模式**下生效；未在 `init` 中调用 `ContextInfo.set_account(account)` 则**不会触发**。
- **注意事项**：回调内避免耗时操作，以免阻塞其他策略；常用字段：`m_dBalance`、`m_dAvailable`、`m_dFrozenCash`、`m_dInstrumentValue`、`m_dPositionProfit`、`m_strAccountID` 等。

**8.9.2 order_callback**

- **用法**：`order_callback(ContextInfo, orderInfo)`。
- **释义**：委托状态变化时由客户端调用（已报、部成、已成、已撤、废单等）。
- **参数**：`orderInfo` 为委托对象（Order）。
- **返回**：无（None）。
- **警告**：同上，仅实盘且需 `set_account`；若未订阅账号，下单后可能只收到 `orderError_callback` 而无 `order_callback`。
- **注意事项**：常用字段：`m_strInstrumentID`、`m_strExchangeID`、`m_strOrderSysID`、`m_nVolumeTotalOriginal`、`m_nVolumeTraded`、`m_dTradedPrice`、`m_nOrderStatus`、`m_strRemark`、`m_strSource` 等。

**8.9.3 deal_callback**

- **用法**：`deal_callback(ContextInfo, dealInfo)`。
- **释义**：每笔成交发生时由客户端调用，用于记录成交明细。
- **参数**：`dealInfo` 为成交对象（Deal）。
- **返回**：无（None）。
- **警告**：仅实盘且需 `set_account`。
- **注意事项**：常用字段：`m_strInstrumentID`、`m_strOrderSysID`、`m_dPrice`、`m_nVolume`、`m_dTradeAmount`、`m_strRemark`、`m_strTradeID`、`m_strTradeTime` 等。

**8.9.4 position_callback**

- **用法**：`position_callback(ContextInfo, positionInfo)`。（官方文档拼写为 positonInfo，实际参数名为 positionInfo。）
- **释义**：持仓发生变化时由客户端调用。
- **参数**：`positionInfo` 为持仓对象（Position）。
- **返回**：无（None）。
- **警告**：仅实盘且需 `set_account`。
- **注意事项**：常用字段：`m_strInstrumentID`、`m_nVolume`、`m_nCanUseVolume`、`m_dOpenPrice`、`m_dInstrumentValue`、`m_dPositionProfit` 等。

**8.9.5 task_callback**

- **用法**：`task_callback(ContextInfo, taskInfo)`。
- **释义**：智能算法任务状态变化时由客户端调用。
- **参数**：`taskInfo` 为任务对象（CTaskDetail）。
- **返回**：无（None）。
- **警告**：仅实盘且需 `set_account`。
- **注意事项**：常用字段：`m_nTaskId`、`m_stockCode`、`m_nNum`、`m_nBusinessNum`、`m_eStatus`、`m_strMsg`、`m_strRemark` 等。

**8.9.6 orderError_callback**

- **用法**：`orderError_callback(ContextInfo, orderArgs, errMsg)`。
- **释义**：下单被柜台拒绝或参数无效时由客户端调用（如指定价无效、对手价无效等）。
- **参数**：`orderArgs` 为下单参数对象（PassOrderArguments）；`errMsg` 为错误信息字符串。
- **返回**：无（None）。
- **警告**：仅实盘且需 `set_account`；未订阅账号时可能只收到本回调而无正常委托回报。
- **注意事项**：orderArgs 字段：`accountID`、`orderCode`、`opType`、`orderType`、`prType`、`strategyName` 等；可根据 errMsg 提示修改 prType 或行情源设置。

**8.9.7 credit_account_callback**

- **用法**：`credit_account_callback(ContextInfo, seq, result)`。
- **释义**：`query_credit_account` 的异步结果通过本回调返回。
- **参数**：`seq` 为 `query_credit_account` 传入的查询序号；`result` 为信用账户明细对象。
- **返回**：无（None）。
- **注意事项**：需在调用 `query_credit_account` 后等待回调，通过 seq 匹配请求与结果。

**8.9.8 credit_opvolume_callback**

- **用法**：`credit_opvolume_callback(ContextInfo, accid, seq, ret, result)`。
- **释义**：`query_credit_opvolume` 的异步结果通过本回调返回；`ret` 表示查询是否成功。
- **参数**：`accid` 查询账号；`seq` 查询序号；`ret` 结果状态（1 正常，-1 查询中，-2 账号非法，-3 参数非法，-4 超时报错）；`result` 查询结果。
- **返回**：无（None）。
- **警告**：`ret != 1` 时 result 可能无效；超时或参数非法时需检查账号与参数。
- **注意事项**：根据 ret 判断后再使用 result；两融下单前可先查询可操作量。

**示例**：

```python
#coding:gbk

def init(ContextInfo):
    ContextInfo.set_account(account)

def order_callback(ContextInfo, orderInfo):
    print(f"代码:{orderInfo.m_strInstrumentID} 委托号:{orderInfo.m_strOrderSysID} "
          f"成交数量:{orderInfo.m_nVolumeTraded} 备注:{orderInfo.m_strRemark}")

def deal_callback(ContextInfo, dealInfo):
    print(f"成交 {dealInfo.m_strInstrumentID} 价格:{dealInfo.m_dPrice} "
          f"数量:{dealInfo.m_nVolume} 金额:{dealInfo.m_dTradeAmount}")

def orderError_callback(ContextInfo, orderArgs, errMsg):
    print(f"下单异常: {errMsg}")
```

---

### 9. 财务数据 API

#### 9.1 `ContextInfo.get_financial_data(fieldList, stockList, startDate, endDate, report_type)`（财务数据）

- **用法**：`ContextInfo.get_financial_data(fieldList, stockList, startDate, endDate, report_type)`。
- **释义**：按起止日期、报告类型获取股票财务指标；支持按公告日或报告期，按公告日可避免未来函数。
- **参数**：`fieldList: list` 字段列表，格式 `'表名.字段名'`；`stockList: list` 股票代码列表；`startDate/endDate: str` 如 `'20230101'`；`report_type: str` 取 `'announce_time'`（按公告日）或 `'report_time'`（按报告期）。
- **返回值数据结构**：返回 `dict`，key 为股票代码（str），value 为 `pd.DataFrame` 或类似结构。DataFrame 的**列名**由 `fieldList` 指定，格式为 `'表名.字段名'`（如 `'ASHAREINCOME.revenue'`）。各表的**字段名及解释**见下文 9.2 主要表与字段；按交易日填充到每日。

**参数联动与选择提示**：

| 联动关系 | 说明 |
|----------|------|
| **report_type → 未来函数风险** | `report_type='report_time'` 按**报告期**取数，公告未出时可能取到**未来数据**，回测会产生未来函数；**回测推荐** `'announce_time'`（按公告日） |
| **fieldList → 表名** | 字段格式必须为 `'表名.字段名'`，表名需与 9.2 中定义一致（如 `ASHAREINCOME`、`ASHAREBALANCESHEET`），否则返回空或报错 |
| **数据下载** | 使用前需在界面「数据管理 - 财务数据」下载对应表数据；未下载的表无法取数 |
- **返回**：同上。
- **警告**：`report_type='report_time'` 时可能取到**未来数据**（报告期已定但公告未出），回测会产生未来函数。
- **注意事项**：使用前需在界面「数据管理 - 财务数据」下载；`get_raw_financial_data` 取原始数据不按日填充；`get_last_volume`、`get_total_share` 取流通股本/总股本。

#### 9.2 主要表与字段（完整，字段名与解释）

`get_financial_data` 的 `fieldList` 使用格式 `'表名.字段名'`，如 `'ASHAREBALANCESHEET.tot_assets'`。下表列出各表**字段名**及**解释**，供返回值 DataFrame 列名对照。

**ASHAREBALANCESHEET 资产负债表**

| 字段名 | 解释 |
|--------|------|
| fix_assets | 固定资产 |
| tot_assets | 总资产 |
| tot_liab | 总负债 |
| cap_stk | 股本 |
| cash_equivalents | 货币资金 |
| invst | 投资 |
| receiv | 应收账款 |
| tot_cur_assets | 流动资产合计 |
| tot_nca | 非流动资产合计 |

**ASHAREINCOME 利润表**

| 字段名 | 解释 |
|--------|------|
| revenue | 营业收入 |
| oper_profit | 营业利润 |
| tot_profit | 利润总额 |
| net_profit_excl_min_int_inc | 净利润（不含少数股东） |
| net_profit | 净利润 |
| gross_profit | 毛利 |

**ASHARECASHFLOW 现金流量表**

| 字段名 | 解释 |
|--------|------|
| net_cash_flows_oper_act | 经营活动现金流量净额 |
| stot_cash_inflows_oper_act | 经营活动现金流入小计 |
| stot_cash_outflows_oper_act | 经营活动现金流出小计 |
| net_cash_flows_inv_act | 投资活动现金流量净额 |
| net_cash_flows_fnc_act | 筹资活动现金流量净额 |

**CAPITALSTRUCTURE 股本表**

| 字段名 | 解释 |
|--------|------|
| total_capital | 总股本 |
| circulating_capital | 流通股本 |
| free_float_capital | 自由流通股本 |

**PERSHAREINDEX 主要指标**

| 字段名 | 解释 |
|--------|------|
| s_fa_eps_basic | 基本每股收益 |
| du_return_on_equity | 净资产收益率 |
| gross_profit | 毛利率 |
| net_profit | 净利润 |

**TOP10HOLDER / TOP10FLOWHOLDER 十大股东/流通股东**

| 字段名 | 解释 |
|--------|------|
| name | 股东名称 |
| quantity | 持股数量 |
| ratio | 持股比例 |
| rank | 排名 |

**SHAREHOLDER 股东数**

| 字段名 | 解释 |
|--------|------|
| shareholder | 股东总数 |
| shareholderA | A 股股东数 |

---

### 10. 引用函数与绘图函数

#### 10.1 引用函数

| 函数 | 用法 | 释义 | 参数 | 返回 | 警告及注意事项 |
|------|------|------|------|------|----------------|
| `ext_data(extdataname, stockcode, deviation, ContextInfo)` | 见左 | 获取扩展数据；deviation：0 不偏移，N 向右 N，-N 向左 N | extdataname: str, stockcode: str, deviation: int, ContextInfo | float | 扩展数据需在扩展数据管理中先配置；未配置或代码错误可能返回异常值 |
| `ext_data_rank(...)` | 同上 | 扩展数据在截面上的排名 | 同上 | int | 同上 |
| `ext_data_rank_range(..., start, end, ContextInfo)` | 同上 | 指定区间内的排名 | 多 start, end: str | 区间内排名 | 同上；start/end 为日期或时间字符串 |
| `ext_data_range(..., start, end, ContextInfo)` | 同上 | 指定区间的扩展数据值 | 同上 | 区间值 | 同上 |
| `get_factor_value(factorname, stockcode, deviation, ContextInfo)` | 见左 | 获取因子在指定 bar 的值 | factorname: str, 其余同上 | float | 因子需在因子管理中配置；建议用 `call_formula` 替代部分场景 |
| `get_factor_rank(...)` | 同上 | 因子在截面上的排名 | 同上 | int | 同上 |
| `call_vba(factorname, stockcode[, period, dividend_type, barpos], ContextInfo)` | 见左 | 调用 VBA 模型（已弃用） | factorname, stockcode, 可选 period, dividend_type, barpos, ContextInfo | 模型返回值 | **已弃用**，建议用 `call_formula`；VBA 未就绪可能报错 |

#### 10.2 绘图函数

| 函数 | 用法 | 释义 | 参数 | 返回 | 警告及注意事项 |
|------|------|------|------|------|----------------|
| `ContextInfo.paint(name, value, index, line_style[, color, limit])` | 见左 | 在 index 位置画数值为 value 的线 | name: str, value: float, index: int, line_style: int, color: str, limit: str | 无 | **参数联动**：同一 `name` 在同一 bar 内多次 paint 会**覆盖**；`limit='noaxis'` 不参与坐标、`limit='nodraw'` 不画线；line_style：0 曲线、42 柱状；color：blue/brown/cyan/green/magenta/red/white/yellow。仅策略附在 K 线图时有效 |
| `ContextInfo.draw_text(condition, position, text)` | 见左 | 在 position 位置显示文字 | condition: bool, position: int/float, text: str | 无 | 同上；position 为 K 线位置或价格 |
| `ContextInfo.draw_number(cond, height, number, precision)` | 见左 | 在 height 位置显示数字 | cond: bool, height: float, number: float, precision: int(0-7) | 无 | 同上；precision 为小数位数 |
| `ContextInfo.draw_vertline(cond, number1, number2[, color, limit])` | 见左 | 画垂直线段 | cond: bool, number1/number2: float, color, limit | 无 | 同上；number1/number2 为起止位置 |
| `ContextInfo.draw_icon(cond, height, type)` | 见左 | 在 height 位置绘制图标 | cond: bool, height: float, type: int | 无 | 同上；type：0 矩形、1 椭圆 |

**警告**：绘图函数仅在策略**附在 K 线图**运行时有效，独立运行或非 K 线驱动可能不显示。**注意事项**：limit `'noaxis'` 不参与坐标、`'nodraw'` 不画线；避免在单 bar 内重复 paint 同一 name 导致覆盖。

---

### 11. 回测专用交易函数（仅回测生效）

以下函数**仅回测**可用，实盘/模拟盘不可用；撮合规则见第 12 节。

| 函数 | 用法 | 释义 | 参数 | 返回 | 警告及注意事项 |
|------|------|------|------|------|----------------|
| `order_lots(stockcode, lots[, style, price], ContextInfo[, accId])` | 见左 | 按手数下单（期货） | stockcode: str, lots: int, style: str, price: float, ContextInfo, accId: str 可选 | 无 | **仅回测**；style：LATEST/FIX/HANG/COMPETE/MARKET 等 |
| `order_value(stockcode, value[, style, price], ContextInfo[, accId])` | 见左 | 按金额下单（股票） | value: float 金额元 | 无 | 仅回测；按金额换算股数，受最小交易单位限制 |
| `order_percent(stockcode, percent[, style, price], ContextInfo[, accId])` | 见左 | 按占总资产比例下单 | percent: float 0~1 | 无 | 仅回测 |
| `order_target_value(stockcode, tar_value[, style, price], ContextInfo[, accId])` | 见左 | 调仓至目标持仓价值 | tar_value: float | 无 | 仅回测；不足则买，超出则卖 |
| `order_target_percent(stockcode, tar_percent[, style, price], ContextInfo[, accId])` | 见左 | 调仓至目标持仓比例 | tar_percent: float 0~1 | 无 | 仅回测 |
| `order_shares(stockcode, shares[, style, price], ContextInfo[, accId])` | 见左 | 按股数下单（股票） | shares: int | 无 | 仅回测 |
| `buy_open` / `sell_open` / `buy_close_tdayfirst` / `buy_close_ydayfirst` / `sell_close_tdayfirst` / `sell_close_ydayfirst` | 见左 | 期货开平仓（平今/平昨优先） | stockcode, lots, style, price, ContextInfo, accId | 无 | 仅回测；平今/平昨由函数名区分 |

**返回**：上述函数均无返回值（None）。

**参数联动与选择提示**（回测专用，按数量/金额/比例选择不同函数，参数含义不同）：

| 联动关系 | 说明 |
|----------|------|
| **函数选择 → 主参数** | `order_lots`/`order_shares` 主参数为**数量**（股/手）；`order_value` 主参数为**金额**（元）；`order_percent`/`order_target_percent` 主参数为**比例**（0~1） |
| **order_target_* → 调仓逻辑** | `order_target_value`/`order_target_percent` 会**先算目标持仓**，不足则买、超出则卖；其他函数为增量下单 |
| **style → price** | `style='FIX'` 时需传有效 `price`；`style='LATEST'`、`'MARKET'` 等市价类可不依赖 price，以客户端为准 |
| **accId** | 多账号回测时可选传；不传则用默认账号 |

**警告**：实盘/模拟盘中调用会报错或无效。**注意事项**：回测撮合规则为指定价在 K 线高低点内按指定价，超范围按收盘价，超可用量按可用量；style 可选 `'LATEST'`、`'FIX'`、`'HANG'`、`'COMPETE'`、`'MARKET'` 等。

### 12. 回测与实盘差异

| 项目 | 回测 | 实盘 |
|------|------|------|
| 数据来源 | 本地数据，`subscribe=False` | 订阅/全推，需下载或订阅 |
| 撮合规则 | 指定价在 K 线高低点内按指定价；超范围按收盘价；超可用量按可用量 | 以交易所为准；超 2% 价格笼子废单 |
| 执行环境 | 副图模式，勿选主图/主图叠加 | 模型交易界面，模拟/实盘模式 |
| 周期设置 | 右侧基本信息中默认周期、主图生效 | 以主图 K 线周期为准 |
| 回测右侧 | 默认周期、默认主图在「我的界面」点回测时生效；在行情界面 K 线下点回测以当前 K 线周期、品种为准 | - |

### 13. 数据字典与枚举常量

本节汇总所有枚举常量、变量名称及取值，便于与变量约定、数据结构、各 API 对照使用。

#### 13.1 证券状态 openInt（股票）

| 时间段 | 状态 | 编码 |
|--------|------|------|
| 9:15-9:25 | 盘前集合竞价 | 12 |
| 9:25-14:57 | 盘中连续竞价 | 13 |
| 14:57-15:00 | 盘后集合竞价 | 18 |
| 15:00 | 收盘 | 15 |

**期货**：0 未知、1 开盘前、2 集合竞价、3 连续交易、4 休市、5 闭市

#### 13.2 opType（操作类型）完整

| 数值 | 说明 |
|------|------|
| 23 | 股票买入 |
| 24 | 股票卖出 |
| 27 | 融资买入 |
| 28 | 融券卖出 |
| 29 | 买券还券 |
| 30 | 融券卖出 |
| 31 | 卖券还款 |
| 33 | 担保品买入 |
| 34 | 担保品卖出 |
| 0 | 期货买开 |
| 1 | 期货卖平（平昨） |
| 2 | 期货卖平（平今） |
| 3 | 期货卖开 |
| 4 | 期货买平（平昨） |
| 5 | 期货买平（平今） |

#### 13.3 orderType（下单方式）完整

| 数值 | 说明 |
|------|------|
| 1101 | 单股、股/手方式 |
| 1102 | 单股、金额方式（仅股票） |
| 1201 | 账号组、股数方式 |
| 1202 | 账号组、金额方式 |
| 2101 | 组合、单账号、按数量 |
| 2102 | 组合、单账号、按权重 |

末位 1 表示 volume 为数量，末位 2 表示 volume 为金额。

#### 13.4 prType（下单选价类型）完整

| 数值 | 说明 |
|------|------|
| 5 | 最新价 |
| 11 | 指定价（限价） |
| 13 | 挂单价（本方一档） |
| 14 | 对手价 |
| 15 | 最优五档即时成交剩余转限价 |
| 16 | 市价剩余转限价 |

对手价（14）需全推行情五档级别，否则报错。

#### 13.5 quickTrade（快速下单）

| 数值 | 说明 | 适用场景 |
|------|------|----------|
| 0 | 否（逐 K 线生效） | handlebar 逐 K 线模式 |
| 1 | 是（非历史 bar 立即） | handlebar 盘中立刻下单 |
| 2 | 是（任何情况立即） | 定时器、行情回调、after_init |

#### 13.6 委托状态（EEntrustStatus）

| 数值 | 说明 |
|------|------|
| 49 | 待报 |
| 50 | 已报 |
| 51 | 部成待撤 |
| 52 | 已撤 |
| 54 | 部撤 |
| 55 | 部成 |
| 56 | 已成 |
| 57 | 废单 |

#### 13.7 安装与使用

- 安装路径**勿放 C 盘**，避免权限问题；若只能装 C 盘，请「以管理员权限启动」
- 首次使用需在「数据管理 - 下载 Python 库」补全依赖，安装后重启客户端；盘中下载慢，建议盘前/盘后更新
- 策略无反应时：检查是否有其他策略运行、尝试「恢复默认布局」、重启客户端

#### 13.8 交易所委托数量（节选）

| 市场 | 限价单笔最大 | 市价单笔最大 |
|------|-------------|-------------|
| 科创板 | 10 万股 | 5 万股 |
| 创业板 | 30 万股 | 15 万股 |
| 主板 | 100 万股 | 视券商 |

#### 13.9 辅助与已弃用

| 函数 | 说明 |
|------|------|
| `ContextInfo.get_bar_timetag(barpos)` | 获取 bar 时间戳 |
| `timetag_to_datetime(timetag, fmt)` | 时间戳转字符串 |
| `get_local_data` | 取本地数据，**已弃用**，用 `get_market_data_ex(subscribe=False)` |
| `get_history_data` | **已弃用**，用 `get_market_data_ex` |
| `get_market_data` | **已弃用**，用 `get_market_data_ex` |
| `set_universe` | **已弃用**，用 `subscribe_quote`（订阅无订阅号无法反订阅） |

#### 13.10 数据字典汇总（枚举常量与变量名称）

**strAccountType（账号类型）**：FUTURE、STOCK、CREDIT、FUTURE_OPTION、STOCK_OPTION、HUGANGTONG、SHENGANGTONG。

**strDatatype（查询数据类型）**：ACCOUNT、POSITION、POSITION_STATISTICS、ORDER、DEAL、TASK。

**openInt/stockStatus（证券状态，股票）**：12 盘前集合竞价、13 盘中连续竞价、18 盘后集合竞价、15 收盘；期货：0 未知、1 开盘前、2 集合竞价、3 连续交易、4 休市、5 闭市。

**opType（操作类型）**：23 股票买入、24 股票卖出、27 融资买入、28 融券卖出、29 买券还券、30 融券卖出、31 卖券还款、33 担保品买入、34 担保品卖出；期货 0~5 六键。

**orderType（下单方式）**：1101/1102 单股股数/金额、1201/1202 账号组股数/金额、2101/2102 组合数量/权重。

**prType（下单选价类型）**：5 最新价、11 限价、13 挂单价、14 对手价、15 最优五档剩余转限价、16 市价剩余转限价。

**quickTrade（快速下单）**：0 逐 K 线生效、1 非历史 bar 立即、2 任何情况立即。

**EEntrustStatus（委托状态）**：49 待报、50 已报、51 部成待撤、52 已撤、54 部撤、55 部成、56 已成、57 废单。

**credit_opvolume_callback 的 ret**：1 正常、-1 查询中、-2 账号非法、-3 参数非法、-4 超时报错。

#### 13.11 参数联动汇总（快速索引）

以下函数存在**参数间相互影响**，选择某参数时需同时考虑对其他参数的要求。详细说明见各函数文档中的「参数联动与选择提示」表。

| 函数 | 主要联动关系 |
|------|--------------|
| **passorder** | prType↔price、prType↔行情五档、orderType↔volume 数量/金额、opType↔orderType、quickTrade↔调用场景 |
| **get_market_data_ex** | period↔fields（Tick/Bar 不同）、period↔dividend_type、subscribe↔场景 |
| **download_history_data** | period↔startTime/endTime 格式、startTime 空↔增量下载 |
| **schedule_run** | repeat_times↔interval、cancel_schedule_run 的 key |
| **subscribe_quote** | period↔订阅额度、callback↔result_type |
| **get_financial_data** | report_type↔未来函数、fieldList↔表名 |
| **get_trade_detail_data** | strDatatype↔返回字段、strategyName↔过滤 |
| **cancel** | orderId 取 Order.m_strOrderSysID、accountId+accountType 与下单一致 |
| **cancel_task/pause_task/resume_task** | taskId 取 CTaskDetail.m_nTaskId、accountId 与下单一致 |
| **回测专用 order_*** 系列** | 函数选择↔主参数（数量/金额/比例）、style↔price |
| **set_basket** | quantity↔weight、optType↔买卖方向 |

#### 13.12 VIP 权限汇总（需要 VIP 权限的函数与数据）

以下函数或数据获取**需要开通 VIP 权限**，使用前需确认账号是否已开通对应权限。

| 函数/数据 | 权限类型 | 说明 |
|-----------|----------|------|
| **Level2 行情数据** | Level2 行情权限（VIP） | |
| `get_market_data_ex(period='l2quote')` | Level2 行情权限（VIP） | 委托队列数据 |
| `get_market_data_ex(period='l2order')` | Level2 行情权限（VIP） | 逐笔委托数据 |
| `get_market_data_ex(period='l2transaction')` | Level2 行情权限（VIP） | 逐笔成交数据 |
| **高级数据** | VIP 数据权限 | |
| `get_longhubang` | VIP 数据权限 | 龙虎榜数据 |
| `get_north_finance_change` | VIP 数据权限 | 北向资金数据 |
| `get_hkt_details` | VIP 数据权限 | 沪深港通持股明细 |
| `get_hkt_statistics` | VIP 数据权限 | 沪深港通持股统计 |
| **其他** | 对应权限 | |
| `get_new_purchase_limit` | 新股申购权限 | 新股申购额度查询（需对应权限，非 VIP） |

**注意事项**：
- Level2 行情权限（VIP）通常需要向券商申请开通，不同券商权限要求可能不同
- VIP 数据权限（如龙虎榜、北向资金）可能随账号等级或订阅服务自动开通
- 未开通权限时调用相关函数可能返回空数据、报错或提示权限不足
- **五档行情**（askPrice1~5、bidPrice1~5）和**算法交易**（algo_passorder、smart_algo_passorder）**不属于 VIP 权限**，但可能需要向券商申请开通对应功能

#### 13.13 原始文档

- 变量约定：https://dict.thinktrader.net/innerApi/variable_convention.html
- 数据结构：https://dict.thinktrader.net/innerApi/data_structure.html
- 系统函数：https://dict.thinktrader.net/innerApi/system_function.html
- 行情函数：https://dict.thinktrader.net/innerApi/data_function.html
- 交易函数：https://dict.thinktrader.net/innerApi/trading_function.html
- 成交回报主推函数：https://dict.thinktrader.net/innerApi/callback_function.html
- 引用函数：https://dict.thinktrader.net/innerApi/quote_function.html
- 绘图函数：https://dict.thinktrader.net/innerApi/drawing_function.html
- 枚举常量：https://dict.thinktrader.net/innerApi/enum_constants.html

---

### 附录：参数联动使用说明

在使用本文档中的 API 时，**部分参数之间存在依赖或联动关系**：选择某一参数的不同取值，会直接影响其他参数的合法性或含义。例如：

- **passorder**：选 `prType=11`（限价）时，`price` 必须填有效委托价；选 `prType=14`（对手价）时，需确保全推行情为五档级别。
- **get_market_data_ex**：选 `period='tick'` 时，返回字段为 Tick 相关；选 K 线周期时，返回 Bar 相关字段，二者不可混用。
- **orderType**：末位 1 表示 volume 为数量，末位 2 表示 volume 为金额。

各函数的「参数联动与选择提示」表格已插入在对应函数说明中，数据字典 13.11 节提供了快速索引。
