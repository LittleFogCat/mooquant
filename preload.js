/**
 * mookquant · Preload
 */
const { contextBridge, ipcRenderer } = require("electron");
const facade = {
  quote: {
    query: (s) => ipcRenderer.invoke("quote:query", s),
    info: () => ipcRenderer.invoke("quote:info"),
    history: (s, p, c) => ipcRenderer.invoke("quote:history", s, p, c),
    search: (q) => ipcRenderer.invoke("quote:search", q),
    status: () => ipcRenderer.invoke("quote:status"),
  },
  strategy: {
    list: () => ipcRenderer.invoke("strategy:list"),
    get: (id) => ipcRenderer.invoke("strategy:get", id),
    create: (p) => ipcRenderer.invoke("strategy:create", p),
    update: (id, p) => ipcRenderer.invoke("strategy:update", id, p),
    delete: (id) => ipcRenderer.invoke("strategy:delete", id),
  },
  backtest: { run: (c) => ipcRenderer.invoke("backtest:run", c) },
  trade: {
    info: () => ipcRenderer.invoke("trade:info"),
    placeOrder: (o) => ipcRenderer.invoke("trade:placeOrder", o),
    cancelOrder: (id) => ipcRenderer.invoke("trade:cancelOrder", id),
    getPositions: () => ipcRenderer.invoke("trade:positions"),
    getOrders: () => ipcRenderer.invoke("trade:orders"),
    getAccount: () => ipcRenderer.invoke("trade:account"),
  },
  settings: {
    get: () => ipcRenderer.invoke("settings:get"),
    set: (patch) => ipcRenderer.invoke("settings:set", patch),
  },
  app: { info: () => ipcRenderer.invoke("app:info") },
};
contextBridge.exposeInMainWorld("mookquant", { facade, isElectron: true });