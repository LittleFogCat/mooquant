/**
 * mookquant · 策略执行引擎
 *
 * 职责：
 *  1. 管理运行中的策略实例（启动/停止/状态查询）
 *  2. 定时调度：风控检查 -> 拉行情 -> 算信号 -> 自动下单
 *  3. 运行时风控：止损止盈、异常熔断
 *  4. 执行日志持久化 + 每日净值记录
 */

const { generateSignal } = require("../strategies/ma_cross");
const { LogService } = require("./log-service");

const SIGNAL_FUNCTIONS = {
  ma_cross: generateSignal,
};

function log(msg) {
  console.log("[executor] " + msg);
}

class StrategyExecutor {
  constructor({ strategy, symbols, quoteService, tradeService, strategyService }) {
    this.strategy = strategy;
    this.symbols = symbols || [];
    this.quoteService = quoteService;
    this.tradeService = tradeService;
    this.strategyService = strategyService;
    this._log = new LogService();
    this._timer = null;
    this.status = "stopped";
    this._lastTick = null;
    this._lastSignal = null;
    this._tickCount = 0;
    this._error = null;
    this._consecutiveErrors = 0;
  }

  start() {
    if (this.status === "running") return;
    this.status = "running";
    this._error = null;
    this._consecutiveErrors = 0;
    this._updateStatus("running");
    log("启动策略: " + this.strategy.name + " (" + this.strategy.id + ")");

    this.tick();
    const intervalMs = (this.strategy.schedule && this.strategy.schedule.intervalMs) || 60000;
    this._timer = setInterval(() => this.tick(), intervalMs);
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
    this._tickCount++;
    this._lastTick = new Date().toISOString();

    try {
      const symbol = this.symbols[0];
      if (!symbol) {
        this._error = "策略未配置标的";
        return;
      }

      // 0. 运行时风控：止损止盈
      const riskAction = await this._checkStopLossProfit(symbol);
      if (riskAction) {
        this._logTick({ symbol, signal: { action: riskAction, reason: "风控触发" } });
        this._consecutiveErrors = 0;
        return;
      }

      // 1. 获取 K 线数据
      const histResult = await this.quoteService.getHistory(symbol, "1d", 60);
      if (!histResult.ok) {
        this._error = "获取行情失败: " + histResult.error;
        this._consecutiveErrors++;
        this._checkCircuitBreaker();
        return;
      }

      const bars = histResult.data.bars;
      if (!bars || bars.length === 0) {
        this._error = "无 K 线数据";
        return;
      }

      // 2. 计算信号
      const signalFn = SIGNAL_FUNCTIONS[this.strategy.type];
      if (!signalFn) {
        this._error = "未知策略类型: " + this.strategy.type;
        return;
      }

      const signal = signalFn(bars, this.strategy.params);
      this._lastSignal = signal;
      log("tick #" + this._tickCount + " " + symbol + " -> " + signal.action + " (" + signal.reason + ")");

      // 3. 获取风控参数，注入 order
      const risk = this.strategy.risk || {};
      const orderRisk = {
        maxOrderAmount: risk.maxOrderAmount,
        maxDailyTrades: risk.maxDailyTrades,
        maxPositionRatio: risk.maxPositionRatio,
      };

      // 4. 执行交易
      if (signal.action === "buy") {
        await this._executeBuy(symbol, bars[bars.length - 1].close, orderRisk);
      } else if (signal.action === "sell") {
        await this._executeSell(symbol, orderRisk);
      }

      // 5. 记录执行日志
      this._logTick({ symbol, signal, price: bars[bars.length - 1].close });
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

      const pos = positions.data.find((p) => p.symbol === symbol);
      if (!pos || pos.quantity <= 0) return null;

      const pnlPct = pos.pnlPct || 0;

      // 止损
      if (risk.stopLoss && pnlPct < 0 && Math.abs(pnlPct) >= risk.stopLoss * 100) {
        log("止损触发: " + symbol + " pnlPct=" + pnlPct.toFixed(2) + "%");
        await this._executeSell(symbol, {});
        return "stopLoss";
      }

      // 止盈
      if (risk.stopProfit && pnlPct > 0 && pnlPct >= risk.stopProfit * 100) {
        log("止盈触发: " + symbol + " pnlPct=" + pnlPct.toFixed(2) + "%");
        await this._executeSell(symbol, {});
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

  async _executeBuy(symbol, price, orderRisk) {
    const account = await this.tradeService.getAccount();
    if (!account.ok) {
      log("买入失败: 无法获取账户信息 - " + account.error);
      return;
    }

    const available = account.data.available;
    const maxAmount = available * 0.9;
    const qty = Math.floor(maxAmount / price / 100) * 100;

    if (qty <= 0) {
      log("买入跳过: 可用资金不足 (available=" + available + " price=" + price + ")");
      return;
    }

    const positions = await this.tradeService.getPositions();
    if (positions.ok) {
      const existing = positions.data.find((p) => p.symbol === symbol);
      if (existing && existing.quantity > 0) {
        log("买入跳过: 已有持仓 " + symbol + " (" + existing.quantity + ")");
        return;
      }
    }

    const result = await this.tradeService.placeOrder({
      symbol,
      side: "buy",
      quantity: qty,
      orderType: "market",
      risk: orderRisk,
      strategyId: this.strategy.id,
    });

    if (result.ok) {
      log("买入下单: " + symbol + " qty=" + qty + " orderId=" + result.data.orderId);
    } else {
      log("买入失败: " + result.error);
    }
  }

  async _executeSell(symbol, orderRisk) {
    const positions = await this.tradeService.getPositions();
    if (!positions.ok) {
      log("卖出失败: 无法获取持仓 - " + positions.error);
      return;
    }

    const pos = positions.data.find((p) => p.symbol === symbol);
    if (!pos || pos.quantity <= 0) {
      log("卖出跳过: 无持仓 " + symbol);
      return;
    }

    const result = await this.tradeService.placeOrder({
      symbol,
      side: "sell",
      quantity: pos.quantity,
      orderType: "market",
      risk: orderRisk,
      strategyId: this.strategy.id,
    });

    if (result.ok) {
      log("卖出下单: " + symbol + " qty=" + pos.quantity + " orderId=" + result.data.orderId);
    } else {
      log("卖出失败: " + result.error);
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
      await this.strategyService.update(this.strategy.id, { status });
    } catch (e) {
      log("更新策略状态失败: " + e.message);
    }
  }

  getStatus() {
    return {
      strategyId: this.strategy.id,
      strategyName: this.strategy.name,
      type: this.strategy.type,
      symbols: this.symbols,
      status: this.status,
      lastTick: this._lastTick,
      lastSignal: this._lastSignal,
      tickCount: this._tickCount,
      error: this._error,
    };
  }

  dispose() {
    this.stop();
  }
}

class ExecutorService {
  constructor({ strategyService, quoteService, tradeService }) {
    this.strategyService = strategyService;
    this.quoteService = quoteService;
    this.tradeService = tradeService;
    this._executors = new Map();
  }

  async start(strategyId, symbols) {
    if (this._executors.has(strategyId)) {
      return { ok: true, data: this._executors.get(strategyId).getStatus() };
    }

    const strategy = await this.strategyService.getById(strategyId);
    if (!strategy) {
      return { ok: false, error: "策略不存在: " + strategyId };
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
        await this.start(s.id);
      }
      if (running.length > 0) {
        log("已恢复 " + running.length + " 个策略");
      }
    } catch (e) {
      log("恢复策略失败: " + e.message);
    }
  }

  dispose() {
    for (const executor of this._executors.values()) {
      executor.dispose();
    }
    this._executors.clear();
  }
}

module.exports = { ExecutorService, StrategyExecutor };
