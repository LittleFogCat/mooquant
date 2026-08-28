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

// ---- 行情页 ----
safe('行情页', () => {
  const quoteVM = new QuoteViewModel(facade);
  SearchPanelView.render(document.getElementById("searchPanel"), quoteVM);
  ResultCardView.render(document.getElementById("result"), quoteVM.state, "");
  StatusToastView.render(document.getElementById("status"), quoteVM.state);
  quoteVM.subscribe(state => {
    const mode = quoteVM.state.info?.mode || "mock";
    ResultCardView.render(document.getElementById("result"), state, mode);
    StatusToastView.render(document.getElementById("status"), state);
  });
  quoteVM.loadInfo();
  window._quickQuery = (symbol) => quoteVM.querySymbol(symbol);

  // K线图
  safe('K线图', () => KlineView.render(document.getElementById("klinePanel"), quoteVM));

  // 自选股
  safe('自选股', () => {
    const watchlistVM = new WatchlistViewModel(facade);
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
  BacktestView.render(document.getElementById("backtestContainer"), backtestVM);
  Router.onEnter("backtest", () => backtestVM.loadStrategies());
  Router.onLeave("backtest", () => backtestVM.unsubscribeProgress());
});

// ---- 交易页 ----
safe('交易页', () => {
  const tradeVM = new TradeViewModel(facade);
  TradeView.render(document.getElementById("tradeContainer"), tradeVM);
  Router.onEnter("trade", () => tradeVM.loadAll());
});

// ---- 设置页 ----
safe('设置页', () => {
  const settingsVM = new SettingsViewModel(facade);
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

// ---- QMT 状态轮询 ----
safe('QMT状态', () => {
  async function pollQmtStatus() {
    const indicator = document.getElementById("qmtStatus");
    if (!indicator) return;
    try {
      const status = await facade.quote.status();
      const dot = indicator.querySelector(".status-dot");
      const text = indicator.querySelector(".status-text");
      if (status.connected) {
        dot.className = "status-dot online";
        text.textContent = "QMT 已连接";
      } else if (status.mode === "mock") {
        dot.className = "status-dot mock";
        text.textContent = "Mock 模式";
      } else {
        dot.className = "status-dot offline";
        text.textContent = "QMT 未连接";
      }
    } catch (e) {
      const dot = indicator.querySelector(".status-dot");
      const text = indicator.querySelector(".status-text");
      if (dot) dot.className = "status-dot offline";
      if (text) text.textContent = "状态未知";
    }
  }
  pollQmtStatus();
  setInterval(pollQmtStatus, 5000);
});

// ---- 模型页 ----
safe('模型页', () => {
  const modelVM = new ModelViewModel(facade);
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

Router.init();
console.log("[main] 应用初始化完成");
