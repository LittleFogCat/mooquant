/**
 * mookquant · Backtest ViewModel
 *
 * 回测：选择策略、配置参数、运行回测、展示结果。
 */
class BacktestViewModel {
  constructor(facade) {
    this.facade = facade;
    this._storageKey = "mookquant_backtest_config";
    var saved = this._loadConfig();
    this.state = {
      strategies: [],
      models: [],
      selectedId: saved.selectedId || "",
      modelId: saved.modelId || "",
      startDate: saved.startDate || "",
      endDate: saved.endDate || "",
      symbols: saved.symbols || "",
      initialCapital: saved.initialCapital || 1000000,
      commission: saved.commission !== undefined ? saved.commission : 0.0003,
      slippage: saved.slippage !== undefined ? saved.slippage : 0.001,
      dividendType: saved.dividendType || "front",
    period: saved.period || "1d",
      running: false,
      result: null,
      error: null,
    };
    this._subs = [];
  }

  _loadConfig() {
    try { return JSON.parse(localStorage.getItem(this._storageKey) || "{}"); }
    catch (e) { return {}; }
  }

  _saveConfig() {
    var s = this.state;
    try {
      localStorage.setItem(this._storageKey, JSON.stringify({
        selectedId: s.selectedId,
        modelId: s.modelId,
        startDate: s.startDate,
        endDate: s.endDate,
        symbols: s.symbols,
        initialCapital: s.initialCapital,
        commission: s.commission,
        slippage: s.slippage,
        dividendType: s.dividendType,
    period: s.period,
      }));
    } catch (e) {}
  }

  subscribe(fn) {
    this._subs.push(fn);
    fn(this.state);
    return () => { this._subs = this._subs.filter((f) => f !== fn); };
  }

  _set(patch) {
    Object.assign(this.state, patch);
    this._notify();
  }

  _notify() {
    for (const fn of this._subs) fn(this.state);
  }

  async loadStrategies() {
    try {
      const resp = await this.facade.strategy.list();
      if (resp.ok) {
        const list = resp.data || [];
        this._set({ strategies: list });
        if (list.length && !this.state.selectedId) {
          this._set({ selectedId: list[0].id });
        }
      }
    } catch (e) {
      // 静默
    }
    this.loadModels();
  }

  async loadModels() {
    try {
      if (!this.facade.modelServer || !this.facade.modelServer.listModels) return;
      const resp = await this.facade.modelServer.listModels();
      if (resp.ok && Array.isArray(resp.data)) {
        this._set({ models: resp.data });
      }
    } catch (e) {
      // 静默
    }
  }

  setField(key, value) {
    this.state[key] = value;
    this._saveConfig();
    // Silent: no re-render to preserve input focus
  }

  /** 把后端错误翻译成人话（M4：失败信息可读化） */
  _humanizeError(msg) {
    const m = String(msg || "");
    if (/数据不足|回测数据不足/.test(m)) {
      return "回测区间内K线数据不足（可能标的未上市、停牌或数据源缺失），请扩大时间范围或更换标的";
    }
    if (/连接 miniQMT 失败|xtquant 不可用/.test(m)) {
      return "无法连接 miniQMT 行情服务，请确认 QMT 客户端已启动；当前将使用模拟数据";
    }
    if (/模型不存在/.test(m)) {
      return "所选模型不存在（可能已被删除），请重新选择模型";
    }
    if (/口径/.test(m)) {
      return m + "（旧版本模型与当前数据口径不兼容，请在模型页重新训练）";
    }
    return m;
  }

  async run() {
    const s = this.state;
    if (!s.selectedId) { this._set({ error: "请先选择策略" }); return; }
    if (!s.symbols || !s.symbols.trim()) { this._set({ error: "请输入回测标的" }); return; }
    if (!s.startDate || !s.endDate) { this._set({ error: "请选择回测时间范围" }); return; }

    this._saveConfig();
    this._set({ running: true, error: null, result: null });
    try {
      const resp = await this.facade.backtest.run({
        strategyId: s.selectedId,
        modelId: s.modelId || "",
        symbols: s.symbols,
        startDate: s.startDate,
        endDate: s.endDate,
        initialCapital: Number(s.initialCapital),
        commission: Number(s.commission),
        slippage: Number(s.slippage),
        dividendType: s.dividendType || "front",
        period: s.period || "1d",
      });
      if (!resp.ok) { this._set({ running: false, error: this._humanizeError(resp.error) }); return; }
      this._set({ running: false, result: resp.data });
    } catch (e) {
      this._set({ running: false, error: this._humanizeError(e.message) });
    }
  }
}

export { BacktestViewModel };
