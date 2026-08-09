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
const { ModelService } = require("./main/services/model-service");
const { createDataSource } = require("./main/datasources");
const { createTradeDataSource } = require("./main/datasources/trade");

let mainWindow = null;
let quoteService = null, strategyService = null, backtestService = null, tradeService = null;
let configManager = null;
let executorService = null;
let modelService = null;

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

function createSplashWindow() {
  const splash = new BrowserWindow({
    width: 360, height: 200,
    frame: false, resizable: false, movable: true,
    transparent: false,
    backgroundColor: "#ffffff",
    show: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  splash.loadFile(path.join(__dirname, "renderer", "splash.html"));
  return splash;
}

function updateSplash(splash, step, progress) {
  try {
    splash.webContents.executeJavaScript(
      "document.getElementById('step').textContent=" + JSON.stringify(step) + ";" +
      "document.getElementById('fill').style.width=" + JSON.stringify(progress + "%") + ";"
    );
  } catch {}
}

function createWindow(config) {
  const win = config.window || {};
  mainWindow = new BrowserWindow({
    width: win.width || 1100, height: win.height || 760,
    minWidth: 860, minHeight: 600, show: false,
    backgroundColor: "#ffffff",
    titleBarStyle: "hidden",
    titleBarOverlay: process.platform === "win32" ? { color: "rgba(0,0,0,0)", symbolColor: "#0d0d0d", height: 40 } : undefined,
    trafficLightPosition: process.platform === "darwin" ? { x: 16, y: 18 } : undefined,
    trafficLightPosition: process.platform === "darwin" ? { x: 16, y: 18 } : undefined,
    title: "mookquant",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: false, webSecurity: true },
  });
  mainWindow.loadFile(path.join(__dirname, "dist", "renderer", "index.html"));
  mainWindow.once("ready-to-show", () => { mainWindow.maximize(); mainWindow.show(); });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  mainWindow.on("closed", () => { mainWindow = null; });
}

function buildMenu() {
  // Hide menu bar (ChatGPT style: no menu bar)
  Menu.setApplicationMenu(null);
}

app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-gpu');
app.commandLine.appendSwitch('disable-software-rasterizer');
app.commandLine.appendSwitch('no-sandbox');
app.whenReady().then(async () => {
  installCspHeader();
  configManager = new ConfigManager();
  const config = configManager.get();
  strategyService = new StrategyService();

  // 显示 splash 启动窗口
  const splash = createSplashWindow();
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(config); });

  // 后台初始化
  updateSplash(splash, "初始化行情数据源...", 20);
  console.log("[main] 初始化数据源...");

  const dataSource = await createDataSource({ mode: configManager.dataSource, qmt: configManager.qmt });

  if (dataSource && typeof dataSource.onPush === "function") {
    dataSource.onPush("tick", (data) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send("quote:tick", data);
      }
    });
  }
  // 策略 bridge：优先复用行情数据源；mock 模式下独立创建（策略 RPC 不依赖 miniQMT）
  let strategyBridge = dataSource;
  if (!dataSource || dataSource.mode !== "qmt" || typeof dataSource.strategyList !== "function") {
    try {
      const { QmtDataSource } = require("./main/datasources/qmt");
      strategyBridge = new QmtDataSource(config.qmt || {});
      await strategyBridge.init();
      console.log("[main] 策略 bridge 已启动（独立模式）");
    } catch (e) {
      console.warn("[main] 策略 bridge 启动失败，策略功能将不可用:", e.message);
      strategyBridge = null;
    }
  }

  quoteService = new QuoteService({ source: dataSource, cacheTtlMs: config.cache?.ttlMs ?? 5000 });

  // 预加载股票列表 + 后台同步（不阻塞启动）
  quoteService._ensureStockList().then(async () => {
    const cnt = quoteService._stockList ? quoteService._stockList.length : 0;
    console.log("[main] 股票列表已加载:", cnt);
    // QMT 模式下，若列表过少（DB 为空），触发后台全量同步
    if (dataSource.mode === "qmt" && cnt < 100) {
      console.log("[main] 开始后台同步股票列表...");
      const r = await quoteService.syncStocks();
      if (r.ok) console.log("[main] 股票列表同步完成:", r.data ? (r.data.total || r.data.count) : 0, "只");
      else console.warn("[main] 股票列表同步失败:", r.error);
    }
  }).catch(e => console.warn("[main] 股票列表加载失败:", e.message));

  updateSplash(splash, "初始化交易数据源...", 50);
  backtestService = new BacktestService({ strategyService, config: configManager.qmt });
  const tradeSource = await createTradeDataSource({ mode: configManager.tradeSource, qmt: configManager.qmt });
  tradeService = new TradeService({ source: tradeSource });

  updateSplash(splash, "恢复策略执行...", 75);
  executorService = new ExecutorService({ strategyService, quoteService, tradeService, strategyBridge, modelService });
  executorService.restoreRunning();

  // 启动模型服务（HTTP 子进程）
  updateSplash(splash, "启动模型服务...", 85);
  modelService = new ModelService({ port: (config.modelServer || {}).port || 8765 });
  try {
    await modelService.init();
    console.log("[main] 模型服务已启动 port=" + modelService.port);
  } catch (e) {
    console.warn("[main] 模型服务启动失败:", e.message);
    modelService = null;
  }

  registerIpc({ quoteService, strategyService, backtestService, tradeService, configManager, executorService, strategyBridge, modelService });

  updateSplash(splash, "启动完成", 100);
  console.log("[main] 数据源初始化完成，IPC 已注册");

  // 关闭 splash，显示主窗口
  buildMenu();
  createWindow(config);
  mainWindow.once("ready-to-show", () => {
    setTimeout(() => { splash.close(); }, 300);
  });
});

app.on("window-all-closed", () => {
  if (quoteService) quoteService.dispose();
  if (backtestService) backtestService.dispose();
  if (tradeService) tradeService.dispose();
  if (executorService) executorService.dispose();
  if (modelService) modelService.dispose();
  if (process.platform !== "darwin") app.quit();
});