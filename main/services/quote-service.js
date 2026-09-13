/**
 * mookquant · Quote Service（主进程）
 */
class QuoteService {
  constructor({ source, cacheTtlMs = 0, configuredMode = "", recreateSource = null }) {
    this.source = source;
    this.cacheTtlMs = cacheTtlMs;
    this._cache = new Map();
    this._stockList = null;        // 缓存的股票列表
    this._stockListPinyin = null;  // 预计算拼音首字母
    this._stockListPromise = null; // 防止重复加载
    this._configuredMode = configuredMode;  // 配置的数据源模式（auto 回落 mock 时供 UI 提示）
    this._recreateSource = recreateSource;  // 重建数据源工厂（重连时 auto 回落 mock 场景用）
    this._reconnecting = false;    // 重连进行中标志（防并发重入）
    this._superviseTimer = null;   // 断线自动重连巡检定时器
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

  /**
   * 数据源状态（真实探测，不凭 mode 字段判断）。
   * QMT 源向桥发带数据探测的 ping，connected 仅在链路真的可用时为 true；
   * mock 源 connected 恒为 false（UI 按 mode 展示 Mock 模式）。
   */
  async getStatus() {
    const status = {
      mode: this.source.mode || "unknown",
      description: this.source.description || "",
      configuredMode: this._configuredMode,
      connected: false,
      error: null,
      reconnecting: this._reconnecting,
      stockCount: this._stockList ? this._stockList.length : 0,
    };
    if (this.source.mode === "qmt" && typeof this.source.ping === "function") {
      const p = await this.source.ping();
      status.connected = !!p.connected;
      status.error = p.error || null;
    }
    return status;
  }

  /**
   * 重连数据源：
   *  - QMT 源：带重连标志 ping（重置桥内连接状态）；桥进程死亡则重启子进程
   *  - auto 回落 mock：按原配置重建数据源，QMT 恢复可用则切换回真实行情
   */
  async reconnect() {
    if (this._reconnecting) return this.getStatus();
    this._reconnecting = true;
    try {
      const src = this.source;
      if (src && typeof src.ping === "function") {
        const p = await src.ping({ reconnect: true });
        if (!p.alive && typeof src.restart === "function") {
          try {
            await src.restart();
          } catch (e) {
            console.warn("[quote] 重启 QMT 桥失败:", e.message);
          }
        }
      } else if (typeof this._recreateSource === "function") {
        try {
          const fresh = await this._recreateSource();
          if (fresh && fresh !== src && fresh.mode === "qmt") {
            if (typeof src.dispose === "function") src.dispose();
            this.source = fresh;
            this._cache.clear();
            this._stockList = null;
            this._stockListPinyin = null;
            console.log("[quote] 数据源已重建并切换到 QMT");
          } else if (fresh && typeof fresh.dispose === "function") {
            // 重建后仍是 mock（QMT 仍不可用），丢弃新实例维持现状
            fresh.dispose();
          }
        } catch (e) {
          console.warn("[quote] 重建数据源失败:", e.message);
        }
      }
    } finally {
      this._reconnecting = false;
    }
    // 在标志复位后再取状态，避免返回值里残留 reconnecting: true
    return this.getStatus();
  }

  /**
   * 断线自动重连巡检：定时 ping 数据源，未连接则重连。
   * 仅覆盖 QMT 源的链路自愈；auto 回落 mock 的场景由用户 F5 手动触发重建。
   */
  startSupervision(intervalMs = 10000) {
    if (this._superviseTimer) return;
    this._superviseTimer = setInterval(async () => {
      if (this._reconnecting) return;
      try {
        if (this.source.mode !== "qmt" || typeof this.source.ping !== "function") return;
        const s = await this.getStatus();
        if (!s.connected) {
          console.warn("[quote] 数据源未连接，自动重连...", s.error || "");
          await this.reconnect();
        }
      } catch (e) {
        // 巡检异常不影响下一轮
      }
    }, intervalMs);
  }

  dispose() {
    if (this._superviseTimer) { clearInterval(this._superviseTimer); this._superviseTimer = null; }
    this._cache.clear();
    this._stockList = null;
    this._stockListPinyin = null;
    if (this.source && typeof this.source.dispose === "function") this.source.dispose();
  }
}
module.exports = { QuoteService };
