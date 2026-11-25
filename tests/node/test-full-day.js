/**
 * 全流程模拟：一整个交易日（无 GUI、无网络）
 *
 * 目的：把「交易日历 → 调度器五时点 → 执行器三道闸门 → 策略周期」串起来跑一遍，
 * 验证的是真实模块之间的接线，而不是单个函数的返回值。
 *
 * 做法：劫持全局 Date 制造虚拟时钟，从 08:50 走到 15:30，每 15s 步进：
 *   scheduler.tickAt(now)  → 触发时点事件
 *   executor.tick()        → 走完整 tick 路径（闸门 + 取行情 + 算信号）
 * 行情/账户/信号全部用桩，因此不产生任何接口调用与文件写入。
 */
const assert = require("assert");
const path = require("path");
const { TradingCalendar } = require("../../main/services/trading-calendar");
const { TradingScheduler } = require("../../main/services/trading-scheduler");
const { ExecutorService } = require("../../main/services/executor-service");

const DAY = "2026-11-25"; // 周三

/** 60 根合成日线，价格平稳（策略桩不依赖具体数值） */
function fakeBars(n = 60) {
  const bars = [];
  for (let i = 0; i < n; i++) {
    bars.push({
      date: "2026-01-" + String((i % 28) + 1).padStart(2, "0"),
      open: 10,
      high: 10.2,
      low: 9.8,
      close: 10,
      volume: 1000000,
    });
  }
  return bars;
}

module.exports = {
  name: "全流程（模拟交易日）",
  cases: [
    {
      name: "08:50→15:30 全流程：事件齐全、日线策略只算 2 次、午休与收盘被拦",
      async fn() {
        // ---- 桩：只读、无副作用 ----
        const quoteService = {
          getStatus: async () => ({ mode: "fake", connected: true }),
          getHistory: async () => ({ ok: true, data: { bars: fakeBars(60) } }),
        };
        const tradeService = {
          getAccount: async () => ({ ok: true, data: { totalAssets: 1000000, available: 1000000 } }),
          getPositions: async () => ({ ok: true, data: [] }),
          getOrders: async () => ({ ok: true, data: [] }),
          placeOrder: async () => {
            throw new Error("本测试不应下单");
          },
        };
        const strategyService = {
          getById: async () => ({
            id: "st_sim",
            name: "模拟策略",
            type: "ma_cross",
            params: {},
            symbols: ["sz000001"],
            schedule: { period: "1d" },
            risk: {},
          }),
          update: async () => ({}),
        };
        const modelService = {
          computeSignal: async () => ({ ok: true, data: { action: "hold", reason: "模拟" } }),
        };

        const calendar = new TradingCalendar({ offline: true });
        await calendar.ensureLoaded(2026); // 有缓存就用缓存，没有则回落周一~周五

        const events = [];
        const executorService = new ExecutorService({
          strategyService,
          quoteService,
          tradeService,
          modelService,
          tradingCalendar: calendar,
          autoTrade: { enabled: true, tickMs: 15000 },
        });

        const scheduler = new TradingScheduler({
          calendar,
          autoTick: false,
          onEvent: (name, payload) => {
            events.push(name);
            return executorService.onTradingEvent(name, payload);
          },
        });
        executorService.setScheduler(scheduler);

        const strategy = await strategyService.getById();
        const { StrategyExecutor } = require("../../main/services/executor-service");
        const executor = new StrategyExecutor({
          strategy,
          symbols: strategy.symbols,
          quoteService,
          tradeService,
          strategyService,
          modelService,
          autoTrade: { enabled: true, tickMs: 15000 },
          tradingCalendar: calendar,
          getScheduler: () => scheduler,
        });
        executor._logTick = () => {}; // 不落盘
        executor._updateStatus = async () => {};

        // ---- 虚拟时钟 ----
        const RealDate = Date;
        let fakeNow = new RealDate(DAY + "T08:50:00");
        class FakeDate extends RealDate {
          constructor(...args) {
            if (args.length === 0) super(fakeNow.getTime());
            else super(...args);
          }
          static now() {
            return fakeNow.getTime();
          }
        }
        global.Date = FakeDate;

        try {
          const start = new RealDate(DAY + "T08:50:00").getTime();
          const end = new RealDate(DAY + "T15:30:00").getTime();
          const step = 15000;

          for (let t = start; t <= end; t += step) {
            fakeNow = new RealDate(t);
            scheduler.tickAt(fakeNow);
            await executor.tick();
          }
        } finally {
          global.Date = RealDate;
        }

        // ---- 断言 ----
        assert.deepStrictEqual(
          events,
          ["pre_open", "market_open", "lunch_break", "market_resume", "tail", "settle", "day_end"],
          "事件序列应为七个时点，实际：" + JSON.stringify(events)
        );
        assert.strictEqual(executor._tickCount, 2, "日线策略应只算 2 次信号，实际 " + executor._tickCount);
        assert.strictEqual(executor._pollCount, 1601, "定时器应被调用 1601 次（08:50→15:30 每 15s），实际 " + executor._pollCount);
        assert.strictEqual(executor._lastFireKey.startsWith(DAY + "#"), true, "触发 key 应带日期前缀");

        // 被闸门拦下的原因必须可解释（不是静默返回）
        assert.ok(executor._lastSkip, "最后一次 tick 应记录跳过原因");
        assert.ok(/非交易时段|调度器未放行|等待调度时点/.test(executor._lastSkip), "跳过原因：" + executor._lastSkip);

        // 收盘后不允许下单
        assert.strictEqual(scheduler.canPlaceOrder(new RealDate(DAY + "T15:01:00")), false);
        assert.strictEqual(scheduler.canPlaceOrder(new RealDate(DAY + "T10:00:00")), true);

        // 盘前体检落到了 ExecutorService 上
        const pre = executorService.getPreflight();
        assert.ok(pre && pre.ok, "盘前体检应成功（桩数据）");
        assert.strictEqual(pre.quote.connected, true);
        assert.strictEqual(pre.account.totalAssets, 1000000);
      },
    },
    {
      name: "非交易日整天不触发任何事件",
      async fn() {
        const calendar = new TradingCalendar({ offline: true });
        await calendar.ensureLoaded(2026);
        const events = [];
        const scheduler = new TradingScheduler({
          calendar,
          autoTick: false,
          onEvent: (name) => events.push(name),
        });
        // 2026-11-28 是周六
        const RealDate = Date;
        let fakeNow = new RealDate("2026-11-28T09:00:00");
        class FakeDate extends RealDate {
          constructor(...a) {
            if (a.length === 0) super(fakeNow.getTime());
            else super(...a);
          }
          static now() {
            return fakeNow.getTime();
          }
        }
        global.Date = FakeDate;
        try {
          for (let t = 0; t <= 6 * 3600 * 1000; t += 60000) {
            fakeNow = new RealDate(new RealDate("2026-11-28T09:00:00").getTime() + t);
            scheduler.tickAt(fakeNow);
          }
        } finally {
          global.Date = RealDate;
        }
        assert.deepStrictEqual(events, [], "周末不应有任何事件");
        assert.strictEqual(scheduler.state, "idle");
        assert.strictEqual(scheduler.canPlaceOrder(new RealDate("2026-11-28T10:00:00")), false);
      },
    },
  ],
};
