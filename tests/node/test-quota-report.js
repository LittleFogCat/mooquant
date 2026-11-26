/**
 * 额度守门 + 日终报告测试
 *
 * 额度：验证「先停开仓、保留平仓」的分级策略，以及用尽后全部拦下。
 * 报告：验证只读聚合、接口失败也出报告、净值计算与落盘。
 *
 * 注意：两处都必须替换掉 LogService 与报告目录，否则会写真实 data/ 文件。
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { TradeService } = require("../../main/services/trade-service");
const { ReportService } = require("../../main/services/report-service");

const REPORT_DIR = path.join(__dirname, "..", "..", "data", "reports");
const TEST_DATE = "1990-01-02"; // 用一个绝不会真实使用的日期，测完删除

/** 交易数据源桩：记录被调用次数 */
function makeSource(opts = {}) {
  const state = { calls: 0, orderSeq: 0, failAccount: !!opts.failAccount };
  const src = {
    mode: "mock",
    get state() {
      return state;
    },
    getAccount: async () => {
      state.calls++;
      if (state.failAccount) throw new Error("账户接口挂了");
      return { totalAssets: 1000000, available: 900000, marketValue: 1100, totalPnl: 1200 };
    },
    getPositions: async () => {
      state.calls++;
      return [{ symbol: "sz000001", name: "平安银行", quantity: 100, costPrice: 10, currentPrice: 11, marketValue: 1100, pnl: 100, pnlPct: 10 }];
    },
    getOrders: async () => {
      state.calls++;
      return [];
    },
    placeOrder: async () => {
      state.calls++;
      return { orderId: "od_" + ++state.orderSeq, status: "pending" };
    },
    cancelOrder: async () => {
      state.calls++;
      return { success: true };
    },
  };
  return src;
}

/** 内存版 LogService：不写盘，只收集 */
function stubLog() {
  const records = [];
  return {
    records,
    logTrade(r) {
      records.push(r);
    },
    readTrades() {
      return records;
    },
    readStrategyLogs() {
      return [];
    },
    readEquity() {
      return [];
    },
  };
}

function makeTradeService(quota, source) {
  const ts = new TradeService({ source: source || makeSource(), quota });
  ts._log = stubLog();
  return ts;
}

module.exports = {
  name: "额度守门与日终报告",
  cases: [
    {
      name: "未配置预算时不限额度",
      async fn() {
        const ts = makeTradeService({});
        await ts.getAccount();
        const q = ts.getQuota();
        assert.strictEqual(q.limit, 0);
        assert.strictEqual(q.openAllowed, true);
        const r = await ts.placeOrder({ symbol: "sz000001", side: "buy", quantity: 100, orderType: "market" });
        assert.strictEqual(r.ok, true);
      },
    },
    {
      name: "额度紧张时先停开仓、保留平仓",
      async fn() {
        const src = makeSource();
        const ts = makeTradeService({ dailyCallBudget: 5, reserveForStopLoss: 2 }, src);
        // 用掉 3 次（= 预算 - 预留），此后禁止开仓
        await ts.getAccount();
        await ts.getPositions();
        await ts.getOrders();
        const q = ts.getQuota();
        assert.strictEqual(q.used, 3);
        assert.strictEqual(q.openAllowed, false, "已到开仓阈值");
        assert.strictEqual(q.closeAllowed, true, "平仓仍允许");

        const buy = await ts.placeOrder({ symbol: "sz000001", side: "buy", quantity: 100, orderType: "market" });
        assert.strictEqual(buy.ok, false);
        assert.ok(/禁止开仓|额度/.test(buy.error), "错误信息应说明额度原因：" + buy.error);
        assert.ok(ts._log.records.some((r) => r.type === "rejected"), "拒单必须留痕");

        const sell = await ts.placeOrder({ symbol: "sz000001", side: "sell", quantity: 100, orderType: "market" });
        assert.strictEqual(sell.ok, true, "平仓应放行");
      },
    },
    {
      name: "额度用尽后全部拦下",
      async fn() {
        const src = makeSource();
        const ts = makeTradeService({ dailyCallBudget: 3, reserveForStopLoss: 1 }, src);
        await ts.getAccount();
        await ts.getAccount();
        await ts.getAccount();
        assert.strictEqual(ts.getQuota().closeAllowed, false);
        const sell = await ts.placeOrder({ symbol: "sz000001", side: "sell", quantity: 100, orderType: "market" });
        assert.strictEqual(sell.ok, false);
        assert.ok(/额度已用尽/.test(sell.error), sell.error);
      },
    },
    {
      name: "读数调用也计入额度（失败同样计数）",
      async fn() {
        const src = makeSource({ failAccount: true });
        const ts = makeTradeService({ dailyCallBudget: 10, reserveForStopLoss: 0 }, src);
        const r = await ts.getAccount();
        assert.strictEqual(r.ok, false);
        assert.strictEqual(ts.getQuota().used, 1, "失败的请求也消耗额度");
      },
    },
    {
      name: "优先用数据源自报的真实调用数（缓存命中不扣额度）",
      async fn() {
        const src = makeSource();
        // 模拟东财适配器：真实 HTTP 调用 4 次，但服务层被调用了 1 次（其余走 5s 缓存）
        src.getCallStats = () => ({ date: "2026-11-25", calls: 4, cacheEntries: 2 });
        const ts = makeTradeService({ dailyCallBudget: 300, reserveForStopLoss: 100 }, src);
        await ts.getAccount();
        const q = ts.getQuota();
        assert.strictEqual(q.source, "adapter", "应使用数据源计数");
        assert.strictEqual(q.used, 4, "应按真实调用数计，而不是服务层调用数");
        assert.strictEqual(q.openAllowed, true);
      },
    },
    {
      name: "日终报告：账户/持仓/成交/信号/额度齐全，净值可算",
      async fn() {
        const src = makeSource();
        const ts = makeTradeService({ dailyCallBudget: 300, reserveForStopLoss: 100 }, src);
        // 造两条成交流水
        await ts.placeOrder({ symbol: "sz000001", side: "buy", quantity: 100, orderType: "market", strategyId: "st_x" });
        await ts.placeOrder({ symbol: "sz000001", side: "sell", quantity: 100, orderType: "market" });

        const rs = new ReportService({
          tradeService: ts,
          logService: ts._log, // 复用内存版日志，避免读真实 data/trades
          getExecutorStatuses: () => [
            {
              strategyId: "st_x",
              strategyName: "演示",
              status: "running",
              tickCount: 2,
              pollCount: 80,
              lastSkip: "等待调度时点（日线 09:31 / 14:55）",
              lastSignal: { action: "hold" },
              schedule: { label: "日线 09:31 / 14:55", lastFireKey: TEST_DATE + "#09:31" },
            },
          ],
        });
        const r = await rs.generate(TEST_DATE);
        assert.strictEqual(r.ok, true, r.error);
        const rep = r.data;
        assert.strictEqual(rep.date, TEST_DATE);
        assert.strictEqual(rep.orders, 2, "成交笔数");
        assert.strictEqual(rep.signals, 2, "信号次数来自执行器 tickCount");
        assert.strictEqual(rep.account.totalAssets, 1000000);
        assert.ok(Math.abs(rep.account.nav - 1000000 / 998800) < 1e-6, "净值 = 总资产 / (总资产 - 盈亏)");
        assert.strictEqual(rep.positions.length, 1);
        assert.strictEqual(rep.strategies.length, 1);
        assert.strictEqual(rep.skips.length, 1, "跳过原因要进报告");
        assert.strictEqual(rep.quota.limit, 300);
        assert.deepStrictEqual(rep.errors, []);

        // 落盘可读回，且列表里能看到
        const back = rs.read(TEST_DATE);
        assert.ok(back && back.date === TEST_DATE);
        assert.ok(rs.list(5).some((x) => x.date === TEST_DATE));

        fs.unlinkSync(path.join(REPORT_DIR, TEST_DATE + ".json"));
      },
    },
    {
      name: "接口失败也照样出报告，并把原因写进 errors",
      async fn() {
        const src = makeSource({ failAccount: true });
        const ts = makeTradeService({}, src);
        const rs = new ReportService({ tradeService: ts, getExecutorStatuses: () => [] });
        const r = await rs.generate(TEST_DATE);
        assert.strictEqual(r.ok, true, "报告不能因为接口失败而缺失");
        assert.strictEqual(r.data.account, null);
        assert.ok(r.data.errors.some((e) => /账户/.test(e)), JSON.stringify(r.data.errors));
        fs.unlinkSync(path.join(REPORT_DIR, TEST_DATE + ".json"));
      },
    },
    {
      name: "盘中预览：反映当日成交与信号，且不调账户/持仓接口",
      async fn() {
        const src = makeSource();
        const ts = makeTradeService({ dailyCallBudget: 300, reserveForStopLoss: 100 }, src);
        await ts.placeOrder({ symbol: "sz000001", side: "buy", quantity: 100, orderType: "market" });
        const callsBefore = src.state.calls;

        const rs = new ReportService({
          tradeService: ts,
          logService: ts._log,
          getExecutorStatuses: () => [{ strategyId: "s", strategyName: "x", status: "running", tickCount: 1, pollCount: 10 }],
        });
        // 用 preview() 而不是 today()：today() 会在报告已生成时返回缓存，结果依赖运行环境
        const view = await rs.preview();
        assert.strictEqual(view.generated, false, "预览不应标记为已生成");
        assert.strictEqual(view.orders, 1, "预览也要能反映当日成交");
        assert.strictEqual(view.signals, 1);
        assert.strictEqual(view.account, null, "预览不查账户");
        assert.strictEqual(src.state.calls, callsBefore, "预览不能额外消耗交易接口额度");
      },
    },
    {
      name: "日报用实时委托回填终态（本地流水只有下单瞬间的 pending）",
      async fn() {
        const src = makeSource();
        const ts = makeTradeService({ dailyCallBudget: 300, reserveForStopLoss: 100 }, src);
        await ts.placeOrder({ symbol: "sz000001", side: "buy", quantity: 100, orderType: "market" });
        assert.strictEqual(ts._log.records[0].status, "pending", "流水记的是下单瞬间状态");

        // 真实委托已成交
        src.getOrders = async () => [
          { orderId: "od_1", status: "filled", filledQuantity: 100, filledPrice: 7.95 },
        ];
        const rs = new ReportService({ tradeService: ts, logService: ts._log, getExecutorStatuses: () => [] });
        const r = await rs.generate(TEST_DATE);
        assert.strictEqual(r.data.trades[0].status, "filled", "必须回填成终态，否则日报把成交记成待成交");
        assert.strictEqual(r.data.trades[0].filledPrice, 7.95);
        assert.strictEqual(r.data.ordersReconciled, 1);
        assert.strictEqual(r.data.filled, 1);

        fs.unlinkSync(path.join(REPORT_DIR, TEST_DATE + ".json"));
      },
    },
    {
      name: "无委托时不额外查委托（省额度）",
      async fn() {
        const src = makeSource();
        const ts = makeTradeService({ dailyCallBudget: 300 }, src);
        const rs = new ReportService({ tradeService: ts, logService: ts._log, getExecutorStatuses: () => [] });
        const before = src.state.calls;
        const r = await rs.generate(TEST_DATE);
        assert.strictEqual(r.data.orders, 0);
        assert.strictEqual(r.data.ordersReconciled, undefined, "没有委托就不该做回填");
        // 账户 + 持仓 = 2 次；没有第 3 次查委托
        assert.strictEqual(src.state.calls - before, 2, "实际多调用了 " + (src.state.calls - before) + " 次");
        fs.unlinkSync(path.join(REPORT_DIR, TEST_DATE + ".json"));
      },
    },
  ],
};
