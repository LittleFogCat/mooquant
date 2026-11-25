/**
 * 交易日历测试
 *
 * 覆盖：兜底逻辑（离线/未校准）、节假日判定、手动覆盖、周末跳过、缓存读写。
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { TradingCalendar } = require("../../main/services/trading-calendar");

const CAL_DIR = path.join(__dirname, "..", "..", "data", "calendar");

/** 造一个「已加载 2026 年」的日历：10 月只把 10-09 当交易日，用来模拟国庆假期 */
function withStubbedYear(overrides) {
  const cal = new TradingCalendar({ offline: true, overrides });
  cal._years.set(2026, {
    year: 2026,
    months: { "10": ["2026-10-09"] },
    covered: new Set(["10"]),
    fetchedAt: Date.now(),
    source: "stub",
  });
  return cal;
}

module.exports = {
  name: "交易日历",
  cases: [
    {
      name: "离线兜底：周末不是交易日，工作日是，且标记未校准",
      fn() {
        const cal = new TradingCalendar({ offline: true });
        assert.strictEqual(cal.isTradingDay(new Date("2026-11-28T10:00:00")), false, "周六应为非交易日");
        assert.strictEqual(cal.isTradingDay(new Date("2026-11-29T10:00:00")), false, "周日应为非交易日");
        assert.strictEqual(cal.isTradingDay(new Date("2026-11-25T10:00:00")), true, "周三应为交易日");
        assert.strictEqual(cal.isCalibrated(new Date("2026-11-25T10:00:00")), false, "兜底模式不应声称已校准");
      },
    },
    {
      name: "节假日：已校准月份里不在列表中的工作日不是交易日",
      fn() {
        const cal = withStubbedYear();
        // 2026-10-01 周四，国庆假期
        assert.strictEqual(cal.isTradingDay("2026-10-01"), false);
        assert.strictEqual(cal.isTradingDay("2026-10-09"), true);
        assert.strictEqual(cal.isCalibrated("2026-10-01"), true);
        // 未校准月份（11 月）回落为「周一~周五」，且不声称已校准
        assert.strictEqual(cal.isTradingDay("2026-11-25"), true);
        assert.strictEqual(cal.isCalibrated("2026-11-25"), false);
      },
    },
    {
      name: "手动覆盖优先于日历与周末判断",
      fn() {
        const cal = withStubbedYear({ "2026-10-09": false, "2026-11-28": true });
        assert.strictEqual(cal.isTradingDay("2026-10-09"), false, "覆盖为休市");
        assert.strictEqual(cal.isTradingDay("2026-11-28"), true, "覆盖为交易（调休上班）");
        assert.strictEqual(cal.isCalibrated("2026-11-28"), true);
      },
    },
    {
      name: "nextTradingDay 跳过周末与节假日",
      fn() {
        const cal = withStubbedYear();
        assert.strictEqual(cal.nextTradingDay("2026-10-01"), "2026-10-09", "国庆后第一个交易日");
        assert.strictEqual(cal.nextTradingDay("2026-11-28"), "2026-11-30", "周六 → 下周一");
        assert.strictEqual(cal.nextTradingDay("2026-11-25"), "2026-11-25", "当天是交易日则返回当天");
      },
    },
    {
      name: "缓存写入/读取可往返",
      fn() {
        const year = 1990; // 用一个绝不会真实使用的年份，避免污染真实缓存
        const cal = new TradingCalendar({ offline: true });
        cal._writeCache({
          year,
          source: "test",
          fetchedAt: 1234567890,
          months: { "01": ["1990-01-02"] },
        });
        const back = cal._readCache(year);
        assert.ok(back, "缓存应可读回");
        assert.deepStrictEqual(back.months["01"], ["1990-01-02"]);
        assert.strictEqual(back.source, "test");
        assert.strictEqual(back.covered.has("01"), true);
        try {
          fs.unlinkSync(path.join(CAL_DIR, year + ".json"));
        } catch {}
      },
    },
    {
      name: "getStatus 在未加载时如实报告状态",
      fn() {
        const cal = new TradingCalendar({ offline: true });
        const s = cal.getStatus();
        assert.strictEqual(s.loaded, false);
        assert.strictEqual(s.tradingDays, 0);
        assert.strictEqual(s.todayCalibrated, false);
      },
    },
  ],
};
