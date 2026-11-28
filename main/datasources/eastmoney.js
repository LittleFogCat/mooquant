/**
 * mookquant · 东财公开行情数据源（免 Key、免客户端）
 *
 * 数据来自东方财富网网页端同名接口，**不消耗妙想 API 额度**：
 *  - 实时快照：push2.eastmoney.com/api/qt/stock/get（单只）
 *               push2.eastmoney.com/api/qt/ulist.np/get（批量，一次 50 只）
 *  - 历史 K 线：push2his.eastmoney.com/api/qt/stock/kline/get
 *  - 标的列表：本地 config/stocks_cache.json（QMT 同步过一次即可复用）
 *
 * 定位：QMT 不可用时的「真行情」兜底，避免 mock 假数据参与策略决策。
 * 注意：网页接口有反爬，需带 UA/Referer 并控制频率，生产环境仍建议 QMT。
 */

const path = require("path");
const fs = require("fs");

const QUOTE_HOST = "https://push2.eastmoney.com";
const HISTORY_HOST = "https://push2his.eastmoney.com";
const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Referer: "https://quote.eastmoney.com/",
  Accept: "application/json",
};

/** 周期 → 东财 klt 参数 */
const PERIOD_MAP = { "1m": "1", "5m": "5", "15m": "15", "30m": "30", "60m": "60", "1h": "60", "1d": "101", "1w": "102", "1mon": "103" };
/** 复权 → fqt */
const FQT_MAP = { none: "0", front: "1", back: "2", front_ratio: "1", back_ratio: "2" };

class EastmoneyDataSource {
  constructor(options = {}) {
    this.mode = "eastmoney";
    this.description = "东财公开行情（免 Key）";
    this._options = options;
    this._stockList = null;
    this._cache = new Map(); // code -> {at, data}
    this._cacheTtl = options.cacheTtlMs || 3000;
  }

  async init() {
    await this.ping();
  }

  /** sh600519 / 600519.SH / 600519 → secid 1.600519 */
  _secid(rawSymbol) {
    const s = String(rawSymbol || "").trim().toLowerCase();
    let market = null;
    let code = null;
    let m = s.match(/^(sh|sz|bj)(\d{6})$/);
    if (m) {
      market = m[1];
      code = m[2];
    } else {
      m = s.match(/^(\d{6})\.(sh|sz|bj)$/);
      if (m) {
        code = m[1];
        market = m[2];
      } else {
        m = s.match(/^(\d{6})$/);
        if (m) {
          code = m[1];
          market = /^[659]/.test(code) ? "sh" : /^[48]/.test(code) ? "bj" : "sz";
        }
      }
    }
    if (!code) throw new Error("无法识别的代码: " + rawSymbol);
    return { secid: (market === "sh" ? "1." : "0.") + code, code, market };
  }

  _uiCode(code, market) {
    return (market === "1" ? "sh" : "sz") + code;
  }

  /** 分 → 元；东财快照价格字段是放大 100 的整数 */
  _num(v, scale = 100) {
    const n = Number(v);
    if (!Number.isFinite(n) || n === -0 || v === "-") return 0;
    return n / scale;
  }

  async _get(url) {
    const res = await fetch(url, { headers: HEADERS, signal: AbortSignal.timeout(this._options.timeoutMs || 10000) });
    if (!res.ok) throw new Error("东财行情 HTTP " + res.status);
    const json = await res.json();
    if (!json || !json.data) throw new Error("东财行情无数据");
    return json.data;
  }

  /** 单只快照 */
  async getQuote(rawSymbol) {
    const { secid, code, market } = this._secid(rawSymbol);
    // 缓存键必须用 secid（含市场），不能用 6 位代码：
    // 000001 既是上证指数（1.000001）又是平安银行（0.000001），
    // 用 code 做键会在 TTL 内互相污染 —— 平安银行会报出指数的 3606，
    // 若策略拿这个价格算均线，信号会完全失真。
    const cacheKey = secid;
    const cached = this._cache.get(cacheKey);
    if (cached && Date.now() - cached.at < this._cacheTtl) return cached.data;

    const fields = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f107,f168,f169,f170,f116,f117";
    const d = await this._get(`${QUOTE_HOST}/api/qt/stock/get?secid=${secid}&fields=${fields}&_=${Date.now()}`);
    const price = this._num(d.f43);
    const prevClose = this._num(d.f60);
    const quote = {
      code: this._uiCode(code, market === "sh" ? "1" : "0"),
      name: d.f58 || code,
      industry: "",
      market: market === "sh" ? "SH" : "SZ",
      marketName: market === "sh" ? "上海证券交易所" : "深圳证券交易所",
      currency: "CNY",
      price,
      open: this._num(d.f46),
      high: this._num(d.f44),
      low: this._num(d.f45),
      prevClose,
      change: this._num(d.f169),
      changePercent: this._num(d.f170),
      volume: this._num(d.f47, 1) * 100, // 手 → 股
      turnover: this._num(d.f48, 1),
      turnoverRate: this._num(d.f168),
      marketCap: this._num(d.f116, 1),
      floatCap: this._num(d.f117, 1),
      timestamp: new Date().toISOString(),
    };
    this._cache.set(cacheKey, { at: Date.now(), data: quote });
    return quote;
  }

  /**
   * 批量快照（一次请求最多 50 只，供 quote-service 批量查询复用）
   * @param {string[]} symbols
   */
  async getQuotes(symbols) {
    const list = (symbols || []).slice(0, 50);
    if (!list.length) return [];
    const ids = list.map((s) => this._secid(s).secid).join(",");
    const fields = "f2,f3,f4,f5,f6,f12,f14,f15,f16,f17,f18";
    const d = await this._get(`${QUOTE_HOST}/api/qt/ulist.np/get?secids=${ids}&fields=${fields}&_=${Date.now()}`);
    const diff = (d && d.diff) || [];
    const arr = Array.isArray(diff) ? diff : Object.values(diff);
    return arr.map((x) => {
      const price = this._num(x.f2);
      const prevClose = this._num(x.f18);
      const code = String(x.f12 || "");
      return {
        code: this._uiCode(code, /^[659]/.test(code) ? "1" : "0"),
        name: x.f14 || code,
        price,
        change: this._num(x.f4),
        changePercent: this._num(x.f3),
        open: this._num(x.f17),
        high: this._num(x.f15),
        low: this._num(x.f16),
        prevClose,
        volume: this._num(x.f5, 1) * 100,
        turnover: this._num(x.f6, 1),
        timestamp: new Date().toISOString(),
      };
    });
  }

  /** 历史 K 线 */
  async getHistory(rawSymbol, period = "1d", count = 60, dividendType = "front") {
    const { secid } = this._secid(rawSymbol);
    const klt = PERIOD_MAP[period] || "101";
    const fqt = FQT_MAP[dividendType] || "1";
    const lmt = Math.max(1, Math.min(Number(count) || 60, 5000));
    const url = `${HISTORY_HOST}/api/qt/stock/kline/get?secid=${secid}&klt=${klt}&fqt=${fqt}` +
      `&beg=0&end=20500101&lmt=${lmt}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58&_=${Date.now()}`;
    const d = await this._get(url);
    const klines = (d && d.klines) || [];
    // 注意：接口在 beg=0 时可能忽略 lmt 返回全部历史（平安银行约 8500 根），
    // 这里按 lmt 截取末尾，避免把整段历史塞给策略/模型服务。
    const bars = klines.slice(-lmt).map((line) => {
      const p = String(line).split(",");
      return {
        date: p[0],
        open: Number(p[1]),
        close: Number(p[2]),
        high: Number(p[3]),
        low: Number(p[4]),
        volume: Number(p[5]) * 100, // 手 → 股
        amount: Number(p[6]),
        amplitude: Number(p[7]),
      };
    });
    return { bars, count: bars.length };
  }

  /** 标的列表：优先本地缓存（QMT 同步过一次即可） */
  async getStockList() {
    if (this._stockList) return this._stockList;
    const file = this._options.stocksCachePath || path.join(__dirname, "..", "..", "config", "stocks_cache.json");
    try {
      const raw = fs.readFileSync(file, "utf-8");
      const arr = JSON.parse(raw);
      this._stockList = Array.isArray(arr) ? arr : [];
    } catch {
      this._stockList = [];
    }
    return this._stockList;
  }

  /** 公开接口不提供全量同步，返回本地缓存规模即可 */
  async syncStocks() {
    const list = await this.getStockList();
    return { total: list.length, source: "cache" };
  }

  /** 连通性探测 */
  async ping() {
    const q = await this.getQuote("sh600519");
    return { ok: !!q && q.price > 0, price: q && q.price };
  }

  dispose() {
    this._cache.clear();
    this._stockList = null;
  }
}

module.exports = { EastmoneyDataSource };
