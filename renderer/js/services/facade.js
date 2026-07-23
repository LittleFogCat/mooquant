/**
 * mookquant · 渲染层 Facade
 * Electron 模式：使用 preload.js 注入的 window.mookquant.facade
 * 浏览器模式：返回 mock facade
 */
export function getFacade() {
if (window.mookquant && window.mookquant.facade) { return window.mookquant.facade; }
  console.warn("[facade] 浏览器演示模式");

  const STOCK_LIST = [
    {code:"sh600519",name:"贵州茅台",industry:"白酒",base:1680.50},
    {code:"sz000001",name:"平安银行",industry:"银行",base:12.34},
    {code:"sh601318",name:"中国平安",industry:"保险",base:48.20},
    {code:"sz300750",name:"宁德时代",industry:"新能源",base:198.76},
    {code:"sh600036",name:"招商银行",industry:"银行",base:35.12},
    {code:"sz000858",name:"五粮液",industry:"白酒",base:142.88},
    {code:"sh600276",name:"恒瑞医药",industry:"医药",base:45.30},
    {code:"sz000651",name:"格力电器",industry:"家电",base:38.50},
    {code:"sh600887",name:"伊利股份",industry:"食品",base:28.60},
    {code:"sz002594",name:"比亚迪",industry:"汽车",base:245.80},
    {code:"sh600030",name:"中信证券",industry:"证券",base:22.15},
    {code:"sz000333",name:"美的集团",industry:"家电",base:65.40},
    {code:"sh601012",name:"隆基绿能",industry:"光伏",base:22.80},
    {code:"sz002475",name:"立讯精密",industry:"电子",base:38.90},
    {code:"sh600900",name:"长江电力",industry:"电力",base:28.50},
    {code:"sz000725",name:"京东方A",industry:"电子",base:4.35},
    {code:"sh601888",name:"中国中免",industry:"旅游",base:85.60},
    {code:"sz300059",name:"东方财富",industry:"证券",base:14.80},
    {code:"sh600809",name:"山西汾酒",industry:"白酒",base:220.50},
    {code:"sz002714",name:"牧原股份",industry:"养殖",base:45.60},
  ];

  const MOCK_TABLE = {};
  for (const s of STOCK_LIST) MOCK_TABLE[s.code] = s;

  function normalize(raw) {
    const s = (raw || "").trim().toLowerCase();
    if (!s) return "";
    if (s.startsWith("sh") || s.startsWith("sz") || s.startsWith("bj")) return s;
    if (/^d{6}$/.test(s)) { const f = s[0];
      if (f === "6" || f === "9" || f === "5") return "sh" + s;
      if (f === "0" || f === "2" || f === "3") return "sz" + s;
      if (f === "8" || f === "4") return "bj" + s;
      return "sh" + s; }
    return s;
  }
  function detectMarket(symbol) {
    if (symbol.startsWith("sh")) return { code: symbol.toUpperCase(), market: "sh", marketName: "上海", currency: "CNY" };
    if (symbol.startsWith("sz")) return { code: symbol.toUpperCase(), market: "sz", marketName: "深圳", currency: "CNY" };
    if (symbol.startsWith("bj")) return { code: symbol.toUpperCase(), market: "bj", marketName: "北京", currency: "CNY" };
    return { code: symbol.toUpperCase(), market: "sh", marketName: "上海", currency: "CNY" };
  }
  function searchStocks(q) {
    q = (q || "").trim().toLowerCase();
    if (!q) return [];
    var isDigit = /^\d+$/.test(q);
    var isChinese = /[\u4e00-\u9fa5]/.test(q);
    return STOCK_LIST.filter(s => {
      if (isDigit) {
        return s.code.replace(/^(sh|sz|bj)/, "").includes(q) || s.code.includes(q);
      } else if (isChinese) {
        return s.name.includes(q);
      } else {
        if (window.PinyinUtils) {
          var initials = window.PinyinUtils.getInitials(s.name);
          if (initials.startsWith(q) || initials.includes(q)) return true;
        }
        return false;
      }
    }).slice(0, 10);
  }
  async function mockQuery(rawSymbol) {
    let symbol = normalize(rawSymbol);
    if (!symbol) { const m = searchStocks(rawSymbol); if (m.length > 0) symbol = m[0].code; }
    if (!symbol) return { ok: false, error: "请输入有效的A股代码或名称" };
    await new Promise(r => setTimeout(r, 300));
    const preset = MOCK_TABLE[symbol]; const si = detectMarket(symbol);
    let base, name, industry;
    if (preset) { base = preset.base; name = preset.name; industry = preset.industry; }
    else { let h = 0; for (let i = 0; i < symbol.length; i++) h = (h * 31 + symbol.charCodeAt(i)) | 0; base = 10 + Math.abs(h % 9900) / 10; name = "未知A股 " + si.code; industry = "-"; }
    const now = new Date(); const tick = Math.floor(now.getTime() / 60000);
    const seed = ((tick * 9301 + 49297) % 233280) / 233280; const drift = (seed - 0.5) * 0.06;
    const price = +(base * (1 + drift)).toFixed(2); const change = +(price - base).toFixed(2);
    return { ok: true, data: { code: si.code, name, industry, market: si.market, marketName: si.marketName, currency: si.currency,
      price, open: +(base * (1 + (seed * 0.02 - 0.01))).toFixed(2), high: +(price * 1.015).toFixed(2), low: +(price * 0.985).toFixed(2),
      prevClose: +base.toFixed(2), change, changePercent: +((change / base) * 100).toFixed(2),
      volume: Math.floor(10000 + Math.abs(seed) * 1000000), turnover: Math.floor(100000000 + Math.abs(seed) * 5000000000), timestamp: now.toISOString() } };
  }

  // Mock strategy
  const _ST_KEY = "mookquant_strategies";
  let _strats = JSON.parse(localStorage.getItem(_ST_KEY) || "[]"); let _stSeq = 0;
  const _strategyTypes = [
    { name: "ma_cross", display_name: "双均线交叉", description: "快线上穿慢线买入，下穿卖出", version: "1.0", trigger_mode: "bar", params_schema: [{ key: "fast", type: "int", default: 5, min: 1, max: 60, label: "快线周期" }, { key: "slow", type: "int", default: 20, min: 2, max: 250, label: "慢线周期" }] },
    { name: "momentum", display_name: "动量策略", description: "动量突破", version: "1.0", trigger_mode: "bar", params_schema: [{ key: "period", type: "int", default: 20, min: 1, max: 120, label: "周期" }, { key: "threshold", type: "float", default: 0.02, min: 0, max: 1, label: "阈值" }] },
    { name: "mean_reversion", display_name: "均值回归", description: "偏离均值回归", version: "1.0", trigger_mode: "bar", params_schema: [{ key: "period", type: "int", default: 20, min: 1, max: 120, label: "周期" }, { key: "deviation", type: "float", default: 2.0, min: 0.1, max: 5, label: "偏离倍数" }] },
  ];
  const strategyMock = {
    list: async () => ({ ok: true, data: _strats }),
    types: async () => ({ ok: true, data: _strategyTypes }),
    export: async () => ({ ok: false, error: "浏览器模式不支持策略导出" }),
    addType: async () => ({ ok: false, error: "浏览器模式不支持添加策略" }),
    deleteType: async () => ({ ok: false, error: "浏览器模式不支持删除策略" }),
    get: async (id) => ({ ok: true, data: _strats.find(s => s.id === id) }),
    create: async (p) => { const now = new Date().toISOString(); const s = { ...p, id: "st_" + (++_stSeq) + "_" + Date.now().toString(36), createdAt: now, updatedAt: now }; _strats.unshift(s); localStorage.setItem(_ST_KEY, JSON.stringify(_strats)); return { ok: true, data: s }; },
    update: async (id, payload) => { const i = _strats.findIndex(s => s.id === id); if (i < 0) return { ok: false, error: "not found" }; _strats[i] = { ..._strats[i], ...payload, updatedAt: new Date().toISOString() }; localStorage.setItem(_ST_KEY, JSON.stringify(_strats)); return { ok: true, data: _strats[i] }; },
    delete: async (id) => { _strats = _strats.filter(s => s.id !== id); localStorage.setItem(_ST_KEY, JSON.stringify(_strats)); return { ok: true, data: { id } }; },
  };
  // Mock backtest
  const backtestMock = {
    run: async (config) => {
      await new Promise(r => setTimeout(r, 1200));
      const strat = _strats.find(s => s.id === config.strategyId);
      if (!strat) return { ok: false, error: "strategy not found" };
      const initCap = config.initialCapital || 1000000; const days = 60, eq = [], trades = [];
      let cash = initCap, pos = 0, cp = 0;
      for (let i = 0; i < days; i++) { const ret = (Math.random() - 0.5) * 0.02; const price = 100 * (1 + Math.sin(i / 10) * 0.1 + ret);
        if (i !== 0 && i % 20 === 0) { if (pos === 0) { pos = 100; cp = price; cash -= price * 100; trades.push({ date: "2024-01" + String(i).padStart(2, "0"), side: "buy", symbol: (strat.symbols||[])[0] || "sh600519", price: +price.toFixed(2), quantity: 100, amount: +(price*100).toFixed(2), pnl: null }); }
          else { const pnl = (price - cp) * 100; cash += price * 100; pos = 0; trades.push({ date: "2024-01" + String(i).padStart(2, "0"), side: "sell", symbol: (strat.symbols||[])[0] || "sh600519", price: +price.toFixed(2), quantity: 100, amount: +(price*100).toFixed(2), pnl: +pnl.toFixed(2) }); } }
        eq.push({ date: "2024-01" + String(i).padStart(2, "0"), value: +(cash + pos * price).toFixed(2) }); }
      const fv = eq[eq.length - 1].value; const tr = (fv - initCap) / initCap * 100;
      const sells = trades.filter(t => t.side === "sell"); const wins = sells.filter(t => t.pnl > 0);
      return { ok: true, data: { metrics: { totalReturn: +tr.toFixed(2), annualReturn: +tr.toFixed(2), maxDrawdown: +(Math.random() * 5).toFixed(2), sharpeRatio: +(Math.random() * 1.5).toFixed(2), winRate: wins.length ? +(wins.length / sells.length * 100).toFixed(2) : 0, profitLossRatio: +(Math.random() * 2).toFixed(2), totalTrades: trades.length, finalCapital: +fv.toFixed(2) }, trades, equityCurve: eq } };
    },
  };
  // Mock trade
  let _tCash = 1000000, _tPos = [], _tOrders = [], _odSeq = 0;
  function _tPrice(sym) { let h = 0; for (let i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) | 0; return 10 + Math.abs(h % 9900) / 10; }
  const tradeMock = {
    info: async () => ({ ok: true, data: { mode: "mock", description: "mock trade" } }),
    placeOrder: async (o) => { const oid = "od_" + (++_odSeq); const fp = o.orderType === "market" ? _tPrice(o.symbol) : o.price; const now = new Date().toISOString(); _tOrders.unshift({ ...o, orderId: oid, price: fp, status: "filled", createdAt: now }); if (o.side === "buy") { _tCash -= fp * o.quantity; const ex = _tPos.find(p => p.symbol === o.symbol); if (ex) { ex.costPrice = (ex.costPrice * ex.quantity + fp * o.quantity) / (ex.quantity + o.quantity); ex.quantity += o.quantity; } else { _tPos.push({ symbol: o.symbol, name: o.symbol, quantity: o.quantity, costPrice: fp, currentPrice: fp, marketValue: fp * o.quantity, pnl: 0, pnlPct: 0 }); } } else { _tCash += fp * o.quantity; const ex = _tPos.find(p => p.symbol === o.symbol); if (ex) { ex.quantity -= o.quantity; if (ex.quantity <= 0) _tPos = _tPos.filter(p => p.symbol !== o.symbol); } } return { ok: true, data: { orderId: oid, status: "filled" } }; },
    cancelOrder: async (id) => ({ ok: true, data: { success: false } }),
    getPositions: async () => ({ ok: true, data: _tPos }),
    getOrders: async () => ({ ok: true, data: _tOrders.slice(0, 50) }),
    getAccount: async () => { const mv = _tPos.reduce((s, p) => s + p.quantity * p.currentPrice, 0); const pnl = _tPos.reduce((s, p) => s + (p.currentPrice - p.costPrice) * p.quantity, 0); return { ok: true, data: { totalAssets: _tCash + mv, available: _tCash, marketValue: mv, totalPnl: pnl } }; },
  };
  // Mock settings
  let _settings = { dataSource: "mock", tradeSource: "mock" };
  const settingsMock = {
    get: async () => _settings,
    set: async (patch) => { _settings = { ..._settings, ...patch }; return _settings; },
  };

  async function mockHistory(rawSymbol, period, count) {
    let symbol = normalize(rawSymbol);
    if (!symbol) { const m = searchStocks(rawSymbol); if (m.length > 0) symbol = m[0].code; }
    if (!symbol) return { ok: false, error: "not found" };
    const preset = MOCK_TABLE[symbol];
    const base = preset ? preset.base : 50;
    const n = Math.min(count || 60, 500);
    const bars = []; let price = base;
    let sv = 0; for (let i = 0; i < symbol.length; i++) sv += symbol.charCodeAt(i);
    const rng = (i) => { const x = Math.sin(sv + i * 17) * 10000; return x - Math.floor(x); };
    const now = Date.now(); const dayMs = 86400000;
    for (let i = n - 1; i >= 0; i--) {
      const ret = (rng(i) - 0.5) * 0.04;
      const open = price; const close = price * (1 + ret);
      const high = Math.max(open, close) * (1 + rng(i + 100) * 0.015);
      const low = Math.min(open, close) * (1 - rng(i + 200) * 0.015);
      bars.push({ time: Math.floor((now - i * dayMs) / 1000), date: new Date(now - i * dayMs).toISOString().slice(0, 10), open: +open.toFixed(2), high: +high.toFixed(2), low: +low.toFixed(2), close: +close.toFixed(2), volume: Math.floor(500000 + rng(i + 300) * 3000000) });
      price = close;
    }
    return { ok: true, data: { bars: bars.reverse(), count: bars.length } };
  }
  var executorMock = {
    start: async (id, symbols) => ({ ok: true, data: { strategyId: id, status: "running" } }),
    stop: async (id) => ({ ok: true, data: { strategyId: id, status: "stopped" } }),
    status: async (id) => ({ ok: true, data: { strategyId: id, status: "stopped" } }),
    list: async () => ({ ok: true, data: [] }),
  };
  var logsMock = {
    trades: async () => [],
    strategy: async () => [],
    equity: async () => [],
  };
  return {
    quote: { query: mockQuery, info: async () => ({ mode: "mock", description: "前端 mock" }), history: async (s, p, c, dt) => mockHistory(s, p, c),
      search: async (q) => ({ ok: true, data: searchStocks(q) }), status: async () => ({ mode: "mock", description: "mock", connected: false }) },
    strategy: strategyMock, backtest: backtestMock, trade: tradeMock, settings: settingsMock,
    modelServer: {
    status: async () => ({ ok: false, data: { ready: false, port: 8765 } }),
    listStrategies: async () => ({ ok: true, data: [] }),
    getStrategy: async () => ({ ok: true, data: {} }),
    addStrategy: async () => ({ ok: true, data: {} }),
    deleteStrategy: async () => ({ ok: true, data: {} }),
    listModels: async () => ({ ok: true, data: [] }),
    getModel: async () => ({ ok: true, data: {} }),
    deleteModel: async () => ({ ok: true, data: {} }),
    startTraining: async () => ({ ok: true, data: { task_id: "mock" } }),
    getTrainingStatus: async () => ({ ok: true, data: { status: "done", progress: 100 } }),
    computeSignal: async () => ({ ok: true, data: { action: "hold" } }),
    updateModel: async () => ({ ok: true, data: {} }),
    activateModel: async () => ({ ok: true, data: {} }),
    getActiveModel: async () => ({ ok: true, data: { model_id: "", meta: null } }),
  },
  app: { info: async () => ({ name: "mookquant", version: "0.2.0", platform: "browser" }), restart: async () => { location.reload(); } },
    executor: executorMock,
    logs: logsMock,
  };
}
