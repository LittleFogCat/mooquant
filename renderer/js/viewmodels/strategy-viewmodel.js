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
      strategyTypes: [],
      exportedScript: null,
      exportedName: null,
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
      await this.loadStrategyTypes();
    } catch (e) {
      this._set({ loading: false, error: e.message });
    }
  }

  /** 加载可用策略类型元数据（供 UI 渲染类型选择 + 参数表单） */
  async loadStrategyTypes() {
    try {
      if (!this.facade.strategy || !this.facade.strategy.types) return;
      const resp = await this.facade.strategy.types();
      if (resp.ok && resp.data) this._set({ strategyTypes: resp.data });
    } catch {}
  }

  /** 导出策略为 QMT 脚本 */
  async exportStrategy(id) {
    const s = this.state.list.find((x) => x.id === id);
    if (!s) return;
    try {
      const resp = await this.facade.strategy.export({ type: s.type, platform: "qmt" });
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      this._set({ exportedScript: resp.data.script, exportedName: s.name, error: null });
    } catch (e) { this._set({ error: e.message }); }
  }

  closeExport() {
    this._set({ exportedScript: null, exportedName: null });
  }

  async loadExecutorStatus() {
    try {
      if (!this.facade.executor) return;
      if (this.state.editing) return;  // 编辑中不打扰用户，避免 DOM 重建丢失输入
      const resp = await this.facade.executor.list();
      if (resp.ok && resp.data) {
        const map = {};
        for (const s of resp.data) map[s.strategyId] = s;
        this._set({ executorStatus: map });
      }
    } catch {}
  }

  async startExecution(id, symbols) {
    try {
      const resp = await this.facade.executor.start(id, symbols);
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
    const types = this.state.strategyTypes || [];
    const first = types[0] || { name: "ma_cross", params_schema: [] };
    const defaultParams = {};
    for (const p of (first.params_schema || [])) defaultParams[p.key] = p.default;
    this._set({
      editing: {
        id: null,
        name: "",
        description: "",
        type: first.name,
        params: JSON.stringify(defaultParams, null, 2),
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

export { StrategyViewModel };
