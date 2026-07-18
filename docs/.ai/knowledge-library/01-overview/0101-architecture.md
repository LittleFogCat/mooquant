# 整体架构

mookquant 是一款基于 **Electron** 的量化投资桌面应用，采用 **MVVM + 进程分层 + 数据源可插拔** 的架构。整体设计原则：

- **进程边界严格**：渲染层不能直接访问 Node API / QMT，全部走 IPC + 主进程
- **数据源可替换**：`DataSource` 抽象接口 + 多实现（QMT / Mock），工厂按 mode 决定
- **UI 与逻辑解耦**：渲染层只声明"想要什么数据"，ViewModel 维护状态、View 只渲染 DOM
- **故障可降级**：QMT 不在线 → 自动回落 Mock；桥接崩溃 → 主进程负责重启

---

## 1. 进程拓扑

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       渲染进程 Renderer（沙箱）                         │
│  ┌──────────┐  订阅   ┌─────────────┐  调用   ┌──────────────┐         │
│  │   View   │◀────────│  ViewModel  │────────▶│   Facade     │         │
│  │ (DOM+CSS)│         │  (状态+订阅) │        │ (preload 暴露)│         │
│  └──────────┘         └─────────────┘        └──────┬───────┘         │
│                                                       │ contextBridge  │
│                                                       ▼                │
└───────────────────────────────────────────────────────────────────────┬─┘
                                                                        │ IPC
┌───────────────────────────────────────────────────────────────────────┴─┐
│                          主进程 Main（Node.js）                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                       IPC Router  (main/ipc/index.js)            │   │
│  └──────────────┬──────────────────────────────────────────────────┘    │
│                 ▼                                                        │
│  ┌──────────────┬──────────────┬──────────────┬──────────────────┐     │
│  │ QuoteService │StrategySvc   │BacktestSvc   │ TradeService     │     │
│  │ ExecutorSvc  │ConfigMgr     │LogService    │                  │     │
│  └──┬───────────┴──────┬───────┴──────┬───────┴────┬─────────────┘     │
│     ▼                  ▼              ▼             ▼                    │
│  ┌──────────────────┐ ┌─────────────┐ ┌──────────────────┐              │
│  │ DataSource (抽象)│ │ Strategy    │ │ TradeDataSource  │              │
│  │  ├─ MockSource   │ │  ├─ma_cross │ │  ├─ MockTrade    │              │
│  │  └─ QmtSource    │ │  └─ ...     │ │  └─ QmtTrade     │              │
│  └────────┬─────────┘ └─────────────┘ └────────┬─────────┘              │
└───────────┼────────────────────────────────────┼─────────────────────────┘
            │ spawn (stdio JSON-RPC)             │
            ▼                                    ▼
┌──────────────────────────┐         ┌──────────────────────────┐
│  Python 桥 (qmt_server)  │  TCP    │  miniQMT / xtquant       │
│  - 行情快照              │────────▶│  (国金 QMT · 迅投 SDK)   │
│  - K线/财务              │         │                          │
│  - 实盘交易(规划)        │         │                          │
└──────────────────────────┘         └──────────────────────────┘
```

**关键点**：
- 渲染层与主进程通过 `contextBridge` + `ipcRenderer.invoke` 通信；preload 注入的 `window.mookquant.facade` 是唯一入口
- 主进程对底层数据源进一步抽象：`DataSource`（行情）/ `TradeDataSource`（交易）接口独立
- Python 桥以 **子进程 + stdio JSON-RPC** 方式运行，崩溃由主进程自动拉起

---

## 2. 分层职责

| 层 | 路径 | 职责 | 允许依赖 |
|---|---|---|---|
| **视图层 View** | `renderer/js/views/` | 纯 DOM 渲染、事件绑定、SVG 绘制 | ViewModel（通过 subscribe） |
| **视图模型层 ViewModel** | `renderer/js/viewmodels/` | 维护页面状态、调用 Facade、订阅 State 变更 | Facade |
| **门面层 Facade** | `renderer/js/services/facade.js` + `preload.js` | 把 IPC 命令封装成业务方法；浏览器演示模式自带 mock | IPC / window 全局 |
| **IPC 路由** | `main/ipc/index.js` | 集中注册所有 `ipcMain.handle`，做参数透传 | Service |
| **业务服务 Service** | `main/services/` | QuoteService / StrategyService / TradeService / BacktestService / ExecutorService / ConfigManager / LogService | DataSource、文件系统 |
| **数据源层 DataSource** | `main/datasources/` | 抽象接口；Mock / QMT 实现 | Python 桥 / 文件 |
| **外部桥 Bridge** | `bridge/` | Python 进程；JSON-RPC over stdio | xtquant / miniQMT |

---

## 3. 数据流：股票查询

```
用户在搜索框输入 "sh600519"
    │
    ▼
SearchPanel (View) → 触发 ViewModel.querySymbol(code)
    │
    ▼
QuoteViewModel.querySymbol
    ├─ 写入 state.loading = true, state.symbol = code
    ├─ 调用 facade.quote.query(code)
    │      └─ ipcRenderer.invoke("quote:query", code)
    ▼
主进程 IPC: quote:query → QuoteService.query
    ├─ 查缓存（_cacheGet），命中直接返回
    ├─ 调 DataSource.getQuote(code)
    │      ├─ Mock: 本地表随机生成
    │      └─ QMT: spawn qmt_server.py → quote.snapshot → xtquant
    ├─ 写入缓存（_cacheSet）
    └─ 返回 { ok, data }
    │
    ▼
QuoteViewModel 收到结果 → 更新 state.data
    │
    ▼
ResultCardView (订阅 state) → 重渲染 DOM
```

---

## 4. 数据源可插拔设计

`main/datasources/index.js` 是工厂入口：

```js
createDataSource({ mode, qmt })   // mode ∈ { mock, qmt, auto }
// createTradeDataSource({ mode, qmt })  // 交易源同理
```

| mode | 行为 |
|---|---|
| `mock` | 始终使用 `MockDataSource` / `MockTradeDataSource`，离线演示 |
| `qmt` | 直接初始化 `QmtDataSource`；启动失败抛错 |
| `auto` | 优先 QMT；连接/初始化异常时自动回落 mock，并在控制台打印 warn |

`DataSource` 接口约定（行情）：

```js
class DataSource {
  mode: string           // "mock" | "qmt"
  description: string
  async getQuote(rawSymbol) → object | null
  async getHistory(symbol, period, count, dividendType) → { bars: [...] }
  async getStockList() → { stocks: [...] }              // 可选
  async syncStocks() → { total, stocks }                // 可选
  onPush(event, cb) → unsubscribe                        // 实时推送
  dispose()
}
```

新增数据源（例如 Tushare / Wind）只需新增一个实现类并在工厂中注册，不影响上层代码。

---

## 5. 配置与持久化

- `config/default.json`：应用静态配置（窗口尺寸、QMT 连接参数、缓存 TTL、数据源 mode）
- `data/strategies/<id>.json`：策略定义（StrategyService 管理）
- `data/logs/...`：执行日志与每日净值（LogService 落盘）
- `localStorage`（渲染层）：UI 偏好（侧边栏折叠、最近搜索等）

所有配置文件改动后通过 `ConfigManager.set(patch)` 立即生效，部分项（如 `dataSource`）需要重启应用。

---

## 6. 安全与沙箱

- `contextIsolation: true` + `nodeIntegration: false`，渲染层无 Node 能力
- preload 仅暴露 `window.mookquant.facade`，接口面最小化
- CSP 由 `main.js → installCspHeader()` 注入：`default-src 'self' file: data: blob:`、`script-src 'self' file: 'unsafe-inline'`
- `setWindowOpenHandler` 拦截 `window.open`，外链统一走系统浏览器
- 渲染层只能访问 `dist/renderer/` 产物；主进程业务代码仅在主进程执行

---

## 7. 启动流程（main.js）

```
1. 解析 .env（手动解析，避免 dotenv 依赖）
2. app.whenReady()
   ├─ 安装 CSP header
   ├─ 创建 ConfigManager  → 读取 config/default.json
   ├─ 显示 splash 窗口（启动进度条）
   ├─ 初始化 StrategyService
   ├─ createDataSource({ mode: dataSource })
   │     └─ 主进程订阅 source.onPush("tick") → webContents.send("quote:tick", ...)
   ├─ 创建 QuoteService（含股票列表预加载 + QMT 后台同步）
   ├─ createTradeDataSource(...) + TradeService
   ├─ BacktestService + ExecutorService.restoreRunning()   // 恢复上次运行的策略
   ├─ registerIpc(...)                                     // 注册全部 IPC 路由
   └─ 关闭 splash，创建主窗口
3. mainWindow.once("ready-to-show") → maximize + show
4. app.on("window-all-closed") → 资源 dispose → 退出
```

---

## 8. 目录速查

```
mooquant/
├── main.js                  # 主进程入口
├── preload.js               # contextBridge 注入 facade
├── config/default.json      # 应用配置
├── main/
│   ├── ipc/index.js         # IPC 路由
│   ├── services/            # 业务服务
│   │   ├── quote-service.js
│   │   ├── strategy-service.js
│   │   ├── backtest-service.js
│   │   ├── trade-service.js
│   │   ├── executor-service.js    # 策略实盘调度
│   │   ├── config-manager.js
│   │   └── log-service.js
│   ├── datasources/         # 数据源抽象与实现
│   │   ├── index.js         # 行情源工厂
│   │   ├── mock.js          # MockDataSource
│   │   ├── qmt.js           # QmtDataSource
│   │   ├── backtest-engine.js    # Python 回测调度
│   │   └── trade/           # 交易源
│   │       ├── index.js
│   │       ├── mock-trade.js
│   │       └── qmt-trade.js
│   ├── strategies/
│   │   └── ma_cross.js      # 示例策略（均线交叉）
│   └── utils/pinyin.js
├── bridge/                  # Python 子进程桥
│   ├── qmt_server.py        # stdio JSON-RPC
│   ├── backtest_engine.py
│   ├── db.py
│   └── requirements.txt
├── renderer/                # 渲染层（vite 构建）
│   ├── index.html
│   ├── splash.html
│   ├── css/{base,app}.css
│   └── js/
│       ├── main.js          # 入口，连接所有 ViewModel+View
│       ├── router.js        # 单页路由（hash）
│       ├── services/facade.js
│       ├── viewmodels/      # 6 个 VM
│       ├── views/           # 11 个视图组件
│       └── utils/pinyin.js
└── docs/.ai/
    ├── plan/                # 项目计划文档
    └── knowledge-library/   # 本知识库
```

---

## 9. 后续扩展建议

- **多数据源**：在 `datasources/index.js` 注册新工厂分支；接口保持不变
- **更多策略**：在 `main/strategies/` 新增实现，注册到 `executor-service.js` 的 `SIGNAL_FUNCTIONS`
- **替换 UI 框架**：当前 View 层是 vanilla ES6，渐进替换为 Vue/React 不影响主进程与 IPC 协议
- **跨端**：将渲染层打包为 Web 部署，主进程保留作为桌面端桥（需调整 IPC 通道）