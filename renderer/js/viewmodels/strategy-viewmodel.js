/**
 * mookquant · Strategy ViewModel
 *
 * 策略管理：列表、创建、编辑、删除 + 启动/停止执行 + 运行状态。
 */
class StrategyViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      list: [],
      executorStatus: {},
      loading: false,
      editing: null,
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

  async loadList() {
    this._set({ loading: true, error: null });
    try {
      const resp = await this.facade.strategy.list();
      if (!resp.ok) { this._set({ loading: false, error: resp.error }); return; }
      this._set({ list: resp.data || [], loading: false });
      await this.loadExecutorStatus();
    } catch (e) {
      this._set({ loading: false, error: e.message });
    }
  }

  async loadExecutorStatus() {
    try {
      if (!this.facade.executor) return;
      const resp = await this.facade.executor.list();
      if (resp.ok && resp.data) {
        const map = {};
        for (const s of resp.data) map[s.strategyId] = s;
        this._set({ executorStatus: map });
      }
    } catch {}
  }

  async startExecution(id) {
    try {
      const resp = await this.facade.executor.start(id);
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      await this.loadExecutorStatus();
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  async stopExecution(id) {
    try {
      const resp = await this.facade.executor.stop(id);
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      await this.loadExecutorStatus();
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  startCreate() {
    this._set({
      editing: {
        id: null,
        name: "",
        description: "",
        type: "ma_cross",
        symbols: "",
        params: JSON.stringify({ fast: 5, slow: 20 }, null, 2),
        risk: JSON.stringify({ stopLoss: 0.05, stopProfit: 0.15, maxOrderAmount: 500000, maxDailyTrades: 10, maxPositionRatio: 0.3 }, null, 2),
        status: "draft",
      },
      error: null,
    });
  }

  startEdit(strategy) {
    this._set({
      editing: {
        id: strategy.id,
        name: strategy.name,
        description: strategy.description || "",
        type: strategy.type,
        symbols: (strategy.symbols || []).join(", "),
        params: JSON.stringify(strategy.params || {}, null, 2),
        risk: JSON.stringify(strategy.risk || {}, null, 2),
        status: strategy.status,
      },
      error: null,
    });
  }

  cancelEdit() {
    this._set({ editing: null, error: null });
  }

  async save(form) {
    const symbols = form.symbols.split(",").map((s) => s.trim()).filter(Boolean);
    let params, risk;
    try {
      params = JSON.parse(form.params || "{}");
    } catch (e) {
      this._set({ error: "策略参数 JSON 解析失败: " + e.message });
      return;
    }
    try {
      risk = JSON.parse(form.risk || "{}");
    } catch (e) {
      this._set({ error: "风控参数 JSON 解析失败: " + e.message });
      return;
    }
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      type: form.type,
      symbols,
      params,
      risk,
      status: form.status,
    };
    if (!payload.name) { this._set({ error: "策略名称不能为空" }); return; }

    try {
      const resp = form.id
        ? await this.facade.strategy.update(form.id, payload)
        : await this.facade.strategy.create(payload);
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      this._set({ editing: null, error: null });
      await this.loadList();
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  async remove(id) {
    try {
      const resp = await this.facade.strategy.delete(id);
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      await this.loadList();
    } catch (e) {
      this._set({ error: e.message });
    }
  }
}

window.StrategyViewModel = StrategyViewModel;
