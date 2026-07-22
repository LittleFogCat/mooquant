# 技术栈

## 1. 总览

| 类别 | 选型 | 版本 | 用途 |
|---|---|---|---|
| 桌面壳 | Electron | ^31.0.0 | 跨平台桌面运行时（Win/macOS/Linux） |
| 打包工具 | electron-builder | ^24.13.3 | 生成 NSIS / DMG / AppImage 安装包 |
| 渲染构建 | Vite | ^8.1.5 | 渲染层 ES 模块打包、HMR |
| 行情图表 | ECharts | ^6.1.0 | K线图、净值曲线 |
| 日期选择 | flatpickr | ^4.6.13 | 回测起止日期、设置页日期 |
| 中文拼音 | pinyin-pro | ^3.28.1 | 股票搜索支持拼音首字母 |
| 行情 SDK | xtquant（迅投） | 由用户安装 | 国金 QMT miniQMT 实盘数据 |
| 语言 | Node.js | ≥18 | 主进程 |
| 语言 | Python | ≥3.9 | 子进程桥（xtquant 调用层） |
| 深度学习 | PyTorch | ≥2.0 | 模型训练与推理（可选依赖） |

> **原则**：能少则少。尽量复用成熟三方库，自写代码只解决"三方解决不了的部分"。

---

## 2. 主进程（Node.js · CommonJS）

| 项 | 说明 |
|---|---|
| 模块系统 | CommonJS（与 Electron 默认一致；无需 ESM/CommonJS 互操作） |
| 入口 | `main.js` |
| 进程模型 | 主进程 + 渲染进程（多 BrowserWindow） + Python 子进程 |
| 配置文件 | `config/default.json`（运行时可写）+ `.env`（敏感信息，gitignore） |
| 持久化 | JSON 文件（`data/strategies/`、`data/logs/`），无数据库依赖 |
| 进程间通信 | Electron `ipcMain.handle` / `ipcRenderer.invoke`（请求-响应） + `webContents.send`（主进程推送） |

主进程用到的 Node 内置/标准模块：

- `electron`：`app`、`BrowserWindow`、`Menu`、`shell`、`session`、`ipcMain`
- `fs` / `path`：配置、日志、策略文件
- `crypto`：策略 ID 生成

**没有引入**：Express、TypeORM、dotenv、log4j 等。`.env` 解析手写（约 10 行），免去一个依赖。

---

## 3. 渲染层（原生 ES6 + Vite）

| 项 | 说明 |
|---|---|
| 框架 | **不引入**（不引入 Vue/React/Angular） |
| 模块系统 | ES Modules（`import` / `export`） |
| 样式 | 纯 CSS + CSS 变量；构建后单文件 |
| 入口 | `renderer/js/main.js`，被 `index.html` 通过 `<script type="module">` 加载 |
| 状态管理 | 自写极简 MVVM（ViewModel 持 state + `subscribe(fn)` 派发） |
| 路由 | 自写 hash 路由（`renderer/js/router.js`），6 个页面 |
| 跨端兼容 | Facade 双模式：Electron 走 `window.mookquant.facade`；纯浏览器走内置 mock，方便 UI 调试 |

### 为什么不上 Vue / React？

初版页面少（5 个），自写 MVVM 总代码量 < 100 行；引入框架会把项目撑大。等到页面 > 10 个或需要组件复用时再渐进替换，**架构已留好扩展口**（View 与 ViewModel 边界清晰）。

### CSS 变量主题（`renderer/css/base.css`）

```css
:root {
  --bg-0: #0a0a0f;        /* 主背景 */
  --bg-1: #12121a;
  --bg-2: #1a1a25;
  --bg-3: #22222e;
  --text-1: #e8e8ed;
  --text-2: #9a9aa8;
  --text-3: #5a5a68;
  --accent: #6366f1;      /* 主题色（靛紫） */
  --accent-hover: #818cf8;
  --green: #22c55e;       /* 涨 */
  --red: #ef4444;         /* 跌 */
  --orange: #f59e0b;      /* mock/warn */
  --glass-bg: rgba(26, 26, 37, .7);
  --radius-md: 12px;
  --radius-lg: 20px;
}
```

整体走 **苹果风毛玻璃 + 暗色主题 + 红涨绿跌（A股习惯）**。

---

## 4. 渲染层三方库

### ECharts（^6.1.0）

K线图（`renderer/js/views/kline-chart.js`）使用 ECharts 自绘：
- 蜡烛图主图 + 成交量副图
- `dataZoom` 内置滑块，支持拖拽 / 滚轮缩放
- 自定义 MA(5/10/20/60) 指标线
- 复权切换（前复权 / 后复权 / 不复权）
- 多周期切换（1m / 5m / 15m / 30m / 60m / 1d / 1w / 1M）

Vite 配置中拆包：

```js
manualChunks(id) {
  if (id.includes('node_modules/echarts')) return 'echarts';
  if (id.includes('node_modules/flatpickr')) return 'flatpickr';
  if (id.includes('node_modules/pinyin-pro')) return 'pinyin-pro';
}
```

### flatpickr（^4.6.13）

回测页面的起止日期选择、设置页的某日日志查询。已自定义 CSS 适配暗色主题。

### pinyin-pro（^3.28.1）

`getInitials(name)` 计算中文拼音首字母，用于股票搜索的拼音输入（如 `gzmt` → 贵州茅台）：
- 主进程：`main/utils/pinyin.js`（`require("pinyin-pro")`）
- 渲染层：`renderer/js/utils/pinyin.js`（同包，浏览器 ESM 版本）

预计算：股票列表加载后一次性算出 `_pinyin` 字段，避免每次搜索重复计算。

---

## 5. Python 桥

| 项 | 说明 |
|---|---|
| 入口 | `bridge/qmt_server.py`（常驻 stdin/stdout 进程） |
| 通信协议 | 行分隔 JSON-RPC：`{"id", "method", "params"}` → `{"id", "result" \| "error"}` |
| 主进程调用 | `child_process.spawn("python", ["qmt_server.py"])` |
| 关键依赖 | `xtquant`（迅投官方 Python 包，pip 安装） |
| 进程管理 | 主进程持有子进程句柄；启动失败/退出 → 日志告警 + 自动重启 |
| 已实现 | `ping`、`quote.snapshot`（快照）、`quote.history`（K 线）、`quote.full`（批量） |
| 待补全 | 实盘交易方法（`trade.connect/order/cancel/positions/orders/account`） |
| 辅助 | `bridge/backtest_engine.py`（回测调度）、`bridge/db.py`（本地行情缓存库）、`bridge/model_server.py`（HTTP 模型服务）、`bridge/qmt_shell.py`（QMT 壳策略） |

`.env` 关键变量：

```bash
QMT_ACCOUNT_ID=资金账号
QMT_ACCOUNT_TYPE=STOCK
QMT_PYTHON=python         # Python 可执行文件名（mac/linux 需调整）
```

---

## 6. 构建 & 运行

| 命令 | 作用 |
|---|---|
| `npm start` | `vite build` + `electron .`（生产模式启动） |
| `npm run dev` | `vite build` + `electron . --enable-logging` |
| `npm run vite:dev` | 仅启动 vite dev server（5173），浏览器调试 |
| `npm run vite:watch` | vite watch 模式 |
| `npm run bridge:install` | `pip install -r bridge/requirements.txt` |
| `npm run pack` | electron-builder `--dir`（解压目录） |
| `npm run dist` | electron-builder 当前平台安装包 |
| `npm run dist:win` / `dist:mac` / `dist:linux` | 三平台分别打包 |

### Vite 配置要点（`vite.config.js`）

```js
root: 'renderer',
base: './',                            // 相对路径，配合 file:// 协议
build: {
  outDir: '../dist/renderer',
  emptyOutDir: true,
  rollupOptions: {
    input: 'renderer/index.html',
    output: { manualChunks: ... }      // echarts / flatpickr / pinyin-pro 拆包
  }
}
plugins: [removeCrossorigin()]         // 去掉 HTML 中的 crossorigin 属性
```

---

## 7. 安全模型

| 机制 | 说明 |
|---|---|
| `contextIsolation: true` | 渲染层 JS 上下文与 preload 隔离 |
| `nodeIntegration: false` | 渲染层无法直接访问 Node API |
| `sandbox: false` | 允许 preload 正常使用 require（业务需要） |
| `webSecurity: true` | 启用同源策略 |
| `setWindowOpenHandler` | 拦截 `window.open`，外链走 `shell.openExternal` |
| CSP（`main.js → installCspHeader`） | `default-src 'self' file: data: blob:`、允许 `unsafe-inline`（Vite 注入需要） |
| preload 最小接口面 | 仅暴露 `window.mookquant.facade` 一个对象 |

---

## 8. 开发与调试

| 场景 | 方法 |
|---|---|
| 渲染层 UI 调试 | 启动后 `Cmd/Ctrl + Shift + I` 开 DevTools |
| 主进程日志 | `npm run dev`（启用 `--enable-logging`），终端查看 |
| 桥接进程日志 | 终端 `python bridge/qmt_server.py` 单跑 |
| 模拟模式（不连 QMT） | `config/default.json` 改 `"dataSource": "mock"` |
| 模拟交易（不下单） | `config/default.json` 改 `"tradeSource": "mock"` |
| 浏览器纯 UI 调试 | `npm run vite:dev`，访问 `http://localhost:5173`（Facade 自动走 mock） |

---

## 9. 第三方库的引入原则

1. **优先官方**：xtquant（迅投）、electron（GitHub 官方）— 同源生态稳定性最重要
2. **避免重量级 ORM / 框架**：JSON 文件 + 自写 Service 足以应对当前数据量
3. **图表**：ECharts 已是行业标准，不再造轮子
4. **如果未来需要**：图表可加 `klinecharts` 做候选；状态管理可考虑 `zustand` / `pinia`