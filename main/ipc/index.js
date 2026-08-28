/**
 * mookquant · IPC 路由
 */
function registerIpc({ quoteService, strategyService, backtestService, tradeService, configManager, executorService, strategyBridge, modelService, getMainWindow }) {
  const { ipcMain } = require("electron");
  // ---- 行情 ----
  ipcMain.handle("quote:query", async (_e, s) => quoteService.query(s));
  ipcMain.handle("quote:info", async () => ({ mode: quoteService.source.mode, description: quoteService.source.description }));
  ipcMain.handle("quote:history", async (_e, s, p, c, dt) => quoteService.getHistory(s, p, c, dt));
    ipcMain.handle("quote:search", async (_e, q) => quoteService.search(q));
  ipcMain.handle("quote:syncStocks", async () => quoteService.syncStocks());
  ipcMain.handle("quote:stockList", async () => quoteService.getStockList());
  ipcMain.handle("quote:status", async () => quoteService.getStatus());
  // ---- ???? ----
  ipcMain.handle("window:minimize", async () => { const w = getMainWindow(); if (w) w.minimize(); });
  ipcMain.handle("window:maximize", async () => {
    const w = getMainWindow(); if (!w) return false;
    // maximize/unmaximize 为异步原生操作，立即 isMaximized() 可能读到旧值，
    // 因此先计算目标态并直接返回，避免渲染层图标状态错乱。
    const willMaximize = !w.isMaximized();
    if (willMaximize) w.maximize(); else w.unmaximize();
    return willMaximize;
  });
  ipcMain.handle("window:close", async () => { const w = getMainWindow(); if (w) w.close(); });
  ipcMain.handle("window:isMaximized", async () => { const w = getMainWindow(); return w ? w.isMaximized() : false; });

  // ---- App ----
  ipcMain.handle("app:info", async () => {
    const { app } = require("electron");
    let version = app.getVersion();
    try { version = require("../../config").version || version; } catch (e) {}
    return { name: app.getName(), version, platform: process.platform };
  });
  ipcMain.handle("app:restart", async () => { const { app } = require("electron"); app.relaunch(); app.exit(0); });
  // ---- 策略 ----
  if (strategyService) {
    ipcMain.handle("strategy:list", async () => { try { return { ok: true, data: await strategyService.list() }; } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("strategy:types", async () => {
      try {
        if (strategyBridge && typeof strategyBridge.strategyList === "function") {
          const r = await strategyBridge.strategyList();
          return { ok: true, data: (r && r.strategies) || [] };
        }
        return { ok: true, data: [] };
      } catch (e) { return { ok: false, error: e.message }; }
    });
    ipcMain.handle("strategy:getCode", async (_e, name) => {
      try {
        if (strategyBridge && typeof strategyBridge.strategyGetCode === "function") {
          const r = await strategyBridge.strategyGetCode({ name });
          if (r && r.error) return { ok: false, error: r.error };
          return { ok: true, data: r };
        }
        return { ok: false, error: "?? bridge ???" };
      } catch (e) { return { ok: false, error: e.message }; }
    });
    ipcMain.handle("strategy:addType", async (_e, payload) => {
      try {
        if (strategyBridge && typeof strategyBridge.strategyAdd === "function") {
          const r = await strategyBridge.strategyAdd(payload || {});
          if (r && r.error) return { ok: false, error: r.error };
          return { ok: true, data: (r && r.strategy) || r };
        }
        return { ok: false, error: "策略 bridge 不可用" };
      } catch (e) { return { ok: false, error: e.message }; }
    });
    ipcMain.handle("strategy:deleteType", async (_e, payload) => {
      try {
        if (strategyBridge && typeof strategyBridge.strategyDelete === "function") {
          const r = await strategyBridge.strategyDelete(payload || {});
          if (r && r.error) return { ok: false, error: r.error };
          return { ok: true, data: r || { ok: true } };
        }
        return { ok: false, error: "策略 bridge 不可用" };
      } catch (e) { return { ok: false, error: e.message }; }
    });
    ipcMain.handle("strategy:export", async (_e, payload) => {
      try {
        if (strategyBridge && typeof strategyBridge.strategyExport === "function") {
          return { ok: true, data: await strategyBridge.strategyExport(payload || {}) };
        }
        return { ok: false, error: "策略 bridge 不可用" };
      } catch (e) { return { ok: false, error: e.message }; }
    });
    ipcMain.handle("strategy:get", async (_e, id) => { try { return { ok: true, data: await strategyService.getById(id) }; } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("strategy:create", async (_e, p) => { try { return { ok: true, data: await strategyService.create(p) }; } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("strategy:update", async (_e, id, p) => { try { return { ok: true, data: await strategyService.update(id, p) }; } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("strategy:delete", async (_e, id) => { try { await strategyService.remove(id); return { ok: true, data: { id } }; } catch (e) { return { ok: false, error: e.message }; } });
  }
  // ---- 回测 ----
  if (backtestService) {
    ipcMain.handle("backtest:run", async (_e, c) => { try { return await backtestService.run(c); } catch (e) { return { ok: false, error: e.message }; } });
    // D4.3 回测历史管理
    ipcMain.handle("backtest:list", async () => { try { return await backtestService.listResults(); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("backtest:compare", async (_e, ids) => { try { return await backtestService.compareResults(ids); } catch (e) { return { ok: false, error: e.message }; } });
    // 回测进度推送：引擎 → 服务 → 渲染进程（backtest:progress 事件）
    backtestService.onProgress((p) => {
      const w = getMainWindow();
      if (w && !w.isDestroyed()) w.webContents.send("backtest:progress", p);
    });
  }
  // ---- 交易 ----
  if (tradeService) {
    ipcMain.handle("trade:info", async () => ({ ok: true, data: tradeService.info }));
    ipcMain.handle("trade:placeOrder", async (_e, o) => tradeService.placeOrder(o));
    ipcMain.handle("trade:cancelOrder", async (_e, id) => tradeService.cancelOrder(id));
    ipcMain.handle("trade:positions", async () => tradeService.getPositions());
    ipcMain.handle("trade:orders", async () => tradeService.getOrders());
    ipcMain.handle("trade:account", async () => tradeService.getAccount());
  }
  // ---- 策略执行 ----
  if (executorService) {
    ipcMain.handle("executor:start", async (_e, id, symbols) => executorService.start(id, symbols));
    ipcMain.handle("executor:stop", async (_e, id) => executorService.stop(id));
    ipcMain.handle("executor:status", async (_e, id) => executorService.getStatus(id));
    ipcMain.handle("executor:list", async () => executorService.listRunning());
  }
  // ---- 日志查询 ----
  {
    const { LogService } = require("../services/log-service");
    const logService = new LogService();
    ipcMain.handle("logs:trades", async (_e, date) => logService.readTrades(date));
    ipcMain.handle("logs:strategy", async (_e, id, date) => logService.readStrategyLogs(id, date));
    ipcMain.handle("logs:equity", async (_e, id) => logService.readEquity(id));
  }
  // ---- 设置 ----
  if (configManager) {
    ipcMain.handle("settings:get", async () => configManager.get());
    ipcMain.handle("settings:set", async (_e, patch) => configManager.set(patch));
  }
  // ---- 模型服务 ----
  if (modelService) {
    ipcMain.handle("model:status", async () => modelService.status());
    ipcMain.handle("model:strategies", async () => { try { return await modelService.listStrategies(); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:strategy", async (_e, name) => { try { return await modelService.getStrategy(name); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:addStrategy", async (_e, payload) => { try { return await modelService.addStrategy(payload); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:deleteStrategy", async (_e, name) => { try { return await modelService.deleteStrategy(name); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:models", async () => { try { return await modelService.listModels(); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:getModel", async (_e, id) => { try { return await modelService.getModel(id); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:deleteModel", async (_e, id) => { try { return await modelService.deleteModel(id); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:train", async (_e, config) => { try { return await modelService.startTraining(config); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:trainStatus", async (_e, taskId) => { try { return await modelService.getTrainingStatus(taskId); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:signal", async (_e, payload) => { try { return await modelService.computeSignal(payload); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:updateModel", async (_e, id, patch) => { try { return await modelService.updateModel(id, patch); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:activate", async (_e, id, force) => { try { return await modelService.activateModel(id, !!force); } catch (e) { return { ok: false, error: e.message }; } });
    ipcMain.handle("model:active", async () => { try { return await modelService.getActiveModel(); } catch (e) { return { ok: false, error: e.message }; } });
  }
}
module.exports = { registerIpc };