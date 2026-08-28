/**
 * mookquant · Preload
 */
const { contextBridge, ipcRenderer } = require("electron");
const facade = {
  quote: {
    query: (s) => ipcRenderer.invoke("quote:query", s),
    info: () => ipcRenderer.invoke("quote:info"),
    history: (s, p, c, dt) => ipcRenderer.invoke("quote:history", s, p, c, dt),
    search: (q) => ipcRenderer.invoke("quote:search", q),
    syncStocks: () => ipcRenderer.invoke("quote:syncStocks"),
    stockList: () => ipcRenderer.invoke("quote:stockList"),
    status: () => ipcRenderer.invoke("quote:status"),
    onTick: (callback) => {
      const handler = (_e, data) => callback(data);
      ipcRenderer.on("quote:tick", handler);
      return () => ipcRenderer.removeListener("quote:tick", handler);
    },
  },
  strategy: {
    list: () => ipcRenderer.invoke("strategy:list"),
    types: () => ipcRenderer.invoke("strategy:types"),
    export: (payload) => ipcRenderer.invoke("strategy:export", payload),
    get: (id) => ipcRenderer.invoke("strategy:get", id),
    create: (p) => ipcRenderer.invoke("strategy:create", p),
    update: (id, p) => ipcRenderer.invoke("strategy:update", id, p),
    delete: (id) => ipcRenderer.invoke("strategy:delete", id),
    getCode: (name) => ipcRenderer.invoke("strategy:getCode", name),
    addType: (payload) => ipcRenderer.invoke("strategy:addType", payload),
    deleteType: (payload) => ipcRenderer.invoke("strategy:deleteType", payload),
  },
  backtest: {
    run: (c) => ipcRenderer.invoke("backtest:run", c),
    // D4.3 回测历史管理
    list: (limit) => ipcRenderer.invoke("backtest:list", limit),
    compare: (ids) => ipcRenderer.invoke("backtest:compare", ids),
    onProgress: (callback) => {
      const handler = (_e, data) => callback(data);
      ipcRenderer.on("backtest:progress", handler);
      return () => ipcRenderer.removeListener("backtest:progress", handler);
    },
  },
  executor: {
    start: (id, symbols) => ipcRenderer.invoke("executor:start", id, symbols),
    stop: (id) => ipcRenderer.invoke("executor:stop", id),
    status: (id) => ipcRenderer.invoke("executor:status", id),
    list: () => ipcRenderer.invoke("executor:list"),
  },
  trade: {
    info: () => ipcRenderer.invoke("trade:info"),
    placeOrder: (o) => ipcRenderer.invoke("trade:placeOrder", o),
    cancelOrder: (id) => ipcRenderer.invoke("trade:cancelOrder", id),
    getPositions: () => ipcRenderer.invoke("trade:positions"),
    getOrders: () => ipcRenderer.invoke("trade:orders"),
    getAccount: () => ipcRenderer.invoke("trade:account"),
  },
  logs: {
    trades: (date) => ipcRenderer.invoke("logs:trades", date),
    strategy: (id, date) => ipcRenderer.invoke("logs:strategy", id, date),
    equity: (id) => ipcRenderer.invoke("logs:equity", id),
  },
  settings: {
    get: () => ipcRenderer.invoke("settings:get"),
    set: (patch) => ipcRenderer.invoke("settings:set", patch),
  },
  modelServer: {
    status: () => ipcRenderer.invoke("model:status"),
    listStrategies: () => ipcRenderer.invoke("model:strategies"),
    getStrategy: (name) => ipcRenderer.invoke("model:strategy", name),
    addStrategy: (payload) => ipcRenderer.invoke("model:addStrategy", payload),
    deleteStrategy: (name) => ipcRenderer.invoke("model:deleteStrategy", name),
    listModels: () => ipcRenderer.invoke("model:models"),
    getModel: (id) => ipcRenderer.invoke("model:getModel", id),
    deleteModel: (id) => ipcRenderer.invoke("model:deleteModel", id),
    startTraining: (config) => ipcRenderer.invoke("model:train", config),
    getTrainingStatus: (taskId) => ipcRenderer.invoke("model:trainStatus", taskId),
    computeSignal: (payload) => ipcRenderer.invoke("model:signal", payload),
    updateModel: (id, patch) => ipcRenderer.invoke("model:updateModel", id, patch),
    activateModel: (id, force) => ipcRenderer.invoke("model:activate", id, force),
    getActiveModel: () => ipcRenderer.invoke("model:active"),
  },
  window: {
    minimize: () => ipcRenderer.invoke("window:minimize"),
    maximize: () => ipcRenderer.invoke("window:maximize"),
    close: () => ipcRenderer.invoke("window:close"),
    isMaximized: () => ipcRenderer.invoke("window:isMaximized"),
    onMaximizeChange: (callback) => {
      const handler = (_e, isMaximized) => callback(isMaximized);
      ipcRenderer.on("window:maximizeChanged", handler);
      return () => ipcRenderer.removeListener("window:maximizeChanged", handler);
    },
  },
  app: { info: () => ipcRenderer.invoke("app:info"), restart: () => ipcRenderer.invoke("app:restart") },
};
contextBridge.exposeInMainWorld("mookquant", { facade, isElectron: true, platform: process.platform });