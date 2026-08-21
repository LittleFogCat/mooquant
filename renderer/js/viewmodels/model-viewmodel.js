/**
 * mookquant * Model Management ViewModel
 *
 * Manages model list, training config/status, and strategy list
 * from the model server (bridge/model_server.py).
 */
class ModelViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      // Service status
      serviceReady: false,
      servicePort: 8765,
      // Models
      models: [],
      modelsLoading: false,
      // Training
      training: { active: false, taskId: null, status: null, progress: 0 },
      trainConfig: {
        symbol: "",
        period: "1d",
        barCount: 500,
        architecture: "lstm",
        epochs: 80,
        learningRate: 0.001,
        hiddenSize: 64,
        batchSize: 32,
        labelType: "classification",
      },
      // Active model
      activeModelId: "",
      // Error
      error: null,
    };
    this._subs = [];
    this._trainPoll = null;
  }

  subscribe(fn) { this._subs.push(fn); fn(this.state); return () => { this._subs = this._subs.filter(f => f !== fn); }; }
  _set(patch) { Object.assign(this.state, patch); this._notify(); }
  _notify() { for (const fn of this._subs) fn(this.state); }

  async loadStatus() {
    try {
      const r = await this.facade.modelServer.status();
      this._set({
        serviceReady: r.ok && r.data ? r.data.ready : false,
        servicePort: r.data ? r.data.port : 8765,
      });
    } catch (e) {
      this._set({ serviceReady: false, error: e.message });
    }
  }

  async loadModels() {
    this._set({ modelsLoading: true });
    try {
      const r = await this.facade.modelServer.listModels();
      this._set({
        models: r.ok ? (r.data || []) : [],
        modelsLoading: false,
        error: r.ok ? null : r.error,
      });
    } catch (e) {
      this._set({ modelsLoading: false, error: e.message });
    }
  }

  async loadAll() {
    await this.loadStatus();
    if (this.state.serviceReady) {
      await Promise.all([this.loadModels(), this.loadActiveModel()]);
    }
  }

  setTrainField(key, value) {
    this.state.trainConfig[key] = value;
  }

  async startTraining() {
    const cfg = this.state.trainConfig;
    const symbols = String(cfg.symbol || "").split(",").map(s => s.trim()).filter(Boolean);
    if (!symbols.length) {
      this._set({ error: "\u8bf7\u8f93\u5165\u6807\u7684\u4ee3\u7801\uff08\u591a\u4e2a\u7528\u9017\u53f7\u5206\u9694\uff09" });
      return;
    }
    this._set({ training: { active: true, taskId: null, status: null, progress: 0 }, error: null });
    try {
      const r = await this.facade.modelServer.startTraining({
        symbols: symbols,
        period: cfg.period,
        bar_count: parseInt(cfg.barCount) || 500,
        architecture: cfg.architecture,
        epochs: parseInt(cfg.epochs) || 50,
        learning_rate: parseFloat(cfg.learningRate) || 0.001,
        hidden_size: parseInt(cfg.hiddenSize) || 64,
        batch_size: parseInt(cfg.batchSize) || 32,
        label_type: cfg.labelType,
      });
      if (r.ok && r.data && r.data.task_id) {
        this.state.training.taskId = r.data.task_id;
        this._notify();
        this._startPolling();
      } else {
        this._set({
          training: { active: false, taskId: null, status: null, progress: 0 },
          error: (r && r.error) || "\u8bad\u7ec3\u542f\u52a8\u5931\u8d25",
        });
      }
    } catch (e) {
      this._set({ training: { active: false, taskId: null, status: null, progress: 0 }, error: e.message });
    }
  }

  _startPolling() {
    if (this._trainPoll) clearInterval(this._trainPoll);
    this._trainPoll = setInterval(async () => {
      if (!this.state.training.taskId) return;
      try {
        const r = await this.facade.modelServer.getTrainingStatus(this.state.training.taskId);
        if (r.ok && r.data) {
          const st = r.data.status || "unknown";
          this.state.training.status = r.data;
          this.state.training.progress = r.data.progress || 0;
          this._notify();
          if (["done", "completed", "failed", "error", "finished"].includes(st)) {
            clearInterval(this._trainPoll);
            this._trainPoll = null;
            this.state.training.active = false;
            this._notify();
            if (["done", "completed", "finished"].includes(st)) {
              this.loadModels();
            }
          }
        }
      } catch (e) { /* ignore poll errors */ }
    }, 2000);
  }

  stopPolling() {
    if (this._trainPoll) { clearInterval(this._trainPoll); this._trainPoll = null; }
  }

  async deleteModel(id) {
    try {
      const r = await this.facade.modelServer.deleteModel(id);
      if (r.ok) {
        this.loadModels();
      } else {
        this._set({ error: r.error });
      }
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  async loadActiveModel() {
    try {
      const r = await this.facade.modelServer.getActiveModel();
      if (r.ok && r.data) {
        this._set({ activeModelId: r.data.model_id || "" });
      }
    } catch (e) { /* ignore */ }
  }

  async activateModel(id, force = false) {
    // degraded 守门：体检不合格的模型激活前需二次确认
    if (!force) {
      const m = this.state.models.find(x => x.model_id === id);
      const degraded = m && m.metrics && m.metrics.degraded;
      if (degraded) {
        const ok = window.confirm("该模型体检不合格（degraded，样本外方向预测接近无效）。\n确定仍要激活它吗？");
        if (!ok) return;
        force = true;
      }
    }
    try {
      const r = await this.facade.modelServer.activateModel(id, force);
      if (r.ok) {
        this._set({ activeModelId: id });
      } else {
        this._set({ error: r.error || (r.data && r.data.error) || "激活失败" });
      }
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  async updateModel(id, patch) {
    try {
      const r = await this.facade.modelServer.updateModel(id, patch);
      if (r.ok) {
        this.loadModels();
      } else {
        this._set({ error: r.error });
      }
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  dismissError() {
    this._set({ error: null });
  }
}
export { ModelViewModel };