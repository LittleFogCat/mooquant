/**
 * mookquant · 本地服务 · 路由表
 *
 * 每条路由自描述（path / family / stage / desc / params），`GET /mooquant/api`
 * 的接口索引即由此表生成，新增接口无需手改文档。
 *
 * 本层只做参数解析、校验、整形与 Service 调用；业务逻辑一律在 Service。
 * 路由只注入 quoteService / marketService —— tradeService 从不进入 HTTP 层。
 */
const M = require("./middleware");
const { CODE, ApiError } = M;

const STATUS_CACHE_MS = 5000; // /status 真实探测代价不低，HTTP 层缓存

/** 快照整形：code 归一为 UI 格式、去掉桥内部字段 rawSymbol、时间戳统一为本地 ISO */
function shapeQuote(q) {
  if (!q || typeof q !== "object") return q;
  const out = Object.assign({}, q);
  delete out.rawSymbol;
  if (out.code) out.code = M.toUiCode(out.code);
  if (out.timestamp) {
    const s = String(out.timestamp);
    // mock 返回 toISOString()（UTC + 毫秒 + Z），桥返回本地 "%Y-%m-%dT%H:%M:%S"；统一成后者
    if (/Z$|[+-]\d{2}:?\d{2}$|\.\d{3}/.test(s)) {
      const d = new Date(s);
      if (!isNaN(d.getTime())) out.timestamp = M.isoNow(d);
    }
  }
  return out;
}

function buildRoutes({ quoteService, maxBatchCodes = 50, maxHistoryCount = 5000 }) {
  const routes = [
    // ---------------- 服务 ----------------
    {
      path: "/mooquant/api",
      family: "service",
      stage: "P0",
      desc: "接口列表（自描述）",
      params: [],
      cache: "no-store",
      gate: "snapshot",
      handler: async (ctx) => ctx.server.info(routes),
    },
    {
      path: "/mooquant/api/status",
      family: "service",
      stage: "P0",
      desc: "服务与数据源状态",
      params: [
        { name: "probe", type: "int", required: false, default: 1, desc: "1 真实探测数据链路；0 只查桥进程存活" },
      ],
      cache: "no-store",
      gate: "snapshot",
      handler: async (ctx) => {
        // probe 参数在 P0 仅用于缓存键区分：QuoteService.getStatus() 恒为真实探测
        M.flagParam(ctx.params, "probe", 1);
        const st = await ctx.server.getCached("status", STATUS_CACHE_MS, () => quoteService.getStatus());
        return Object.assign({}, st, { server: ctx.server.serverInfo() });
      },
    },

    // ---------------- 实时行情 ----------------
    {
      path: "/mooquant/api/quote",
      family: "realtime",
      stage: "P0",
      desc: "单标的快照",
      params: [{ name: "code", type: "string", required: true, default: null, desc: "标的代码，sh600519 / 600519.SH" }],
      cache: "public, max-age=5",
      gate: "snapshot",
      handler: async (ctx) => {
        const code = M.toUiCode(M.strParam(ctx.params, "code", { required: true, maxLen: 32 }));
        const r = await quoteService.query(code);
        if (!r || !r.ok) throw new ApiError(CODE.NOT_FOUND, (r && r.error) || `未找到该标的（${code}）`);
        return shapeQuote(r.data);
      },
    },
    {
      path: "/mooquant/api/quotes",
      family: "realtime",
      stage: "P0",
      desc: "批量快照（扇出）",
      params: [
        { name: "codes", type: "string", required: true, default: null, desc: "逗号分隔，最多 50 个" },
        { name: "skipError", type: "int", required: false, default: 1, desc: "1 单个标的失败不影响整体" },
      ],
      cache: "public, max-age=5",
      gate: "snapshot",
      handler: async (ctx) => {
        const codes = M.csvParam(ctx.params, "codes", { required: true, maxItems: maxBatchCodes, normalize: M.toUiCode });
        const skipError = M.flagParam(ctx.params, "skipError", 1) === 1;
        const r = await quoteService.queryMany(codes, { skipError });
        if (!r || !r.ok) throw new ApiError(CODE.NOT_FOUND, (r && r.error) || "查询失败");
        return { count: r.data.count, quotes: r.data.quotes.map(shapeQuote), errors: r.data.errors };
      },
    },

    // ---------------- 历史行情 ----------------
    {
      path: "/mooquant/api/history",
      family: "history",
      stage: "P0",
      desc: "K 线历史",
      // start / end / ensure 属 P1（需桥侧改造），P0 暂不提供：
      // 当前桥的 handle_quote_history 在缓存未命中时会无条件 download_history_data，
      // 因此 P0 的 /history 仍会触发补数据并可能长时间占用桥（见文档 3.4.0）。
      params: [
        { name: "code", type: "string", required: true, default: null, desc: "标的代码" },
        { name: "period", type: "string", required: false, default: "1d", desc: "1m 5m 15m 30m 1h 1d 1w 1mon" },
        { name: "count", type: "int", required: false, default: 60, desc: "数据条数；-1 表示全部（归一为上限）" },
        { name: "dividendType", type: "string", required: false, default: "front", desc: "none front back front_ratio back_ratio" },
      ],
      cache: "public, max-age=60",
      gate: "history",
      handler: async (ctx) => {
        const code = M.toUiCode(M.strParam(ctx.params, "code", { required: true, maxLen: 32 }));
        const period = M.enumParam(ctx.params, "period", ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mon"], "1d");
        const rawCount = M.intParam(ctx.params, "count", { def: 60, min: -1, max: maxHistoryCount });
        const count = rawCount === -1 ? maxHistoryCount : rawCount;
        const dividendType = M.enumParam(ctx.params, "dividendType", ["none", "front", "back", "front_ratio", "back_ratio"], "front");
        if (M.flagParam(ctx.params, "ensure", 0) === 1) {
          console.warn("[local-http] /history 收到 ensure=1：P0 桥尚未支持该开关，实际行为仍是补数据");
        }
        const r = await quoteService.getHistory(code, period, count, dividendType);
        if (!r || !r.ok) throw new ApiError(CODE.NOT_FOUND, (r && r.error) || `未找到该标的（${code}）`);
        const d = r.data || {};
        return { code, period, count: d.count || (d.bars ? d.bars.length : 0), bars: d.bars || [] };
      },
    },

    // ---------------- 基础数据 ----------------
    {
      path: "/mooquant/api/search",
      family: "basic",
      stage: "P0",
      desc: "代码/名称/拼音搜索",
      params: [
        { name: "q", type: "string", required: true, default: null, desc: "查询词" },
        { name: "limit", type: "int", required: false, default: 20, desc: "返回条数上限，最大 50" },
      ],
      cache: "public, max-age=300",
      gate: "snapshot",
      handler: async (ctx) => {
        const q = M.strParam(ctx.params, "q", { required: true, maxLen: 64 });
        const limit = M.intParam(ctx.params, "limit", { def: 20, min: 1, max: 50 });
        const r = await quoteService.search(q, limit);
        if (!r || !r.ok) throw new ApiError(CODE.SOURCE_NOT_READY, (r && r.error) || "搜索失败");
        return r.data;
      },
    },
    {
      path: "/mooquant/api/stocks",
      family: "basic",
      stage: "P0",
      desc: "标的列表（分页）",
      params: [
        { name: "offset", type: "int", required: false, default: 0, desc: "起始偏移" },
        { name: "limit", type: "int", required: false, default: 100, desc: "每页条数，最大 1000" },
        { name: "type", type: "string", required: false, default: null, desc: "A股 创业板 科创板 北交所 ETF" },
        { name: "exchange", type: "string", required: false, default: null, desc: "SH SZ BJ" },
        { name: "q", type: "string", required: false, default: null, desc: "关键字过滤" },
      ],
      cache: "public, max-age=300",
      gate: "snapshot",
      handler: async (ctx) => {
        const offset = M.intParam(ctx.params, "offset", { def: 0, min: 0 });
        const limit = M.intParam(ctx.params, "limit", { def: 100, min: 1, max: 1000 });
        const type = M.strParam(ctx.params, "type", { def: null, maxLen: 16 });
        const exchange = M.strParam(ctx.params, "exchange", { def: null, maxLen: 8 });
        const q = M.strParam(ctx.params, "q", { def: null, maxLen: 64 });
        const r = await quoteService.listStocks({ offset, limit, type, exchange, q });
        if (!r || !r.ok) throw new ApiError(CODE.SOURCE_NOT_READY, (r && r.error) || "读取标的列表失败");
        return r.data;
      },
    },
  ];

  return routes;
}

module.exports = { buildRoutes, shapeQuote };
