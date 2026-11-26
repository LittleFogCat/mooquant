/**
 * mookquant · 日终报告
 *
 * 每个交易日 15:05（调度器 settle 事件）自动生成 `data/reports/{YYYY-MM-DD}.json`，
 * 回答三个问题：今天账户变成什么样了、今天下了哪些单、今天为什么没交易。
 *
 * 只读聚合，不做任何交易动作；接口调用尽量少（账户 1 次 + 持仓 1 次），失败也照常出报告，
 * 把失败原因写进 errors —— 报告本身不能因为某个接口挂了就缺失。
 */
const path = require("path");
const fs = require("fs");
const { LogService } = require("./log-service");

const REPORT_DIR = path.join(__dirname, "..", "..", "data", "reports");

/** 本地日期 YYYY-MM-DD（与 LogService 的日志文件口径一致） */
function ymd(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

class ReportService {
  /**
   * @param {object} deps
   * @param {import('./trade-service').TradeService} deps.tradeService
   * @param {() => Array} [deps.getExecutorStatuses] 取运行中策略状态（信号数/异常）
   * @param {object} [deps.logService] 注入口子（测试用，默认读 data/ 日志）
   */
  constructor({ tradeService, getExecutorStatuses, logService }) {
    this.tradeService = tradeService;
    this._getExecutors = getExecutorStatuses || (() => []);
    this._log = logService || new LogService();
    this._lastError = null;
  }

  /**
   * 生成指定日期的报告（默认今天）并落盘
   * @param {string} [date]
   * @returns {Promise<{ok:boolean, data?:object, error?:string}>}
   */
  async generate(date = ymd()) {
    const report = await this._aggregate(date, { withAccount: true });
    try {
      if (!fs.existsSync(REPORT_DIR)) fs.mkdirSync(REPORT_DIR, { recursive: true });
      fs.writeFileSync(this._path(date), JSON.stringify(report, null, 2), "utf-8");
      this._lastError = null;
      console.log("[report] 日终报告已生成 " + date + "：成交 " + report.orders + " 笔，信号 " + report.signals + " 次");
      return { ok: true, data: report };
    } catch (e) {
      this._lastError = "报告写入失败: " + e.message;
      console.log("[report] " + this._lastError);
      return { ok: false, error: this._lastError, data: report };
    }
  }

  /**
   * 盘中预览：与报告同口径，但不查账户/持仓（零接口开销），用于「今日表现」卡片实时显示。
   * @param {string} [date]
   */
  async preview(date = ymd()) {
    return this._aggregate(date, { withAccount: false });
  }

  /** 今日视图：已生成报告优先，否则给实时预览 */
  async today() {
    const cached = this.read();
    if (cached) return cached;
    return this.preview();
  }

  /**
   * 聚合核心
   * @param {string} date
   * @param {{withAccount:boolean}} opts 是否查询账户/持仓（唯一会产生接口调用的部分）
   */
  async _aggregate(date, opts = {}) {
    const errors = [];
    const report = {
      date,
      generatedAt: opts.withAccount ? new Date().toISOString() : null,
      generated: !!opts.withAccount,
      source: null,
      account: null,
      trades: [],
      positions: [],
      signals: 0,
      orders: 0,
      cancels: 0,
      rejects: 0,
      strategies: [],
      skips: [],
      quota: null,
      errors,
    };

    // 1. 账户与持仓（接口失败不阻断报告生成）
    try {
      report.source = this.tradeService.info ? this.tradeService.info.mode : null;
    } catch {}
    if (opts.withAccount) {
      try {
        const acct = await this.tradeService.getAccount();
        if (acct.ok && acct.data) {
          const a = acct.data;
          const pnl = Number(a.totalPnl) || 0;
          const base = (Number(a.totalAssets) || 0) - pnl;
          report.account = {
            totalAssets: a.totalAssets,
            available: a.available,
            marketValue: a.marketValue,
            pnl: a.totalPnl,
            pnlPct: base > 0 ? +((pnl / base) * 100).toFixed(4) : null,
            nav: base > 0 ? +(Number(a.totalAssets) / base).toFixed(6) : null,
          };
        } else {
          errors.push("账户查询失败: " + (acct.error || "未知错误"));
        }
      } catch (e) {
        errors.push("账户查询异常: " + e.message);
      }

      try {
        const pos = await this.tradeService.getPositions();
        if (pos.ok && Array.isArray(pos.data)) {
          report.positions = pos.data.map((p) => ({
            symbol: p.symbol,
            name: p.name || "",
            quantity: p.quantity,
            cost: p.costPrice,
            currentPrice: p.currentPrice,
            marketValue: p.marketValue,
            pnl: p.pnl,
            pnlPct: p.pnlPct,
          }));
        } else if (!pos.ok) {
          errors.push("持仓查询失败: " + (pos.error || "未知错误"));
        }
      } catch (e) {
        errors.push("持仓查询异常: " + e.message);
      }
    }

    // 2. 当日流水（本地日志，不耗接口）
    try {
      const records = this._log.readTrades(date) || [];
      report.trades = records
        .filter((r) => r.type === "order")
        .map((r) => ({
          orderId: r.orderId,
          symbol: r.symbol,
          side: r.side,
          price: r.price,
          quantity: r.quantity,
          status: r.status,
          strategyId: r.strategyId || null,
          time: r.timestamp,
        }));
      report.orders = report.trades.length;
      report.cancels = records.filter((r) => r.type === "cancel").length;
      report.rejects = records.filter((r) => r.type === "rejected" || r.type === "error").length;
    } catch (e) {
      errors.push("交易流水读取失败: " + e.message);
    }

    // 2b. 委托终态回填：本地流水写的是「下单瞬间」的状态（多为 pending），
    //     而不像成交回查只在策略日志里留痕。不查回来，日报会把成交记成待成交。
    //     只在「出正式报告」时回填（预览要保持零接口开销），且仅当日有委托时才多花这 1 次调用。
    if (opts.withAccount && report.trades.length) {
      try {
        const ord = await this.tradeService.getOrders();
        if (ord.ok && Array.isArray(ord.data)) {
          const byId = new Map(ord.data.map((o) => [String(o.orderId), o]));
          let reconciled = 0;
          for (const t of report.trades) {
            const live = byId.get(String(t.orderId));
            if (!live) continue;
            if (live.status && live.status !== t.status) {
              t.status = live.status;
              reconciled++;
            }
            t.filledQuantity = live.filledQuantity;
            t.filledPrice = live.filledPrice;
          }
          report.ordersReconciled = reconciled;
        } else if (!ord.ok) {
          errors.push("委托终态回填失败: " + (ord.error || "未知错误"));
        }
      } catch (e) {
        errors.push("委托终态回填异常: " + e.message);
      }
    }
    report.filled = report.trades.filter((t) => t.status === "filled").length;

    // 3. 策略侧视角（为什么没交易）
    try {
      const execs = this._getExecutors() || [];
      report.strategies = execs.map((e) => ({
        id: e.strategyId,
        name: e.strategyName,
        status: e.status,
        signals: e.tickCount || 0,
        polls: e.pollCount || 0,
        lastSignal: e.lastSignal || null,
        schedule: e.schedule ? e.schedule.label : null,
        lastFireKey: e.schedule ? e.schedule.lastFireKey : null,
        marketLabel: e.marketLabel || null,
        error: e.error || null,
      }));
      report.signals = report.strategies.reduce((n, s) => n + (s.signals || 0), 0);
      report.skips = execs
        .filter((e) => e.lastSkip)
        .map((e) => ({ strategy: e.strategyName, reason: e.lastSkip }));
      for (const e of execs) if (e.error) errors.push(e.strategyName + ": " + e.error);
    } catch (e) {
      errors.push("策略状态读取失败: " + e.message);
    }

    // 4. 额度占用
    try {
      report.quota = this.tradeService.getQuota ? this.tradeService.getQuota() : null;
    } catch (e) {
      errors.push("额度读取失败: " + e.message);
    }

    // 5. 落盘交给 generate()，这里只返回聚合结果
    return report;
  }

  /** 读取已生成的报告（没有则 null） */
  read(date = ymd()) {
    try {
      return JSON.parse(fs.readFileSync(this._path(date), "utf-8"));
    } catch {
      return null;
    }
  }

  /** 最近 N 天的报告列表（按日期倒序） */
  list(limit = 30) {
    try {
      if (!fs.existsSync(REPORT_DIR)) return [];
      const files = fs
        .readdirSync(REPORT_DIR)
        .filter((f) => /^\d{4}-\d{2}-\d{2}\.json$/.test(f))
        .sort()
        .reverse()
        .slice(0, Math.max(1, Number(limit) || 30));
      return files
        .map((f) => {
          try {
            const r = JSON.parse(fs.readFileSync(path.join(REPORT_DIR, f), "utf-8"));
            return {
              date: r.date,
              generatedAt: r.generatedAt,
              totalAssets: r.account ? r.account.totalAssets : null,
              orders: r.orders,
              signals: r.signals,
              errors: (r.errors || []).length,
            };
          } catch {
            return null;
          }
        })
        .filter(Boolean);
    } catch {
      return [];
    }
  }

  getStatus() {
    const today = this.read(ymd());
    return {
      today: ymd(),
      generated: !!today,
      generatedAt: today ? today.generatedAt : null,
      lastError: this._lastError,
    };
  }

  _path(date) {
    return path.join(REPORT_DIR, date + ".json");
  }
}

module.exports = { ReportService, REPORT_DIR, ymd };
