/**
 * mookquant · Mock 交易数据源
 *
 * 内存模拟交易：维护账户、持仓、委托。
 * 用于开发测试和 QMT 不可用时的兜底。
 */

const crypto = require("crypto");

class MockTradeDataSource {
  constructor() {
    this.mode = "mock";
    this.description = "模拟交易（内存）";

    this._cash = 1000000;
    this._positions = new Map();   // symbol -> { symbol, name, quantity, costPrice, currentPrice }
    this._orders = [];
    this._orderIdSeq = 0;
  }

  _genOrderId() {
    return "od_" + (++this._orderIdSeq) + "_" + crypto.randomBytes(2).toString("hex");
  }

  async placeOrder(order) {
    const orderId = this._genOrderId();
    const now = new Date().toISOString();

    // 模拟撮合：立即成交
    const fillPrice = order.orderType === "market"
      ? this._mockPrice(order.symbol)
      : order.price;

    const record = {
      orderId,
      symbol: order.symbol,
      side: order.side,
      price: fillPrice,
      quantity: order.quantity,
      orderType: order.orderType,
      status: "filled",
      createdAt: now,
      filledAt: now,
    };
    this._orders.unshift(record);

    // 更新持仓
    if (order.side === "buy") {
      const cost = fillPrice * order.quantity;
      this._cash -= cost;
      const existing = this._positions.get(order.symbol);
      if (existing) {
        const totalQty = existing.quantity + order.quantity;
        existing.costPrice = (existing.costPrice * existing.quantity + fillPrice * order.quantity) / totalQty;
        existing.quantity = totalQty;
      } else {
        this._positions.set(order.symbol, {
          symbol: order.symbol,
          name: order.symbol,
          quantity: order.quantity,
          costPrice: fillPrice,
          currentPrice: fillPrice,
        });
      }
    } else {
      // sell
      const existing = this._positions.get(order.symbol);
      if (existing) {
        existing.quantity -= order.quantity;
        if (existing.quantity <= 0) {
          this._positions.delete(order.symbol);
        }
      }
      this._cash += fillPrice * order.quantity;
    }

    return { orderId, status: "filled" };
  }

  async cancelOrder(orderId) {
    const order = this._orders.find((o) => o.orderId === orderId);
    if (order && order.status === "pending") {
      order.status = "cancelled";
      return { success: true };
    }
    return { success: false, error: "订单不存在或不可撤" };
  }

  async getPositions() {
    return Array.from(this._positions.values()).map((p) => ({
      symbol: p.symbol,
      name: p.name,
      quantity: p.quantity,
      costPrice: p.costPrice,
      currentPrice: p.currentPrice,
      marketValue: p.quantity * p.currentPrice,
      pnl: (p.currentPrice - p.costPrice) * p.quantity,
      pnlPct: ((p.currentPrice - p.costPrice) / p.costPrice) * 100,
    }));
  }

  async getOrders() {
    return this._orders.slice(0, 50);
  }

  async getAccount() {
    const positions = await this.getPositions();
    const marketValue = positions.reduce((sum, p) => sum + p.marketValue, 0);
    const totalPnl = positions.reduce((sum, p) => sum + p.pnl, 0);
    return {
      totalAssets: this._cash + marketValue,
      available: this._cash,
      marketValue,
      totalPnl,
    };
  }

  _mockPrice(symbol) {
    let h = 0;
    for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0;
    return 10 + Math.abs(h % 9900) / 10;
  }

  dispose() {}
}

module.exports = { MockTradeDataSource };
