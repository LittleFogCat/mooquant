/**
 * 模拟盘视图渲染测试
 *
 * 视图是纯函数（state → HTML 字符串），所以可以不启动 Electron 就验证：
 * 把 ESM 的 format.js + sim-view.js 拼成一个临时的 CJS 文件再 require。
 *
 * 这类测试专门防「拼装漏了一段」的低级错误——上一版策略页就漏了把
 * 状态条拼进主返回串，界面上什么都看不到，但单测和构建都是绿的。
 */
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.join(__dirname, "..", "..");
const TMP = path.join(os.tmpdir(), "mookquant-sim-view-test.cjs");

/** 把两个 ESM 文件压成一个 CJS 模块，导出 render */
function buildModule() {
  const strip = (code) =>
    code
      .replace(/^\s*import[\s\S]*?;\s*$/gm, "")     // 去掉 import 语句
      .replace(/^export\s*\{[^}]*\};?\s*$/gm, "")   // 去掉 export {...}
      .replace(/^export\s+/gm, "");                 // export function/const → 普通声明
  const fmt = strip(fs.readFileSync(path.join(ROOT, "renderer/js/utils/format.js"), "utf-8"));
  const view = strip(fs.readFileSync(path.join(ROOT, "renderer/js/views/sim-view.js"), "utf-8"));
  fs.writeFileSync(TMP, fmt + "\n\n" + view + "\n\nmodule.exports = { render };\n", "utf-8");
  delete require.cache[TMP];
  return require(TMP);
}

/** 最小 DOM 桩：只需要 innerHTML 与两个查询方法 */
function fakeRoot() {
  return {
    _html: "",
    set innerHTML(v) {
      this._html = v;
    },
    get innerHTML() {
      return this._html;
    },
    querySelectorAll() {
      return [];
    },
    querySelector() {
      return null;
    },
  };
}

/** 用给定 state 渲染一次，返回 HTML */
function renderWith(state) {
  const { render } = buildModule();
  const root = fakeRoot();
  const vm = {
    subscribe(fn) {
      fn(state);
    },
  };
  render(root, vm);
  return root.innerHTML;
}

const baseState = {
  engine: {
    state: "open",
    stateLabel: "交易中",
    isTradingDay: true,
    calibrated: true,
    nextEventAt: "2026-11-25 14:55",
    todayFired: ["pre_open", "market_open"],
    role: "trader",
    lastError: null,
  },
  executors: [],
  report: {
    date: "2026-11-25",
    generatedAt: null,
    orders: 2,
    signals: 3,
    errors: [],
    skips: [{ strategy: "演示", reason: "等待调度时点（日线 09:31 / 14:55）" }],
  },
  reports: [{ date: "2026-11-24", totalAssets: 1000000, orders: 1, signals: 2, errors: 0 }],
  quota: { used: 7, limit: 300, reserve: 100, openAllowed: true, closeAllowed: true },
  info: { mode: "eastmoney", description: "东财妙想模拟盘" },
  settings: { dataSource: "eastmoney", autoTrade: { enabled: true } },
  account: { totalAssets: 1000000, available: 996456.97, marketValue: 3495, totalPnl: 1200 },
  positions: [],
  orders: [],
  snapshotAt: "2026-11-25T02:30:00.000Z",
  loading: false,
  generating: false,
  error: null,
};

module.exports = {
  name: "模拟盘视图",
  cases: [
    {
      name: "渲染出引擎状态、今日表现、额度与日报区块",
      fn() {
        const html = renderWith(baseState);
        assert.ok(html.includes("交易引擎"), "缺引擎卡片");
        assert.ok(html.includes("交易中"), "缺引擎状态");
        assert.ok(html.includes("下一时点 2026-11-25 14:55"), "缺下一时点");
        assert.ok(html.includes("今日表现"), "缺今日表现卡片");
        assert.ok(html.includes("今日成交 2 笔"), "成交笔数没拼进去：" + html.slice(0, 200));
        assert.ok(html.includes("策略信号 3 次"), "信号次数没拼进去");
        assert.ok(html.includes("7 / 300"), "额度占用没拼进去");
        assert.ok(html.includes("历史日报"), "缺日报区块");
        assert.ok(html.includes("2026-11-24"), "缺历史日报行");
        assert.ok(html.includes("东财妙想模拟盘"), "缺交易通道信息");
      },
    },
    {
      name: "观察模式有醒目提示，且没策略时说明原因",
      fn() {
        const html = renderWith({
          ...baseState,
          engine: { ...baseState.engine, role: "observer" },
          executors: [],
        });
        assert.ok(html.includes("观察模式"), "缺观察模式徽标");
        assert.ok(html.includes("交易由另一进程执行"), "缺观察模式说明");
      },
    },
    {
      name: "运行中策略表展示调度、已算信号、上次信号与跳过原因",
      fn() {
        const html = renderWith({
          ...baseState,
          executors: [
            {
              strategyId: "st_1",
              strategyName: "无人值守演示（MA交叉）",
              status: "running",
              tickCount: 2,
              lastSignal: { action: "hold", reason: "无信号" },
              lastSkip: "等待调度时点（日线 09:31 / 14:55）",
              schedule: { label: "日线 09:31 / 14:55", next: { at: "14:55" } },
              error: null,
            },
          ],
        });
        assert.ok(html.includes("无人值守演示（MA交叉）"));
        // escapeHtml 会把 "/" 转成 &#x2F;，所以只断言不会被转义的部分
        assert.ok(html.includes("日线 09:31"), "缺调度说明：" + html.match(/日线[^<]*/));
        assert.ok(html.includes("下次 14:55"), "缺下次时点");
        assert.ok(html.includes("hold（无信号）"));
        assert.ok(html.includes("等待调度时点"));
      },
    },
    {
      name: "报告未生成时提示 15:05 自动生成（而不是假装已完成）",
      fn() {
        const html = renderWith(baseState);
        assert.ok(html.includes("尚未生成"), "应提示未生成");
        assert.ok(html.includes("15:05"), "应说明 15:05 自动生成");
      },
    },
    {
      name: "额度不足时给出「仅允许平仓」的明确提示",
      fn() {
        const html = renderWith({
          ...baseState,
          quota: { used: 250, limit: 300, reserve: 100, openAllowed: false, closeAllowed: true },
        });
        assert.ok(html.includes("仅允许平仓"), "缺额度告警");
      },
    },
    {
      name: "XSS：策略名与跳过原因必须转义",
      fn() {
        const html = renderWith({
          ...baseState,
          executors: [
            {
              strategyId: "x",
              strategyName: "<img src=x onerror=alert(1)>",
              status: "running",
              tickCount: 0,
              lastSignal: null,
              lastSkip: "<script>alert(2)</script>",
              schedule: null,
              error: null,
            },
          ],
        });
        assert.ok(!html.includes("<script>alert(2)</script>"), "跳过原因未转义");
        assert.ok(!html.includes("<img src=x onerror=alert(1)>"), "策略名未转义");
      },
    },
    {
      name: "通道阻断（113 休眠）时给出红字原因与徽标",
      fn() {
        const html = renderWith({
          ...baseState,
          quota: {
            used: 5, limit: 300, reserve: 100, openAllowed: false, closeAllowed: false,
            blocked: true, blockCode: 113,
            blockReason: "您的调用次数已达到上限，由于您近期活跃度较低，账户已经进入休眠状态",
          },
        });
        assert.ok(html.includes("通道阻断"), "缺阻断徽标");
        assert.ok(html.includes("113"), "缺错误码");
        assert.ok(html.includes("休眠状态"), "缺具体原因");
      },
    },
  ],
};
