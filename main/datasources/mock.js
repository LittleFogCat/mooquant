/**
 * mookquant · Mock 数据源（A股专属）
 */
const path = require("path");
const fs2 = require("fs");

let STOCK_LIST = [];

/**
 * 加载股票列表：优先读 stocks_cache.json（DB 同步导出的全量列表），
 * 回退到 a_stocks.json（30 只种子数据）
 */
function loadStockList() {
  if (STOCK_LIST.length > 0) return STOCK_LIST;
  // 1. 优先读全量缓存
  try {
    STOCK_LIST = JSON.parse(fs2.readFileSync(
      path.join(__dirname, "..", "..", "config", "stocks_cache.json"), "utf-8"));
    if (STOCK_LIST.length > 0) return STOCK_LIST;
  } catch (e) { /* 缓存不存在，继续回退 */ }
  // 2. 回退到种子数据
  try {
    STOCK_LIST = JSON.parse(fs2.readFileSync(
      path.join(__dirname, "..", "..", "config", "a_stocks.json"), "utf-8"));
  } catch (e) { STOCK_LIST = []; }
  return STOCK_LIST;
}

/**
 * 重新加载股票列表（sync 完成后调用以刷新内存缓存）
 */
function reloadStockList() {
  STOCK_LIST = [];
  return loadStockList();
}

loadStockList(); // 模块加载时初始化

const MOCK_TABLE = {};
for (const s of STOCK_LIST) { MOCK_TABLE[s.code] = { name: s.name, industry: s.industry, base: s.base }; }

function normalize(raw) {
  const s = (raw || "").trim().toLowerCase();
  if (!s) return "";
  if (s.startsWith("sh") || s.startsWith("sz") || s.startsWith("bj")) return s;
  if (/^d{6}$/.test(s)) {
    const f = s[0];
    if (f === "6" || f === "9" || f === "5") return "sh" + s;
    if (f === "0" || f === "2" || f === "3") return "sz" + s;
    if (f === "8" || f === "4") return "bj" + s;
    return "sh" + s;
  }
  return s;
}

function detectMarket(symbol) {
  if (symbol.startsWith("sh")) return { code: symbol.toUpperCase(), market: "sh", marketName: "上海", currency: "CNY" };
  if (symbol.startsWith("sz")) return { code: symbol.toUpperCase(), market: "sz", marketName: "深圳", currency: "CNY" };
  if (symbol.startsWith("bj")) return { code: symbol.toUpperCase(), market: "bj", marketName: "北京", currency: "CNY" };
  return { code: symbol.toUpperCase(), market: "sh", marketName: "上海", currency: "CNY" };
}

function searchByName(query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return [];
  const { getInitials } = require("../utils/pinyin");
  const isDigit = /^\d+$/.test(q);
  const isChinese = /[\u4e00-\u9fa5]/.test(q);
  return STOCK_LIST.filter(s => {
    if (isDigit) {
      return s.code.replace(/^(sh|sz|bj)/, "").includes(q) || s.code.includes(q);
    } else if (isChinese) {
      return s.name.includes(q);
    } else {
      const initials = getInitials(s.name);
      return initials.startsWith(q) || initials.includes(q);
    }
  }).slice(0, 10).map(s => ({ code: s.code, name: s.name, industry: s.industry }));
}

class MockDataSource {
  constructor() { this.mode = "mock"; this.description = "本地模拟数据（A股）"; this._artificialDelayMs = 200; }
  async getQuote(rawSymbol) {
    let symbol = normalize(rawSymbol);
    if (!symbol) { const m = searchByName(rawSymbol); if (m.length > 0) symbol = m[0].code; }
    if (!symbol) throw new Error("请输入有效的A股代码或名称");
    await new Promise(r => setTimeout(r, this._artificialDelayMs));
    const preset = MOCK_TABLE[symbol];
    const si = detectMarket(symbol);
    let base, name, industry;
    if (preset) { base = preset.base; name = preset.name; industry = preset.industry; }
    else { let h = 0; for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0; base = 10 + Math.abs(h % 9900) / 10; name = "未知A股 " + si.code; industry = "-"; }
    const now = new Date(); const tick = Math.floor(now.getTime() / 60000);
    const seed = ((tick * 9301 + 49297) % 233280) / 233280; const drift = (seed - 0.5) * 0.06;
    const price = +(base * (1 + drift)).toFixed(2); const change = +(price - base).toFixed(2);
    return { code: si.code, name, industry, market: si.market, marketName: si.marketName, currency: si.currency,
      price, open: +(base * (1 + (seed * 0.02 - 0.01))).toFixed(2), high: +(price * 1.015).toFixed(2), low: +(price * 0.985).toFixed(2),
      prevClose: +base.toFixed(2), change, changePercent: +((change / base) * 100).toFixed(2),
      volume: Math.floor(10000 + Math.abs(seed) * 1000000), turnover: Math.floor(100000000 + Math.abs(seed) * 5000000000),
      timestamp: now.toISOString() };
  }
  async search(query) { return searchByName(query); }
  async getHistory(rawSymbol, period, count, dividendType) {
    let symbol = normalize(rawSymbol);
    if (!symbol) { const m = searchByName(rawSymbol); if (m.length > 0) symbol = m[0].code; }
    if (!symbol) throw new Error("symbol not found");
    const preset = MOCK_TABLE[symbol];
    const base = preset ? preset.base : 50;
    const n = Math.min(count || 60, 500);
    const bars = [];
    let price = base;
    let seedVal = 0;
    for (let i = 0; i < symbol.length; i++) seedVal += symbol.charCodeAt(i);
    const rng = (i) => { const x = Math.sin(seedVal + i * 17) * 10000; return x - Math.floor(x); };
    const now = Date.now();
    const dayMs = 86400000;
    for (let i = n - 1; i >= 0; i--) {
      const ret = (rng(i) - 0.5) * 0.04;
      const open = price;
      const close = price * (1 + ret);
      const high = Math.max(open, close) * (1 + rng(i + 100) * 0.015);
      const low = Math.min(open, close) * (1 - rng(i + 200) * 0.015);
      const vol = Math.floor(500000 + rng(i + 300) * 3000000);
      bars.push({ time: Math.floor((now - i * dayMs) / 1000), date: new Date(now - i * dayMs).toISOString().slice(0, 10), open: +open.toFixed(2), high: +high.toFixed(2), low: +low.toFixed(2), close: +close.toFixed(2), volume: vol });
      price = close;
    }
    return { bars: bars.reverse(), count: bars.length };
  }
  dispose() {}
}
module.exports = { MockDataSource, searchByName, loadStockList, reloadStockList, STOCK_LIST };