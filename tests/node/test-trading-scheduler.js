/**
 * 交易调度器测试
 *
 * 覆盖：五时点事件序列、去重、重启补跑、非交易日、放行边界、跨日重置、异常隔离。
 * 用假日历 + 手动 tickAt 驱动，不依赖真实时间与网络。
 */
const assert = require("assert");
const { TradingScheduler, EVENTS } = require("../../main/services/trading-scheduler");

/** 假日历：周末休市 */
function fakeCalendar(opts = {}) {
  return {
    isTradingDay: (d) => (opts.tradingDay != null ? opts.tradingDay : d.getDay() !== 0 && d.getDay() !== 6),
    isCalibrated: () => opts.calibrated !== false,
    ensureLoaded: async () => ({ ok: true, calibrated: true, source: "fake" }),
    getStatus: () => ({ loaded: true, tradingDays: 242, source: "fake" }),
  };
}

function makeScheduler(opts = {}) {
  const events = [];
  const sched = new TradingScheduler({
    calendar: opts.calendar || fakeCalendar(opts),
    autoTick: false,
    onEvent: (name, payload) => {
      events.push({ name, payload });
      if (opts.onEvent) return opts.onEvent(name, payload);
      return undefined;
    },
  });
  return { sched, events };
}

/** 2026-11-25 是周三 */
const at = (s) => new Date("2026-11-25T" + s);

module.exports = {
  name: "交易调度器",
  cases: [
    {
      name: "一天的时间轴：六个事件按时点依次触发",
      fn() {
        const { sched, events } = makeScheduler();
        const seq = ["09:00:00", "09:15:00", "09:29:00", "09:30:00", "11:30:00", "13:00:00", "14:55:00", "15:05:00", "15:30:00"];
        for (const t of seq) sched.tickAt(at(t));
        assert.deepStrictEqual(
          events.map((e) => e.name),
          ["pre_open", "market_open", "lunch_break", "market_resume", "tail", "settle", "day_end"]
        );
      },
    },
    {
      name: "同一时点重复 tick 不会重复发事件",
      fn() {
        const { sched, events } = makeScheduler();
        for (let i = 0; i < 10; i++) sched.tickAt(at("10:00:" + String(i * 5).padStart(2, "0")));
        assert.strictEqual(events.length, 1, "实际 " + events.length + " 次");
        assert.strictEqual(events[0].name, "market_open");
      },
    },
    {
      name: "进程重启补跑：09:40 启动补 market_open，且标记 catch-up",
      fn() {
        const { sched, events } = makeScheduler();
        sched.tickAt(at("09:40:00"));
        assert.strictEqual(events.length, 1);
        assert.strictEqual(events[0].name, "market_open");
        assert.strictEqual(events[0].payload.catchUp, true);
      },
    },
    {
      name: "进程重启补跑：12:10 启动只补 lunch_break",
      fn() {
        const { sched, events } = makeScheduler();
        sched.tickAt(at("12:10:00"));
        assert.deepStrictEqual(events.map((e) => e.name), ["lunch_break"]);
      },
    },
    {
      name: "非交易日不发任何事件",
      fn() {
        const { sched, events } = makeScheduler({ tradingDay: false });
        for (const t of ["09:15:00", "09:30:00", "14:55:00", "15:05:00"]) sched.tickAt(at(t));
        assert.strictEqual(events.length, 0);
        assert.strictEqual(sched.state, "idle");
      },
    },
    {
      name: "盘前/盘后不触发事件，状态保持 idle",
      fn() {
        const { sched, events } = makeScheduler();
        sched.tickAt(at("08:50:00"));
        assert.strictEqual(sched.state, "idle");
        assert.strictEqual(events.length, 0);
        sched.tickAt(at("16:00:00"));
        assert.strictEqual(sched.state, "idle");
      },
    },
    {
      name: "放行边界：开盘才放行，15:00 后收盘不再放行",
      fn() {
        const { sched } = makeScheduler();
        const allowed = (t) => {
          sched.tickAt(at(t));
          return sched.canPlaceOrder(at(t));
        };
        assert.strictEqual(allowed("09:29:00"), false, "集合竞价前");
        assert.strictEqual(allowed("09:30:00"), true, "开盘放行");
        assert.strictEqual(allowed("11:29:00"), true, "上午盘中");
        assert.strictEqual(allowed("11:30:00"), false, "午休不放行");
        assert.strictEqual(allowed("13:00:00"), true, "下午复盘");
        assert.strictEqual(allowed("14:58:00"), true, "尾盘放行");
        assert.strictEqual(allowed("15:01:00"), false, "收盘后不放行（尾盘窗口也不能越过收盘）");
        assert.strictEqual(allowed("15:10:00"), false, "结算窗口不放行");
      },
    },
    {
      name: "跨日自动重置，次日事件照常触发",
      fn() {
        const { sched, events } = makeScheduler();
        sched.tickAt(at("14:55:00"));
        assert.deepStrictEqual(events.map((e) => e.name), ["tail"]);
        sched.tickAt(new Date("2026-11-26T09:15:30"));
        assert.deepStrictEqual(events.map((e) => e.name), ["tail", "pre_open"]);
      },
    },
    {
      name: "事件处理器抛错不影响调度继续",
      fn() {
        const { sched, events } = makeScheduler({
          onEvent: () => {
            throw new Error("boom");
          },
        });
        sched.tickAt(at("09:15:00"));
        sched.tickAt(at("09:30:00"));
        assert.strictEqual(events.length, 2);
        assert.ok(/boom/.test(sched.getStatus().lastError || ""), "异常应记入 lastError");
      },
    },
    {
      name: "nextEvent 给出下一时点与剩余分钟",
      fn() {
        const { sched } = makeScheduler();
        sched.tickAt(at("08:00:00"));
        const n = sched.nextEvent(at("08:00:00"));
        assert.strictEqual(n.name, "pre_open");
        assert.strictEqual(n.at, "09:15");
        assert.strictEqual(n.inMinutes, 75);

        sched.tickAt(at("15:10:00"));
        const n2 = sched.nextEvent(at("15:10:00"));
        assert.strictEqual(n2.name, "day_end");
        assert.strictEqual(n2.at, "15:30");
      },
    },
    {
      name: "getStatus 反映当日已触发事件与状态标签",
      fn() {
        const { sched } = makeScheduler();
        sched.tickAt(at("10:00:00"));
        const st = sched.getStatus();
        assert.strictEqual(st.state, "open");
        assert.strictEqual(st.stateLabel, "交易中");
        assert.deepStrictEqual(st.todayFired, ["market_open"]);
        assert.strictEqual(st.isTradingDay, true);
        assert.strictEqual(st.calibrated, true);
        assert.strictEqual(st.lastEvent.name, "market_open");
      },
    },
    {
      name: "EVENTS 常量为深拷贝，外部修改不污染调度逻辑",
      fn() {
        const copy = TradingScheduler.EVENTS;
        assert.strictEqual(copy.length, EVENTS.length);
        copy[0].at = "00:00";
        assert.strictEqual(EVENTS[0].at, "09:15");
      },
    },
  ],
};
