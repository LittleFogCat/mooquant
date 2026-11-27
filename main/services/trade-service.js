/**
 * mookquant · Trade Service（主进程）
 *
 * 交易编排：下单/撤单/查询 + 下单前风控 + 接口额度守门 + 交易记录持久化。
 *
 * 额度守门的意义：东财妙想模拟组合每天只有 1000 次调用额度，用尽后连止损都下不出去。
 * 所以在这里（唯一的交易出口）计数，并区分「开仓」与「平仓」：
 * 额度逼近上限时先停开仓，把剩余额度留给止损/平仓。
 */

const { LogService } = require("./log-service");

/** 本地日期 YYYY-MM-DD（与日志/报告文件口径一致） */
function ymdLocal(d = new Date()) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

class TradeService {
  /**
   * @param {object} deps
   * @param {object} deps.source 交易数据源
   * @param {object} [deps.quota] { dailyCallBudget, reserveForStopLoss }
   */
  constructor({ source, quota }) {
    this.source = source;
    this._log = new LogService();
    this._dailyTradeCount = {}; // dateStr -> count
    const q = quota || {};
    this._quotaCfg = {
      dailyCallBudget: Number(q.dailyCallBudget) > 0 ? Number(q.dailyCallBudget) : 0,
      reserveForStopLoss: Number(q.reserveForStopLoss) > 0 ? Number(q.reserveForStopLoss) : 0,
      // 阻断后多久允许再试一次（默认 30 分钟）：休眠额度每天重置、激活后应立即恢复
      blockRetryMs: Number(q.blockRetryMs) > 0 ? Number(q.blockRetryMs) : undefined,
    };
    this._calls = 0;
    this._callsDate = ymdLocal();
  }

  _todayKey() {
    return ymdLocal();
  }

  /** 每次真正打到数据源的调用都记一笔（失败的请求同样消耗额度） */
  _trackCall() {
    const today = this._todayKey();
    if (today !== this._callsDate) {
      this._callsDate = today;
      this._calls = 0;
    }
    this._calls++;
  }

  /** 统一出口：计数 + 调用 */
  async _call(method, ...args) {
    this._trackCall();
    try {
      return await this.source[method](...args);
    } catch (e) {
      this._noteBlockingError(e);
      throw e;
    }
  }

  /**
   * 识别「整条通道已经不可用」的错误，并把通道标记为阻断
   *
   * 为什么要在服务层统一识别：东财的 113（额度用尽/账户休眠）和 114（密钥失效）不是
   * 单次请求失败，而是**后续所有请求都会失败**。如果只让每次调用各自报错，
   * 执行器会继续每个时点试一遍，日报也会生成一份「账户 null、持仓空、成交 0」的假报告。
   *
   * @param {Error} e
   */
  _noteBlockingError(e) {
    const msg = (e && e.message) || "";
    if (!msg) return;
    const isQuota = /\b113\b/.test(msg) || msg.includes("休眠") || msg.includes("调用次数已达到上限");
    const isAuth = /\b114\b/.test(msg) || msg.includes("密钥不存在") || msg.includes("已失效");
    if (!isQuota && !isAuth) return;
    this._blocked = {
      at: new Date().toISOString(),
      date: this._todayKey(),
      fatal: isAuth,
      code: isAuth ? 114 : 113,
      reason: msg,
    };
  }

  /**
   * 当前阻断状态
   *
   * 阻断不能永久生效：
   *  - 113（休眠/额度）每天重置 10 次，用户当天激活后额度立刻恢复；
   *  - 114（密钥）改完 .env 也需要重启才生效，但至少别让程序一直卡在阻断态。
   * 所以超过 retryAfterMs（默认 30 分钟）就允许放行一次"探针"：真恢复了下一次调用就能成功，
   * 没恢复会再次被标记阻断。
   */
  getBlocked() {
    if (!this._blocked) return null;
    if (this._blocked.date !== this._todayKey()) {
      this._blocked = null;
      return null;
    }
    const retryAfterMs = this._quotaCfg.blockRetryMs > 0 ? this._quotaCfg.blockRetryMs : 30 * 60 * 1000;
    const age = Date.now() - new Date(this._blocked.at).getTime();
    if (Number.isFinite(age) && age > retryAfterMs) {
      this._blockedProbe = this._blocked;
      this._blocked = null;
      return null; // 放行一次探测；若仍失败，_noteBlockingError 会立刻重新标记
    }
    return this._blocked;
  }

  /** 上一次阻断的残留信息（用于界面提示"已尝试恢复"） */
  getLastBlock() {
    return this._blocked || this._blockedProbe || null;
  }

  /**
   * 当前额度状态
   * limit=0 表示未启用守门（mock/QMT 无硬性额度）
   *
   * 计数口径：优先用数据源自报的「真实 HTTP 调用数」（缓存命中不算），
   * 否则用服务层调用数。前者才对应平台真正扣的额度——读缓存不该扣费。
   */
  getQuota() {
    const limit = this._quotaCfg.dailyCallBudget;
    const reserve = Math.min(this._quotaCfg.reserveForStopLoss, limit);
    if (this._todayKey() !== this._callsDate) {
      this._calls = 0;
      this._callsDate = this._todayKey();
    }
    let used = this._calls;
    let source = "service";
    if (this.source && typeof this.source.getCallStats === "function") {
      try {
        const stats = this.source.getCallStats();
        if (stats && Number.isFinite(Number(stats.calls))) {
          used = Number(stats.calls);
          source = "adapter";
        }
      } catch {}
    }
    const openThreshold = limit > 0 ? limit - reserve : 0;
    // 通道被阻断（113 额度/休眠、114 密钥失效）时，本地计数已经毫无意义：平台压根不收请求
    const blocked = this.getBlocked();
    return {
      date: this._callsDate,
      used,
      source,
      limit,
      reserve,
      remaining: limit > 0 ? Math.max(0, limit - used) : null,
      openBudget: limit > 0 ? openThreshold : null,
      openAllowed: !blocked && (limit <= 0 || used < openThreshold),
      closeAllowed: !blocked && (limit <= 0 || used < limit),
      usedRatio: limit > 0 ? +(used / limit).toFixed(4) : 0,
      blocked: !!blocked,
      blockCode: blocked ? blocked.code : null,
      blockReason: blocked ? blocked.reason : null,
      blockedAt: blocked ? blocked.at : null,
    };
  }

  /**
   * 下单前风控检查
   */
  async _riskCheck(order) {
    const risk = order.risk || {};

    // 1. 单笔最大金额
    if (risk.maxOrderAmount && risk.maxOrderAmount > 0) {
      let price = order.price;
      if (!price && typeof this.source.getAccount === "function") {
        // 市价单无法预知价格，用持仓或账户信息估算
        // 这里简单跳过，由执行器层控制
      }
      if (price && price > 0) {
        const amount = price * order.quantity;
        if (amount > risk.maxOrderAmount) {
          return { passed: false, error: "单笔金额超限: " + amount.toFixed(2) + " > " + risk.maxOrderAmount };
        }
      }
    }

    // 2. 日内交易次数
    if (risk.maxDailyTrades && risk.maxDailyTrades > 0) {
      const today = this._todayKey();
      const count = this._dailyTradeCount[today] || 0;
      if (count >= risk.maxDailyTrades) {
        return { passed: false, error: "日内交易次数超限: " + count + " >= " + risk.maxDailyTrades };
      }
    }

    // 3. 最大持仓比例
    if (risk.maxPositionRatio && risk.maxPositionRatio > 0 && order.side === "buy") {
      try {
        const account = await this._call("getAccount");
        const positions = await this._call("getPositions");
        const totalAssets = account.totalAssets || 0;
        if (totalAssets > 0) {
          const existing = (positions || []).find(
            (p) => p.symbol === order.symbol
          );
          const existingValue = existing ? existing.marketValue : 0;
          const orderValue = (order.price || 0) * order.quantity;
          const newPosValue = existingValue + orderValue;
          if (newPosValue / totalAssets > risk.maxPositionRatio) {
            return {
              passed: false,
              error: "持仓比例超限: " +
                ((newPosValue / totalAssets) * 100).toFixed(1) +
                "% > " + (risk.maxPositionRatio * 100) + "%",
            };
          }
        }
      } catch {
        // 查询失败时不阻断，让下单尝试
      }
    }

    return { passed: true };
  }

  async placeOrder(order) {
    // 基础校验
    if (!order.symbol || !order.symbol.trim()) {
      return { ok: false, error: "股票代码不能为空" };
    }
    if (!order.quantity || order.quantity <= 0) {
      return { ok: false, error: "数量必须大于 0" };
    }
    if (order.orderType === "limit" && (!order.price || order.price <= 0)) {
      return { ok: false, error: "限价单价格必须大于 0" };
    }

    // 额度守门：额度紧张时先停开仓，把余量留给平仓/止损
    const quota = this.getQuota();
    if (!quota.closeAllowed) {
      const msg = "交易接口额度已用尽（" + quota.used + "/" + quota.limit + "），今日不再下单";
      this._log.logTrade({ type: "rejected", symbol: order.symbol, side: order.side, quantity: order.quantity, reason: msg });
      return { ok: false, error: msg };
    }
    if (order.side === "buy" && !quota.openAllowed) {
      const msg =
        "额度余量不足（已用 " + quota.used + "，为平仓预留 " + quota.reserve + "），今日禁止开仓";
      this._log.logTrade({ type: "rejected", symbol: order.symbol, side: order.side, quantity: order.quantity, reason: msg });
      return { ok: false, error: msg };
    }

    // 风控检查
    const riskResult = await this._riskCheck(order);
    if (!riskResult.passed) {
      this._log.logTrade({
        type: "rejected",
        symbol: order.symbol,
        side: order.side,
        quantity: order.quantity,
        price: order.price,
        reason: riskResult.error,
      });
      return { ok: false, error: riskResult.error };
    }

    try {
      const result = await this._call("placeOrder", order);

      // 交易计数 +1
      const today = this._todayKey();
      this._dailyTradeCount[today] = (this._dailyTradeCount[today] || 0) + 1;

      // 持久化交易记录
      this._log.logTrade({
        type: "order",
        orderId: result.orderId,
        symbol: order.symbol,
        side: order.side,
        quantity: order.quantity,
        price: order.price,
        orderType: order.orderType,
        status: result.status,
        strategyId: order.strategyId || null,
      });

      return { ok: true, data: result };
    } catch (e) {
      this._log.logTrade({
        type: "error",
        symbol: order.symbol,
        side: order.side,
        quantity: order.quantity,
        error: e.message,
      });
      return { ok: false, error: e.message };
    }
  }

  async cancelOrder(orderId) {
    try {
      const result = await this._call("cancelOrder", orderId);
      this._log.logTrade({
        type: "cancel",
        orderId: orderId,
        result: result,
      });
      return { ok: true, data: result };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  async getPositions() {
    try {
      const data = await this._call("getPositions");
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  async getOrders() {
    try {
      const data = await this._call("getOrders");
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  async getAccount() {
    try {
      const data = await this._call("getAccount");
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  get info() {
    return {
      mode: this.source.mode,
      description: this.source.description,
    };
  }

  dispose() {
    if (this.source && typeof this.source.dispose === "function") {
      this.source.dispose();
    }
  }
}

module.exports = { TradeService };
