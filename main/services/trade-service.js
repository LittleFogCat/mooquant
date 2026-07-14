/**
 * mookquant · Trade Service（主进程）
 *
 * 交易编排：下单/撤单/查询 + 风控。
 */

class TradeService {
  constructor({ source }) {
    this.source = source;
  }

  async placeOrder(order) {
    // 基础风控
    if (!order.symbol || !order.symbol.trim()) {
      return { ok: false, error: "股票代码不能为空" };
    }
    if (!order.quantity || order.quantity <= 0) {
      return { ok: false, error: "数量必须大于 0" };
    }
    if (order.orderType === "limit" && (!order.price || order.price <= 0)) {
      return { ok: false, error: "限价单价格必须大于 0" };
    }

    try {
      const result = await this.source.placeOrder(order);
      return { ok: true, data: result };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  async cancelOrder(orderId) {
    try {
      const result = await this.source.cancelOrder(orderId);
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
