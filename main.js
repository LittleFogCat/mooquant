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

// ---- 日志落盘：console 输出镜像到 data/logs/main.log（带时间戳）----
// 排查启动卡死类问题依赖现场，console 关掉就没了，必须落盘
(function setupFileLogging() {
  try {
    const logDir = path.join(__dirname, "data", "logs");
    fs2.mkdirSync(logDir, { recursive: true });
    const logFile = path.join(logDir, "main.log");
    const stream = fs2.createWriteStream(logFile, { flags: "a" });
    const stamp = () => new Date().toISOString();
    const origLog = console.log, origWarn = console.warn, origErr = console.error;
    console.log = (...a) => { origLog(...a); try { stream.write(`[${stamp()}] INFO  ` + a.join(" ") + "\n"); } catch (e) {} };
    console.warn = (...a) => { origWarn(...a); try { stream.write(`[${stamp()}] WARN  ` + a.join(" ") + "\n"); } catch (e) {} };
    console.error = (...a) => { origErr(...a); try { stream.write(`[${stamp()}] ERROR ` + a.join(" ") + "\n"); } catch (e) {} };
    // 主进程未捕获异常也落盘
    process.on("uncaughtException", (e) => { origErr("[uncaught]", e); try { stream.write(`[${stamp()}] FATAL uncaughtException ${e && e.stack || e}\n`); } catch (_) {} });
    console.log(`[main] ===== 日志已落盘 ${logFile}，app v${require("./config").version} =====`);
  } catch (e) {
    // 日志初始化失败不阻塞启动
  }
})();

const { registerIpc } = require("./main/ipc");
const { QuoteService } = require("./main/services/quote-service");
const { StrategyService } = require("./main/services/strategy-service");
const { BacktestService } = require("./main/services/backtest-service");
const { TradeService } = require("./main/services/trade-service");
const { ConfigManager } = require("./main/services/config-manager");
const { ExecutorService } = require("./main/services/executor-service");
const { TradingCalendar } = require("./main/services/trading-calendar");
const { TradingScheduler } = require("./main/services/trading-scheduler");
const { ReportService } = require("./main/services/report-service");
const { ModelService } = require("./main/services/model-service");
const { createDataSource } = require("./main/datasources");
const { createTradeDataSource } = require("./main/datasources/trade");
const { LocalHttpServer } = require("./main/server/local-http-server");
const { DiscoveryBeacon } = require("./main/server/discovery-beacon");

let mainWindow = null;
let quoteService = null, strategyService = null, backtestService = null, tradeService = null;
let configManager = null;
let executorService = null;
let tradingCalendar = null;
let tradingScheduler = null;
let reportService = null;
let modelService = null;
let localServer = null;
let discoveryBeacon = null;

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
    backgroundColor: "#212121",
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
    title: "mookquant",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: false, webSecurity: true },
  });
  mainWindow.loadFile(path.join(__dirname, "dist", "renderer", "index.html"));
  mainWindow.once("ready-to-show", () => {
    mainWindow.maximize();
    mainWindow.show();
    // 启动即最大化时，确保渲染层窗口控制按钮拿到正确初始状态（避免事件订阅前错过）
    setTimeout(() => {
      if (!mainWindow.isDestroyed()) {
        mainWindow.webContents.send("window:maximizeChanged", mainWindow.isMaximized());
      }
    }, 150);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
  mainWindow.on("closed", () => { mainWindow = null; });

  // ?????/?????????
  mainWindow.on("maximize", () => { if (!mainWindow.isDestroyed()) mainWindow.webContents.send("window:maximizeChanged", true); });
  mainWindow.on("unmaximize", () => { if (!mainWindow.isDestroyed()) mainWindow.webContents.send("window:maximizeChanged", false); });
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

  // 后台初始化（每步打点耗时，卡住时日志可定位到具体节点）
  const t = async (label, fn) => {
    const start = Date.now();
    console.log(`[main] >>> ${label} 开始`);
    try {
      const r = await fn();
      console.log(`[main] <<< ${label} 完成，耗时 ${Date.now() - start}ms`);
      return r;
    } catch (e) {
      console.warn(`[main] <<< ${label} 失败（${Date.now() - start}ms）: ${e && e.message}`);
      throw e;
    }
  };

  updateSplash(splash, "初始化行情数据源...", 20);
  console.log("[main] 初始化数据源...");

  // tick 推送转发（初始源与重连后重建的源共用）
  const forwardTick = (data) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("quote:tick", data);
    }
  };

  const dataSource = await t("初始化行情数据源", () => createDataSource({ mode: configManager.dataSource, qmt: configManager.qmt }));

  if (dataSource && typeof dataSource.onPush === "function") {
    dataSource.onPush("tick", forwardTick);
  }
  // 策略 bridge：优先复用行情数据源；mock 模式下独立创建（策略 RPC 不依赖 miniQMT）
  let strategyBridge = dataSource;
  if (!dataSource || dataSource.mode !== "qmt" || typeof dataSource.strategyList !== "function") {
    try {
      const { QmtDataSource } = require("./main/datasources/qmt");
      strategyBridge = await t("策略 bridge（独立模式）", () => {
        const q = new QmtDataSource(config.qmt || {});
        return q.init().then(() => q);
      });
      console.log("[main] 策略 bridge 已启动（独立模式）");
    } catch (e) {
      console.warn("[main] 策略 bridge 启动失败，策略功能将不可用:", e.message);
      strategyBridge = null;
    }
  }

  quoteService = new QuoteService({
    source: dataSource,
    cacheTtlMs: config.cache?.ttlMs ?? 5000,
    configuredMode: configManager.dataSource,
    // 重连用重建工厂：当前源不支持自检（auto 回落 mock）时，按原配置重建并接上 tick 转发
    recreateSource: async () => {
      const fresh = await createDataSource({ mode: configManager.dataSource, qmt: configManager.qmt });
      if (fresh && typeof fresh.onPush === "function") fresh.onPush("tick", forwardTick);
      return fresh;
    },
  });
  // 断线自动重连巡检（仅 QMT 源）
  quoteService.startSupervision(10000);

  // 本地 HTTP 服务：只读数据面暴露到局域网（与 UI 共享同一份 QuoteService 缓存与 QMT 连接）。
  // 启动失败（端口占用/无权限）只降级本服务，不阻塞应用启动。
  localServer = new LocalHttpServer({
    quoteService,
    config: config.localServer || {},
    version: require("./config").version,
  });
  updateSplash(splash, "启动本地服务...", 30);
  await t("启动本地 HTTP 服务", () => localServer.start());
  {
    const info = localServer.info();
    if (info.server.listening) {
      const url = `http://127.0.0.1:${info.server.port}/mooquant/api`;
      console.log(`[main] 本地服务可用: ${url}  局域网: ${info.addrs.map((a) => `http://${a}:${info.server.port}/mooquant/api`).join(" ")}`);
    } else {
      console.warn(`[main] 本地服务不可用: ${info.server.error || localServer.state}`);
    }
  }

  // LAN 服务发现：UDP 广播信标（纯 Node dgram，零新依赖）。
  // 必须晚于 localServer.start()：信标只广播已就绪的监听地址，HTTP 未监听时不广播。
  // 起不来只降级发现功能，不影响应用其余部分。
  discoveryBeacon = new DiscoveryBeacon({
    getInfo: () => localServer.info(),
    config: (config.localServer || {}).discovery || {},
    version: require("./config").version,
  });
  await t("启动 LAN 服务发现", () => discoveryBeacon.start());
  {
    const d = discoveryBeacon.info();
    if (d.listening) console.log(`[main] 服务发现已就绪 UDP:${d.port}，局域网客户端可探测`);
    else console.warn(`[main] 服务发现未启动: ${d.error || d.state}`);
  }

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
  const tradeSource = await t("初始化交易数据源", () => createTradeDataSource({ mode: configManager.tradeSource, qmt: configManager.qmt, eastmoney: configManager.eastmoney }));
  // 额度守门配置来自 autoTrade 块：日调用预算 + 为平仓预留的份数
  tradeService = new TradeService({ source: tradeSource, quota: configManager.autoTrade });

  updateSplash(splash, "加载交易日历...", 70);
  // 交易日历：拉不到就降级为「仅周一~周五」，不阻塞启动
  tradingCalendar = new TradingCalendar({ overrides: configManager.autoTrade.calendarOverrides });
  const cal = await t("加载交易日历", () => tradingCalendar.ensureLoaded());
  console.log("[main] 交易日历:", JSON.stringify(cal));

  updateSplash(splash, "恢复策略执行...", 75);

  executorService = new ExecutorService({
    strategyService, quoteService, tradeService, strategyBridge, modelService,
    autoTrade: configManager.autoTrade,
    tradingCalendar,
  });

  // 日终报告服务：只读聚合，15:05 由调度器 settle 事件触发
  reportService = new ReportService({
    tradeService,
    getExecutorStatuses: () => executorService.listRunning().data,
  });

  // 调度器：只发事件、不放行下单；执行器每次 tick 向它确认窗口
  tradingScheduler = new TradingScheduler({
    calendar: tradingCalendar,
    autoTrade: configManager.autoTrade,
    onEvent: async (name, payload) => {
      const r = await executorService.onTradingEvent(name, payload);
      // 日终结算：出报告（失败只记日志，不影响次日）
      if (name === "settle") {
        const rep = await reportService.generate(payload && payload.date);
        if (!rep.ok) console.warn("[main] 日终报告生成失败:", rep.error);
      }
      return r;
    },
  });
  executorService.setScheduler(tradingScheduler);
  await t("启动交易调度器", () => tradingScheduler.start());
  console.log("[main] 调度器状态:", JSON.stringify(tradingScheduler.getStatus()));

  await t("恢复策略执行", () => executorService.restoreRunning());

  // 启动模型服务（HTTP 子进程）
  updateSplash(splash, "启动模型服务...", 85);
  modelService = new ModelService({ port: (config.modelServer || {}).port || 8765 });
  try {
    await t("启动模型服务", () => modelService.init());
    console.log("[main] 模型服务已启动 port=" + modelService.port);
  } catch (e) {
    console.warn("[main] 模型服务启动失败:", e.message);
    modelService = null;
  }

  try {
    registerIpc({ quoteService, strategyService, backtestService, tradeService, configManager, executorService, tradingScheduler, reportService, strategyBridge, modelService, localServer, discoveryBeacon, getMainWindow: () => mainWindow });
  } catch (e) {
    console.warn("[main] IPC registration partially failed:", e && e.stack || e);
  }

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
  // 先停止广告，再拆端点：避免客户端在这中间被指向一个已关闭的服务
  if (discoveryBeacon) Promise.resolve(discoveryBeacon.stop()).catch(() => {});
  if (localServer) Promise.resolve(localServer.stop()).catch(() => {});
  if (quoteService) quoteService.dispose();
  if (backtestService) backtestService.dispose();
  if (tradeService) tradeService.dispose();
  if (tradingScheduler) tradingScheduler.stop();
  if (executorService) executorService.dispose();
  if (modelService) modelService.dispose();
  if (process.platform !== "darwin") app.quit();
});