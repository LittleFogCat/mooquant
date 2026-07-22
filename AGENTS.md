# Agent Guidance

## 项目概述

mookquant 是基于 electron 构建的桌面应用，用于查询量化投资应用。用户输入 A 股 / 美股代码查询实时行情快照。数据源可插拔：QMT（真实行情，经 Python 桥）、Mock（本地模拟）、Auto（先试 QMT，连不上回落 mock）。

- **包名**：`mookquant`（package.json），目录名为 `mooquant`
- **平台**：Windows / macOS / Linux
- **技术栈**：Electron 31 + 原生 JS（无框架），Python 3 桥接（xtquant）
- **无测试框架、无 lint 配置**

## 常用命令

```bash
npm install            # 安装 Node 依赖（需 Node.js 18+）
npm start              # 启动 Electron（默认 auto 数据源）
npm run dev            # 启动并开启 --enable-logging
npm run bridge:install # 安装 Python 桥依赖（cd bridge && pip install -r requirements.txt）
npm run pack           # electron-builder --dir（不打包成安装器）
npm run dist           # 打包分发（dist:win / dist:mac / dist:linux 指定平台）
```

- 调试渲染层：应用窗口内 `Cmd/Ctrl + Shift + I`
- 切换到强制 mock：改 `config/default.json` 的 `"dataSource": "mock"`

## 架构：严格分层，切勿越层

数据流：`renderer (View → ViewModel → Facade)` →IPC→ `main (IPC 路由 → Service → DataSource)` →stdio→ `bridge (Python)`

```
renderer/                        main/                           bridge/
  View (DOM)                       IPC 路由 (转发-only)             qmt_server.py
    ↕                               ↕                               (xtquant)
  ViewModel (状态)                  Service (缓存/编排)              ↑ stdio
    ↕                               ↕                               JSON-RPC
  Facade (IPC 封装) ── preload ──  DataSource (抽象/工厂)
```

### 主进程（main/ + main.js）

- **`main.js`** — 只做窗口创建、IPC 注册、依赖图组装（DataSource → Service → IPC）、生命周期管理、CSP 头注入。不写业务。
- **`main/ipc/index.js`** — IPC 路由，*唯一职责是把调用转给 Service，绝不写业务逻辑*。
- **`main/services/quote-service.js`** — 业务编排层：查询 + 可选 TTL 缓存 + 统一 `{ok, data?, error?}` 返回格式。
- **`main/datasources/`** — 数据源抽象层。所有实现遵循同一接口：`{ mode, description, async getQuote(rawSymbol), dispose() }`。`createDataSource({mode})` 工厂按模式实例化；`auto` 模式在 `index.js` 里 try QMT、catch 回落 mock。
  - `mock.js` — 本地模拟数据，分钟级时间种子缓慢变动模拟"实时"感
  - `qmt.js` — spawn Python 子进程，stdio JSON-RPC 通信，代码格式转换（`sh600519` → `600519.SH`）

### 渲染层（renderer/）— 极简 MVVM

- **`bootstrap.js`** — 唯一有副作用的入口：把 View 接到 ViewModel、ViewModel 接到 Facade。其余渲染层文件**在 bootstrap 执行前不碰 DOM**。
- **`viewmodels/quote-viewmodel.js`** — `state` 是单一事实源，`subscribe/notify` 驱动视图。
- **`views/`** — 纯 DOM 渲染，每个视图暴露 `window.XxxView.render(el, ...)`，无状态。
  - `search-panel.js` — 搜索框 + 热门标签
  - `result-card.js` — 行情卡片（价格、涨跌、指标网格）
  - `status-toast.js` — 错误提示
- **`services/facade.js`** — *渲染层唯一接触 `window.mookquant.facade` 的地方*。当不在 Electron 环境（纯浏览器）时，注入前端 mock facade，让 UI 无需启动 Electron 就能演示。

### 安全桥接

- **`preload.js`** — *渲染层 → 主进程唯一允许的接触面*。用 `contextBridge` 暴露 `window.mookquant.facade`，每个方法是包装好的 `ipcRenderer.invoke` Promise。`contextIsolation: true`，绝不直接暴露 `ipcRenderer`。

### Python 桥（bridge/）

- **`qmt_server.py`** — 主进程通过 `spawn` 启动的 stdio JSON-RPC 服务（行分隔 JSON）。请求 `{"id","method","params"}`，响应 `{"id","result"}` 或 `{"id","error"}`。
  - 已实现方法：`ping`（心跳）、`quote.*`（行情快照/K线/订阅）、`trade.*`（下单/撤单/持仓/资金）、`stock.*`（股票同步/列表）、`strategy.list/signal/export/add/delete`（策略框架：列策略/算信号/导出/添加自定义策略/删除策略）
  - **xtquant 路径**：通过 `.env` 中 `XTQUANT_PATH` 配置，`ensure_xtquant()` 读取 `os.environ.get("XTQUANT_PATH")`
  - **miniQMT 连接**：通过 `.env` 中 `QMT_HOST` / `QMT_PORT` 配置（默认 127.0.0.1:58610）
  - **编码处理**：强制 UTF-8 stdout，并用 `contextlib.redirect_stdout` 抑制 xtquant 内部输出（避免破坏 JSON-RPC 协议）
  - **代码转换**：`to_xtcode()` 把 UI 代码（`sh600519`/`sz000001`/`AAPL`）转为 xtquant 格式（`600519.SH`/`000001.SZ`/`AAPL.US`）
  - **字段映射**：`handle_quote_snapshot` 把 xtquant tick 数据映射到统一领域模型

## 关键约定与陷阱

- **代码格式转换**：内部统一用 `sh600519`/`sz000001`/`AAPL`；QMT 侧转为 `600519.SH`/`000001.SZ`/`AAPL.US`。转换逻辑在两处：`facade.js`（浏览器 mock）和 `qmt_server.py` 的 `to_xtcode()`。
- **行情数据领域模型**统一形状：`code/name/industry/market/marketName/currency/price/open/high/low/prevClose/change/changePercent/volume/turnover/timestamp`。新增数据源必须映射到这个形状。
- **成交量单位**：xtquant 返回的是「手」，`qmt_server.py` 中已 ×100 转为「股」。
- **配色**：中国市场习惯，**红涨绿跌**（CSS class `up`/`down`）。
- **CSP**：通过 `session.webRequest.onHeadersReceived` 注入响应头（非 meta 标签），兼容 Electron 31+ 和 file:// 协议。
- **无副作用原则**：渲染层除 `bootstrap.js` 外的文件不碰 DOM，直到 bootstrap 执行。
- **新增行情功能时**，遵循分层：新 IPC 通道要在 `preload.js` 暴露 + `ipc/index.js` 注册 + Service 编排，不要在 IPC 层或 preload 写业务。
- **auto 模式回落**：`createDataSource({mode:"auto"})` 会 try QMT init，失败后静默回落 mock。调试时注意控制台 `[datasource]` 日志区分实际数据源。
- **策略统一**：策略信号计算统一在 `bridge/strategies/`（Python），回测引擎与实盘执行器调用**同一个 `on_bar`**，Node 侧不再写策略逻辑（`main/strategies/ma_cross.js` 已 deprecated）。新增策略 = UI 编写代码自动注册（`strategy.add` RPC，保存即生效无需重启），或丢 `.py` 到 `data/strategies/user/`。策略 RPC 走独立 strategyBridge（不依赖行情数据源，mock 模式也可用）。UI 据 `params_schema` 自动生成参数表单。
- **策略导出**：`strategy.export` RPC 用适配壳包装，把策略类源码原样嵌入 QMT 单文件脚本（init/handlebar/stop 壳 + Context 适配层），粘到 QMT 客户端即可运行。

## 项目结构

```
mooquant/
├── package.json
├── main.js                       # Electron 主进程入口
├── preload.js                    # 安全桥接（contextBridge）
├── config/
│   └── default.json              # 应用配置（dataSource/cache/qmt/window）
├── main/                         # 主进程业务代码
│   ├── ipc/index.js              # IPC 路由器（转发-only）
│   ├── services/quote-service.js # 行情业务服务（缓存+编排）
│   └── datasources/              # 数据源适配层
│       ├── index.js              # 抽象接口 + 工厂（auto 回落逻辑）
│       ├── mock.js               # 模拟数据源
│       └── qmt.js                # QMT 桥接数据源（spawn Python）
├── bridge/                       # Python 桥
│   ├── qmt_server.py             # stdio JSON-RPC 服务（xtquant 真实接入 + strategy.* RPC）
│   ├── backtest_engine.py        # 回测引擎（逐bar调策略 on_bar，回测=实盘）
│   ├── strategies/               # 策略框架（回测与实盘共用，详见 docs/.ai/knowledge-library/03-strategy/）
│   │   ├── base.py               # StrategyBase + Signal + Context（跨平台接口）
│   │   ├── indicators.py         # 指标库（MA/EMA/MACD/RSI/KDJ/BOLL）
│   │   ├── registry.py           # 自动扫描注册（builtin/ + data/strategies/user/）
│   │   ├── builtin/              # 内置策略（ma_cross/momentum/mean_reversion）
│   │   └── exporters/            # 平台导出器（qmt_exporter 适配壳包装）
│   ├── requirements.txt          # xtquant>=1.0
│   └── README.md
├── renderer/                     # 渲染层
│   ├── index.html
│   ├── css/{base,app}.css        # 苹果风样式
│   └── js/
│       ├── bootstrap.js          # 启动入口（唯一副作用文件）
│       ├── services/facade.js    # 屏蔽 IPC 细节 + 浏览器 mock
│       ├── viewmodels/quote-viewmodel.js
│       └── views/                # 纯 DOM 渲染
│           ├── search-panel.js
│           ├── result-card.js
│           └── status-toast.js
├── assets/                       # 图标
└── docs/.ai/                     # AI 规划文档 + xtquant API 参考
    ├── plan/first_demo.md
    ├── xtdata_api.html
    └── xtquant_official_examples.html
```

## 环境备忘（重要！）

- **系统 Python**: 通过 `.env` 中 `MOOKQUANT_PYTHON` 配置（需包含 numpy 等依赖）
- **config/default.json 的 `qmt.python`**: 默认 `"python"`，由 `.env` 中 `MOOKQUANT_PYTHON` 覆盖
- **xtquant 路径**: 通过 `.env` 中 `XTQUANT_PATH` 配置
- **npm install 权限问题**: 如遇 EPERM，设置 `npm_config_cache` 为项目内目录（如 `.npm-cache`）
- **miniQMT**: 通过 `.env` 中 `QMT_HOST` / `QMT_PORT` 配置（默认 127.0.0.1:58610）
- 知识库文档根目录在 `/docs/.ai/knowledge-library/`，如果对项目有疑问，可以先查看知识库文档

## 其他

### 代码质量约定

- 有意识的遵循干净整洁的代码架构，对界面、业务、数据有清晰的分层，对各个类的逻辑有明确的交代
- 遵循高内聚低耦合的代码设计，严格按照职责分离，避免耦合和冗余
- 适量的注释，对接口方法、重要逻辑、关键变量等必须进行注释，提高代码的可读性和可维护性
- 应有意识的复用 UI 组件，能判断不同页面的相似组件是否可以、是否应该复用
- 对一些复杂且容易出错的通用功能，例如图表、拼音等，应优先考虑采用成熟的第三方实现
- 在更新了任何内容后，需要判断是否应该同步更新知识库文档
