/**
 * mookquant · 东方财富妙想 · 模拟交易数据源
 *
 * 对接妙想 Skills 的模拟组合交易接口（官方 REST，非 QMT）：
 *   POST {baseUrl}/api/claw/mockTrading/{trade,cancel,positions,orders,balance}
 *   认证：请求头 apikey: <MX_APIKEY>
 *
 * 关键约定（实测）：
 *  1. HTTP 恒为 200，成败在 body 的 success/status 上，必须解析 body 判错；
 *  2. 金额单位是「厘」（currencyUnit=1000），对外统一换算成「元」；
 *  3. 价格是放大后的整数，需按 priceDec/costPriceDec 还原；
 *  4. 代码只支持 A 股 6 位数字，内部 sh600519 需去掉市场前缀；
 *  5. 每日调用次数有上限，读接口做了短 TTL 缓存。
 *
 * 依赖：Node 18+ 内置 fetch，无第三方库。
 */

const DEFAULT_BASE_URL = "https://mkapi2.dfcfs.com/finskillshub";
const CURRENCY_UNIT = 1000; // 厘 → 元

/** 委托状态码 → 统一状态（与 mock/qmt 数据源保持一致的四态） */
const ORDER_STATUS = {
  1: "pending",  // 未报
  2: "pending",  // 已报
  3: "partial",  // 部成
  4: "filled",   // 已成
  5: "pending",  // 部成待撤
  6: "pending",  // 已报待撤
  7: "cancelled",// 部撤
  8: "cancelled",// 已撤
  9: "rejected", // 废单
  10: "cancelled",// 撤单失败
};

/** 读接口缓存时长（毫秒）：保护每日调用额度，策略 60s tick 不会打穿 */
const READ_CACHE_MS = 5000;

class EastmoneySimTradeDataSource {
  /**
   * @param {object} options
   * @param {string} [options.apiKey]  妙想 API Key，缺省读环境变量 MX_APIKEY
   * @param {string} [options.baseUrl] 接口基址，缺省读 MX_API_URL
   * @param {string} [options.accountId] 绑定的模拟组合 ID（仅用于信息展示）
   */
  constructor(options = {}) {
    this.mode = "eastmoney";
    this.description = "东财妙想模拟盘";
    this._options = options;
    this._apiKey = (options.apiKey || process.env.MX_APIKEY || "").trim();
    this._baseUrl = (options.baseUrl || process.env.MX_API_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
    this._accountId = options.accountId || process.env.MX_ACCOUNT_ID || "";
    this._cache = new Map(); // method -> { at, data }
  }

  /** 启动自检：没有 key 直接失败，让工厂回落到 mock */
  async init() {
    if (!this._apiKey) {
      throw new Error("缺少 MX_APIKEY（请写入项目根目录 .env）");
    }
    // 用一次资金查询验证 key 与绑定关系
    await this.getAccount({ force: true });
  }

  // ---------------------------------------------------------------- 内部工具

  _url(path) {
    return this._baseUrl + path;
  }

  /**
   * 统一请求：解析 body，把 success=false 转成异常
   * 频控（112 请求频率过高）自动退避重试，其余错误直接抛出
   * @param {string} path
   * @param {object} body
   * @param {number} [attempt] 内部重试计数
   */
  async _post(path, body = {}, attempt = 0) {
    let res;
    try {
      res = await fetch(this._url(path), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          apikey: this._apiKey,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this._options.timeoutMs || 15000),
      });
    } catch (e) {
      throw new Error("东财模拟盘请求失败: " + e.message);
    }

    let json;
    const text = await res.text();
    try {
      json = JSON.parse(text);
    } catch {
      throw new Error("东财模拟盘返回非 JSON: " + text.slice(0, 120));
    }

    // 频控：等一会儿重试（柜台限制较严，最多 3 次）
    if (String(json.status) === "112" && attempt < 3) {
      await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
      return this._post(path, body, attempt + 1);
    }

    // 成功判定：响应结构不统一（balance→{success,status}；trade/cancel→{code,message}；
    // positions/orders→{code:"200"}），任一“失败型”字段出现即视为失败
    const okValues = new Set(["0", "200"]);
    const statusBad = json.status !== undefined && !okValues.has(String(json.status));
    // trade/cancel 的失败响应只有 code（如 501 当前时间不可交易），必须单独判
    const codeBad = json.code !== undefined && !okValues.has(String(json.code));
    const failed = json.success === false || statusBad || codeBad;
    if (failed || res.status >= 400) {
      const msg = json.message || json.msg || ("HTTP " + res.status);
      const tag = json.status !== undefined ? json.status : json.code;
      throw new Error("东财模拟盘: " + msg + (tag !== undefined ? " (" + tag + ")" : ""));
    }
    return json.data !== undefined ? json.data : json;
  }

  /** 读缓存（同一 method 在 TTL 内复用，避免消耗每日额度） */
  async _cached(key, loader, force = false) {
    const now = Date.now();
    const hit = this._cache.get(key);
    if (!force && hit && now - hit.at < READ_CACHE_MS) return hit.data;
    const data = await loader();
    this._cache.set(key, { at: now, data });
    return data;
  }

  /** 内部代码（sh600519/sz000001/600519）→ 东财 6 位代码 */
  _toEastmoneyCode(symbol) {
    const s = String(symbol || "").trim().toLowerCase();
    const m = s.match(/(\d{6})/);
    if (!m) throw new Error("东财模拟盘仅支持 A 股 6 位代码: " + symbol);
    return m[1];
  }

  /**
   * 取最新价（东财公开行情，不消耗妙想额度）
   * 用于把「市价委托」转成可成交的激进限价单
   * @param {string} stockCode 6 位代码
   * @returns {Promise<number>} 最新价，失败返回 0
   */
  async _lastPrice(stockCode) {
    const sh = /^[659]/.test(stockCode); // 6/5/9 开头归上交所
    const secid = (sh ? "1." : "0.") + stockCode;
    const res = await fetch(
      "https://push2.eastmoney.com/api/qt/stock/get?secid=" + secid + "&fields=f43",
      {
        headers: { Referer: "https://quote.eastmoney.com/" },
        signal: AbortSignal.timeout(this._options.timeoutMs || 15000),
      }
    );
    const j = await res.json();
    const p = j && j.data && j.data.f43;
    const n = Number(p);
    return Number.isFinite(n) && n > 0 ? n / 100 : 0;
  }

  /** 东财代码 + 市场号 → 内部代码（0=深 1=沪） */  _toInternalCode(code, market) {
    const c = String(code || "");
    if (market === 1) return "sh" + c;
    if (market === 0) return "sz" + c;
    return c;
  }

  /** 厘 → 元（保留 2 位） */
  _money(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return 0;
    return Math.round((n / CURRENCY_UNIT) * 100) / 100;
  }

  /** 放大整数 + 小数位 → 浮点价格 */
  _price(v, dec) {
    const n = Number(v);
    const d = Number(dec);
    if (!Number.isFinite(n)) return 0;
    const base = Number.isFinite(d) && d >= 0 ? Math.pow(10, d) : 1;
    return n / base;
  }

  // ---------------------------------------------------------------- 交易接口

  /**
   * 下单
   * @param {{symbol:string, side:'buy'|'sell', quantity:number, price?:number, orderType:'limit'|'market'}} order
   * @returns {Promise<{orderId:string, status:string}>}
   */
  async placeOrder(order) {
    const stockCode = this._toEastmoneyCode(order.symbol);
    const isMarket = order.orderType === "market";

    // 整手校验：东财柜台只接受 100 的整数倍
    const qty = Math.floor(Number(order.quantity) / 100) * 100;
    if (qty <= 0) {
      throw new Error("东财模拟盘委托数量须为 100 的整数倍（当前 " + order.quantity + "）");
    }

    const isSell = order.side === "sell";
    const body = {
      type: isSell ? "sell" : "buy",
      stockCode,
      quantity: qty,
      useMarketPrice: false,
    };

    if (isMarket) {
      // 实测：东财模拟盘对 useMarketPrice=true 的市价委托不撮合（长期停留「已报」）。
      // 改为按现价取一个可成交的激进价，用限价单下单（柜台按最新价成交）。
      const last = await this._lastPrice(stockCode).catch(() => 0);
      if (last > 0) {
        const slip = isSell ? 0.98 : 1.02;
        body.price = Math.round(last * slip * 100) / 100;
      } else {
        // 拿不到行情时退回官方市价语义
        body.useMarketPrice = true;
      }
    } else {
      const price = Number(order.price);
      if (!(price > 0)) throw new Error("限价单价格必须大于 0");
      body.price = price;
    }

    const data = await this._post("/api/claw/mockTrading/trade", body);
    this._cache.clear();

    // 柜台的下单响应通常不带委托号（实测 data 为空），回查最近一笔同标的同向委托补齐
    let orderId = (data && (data.orderId || data.id)) || "";
    let status = (data && data.status) || "submitted";
    if (!orderId) {
      try {
        const list = await this.getOrders({ force: true });
        const fresh = Date.now() - 120000; // 只认最近 2 分钟内的委托，避免匹配到历史同参数单
        const hit = list
          .filter((o) => o.code === stockCode && o.side === body.type && o.quantity === qty)
          .filter((o) => {
            const t = o.createdAt ? new Date(o.createdAt).getTime() : 0;
            return t >= fresh;
          })
          .sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")))[0];
        if (hit) {
          orderId = hit.orderId;
          status = hit.status;
        }
      } catch {
        // 回查失败不影响下单结果，orderId 留空由调用方按状态处理
      }
    }
    return { orderId, status };
  }

  /**
   * 撤单：东财要求同时给出 orderId 与 stockCode，故先从委托列表反查
   * @param {string} orderId
   */
  async cancelOrder(orderId) {
    const orders = await this.getOrders({ force: true });
    const target = (orders || []).find((o) => String(o.orderId) === String(orderId));
    if (!target) throw new Error("未找到该委托: " + orderId);

    const bodys = {
      type: "order",
      orderId: String(orderId),
      stockCode: this._toEastmoneyCode(target.symbol),
    };
    await this._post("/api/claw/mockTrading/cancel", bodys);
    this._cache.clear();
    return { success: true };
  }

  /** 持仓列表（统一领域模型） */
  async getPositions(opts = {}) {
    return this._cached("positions", async () => {
      const data = await this._post("/api/claw/mockTrading/positions");
      const unit = data && data.currencyUnit ? data.currencyUnit : CURRENCY_UNIT;
      const list = (data && data.posList) || [];
      return list.map((p) => {
        const price = this._price(p.price, p.priceDec);
        const costPrice = this._price(p.costPrice, p.costPriceDec);
        const marketValue = Number(p.value) / unit;
        return {
          symbol: this._toInternalCode(p.secCode, p.secMkt),
          code: p.secCode,
          name: p.secName,
          quantity: Number(p.count) || 0,
          available: Number(p.availCount) || 0,
          costPrice: Math.round(costPrice * 1000) / 1000,
          currentPrice: Math.round(price * 1000) / 1000,
          marketValue: Math.round(marketValue * 100) / 100,
          pnl: Math.round((Number(p.profit) / unit) * 100) / 100,
          pnlPct: Number(p.profitPct) || 0,
          dayPnl: Math.round((Number(p.dayProfit) / unit) * 100) / 100,
          dayPnlPct: Number(p.dayProfitPct) || 0,
          positionPct: Number(p.posPct) || 0,
        };
      });
    }, opts.force);
  }

  /** 委托列表（含已成交/已撤） */
  async getOrders(opts = {}) {
    return this._cached("orders", async () => {
      const data = await this._post("/api/claw/mockTrading/orders", { fltOrderDrt: 0, fltOrderStatus: 0 });
      const list = (data && data.orders) || [];
      return list.map((o) => ({
        orderId: String(o.id || ""),
        symbol: this._toInternalCode(o.secCode, o.secMkt),
        code: o.secCode,
        name: o.secName,
        side: Number(o.drt) === 2 ? "sell" : "buy",
        price: this._price(o.price, o.priceDec),
        quantity: Number(o.count) || 0,
        filledQuantity: Number(o.tradeCount) || 0,
        filledPrice: this._price(o.tradePrice, o.priceDec),
        orderType: Number(o.type) === 5 ? "market" : "limit",
        status: ORDER_STATUS[Number(o.status)] || "unknown",
        rawStatus: Number(o.status),
        createdAt: o.time ? new Date(Number(o.time) * 1000).toISOString() : null,
      }));
    }, opts.force);
  }

  /** 资金账户 */
  async getAccount(opts = {}) {
    return this._cached("account", async () => {
      const d = await this._post("/api/claw/mockTrading/balance");
      const unit = d.currencyUnit || CURRENCY_UNIT;
      const totalAssets = Number(d.totalAssets) / unit;
      const marketValue = Number(d.totalPosValue) / unit;
      return {
        accountId: d.accID || this._accountId,
        accountName: d.accName || "",
        totalAssets: Math.round(totalAssets * 100) / 100,
        available: Math.round((Number(d.availBalance) / unit) * 100) / 100,
        marketValue: Math.round(marketValue * 100) / 100,
        frozen: Math.round((Number(d.frozenMoney) / unit) * 100) / 100,
        totalPnl: Math.round(((totalAssets - Number(d.initMoney) / unit)) * 100) / 100,
        initMoney: Math.round((Number(d.initMoney) / unit) * 100) / 100,
        nav: Number(d.nav) || 1,
        positionPct: Number(d.totalPosPct) || 0,
        oprDays: Number(d.oprDays) || 0,
      };
    }, opts.force);
  }

  dispose() {
    this._cache.clear();
  }
}

module.exports = { EastmoneySimTradeDataSource };
