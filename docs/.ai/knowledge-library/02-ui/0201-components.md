# 公共组件

> 渲染层所有 View 组件都在 `renderer/js/views/`，每个组件以 ES Module 形式导出 `render(root, ...)` 函数，遵循"订阅 state → 重渲染"模式。

## 设计原则

1. **纯 DOM 渲染**：View 不持有业务状态，只负责把 ViewModel 状态投影到 DOM
2. **单向数据流**：View 触发事件 → ViewModel 更新 state → View 收到回调 → 重渲染
3. **escape 优先**：所有用户输入 / 外部数据在写入 `innerHTML` 前必须 `escapeHtml()`
4. **CSS 类名约定**：`.up`（涨）/ `.down`（跌）/ `.flat`（平）三态，组件外部可直接套用

---

## 组件清单

| 组件 | 文件 | 职责 |
|---|---|---|
| 搜索面板 | `search-panel.js` | 股票代码/名称/拼音输入 + 热门快捷标签 |
| 股票搜索下拉 | `stock-search.js` | 输入框联想下拉，代码/中文/拼音三种匹配 |
| 结果卡片 | `result-card.js` | 单只股票快照展示（毛玻璃卡片 + 8 项指标） |
| 状态提示 | `status-toast.js` | 顶部错误条 / 加载提示 |
| K线图容器 | `kline-view.js` | 监听 state，加载历史数据并喂给图表组件 |
| K线图组件 | `kline-chart.js` | ECharts 自绘蜡烛图 + 成交量 + 指标线 + 周期切换 |
| 自选股列表 | `watchlist-view.js` | 侧边栏 ⭐ 自选股，行内展示价/涨跌幅 |
| 策略管理 | `strategy-view.js` | 策略 CRUD、列表、状态徽章、启动/停止、源码编辑、复制、导出 |
| 回测视图 | `backtest-view.js` | 回测表单、进度条、绩效指标、收益曲线 |
| 交易视图 | `trade-view.js` | 下单表单、持仓、当日委托、账户资金 |
| 设置视图 | `settings-view.js` | 数据源切换、QMT 连接参数、日志查询 |

---

## 1. 搜索面板 `search-panel.js`

### 形态

```
┌──────────────────────────────────────────────────────────┐
│  [ 输入 sh600519 / 茅台 / gzmt          ]  [  查询  ]    │
│  热门：贵州茅台  平安银行  中国平安  宁德时代  招商银行  │
└──────────────────────────────────────────────────────────┘
```

### 接口

```js
import * as SearchPanel from "./views/search-panel.js";
SearchPanel.render(rootEl, quoteViewModel);
```

### 行为

- 输入框 `input` → `viewModel.setSymbol(val)`
- 输入框失焦 / Enter → `viewModel.query()`
- 查询按钮 → 同上
- 快捷标签点击 → `viewModel.querySymbol(tag.symbol)`
- 内部挂载 `StockSearch` 子组件，输入时弹出联想下拉
- 订阅 `viewModel.state`：加载中给按钮加 `.loading`、非法输入给输入框加 `.invalid`

### 热门标签（`QUICK_TAGS`）

```js
const QUICK_TAGS = [
  { symbol: "sh600519", label: "贵州茅台" },
  { symbol: "sz000001", label: "平安银行" },
  { symbol: "sh601318", label: "中国平安" },
  { symbol: "sz300750", label: "宁德时代" },
  { symbol: "sh600036", label: "招商银行" },
  { symbol: "sz002594", label: "比亚迪" },
];
```

---

## 2. 股票搜索下拉 `stock-search.js`

联想下拉组件，挂载在搜索输入框下方，**position: fixed，append 到 body**（避免被父容器 overflow 裁剪）。

### 匹配规则

| 输入类型 | 判定正则 | 匹配字段 |
|---|---|---|
| 纯数字 | `/^\d+$/` | `code` 去掉 `sh/sz/bj` 前缀后 includes；或 code 全串 includes |
| 中文 | `/[\u4e00-\u9fa5]/` | `name` includes |
| 英文/拼音 | 其余 | `pinyin` 首字母 startsWith / includes；code includes |

匹配结果最多返回 20 条（按搜索结果顺序），包含 `code / name / industry / type`，并支持最近使用（LRU，标记 `.sugg-lru`）。

### 关键 CSS

```css
.stock-search-dropdown {
  position: fixed; z-index: 9999;
  background: var(--bg-2);
  border: 1px solid var(--border-hover);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  max-height: 320px; overflow-y: auto;
}
.suggestion-item:hover, .suggestion-item.active {
  background: var(--accent-dim);
}
```

### 键盘支持

- `↑` / `↓`：在结果项间移动高亮
- `Enter`：选中当前项触发 `onSelect(stock)`
- `Esc`：隐藏下拉
- 失焦：延迟 200ms 隐藏（避免点击下拉时被收起）

---

## 3. 结果卡片 `result-card.js`

行情页的主展示卡片，毛玻璃 + 大字号 + 红涨绿跌。

### 形态

```
┌────────────────────────────────────────────────────┐
│  贵州茅台 ●实时                   ⭐ 加入自选        │
│  sh600519  上海 · CNY                               │
│                                       ¥ 1680.50   │
│                                      ▲ +12.30     │
│                                      +0.74%       │
├────────────────────────────────────────────────────┤
│  今开     昨收     最高     最低     成交量     成交额     行业     振幅     │
│  1668.20  1668.20  1692.40  1665.10  12.34 万    ¥ 20.71亿  白酒    1.63%  │
├────────────────────────────────────────────────────┤
│  最后更新：2026-07-18 14:32:11                     │
└────────────────────────────────────────────────────┘
```

### 接口

```js
ResultCardView.render(root, quoteViewModel.state, dataSourceMode);
```

`mode` 用于渲染数据源标签：
- `"qmt"` → `<span class="source-tag">● 实时</span>`
- `"mock"` → `<span class="source-tag mock">● 演示</span>`

### 空态

未查询时显示空态卡：

```
┌────────────────────────────────┐
│           📈                   │
│      开始查询股票               │
│  在搜索栏输入股票代码或名称       │
│  [贵州茅台][招商银行][平安银行]… │
└────────────────────────────────┘
```

空态的快捷标签通过 `onclick="window._quickQuery(code)"` 触发（`main.js` 中暴露）。

### CSS 类

```css
.stock-card { /* 毛玻璃 */ }
.stock-header { display: flex; justify-content: space-between; }
.price-block { text-align: right; }
.current-price.up { color: var(--green); }
.current-price.down { color: var(--red); }
.metrics-grid { display: grid; grid-template-columns: repeat(4, 1fr); }
.metric { display: flex; flex-direction: column; }
```

---

## 4. 状态提示 `status-toast.js`

极简组件，只暴露 `render(root, state)`：

```js
if (state.error) {
  root.textContent = state.error;
  root.className = "status error";
} else {
  root.className = "status hidden";
}
```

错误样式：

```css
.status.error {
  background: rgba(239,68,68,.12);
  border: 1px solid var(--red);
  color: var(--red);
  padding: 12px 16px;
  border-radius: var(--radius-md);
}
```

---

## 5. K线图容器 `kline-view.js`

监听 `quoteViewModel.state.data.code`，自动加载并喂数据给 `kline-chart.js`。

### 周期选项

```js
PERIOD_OPTIONS = [
  { value: "1m",  label: "1分" },
  { value: "5m",  label: "5分" },
  { value: "15m", label: "15分" },
  { value: "30m", label: "30分" },
  { value: "60m", label: "60分" },
  { value: "1d",  label: "日K" },
  { value: "1w",  label: "周K" },
  { value: "1M",  label: "月K" },
];
```

### 复权

```js
dividendType ∈ { "none" /*不复权*/, "front" /*前复权*/, "back" /*后复权*/, "ratio" /*等比*/ }
```

切换后通过 `vm.facade.quote.history(symbol, period, -1, dividendType)` 重新拉数据。

### 数据条数

`count = -1`：无上限加载全部历史数据；展示时默认只显示最近 150 根（`dataZoomStart` 自动计算定位）。

---

## 6. K线图组件 `kline-chart.js`

基于 **ECharts** 自绘：

### 主图区

- 蜡烛（红涨绿跌，符合 A股习惯）
- MA(5/10/20/60) 四条均线
- 鼠标悬停十字光标 + 数据 tooltip

### 副图区

- 成交量柱状图（颜色与蜡烛同步：红绿）

### 交互

- 底部 `dataZoom` 滑块（内嵌 + 滑出）
- 鼠标滚轮缩放
- 拖动平移
- 双击重置视图

### 配置

```js
KlineChart.mount(containerEl, {
  bars: [],                    // OHLCV 数据
  title: "查询股票后显示K线图",
  periods: PERIOD_OPTIONS,
  period: "1d",
  dividendType: "front",
  height: 380,
  onPeriodChange: (p) => {},   // 用户切换周期
  onSettingsChange: (s) => {}, // 用户切换复权
});
```

返回的 `ctrl` 暴露：

```js
ctrl.update(bars, options);    // 更新数据 + 标题/缩放
ctrl.destroy();                // dispose 时调用（路由切换会触发）
```

---

## 7. 自选股列表 `watchlist-view.js`

侧边栏 `⭐ 自选股` 卡片，纯本地存储（`localStorage`），不依赖主进程。

### 行形态

```
┌────────────────────────────────────────────┐
│  贵州茅台                  +0.74%         │
│  sh600519          ¥1680.50      ×         │
└────────────────────────────────────────────┘
```

- 点击行 → `quoteVM.querySymbol(item.symbol)`
- 点击 × → `vm.remove(item.symbol)`（阻止冒泡）
- 5 秒自动刷新（`watchlistVM.startAutoRefresh()`）

### 空态

```
⭐
还没有自选股，查询后点击"加入自选"
```

---

## 8. 策略管理 `strategy-view.js`

### 形态

```
┌──────────────────────────────────────────────────────────┐
│  + 新建策略                                  [搜索框]     │
├──────────────────────────────────────────────────────────┤
│  ● 均线交叉策略                                            │
│    5日上穿20日买入，下穿卖出      [运行中] [编辑][删除]    │
│    最近更新：2026-07-18 14:00                             │
├──────────────────────────────────────────────────────────┤
│  ○ 海龟交易法则                                            │
│    ...                                  [已停止] [启动]   │
└──────────────────────────────────────────────────────────┘
```

### 状态徽章（CSS）

```css
.badge-running { background: rgba(34,197,94,.15); color: var(--green); animation: pulse 2s infinite; }
.badge-error   { background: rgba(239,68,68,.15); color: var(--red); }
.badge-draft   { background: rgba(154,154,168,.12); color: var(--text-3); }
.badge-active  { background: rgba(99,102,241,.12); color: var(--accent-hover); }
.badge-archived{ background: rgba(154,154,168,.08); color: var(--text-3); }
```

策略页进入时轮询（5s）`strategyVM.loadExecutorStatus()`，离开时清理。

### 源码编辑与复制

- 编辑弹窗中可展开「策略源码」区域（`<details>` 折叠），查看和修改任意策略（含内置）的 Python 源码
- 修改源码后保存，通过 `strategy.add` 覆盖原策略类型（非新建）
- 列表中「复制」按钮复制当前策略实例（新 ID、草稿状态）


---

## 9. 回测视图 `backtest-view.js`

### 表单字段

- 策略选择（下拉，从 `strategy:list` 拉）
- 标的代码（支持股票搜索联想）
- 起止日期（flatpickr）
- 初始资金
- 手续费率
- 滑点

### 提交后流程

```
[开始回测]
  └─ facade.backtest.run(config)
       └─ IPC: backtest:run
            └─ BacktestService.run
                 └─ spawn bridge/backtest_engine.py
                      └─ 返回 { ok, data: { metrics, equity, trades } }

进度条 + 轮询 / 一次性返回
渲染：收益曲线（ECharts） + 绩效指标卡片
```

### 绩效指标

| 指标 | 说明 |
|---|---|
| 总收益率 | (期末 - 期初) / 期初 |
| 年化收益 | (1 + 总收益)^(365/天数) - 1 |
| 最大回撤 | 历史峰值到谷底的最大跌幅 |
| 夏普比率 | (年化收益 - 无风险利率) / 年化波动率 |
| 胜率 | 盈利交易 / 总交易 |
| 盈亏比 | 平均盈利 / 平均亏损 |

---

## 10. 交易视图 `trade-view.js`

### Tab 结构

```
[ 下单 ] [ 持仓 ] [ 委托 ] [ 账户 ]
```

#### 下单表单

- 股票代码（带联想搜索）
- 方向（买入 / 卖出）
- 价格类型（限价 / 市价）
- 数量
- 价格
- 提示框：根据当前账户和持仓校验资金/持仓

#### 持仓列表

| 字段 | 来源 |
|---|---|
| 股票代码 | `trade.positions` |
| 持仓数量 | 同上 |
| 可用数量 | 同上 |
| 成本价 | 同上 |
| 现价 | 实时（`quote.tick` 推送） |
| 浮动盈亏 | 计算 |

#### 委托 / 账户

只读列表 + 状态过滤。

### Mock 模式

`tradeSource: mock` 时，所有操作走内存模拟（`MockTradeDataSource`），下单立即成交，适合演示。

---

## 11. 设置视图 `settings-view.js`

### 字段

| 字段 | 写入 |
|---|---|
| 行情数据源 (mock/qmt/auto) | `settings:set({ dataSource })` |
| 交易数据源 (mock/qmt) | `settings:set({ tradeSource })` |
| QMT Python 路径 | `settings:set({ qmt: { python } })` |
| QMT 桥接脚本 | `settings:set({ qmt: { bridgeScript } })` |
| 请求超时 | `settings:set({ qmt: { requestTimeoutMs } })` |
| 缓存 TTL | `settings:set({ cache: { ttlMs } })` |

保存后部分项需要重启（弹 toast 提示）。

---

## 12. 通用视觉规范

### 颜色

| 用途 | 变量 |
|---|---|
| 背景层叠 | `--bg-0` → `--bg-3`（由深至浅） |
| 文字层叠 | `--text-1` 强 / `--text-2` 中 / `--text-3` 弱 |
| 主题色 | `--accent`（靛紫 `#6366f1`） |
| 涨 / 跌 / 警告 | `--green` / `--red` / `--orange` |

### 圆角

| 尺寸 | 用途 |
|---|---|
| `--radius-sm: 8px` | 标签、徽章、小按钮 |
| `--radius-md: 12px` | 卡片、输入框、按钮 |
| `--radius-lg: 20px` | 大型容器（如主行情卡） |

### 阴影

| 级别 | 用途 |
|---|---|
| `--shadow-sm` | 浮起微动效 |
| `--shadow-md` | 卡片默认 |
| `--shadow-lg` | 弹窗、下拉、模态 |

### 字体

```
-apple-system, BlinkMacSystemFont, "SF Pro Display", "Helvetica Neue",
"PingFang SC", "Microsoft YaHei", sans-serif
```

数字一律 `font-variant-numeric: tabular-nums`，避免抖动。

### 动效

```css
--transition: all .2s cubic-bezier(.4, 0, .2, 1);

/* 列表进入 */
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

/* 状态点呼吸 */
@keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 currentColor; } 50% { box-shadow: 0 0 0 6px transparent; } }
```

---

## 13. 组件间通信约定

### ViewModel 接口（每个 VM 必须实现）

```js
class ViewModel {
  state = { /* 任意字段 */ };
  subscribe(fn) { /* 注册订阅，回调 fn(state) */ }
  // 业务方法 ...
}
```

### View 渲染约定

```js
import { SomeVM } from "../viewmodels/some-vm.js";
const vm = new SomeVM(facade);
View.render(rootEl, vm);
```

View 内部 `vm.subscribe(state => paint(state))`，不直接持有 state。

### 事件传递

- View 触发动作 → 调用 `vm.actionX(...)`
- ViewModel 改 state → 订阅者被回调 → View 重渲染
- 全局跳转：调用 `window.AppRouter.switchPage("xxx")`

---

## 14. 添加新组件的步骤

1. 在 `renderer/js/views/` 新建 `xxx-view.js`，导出 `render(root, ...args)`
2. 在 `renderer/js/viewmodels/` 新建对应的 VM（如需要）
3. 在 `index.html` 添加 `<section class="page" id="page-xxx">` 与挂载点
4. 在 `renderer/js/router.js` 的 `PAGES` 加入路由名
5. 在 `renderer/js/main.js` 用 `safe("xxx页", () => ...)` 包裹初始化
6. 在侧边栏 `index.html` 的 `.nav-list` 加导航项 + SVG 图标
7. 必要时在 `main/ipc/index.js` 注册新 IPC、在 `preload.js` 暴露 facade 方法