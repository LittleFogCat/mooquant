/**
 * mookquant · Quote Service（主进程）
 */
class QuoteService {
  constructor({ source, cacheTtlMs = 0 }) {
    this.source = source;
    this.cacheTtlMs = cacheTtlMs;
    this._cache = new Map();
    this._stockList = null;        // 缓存的股票列表
    this._stockListPinyin = null;  // 预计算拼音首字母
    this._stockListPromise = null; // 防止重复加载
  }

  _cacheKey(s) { return (s || "").trim().toLowerCase(); }
  _cacheGet(k) { if (this.cacheTtlMs <= 0) return null; const e = this._cache.get(k); if (!e) return null; if (Date.now() - e.ts > this.cacheTtlMs) { this._cache.delete(k); return null; } return e.data; }
  _cacheSet(k, d) { if (this.cacheTtlMs <= 0) return; this._cache.set(k, { data: d, ts: Date.now() }); }

  async query(rawSymbol) {
    const key = this._cacheKey(rawSymbol);
    if (!key) return { ok: false, error: "请输入股票代码或名称" };
    const cached = this._cacheGet(key); if (cached) return { ok: true, data: cached, cached: true };
    try {
      const data = await this.source.getQuote(rawSymbol);
      if (!data) return { ok: false, error: "未找到该标的（" + rawSymbol + "）" };
      this._cacheSet(key, data); return { ok: true, data };
    } catch (e) { return { ok: false, error: e.message || "查询失败" }; }
  }

  async getHistory(symbol, period, count, dividendType) {
    try {
      if (typeof this.source.getHistory === "function") {
        const data = await this.source.getHistory(symbol, period || "1d", count || 60, dividendType);
        return { ok: true, data };
      }
      return { ok: false, error: "data source does not support history" };
    } catch (e) { return { ok: false, error: e.message }; }
  }

  /**
   * 确保股票列表已加载到内存（带防重入）
   * 优先从 QMT 数据库读取，回退到 JSON 缓存文件
   */
  async _ensureStockList() {
    if (this._stockList) return this._stockList;
    if (this._stockListPromise) return this._stockListPromise;

    this._stockListPromise = (async () => {
      // 1. 优先从数据源（QMT）读取数据库
      if (typeof this.source.getStockList === "function") {
        try {
          const result = await this.source.getStockList();
          if (result && result.stocks && result.stocks.length > 0) {
            this._stockList = result.stocks;
            this._precomputePinyin();
            console.log("[quote] stock list loaded from DB:", result.stocks.length);
            return this._stockList;
          }
        } catch (e) {
          console.warn("[quote] getStockList from source failed:", e.message);
        }
      }

      // 2. 回退：从 JSON 缓存文件读取（mock 模式或 DB 为空时）
      const { loadStockList } = require("../datasources/mock");
      this._stockList = loadStockList();
      this._precomputePinyin();
      console.log("[quote] stock list loaded from JSON cache:", this._stockList.length);
      return this._stockList;
    })();

    try {
      return await this._stockListPromise;
    } finally {
      this._stockListPromise = null;
    }
  }

  /**
   * 预计算全部股票的拼音首字母，缓存到 _stockListPinyin
   */
  _precomputePinyin() {
    if (!this._stockList) return;
    let getInitials = null;
    try { getInitials = require("../utils/pinyin").getInitials; } catch (e) { /* pinyin-pro 未安装 */ }
    this._stockListPinyin = this._stockList.map(s => ({
      code: s.code,
      name: s.name,
      industry: s.industry || "",
      type: s.type || "",
      _pinyin: getInitials ? getInitials(s.name || "") : "",
    }));
  }

  /**
   * 从 xtquant 同步全量 A 股列表到数据库
   */
  async syncStocks() {
    try {
      if (typeof this.source.syncStocks === "function") {
        const result = await this.source.syncStocks();
        if (result && result.stocks) {
          // 更新内存缓存
          const { reloadStockList } = require("../datasources/mock");
          this._stockList = reloadStockList(); // 强制重载 JSON 缓存（sync 时已导出）
          this._precomputePinyin();
          console.log("[quote] stock sync complete:", result.total || result.count, "stocks");
        }
        return { ok: true, data: result };
      }
      return { ok: false, error: "当前数据源不支持股票同步" };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /**
   * 获取股票列表（确保已加载）
   */
  async getStockList() {
    try {
      await this._ensureStockList();
      return { ok: true, data: { count: this._stockList.length, stocks: this._stockList } };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /**
   * 搜索股票（代码 / 名称 / 拼音首字母）
   */
  async search(query) {
    try {
      await this._ensureStockList();
      const q = (query || "").trim().toLowerCase();
      if (!q) return { ok: true, data: [] };

      const isDigit = /^\d+$/.test(q);
      const isChinese = /[\u4e00-\u9fa5]/.test(q);

      const results = (this._stockListPinyin || []).filter(s => {
        if (isDigit) {
          return s.code.replace(/^(sh|sz|bj)/, "").includes(q) || s.code.includes(q);
        } else if (isChinese) {
          return (s.name || "").includes(q);
        } else {
          // 英文输入：匹配拼音首字母 + 代码（如 "sh600" 或 "zsyh"）
          const py = s._pinyin || "";
          return py.startsWith(q) || py.includes(q) || (s.code || "").toLowerCase().includes(q);
        }
      }).slice(0, 20).map(s => ({ code: s.code, name: s.name, industry: s.industry || "", type: s.type || "" }));

      return { ok: true, data: results };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  getStatus() {
    return {
      mode: this.source.mode || "unknown",
      description: this.source.description || "",
      connected: this.source.mode === "qmt",
      stockCount: this._stockList ? this._stockList.length : 0,
    };
  }

  dispose() {
    this._cache.clear();
    this._stockList = null;
    this._stockListPinyin = null;
    if (this.source && typeof this.source.dispose === "function") this.source.dispose();
  }
}
module.exports = { QuoteService };
