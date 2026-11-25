/**
 * 执行器调度闸门测试
 *
 * 直接验证验收标准：「日线策略当天信号计算次数 = 2」。
 * 这里不启动定时器，只按 15s 步长手动驱动 _isDue()，
 * 等价于执行器在真实运行时的判断路径。
 */
const assert = require("assert");
const { StrategyExecutor } = require("../../main/services/executor-service");

function makeExecutor(schedule) {
  const ex = new StrategyExecutor({
    strategy: { id: "st_test", name: "测试策略", type: "ma_cross", schedule, risk: {} },
    symbols: ["sz000001"],
    autoTrade: {},
  });
  // 除了专门的「重启去重」用例外，其余用例不落盘（避免污染 data/executor）
  ex._saveFiredKeys = () => {};
  return ex;
}

/** 以 stepSec 步长遍历 [from, to] 时间段，返回真正该算信号的次数 */
function countDue(executor, day, from, to, stepSec = 15) {
  const [fh, fm] = from.split(":").map(Number);
  const [th, tm] = to.split(":").map(Number);
  const start = fh * 3600 + fm * 60;
  const end = th * 3600 + tm * 60;
  let n = 0;
  for (let s = start; s <= end; s += stepSec) {
    const hh = String(Math.floor(s / 3600)).padStart(2, "0");
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    if (executor._isDue(new Date(day + "T" + hh + ":" + mm + ":" + ss))) n++;
  }
  return n;
}

module.exports = {
  name: "执行器调度闸门",
  cases: [
    {
      name: "日线策略一整天只算 2 次信号",
      fn() {
        const ex = makeExecutor({ period: "1d" });
        const n = countDue(ex, "2026-11-25", "09:00", "15:30");
        assert.strictEqual(n, 2, "实际触发 " + n + " 次");
      },
    },
    {
      name: "日线策略重复启动也不重复计算（去重 key 跨 tick 生效）",
      fn() {
        const ex = makeExecutor({ period: "1d" });
        const d = new Date("2026-11-25T09:31:10");
        assert.strictEqual(ex._isDue(d), true);
        assert.strictEqual(ex._isDue(d), false);
        assert.strictEqual(ex._isDue(new Date("2026-11-25T09:31:25")), false);
      },
    },
    {
      name: "1m 策略上午按分钟触发（约 120 次）",
      fn() {
        const ex = makeExecutor({ period: "1m" });
        const n = countDue(ex, "2026-11-25", "09:30", "11:31", 15);
        assert.ok(n >= 119 && n <= 121, "实际触发 " + n + " 次");
      },
    },
    {
      name: "旧策略（无 period）按 intervalMs 兜底，一分钟一次",
      fn() {
        const ex = makeExecutor({ intervalMs: 60000 });
        const n = countDue(ex, "2026-11-25", "10:00", "10:10", 15);
        assert.ok(n >= 10 && n <= 11, "10 分钟内实际触发 " + n + " 次");
      },
    },
    {
      name: "交易时段闸门：午休/收盘/调度器未放行都拦下",
      fn() {
        const ex = makeExecutor({ period: "1d" });
        // 无调度器时只看 market-clock
        assert.strictEqual(ex._isTradingAllowed(new Date("2026-11-25T10:00:00")), true);
        assert.strictEqual(ex._isTradingAllowed(new Date("2026-11-25T12:00:00")), false, "午休应拦下");
        assert.strictEqual(ex._isTradingAllowed(new Date("2026-11-25T15:10:00")), false, "收盘后应拦下");

        // 挂一个不放行的调度器
        ex._getScheduler = () => ({ state: "pre", canPlaceOrder: () => false });
        assert.strictEqual(ex._isTradingAllowed(new Date("2026-11-25T10:00:00")), false, "调度器未放行应拦下");

        // 调度器放行
        ex._getScheduler = () => ({ state: "open", canPlaceOrder: () => true });
        assert.strictEqual(ex._isTradingAllowed(new Date("2026-11-25T10:00:00")), true);
      },
    },
    {
      name: "allowOffHours 调试开关可绕过时段与调度器",
      fn() {
        const ex = makeExecutor({ period: "1d" });
        ex._autoTrade = { allowOffHours: true };
        ex._getScheduler = () => ({ state: "idle", canPlaceOrder: () => false });
        assert.strictEqual(ex._isTradingAllowed(new Date("2026-11-25T23:00:00")), true);
      },
    },
    {
      name: "跨日自动重置，第二天仍能算 2 次",
      fn() {
        const ex = makeExecutor({ period: "1d" });
        ex._isDue(new Date("2026-11-25T09:31:00"));
        ex._isDue(new Date("2026-11-25T14:55:00"));
        assert.strictEqual(ex._isDue(new Date("2026-11-26T09:31:00")), true, "次日照常触发");
      },
    },
    {
      name: "分钟周期无 K 线时明确跳过（不抛错、不计熔断）",
      async fn() {
        const ex = makeExecutor({ period: "1m" });
        ex._plan = require("../../main/services/strategy-timing").resolveSchedule({ period: "1m" });
        ex.quoteService = {
          getHistory: async () => ({ ok: true, data: { bars: [] } }),
        };
        ex.tradeService = { getPositions: async () => ({ ok: true, data: [] }) };
        ex._logTick = () => {};
        await ex._tickSymbol("sz000001");
        assert.ok(/1m/.test(ex._lastSkip || ""), "跳过原因应说明缺 1m K 线，实际：" + ex._lastSkip);
        assert.strictEqual(ex._consecutiveErrors, 0, "不该计为错误（否则会误触发熔断）");
      },
    },
    {
      name: "重启后不重算已触发时点（防重复下单）",
      fn() {
        const fs = require("fs");
        const path = require("path");
        const DIR = path.join(__dirname, "..", "..", "data", "executor");
        const id = "st_test_restart_guard";
        const file = path.join(DIR, id + ".json");
        try { fs.unlinkSync(file); } catch {}

        const mk = () =>
          new StrategyExecutor({
            strategy: { id, name: "重启守卫", type: "ma_cross", schedule: { period: "1d" }, risk: {} },
            symbols: ["sz000001"],
            autoTrade: {},
          });

        const first = mk();
        assert.strictEqual(first._isDue(new Date("2026-11-25T09:31:10")), true, "首次应触发");
        assert.ok(fs.existsSync(file), "触发后必须落盘，否则重启无法去重");

        // 模拟进程重启：新实例只能靠磁盘恢复
        const second = mk();
        second._loadFiredKeys(new Date("2026-11-25T09:33:00"));
        assert.strictEqual(second._isDue(new Date("2026-11-25T09:33:00")), false, "重启后不能重算同一个时点");
        assert.strictEqual(second._isDue(new Date("2026-11-25T14:55:10")), true, "另一个时点照常触发");

        // 隔天应清空旧 key
        const third = mk();
        third._loadFiredKeys(new Date("2026-11-26T09:31:10"));
        assert.strictEqual(third._isDue(new Date("2026-11-26T09:31:10")), true, "次日照常触发");

        try { fs.unlinkSync(file); } catch {}
      },
    },
  ],
};
