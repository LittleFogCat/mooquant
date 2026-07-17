/**
 * mookquant · Electron 主进程入口
 */
// 加载 .env 文件（手动解析，无需 dotenv 依赖）
try {
  const fs = require("fs");
  const envPath = require("path").join(__dirname, ".env");
  const envContent = fs.readFileSync(envPath, "utf-8");
  for (const line of envContent.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx === -1) continue;
    const key = trimmed.slice(0, idx).trim();
    const val = trimmed.slice(idx + 1).trim();
    if (!process.env[key]) process.env[key] = val;
  }
} catch (e) {
  // .env 不存在时静默忽略
}

const path = require("path");
const fs2 = require("fs");
const { app, BrowserWindow, Menu, shell, session } = require("electron");

const { registerIpc } = require("./main/ipc");
const { QuoteService } = require("./main/services/quote-service");
const { StrategyService } = require("./main/services/strategy-service");
const { BacktestService } = require("./main/services/backtest-service");
const { TradeService } = require("./main/services/trade-service");
const { ConfigManager } = require("./main/services/config-manager");
const { ExecutorService } = require("./main/services/executor-service");
const { createDataSource } = require("./main/datasources");
const { createTradeDataSource } = require("./main/datasources/trade");

let mainWindow = null;
let quoteService = null, strategyService = null, backtestService = null, tradeService = null;
let configManager = null;
let executorService = null;

function installCspHeader() {
  const csp = [
    "default-src 'self' file: data: blob:",
    "script-src 'self' file: 'unsafe-inline'",
    "style-src 'self' file: 'unsafe-inline'",
    "img-src 'self' file: data:",
    "connect-src 'self' file: data: blob: ws: http://localhost:* http://127.0.0.1:*",
  ].join("; ");
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    const headers = details.responseHeaders || {};
    for (const k of Object.keys(headers)) { if (k.toLowerCase() === "content-security-policy") delete headers[k]; }
    headers["Content-Security-Policy"] = [csp];
    cb({ responseHeaders: headers });
  });
}

function createWindow(config) {
  const win = config.window || {};
  mainWindow = new BrowserWindow({
    width: win.width || 1100, height: win.height || 760,
    minWidth: 860, minHeight: 600, show: false,
    backgroundColor: "#0a0a0f",
    titleBarStyle: process.platform === "darwin" ? "hiddenInset" : "default",
    trafficLightPosition: process.platform === "darwin" ? { x: 16, y: 18 } : undefined,
    title: "mookquant",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: false, webSecurity: true },
  });
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  mainWindow.on("closed", () => { mainWindow = null; });
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ label: app.getName(), submenu: [{ role: "about" }, { type: "separator" }, { role: "hide" }, { role: "hideOthers" }, { role: "unhide" }, { type: "separator" }, { role: "quit" }] }] : []),
    { label: "编辑", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }] },
    { label: "视图", submenu: [{ role: "reload" }, { role: "forceReload" }, { role: "toggleDevTools" }, { type: "separator" }, { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" }] },
    { label: "窗口", submenu: [{ role: "minimize" }, { role: "close" }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-software-rasterizer');
app.commandLine.appendSwitch('no-sandbox');
app.whenReady().then(async () => {
  installCspHeader();
  configManager = new ConfigManager();
  const config = configManager.get();

  const dataSource = await createDataSource({ mode: configManager.dataSource, qmt: configManager.qmt });

  // 行情推送：转发到渲染进程
  if (dataSource && typeof dataSource.onPush === "function") {
    dataSource.onPush("tick", (data) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("quote:tick", data);
      }
    });
  }
  quoteService = new QuoteService({ source: dataSource, cacheTtlMs: config.cache?.ttlMs ?? 5000 });
  strategyService = new StrategyService();
  backtestService = new BacktestService({ strategyService, config: configManager.qmt });
  const tradeSource = await createTradeDataSource({ mode: configManager.tradeSource, qmt: configManager.qmt });
  tradeService = new TradeService({ source: tradeSource });

  executorService = new ExecutorService({ strategyService, quoteService, tradeService });
  executorService.restoreRunning();

  registerIpc({ quoteService, strategyService, backtestService, tradeService, configManager, executorService });
  buildMenu();
  createWindow(config);
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(config); });
});

app.on("window-all-closed", () => {
  if (quoteService) quoteService.dispose();
  if (backtestService) backtestService.dispose();
  if (tradeService) tradeService.dispose();
  if (executorService) executorService.dispose();
  if (process.platform !== "darwin") app.quit();
});