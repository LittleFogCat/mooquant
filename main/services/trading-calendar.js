/**
 * mookquant · A 股交易日历
 *
 * 唯一职责：回答「某一天是不是交易日」。
 * 调度器与执行器都只认这一个来源，避免各自维护一套周末/节假日判断。
 *
 * 数据来源优先级：
 *  1. 深交所公开日历接口（官方、免密钥，按月查询 → 拼成整年）
 *  2. 本地缓存 data/calendar/{year}.json（离线可用，默认 24h 内不重复拉取）
 *  3. 兜底：仅按周一~周五判断，calibrated=false，界面提示「未校准节假日」
 *
 * 注意：交易所日历只按年发布，跨年（如 12 月查次年）常返回空，此时回落兜底逻辑。
 */
const path = require("path");
const fs = require("fs");

const CALENDAR_DIR = path.join(__dirname, "..", "..", "data", "calendar");

/** 深交所交易日历接口（返回当月每日 jybz=1 为交易日） */
const SZSE_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=";

/** 缓存有效期：同一年份 24h 内不重复拉取 */
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 8000;
const FETCH_GAP_MS = 120;

/** 本地日期 → YYYY-MM-DD（不用 toISOString，避免 UTC 偏移串日） */
function ymd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

function isWeekend(d) {
  const w = d.getDay();
  return w === 0 || w === 6;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

class TradingCalendar {
  /**
   * @param {object} [opts]
   * @param {object} [opts.overrides] 手动覆盖 {"2026-11-25": true}，用于纠偏
   * @param {number} [opts.cacheTtlMs]
   * @param {boolean} [opts.offline] 仅用本地缓存（测试/断网降级）
   */
  constructor(opts = {}) {
    this._overrides = opts.overrides || {};
    this._cacheTtlMs = Number(opts.cacheTtlMs) > 0 ? Number(opts.cacheTtlMs) : CACHE_TTL_MS;
    this._offline = !!opts.offline;
    /** @type {Map<number, {year:number, months:Object<string,string[]>, covered:Set<string>, fetchedAt:number, source:string}>} */
    this._years = new Map();
    this._loading = null;
    this._lastError = null;
  }

  // ---------- 加载 ----------

  /**
   * 确保指定年份日历已就绪（读缓存 → 必要时联网拉取）
   * 失败不抛错：降级为「仅周一到周五」并记录 lastError。
   * @param {number} [year]
   * @returns {Promise<{ok:boolean, source:string, year:number, calibrated:boolean, error?:string}>}
   */
  async ensureLoaded(year = new Date().getFullYear()) {
    if (this._years.has(year)) return this._summarize(year);
    // 并发保护：同一时刻只允许一次加载
    if (this._loading) {
      await this._loading;
      if (this._years.has(year)) return this._summarize(year);
    }
    this._loading = this._loadYear(year).finally(() => { this._loading = null; });
    await this._loading;
    return this._summarize(year);
  }

  async _loadYear(year) {
    const cached = this._readCache(year);
    const fresh = cached && Date.now() - cached.fetchedAt < this._cacheTtlMs;
    if (cached && (fresh || this._offline)) {
      this._years.set(year, cached);
      return;
    }
    if (this._offline) {
      this._lastError = "离线模式，使用内置兜底（仅周一~周五）";
      return;
    }
    try {
      const fetched = await this._fetchYear(year);
      if (fetched && fetched.covered.size > 0) {
        this._years.set(year, fetched);
        this._writeCache(fetched);
        this._lastError = null;
      } else {
        // 拉取为空：优先沿用旧缓存，否则兜底
        if (cached) this._years.set(year, cached);
        this._lastError = "交易所日历无数据（可能尚未发布），已降级";
      }
    } catch (e) {
      if (cached) this._years.set(year, cached);
      this._lastError = "交易日历拉取失败: " + e.message;
    }
  }

  /** 逐月拉取整年交易日，返回内部结构 */
  async _fetchYear(year) {
    const months = {};
    const covered = new Set();
    for (let m = 1; m <= 12; m++) {
      const key = String(m).padStart(2, "0");
      try {
        const rows = await this._fetchMonth(year + "-" + key);
        if (rows && rows.length) {
          months[key] = rows.filter((r) => r.jybz === "1").map((r) => r.jyrq);
          covered.add(key);
        }
        if (!rows || !rows.length) break; // 未来月份为空 → 后面都不会有，提前收工
      } catch (e) {
        // 单月失败不影响整年，继续下一个
        this._lastError = "月份 " + key + " 拉取失败: " + e.message;
      }
      await sleep(FETCH_GAP_MS);
    }
    return { year, months, covered, fetchedAt: Date.now(), source: "szse" };
  }

  async _fetchMonth(month) {
    const url = SZSE_URL + encodeURIComponent(month) + "&random=" + Math.random();
    const res = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        Referer: "https://www.szse.cn/",
      },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const json = await res.json();
    return Array.isArray(json && json.data) ? json.data : [];
  }

  // ---------- 缓存 ----------

  _cachePath(year) {
    return path.join(CALENDAR_DIR, year + ".json");
  }

  _readCache(year) {
    try {
      const raw = JSON.parse(fs.readFileSync(this._cachePath(year), "utf-8"));
      if (!raw || !raw.months) return null;
      return {
        year,
        months: raw.months,
        covered: new Set(Object.keys(raw.months)),
        fetchedAt: Number(raw.fetchedAt) || 0,
        source: raw.source || "cache",
      };
    } catch {
      return null;
    }
  }

  _writeCache(entry) {
    try {
      if (!fs.existsSync(CALENDAR_DIR)) fs.mkdirSync(CALENDAR_DIR, { recursive: true });
      fs.writeFileSync(
        this._cachePath(entry.year),
        JSON.stringify(
          {
            year: entry.year,
            source: entry.source,
            fetchedAt: entry.fetchedAt,
            months: entry.months,
          },
          null,
          2
        ),
        "utf-8"
      );
    } catch (e) {
      this._lastError = "交易日历缓存写入失败: " + e.message;
    }
  }

  // ---------- 查询（同步，热路径） ----------

  /**
   * 是否交易日
   * @param {Date|string} [date]
   */
  isTradingDay(date = new Date()) {
    const key = typeof date === "string" ? date.slice(0, 10) : ymd(date);
    const override = this._overrides[key];
    if (override === true || override === false) return override;

    const d = typeof date === "string" ? new Date(key + "T00:00:00") : date;
    if (isWeekend(d)) return false;

    const year = Number(key.slice(0, 4));
    const monthKey = key.slice(5, 7);
    const entry = this._years.get(year);
    // 该月未校准（无缓存/未发布）：只能按周一~周五判断
    if (!entry || !entry.covered.has(monthKey)) return true;
    return (entry.months[monthKey] || []).includes(key);
  }

  /** 该日期是否已用交易所日历校准过（false 表示节假日判断可能不准） */
  isCalibrated(date = new Date()) {
    const key = typeof date === "string" ? date.slice(0, 10) : ymd(date);
    if (this._overrides[key] != null) return true;
    const entry = this._years.get(Number(key.slice(0, 4)));
    return !!(entry && entry.covered.has(key.slice(5, 7)));
  }

  /**
   * 下一个交易日（含当天）
   * @param {Date|string} [date]
   */
  nextTradingDay(date = new Date()) {
    const d = typeof date === "string" ? new Date(date.slice(0, 10) + "T00:00:00") : new Date(date.getTime());
    for (let i = 0; i < 30; i++) {
      if (this.isTradingDay(d)) return ymd(d);
      d.setDate(d.getDate() + 1);
    }
    return ymd(d);
  }

  /** 最近一个已加载年份的交易日总数（监控用） */
  tradingDayCount(year = new Date().getFullYear()) {
    const entry = this._years.get(year);
    if (!entry) return 0;
    return Object.values(entry.months).reduce((n, arr) => n + arr.length, 0);
  }

  getStatus() {
    const year = new Date().getFullYear();
    const entry = this._years.get(year);
    return {
      year,
      loaded: !!entry,
      source: entry ? entry.source : null,
      fetchedAt: entry ? new Date(entry.fetchedAt).toISOString() : null,
      coveredMonths: entry ? Array.from(entry.covered).sort() : [],
      tradingDays: this.tradingDayCount(year),
      todayCalibrated: this.isCalibrated(new Date()),
      lastError: this._lastError,
    };
  }

  _summarize(year) {
    const entry = this._years.get(year);
    return {
      ok: !!entry,
      year,
      source: entry ? entry.source : "fallback-weekday-only",
      calibrated: entry ? entry.covered.size > 0 : false,
      error: this._lastError || undefined,
    };
  }
}

module.exports = { TradingCalendar, ymd, isWeekend };
