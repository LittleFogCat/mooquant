/**
 * mookquant · 策略执行引擎（无人值守）
 *
 * 职责：
 *  1. 管理运行中的策略实例（启动/停止/状态查询）
 *  2. 定时调度：交易日 → 交易时段 → 策略调度时点 → 风控 → 拉行情 → 算信号 → 下单 → 回查
 *  3. 信号语义适配：qty > target_position > 默认仓位（满仓 90% / 清仓）
 *  4. 无人值守保护：非交易日/非交易时段不下单、按策略周期调度、下单节流、重复委托去重、异常熔断
 *  5. 执行日志持久化 + 每日净值记录
 *
 * 三道闸门（顺序不可调换，都是"零接口开销"的本地判断）：
 *  ① 交易日历：今天是不是交易日（节假日）
 *  ② 交易时段 + 调度器放行：交易所开没开市、今天在不在我们的交易计划窗口
 *  ③ 策略调度时点：这个周期的这次信号是不是该算了（日线策略一天只算 2 次）
 */

const { LogService } = require("./log-service");
const { sessionState, canTrade, stateLabel } = require("./market-clock");
const { resolveSchedule, evaluate, nextFireAt } = require("./strategy-timing");

function log(msg) {
  console.log("[executor] " + msg);
}

/** 本地日期 YYYY-MM-DD */
function ymdLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

/** 向下取整到整手（100 股） */
function toLot(qty) {
  return Math.floor(Number(qty) / 100) * 100;
}

/** 轮询间隔：只做本地时间比对，15s 足够（1m 策略也能在收盘后 5s 内响应） */
const DEFAULT_TICK_MS = 15000;
/** 去重 key 上限，超过则按日期清理 */
const FIRED_KEYS_LIMIT = 400;

class StrategyExecutor {
  constructor({ strategy, symbols, quoteService, tradeService, strategyService, strategyBridge, modelService, autoTrade, tradingCalendar, getScheduler }) {
    this.strategy = strategy;
    this.symbols = symbols || [];
    this.quoteService = quoteService;
    this.tradeService = tradeService;
    this.strategyService = strategyService;
    this.strategyBridge = strategyBridge;
    this.modelService = modelService;
    this._autoTrade = autoTrade || {};
    this._tradingCalendar = tradingCalendar || null;
    this._getScheduler = getScheduler || null;
    this._log = new LogService();
    this._timer = null;
    this.status = "stopped";
    this._lastTick = null;
    this._lastSignal = null;
    this._tickCount = 0;        // 真正执行了信号计算的次数（不含空轮询）
    this._pollCount = 0;        // 定时器触发次数（含被闸门挡掉的）
    this._error = null;
    this._consecutiveErrors = 0;
    this._lastOrderAt = 0;
    this._lastSkip = null;      // 最近一次跳过原因（时段/调度/节流/无资金…）
    this._marketState = null;   // 最近一次 tick 的市场状态
    // 调度
    this._plan = null;          // resolveSchedule 结果
    this._firedKeys = new Set();// 已触发时点 key（`日期#时点`）
    this._lastFireKey = null;
    this._lastFireAt = null;
    this._nextFire = null;
  }

  start() {
    if (this.status === "running") return;
    this.status = "running";
    this._error = null;
    this._consecutiveErrors = 0;
    this._plan = resolveSchedule(this.strategy.schedule || {});
    this._updateStatus("running");
    log("启动策略: " + this.strategy.name + " (" + this.strategy.id + ") 调度=" + this._plan.label);

    this.tick();
    const tickMs = Math.min(Math.max(Number(this._autoTrade.tickMs) || DEFAULT_TICK_MS, 1000), 60000);
    this._timer = setInterval(() => this.tick(), tickMs);
  }

  stop() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    this.status = "stopped";
    this._updateStatus("stopped");
    log("停止策略: " + this.strategy.name + " (" + this.strategy.id + ")");
  }

  async tick() {
    this._pollCount++;
    this._lastTick = new Date().toISOString();
    const now = new Date();
    this._pollAt = now.toISOString();

    try {
      // ① 交易日守卫：节假日不交易（日历未校准时按周一~周五放行）
      if (this._tradingCalendar && !this._tradingCalendar.isTradingDay(now)) {
        this._marketState = "holiday";
        this._lastSkip = "非交易日（" + ymdLocal(now) + "）";
        this._error = null;
        return;
      }

      // ② 交易时段 + 调度器放行
      const state = sessionState(now);
      this._marketState = state;
      if (!this._isTradingAllowed(now)) {
        const scheduler = this._getScheduler && this._getScheduler();
        this._lastSkip = !canTrade({ allowOffHours: this._autoTrade.allowOffHours, now })
          ? "非交易时段（" + stateLabel(state) + "）"
          : "调度器未放行（" + (scheduler ? scheduler.state : "?") + "）";
        this._error = null;
        return;
      }

      // ③ 策略调度时点守卫：没到本周期该算信号的时候，一次接口都不调用
      if (!this._isDue(now)) return;

      const symbols = this.symbols || [];
      if (!symbols.length) {
        this._error = "策略未配置标的";
        return;
      }

      this._tickCount++;
      this._lastSkip = null;   // 真正进入执行阶段：先清掉上一轮的跳过原因，本次若有跳过会被重新赋值
      for (const symbol of symbols) {
        try {
          await this._tickSymbol(symbol);
        } catch (e) {
          this._error = symbol + " 执行异常: " + e.message;
          this._consecutiveErrors++;
          log("tick 异常(" + symbol + "): " + e.message);
          this._checkCircuitBreaker();
          if (this.status !== "running") return;
        }
      }
      this._error = null;
      this._consecutiveErrors = 0;
    } catch (e) {
      this._error = e.message;
      this._consecutiveErrors++;
      log("tick 异常: " + e.message);
      this._logTick({ error: e.message });
      this._checkCircuitBreaker();
    }
  }

  /** 交易时段 + 调度器双重放行（allowOffHours 调试开关可绕过） */
  _isTradingAllowed(now) {
    if (this._autoTrade.allowOffHours) return true;
    if (!canTrade({ now })) return false;
    const scheduler = this._getScheduler && this._getScheduler();
    if (scheduler && typeof scheduler.canPlaceOrder === "function") {
      return scheduler.canPlaceOrder(now);
    }
    return true;
  }

  /**
   * 是否到了本周期的信号时点
   * 注意：先记账再执行——宁可漏一次，也不重复下单。
   */
  _isDue(now) {
    if (!this._plan) this._plan = resolveSchedule(this.strategy.schedule || {});
    const ev = evaluate(this._plan, now, this._firedKeys);
    if (!ev.due) {
      this._lastSkip = "等待调度时点（" + this._plan.label + "）";
      this._nextFire = nextFireAt(this._plan, now);
      return false;
    }
    this._firedKeys.add(ev.key);
    this._pruneFired(now);
    this._lastFireKey = ev.key;
    this._lastFireAt = now.toISOString();
    this._nextFire = nextFireAt(this._plan, now);
    return true;
  }

  /** 去重集合按天清理，避免长跑内存增长（1m 策略一天 240 个 key） */
  _pruneFired(now) {
    if (this._firedKeys.size <= FIRED_KEYS_LIMIT) return;
    const today = ymdLocal(now);
    for (const key of this._firedKeys) {
      if (!key.startsWith(today)) this._firedKeys.delete(key);
    }
  }

  /** 单标的：风控 → 取数 → 信号 → 执行 */
  async _tickSymbol(symbol) {
    // 1. 运行时风控：止损止盈（优先级最高）
    const riskAction = await this._checkStopLossProfit(symbol);
    if (riskAction) {
      this._logTick({ symbol, signal: { action: riskAction, reason: "风控触发" } });
      return;
    }

    // 2. K 线（周期策略取对应周期 bar，日线策略取日线）
    const plan = this._plan || resolveSchedule(this.strategy.schedule || {});
    const isBar = plan.kind === "bar";
    const period = isBar ? plan.period : "1d";
    const barCount = isBar ? 240 : 60;
    const histResult = await this.quoteService.getHistory(symbol, period, barCount);
    if (!histResult.ok) throw new Error("获取行情失败: " + histResult.error);
    const bars = histResult.data.bars;
    if (!bars || !bars.length) {
      if (isBar) {
        // 东财公开行情只有日线，没有分钟线：明确跳过而不是拿日线算分钟信号，也不计入熔断
        this._lastSkip = "行情源无 " + period + " K 线（请改用日线周期，或切换 QMT 行情）";
        log(this._lastSkip + " 标的=" + symbol);
        return;
      }
      throw new Error("无 K 线数据");
    }

    // 3. 信号（优先模型服务 HTTP，回退 stdio 策略桥）
    const signal = await this._computeSignal(symbol, bars);
    if (!signal) return;
    if (signal.error) throw new Error(signal.error);

    this._lastSignal = signal;
    log("tick #" + this._tickCount + " " + symbol + " -> " + signal.action + " (" + signal.reason + ")");

    // 4. 执行
    const risk = this.strategy.risk || {};
    await this._executeSignal(symbol, signal, bars[bars.length - 1].close, {
      maxOrderAmount: risk.maxOrderAmount,
      maxDailyTrades: risk.maxDailyTrades,
      maxPositionRatio: risk.maxPositionRatio,
    });

    // 5. 记录
    this._logTick({ symbol, signal, price: bars[bars.length - 1].close });
  }

  /** 信号计算：模型服务优先，其次策略桥 */
  async _computeSignal(symbol, bars) {
    try {
      if (this.modelService) {
        const payload = {
          bars,
          params: { ...(this.strategy.params || {}) },
          symbol,
        };
        if (this.strategy.modelId) payload.params.model_id = this.strategy.modelId;
        if (this.strategy.type && this.strategy.type !== "shell") payload.strategy = this.strategy.type;
        const result = await this.modelService.computeSignal(payload);
        if (!result.ok) throw new Error("信号计算失败: " + (result.error || "未知错误"));
        return result.data;
      }
      if (this.strategyBridge && this.strategyBridge.strategySignal) {
        const rpcParams = { ...(this.strategy.params || {}) };
        if (this.strategy.modelId) rpcParams.model_id = this.strategy.modelId;
        return await this.strategyBridge.strategySignal({
          type: this.strategy.type,
          bars,
          params: rpcParams,
          symbol,
        });
      }
      throw new Error("模型服务和策略桥均不可用");
    } catch (e) {
      throw new Error("信号计算失败: " + e.message);
    }
  }

  /**
   * 信号执行（策略适配核心）
   * 优先级：signal.qty > signal.target_position > 默认（买入 90% 可用 / 卖出清仓）
   */
  async _executeSignal(symbol, signal, price, orderRisk) {
    const action = signal.action;
    if (action !== "buy" && action !== "sell") return;

    // 总开关：autoTrade.enabled=false 时只算信号不下单（安全演练模式）
    if (this._autoTrade.enabled === false) {
      this._lastSkip = "自动交易已关闭（仅记录信号）";
      return;
    }

    const acct = await this.tradeService.getAccount();
    if (!acct.ok) {
      this._lastSkip = "无法获取账户: " + acct.error;
      return;
    }
    const positions = await this.tradeService.getPositions();
    const pos = positions.ok ? (positions.data || []).find((p) => p.symbol === symbol) : null;

    let qty = 0;
    if (Number(signal.qty) > 0) {
      qty = toLot(signal.qty);
    } else if (signal.target_position != null && Number.isFinite(Number(signal.target_position))) {
      const target = Math.min(Math.max(Number(signal.target_position), 0), 1);
      const targetValue = (acct.data.totalAssets || 0) * target;
      const curValue = pos ? pos.marketValue || 0 : 0;
      const delta = targetValue - curValue;
      if (action === "buy" && delta > 0) qty = toLot(delta / price);
      if (action === "sell" && delta < 0) qty = toLot(Math.min(-delta / price, pos ? pos.quantity : 0));
      if (qty <= 0) {
        this._lastSkip = "目标仓位已达（" + (target * 100).toFixed(0) + "%）";
        return;
      }
    } else if (action === "sell") {
      qty = pos ? toLot(pos.quantity) : 0;
      if (pos && pos.quantity > qty && qty === 0) qty = pos.quantity; // 零股兜底
    } else {
      const avail = acct.data.available || 0;
      qty = toLot((avail * 0.9) / price);
    }

    if (qty <= 0) {
      this._lastSkip = action === "buy" ? "可用资金不足" : "无持仓可卖";
      return;
    }

    // 节流：两次下单至少间隔 minOrderIntervalMs（默认 30s）
    const minGap = Number(this._autoTrade.minOrderIntervalMs || 30000);
    if (Date.now() - this._lastOrderAt < minGap) {
      this._lastSkip = "下单节流（" + Math.round(minGap / 1000) + "s）";
      return;
    }

    // 去重：同标的同方向仍有未成交委托时不再重复下单
    const orders = await this.tradeService.getOrders();
    if (orders.ok) {
      const dup = (orders.data || []).find(
        (o) => o.symbol === symbol && o.side === action && o.status === "pending"
      );
      if (dup) {
        this._lastSkip = "已有未成交委托 " + dup.orderId;
        return;
      }
    }

    const result = await this.tradeService.placeOrder({
      symbol,
      side: action,
      quantity: qty,
      orderType: "market",
      risk: orderRisk,
      strategyId: this.strategy.id,
    });

    if (!result.ok) {
      this._lastSkip = "下单失败: " + result.error;
      log((action === "buy" ? "买入" : "卖出") + "失败 " + symbol + ": " + result.error);
      return;
    }

    this._lastOrderAt = Date.now();
    log((action === "buy" ? "买入" : "卖出") + "下单: " + symbol + " qty=" + qty + " orderId=" + result.data.orderId);

    // 回查成交（无人值守要能自证结果）
    await this._confirmOrder(result.data.orderId);
  }

  /** 下单后回查状态，写入执行日志 */
  async _confirmOrder(orderId) {
    if (!orderId) return;
    for (let i = 0; i < 3; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const r = await this.tradeService.getOrders();
      if (!r.ok) continue;
      const o = (r.data || []).find((x) => x.orderId === orderId);
      if (!o) continue;
      if (o.status === "filled" || o.status === "cancelled" || o.status === "rejected") {
        log("委托 " + orderId + " 终态: " + o.status + (o.filledPrice ? " @" + o.filledPrice : ""));
        this._logTick({ orderId, status: o.status, filledPrice: o.filledPrice, filledQuantity: o.filledQuantity });
        return;
      }
    }
  }

  /**
   * 止损止盈检查
   * 返回 'sell' 表示触发了风控卖出，null 表示正常
   */
  async _checkStopLossProfit(symbol) {
    const risk = this.strategy.risk || {};
    if (!risk.stopLoss && !risk.stopProfit) return null;

    try {
      const positions = await this.tradeService.getPositions();
      if (!positions.ok) return null;

      const pos = (positions.data || []).find((p) => p.symbol === symbol);
      if (!pos || pos.quantity <= 0) return null;

      const pnlPct = pos.pnlPct || 0;

      // 止损
      if (risk.stopLoss && pnlPct < 0 && Math.abs(pnlPct) >= risk.stopLoss * 100) {
        log("止损触发: " + symbol + " pnlPct=" + pnlPct.toFixed(2) + "%");
        await this._executeSignal(symbol, { action: "sell", reason: "止损" }, pos.currentPrice, {});
        return "stopLoss";
      }

      // 止盈
      if (risk.stopProfit && pnlPct > 0 && pnlPct >= risk.stopProfit * 100) {
        log("止盈触发: " + symbol + " pnlPct=" + pnlPct.toFixed(2) + "%");
        await this._executeSignal(symbol, { action: "sell", reason: "止盈" }, pos.currentPrice, {});
        return "stopProfit";
      }
    } catch (e) {
      log("止损止盈检查异常: " + e.message);
    }

    return null;
  }

  /**
   * 异常熔断：连续错误超过阈值则停止策略
   */
  _checkCircuitBreaker() {
    const risk = this.strategy.risk || {};
    const maxErrors = risk.maxConsecutiveErrors || 5;
    if (this._consecutiveErrors >= maxErrors) {
      log("异常熔断: 连续 " + this._consecutiveErrors + " 次错误，停止策略");
      this.status = "error";
      this._error = "异常熔断: 连续 " + this._consecutiveErrors + " 次错误";
      this.stop();
    }
  }

  _logTick(record) {
    try {
      this._log.logStrategyTick(this.strategy.id, {
        ...record,
        tickCount: this._tickCount,
        status: this.status,
      });
    } catch {}
  }

  async _updateStatus(status) {
    try {
      await this.strategyService.update(this.strategy.id, { status, symbols: this.symbols });
    } catch (e) {
      log("更新策略状态失败: " + e.message);
    }
  }

  getStatus() {
    const scheduler = this._getScheduler && this._getScheduler();
    return {
      strategyId: this.strategy.id,
      strategyName: this.strategy.name,
      type: this.strategy.type,
      symbols: this.symbols,
      status: this.status,
      lastTick: this._lastTick,
      lastSignal: this._lastSignal,
      tickCount: this._tickCount,
      pollCount: this._pollCount,
      error: this._error,
      marketState: this._marketState,
      marketLabel: this._marketState === "holiday" ? "非交易日" : this._marketState ? stateLabel(this._marketState) : null,
      lastSkip: this._lastSkip,
      schedule: this._plan
        ? {
            kind: this._plan.kind,
            label: this._plan.label,
            next: this._nextFire,
            lastFireKey: this._lastFireKey,
            lastFireAt: this._lastFireAt,
          }
        : null,
      schedulerState: scheduler ? scheduler.state : null,
    };
  }

  dispose() {
    this.stop();
  }
}

class ExecutorService {
  constructor({ strategyService, quoteService, tradeService, strategyBridge, modelService, autoTrade, tradingCalendar }) {
    this.strategyService = strategyService;
    this.quoteService = quoteService;
    this.tradeService = tradeService;
    this.strategyBridge = strategyBridge;
    this.modelService = modelService;
    this._autoTrade = autoTrade || {};
    this._tradingCalendar = tradingCalendar || null;
    this._scheduler = null;
    this._executors = new Map();
    this._lastTradingEvent = null;
    this._preflight = null;
  }

  /**
   * 挂载调度器（执行器每次 tick 都要向它确认「今天此刻是否放行」）
   * 用 setter 注入而非构造注入，避免调度器与执行器循环依赖。
   */
  setScheduler(scheduler) {
    this._scheduler = scheduler;
  }

  async start(strategyId, symbols) {
    if (this._executors.has(strategyId)) {
      return { ok: true, data: this._executors.get(strategyId).getStatus() };
    }

    const strategy = await this.strategyService.getById(strategyId);
    if (!strategy) {
      return { ok: false, error: "策略不存在: " + strategyId };
    }

    // normalize symbols: accept "600036,000001" string or array
    if (typeof symbols === "string") {
      symbols = symbols.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (!symbols || !symbols.length) {
      return { ok: false, error: "请先指定执行标的" };
    }

    const executor = new StrategyExecutor({
      strategy,
      symbols,
      quoteService: this.quoteService,
      tradeService: this.tradeService,
      strategyService: this.strategyService,
      strategyBridge: this.strategyBridge,
      modelService: this.modelService,
      autoTrade: this._autoTrade,
      tradingCalendar: this._tradingCalendar,
      getScheduler: () => this._scheduler,
    });
    executor.start();
    this._executors.set(strategyId, executor);

    return { ok: true, data: executor.getStatus() };
  }

  stop(strategyId) {
    const executor = this._executors.get(strategyId);
    if (!executor) {
      return { ok: false, error: "策略未在运行" };
    }
    executor.stop();
    this._executors.delete(strategyId);
    return { ok: true, data: { strategyId, status: "stopped" } };
  }

  getStatus(strategyId) {
    const executor = this._executors.get(strategyId);
    if (!executor) {
      return { ok: true, data: { strategyId, status: "stopped" } };
    }
    return { ok: true, data: executor.getStatus() };
  }

  listRunning() {
    return {
      ok: true,
      data: Array.from(this._executors.values()).map((e) => e.getStatus()),
    };
  }

  async restoreRunning() {
    try {
      const strategies = await this.strategyService.list();
      const running = strategies.filter((s) => s.status === "running");
      for (const s of running) {
        log("恢复策略: " + s.name + " (" + s.id + ")");
        await this.start(s.id, s.symbols);
      }
      if (running.length > 0) {
        log("已恢复 " + running.length + " 个策略");
      }
    } catch (e) {
      log("恢复策略失败: " + e.message);
    }
  }

  /**
   * 调度器事件入口（盘前检查 / 日终结算等"整点动作"挂在这里）
   * 交易动作不在此处触发：执行器自己按策略周期调度，这里只做健康检查与收尾。
   * @param {string} name 事件名（pre_open / market_open / lunch_break / market_resume / tail / settle / day_end）
   * @param {object} payload 事件详情
   */
  async onTradingEvent(name, payload) {
    this._lastTradingEvent = { name, ...payload };
    log("调度事件: " + name + (payload && payload.catchUp ? "（补跑）" : ""));
    if (name === "pre_open") return this.preflight();
    if (name === "settle") {
      // P1 会在这里生成日终报告；当前先落一条运行快照，便于事后核对
      log("日终快照: 运行中策略 " + this._executors.size + " 个");
      for (const e of this._executors.values()) {
        const st = e.getStatus();
        log("  - " + st.strategyName + " tick=" + st.tickCount + " 信号=" + (st.lastSignal ? st.lastSignal.action : "无"));
      }
    }
    return { ok: true };
  }

  /**
   * 盘前体检：行情源 + 交易账户 + 交易日历
   * 只读，不产生委托；失败不抛错（盘前不通就如实记录，不影响开盘后重试）。
   */
  async preflight() {
    const result = {
      at: new Date().toISOString(),
      tradingDay: this._tradingCalendar ? this._tradingCalendar.isTradingDay(new Date()) : null,
      calendarCalibrated: this._tradingCalendar ? this._tradingCalendar.isCalibrated(new Date()) : null,
      quote: null,
      account: null,
      strategies: this._executors.size,
      ok: false,
    };

    try {
      if (this.quoteService && typeof this.quoteService.getStatus === "function") {
        const q = await this.quoteService.getStatus();
        result.quote = { mode: q.mode, connected: !!(q.connected ?? q.ok), error: q.error || null };
      }
    } catch (e) {
      result.quote = { connected: false, error: e.message };
    }

    try {
      const acct = await this.tradeService.getAccount();
      result.account = acct.ok
        ? { totalAssets: acct.data.totalAssets, available: acct.data.available, error: null }
        : { error: acct.error };
    } catch (e) {
      result.account = { error: e.message };
    }

    result.ok = !!(result.quote && result.quote.connected) && !!(result.account && !result.account.error);
    this._preflight = result;
    log("盘前体检: " + JSON.stringify(result));
    return result;
  }

  getPreflight() {
    return this._preflight;
  }

  dispose() {
    for (const executor of this._executors.values()) {
      executor.dispose();
    }
    this._executors.clear();
  }
}

module.exports = { ExecutorService, StrategyExecutor, toLot };
