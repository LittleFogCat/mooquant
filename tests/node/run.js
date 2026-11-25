/**
 * mookquant · Node 侧测试脚手架（零依赖）
 *
 * 项目没有 JS 测试框架，这里用 node:assert 做最小可用的跑批：
 *   node tests/node/run.js            # 全部
 *   node tests/node/run.js scheduler  # 只跑名字匹配的文件
 *
 * 每个测试文件导出 { name, cases: [{ name, fn }] }。
 */
const path = require("path");

const FILES = [
  "test-trading-calendar.js",
  "test-strategy-timing.js",
  "test-trading-scheduler.js",
  "test-executor-gating.js",
  "test-quota-report.js",
  "test-full-day.js",
];

async function main() {
  const filter = process.argv[2] || "";
  let pass = 0;
  const failures = [];

  for (const file of FILES) {
    if (filter && !file.includes(filter)) continue;
    const mod = require(path.join(__dirname, file));
    console.log("\n=== " + (mod.name || file) + " ===");
    for (const c of mod.cases) {
      try {
        await c.fn();
        pass++;
        console.log("  ok   " + c.name);
      } catch (e) {
        failures.push({ file, name: c.name, error: e });
        console.log("  FAIL " + c.name + "\n       " + (e && e.message));
      }
    }
  }

  console.log("\n" + (failures.length ? "FAILED" : "PASSED") + "  " + pass + " passed, " + failures.length + " failed");
  if (failures.length) {
    process.exitCode = 1;
  }
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
