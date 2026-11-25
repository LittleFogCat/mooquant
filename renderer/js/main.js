/**
 * mookquant · Entry point (replaces bootstrap.js)
 * Imports all modules and wires up the application
 */
import '../css/base.css';
import '../css/app.css';

import { getFacade } from './services/facade.js';
import * as Router from './router.js';

import { QuoteViewModel } from './viewmodels/quote-viewmodel.js';
import { WatchlistViewModel } from './viewmodels/watchlist-viewmodel.js';
import { StrategyViewModel } from './viewmodels/strategy-viewmodel.js';
import { BacktestViewModel } from './viewmodels/backtest-viewmodel.js';
import { TradeViewModel } from './viewmodels/trade-viewmodel.js';
import { SimViewModel } from './viewmodels/sim-viewmodel.js';
import { SettingsViewModel } from './viewmodels/settings-viewmodel.js';
import { ModelViewModel } from './viewmodels/model-viewmodel.js';

import * as SearchPanelView from './views/search-panel.js';
import * as ResultCardView from './views/result-card.js';
import * as StatusToastView from './views/status-toast.js';
import * as KlineView from './views/kline-view.js';
import * as WatchlistView from './views/watchlist-view.js';
import * as StrategyView from './views/strategy-view.js';
import * as BacktestView from './views/backtest-view.js';
import * as TradeView from './views/trade-view.js';
import * as SimView from './views/sim-view.js';
import * as SettingsView from './views/settings-view.js';
import * as ModelView from './views/model-view.js';

// ---- 错误隔离包装 ----
function safe(name, fn) {
  try { fn(); } catch (e) { console.error('[main] ' + name + ' 初始化失败:', e); }
}

// ---- Initialize facade ----
const facade = getFacade();
if (!facade) { console.error("[main] Facade not ready"); }
window.AppFacade = facade;
window.AppRouter = Router;

// ---- 跨区块引用（F5 刷新需要触达各页 ViewModel）----
const pageVM = { quote: null, watchlist: null, strategy: null, backtest: null, trade: null, sim: null, settings: null, model: null };

// ---- 行情页 ----
safe('行情页', () => {
  const quoteVM = new QuoteViewModel(facade);
  pageVM.quote = quoteVM;
  SearchPanelView.render(document.getElementById("searchPanel"), quoteVM);
  ResultCardView.render(document.getElementById("result"), quoteVM.state, "");
  StatusToastView.render(document.getElementById("status"), quoteVM.state, quoteVM);
  quoteVM.subscribe(state => {
    const mode = quoteVM.state.info?.mode || "mock";
    ResultCardView.render(document.getElementById("result"), state, mode);
    StatusToastView.render(document.getElementById("status"), state, quoteVM);
  });
  quoteVM.loadInfo();
  window._quickQuery = (symbol) => quoteVM.querySymbol(symbol);

  // K线图
  safe('K线图', () => KlineView.render(document.getElementById("klinePanel"), quoteVM));

  // 自选股
  safe('自选股', () => {
    const watchlistVM = new WatchlistViewModel(facade);
    pageVM.watchlist = watchlistVM;
    WatchlistView.render(document.getElementById("watchlistPanel"), watchlistVM, quoteVM);
    watchlistVM.startAutoRefresh();
    window._watchlistVM = watchlistVM;
    if (watchlistVM.state.items && watchlistVM.state.items.length > 0) {
      quoteVM.querySymbol(watchlistVM.state.items[0].symbol);
    }
  });
});

// ---- 策略页 ----
safe('策略页', () => {
  const strategyVM = new StrategyViewModel(facade);
  pageVM.strategy = strategyVM;
  StrategyView.render(document.getElementById("strategyContainer"), strategyVM);
  Router.onEnter("strategy", () => strategyVM.loadList());

  var _executorPoll = null;
  Router.onEnter("strategy", () => {
    if (_executorPoll) clearInterval(_executorPoll);
    _executorPoll = setInterval(() => strategyVM.loadExecutorStatus(), 5000);
  });
  Router.onLeave("strategy", () => {
    if (_executorPoll) { clearInterval(_executorPoll); _executorPoll = null; }
  });
});

// ---- 回测页 ----
safe('回测页', () => {
  const backtestVM = new BacktestViewModel(facade);
  pageVM.backtest = backtestVM;
  BacktestView.render(document.getElementById("backtestContainer"), backtestVM);
  Router.onEnter("backtest", () => backtestVM.loadStrategies());
  // 订阅必须每次进入本页时重建：onLeave 会退订，只在 render() 里订阅会导致离开一次后永久失效
  Router.onEnter("backtest", () => backtestVM.subscribeProgress());
  Router.onLeave("backtest", () => backtestVM.unsubscribeProgress());
});

// ---- 交易页 ----
safe('交易页', () => {
  const tradeVM = new TradeViewModel(facade);
  pageVM.trade = tradeVM;
  TradeView.render(document.getElementById("tradeContainer"), tradeVM);
  Router.onEnter("trade", () => tradeVM.loadAll());
});

// ---- 模拟盘页（无人值守面板）----
safe('模拟页', () => {
  const simVM = new SimViewModel(facade);
  pageVM.sim = simVM;
  SimView.render(document.getElementById("simContainer"), simVM);
  // 进入：免费状态 + 一次账户快照；离开：停掉轮询，避免后台空转
  Router.onEnter("sim", () => {
    simVM.enter();
    simVM.startPolling(5000);
  });
  Router.onLeave("sim", () => simVM.stopPolling());
});

// ---- 设置页 ----
safe('设置页', () => {
  const settingsVM = new SettingsViewModel(facade);
  pageVM.settings = settingsVM;
  SettingsView.render(document.getElementById("settingsContainer"), settingsVM);
  Router.onEnter("settings", () => settingsVM.load());
});

// ---- 应用版本号 ----
safe('应用版本号', () => {
  if (!facade.app) return;
  facade.app.info().then(info => {
    const ver = document.getElementById('appVersion');
    if (ver && info.version) ver.textContent = 'v' + info.version;
  }).catch(() => {});
});

// ---- QMT 状态轮询（真实探测：connected 由主进程 ping 桥 + 数据探测得出）----
function renderQmtStatus(status) {
  const indicator = document.getElementById("qmtStatus");
  if (!indicator || !status) return;
  const dot = indicator.querySelector(".status-dot");
  const text = indicator.querySelector(".status-text");
  if (!dot || !text) return;
  if (status.reconnecting) {
    dot.className = "status-dot connecting";
    text.textContent = "重连中...";
  } else if (status.connected) {
    dot.className = "status-dot online";
    text.textContent = status.mode === "eastmoney" ? "东财行情 已连接" : "QMT 已连接";
  } else if (status.mode === "mock") {
    dot.className = "status-dot mock";
    // auto 模式回落到 mock：提示 QMT 不可用（按 F5 可重试 QMT）
    text.textContent = status.configuredMode === "auto" ? "Mock · QMT 不可用" : "Mock 模式";
  } else if (status.mode === "eastmoney") {
    dot.className = "status-dot offline";
    text.textContent = "东财行情 未连接";
  } else {
    dot.className = "status-dot offline";
    text.textContent = "QMT 未连接";
  }
  indicator.title = status.error ? "连接异常：" + status.error : "";
}

async function pollQmtStatus() {
  try {
    renderQmtStatus(await facade.quote.status());
  } catch (e) {
    const indicator = document.getElementById("qmtStatus");
    if (!indicator) return;
    const dot = indicator.querySelector(".status-dot");
    const text = indicator.querySelector(".status-text");
    if (dot) dot.className = "status-dot offline";
    if (text) text.textContent = "状态未知";
  }
}
pollQmtStatus();
setInterval(pollQmtStatus, 5000);

// ---- 模型页 ----
safe('模型页', () => {
  const modelVM = new ModelViewModel(facade);
  pageVM.model = modelVM;
  ModelView.render(document.getElementById("modelContainer"), modelVM);
  Router.onEnter("models", () => modelVM.loadAll());
  Router.onLeave("models", () => modelVM.stopPolling());
});

// ---- 路由初始化（始终执行，不受上面错误影响）----
// ---- 侧边栏折叠 + 标题栏同步 ----
safe('侧边栏折叠', () => {
  const sidebar = document.getElementById('navBar');
  const contentArea = document.querySelector('.content-area');
  const titlebar = document.getElementById('titlebar');
  const toggle = document.getElementById('sidebarToggle');
  if (!sidebar || !toggle || !contentArea || !titlebar) return;

  const COLLAPSE_KEY = 'mookquant.sidebar.collapsed';

  function applyCollapsed(collapsed) {
    sidebar.classList.toggle('collapsed', collapsed);
    contentArea.classList.toggle('sidebar-collapsed', collapsed);
    titlebar.classList.toggle('collapsed', collapsed);
    toggle.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
  }

  const saved = localStorage.getItem(COLLAPSE_KEY) === 'true';
  applyCollapsed(saved);

  toggle.addEventListener('click', () => {
    const collapsed = !sidebar.classList.contains('collapsed');
    applyCollapsed(collapsed);
    localStorage.setItem(COLLAPSE_KEY, String(collapsed));
  });
});

// ---- 窗口控制按钮 ----
safe('窗口控制', () => {
  const isElectron = !!(window.mookquant && window.mookquant.isElectron);
  const controls = document.getElementById('titlebarControls');

  // 非 Electron 环境（纯浏览器）隐藏窗口按钮
  if (!isElectron || !facade.window) {
    if (controls) controls.style.display = 'none';
    return;
  }

  const btnMin = document.getElementById('winMinimize');
  const btnMax = document.getElementById('winMaximize');
  const btnClose = document.getElementById('winClose');

  if (btnMin) btnMin.addEventListener('click', () => facade.window.minimize());
  if (btnClose) btnClose.addEventListener('click', () => facade.window.close());

  if (btnMax) {
    const iconMax = btnMax.querySelector('.icon-maximize');
    const iconRestore = btnMax.querySelector('.icon-restore');

    function updateMaxIcon(isMax) {
      if (!iconMax || !iconRestore) return;
      iconMax.style.display = isMax ? 'none' : '';
      iconRestore.style.display = isMax ? '' : 'none';
      btnMax.title = isMax ? '还原' : '最大化';
    }

    btnMax.addEventListener('click', async () => {
      const isMax = await facade.window.maximize();
      updateMaxIcon(isMax);
    });

    // 初始化最大化状态
    facade.window.isMaximized().then(updateMaxIcon);

    // 监听最大化/还原事件
    facade.window.onMaximizeChange(updateMaxIcon);
  }
});

// ---- 主题初始化（从 localStorage 恢复）----
safe('主题初始化', () => {
  const THEME_KEY = 'mookquant.theme';
  const saved = localStorage.getItem(THEME_KEY) || 'dark';
  document.documentElement.setAttribute('data-theme', saved);
});

// ---- 设置快捷入口 ----
safe('设置快捷入口', () => {
  const btn = document.getElementById('settingsBtn');
  if (!btn) return;
  btn.addEventListener('click', () => Router.switchPage('settings'));
});

// ---- F5 / Ctrl+R 全局刷新（重连数据源 + 重拉当前页数据）----
safe('F5刷新', () => {
  async function refreshApp() {
    const text = document.querySelector("#qmtStatus .status-text");
    if (text) text.textContent = "正在刷新...";
    // 1. 重连/校验数据源（断线时重建链路；auto 回落 mock 时尝试切回 QMT）
    try {
      renderQmtStatus(await facade.quote.reconnect());
    } catch (e) {
      console.warn("[main] 重连失败:", e);
      pollQmtStatus();
    }
    // 2. 按当前页面重拉数据
    const page = Router.currentPage();
    try {
      if (page === "quote") {
        if (pageVM.quote) {
          pageVM.quote.loadInfo();
          if (pageVM.quote.state.symbol) pageVM.quote.querySymbol(pageVM.quote.state.symbol);
        }
        if (pageVM.watchlist) pageVM.watchlist.refresh();
      } else if (page === "strategy" && pageVM.strategy) {
        pageVM.strategy.loadList();
        pageVM.strategy.loadExecutorStatus();
      } else if (page === "backtest" && pageVM.backtest) {
        pageVM.backtest.loadStrategies();
      } else if (page === "trade" && pageVM.trade) {
        pageVM.trade.loadAll();
      } else if (page === "models" && pageVM.model) {
        pageVM.model.loadAll();
      } else if (page === "settings" && pageVM.settings) {
        pageVM.settings.load();
      }
    } catch (e) {
      console.error("[main] 刷新页面数据失败:", e);
    }
  }
  window.addEventListener("keydown", (e) => {
    if (e.key === "F5" || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r")) {
      e.preventDefault();
      refreshApp();
    }
  });
});

Router.init();
console.log("[main] 应用初始化完成");
