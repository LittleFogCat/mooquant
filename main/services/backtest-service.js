/**
 * mookquant · Backtest Service（主进程）
 *
 * 回测编排：加载策略 -> 调用引擎 -> 返回结果。
 */
const { BacktestEngine } = require("../datasources/backtest-engine");

class BacktestService {
  constructor({ strategyService, config }) {
    this.strategyService = strategyService;
    this._config = config || {};
    this._engine = null;
  }

  _getEngine() {
    if (!this._engine) {
      this._engine = new BacktestEngine({
        python: this._config.python,
      });
    }
    return this._engine;
  }

  async run({ strategyId, modelId, symbols, startDate, endDate, initialCapital, commission, slippage, dividendType, period }) {
    // 加载策略
    const strategy = await this.strategyService.getById(strategyId);
    if (!strategy) return { ok: false, error: "策略不存在: " + strategyId };

    // 模型优先级：回测显式指定 > 策略实例绑定 > ML 策略内部回退激活模型
    const params = { ...(strategy.params || {}) };
    const boundModel = modelId || strategy.modelId || "";
    if (boundModel) params.model_id = boundModel;

    // 标的与策略解耦：回测时由调用方指定
    const symbolList = (symbols || [])
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!symbolList.length) return { ok: false, error: "回测标的不能为空" };

    const engine = this._getEngine();

    const result = await engine.run({
      strategy: {
        type: strategy.type,
        params,
      },
      symbols: symbolList,
      startDate,
      endDate,
      initialCapital: initialCapital || 1000000,
      commission: commission || 0.0003,
      slippage: slippage || 0.001,
      dividendType: dividendType || "front",
      period: period || "1d",
    });

    return { ok: true, data: result };
  }

  dispose() {
    if (this._engine) this._engine.dispose();
  }
}

module.exports = { BacktestService };
