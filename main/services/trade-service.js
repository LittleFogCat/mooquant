/**
 * mookquant · Trade Service（主进程）
 *
 * 交易编排：下单/撤单/查询 + 下单前风控 + 交易记录持久化。
 */

const { LogService } = require("./log-service");

class TradeService {
  constructor({ source }) {
    this.source = source;
    this._log = new LogService();
    this._dailyTradeCount = {}; // dateStr -> count
  }

  _todayKey() {
    return new Date().toISOString().slice(0, 10);
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
        const account = await this.source.getAccount();
        const positions = await this.source.getPositions();
        const totalAssets = account.totalAssets || account.totalAssets || 0;
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
      const result = await this.source.placeOrder(order);

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
      const result = await this.source.cancelOrder(orderId);
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
      const data = await this.source.getPositions();
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  async getOrders() {
    try {
      const data = await this.source.getOrders();
      return { ok: true, data };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  async getAccount() {
    try {
      const data = await this.source.getAccount();
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
