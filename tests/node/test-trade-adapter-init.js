/**
 * 东财适配器初始化容错测试
 *
 * 背景（2026-11-26 事故）：账户被平台降额到 10 次/天进入休眠后，
 * `init()` 里的自检查询抛 113 → 工厂抛错 → **daemon 起不来**（GUI 同样会起不来）。
 * 最需要“能启动、能看到状态、能提示去激活”的时刻，反而起不来。
 *
 * 正确语义：113（额度/休眠）是**非致命**的，密钥有效，通道以阻断模式启动；
 * 114（密钥失效）才是致命的。
 */
const assert = require("assert");
const { EastmoneySimTradeDataSource } = require("../../main/datasources/trade/eastmoney-sim");

function makeSource(errMessage) {
  const src = new EastmoneySimTradeDataSource({ apiKey: "k_test", baseUrl: "http://127.0.0.1:9" });
  src._post = async () => {
    throw new Error(errMessage);
  };
  return src;
}

module.exports = {
  name: "东财适配器初始化",
  cases: [
    {
      name: "没有 API Key 时初始化失败（这是配置错误，必须报出来）",
      async fn() {
        const src = new EastmoneySimTradeDataSource({ apiKey: "", baseUrl: "http://127.0.0.1:9" });
        await assert.rejects(() => src.init(), /MX_APIKEY/);
      },
    },
    {
      name: "113 休眠：初始化不失败，只记 warning（通道以阻断模式起来）",
      async fn() {
        const src = makeSource("东财模拟盘: 您的调用次数已达到上限…账户已经进入休眠状态… (113)");
        await src.init(); // 不应抛
        assert.ok(src.initWarning && /休眠/.test(src.initWarning), "应记录休眠告警");
      },
    },
    {
      name: "114 密钥失效：初始化仍然失败（重试无意义）",
      async fn() {
        const src = makeSource("东财模拟盘: API密钥不存在或已失效 (114)");
        await assert.rejects(() => src.init(), /密钥/);
      },
    },
    {
      name: "普通网络错误：初始化失败，不吞掉",
      async fn() {
        const src = makeSource("fetch failed: ECONNREFUSED");
        await assert.rejects(() => src.init(), /ECONNREFUSED/);
      },
    },
    {
      name: "休眠状态解除后 initWarning 会被清掉",
      async fn() {
        const src = makeSource("…休眠状态… (113)");
        await src.init();
        assert.ok(src.initWarning, "先有告警");
        // 模拟恢复：自检成功
        src._post = async (path) => ({ uid: "1", totalAssets: 1000000, balanceActual: 1000000, availBalance: 1000000 });
        await src.init();
        assert.strictEqual(src.initWarning, null, "恢复后不该继续报警");
      },
    },
  ],
};
