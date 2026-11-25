/**
 * mookquant · 无界面交易引擎（trade daemon）
 *
 * 为什么需要它：
 *   1. 无人值守常驻机不需要界面，Electron 只为看盘；
 *   2. 界面起不来（远程会话/无桌面/被安全软件拦）时，策略仍然要能跑。
 *
 * 它与 main.js 共用同一套服务层（QuoteService / TradeService / ExecutorService /
 * TradingScheduler / ReportService），区别只是把「窗口 + IPC」换成了「控制台 + 日志」，
 * 因此策略、风控、额度守门的代码路径完全一致，不会出现两套逻辑。
 *
 * 用法：
 *   node tools/trade-daemon.js                 # 跑到收盘（day_end）自动退出
 *   node tools/trade-daemon.js --forever       # 常驻：收盘后不退出，等下个交易日
 *   node tools/trade-daemon.js --until=15:10   # 指定退出时间
 *   node tools/trade-daemon.js --no-trade      # 只算信号不下单（演练）
 *
 * 安全：与界面共用 data/trading.lock 抢「交易权」，同一策略只会有一个进程下单；
 * 界面（npm start）抢不到锁时会自动降级为观察模式（只读，不下单）。
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");

// ---- .env（与 main.js 同一套手动解析）----
try {
  const envText = fs.readFileSync(path.join(ROOT, ".env"), "utf-8");
  for (const line of envText.split("\n")) {
    const s = line.trim();
    if (!s || s.startsWith("#")) continue;
    const i = s.indexOf("=");
    if (i === -1) continue;
    const k = s.slice(0, i).trim();
    const v = s.slice(i + 1).trim();
    if (!process.env[k]) process.env[k] = v;
  }
} catch {}

const { ConfigManager } = require(path.join(ROOT, "main/services/config-manager"));
const { QuoteService } = require(path.join(ROOT, "main/services/quote-service"));
const { TradeService } = require(path.join(ROOT, "main/services/trade-service"));
const { StrategyService } = require(path.join(ROOT, "main/services/strategy-service"));
const { ExecutorService } = require(path.join(ROOT, "main/services/executor-service"));
const { ModelService } = require(path.join(ROOT, "main/services/model-service"));
const { TradingCalendar } = require(path.join(ROOT, "main/services/trading-calendar"));
const { TradingScheduler } = require(path.join(ROOT, "main/services/trading-scheduler"));
const { ReportService } = require(path.join(ROOT, "main/services/report-service"));
const { InstanceLock } = require(path.join(ROOT, "main/services/instance-lock"));
const { createDataSource } = require(path.join(ROOT, "main/datasources"));
const { createTradeDataSource } = require(path.join(ROOT, "main/datasources/trade"));

const LOG = path.join(ROOT, "data", "logs", "daemon.log");

/** 本次运行的锁句柄（退出时统一释放） */
let activeLock = null;

/** 解析 --key=value 形式的命令行参数 */
function args() {
  const out = {};
  for (const a of process.argv.slice(2)) {
    const m = a.match(/^--([^=]+)(?:=(.*))?$/);
    if (m) out[m[1]] = m[2] === undefined ? true : m[2];
  }
  return out;
}

/** 控制台 + 文件双写（daemon 没有界面，日志就是唯一的现场） */
function log(msg) {
  const line = "[" + new Date().toISOString() + "] " + msg;
  console.log(line);
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    fs.appendFileSync(LOG, line + "\n", "utf-8");
  } catch {}
}

/**
 * 单实例锁：与界面共用 data/trading.lock —— 同一时刻只允许一个进程拥有交易权，
 * 否则两边各下一单。界面抢不到锁会自己降级为观察模式。
 */
function acquireLock() {
  const lock = new InstanceLock({ role: "daemon" });
  const r = lock.acquire("daemon");
  if (!r.ok) {
    const h = r.holder || {};
    log("交易权已被 pid=" + h.pid + "（" + (h.role || "?") + "，启动于 " + (h.startedAt || "?") +
      "）持有，本次退出。若确认那是残留实例，先结束它再启动。");
    return null;
  }
  if (r.stale) log("已接管陈旧锁（原持有进程已不存在）");
  return lock;
}

async function main() {
  const opt = args();
  const cfgManager = new ConfigManager();
  const config = cfgManager.get();
  const autoTrade = { ...cfgManager.autoTrade };
  if (opt["no-trade"]) autoTrade.enabled = false;

  const lock = acquireLock();
  activeLock = lock;
  if (!lock) process.exit(0);

  let quoteService = null, tradeService = null, modelService = null;
  let executorService = null, scheduler = null, reportService = null;
  let shuttingDown = false;
  let statusTimer = null;

  const shutdown = async (reason) => {
    if (shuttingDown) return;
    shuttingDown = true;
    log("收到退出指令：" + reason);
    if (statusTimer) clearInterval(statusTimer);
    if (scheduler) scheduler.stop();
    // 收盘前主动退出时补一份报告，便于复盘
    try {
      if (reportService) {
        const r = await reportService.generate();
        log("退出前生成日报：" + (r.ok ? "成功 " + r.data.date : "失败 " + r.error));
      }
    } catch (e) {
      log("退出前生成日报异常：" + e.message);
    }
    try { if (executorService) executorService.dispose(); } catch {}
    try { if (tradeService) tradeService.dispose(); } catch {}
    try { if (quoteService) quoteService.dispose(); } catch {}
    try { if (modelService) modelService.dispose(); } catch {}
    lock.release();
    log("已退出");
    process.exit(0);
  };

  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("uncaughtException", (e) => {
    log("未捕获异常：" + (e && e.stack ? e.stack : e));
    shutdown("uncaughtException");
  });

  log("===== mookquant 交易引擎启动 v" + require(path.join(ROOT, "config")).version +
    "（行情=" + cfgManager.dataSource + "，交易=" + cfgManager.tradeSource +
    "，自动下单=" + (autoTrade.enabled ? "开" : "关") + "）=====");

  // ---- 行情 ----
  const quoteSource = await createDataSource({ mode: cfgManager.dataSource, qmt: cfgManager.qmt });
  quoteService = new QuoteService({
    source: quoteSource,
    cacheTtlMs: (config.cache && config.cache.ttlMs) || 5000,
    configuredMode: cfgManager.dataSource,
  });
  log("行情源就绪：" + quoteSource.mode);

  // ---- 交易 ----
  const tradeSource = await createTradeDataSource({
    mode: cfgManager.tradeSource,
    qmt: cfgManager.qmt,
    eastmoney: cfgManager.eastmoney,
  });
  tradeService = new TradeService({ source: tradeSource, quota: autoTrade });
  log("交易通道就绪：" + tradeSource.mode + "，额度预算 " + autoTrade.dailyCallBudget);

  // ---- 策略 ----
  const strategyService = new StrategyService();

  // ---- 信号服务（策略 on_bar 在 python 侧；起不来就只报错不崩）----
  modelService = new ModelService({ port: (config.modelServer || {}).port || 18765 });
  try {
    await modelService.init();
    log("模型/策略服务已就绪 port=" + modelService.port);
  } catch (e) {
    log("模型服务启动失败，信号计算将不可用：" + e.message);
    modelService = null;
  }

  // ---- 交易日历 ----
  const calendar = new TradingCalendar({ overrides: autoTrade.calendarOverrides });
  const cal = await calendar.ensureLoaded();
  log("交易日历：" + JSON.stringify(cal));

  // ---- 执行器 + 调度器 + 日报 ----
  executorService = new ExecutorService({
    strategyService, quoteService, tradeService, modelService, autoTrade, tradingCalendar: calendar,
  });
  reportService = new ReportService({
    tradeService,
    getExecutorStatuses: () => executorService.listRunning().data,
  });
  scheduler = new TradingScheduler({
    calendar,
    autoTrade,
    onEvent: async (name, payload) => {
      log("调度事件 " + name + (payload && payload.catchUp ? "（补跑）" : ""));
      await executorService.onTradingEvent(name, payload);
      if (name === "settle") {
        const r = await reportService.generate(payload && payload.date);
        log("日终报告：" + (r.ok ? "已生成 " + r.data.date + "，成交 " + r.data.orders + " 笔" : "失败 " + r.error));
      }
      if (name === "day_end") {
        if (opt.forever) {
          // 常驻模式：不退出、不再重复出报告（15:05 已生成），等次日 09:15
          log("今日收盘结束，常驻等待下一个交易日（Ctrl+C 退出）");
        } else {
          await shutdown("day_end");
        }
      }
    },
  });
  executorService.setScheduler(scheduler);
  await scheduler.start();
  await executorService.restoreRunning();
  log("策略已恢复：" + JSON.stringify(executorService.listRunning().data.map((e) => ({
    name: e.strategyName, status: e.status, schedule: e.schedule && e.schedule.label,
  }))));

  // 每 5 分钟打一次心跳，便于事后确认「它当时还活着、在等什么」
  // 空闲（非交易时段）时降到 30 分钟一次，避免常驻一晚刷几百行日志
  let lastIdleLogAt = 0;
  statusTimer = setInterval(() => {
    try {
      const st = scheduler.getStatus();
      const ex = executorService.listRunning().data;
      if (st.state === "idle") {
        const nowMs = Date.now();
        if (nowMs - lastIdleLogAt < 30 * 60 * 1000) return;
        lastIdleLogAt = nowMs;
      }
      log("心跳 状态=" + st.stateLabel + " 交易日=" + (st.isTradingDay ? "是" : "否") +
        " 下一时点=" + st.nextEventAt +
        " 额度=" + JSON.stringify(tradeService.getQuota()));
      for (const e of ex) {
        log("  · 策略 " + e.strategyName + " [" + e.status + "] 调度=" + (e.schedule && e.schedule.label) +
          " 已算信号=" + e.tickCount +
          " 上次=" + (e.lastSignal ? e.lastSignal.action : "-") +
          " 跳过=" + (e.lastSkip || "-") + (e.error ? " 异常=" + e.error : ""));
      }
    } catch (e) {
      log("心跳异常：" + e.message);
    }
  }, 5 * 60 * 1000);

  // ---- 可选：指定时间自动退出 ----
  if (opt.until) {
    const [h, m] = String(opt.until).split(":").map(Number);
    const timer = setInterval(() => {
      const now = new Date();
      if (now.getHours() * 60 + now.getMinutes() >= h * 60 + m) {
        clearInterval(timer);
        shutdown("到达 --until=" + opt.until);
      }
    }, 20000);
  }

  log("引擎进入常驻，等待调度时点（Ctrl+C 退出）");
}

main().catch((e) => {
  log("启动失败：" + (e && e.stack ? e.stack : e));
  try { if (activeLock) activeLock.release(); } catch {}
  process.exit(1);
});
