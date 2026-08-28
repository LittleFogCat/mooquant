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
      dividendType: saved.dividendType || "front_ratio",
      period: saved.period || "1d",
      // 日内做T（分钟周期专用，默认关闭/0）
      basePositionShares: saved.basePositionShares !== undefined ? saved.basePositionShares : 0,
      forceEodClose: saved.forceEodClose !== undefined ? saved.forceEodClose : true,
      // D0.1/D0.3：撮合口径（默认 next_open 保守）与是否允许模拟数据
      fillModel: saved.fillModel || "next_open",
      allowMock: !!saved.allowMock,
      // D2 风控与仓位（默认全部关闭/全仓，保持兼容）
      stopLoss: saved.stopLoss !== undefined ? saved.stopLoss : 0,
      takeProfit: saved.takeProfit !== undefined ? saved.takeProfit : 0,
      maxHoldDays: saved.maxHoldDays !== undefined ? saved.maxHoldDays : 0,
      maxConsecLosses: saved.maxConsecLosses !== undefined ? saved.maxConsecLosses : 0,
      cooldownDays: saved.cooldownDays !== undefined ? saved.cooldownDays : 5,
      maxPositionPct: saved.maxPositionPct !== undefined ? saved.maxPositionPct : 1.0,
      strengthScaling: !!saved.strengthScaling,
      // D2.3 市场状态过滤（regime，默认关闭）
      regimeEnabled: !!saved.regimeEnabled,
      regimeIndex: saved.regimeIndex || "000300.SH",
      regimeFast: saved.regimeFast !== undefined ? saved.regimeFast : 20,
      regimeOwnMa: !!saved.regimeOwnMa,
      // D4.2 稳健性分析（默认关闭）
      robustness: !!saved.robustness,
      // D4.3 回测历史（列表 + A/B 对比）
      history: [],
      compareSel: {},
      comparison: null,
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
        basePositionShares: s.basePositionShares,
        forceEodClose: s.forceEodClose,
        fillModel: s.fillModel,
        allowMock: s.allowMock,
        stopLoss: s.stopLoss,
        takeProfit: s.takeProfit,
        maxHoldDays: s.maxHoldDays,
        maxConsecLosses: s.maxConsecLosses,
        cooldownDays: s.cooldownDays,
        maxPositionPct: s.maxPositionPct,
        strengthScaling: s.strengthScaling,
        regimeEnabled: s.regimeEnabled,
        regimeIndex: s.regimeIndex,
        regimeFast: s.regimeFast,
        regimeOwnMa: s.regimeOwnMa,
        robustness: s.robustness,
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

  // D4.3 回测历史管理
  async loadHistory() {
    try {
      const resp = await this.facade.backtest.list();
      if (resp.ok && Array.isArray(resp.data)) {
        this._set({ history: resp.data });
      }
    } catch (e) {
      // 静默
    }
  }

  toggleCompare(id) {
    const sel = Object.assign({}, this.state.compareSel);
    if (sel[id]) delete sel[id];
    else sel[id] = true;
    this._set({ compareSel: sel, comparison: null });
  }

  async runCompare() {
    const ids = Object.keys(this.state.compareSel);
    if (ids.length < 2) {
      this._set({ error: "请至少选择 2 个历史回测进行对比" });
      return;
    }
    try {
      const resp = await this.facade.backtest.compare(ids);
      if (!resp.ok) { this._set({ error: this._humanizeError(resp.error) }); return; }
      this._set({ comparison: resp.data || [], error: null });
    } catch (e) {
      this._set({ error: this._humanizeError(e.message) });
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
        dividendType: s.dividendType || "front_ratio",
        period: s.period || "1d",
        basePositionShares: Number(s.basePositionShares) || 0,
        forceEodClose: s.forceEodClose !== false,
        fillModel: s.fillModel || "next_open",
        allowMock: !!s.allowMock,
        // D2 风控与仓位
        stopLoss: Number(s.stopLoss) || 0,
        takeProfit: Number(s.takeProfit) || 0,
        maxHoldDays: Number(s.maxHoldDays) || 0,
        maxConsecLosses: Number(s.maxConsecLosses) || 0,
        cooldownDays: Number(s.cooldownDays) || 5,
        maxPositionPct: Number(s.maxPositionPct) || 1.0,
        strengthScaling: !!s.strengthScaling,
        // D2.3 市场状态过滤
        regimeEnabled: !!s.regimeEnabled,
        regimeIndex: (s.regimeIndex || "000300.SH").trim(),
        regimeFast: Number(s.regimeFast) || 20,
        regimeOwnMa: !!s.regimeOwnMa,
        // D4.2 稳健性分析
        robustness: !!s.robustness,
      });
      if (!resp.ok) { this._set({ running: false, error: this._humanizeError(resp.error) }); return; }
      this._set({ running: false, result: resp.data });
    } catch (e) {
      this._set({ running: false, error: this._humanizeError(e.message) });
    }
  }
}

export { BacktestViewModel };
