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

  async run({ strategyId, startDate, endDate, initialCapital, commission, slippage }) {
    // 加载策略
    const strategy = await this.strategyService.getById(strategyId);
    if (!strategy) return { ok: false, error: "策略不存在: " + strategyId };

    const engine = this._getEngine();

    const result = await engine.run({
      strategy: {
        type: strategy.type,
        params: strategy.params,
        symbols: strategy.symbols,
      },
      startDate,
      endDate,
      initialCapital: initialCapital || 1000000,
      commission: commission || 0.0003,
      slippage: slippage || 0.001,
    });

    return { ok: true, data: result };
  }

  dispose() {
    if (this._engine) this._engine.dispose();
  }
}

module.exports = { BacktestService };
