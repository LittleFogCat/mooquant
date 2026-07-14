/**
 * mookquant · 交易数据源抽象层
 *
 * 接口：
 *   class TradeDataSource {
 *     mode: string
 *     description: string
 *     async placeOrder(order): { orderId, status }
 *     async cancelOrder(orderId): { success }
 *     async getPositions(): [position]
 *     async getOrders(): [order]
 *     async getAccount(): { totalAssets, available, marketValue, totalPnl }
 *     dispose(): void
 *   }
 */

const { MockTradeDataSource } = require("./mock-trade");

async function createTradeDataSource(opts) {
  const mode = opts.mode || "mock";

  if (mode === "mock") {
    return new MockTradeDataSource();
  }

  if (mode === "qmt") {
    const { QmtTradeDataSource } = require("./qmt-trade");
    const source = new QmtTradeDataSource(opts.qmt || {});
    await source.init();
    return source;
  }

  if (mode === "auto") {
    const { QmtTradeDataSource } = require("./qmt-trade");
    try {
      const qmt = new QmtTradeDataSource(opts.qmt || {});
      await qmt.init();
      console.log("[trade] 使用 QMT 交易数据源");
      return qmt;
    } catch (e) {
      console.warn("[trade] QMT 交易不可用，回落到 mock:", e.message);
      return new MockTradeDataSource();
    }
  }

  throw new Error("未知交易数据源模式: " + mode);
}

module.exports = { createTradeDataSource };
