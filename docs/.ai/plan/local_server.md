# 本地服务

> 日期：2026-09-21
> 版本基线：v0.1.32（目标 v0.1.33）
> 目标：启用本地 HTTP 服务，把 QMT 行情数据以只读 GET 接口暴露到局域网
> 文档结构：数据格式 -> 详细设计 -> 落地设计 -> 方案评估 -> 实施计划 -> 附录

## 目标

启用本地Http服务，透传qmt数据。

- 绑定 `0.0.0.0`，局域网内其他设备可通过 `http://<本机IP>:12678/mooquant/api/...` 获取行情数据。
- **只读**：全部接口为 `GET`，只暴露数据获取能力；**不暴露任何交易接口**（`trade.*` 一律不实现）。
- 复用现有 `QuoteService` 链路（同一份 TTL 缓存、同一个 xtquant 连接、同一套字段映射），
  使 HTTP 客户端拿到的数据与桌面 UI 完全一致 —— 不新开 xtquant 连接，不重复实现数据源。

## 数据格式

所有接口统一以JSON格式返回。
JSON格式统一包含以下wrapper：
```
{
    "code": 0,
    "msg": "success",
    "data": {}
}
```

其中，
- `code`：状态码，0表示成功。
- `msg`：状态描述。
- `data`：返回数据。根据接口不同，数据格式也不同，可能为null、obj、arr等。

### 补充约定

**HTTP 状态码与业务 `code` 双轨**，职责不重叠：

- **HTTP 状态码**反映「请求是否被服务受理」（传输/协议层）；
- **业务 `code`**反映「业务是否成功」（业务层），客户端判错只需看 `code === 0`。

| 场景 | HTTP | `code` | 说明 |
|------|------|--------|------|
| 成功 | 200 | `0` | `msg` 为 `"success"` |
| 参数缺失 / 非法 | 200 | `10001` | `msg` 指明具体参数 |
| 参数越界 | 200 | `10002` | 如 `count` 超上限、`codes` 超 50 个 |
| 资源不存在 | 200 | `20001` | 标的/板块查不到 |
| 数据源未就绪 | 200 | `30001` | miniQMT 未连接，或接口返回空数据 |
| 数据源不支持该接口 | 200 | `30002` | mock 模式下的 P1/P2 接口 |
| 上游超时 | 200 | `30003` | 桥内 `download_history_data` 等耗时操作超时 |
| 数据不完整 | 200 | `30004` | `ensure=0` 时本地数据不足，`complete:false` |
| 服务内部错误 | 200 | `50000` | 未预期异常，详见本机日志 |
| 鉴权失败 | 401 | `40001` | 配置了 token 且校验不通过 |
| 限流 / 队列已满 | 429 | `40002` | 见「并发、超时与限流」 |
| 接口不存在 | 404 | `40400` | 路径未匹配任何路由 |
| 方法不允许 | 405 | `40500` | 除 `GET` / `OPTIONS` 外的方法 |

> `GET` 之外的业务方法一律 405。`OPTIONS` 不是业务接口，仅用于 CORS 预检（见「访问控制」）。

### 字段命名与单位约定

| 项 | 约定 |
|----|------|
| 命名风格 | `camelCase`（与主进程 Service 层、渲染层领域模型一致） |
| `code`（标的内） | 统一为 **UI 格式** `sh600519` / `sz000001` / `bj430047`；请求入参与响应同构 |
| `code`（请求参数） | 同时兼容 `sh600519`（UI 格式）与 `600519.SH`（xtquant 格式），桥内 `to_xtcode()` 已处理 |
| `volume` | **股**（桥内已把 xtquant 的「手」×100 转换） |
| `turnover` | **元** |
| 价格字段 | `float`，保留 4 位小数 |
| 盘口字段 | `bidPrice` / `bidVol` / `askPrice` / `askVol` 为**长度 5 的数组**，索引 `0` 为**一档**（买一/卖一） |
| 时间字段 | ISO 字符串 `2026-09-21T14:32:01`（快照 `timestamp`）；K 线为 `YYYY-MM-DD`（日线）或 `YYYY-MM-DD HH:MM`（分钟线） |

> 已知不一致：`bridge/qmt_server.py` 的 `handle_quote_snapshot` 返回的 `code` 是 xtquant 格式
> `600519.SH`，而 `main/datasources/mock.js` 返回 `SH600519`。HTTP 层统一归一化为 UI 格式
> `sh600519`，使请求参数与响应字段同构（**仅 HTTP 层归一，不改动桥与 IPC 的既有行为**）。

**响应头**（所有接口）：

| 头 | 值 |
|----|----|
| `Content-Type` | `application/json; charset=utf-8`（必须显式带 charset，否则中文名乱码） |
| `Content-Encoding` | 客户端带 `Accept-Encoding: gzip` 时用 `gzip`（见「响应压缩」） |
| `X-Mooquant-Source` | 实际数据源 `qmt` / `mock`（`auto` 回落 mock 时客户端可感知） |
| `X-Mooquant-Version` | 应用版本号 |
| `Cache-Control` | 按接口给，见各接口小节 |
| `Access-Control-Allow-Origin` | 仅当配置了 `cors` 时下发 |

## 详细设计

### 1. 服务端口

使用12678端口。

- 与服务端其它端口互不冲突：模型服务 `18765`（仅 `127.0.0.1`）、miniQMT 行情 `58610` / `58620`。
- 通过 `config/default.json` 的 `localServer.port` 配置，或环境变量 `MOOKQUANT_LOCAL_SERVER_PORT` 覆盖。
- 端口占用（`EADDRINUSE`）时**不阻塞应用启动**：打日志、内部状态标记为启动失败、HTTP 功能降级为不可用，主流程继续。

**服务发现另占用一个独立 UDP 端口 `12679`**（见 4.5）：它不共享 HTTP 端口，由 `localServer.discovery.port`
单独配置、也**不受 `MOOKQUANT_LOCAL_SERVER_PORT` 影响**（UDP 与 TCP 端口空间本就独立）。
该端口起不来（被占 / 广播被系统阻止 / 无局域网网卡）
**只降级「被发现」能力，HTTP 数据面照常可用** —— 客户端仍可手工填 `http://<ip>:12678/mooquant/api`。

### 2. 路径

暴露端口以 `/mooquant/api` 开头。

- `/mooquant/api` 本身是接口索引；其余接口为其子路径。
- 未匹配 `/mooquant/api` 前缀的请求一律 `404`（不提供任何静态文件服务，杜绝路径穿越）。
- 子路径风格：集合用复数名词（`/stocks`、`/sectors`），单资源用查询参数定位（`/quote?code=`）。
  仅「板块成分股」使用路径参数 `/sectors/{name}/stocks`（板块名含中文，需 URL 编码）。

### 3. api

行情相关接口按 **实时行情 / 历史行情 / 基础数据** 三族划分：

- **实时行情**：描述「当下这一刻」的市场状态，数据随行情滚动，适合高频轮询。全部为「拉」模式（本期不做推送）。
- **历史行情**：描述「已收盘」的确定序列，数据不可变，可缓存较久。
- **基础数据**：标的/板块/日历等元数据，日级不变。

> 实时与历史的边界要特别注意：`/full-kline` 是**实时**（仅最新交易日、随行情滚动），
> `/history` 是**历史**（含已收盘的全部交易日）。要拼出「含今日的完整 K 线序列」，
> 需要 `/history` + `/full-kline` 各取一段。

#### 3.0 接口总览

| # | 路径 | 族 | 阶段 | 说明 |
|---|------|----|------|------|
| 1 | `GET /mooquant/api` | 服务 | P0 | 接口列表（自描述） |
| 2 | `GET /mooquant/api/status` | 服务 | P0 | 服务与数据源状态 |
| 3 | `GET /mooquant/api/quote` | 实时 | P0→P1 | 单标的快照（P1 起含五档盘口） |
| 4 | `GET /mooquant/api/quotes` | 实时 | P0→P1 | 批量快照（P1 升级为单次 RPC） |
| 5 | `GET /mooquant/api/full-tick` | 实时 | P1 | **全市场全推快照**（一次拿全市场） |
| 6 | `GET /mooquant/api/full-kline` | 实时 | P1 | **实时 K 线**（仅最新交易日，滚动） |
| 7 | `GET /mooquant/api/history` | 历史 | P0→P1 | K 线历史（P1 起含时间范围与补数据开关） |
| 8 | `GET /mooquant/api/history-batch` | 历史 | P1 | **多标的批量历史**（一次调用） |
| 9 | `GET /mooquant/api/search` | 基础 | P0 | 代码/名称/拼音搜索 |
| 10 | `GET /mooquant/api/stocks` | 基础 | P0 | 标的列表（分页） |
| 11 | `GET /mooquant/api/instrument` | 基础 | P2 | 标的详情（涨跌停/股本/最小变动单位） |
| 12 | `GET /mooquant/api/sectors` | 基础 | P2 | 板块列表 |
| 13 | `GET /mooquant/api/sectors/{name}/stocks` | 基础 | P2 | 板块成分股 |
| 14 | `GET /mooquant/api/calendar` | 基础 | P2 | 交易日历 |
| 15 | `GET /mooquant/api/dividends` | 基础 | P2 | 除权因子 |
| 16 | `GET /mooquant/api/periods` | 基础 | P2 | 可用周期列表 |

- **P0** = 纯 Node 侧改造，复现有桥能力，不改 `bridge/`。
- **P1** = 行情能力补全，需改 `bridge/qmt_server.py`（盘口 / 全推 / 实时 K 线 / 历史参数）。
- **P2** = 基础数据接口，需新增桥的 `market.*` RPC。
- **P3 预留** 见 3.6。

---

#### 3.1 接口列表

路径：`/mooquant/api` 

方法：`GET`

描述：获取所有接口列表。

参数：无。

`data` 结构：

```json
{
  "version": "0.1.32",
  "server": { "host": "0.0.0.0", "port": 12678, "startedAt": "2026-09-21T09:12:00", "uptimeMs": 86400000 },
  "addrs": ["192.168.1.5", "10.0.0.3"],
  "endpoints": [
    {
      "path": "/mooquant/api/quote",
      "method": "GET",
      "family": "realtime",
      "desc": "单标的快照（含五档盘口）",
      "stage": "P1",
      "params": [
        { "name": "code", "type": "string", "required": true, "default": null, "desc": "标的代码，sh600519 / 600519.SH" }
      ]
    }
  ]
}
```

- `endpoints` **由路由表自动生成**，杜绝文档与实现漂移；新增接口无需手改此文档。
- `addrs` 为本机可用于局域网访问的 IPv4 地址（`os.networkInterfaces()` 过滤内部/虚拟网卡），
  客户端可先请求此接口完成服务发现。

缓存：`Cache-Control: no-store`。

#### 3.2 服务状态

路径：`/mooquant/api/status`

方法：`GET`

描述：获取服务与数据源状态（是否真的连上 miniQMT）。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `probe` | int | 否 | `1` | `1` 真实探测数据链路（拉一次基准 tick）；`0` 只查桥进程存活 |

`data`：`QuoteService.getStatus()` 结果 + 服务信息。

```json
{
  "mode": "qmt",
  "description": "国金 QMT · 迅投 xtquant",
  "configuredMode": "qmt",
  "connected": true,
  "error": null,
  "reconnecting": false,
  "stockCount": 10432,
  "server": { "port": 12678, "uptimeMs": 86400000, "requestCount": 1287 }
}
```

- `probe=1` 会真实拉取基准标的 tick，代价不低：**HTTP 层对状态结果做 5s 缓存**，避免客户端高频轮询打爆桥。
- `connected:false` + `mode:"mock"` 表示 `auto` 模式已回落 mock，此时数据为模拟数据（响应头 `X-Mooquant-Source: mock` 同步体现）。

缓存：`Cache-Control: no-store`。

---

#### 3.3 实时行情

##### 3.3.1 单标的快照

路径：`/mooquant/api/quote`

方法：`GET`

描述：获取单个标的的实时快照，含**五档盘口**。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `code` | string | 是 | — | `sh600519` / `600519.SH` |

`data`：

```json
{
  "code": "sh600519",
  "name": "贵州茅台",
  "industry": "—",
  "market": "sh",
  "marketName": "上海",
  "currency": "CNY",
  "price": 1680.5000,
  "open": 1670.0000,
  "high": 1695.0000,
  "low": 1662.0000,
  "prevClose": 1665.0000,
  "change": 15.5000,
  "changePercent": 0.9310,
  "volume": 1234500,
  "turnover": 2074000000.00,
  "bidPrice": [1680.40, 1680.30, 1680.20, 1680.10, 1680.00],
  "bidVol":   [1200, 800, 640, 520, 300],
  "askPrice": [1680.50, 1680.60, 1680.70, 1680.80, 1680.90],
  "askVol":   [900, 1500, 700, 1100, 430],
  "timestamp": "2026-09-21T14:32:01"
}
```

字段整形（HTTP 层，不改动 IPC）：
- `code` 归一为 UI 格式（桥返回 `600519.SH`、mock 返回 `SH600519`，这里统一为 `sh600519`）；
- 移除桥返回的内部字段 `rawSymbol`（与请求参数重复）。

**盘口字段**（P1 新增，当前桥的字段映射**丢掉了**这部分）：

- 来源：`get_full_tick` 返回的 tick 中 `bidPrice` / `bidVol` / `askPrice` / `askVol` 四个数组；
- `bidPrice[0]` = 买一价，`askVol[4]` = 卖五量；
- **容错**：停牌、集合竞价撮合前、非交易时段可能为空数组或全 0，客户端需兼容；
- 实现要点：桥的 `handle_quote_snapshot` 需把这四个数组一并透传（`float()` 逐项转换）。
  其余 tick 原始字段（`stockStatus` 证券状态、`transactionNum` 成交笔数、`lastSettlementPrice` 前结算）
  成本几乎为零，**建议同批透传**，避免日后逐个补，但本期不作强制要求。

缓存：`Cache-Control: public, max-age=5`（对齐 `config.cache.ttlMs = 5000`，多设备可共享浏览器/中间缓存）。

##### 3.3.2 批量快照

路径：`/mooquant/api/quotes`

方法：`GET`

描述：一次请求获取多个标的最新快照，供局域网看板/自选股轮询使用。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `codes` | string | 是 | — | 逗号分隔，最多 **50** 个（对齐 xtquant「单股订阅不宜超过 50」的建议） |
| `skipError` | int | 否 | `1` | `1` 单个标的失败不影响整体；`0` 任一失败即返回错误 |

`data`：`{ "count": 2, "quotes": [ {...快照...}, {...} ], "errors": [ {"code": "sh999999", "msg": "未找到该标的"} ] }`

实现：`QuoteService.queryMany(codes)`（**新增方法**）：

```js
// main/services/quote-service.js
// 并发扇出 query()，并发上限 4；单个失败不影响整体；返回 { ok, data:{ count, quotes, errors } }
async queryMany(codes, { skipError = true } = {})
```

- **压力摊薄**：`query()` 自带 5s TTL 缓存，客户端按 3s 轮询同一批标的时，只有每 5s 的第一轮会穿透到桥，
  其余全部命中 Node 内存缓存。以 30 标的、3s 轮询估算，桥侧约 6 次/秒，可承受。
- **P1 优化**：桥新增 `quote.snapshot_batch`（一次 `get_full_tick([...])` + 一次
  `get_instrument_detail_list([...])`），把 50 次 stdio 往返压成 1 次。

> **与 `/full-tick` 的分工**：只在自选股范围内轮询用本接口（数据小、走 TTL 缓存）；
> 要看全市场或标的集合大且分散，直接用 `/full-tick`。

缓存：`Cache-Control: public, max-age=5`。

##### 3.3.3 全市场全推快照

路径：`/mooquant/api/full-tick`

方法：`GET`

描述：获取**全市场**（或指定市场）全部合约的最新 tick 快照。对应 `xtdata.get_full_tick(code_list)`。

xtquant 的 `get_full_tick` 支持传**市场代码**（`['SH','SZ']` = 全市场）或**合约代码列表**（`['600519.SH']`），
本接口据此把两种用法统一为两个互斥参数。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `market` | string | 否 | `SH,SZ` | 市场代码，逗号分隔，可选 `SH` `SZ` `BJ`；`*` 表示全部市场 |
| `codes` | string | 否 | — | 指定标的列表（逗号分隔，最多 50），与 `market` **二选一**；同时传则 `codes` 优先 |
| `fields` | string | 否 | — | 字段裁剪（逗号分隔，如 `lastPrice,volume`）。**全市场时强烈建议使用**，见下 |
| `format` | string | 否 | `map` | `map` = `{code: tick}`；`arr` = `[{code, ...tick}]` |

`data`：

```json
{
  "market": "SH,SZ",
  "count": 5231,
  "asOf": "2026-09-21T14:32:01",
  "quotes": {
    "sh600519": { "price": 1680.5, "open": 1670.0, "high": 1695.0, "low": 1662.0, "prevClose": 1665.0,
                  "volume": 1234500, "turnover": 2074000000.0,
                  "bidPrice": [1680.4,1680.3,1680.2,1680.1,1680.0], "askPrice": [1680.5,1680.6,1680.7,1680.8,1680.9],
                  "bidVol": [1200,800,640,520,300], "askVol": [900,1500,700,1100,430] },
    "sz000001": { "price": 11.32 }
  }
}
```

> **`fields` 为什么在这里提供**：全市场约 5000+ 标的 × 十余个字段，完整响应约 1.2~1.5MB。
> 虽然 gzip 后约 120~200KB（见「响应压缩」），但客户端多数场景只需要 `lastPrice`/`volume` 几个字段。
> `fields` 仅在本接口提供（其他接口数据量小，不做裁剪）。

实现：桥新增 `quote.full_tick` RPC（`get_full_tick(code_list)`，`code_list` 由 `market`/`codes` 拼装），
`QuoteService.getFullTick({ market, codes })` 编排。

- **不进** `QuoteService` 的 per-symbol 缓存（那是一标的一条目的结构），
  改由 Service 层加 **3s TTL 缓存**，按 `market`/`codes` 组合做 key。
- 全推数据量大、序列化开销集中在 Python 侧，纳入**更严的限流**（单 IP 1 req/s）。

缓存：`Cache-Control: public, max-age=3`。

##### 3.3.4 实时 K 线

路径：`/mooquant/api/full-kline`

方法：`GET`

描述：获取**最新一个交易日**的 K 线全推数据，随行情滚动更新。对应 `xtdata.get_full_kline()`。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `codes` | string | **是** | — | 逗号分隔，最多 **50**；**必填**，理由见下 |
| `period` | string | 否 | `1m` | `1m` `5m` `15m` `30m` `1h` `1d` |
| `count` | int | 否 | `240` | 每标的取最后 N 根，最大 1000（1m 一个交易日 240 根，默认即当日全部） |
| `dividendType` | string | 否 | `none` | 除权方式 |

`data`：

```json
{
  "period": "1m",
  "tradeDate": "2026-09-21",
  "count": 240,
  "series": {
    "sh600519": {
      "bars": [
        { "time": 1758409800, "date": "2026-09-21 09:30", "open": 1670.0, "high": 1672.5, "low": 1669.2,
          "close": 1671.8, "volume": 5400, "amount": 9022320.0 }
      ]
    }
  }
}
```

> **`codes` 为何必填**：`get_full_kline` 在 `stock_list` 为空时可能返回全市场，
> 而「全市场 × 当日 240 根 1m bar」是百万级记录，一条请求即可拖垮桥与内存。
> 强制指定标的并限 50，把最坏情况锁死。

**与 `/history` 的区别**（最容易搞混的一点）：

| | `/full-kline` | `/history` |
|---|---|---|
| 数据范围 | **仅最新一个交易日** | 已收盘的历史交易日（可含当日） |
| 是否滚动更新 | 是（盘中每根 bar 实时变化） | 否（收盘后确定） |
| 上游接口 | `get_full_kline` | `get_market_data_ex` |
| 返回形状 | `{field: DataFrame}`（**字段优先**） | `{code: DataFrame}`（**代码优先**） |

> ⚠️ **实现陷阱**：两者返回的 dict 结构不同（字段优先 vs 代码优先），
> 桥内不能复用同一个 `_parse_bars` 解析路径，需为 `get_full_kline` 单独写解析。

实现：桥新增 `quote.full_kline` RPC。Service 层加 **3s TTL 缓存**（按 `codes`+`period` 做 key）。

缓存：`Cache-Control: public, max-age=3`。

---

#### 3.4 历史行情

##### 3.4.0 补数据策略（`ensure`）

本节适用于 3.4.1 与 3.4.2 两个接口，是「历史行情」族的共用前置约定。

现状：桥的 `handle_quote_history` 在核心缓存未命中时**无条件**调用
`xt.download_history_data(...)`。这是 GET 请求的**副作用**，且 `download_history_data`
是同步阻塞调用，期间 `qmt_server.py` 的单线程主循环**会阻塞整条桥**（含 UI 的快照请求）。

因此两个历史接口都提供 `ensure` 开关：

| `ensure` | 行为 |
|----------|------|
| `0`（默认） | **只读**本地数据库 / xtquant 本地数据，不触发任何下载；不足则返回已有部分 + `complete:false` |
| `1` | 先 `download_history_data` 补数据，再读取；请求进入**独立串行队列（并发 1）**，绝不并发触发 |

> **桥侧参数默认值必须是「保持现状」**：`quote.history` 的 `ensure` 在桥内默认 **`true`**，
> 这样 App 的 UI 查询与回测数据源行为不变；**HTTP 层默认传 `ensure=0`**。
> 若把桥的默认值改成 false，会静默影响 App 的历史查询与回测（可能拿到不完整数据），属于隐性回归。

##### 3.4.1 K 线历史

路径：`/mooquant/api/history`

方法：`GET`

描述：获取标的的 K 线历史序列。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `code` | string | 是 | — | 标的代码 |
| `period` | string | 否 | `1d` | `1m` `5m` `15m` `30m` `1h` `1d` `1w` `1mon` |
| `start` | string | 否 | — | 起始时间 `YYYYMMDD` 或 `YYYY-MM-DD HH:MM:SS` |
| `end` | string | 否 | — | 结束时间，格式同上 |
| `count` | int | 否 | `60` | 数据条数；`-1` 表示全部（HTTP 层归一为上限 5000） |
| `dividendType` | string | 否 | `front` | 除权方式：`none` `front` `back` `front_ratio` `back_ratio` |
| `ensure` | int | 否 | `0` | `1` = 允许触发本地补数据；`0` = 只读已有数据。见 3.4.0 |

**`start`/`end` 与 `count` 的关系**（与 xtquant 语义一致）：

- 请求范围是 **闭区间** `[start, end]`，在其中取**最后不超过 `count` 条**；
- `start`/`end` 都为空时 = 取本地最新的 `count` 条；
- 三者都为空 / `count=-1` 时 = 尽量取全部（受上限约束）。

`data`：

```json
{
  "code": "sh600519",
  "period": "1d",
  "count": 250,
  "complete": true,
  "cached": false,
  "bars": [
    { "time": 1757894400, "date": "2026-09-15", "open": 1670.0, "high": 1695.0, "low": 1662.0,
      "close": 1680.5, "volume": 1234500, "amount": 2074000000.0 }
  ]
}
```

- `complete: true` 表示本地数据**已满足**请求的区间/条数；`false` 表示本地数据不全
  （`ensure=0` 时不再补数据，客户端可改带 `ensure=1` 重试）。

缓存：`Cache-Control: public, max-age=60`。

##### 3.4.2 多标的批量历史

路径：`/mooquant/api/history-batch`

方法：`GET`

描述：一次请求获取多个标的的历史 K 线。`get_market_data_ex` 的 `stock_list` 本身就是列表，
批量取是它的原生能力，无需逐标的扇出。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `codes` | string | 是 | — | 逗号分隔，最多 **50** |
| `period` | string | 否 | `1d` | 同上 |
| `start` / `end` | string | 否 | — | 同上 |
| `count` | int | 否 | `60` | 同上 |
| `dividendType` | string | 否 | `front` | 同上 |
| `ensure` | int | 否 | `0` | 同上 |
| `skipError` | int | 否 | `1` | `1` 单个标的失败不影响整体 |

`data`：

```json
{
  "period": "1d",
  "count": 250,
  "series": {
    "sh600519": { "complete": true, "cached": false, "bars": [ {...} ] },
    "sz000001": { "complete": false, "bars": [ {...} ] }
  },
  "errors": [ { "code": "sh999999", "msg": "未找到该标的" } ]
}
```

实现：桥新增 `quote.history_batch` RPC，内部一次 `xt.get_market_data_ex([], codes, ...)` +
逐标的 `_parse_bars`（返回形状是**代码优先**的 `{code: DataFrame}`，与 3.3.4 不同）。

- **耗时与数据量都放大**：50 标的 × 250 根 ≈ 12500 条 bar，序列化集中在 Python 侧。
  与单标的 history 共用**同一条串行队列（并发 1）**，`count` 上限同样受 `maxHistoryCount` 约束。
- 聚合路径（`5m/15m/30m/60m` 从 1m 聚合、`1w/1mon` 从 1d 聚合）目前桥内按单标的实现，
  批量版本**先只支持非聚合周期**（`1m`/`1d`/`tick` 等直取路径）；聚合周期的批量留待后续，
  请求聚合周期时返回 `code:10001`（参数不支持）并提示改用 `/history`。

缓存：`Cache-Control: public, max-age=60`。

---

#### 3.5 基础数据

##### 3.5.1 股票搜索

路径：`/mooquant/api/search`

方法：`GET`

描述：按代码、名称或拼音首字母搜索标的。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `q` | string | 是 | — | 查询词 |
| `limit` | int | 否 | `20` | 返回条数上限，最大 50 |

匹配规则（与 UI 搜索一致，见 `QuoteService.search`）：

- 纯数字 → 匹配代码（`600519`、`sh600` 均可）
- 含中文 → 匹配名称（`茅台`）
- 纯英文 → 匹配拼音首字母（`zsyh` → 招商银行）或代码

`data`：`[ { "code": "sh600036", "name": "招商银行", "industry": "", "type": "A股" } ]`

> `QuoteService.search` 当前把返回条数硬编码为 `slice(0, 20)`，`limit` 无法生效。
> P0 需将其签名放宽为 `search(query, limit = 20)`（UI 侧不传即保持原行为），
> 并在此处按 `limit` 上限 50 截断。

缓存：`Cache-Control: public, max-age=300`。

##### 3.5.2 股票列表

路径：`/mooquant/api/stocks`

方法：`GET`

描述：分页获取全部标的列表（沪深A股/创业板/科创板/北交所/ETF）。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `offset` | int | 否 | `0` | 起始偏移 |
| `limit` | int | 否 | `100` | 每页条数，最大 1000 |
| `type` | string | 否 | — | `A股` `创业板` `科创板` `北交所` `ETF` |
| `exchange` | string | 否 | — | `SH` `SZ` `BJ` |
| `q` | string | 否 | — | 关键字过滤（复用 3.5.1 的匹配规则） |

`data`：

```json
{
  "total": 10432,
  "offset": 0,
  "limit": 100,
  "count": 100,
  "stocks": [ { "code": "sh600000", "name": "浦发银行", "exchange": "SH", "type": "A股" } ]
}
```

- **必须分页**：全量含 ETF 约 1 万+ 条，一次性返回约 1MB，故 `limit` 默认 100、最大 1000。
- 列表数据来自 SQLite（`stock.sync` 已同步）或 `config/stocks_cache.json`，由 `QuoteService` 内存缓存，不穿透到桥。

缓存：`Cache-Control: public, max-age=300`。

##### 3.5.3 标的详情（P2）

路径：`/mooquant/api/instrument`

方法：`GET`

描述：获取合约基础信息（涨跌停价、股本、最小变动单位、上市/退市日等），对应 `xtdata.get_instrument_detail`。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `code` | string | 是 | — | 标的代码 |
| `iscomplete` | int | 否 | `0` | `1` 返回全部字段 |

`data`：

```json
{
  "code": "sh600519",
  "name": "贵州茅台",
  "exchangeId": "SH",
  "productId": "",
  "priceTick": 0.01,
  "upStopPrice": 1831.5,
  "downStopPrice": 1498.5,
  "settlementPrice": 1665.0,
  "floatVolume": 1256197800.0,
  "totalVolume": 1256197800.0,
  "openDate": "20010827",
  "expireDate": 0
}
```

缓存：`Cache-Control: public, max-age=3600`（日内基本不变）。

##### 3.5.4 板块列表 / 成分股（P2）

路径：`/mooquant/api/sectors`

方法：`GET`

描述：获取全部板块名称列表（`xtdata.get_sector_list`）。

`data`：`[ "沪深A股", "创业板", "科创板", "京市A股", "沪深ETF", "上证50", ... ]`

缓存：`Cache-Control: public, max-age=3600`。

---

路径：`/mooquant/api/sectors/{name}/stocks`

方法：`GET`

描述：获取指定板块的成分股（`xtdata.get_stock_list_in_sector`）。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `name` | path | 是 | — | 板块名，中文需 URL 编码，如 `/sectors/%E6%B2%AA%E6%B7%B1A%E8%82%A1/stocks` |
| `offset` | int | 否 | `0` | 起始偏移（沪深A股约 5000+ 只，需要分页） |
| `limit` | int | 否 | `200` | 每页条数，最大 2000 |

`data`：`{ "sector": "沪深A股", "total": 5123, "offset": 0, "limit": 200, "count": 200, "stocks": [ { "code": "sh600000", "name": "浦发银行" } ] }`

缓存：`Cache-Control: public, max-age=300`。

##### 3.5.5 交易日历（P2）

路径：`/mooquant/api/calendar`

方法：`GET`

描述：获取指定市场的交易日列表（`xtdata.get_trading_calendar`）。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `market` | string | 否 | `SH` | `SH` / `SZ` / `BJ` |
| `start` | string | 否 | — | 起始日期 `YYYYMMDD`，空表示该市场首个交易日 |
| `end` | string | 否 | — | 结束日期 `YYYYMMDD`，空表示当前时间；可填未来 |
| `count` | int | 否 | `-1` | 截取最后 N 个交易日；`-1` 表示全部 |

`data`：`{ "market": "SH", "count": 244, "dates": ["20260102", "20260103", ...] }`

> 只暴露一个日历接口：`get_trading_dates`（返回时间戳）与 `get_trading_calendar`（返回 8 位日期字符串）
> 语义重叠，选后者（客户端无需自己做时间戳转换）。获取未来交易日需先 `download_holiday_data`，桥内处理。

缓存：`Cache-Control: public, max-age=86400`。

##### 3.5.6 除权因子（P2）

路径：`/mooquant/api/dividends`

方法：`GET`

描述：获取标的的除权除息因子（`xtdata.get_divid_factors`）。

参数：

| 名称 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `code` | string | 是 | — | 标的代码 |
| `start` | string | 否 | — | 起始时间 `YYYYMMDD` |
| `end` | string | 否 | — | 结束时间 `YYYYMMDD` |

`data`：`{ "code": "sh600519", "count": 12, "factors": [ { "date": "20260620", "dr": 1.0023 } ] }`

> 列名以 xtquant 返回的 DataFrame 为准（桥内 `to_dict('records')` 透传），此处不硬编码字段全集。

缓存：`Cache-Control: public, max-age=86400`。

##### 3.5.7 可用周期列表（P2）

路径：`/mooquant/api/periods`

方法：`GET`

描述：获取本机 QMT 支持的数据周期（`xtdata.get_period_list`）。

`data`：`[ "tick", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mon", "1q", "1hy", "1y" ]`

缓存：`Cache-Control: public, max-age=86400`。

---

#### 3.6 预留（P3，本期不实现）

| 路径 | 说明 |
|------|------|
| `GET /mooquant/api/stream` | SSE 实时推送（`text/event-stream`，仍是 GET；复用桥的 `quote.subscribe` / `subscribe_whole_quote` 推送通道） |
| `GET /mooquant/api/financial` | 财务数据 `get_financial_data` |
| `GET /mooquant/api/convertible-bonds` | 可转债信息 `get_cb_info` |
| `GET /mooquant/api/etf-info` | ETF 申赎清单 `get_etf_info` |
| `GET /mooquant/api/index-weight` | 指数权重 `get_index_weight` |

> 本期实时行情走**纯拉**模式（`/full-tick` 3s 轮询已能满足秒级看板）。
> 若日后需要真正的「推」，SSE 是 GET-only 约束下唯一可行的实时推送形态，
> 届时需补：连接表、心跳保活、订阅池、背压与断线反订阅。

### 4. 访问控制

绑定 `0.0.0.0` 意味着**局域网内任何设备都能读到行情数据**。数据本身只读、不含资金与持仓，
风险可控，但仍提供轻量开关。

#### 4.1 配置

`config/default.json` 新增：

```json
"localServer": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 12678,
  "token": "",
  "cors": "*",
  "maxBatchCodes": 50,
  "maxHistoryCount": 5000,
  "gzip": true
}
```

| 字段 | 说明 |
|------|------|
| `enabled` | 总开关，`false` 时不监听端口 |
| `host` | 绑定地址；填 `127.0.0.1` 即退回「仅本机可用」 |
| `port` | 监听端口，`MOOKQUANT_LOCAL_SERVER_PORT` 可覆盖 |
| `token` | 空 = 不校验；非空 = 每次请求必须携带且匹配 |
| `cors` | 空 = 不下发 CORS 头；`*` = 允许任意来源（只读数据可接受）；也可填逗号分隔的来源白名单 |
| `maxBatchCodes` / `maxHistoryCount` | 参数上限，防止单次请求放大 |
| `gzip` | 是否对响应做 gzip 压缩（全市场全推建议保持开启） |

#### 4.2 Token 校验

- 两种携带方式，二者任一满足即可：查询参数 `?token=xxx`，或请求头 `X-Api-Token: xxx`。
- 校验用 `crypto.timingSafeEqual` 做**定长比较**，避免时序侧信道；长度不等直接判失败。
- 失败返回 `HTTP 401` + `{ code: 40001, msg: "鉴权失败" }`。
- Token 只写日志的「是否配置」，**绝不打印明文**。

> `?token=` 会出现在 URL 中，可能被浏览器历史/代理日志记录；脚本客户端优先用 `X-Api-Token` 头。

#### 4.3 CORS

- 配置了 `cors` 时，所有响应下发 `Access-Control-Allow-Origin`（`*` 或命中白名单的来源）。
- 因为 `X-Api-Token` 属自定义头，配置了 token 的浏览器客户端会触发**预检**，
  故必须实现 `OPTIONS` 并返回 `Access-Control-Allow-Methods: GET, OPTIONS` 与
  `Access-Control-Allow-Headers: X-Api-Token`。`OPTIONS` 不校验 token、不计数限流。

#### 4.4 不做的事（安全边界）

- **不提供任何交易接口**：`LocalHttpServer` 只注入 `quoteService`（P2 再加 `marketService`），
  `tradeService` 从不进入 HTTP 层。这是**结构性隔离**，不是靠路由表遗漏来保证。
- 不提供写操作（`ensure=1` 的补数据是唯一例外，且已显式化、串行化、可被配置关掉）。
- 不提供文件读取/静态资源、不提供任意命令执行入口。
- 不实现 IP 白名单（P3 可选，用 `allowCidrs` 字段）。
- 若需进一步收紧：把 `host` 改为 `127.0.0.1` 并用反向代理（Nginx）转发到局域网，可叠加 TLS 与更细的鉴权。

> **Windows 防火墙**：首次监听 `0.0.0.0:12678` 会弹出「是否允许访问网络」授权框，
> 需选择「专用网络」允许，否则局域网设备无法连接。文档与启动日志中应给出提示。
> **UDP 发现端口同理**：首次绑定 `0.0.0.0:12679` 会再弹一次（或需在「Windows Defender 防火墙 →
> 允许应用」里为该程序勾上「专用网络」的 UDP 入站），否则广播收不到、探测也无应答。

#### 4.5 服务发现（UDP 广播信标）

绑定 `0.0.0.0` 解决了「能不能连」，但没解决「**怎么知道往哪连**」：客户端拿到的本机 IP 目前只存在于
启动日志里，手机/脚本没法自动获取。本节为**非浏览器客户端**（脚本、原生 App）提供发现能力。

**为什么是 UDP 广播**（而非 mDNS / SSDP）：

| 备选 | 结论 |
|------|------|
| **UDP 广播信标（采纳）** | 纯 Node `dgram`，零新依赖；协议就是「发 JSON、收 JSON」，任何语言都能写客户端 |
| mDNS / `.local` | **未采纳**：需自行实现 responder（DNS 报文解析 + 组播），`dgram` 不够用；且 Windows 上 `Bonjour` 服务不保证存在 |
| SSDP（UPnP） | **未采纳**：面向上层设备描述，报文格式（HTTP-over-UDP）比需求重得多，还得管 `NOTIFY` 生命周期 |

**⚠️ 明确非目标：浏览器**。浏览器**用不了** UDP 广播，所以本机制对网页客户端无效 ——
网页靠设置页展示/复制 URL（见 §7）。本功能只服务脚本与原生 App。

**端口表**（全部互不冲突，TCP/UDP 分开算）：

| 端口 | 协议 | 用途 | 绑定 |
|------|------|------|------|
| `12678` | TCP | 只读 HTTP 数据面 | `0.0.0.0`，`MOOKQUANT_LOCAL_SERVER_PORT` 可覆盖 |
| `12679` | **UDP** | **服务发现信标** | `0.0.0.0`，`localServer.discovery.port`，**不受上述 env 影响** |
| `18765` | TCP | 模型服务 | 仅 `127.0.0.1` |
| `58610` / `58620` | TCP | miniQMT 行情 | 外部进程 |

**报文 schema**（单包 JSON，远小于 1472B，不涉及分片）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `magic` | string | 恒为 `"MOOQUANT-DISCOVERY"`，**先验它再解析其余字段** |
| `v` | number | 协议版本，当前 `1` |
| `probe` | bool | `true` = 这是对探测的**应答**（单播）；`false` = 周期广播 |
| `sid` | string | 本进程随机 12 位十六进制，客户端可用来区分多实例/忽略自己的回声 |
| `name` | string | 服务标识 `"mookquant"` |
| `appVersion` | string | 应用版本号，便于客户端做兼容判断 |
| `hostname` | string | 主机名（人类可读，便于在多台机器里认人） |
| `host` | string | 监听地址，通常 `"0.0.0.0"` |
| `port` | number | **HTTP 实际绑定端口**（取自监听状态，非配置文件） |
| `prefix` | string | `"/mooquant/api"` |
| `addrs` | string[] | 本机局域网 IPv4 列表 |
| `urls` | string[] | 现成的完整基址，如 `["http://192.168.1.5:12678/mooquant/api"]`，**客户端直接用这个，别自己拼** |
| `source` | string | 当前数据源模式 `qmt` / `mock` / `auto` |
| `tokenRequired` | bool | **是否需要 token 才可访问** |
| `ts` | string | 发出时间，ISO 字符串 |

> **安全红线：`token` 明文绝不出现在任何广播报文中，只广播 `tokenRequired` 布尔。**
> 未认证的局域网广播通道上放密钥，等于把门开着。这与 §4.2「绝不打印明文」同源 ——
> 发现机制只负责告诉你「在哪、要不要密钥」，密钥本身由使用者**带外**获取。

**发现协议**：

1. **周期广播**：`intervalMs`（默认 3000，运行时钳制到 `[1000, 60000]`）向受限广播地址
   `255.255.255.255:12679` 以及**每个网卡的定向广播地址**（`ip & mask | ~mask`，多网卡/VPN 机器上
   另一个网段的客户端只能靠定向广播收到）各发一份，去重。绑定成功后**立即先发一次**，客户端无需等一个周期。
2. **探测应答**：客户端向 `12679` 发一个**探包**（同样 `magic` + `probe:true`），服务端**单播**回
   `rinfo.address:rinfo.port` —— 客户端因此可以用**临时端口**发包并在同一 socket 上等回包，无需固定端口，
   也不用等最多一个广播周期。
3. **入站过滤（严格短路）**：`> 512B` 丢弃（防放大）→ JSON 解析失败丢弃 → `magic` 不符丢弃 →
   `sid` 等于自己（回声）丢弃 → `probe !== true` 丢弃 → 同 IP **100ms 最小间隔**丢弃（限速，防探包洪泛）。
4. **降级**：`setBroadcast(true)` 失败（被系统/安全软件阻止）时**不中止**，只把 `broadcastOk` 记为 `false` ——
   应答走单播、不需要 `SO_BROADCAST`，所以**探测仍可用**，只是收不到主动广播。

**客户端示例**（Node，零依赖，拿到 URL 后即可调 §3 的接口）：

```js
const dgram = require("dgram");
const sock = dgram.createSocket({ type: "udp4", reuseAddr: true });

sock.on("message", (buf) => {
  const m = JSON.parse(buf);
  if (m.magic !== "MOOQUANT-DISCOVERY" || !m.probe) return;
  console.log("发现:", m.urls, "数据源:", m.source, "需 token:", m.tokenRequired);
  const base = m.urls[0];                    // 直接用现成的 URL
  // fetch(`${base}/quote?code=sh600519`) ...
  sock.close();
});

sock.bind(0, () => {                          // 绑临时端口即可，应答会单播回来
  sock.setBroadcast(true);
  const probe = Buffer.from(JSON.stringify({ magic: "MOOQUANT-DISCOVERY", v: 1, probe: true }));
  sock.send(probe, 0, probe.length, 12679, "255.255.255.255");
});
```

Python 同理（`socket.AF_INET` + `SOCK_DGRAM` + `SO_BROADCAST`），不再赘述。

## 落地设计

### 5.1 分层与新增文件

```
main.js                              # 组装：创建 LocalHttpServer + DiscoveryBeacon，随应用启停
main/server/local-http-server.js     # 传输层：http.createServer、路由匹配、中间件、生命周期
main/server/discovery-beacon.js      # 传输层：UDP 广播 / 探测应答（无业务逻辑，无 DataSource）
main/server/routes.js                # 路由表：path/family/desc/params/handler，索引端点由此自描述
main/server/middleware.js            # envelope / CORS / token / 日志 / 超时 / 限流 / gzip
main/services/quote-service.js       # + queryMany / getFullTick / getFullKline / getHistoryBatch
main/services/market-service.js      # (P2) 标的详情/板块/日历/除权/周期，带 TTL 缓存
main/datasources/qmt.js              # + 对应透传方法
main/datasources/mock.js             # + 同名降级（code 30002）
bridge/qmt_server.py                 # 见下表
config/default.json                  # + localServer 配置段
```

桥侧改动汇总：

| RPC | 阶段 | 改动 |
|-----|------|------|
| `quote.snapshot` | P1 | 透传 `bidPrice`/`bidVol`/`askPrice`/`askVol`（+ 建议同批透传 `stockStatus`/`transactionNum`） |
| `quote.snapshot_batch` | P1 | **新增**：一次 `get_full_tick(codes)` + `get_instrument_detail_list(codes)` |
| `quote.full_tick` | P1 | **新增**：`get_full_tick(['SH','SZ'])` 全市场/指定市场 |
| `quote.full_kline` | P1 | **新增**：`get_full_kline(...)`，注意返回形状是**字段优先**，需独立解析 |
| `quote.history` | P1 | 增 `start` / `end` / `ensure`（**桥内默认 `true` 保持现状**）/ 返回 `complete` |
| `quote.history_batch` | P1 | **新增**：一次 `get_market_data_ex([], codes, ...)` |
| `market.*` | P2 | **新增**：`instrument` / `sectors` / `sector_stocks` / `calendar` / `dividends` / `periods` |

**越层约束**（对齐 `AGENTS.md`）：`main/server/*` 是传输层，与 `main/ipc/index.js` 平级，
**只做参数解析/校验/整形 + 调用 Service**，绝不写业务逻辑；数据源差异一律下沉到 DataSource。

`discovery-beacon.js` 刻意**不放进 `main/services/`**：`main/services/*` 在本仓库里都骑在一个 DataSource 上
（quote→qmt/mock，trade→qmt/mock），而信标**没有 DataSource、没有业务编排**，它唯一的输入就是 HTTP 传输层
自己的监听状态。放进 services 会让 Service 反向依赖传输层拿端口，违反上面的分层。它是 `http.createServer`
的 **UDP 对等物**：同层、同生命周期、同配置块。

它与 `local-http-server.js` 是**单向依赖**（信标 `require` 它取 `API_PREFIX` 这一常量；它**从不** require 信标，
不存在环）。取数据则**不靠 require、靠注入**：构造时收一个 `getInfo: () => localServer.info()` 回调，好处是拿到的
是**实际绑定端口**（`serverInfo().port`，已被 `MOOKQUANT_LOCAL_SERVER_PORT` 覆盖）而非配置里的值 —— 而配置里的
端口恰恰可能是错的（见验证 V2）。

请求链路：

```
LAN 客户端 ──HTTP──> LocalHttpServer ──> QuoteService / MarketService ──> DataSource ──stdio──> qmt_server.py ──> miniQMT
                        (路由/校验/包装)        (缓存/编排)                (qmt | mock)
```

使用的都是 Node 原生模块（`http`、`zlib`、`crypto`、`os`、`url`），**不引入任何新依赖**
（与项目「Electron 31 + 原生 JS，无框架、无 lint」的约定一致）。

### 5.2 生命周期

1. `main.js` 在 `quoteService` 创建完成后实例化 `LocalHttpServer({ quoteService, config: configManager.get().localServer })`。
2. `await server.start()`；成功则日志打印 `http://<每个局域网IP>:12678/mooquant/api`。
3. **启动失败不阻塞应用**：`EADDRINUSE` / `EACCES` 只打 `console.warn` 并把内部状态标记为启动失败，
   应用其余功能（UI、IPC、模型服务）照常可用。
4. `app.on("window-all-closed")` 中调用 `server.stop()`（`server.close()` + 销毁 keep-alive 连接）。
5. 配置热更新：`ConfigManager.set()` 后由 `settings` 通道触发 `server.restart()`（P3，P0 只读取启动时配置）。

信标（`DiscoveryBeacon`）的生命周期**严格依附于 HTTP 服务**：

- **启动在 `localServer.start()` 之后**（`main.js` 里紧随 HTTP 的日志块），因为它要读已经定型的监听状态。
  三道放行闸任一不满足就**静默不绑端口**：`discovery.enabled !== true` → `disabled`；
  `getInfo()` 无值或 `server.listening === false` → `failed`（理由「HTTP 服务未监听，不广播」，广播一个
  不可达地址没有意义）；`addrs.length === 0` → `failed`（理由「未检测到局域网 IPv4 地址」）。
- **`start()` 永不 reject**：与 HTTP 服务同一套写法（resolve 后置 `_state="failed"`）。原因见 `main.js` 的
  `t()` 包装器 —— 它在失败时 **rethrow**，会让整个启动流程挂掉。UDP 起不来只降级发现，绝不抛给调用方。
- **停止在 `localServer.stop()` 之前**（`window-all-closed`）：先停止「广告」，再拆「端点」。
- **两个配置更新入口，职责不同**：
  - `LocalHttpServer.applyConfig(config)` —— **只换配置、不重绑端口**，给 discovery 这类热更新用。
  - `LocalHttpServer.restart(config)` —— **完整 `stop()` + `start()`**，会踢掉在线连接、重置
    `_startedAt` / `_requestCount`，只用于 `enabled` / `host` / `port` 真的变了。
  信标侧对称地有 `restart(config)` = `stop()` + 换配置 + `start()`（UDP 没有「半更新」语义，直接重绑最省心）。

### 5.3 响应压缩

Node 原生 `zlib.gzipSync`，无新依赖。

- 触发条件：`localServer.gzip === true` 且请求带 `Accept-Encoding: gzip`。
- 小响应（< 1KB）不压缩，避免反而增大。
- 收益最大的正是新增的重接口：`/full-tick` 全市场约 1.2~1.5MB → gzip 后约 120~200KB；
  `/stocks`（1 万条）与 `/history-batch` 同理。
- 压缩在 `zlib` 同步 API 下会占用主进程事件循环；**大响应建议用 `zlib.gzip` 异步版本**，避免卡住 UI。

### 5.4 并发、超时与限流

桥是**单线程同步循环**，这是本设计最核心的约束。HTTP 层必须替它挡住并发洪峰。

| 机制 | 取值 | 目的 |
|------|------|------|
| 快照类并发 | 4 | 批量扇出限制；配合 5s TTL 缓存摊薄 |
| **history 独立串行队列** | **并发 1** | `download_history_data` 会长时间阻塞整条桥，绝不允许并发触发；`/history`、`/history-batch` 共用此队列 |
| 全推类（`/full-tick`、`/full-kline`） | 并发 2 | 单次数据量大，序列化集中在 Python 侧 |
| 单请求超时 | 快照/全推 10s；K线历史 30s；其余 10s | 超时返回 `code:30003`，并主动从等待队列摘除 |
| 等待队列上限 | 64 | 超出直接 `429` + `code:40002`，避免无限堆积 |
| 单 IP 限流 | 默认 20 req/s；全推类 1 req/s | 防止单设备刷爆 |
| `server.keepAliveTimeout` | 5s | 及时回收空闲连接 |
| `server.headersTimeout` / `requestTimeout` | 10s / 60s | 防慢速攻击 |

> 「history 串行 + 快照可并发」的组合会造成一个副作用：一个长 history 请求仍会阻塞 UI 的快照。
> 根治方案是让 `qmt_server.py` 用工作线程池处理请求（`download_history_data` 放子线程），
> 但那是桥的较大改造，**本期不做**，仅在文档中记录为已知限制与后续优化项。

### 5.5 日志

- 复用 `main.js` 已建立的 console → `data/logs/main.log` 落盘（自动带时间戳）。
- 每请求一行（含 `X-Mooquant-Source`），由 HTTP 层统一输出，便于排查 LAN 侧调用。
- 日志示例：`[local-http] 192.168.1.20 GET /mooquant/api/quote?code=sh600519 -> 200 code=0 12ms source=qmt`
- 补数据（`ensure=1`）单独打一条 WARN，便于统计谁在触发下载。

### 5.6 错误处理

- 统一 `try/catch` 包裹 handler：未预期异常 → `code:50000`，`msg` 为通用文案，
  **完整堆栈只写本机日志**，不外泄（避免泄露路径与内部结构）。
- 参数校验在路由层前置完成（必填、类型、上限），不合法直接返回对应 `code`，不进入 Service。
- 上游返回 `ok:false` 时按 `error` 文本映射到 `20001`/`30001`，映射表见「数据格式」小节。

## 方案评估

### 6.1 备选方案与取舍

| 方案 | 做法 | 结论 |
|------|------|------|
| **A. Node 主进程（采纳）** | `main/server/*` 复用 `QuoteService` | 与 UI 共享同一份缓存与 xtquant 连接；复用重连巡检、拼音搜索、股票列表；贴合分层。代价：服务随应用启停 |
| B. 独立 Python 服务 | `bridge/local_server.py` 仿 `model_server.py`，直接 `import qmt_server` | **未采纳**：会开第二份 xtquant 连接与第二份 SQLite 缓存，QMT 连接状态/复权口径出现两套真相；换来的「脱离 Electron 运行」在本项目里不是真实需求（行情桥本身就是 Electron 的子进程） |
| C. 复用模型服务端口 | 挂在 `18765` 的 `model_server.py` 上 | **未采纳**：`model_server` 只绑 `127.0.0.1` 且面向模型/策略训练，关注点不同；混在一起会让「局域网只读数据面」与「本机模型控制面」共享同一个暴露面 |
| D. 直接把 `qmt_server.py` 的 JSON-RPC 用 HTTP POST 暴露 | 加一层 HTTP→RPC 转发 | **未采纳**：与「只需 GET」的要求冲突，且会把 `trade.*` 一并暴露，违背只读边界 |
| E. 实时走 SSE 推送 | `subscribe_whole_quote` → `text/event-stream` | **本期不做**（P3 预留）：`/full-tick` 3s 轮询已能满足看板；SSE 需要连接管理/心跳/订阅池/背压，成本明显高于收益 |
| F. 历史走扇出批量 | 循环 N 次 `/history` | **未采纳**：`get_market_data_ex` 原生支持多标的，扇出会把 N 次往返与 N 次 DataFrame 解析的开销叠加，且更容易把桥的 FIFO 队列塞满 |

### 6.2 风险清单

| 风险 | 影响 | 缓解 |
|------|------|------|
| 桥单线程被 history 补数据阻塞 | UI 快照卡顿 | `ensure` 默认 0（HTTP 侧不补数据）+ history 串行队列 + `count` 上限 |
| `ensure` 默认值改动波及 App | 回测/历史查询静默拿到不完整数据 | **桥内 `ensure` 默认 `true`**（保持现状），仅 HTTP 层传 `false`；改动需回归 App 历史查询与回测 |
| 全市场全推数据量 | 主进程序列化与网络压力 | gzip（异步 `zlib.gzip`）+ Service 3s TTL 缓存 + 单 IP 1 req/s 限流 + `fields` 裁剪 |
| `/full-kline` 全市场爆量 | 单请求百万级记录，拖垮桥与内存 | **强制 `codes` 必填**且限 50 |
| `get_full_kline` 返回形状与 `get_market_data_ex` 不同 | 解析复用导致数据错位 | 桥内独立解析路径，不复用 `_parse_bars`（已在 3.3.4 标注） |
| LAN 高频轮询 | 桥压力大 / 本机 CPU 升高 | TTL 缓存 + 单 IP 限流 + 队列上限 429 |
| `auto` 回落 mock 时客户端误用模拟数据 | 数据误判 | `X-Mooquant-Source` 响应头 + `/status` 的 `mode` 字段 |
| 端口被占 / 防火墙拦截 | 局域网不可达 | 启动不阻塞 + 明确日志 + 防火墙提示；`/status` 的 `addrs` 便于服务发现 |
| 无鉴权时数据被局域网任意读取 | 信息泄露（仅行情） | 可选 token + `host` 可回收为 `127.0.0.1` |
| 广播被防火墙拦截 / 多网卡只发到本网段 | 客户端「发现不到」 | 定向广播兜底（`ip & mask \| ~mask`）+ 探测应答（单播，不依赖 `SO_BROADCAST`）+ 设置页展示 URL 手工兜底 |
| Windows `SO_REUSEADDR` 被后启动实例静默抢占（**不是** `EADDRINUSE`） | 两台实例抢投递，可能收不到广播 | 两种结果都无害（任一实例应答、URL 相同）；只 `warn` 不抛，仅降级发现 |
| 信标信息泄露（面向未认证的局域网广播通道） | 攻击者借此扫描本机行情服务 | **只广播 URL 与数据源模式，`token` 明文绝不入包**（仅 `tokenRequired` 布尔）；`enabled:false` 时连 UDP 端口都不占 |
| `code` 格式两端不一致（桥 vs mock） | 客户端解析困惑 | HTTP 层统一归一化为 UI 格式，并在响应文档中固化 |

## 实施计划

### P0 —— 只读数据面打通（纯 Node，不改桥）

| # | 任务 | 验收标准 |
|---|------|----------|
| 0.1 | `config/default.json` 增 `localServer` 段；`config.js` 版本 → `0.1.32` | 配置生效，`enabled:false` 时不监听 |
| 0.2 | `main/server/middleware.js`：envelope / CORS / token / 日志 / 超时 / gzip | `code` 0/10001/40001 均正确；gzip 生效 |
| 0.3 | `main/server/local-http-server.js`：路由匹配 + 生命周期 + 限流队列 | 局域网另一台设备可访问；端口占用不崩 |
| 0.4 | `QuoteService.queryMany()`（新增）+ `search(query, limit)`（放宽签名） | 50 标的单次请求可用，单个失败不影响整体；搜索 `limit` 生效 |
| 0.5 | `main/server/routes.js`：3.1、3.2、3.3.1（无盘口）、3.3.2（扇出）、3.4.1（仅 count）、3.5.1、3.5.2 | 与 UI 同标的比对，数据一致 |
| 0.6 | `main.js` 组装 + `window-all-closed` 释放 | 关闭应用后端口释放 |
| 0.7 | 真机验证：另一台设备（手机/笔记本）浏览器 + curl 各接口 | 见「附录：客户端示例」 |

### P0.5 —— LAN 服务发现（纯 Node `dgram`，不改桥）

| # | 任务 | 验收标准 |
|---|------|----------|
| 5.1 | `config/default.json` 增 `localServer.discovery` 段；`config.js` 版本 → `0.1.33`；`ConfigManager._normalizeDiscovery` 补齐缺失键 | 手改/旧版配置下信标不读到 `undefined`；`enabled:false` 时不绑端口 |
| 5.2 | `main/server/discovery-beacon.js`：生命周期 + 周期广播 + 探测应答 | 探针 `probe` 模式立刻收到应答；`passive` 模式 6s 内收到 ≥1 条周期广播；`silence` 模式一条都收不到 |
| 5.3 | `LocalHttpServer.applyConfig()`（不重绑端口） | 切换开关后 `/status` 的 `uptimeMs` 单调增长、`requestCount` 不重置、日志不出现第二次「已监听」 |
| 5.4 | `main.js` 接线（HTTP `start()` 之后启动、`stop()` 之前停止）+ `main/ipc/index.js` 两个通道 + `preload.js` | `localServer:info` / `localServer:setDiscovery` 返回标准 `{ok,data}`；不重启应用即可开关 |
| 5.5 | 渲染层：facade mock（带 `{ok,data}` 信封）+ 设置页「本地服务（局域网）」卡片 + CSS | 每个局域网 IP 一行、复制按钮变「已复制」、开关切换后 UDP 文案即时更新 |
| 5.6 | **端口真实性**：广播的必须是实际绑定端口 | 设 `MOOKQUANT_LOCAL_SERVER_PORT=19999` 时，报文 `port === 19999` 且每个 URL 都是 `:19999`（而非配置的 12678） |
| 5.7 | **广播安全**：报文不含 token 明文 | 探针断言 `!("token" in m)`，且 `typeof m.tokenRequired === "boolean"` |
| 5.8 | 真机跨设备：同 Wi-Fi 下手机/另一台电脑跑探针 | 收到应答 → 用返回 URL 调 `/status` 得到 `code:0` |

### P1 —— 行情能力补全（需改桥）

| # | 任务 | 验收标准 |
|---|------|----------|
| 1.1 | `quote.snapshot` 透传五档盘口（+ `stockStatus`/`transactionNum`） | `/quote` 返回 4 个长度 5 的数组；UI 与 IPC 不回归 |
| 1.2 | `quote.full_tick` RPC + `QuoteService.getFullTick` + `/full-tick` | `market=SH,SZ` 一次拿全市场；gzip 后 < 300KB |
| 1.3 | `quote.full_kline` RPC（独立解析）+ `QuoteService.getFullKline` + `/full-kline` | `codes` 必填生效；当日 1m bar 与 QMT 客户端一致 |
| 1.4 | `quote.snapshot_batch` RPC，`/quotes` 切换到单次调用 | 50 标的从 50 次往返降到 1 次 |
| 1.5 | `quote.history` 增 `start`/`end`/`ensure`/`complete`（**桥内 `ensure` 默认 true**） | App 历史查询与回测无回归；HTTP 默认 `ensure=0` 不触发下载 |
| 1.6 | `quote.history_batch` RPC + `/history-batch` | 50 标的 × 250 根一次拿到；聚合周期返回 `10001` 并提示 |
| 1.7 | 串行队列 / 全推限流 / `fields` 裁剪落地 | 一个长 history 请求期间快照仍可服务（限流生效） |

### P2 —— 基础数据接口（需改桥）

| # | 任务 | 验收标准 |
|---|------|----------|
| 2.1 | `qmt_server.py` 增 `market.*` RPC（instrument / sectors / sector_stocks / calendar / dividends / periods） | 桥侧单独调用通 |
| 2.2 | `qmt.js` 增透传方法；`mock.js` 同名降级（`code 30002`） | mock 模式下接口不报错、明确提示 |
| 2.3 | `main/services/market-service.js`：TTL 缓存 + 统一 `{ok,data,error}` | 各接口缓存命中率可观测 |
| 2.4 | 路由 3.5.3~3.5.7 落地 | 与 QMT 客户端显示值比对一致 |

### P3 —— 预留

- SSE 实时推送 `/stream`；财务/可转债/ETF/指数权重；IP 白名单；设置页开关；`accept: text/html` 时返回索引 HTML 页。

## 附录：客户端示例

**第 0 步：先发现服务在哪**（局域网客户端，非浏览器）。下面的 `192.168.1.5` 是演示值，
真实场景里用探测拿到，**不要写死**：

```bash
# 发探包（UDP 广播）并等单播应答；应答里的 urls[0] 就是下面所有 curl 的基址
# Python 等价物（无依赖）：
python -c "
import socket,json
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
s.settimeout(3); s.bind(('',0))
s.sendto(json.dumps({'magic':'MOOQUANT-DISCOVERY','v':1,'probe':True}).encode(),('255.255.255.255',12679))
m=json.loads(s.recv(2048)); print(m['urls'], m['source'], m['tokenRequired'])
"
```

拿到 `urls[0]`（如 `http://192.168.1.5:12678/mooquant/api`）后，再走下面的调用：

```bash
# 已知 URL 时，先探活（也用于把接口列表拿回来）
curl http://192.168.1.5:12678/mooquant/api

# 单标的快照（含五档盘口）
curl "http://192.168.1.5:12678/mooquant/api/quote?code=sh600519"

# 批量快照
curl "http://192.168.1.5:12678/mooquant/api/quotes?codes=sh600519,sz000001,sz300750"

# 全市场全推（一次拿全市场，gzip 自动协商）
curl --compressed "http://192.168.1.5:12678/mooquant/api/full-tick?market=SH,SZ"

# 全市场全推 + 只要几个字段
curl --compressed "http://192.168.1.5:12678/mooquant/api/full-tick?market=SH,SZ&fields=lastPrice,volume,turnover"

# 实时 K 线（仅最新交易日，codes 必填）
curl "http://192.168.1.5:12678/mooquant/api/full-kline?codes=sh600519,sz000001&period=1m"

# 历史日线：最近 250 根（只读本地，不触发下载）
curl "http://192.168.1.5:12678/mooquant/api/history?code=sh600519&period=1d&count=250"

# 历史日线：指定区间，并允许补数据
curl "http://192.168.1.5:12678/mooquant/api/history?code=sh600519&period=1d&start=20260101&end=20260921&ensure=1"

# 多标的批量历史
curl "http://192.168.1.5:12678/mooquant/api/history-batch?codes=sh600519,sz000001&period=1d&count=250"

# 需要 token 时
curl -H "X-Api-Token: my-secret" "http://192.168.1.5:12678/mooquant/api/status"
```

```python
import requests

BASE = "http://192.168.1.5:12678/mooquant/api"

def get(path, **params):
    body = requests.get(f"{BASE}{path}", params=params, timeout=10).json()
    if body["code"] != 0:
        raise RuntimeError(f'{body["code"]}: {body["msg"]}')
    return body["data"]

# 全市场快照里挑出涨幅榜
quotes = get("/full-tick", market="SH,SZ", fields="lastPrice,lastClose,volume")["quotes"]
rank = sorted(quotes.items(), key=lambda kv: kv[1]["lastPrice"] / kv[1]["lastClose"] - 1, reverse=True)[:10]
for code, q in rank:
    print(code, round((q["lastPrice"] / q["lastClose"] - 1) * 100, 2), "%")

# 自选股实时行情
for q in get("/quotes", codes="sh600519,sz000001")["quotes"]:
    print(q["code"], q["price"], q["changePercent"])

# 历史 + 当日实时 K 线拼接
hist = get("/history", code="sh600519", period="1d", count=250)["bars"]
today = get("/full-kline", codes="sh600519", period="1d")["series"]["sh600519"]["bars"]
series = hist + [b for b in today if b["date"] > hist[-1]["date"]]
```

```js
// 局域网内任意网页（需服务端配置 cors）
const BASE = "http://192.168.1.5:12678/mooquant/api";

async function api(path, params = {}) {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const { code, msg, data } = await (await fetch(url)).json();
  if (code !== 0) throw new Error(msg);
  return data;
}

setInterval(async () => {
  const { quotes } = await api("/quotes", { codes: "sh600519,sz000001" });
  console.table(quotes.map(q => ({ code: q.code, price: q.price, pct: q.changePercent })));
}, 3000);
```
