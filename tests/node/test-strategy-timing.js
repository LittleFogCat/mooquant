/**
 * 策略调度规则测试
 *
 * 覆盖：三类调度解析、日线时点去重与补跑、分钟 bar 收盘触发、interval 兼容、下次触发时间。
 */
const assert = require("assert");
const {
  resolveSchedule,
  evaluate,
  nextFireAt,
  normalizePoint,
  latestBarCloseSec,
} = require("../../main/services/strategy-timing");

const at = (s) => new Date("2026-11-25T" + s);  // 2026-11-25 是周三
const hhmmss = (s) => {
  const [h, m, sec] = s.split(":").map(Number);
  return h * 3600 + m * 60 + (sec || 0);
};

module.exports = {
  name: "策略调度规则",
  cases: [
    {
      name: "未配置 period 时沿用 intervalMs（兼容旧策略）",
      fn() {
        const p = resolveSchedule({});
        assert.strictEqual(p.kind, "interval");
        assert.strictEqual(p.intervalMs, 60000);
        assert.strictEqual(resolveSchedule({ intervalMs: 5000 }).intervalMs, 5000);
      },
    },
    {
      name: "period=1d 解析出固定时点，缺省 09:31 / 14:55",
      fn() {
        const p = resolveSchedule({ period: "1d" });
        assert.strictEqual(p.kind, "daily");
        assert.deepStrictEqual(p.points, ["09:31", "14:55"]);
        const custom = resolveSchedule({ period: "1d", at: ["9:35", "14:50"] });
        assert.deepStrictEqual(custom.points, ["09:35", "14:50"]);
        assert.deepStrictEqual(resolveSchedule({ period: "1d", at: ["bad", "10:00"] }).points, ["10:00"]);
      },
    },
    {
      name: "period=5m 解析出 bar 长度",
      fn() {
        const p = resolveSchedule({ period: "5m" });
        assert.strictEqual(p.kind, "bar");
        assert.strictEqual(p.barSec, 300);
      },
    },
    {
      name: "日线：09:31 与 14:55 各触发一次，中间不重复",
      fn() {
        const p = resolveSchedule({ period: "1d" });
        const fired = new Set();
        const dueAt = (s) => {
          const r = evaluate(p, at(s), fired);
          if (r.due) fired.add(r.key);
          return r.due;
        };
        assert.strictEqual(dueAt("09:00:00"), false, "开盘前不算");
        assert.strictEqual(dueAt("09:31:00"), true, "09:31 触发");
        assert.strictEqual(dueAt("09:31:15"), false, "同一时点不重复");
        assert.strictEqual(dueAt("10:30:00"), false, "上午不再触发");
        assert.strictEqual(dueAt("14:54:59"), false, "尾盘前不算");
        assert.strictEqual(dueAt("14:55:00"), true, "14:55 触发");
        assert.strictEqual(dueAt("15:00:00"), false, "当天已无时点");
        assert.strictEqual(fired.size, 2);
      },
    },
    {
      name: "日线补跑：15:00 重启只补一次（不重放两个时点）",
      fn() {
        const p = resolveSchedule({ period: "1d" });
        const fired = new Set();
        const first = evaluate(p, at("15:00:00"), fired);
        assert.strictEqual(first.due, true);
        assert.strictEqual(first.point, "14:55", "取已过的最新时点");
        fired.add(first.key);
        assert.strictEqual(evaluate(p, at("15:00:15"), fired).due, false);
        assert.strictEqual(fired.size, 1);
      },
    },
    {
      name: "1m bar：收盘后 5 秒才触发，午休与收盘边界正确",
      fn() {
        const p = resolveSchedule({ period: "1m" });
        const dueAt = (s) => evaluate(p, at(s), new Set()).key;
        assert.strictEqual(dueAt("09:30:30"), "", "第一根 bar 未收盘");
        assert.strictEqual(dueAt("09:31:03"), "", "收盘后 3s 还不算（延迟 5s）");
        assert.strictEqual(dueAt("09:31:06"), "2026-11-25#09:31", "收盘 5s 后触发");
        assert.strictEqual(dueAt("12:00:00"), "2026-11-25#11:30", "午休取上午最后一根");
        assert.strictEqual(dueAt("15:00:06"), "2026-11-25#15:00", "收盘最后一根");
      },
    },
    {
      name: "30m bar：对齐交易时段起点",
      fn() {
        assert.strictEqual(latestBarCloseSec(hhmmss("10:29:00"), 1800), hhmmss("10:00"));
        assert.strictEqual(latestBarCloseSec(hhmmss("10:31:00"), 1800), hhmmss("10:30"));
        assert.strictEqual(latestBarCloseSec(hhmmss("13:31:00"), 1800), hhmmss("13:30"));
        assert.strictEqual(latestBarCloseSec(hhmmss("09:40:00"), 1800), null, "开盘 10 分钟内无 30m bar");
      },
    },
    {
      name: "interval：同一时间桶只触发一次",
      fn() {
        const p = resolveSchedule({ intervalMs: 60000 });
        const fired = new Set();
        const r1 = evaluate(p, at("10:00:05"), fired);
        assert.strictEqual(r1.due, true);
        fired.add(r1.key);
        assert.strictEqual(evaluate(p, at("10:00:25"), fired).due, false, "同桶内不重复");
        assert.strictEqual(evaluate(p, at("10:01:05"), fired).due, true, "下一桶触发");
      },
    },
    {
      name: "nextFireAt 给出下一次预计时点",
      fn() {
        const daily = resolveSchedule({ period: "1d" });
        assert.deepStrictEqual(nextFireAt(daily, at("10:00:00")), { date: "2026-11-25", at: "14:55" });
        assert.strictEqual(nextFireAt(daily, at("15:00:00")), null, "当天已无时点");
        const bar = resolveSchedule({ period: "5m" });
        assert.deepStrictEqual(nextFireAt(bar, at("10:01:00")), { date: "2026-11-25", at: "10:05" });
      },
    },
    {
      name: "normalizePoint 容忍多种写法",
      fn() {
        assert.strictEqual(normalizePoint("9:31"), "09:31");
        assert.strictEqual(normalizePoint("14:55"), "14:55");
        assert.strictEqual(normalizePoint("0931"), "09:31");
        assert.strictEqual(normalizePoint("25:00"), null);
        assert.strictEqual(normalizePoint(""), null);
      },
    },
  ],
};
