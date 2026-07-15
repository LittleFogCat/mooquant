/**
 * mookquant · MA Cross 信号计算模块
 *
 * 供策略执行器调用：输入 K 线序列 + 参数，输出买卖信号。
 * 信号逻辑与 bridge/backtest_engine.py 的 strategy_ma_cross 保持一致。
 */

function sma(closes, period) {
  if (closes.length < period) return null;
  let sum = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    sum += closes[i];
  }
  return sum / period;
}

function generateSignal(bars, params) {
  const fast = parseInt(params.fast) || 5;
  const slow = parseInt(params.slow) || 20;
  const closes = bars.map((b) => b.close);

  if (closes.length < slow + 1) {
    return { action: "hold", reason: "数据不足（需要 " + (slow + 1) + " 根 K 线）" };
  }

  const prevCloses = closes.slice(0, -1);
  const maFastPrev = sma(prevCloses, fast);
  const maSlowPrev = sma(prevCloses, slow);
  const maFastNow = sma(closes, fast);
  const maSlowNow = sma(closes, slow);

  if (maFastPrev <= maSlowPrev && maFastNow > maSlowNow) {
    return { action: "buy", reason: "金叉", maFast: maFastNow, maSlow: maSlowNow };
  }

  if (maFastPrev >= maSlowPrev && maFastNow < maSlowNow) {
    return { action: "sell", reason: "死叉", maFast: maFastNow, maSlow: maSlowNow };
  }

  return { action: "hold", reason: "无信号", maFast: maFastNow, maSlow: maSlowNow };
}

module.exports = { generateSignal, sma };
