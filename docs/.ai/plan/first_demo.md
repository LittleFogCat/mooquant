# 初版演示

## 任务描述

这是一个量化交易项目，基于 electron 构建的桌面应用。本次任务的目标是做一个初版的模型，实现基本的界面、逻辑、数据获取。

---

## 一、项目目标与边界

### 1.1 目标
做出 **第一版可运行的演示 demo**，主流程跑通：

```
用户在搜索框输入股票代码 → 点击查询 → 主进程调用 QMT 数据源 → 渲染层拿到结构化数据 → 苹果风卡片展示
```

### 1.2 初版范围（克制，绝不堆功能）
- ✅ 单只股票代码查询（A 股 6 位、美股代码）
- ✅ 当前价、涨跌幅、今开、昨收、最高、最低、成交量、成交额
- ✅ QMT 数据源接入（带 mock 兜底）
- ✅ 苹果风毛玻璃 UI
- ⚠️ 不做：K 线图、技术指标、自选股、订阅刷新、多股票对比（预留扩展点）

### 1.3 设计原则
> **初版功能尽量少，但架构必须留好扩展口，避免形成屎山。**

四条红线：
1. **分层清晰**：渲染层 / 视图模型层 / 数据访问层 / 进程边界 一目了然
2. **进程边界严格**：渲染层永远不直接调用 QMT，全部经过 IPC + 主进程或单独 worker
3. **数据源可替换**：`DataSource` 接口 + 多实现（QmtSource / MockSource / …）
4. **UI 与逻辑解耦**：渲染层只声明"想要什么数据"，不关心数据从哪来

---

## 二、技术栈

### 2.1 选型

| 层 | 选型 | 理由 |
|---|---|---|
| 桌面壳 | **Electron 31+** | 跨平台、生态成熟、与 Node.js 同源 |
| 主进程 | **Node.js (CommonJS)** | 与 Electron 默认一致，避免 ESM/CommonJS 互操作 |
| 渲染层 | **原生 ES6 + 模块化** | 0 依赖、零打包、易调试；后续可平滑接入 Vue/React |
| 视图模式 | **MVVM 手写轻量版** | 体量小，没必要上 Vue；对扩展友好 |
| 数据源通信 | **IPC + spawn 子进程**（Python QMT 桥） | QMT SDK 是 Python，Electron 主进程直接调用不便，需独立 Python 服务 |
| QMT 接入 | **xtquant（迅投官方） + miniQMT** | 主人指定；行业标准 |
| 打包 | **electron-builder** | 一键三平台安装包 |
| 样式 | **纯 CSS**（无 Tailwind / Sass） | 减少构建链 |

### 2.2 是否引入框架？
**不引入**。理由：
- 初版就一个查询面板，引入 Vue/React 反而把项目撑大
- 用一个自写的极简 MVVM（70 行内）足够；后续如果页面变多，再换 Vue

### 2.3 主进程与 Python 通信方案

```
┌──────────┐     IPC      ┌──────────┐    IPC+JSON    ┌──────────┐    TCP    ┌──────────┐
│ Renderer │ ──────────▶  │  Main    │ ───────────▶  │ QMT      │ ────────▶ │ miniQMT  │
│ (web)    │              │ (Node)   │     JSON      │ Bridge   │  (Python) │ (迅投)   │
└──────────┘              └──────────┘               └──────────┘           └──────────┘
```

- **主进程不直接 import xtquant**（xtquant 是 Python 包，强行调会很难维护）
- 启动一个 **Python 子进程**（`bridge/qmt_server.py`）作为本地服务
- 通信协议：JSON-RPC over stdio（最简单，无需端口探测）
- 子进程崩溃可重启，主进程负责生命周期

---

## 三、整体架构（MVVM + 进程分层）

### 3.1 分层图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Renderer Process (沙箱)                       │
│                                                                 │
│   ┌──────────────┐  观察  ┌────────────────┐  调用  ┌──────────┐ │
│   │     View     │ ◀──── │  ViewModel     │ ─────▶ │ Service  │ │
│   │  (HTML+CSS)  │       │  (app.js)      │        │  Facade  │ │
│   └──────────────┘        └────────────────┘        └─────┬────┘ │
│        ▲                          │                       │     │
│        │ DOM 更新                 │ 状态                  │ IPC │
└────────┼──────────────────────────┼───────────────────────┼─────┘
         │                          │                       │
         │                          ▼                       │
┌────────┴─────────────────────────────────────────────────────┐
│                    Main Process (Node)                         │
│                                                                 │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐  │
│   │  IPC Router  │──▶│ QuoteService │──▶│ DataSource       │  │
│   │  (ipcMain)   │   │ (业务编排)    │   │ 抽象接口           │  │
│   └──────────────┘   └──────────────┘   └────────┬─────────┘  │
│                                                   │             │
│                                                   ▼             │
│                                          ┌────────────────┐     │
│                                          │ QmtDataSource  │     │
│                                          │ MockDataSource │     │
│                                          └────────────────┘     │
│                                                   │ stdio       │
└───────────────────────────────────────────────────┼─────────────┘
                                                    ▼
                                          ┌────────────────┐
                                          │ bridge/qmt_    │
                                          │ server.py      │
                                          └────────────────┘
```

### 3.2 各层职责

| 层 | 文件 | 职责 |
|---|---|---|
| **View 视图** | `index.html`、`css/*.css` | 纯粹 UI 描述，无逻辑 |
| **ViewModel 视图模型** | `js/viewmodels/*.js` | UI 状态、表单校验、把 Service 返回值映射成 View 友好的形状 |
| **Service 服务（门面）** | `js/services/*.js`、`preload.js` | 对 ViewModel 暴露简洁 API，屏蔽 IPC 细节 |
| **IPC Router** | `main.js` | 接收渲染层调用，转发到业务层 |
| **业务服务** | `main/services/quote-service.js` | 业务编排：参数校验、缓存、错误规整 |
| **数据访问层** | `main/datasources/*.js` | 不同数据源适配，实现同一接口 |
| **数据源实现** | `main/datasources/qmt.js` / `mock.js` | 各自负责调用真实或模拟数据 |
| **外部桥接进程** | `bridge/qmt_server.py` | 与 miniQMT 通信的 Python 服务 |

### 3.3 MVVM 极简实现要点

ViewModel 暴露三个方法就够初版用：

```js
class QuoteViewModel {
  constructor(service) { this.service = service; this.state = {...}; this.subs = []; }
  subscribe(fn) { this.subs.push(fn); }                 // 订阅状态变化
  notify() { this.subs.forEach(fn => fn(this.state)); } // 通知 View
  async query(symbol) { ... }                           // 业务方法
}
```

不引入框架也能享受响应式的好处：状态变了 → 调 `notify()` → View `render()`。

---

## 四、UI 风格设计

### 4.1 整体调性
- **Apple Human Interface Guidelines** 原生映射
- 配色：浅灰底 `#f5f5f7` + 三色径向高斯模糊作为背景氛围
- 字体栈：`SF Pro Display` → `PingFang SC` → `Microsoft YaHei`
- 圆角：10 / 16 / 24px 三档
- 阴影：彩色、长投影、低不透明（`0 20px 60px rgba(0,0,0,0.08)`）
- **毛玻璃**：`backdrop-filter: saturate(180%) blur(20px)`
- **A 股配色**：红涨绿跌（与海外相反，是有意为之）

### 4.2 信息层级（重要程度递减）

```
┌─────────────────────────────────────────────────────┐
│  Hero                                                │
│  📈 股票查询                                          │
│  输入股票代码，获取实时行情                              │
├─────────────────────────────────────────────────────┤
│  Search Card（毛玻璃）                                 │
│  [__________输入框__________]  [查询]                  │
│  热门：贵州茅台 · 平安银行 · 中国平安 · 宁德时代           │
├─────────────────────────────────────────────────────┤
│  Result Card（毛玻璃）                                 │
│  股票名 [SH]   1680.50                                │
│  sh600519        ▲+12.30 (+0.74%)                    │
│  ─────────────────────────────────────               │
│  今开    昨收    最高    最低                          │
│  成交量            成交额                              │
│  行业    振幅                                         │
│  最后更新：2026-07-13 12:34:56                         │
├─────────────────────────────────────────────────────┤
│  Footer: mookquant · 桌面版 v0.1.0                     │
└─────────────────────────────────────────────────────┘
```

### 4.3 交互细节
- Enter 直接查询，无需鼠标点按钮
- 输入框获得焦点时蓝色光环（`box-shadow: 0 0 0 4px rgba(0,113,227,.15)`）
- 按钮 hover 上浮 + 投影变深
- 查询中按钮变 "查询..." + 失能
- 卡片加载用 `slideUp` 动画，不用遮罩
- 手机端响应式：600px 以下变单列，搜索框与按钮上下堆叠

### 4.4 不在初版做的事
- ❌ 暗色模式（留 hook 不实现）
- ❌ 多页签 / 多窗口
- ❌ K 线图 / 复杂图表（用 ECharts？还是 native canvas？留决定给下一版）

---

## 五、业务逻辑设计

### 5.1 单只股票查询流程

```
┌────────┐  ①输入   ┌──────────┐  ②调用   ┌────────────┐  ③路由  ┌─────────┐  ④调用  ┌────────────┐
│  用户   │ ────▶  │ ViewModel │ ──────▶ │ Service    │ ──────▶ │ DataSrc │ ──────▶ │ QmtSource  │
└────────┘          └──────────┘          │ Facade     │         └─────────┘         └────────────┘
                                          └────────────┘
        ⑩渲染   ┌──────────┐  ⑨返回  ┌──────────┐  ⑧返回  ┌─────────┐  ⑦返回  ┌─────────┐
        ◀───── │   View   │ ◀───── │ ViewModel │ ◀───── │ BusSvc  │ ◀───── │ Mock/QMT │ 
                                          
```

### 5.2 错误处理矩阵

| 场景 | 表现 |
|---|---|
| 空代码 | 输入框红边 + "请输入股票代码" |
| 代码格式无效 | "代码无效，请检查" |
| QMT 未连接 | "QMT 客户端未启动 / 已掉线" |
| 查不到该代码 | "未找到该标的（600519）" |
| 数据源异常 | "服务暂不可用，请稍后重试" |

所有错误统一通过 ViewModel `state.error` 字段传递，View 用一个 toast/状态条展示。

### 5.3 配置 & 环境变量

`config/default.json`：
```json
{
  "dataSource": "auto",           // auto | qmt | mock
  "cache": { "ttlMs": 5000 },
  "qmt": { "host": "127.0.0.1", "port": 58620 }
}
```

- `auto`：主进程尝试连 QMT，连不上自动 fallback 到 mock（开发期很贴心）
- `qmt`：强制要求 QMT，连不上就报错
- `mock`：纯演示，不连真实服务

---

## 六、数据获取与 QMT 接入

### 6.1 整体方案

主进程 → Python 子进程（桥接） → miniQMT 客户端（迅投） → 行情数据

### 6.2 Python 桥接服务（`bridge/qmt_server.py`）

采用 **stdio JSON-RPC**，最简单：

- 主进程 `spawn('python', ['bridge/qmt_server.py'])`
- Python 服务读 stdin、写 stdout，每行一条 JSON
- 协议示例：
  ```json
  {"id": "1", "method": "quote.subscribe", "params": {"code": "600519.SH"}}
  {"id": "1", "result": {"price": 1680.50, ...}}
  ```

### 6.3 关键 xtquant 函数（**待主人实测校对**）

| 用途 | 函数（猜测/常用名） |
|---|---|
| 订阅实时行情 | `subscribe_quote(stock_code, period='tick')` |
| 拉快照 | `get_full_tick(code_list)` |
| 历史 K 线 | `download_history_data(code, period, start, end)` |
| 财务数据 | `request_stock_finance_data` |

> ⚠️ **待实测**：xtquant 各版本函数签名可能不一样，需要主人在正式接入时按本地 `xtquant` 包签名为准。

### 6.4 回退策略
- QMT 桥超时（>3s）→ 自动用 mock 数据 + 显示"演示数据"标签
- 桥接进程崩溃 → 主进程自动重启 + 在状态栏提示

---

## 七、目录结构

```
mooquant/
├── package.json
├── electron-builder.yml              # 打包配置
├── main.js                            # Electron 主进程入口
├── preload.js                         # 渲染层桥
├── config/
│   └── default.json                   # 应用配置
├── main/                              # 主进程业务代码
│   ├── ipc/
│   │   └── index.js                   # IPC 路由器
│   ├── services/
│   │   └── quote-service.js           # 行情业务服务
│   └── datasources/
│       ├── index.js                   # 抽象接口 + 工厂
│       ├── mock.js                    # 模拟数据
│       └── qmt.js                     # QMT 桥接
├── bridge/
│   ├── qmt_server.py                  # Python 桥（stdin/stdout JSON-RPC）
│   ├── requirements.txt               # xtquant 等依赖
│   └── README.md                      # 桥接使用说明
├── renderer/                          # 渲染层
│   ├── index.html                     # 入口
│   ├── css/
│   │   ├── base.css                   # 变量 + reset
│   │   └── app.css                    # 组件样式
│   └── js/
│       ├── bootstrap.js               # 启动入口，连接 preload 暴露的 facade
│       ├── services/
│       │   └── facade.js              # 封装 preload 暴露的方法
│       ├── viewmodels/
│       │   └── quote-viewmodel.js     # MVVM 核心
│       └── views/
│           ├── search-panel.js        # 搜索面板渲染
│           ├── result-card.js         # 结果卡片渲染
│           └── status-toast.js        # 状态提示
├── assets/
│   ├── icon.png
│   └── icon-512.png
├── docs/
│   └── .ai/plan/first_demo.md         # 本文件
└── README.md
```

> 当前已有文件会被迁入新结构；不会引入破坏式变更。

---

## 八、实施步骤（按这个顺序推进）

1. **建立分层骨架**：新建 `main/`、`renderer/js/{services,viewmodels,views}/`、`bridge/` 目录
2. **抽象数据源接口**：`datasources/index.js` 定义 `DataSource` 抽象类
3. **实现 MockDataSource**：把现有 `data-source.js` 的 mock 逻辑搬过来并接口化
4. **主进程 + 渲染层**：保留现有 `main.js`、`preload.js`、`index.html`、`style.css` 兼容
5. **验证 mock 链路通**：npm start 能用 mock 数据展示股票卡
6. **实现 QmtDataSource + Python 桥**：接入真实行情（**分两步：第一版只接入实时快照**）
7. **打包**：用 electron-builder 出 Windows 安装包做 demo

---

## 九、验证 & 验收

### 9.1 功能验证
- [ ] 输入 `sh600519` → 显示贵州茅台信息卡
- [ ] 输入 `AAPL` → 显示 Apple 信息卡
- [ ] 输入空 → 输入框红边 + 提示
- [ ] 输入垃圾代码 → 友好错误
- [ ] Enter 直接查询有效
- [ ] 快捷标签点击有效

### 9.2 架构验收（**比功能更重要**）
- [ ] 渲染层 *不* 引入 Node API、不直接 require 任何东西
- [ ] 数据源可切换：把 `dataSource: "mock"` 改成 `"qmt"` 即可工作
- [ ] 新增一个 DataSource 实现只需要改 `main/datasources/index.js` 的工厂
- [ ] 业务逻辑单元测试（后续阶段）：Service 层可独立测试，不依赖 Electron

### 9.3 UI 验收
- [ ] macOS 标题栏隐藏式 + traffic-light 按钮正常
- [ ] 毛玻璃、字体、颜色都符合苹果设计语言
- [ ] 浏览器 / Electron 双环境都跑得通
- [ ] 600px 以下响应式 OK

---

## 十、风险 & 决策记录

| 风险 | 应对 |
|---|---|
| QMT 桥接不稳定 | mock 兜底 + 状态提示 |
| xtquant 函数签名各券商不一样 | 在接口层做适配，`qmt.js` 是唯一需要改的文件 |
| Electron 体积大 | electron-builder 输出 nsis 安装包，约 80MB 可接受 |
| 没有 native UI 框架 | 初版可用，后续可渐进替换 Vue 单组件而不动架构 |
| CSP 影响后续接入图表库 | 在 `index.html` 的 CSP 中预留 `cdn.example.com` 的口子 |

---

## 十一、参考资料 / 待主人确认

- 主人电脑是否已安装 **miniQMT 客户端**？IP/端口默认是 `127.0.0.1:58620`（需实测）
- 已有 **国金 QMT 账号**与 **`xtquant` 包**安装情况？（主人提供的接口是国金 QMT 下的标准迅投 SDK）
- 如果主人希望 **跳过 Python 桥**，直接用 Node.js 通过 HTTP 调中转服务也是备选方案 —— 但默认采用 **stdio 子进程**，最简洁

> 初版部署门槛：Node 18+ + Python 3.9+ + 国金 miniQMT 客户端在线