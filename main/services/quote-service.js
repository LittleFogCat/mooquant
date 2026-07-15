/**
 * mookquant · Quote Service（主进程）
 */
class QuoteService {
  constructor({ source, cacheTtlMs = 0 }) { this.source = source; this.cacheTtlMs = cacheTtlMs; this._cache = new Map(); }
  _cacheKey(s) { return (s || "").trim().toLowerCase(); }
  _cacheGet(k) { if (this.cacheTtlMs <= 0) return null; const e = this._cache.get(k); if (!e) return null; if (Date.now() - e.ts > this.cacheTtlMs) { this._cache.delete(k); return null; } return e.data; }
  _cacheSet(k, d) { if (this.cacheTtlMs <= 0) return; this._cache.set(k, { data: d, ts: Date.now() }); }
  async query(rawSymbol) {
    const key = this._cacheKey(rawSymbol);
    if (!key) return { ok: false, error: "请输入股票代码或名称" };
    const cached = this._cacheGet(key); if (cached) return { ok: true, data: cached, cached: true };
    try { const data = await this.source.getQuote(rawSymbol);
      if (!data) return { ok: false, error: "未找到该标的（" + rawSymbol + "）" };
      this._cacheSet(key, data); return { ok: true, data };
    } catch (e) { return { ok: false, error: e.message || "查询失败" }; }
  }
  async getHistory(symbol, period, count) {
    try {
      if (typeof this.source.getHistory === "function") {
        const data = await this.source.getHistory(symbol, period || "1d", count || 60);
        return { ok: true, data };
      }
      return { ok: false, error: "data source does not support history" };
    } catch (e) { return { ok: false, error: e.message }; }
  }
  async search(query) {
    try {
      if (typeof this.source.search === "function") return { ok: true, data: await this.source.search(query) };
      // 数据源无 search 方法时，回退到本地股票列表搜索
      const { searchByName } = require("../datasources/mock");
      return { ok: true, data: searchByName(query) };
    } catch (e) { return { ok: false, error: e.message }; }
  }
  getStatus() { return { mode: this.source.mode || "unknown", description: this.source.description || "", connected: this.source.mode === "qmt" }; }
  dispose() { this._cache.clear(); if (this.source && typeof this.source.dispose === "function") this.source.dispose(); }
}
module.exports = { QuoteService };