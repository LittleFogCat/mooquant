/**
 * mookquant · Bootstrap
 */
(function () {
  const facade = window.AppFacade;
  if (!facade) { console.error("[bootstrap] AppFacade not ready"); return; }

  // ---- 行情页 ----
  const quoteVM = new window.QuoteViewModel(facade);
  window.SearchPanelView.render(document.getElementById("searchPanel"), quoteVM);
  window.ResultCardView.render(document.getElementById("result"), quoteVM.state, "");
  window.StatusToastView.render(document.getElementById("status"), quoteVM.state);
  quoteVM.subscribe(state => {
    const mode = quoteVM.state.info?.mode || "mock";
    window.ResultCardView.render(document.getElementById("result"), state, mode);
    window.StatusToastView.render(document.getElementById("status"), state);
  });
  quoteVM.loadInfo();
  // ---- K线图 ----
  window.KlineView.render(document.getElementById("klinePanel"), quoteVM);

  // ---- 自选股 ----
  const watchlistVM = new window.WatchlistViewModel(facade);
  window.WatchlistView.render(document.getElementById("watchlistPanel"), watchlistVM, quoteVM);
  watchlistVM.startAutoRefresh();
  window._watchlistVM = watchlistVM;

  // ---- 策略页 ----
  const strategyVM = new window.StrategyViewModel(facade);
  window.StrategyView.render(document.getElementById("strategyContainer"), strategyVM);
  window.AppRouter.onEnter("strategy", () => strategyVM.loadList());

  // ---- 回测页 ----
  const backtestVM = new window.BacktestViewModel(facade);
  window.BacktestView.render(document.getElementById("backtestContainer"), backtestVM);
  window.AppRouter.onEnter("backtest", () => backtestVM.loadStrategies());

  // ---- 交易页 ----
  const tradeVM = new window.TradeViewModel(facade);
  window.TradeView.render(document.getElementById("tradeContainer"), tradeVM);
  window.AppRouter.onEnter("trade", () => tradeVM.loadAll());

  // ---- 设置页 ----
  const settingsVM = new window.SettingsViewModel(facade);
  window.SettingsView.render(document.getElementById("settingsContainer"), settingsVM);
  window.AppRouter.onEnter("settings", () => settingsVM.load());

  // ---- QMT 状态轮询 ----
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


  window.AppRouter.init();
})();