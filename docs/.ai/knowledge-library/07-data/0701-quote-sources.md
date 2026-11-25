# 行情数据源（Quote DataSource）

## 概述

行情与交易**完全分离**：行情源只负责「取数」，交易源负责「下单」，两者互不影响，可任意组合。

```
renderer（行情页 / 策略 tick）
        ↓
main/services/quote-service.js（TTL 缓存 + 状态探测 + 断线重连）
        ↓
main/datasources/index.js（工厂：mock / qmt / eastmoney / auto）
        ├── mock.js          本地模拟（开发演示，假数据）
        ├── qmt.js           xtquant（tick 级、本地、免额度，需 miniQMT）
        └── eastmoney.js     东财公开行情（免 Key、免客户端、有反爬）
```

## 数据分层：什么数据走什么源

| 数据 | 推荐源 | 频率能力 | 额度/成本 |
|---|---|---|---|
| **实时快照（秒级/批量）** | 东财公开 `push2` 批量接口（一次 50 只）或 QMT `get_full_tick` | 东财 3-5s；QMT 约 50ms | 东财免额度；QMT 本地免费 |
| **历史 K 线（日/分钟）** | 东财 `push2his` 或 QMT `download_history_data` | 按需 | 免额度 |
| **深度数据（财务/股东/事件/研报）** | 妙想 MCP（`mx-ds-mcp`）、iFinD MCP | 按需调用 | 妙想 150 次/天；iFinD 试用 2000 次 |
| **情绪/因子类** | 国金证券情绪脉冲指数 MCP | 5 分钟更新 | 限时免费 |

**关键结论：妙想的 1000 次/天是「交易 skill」的额度，行情根本不占它。** 把行情留在妙想接口上轮询是架构错误。

## 东财公开行情（eastmoney.js）

免费、无 Key、无客户端，适合「QMT 不可用时也要真行情」的场景。

| 能力 | 接口 |
|---|---|
| 单只快照 | `push2.eastmoney.com/api/qt/stock/get?secid=1.600519` |
| 批量快照（≤50 只） | `push2.eastmoney.com/api/qt/ulist.np/get?secids=1.600519,0.000001` |
| 历史 K 线 | `push2his.eastmoney.com/api/qt/stock/kline/get?secid=...&klt=101&fqt=1` |
| 标的列表 | 本地 `config/stocks_cache.json`（QMT 同步过一次即可复用） |

字段映射：价格 `×100` 为整数、`f47/f5` 成交量单位是「手」（×100 转股）、`f60` 昨收、`f169/f170` 涨跌额/幅。

代码转换：`sh600519` → `1.600519`，`sz000001` → `0.000001`（沪=1，深/北=0）。

### 限制

- 网页端接口有反爬，必须带 UA/Referer，且要控制频率（本项目 3s TTL 缓存 + 批量合并）。
- 无逐笔/Level-2；分钟线以上够用，tick 级仍需 QMT。
- 稳定性弱于 QMT，生产环境建议 QMT 为主、公开接口为备。

## 切换方式

| 位置 | 键 | 可选值 |
|---|---|---|
| `config/default.json` | `dataSource` | `mock` / `qmt` / `eastmoney` / `auto` |
| 设置页「行情数据源」 | — | 同上（单选） |

`auto` 语义：先试 QMT，握手失败回落 mock（不会自动切东财，避免静默换源）。

## 缓存与额度

`quote-service` 默认 5 秒 TTL 缓存（`config.cache.ttlMs`），同一标的在 TTL 内不会重复取数；
批量查询走一次请求，50 只标的只消耗 1 次 HTTP。

## 验证

```bash
node -e "const {EastmoneyDataSource}=require('./main/datasources/eastmoney');
(async()=>{const ds=new EastmoneyDataSource({});await ds.init();
console.log(await ds.getQuote('sh600519'));
console.log((await ds.getQuotes(['sh600519','sz000001'])).map(x=>x.name+' '+x.price));
console.log((await ds.getHistory('sh600519','1d',3)).bars);
ds.dispose();})()"
```
