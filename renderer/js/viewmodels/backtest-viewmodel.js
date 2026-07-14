/**
 * mookquant · Backtest ViewModel
 *
 * 回测：选择策略、配置参数、运行回测、展示结果。
 */
class BacktestViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      strategies: [],
      selectedId: "",
      startDate: "",
      endDate: "",
      initialCapital: 1000000,
      commission: 0.0003,
      slippage: 0.001,
      running: false,
      result: null,
      error: null,
    };
    this._subs = [];
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
  }

  setField(key, value) {
    this.state[key] = value;
    // Silent: no re-render to preserve input focus
  }

  async run() {
    const s = this.state;
    if (!s.selectedId) { this._set({ error: "请先选择策略" }); return; }
    if (!s.startDate || !s.endDate) { this._set({ error: "请选择回测时间范围" }); return; }

    this._set({ running: true, error: null, result: null });
    try {
      const resp = await this.facade.backtest.run({
        strategyId: s.selectedId,
        startDate: s.startDate,
        endDate: s.endDate,
        initialCapital: Number(s.initialCapital),
        commission: Number(s.commission),
        slippage: Number(s.slippage),
      });
      if (!resp.ok) { this._set({ running: false, error: resp.error }); return; }
      this._set({ running: false, result: resp.data });
    } catch (e) {
      this._set({ running: false, error: e.message });
    }
  }
}

window.BacktestViewModel = BacktestViewModel;
