/**
 * 东财行情：缓存键不能串市场
 *
 * 真实 bug（2026-11-27 发现）：`getQuote` 用 6 位代码做缓存键，
 * 而 `000001` 既是上证指数（secid 1.000001）又是平安银行（0.000001）。
 * 3 秒缓存窗口内先查指数再查个股，会拿到指数的价格（3606 而不是 12）。
 *
 * 测试用桩替换 `_get`，不联网。
 */
const assert = require("assert");
const { EastmoneyDataSource } = require("../../main/datasources/eastmoney");

/** 按 secid 返回构造好的假响应，价格可区分（接口价格是 ×100 的整数，_num 默认 /100） */
function makeDataSource() {
  const ds = new EastmoneyDataSource({});
  ds._get = async (url) => {
    const m = url.match(/secid=([0-9.]+)/);
    const secid = m ? m[1] : "0.0";
    const isIndex = secid === "1.000001";
    // 指数 3606.49 元、个股 12.23 元：量级差很大，串了一眼能看出来
    const price = isIndex ? 3606.49 : 12.23;
    const scaled = Math.round(price * 100);
    return {
      f43: scaled,
      f44: scaled + 100,
      f45: scaled - 100,
      f46: scaled,
      f47: 1000,
      f48: 2000000,
      f57: secid.split(".")[1],
      f58: isIndex ? "上证指数" : "平安银行",
      f60: scaled - 10,
      f107: 1,
      f168: 0.5,
      f169: 0.2,
      f170: 1.5,
      f116: 1e11,
      f117: 8e10,
    };
  };
  return ds;
}

module.exports = {
  name: "行情缓存隔离",
  cases: [
    {
      name: "同一代码不同市场不会串数据（000001：上证指数 vs 平安银行）",
      async fn() {
        const ds = makeDataSource();
        const idx = await ds.getQuote("sh000001");
        const stock = await ds.getQuote("sz000001");
        assert.strictEqual(idx.name, "上证指数");
        assert.strictEqual(idx.price, 3606.49);
        assert.strictEqual(stock.name, "平安银行", "个股不应拿到指数的缓存");
        assert.strictEqual(stock.price, 12.23, "价格必须是个股自己的");
        ds.dispose();
      },
    },
    {
      name: "反向顺序也不串（先个股后指数）",
      async fn() {
        const ds = makeDataSource();
        const stock = await ds.getQuote("sz000001");
        const idx = await ds.getQuote("sh000001");
        assert.strictEqual(stock.price, 12.23);
        assert.strictEqual(idx.price, 3606.49);
        ds.dispose();
      },
    },
    {
      name: "同一标的重复查询命中缓存（只请求一次）",
      async fn() {
        const ds = makeDataSource();
        let calls = 0;
        const raw = ds._get;
        ds._get = async (url) => {
          calls++;
          return raw(url);
        };
        await ds.getQuote("sz000001");
        await ds.getQuote("sz000001");
        await ds.getQuote("000001.SZ"); // 等价写法也应命中同一条缓存
        assert.strictEqual(calls, 1, "同一 secid 的重复查询应命中缓存，实际请求 " + calls + " 次");
        ds.dispose();
      },
    },
    {
      name: "不同标的各自缓存，互不覆盖",
      async fn() {
        const ds = makeDataSource();
        const a = await ds.getQuote("sz000001");
        const b = await ds.getQuote("sh600000");
        assert.strictEqual(a.code !== b.code || a.name !== b.name, true, "两个标的应是不同的数据");
        assert.strictEqual(a.price, 12.23);
        ds.dispose();
      },
    },
  ],
};
