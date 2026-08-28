import * as echarts from 'echarts';
import flatpickr from 'flatpickr';
import 'flatpickr/dist/flatpickr.min.css';
import '../../css/flatpickr-override.css';
import { Mandarin } from 'flatpickr/dist/l10n/zh';

flatpickr.localize(Mandarin);
import * as KlineChart from './kline-chart.js';
import * as StockSearch from './stock-search.js';

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"'/]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;","/":"&#x2F;" }[c])); }
  function fmtPct(n) { if (n == null || isNaN(n)) return "-"; return (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%"; }
  function fmtNum(n, d) { d = d || 2; if (n == null || isNaN(n)) return "-"; return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function cls(n) { if (n > 0) return "up"; if (n < 0) return "down"; return "flat"; }
  function modelName(m) {
    if (!m) return "";
    if (m.name && m.name !== "unnamed") return m.name;
    if (m.arch) {
      var ts = (m.created_at || "").slice(0, 10);
      return m.arch + (ts ? " · " + ts : "");
    }
    return m.model_id || "";
  }

  let _fpStart = null, _fpEnd = null;
  let _equityChart = null;
  let _klineChart = null;

  function renderConfig(state, vm) {
    const opts = state.strategies.map(s => `<option value="${esc(s.id)}" ${state.selectedId === s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
    const modelOpts = [`<option value="" ${!state.modelId ? "selected" : ""}>跟随策略绑定 / 激活模型</option>`]
      .concat((state.models || []).map(m => `<option value="${esc(m.model_id)}" ${state.modelId === m.model_id ? "selected" : ""}>${esc(modelName(m))}</option>`))
      .join("");
    return `
      <div class="card">
        <div class="card-title">回测配置</div>
        ${state.error ? `<div class="status error" style="margin-bottom:16px">${esc(state.error)}</div>` : ""}
        <div class="form-group">
          <label class="form-label">选择策略</label>
          <select class="input-field" id="bt_strategy">${opts || `<option value="">请先在策略页创建策略</option>`}</select>
        </div>
        <div class="form-group">
          <label class="form-label">回测模型 <span style="color:var(--text-3);font-size:11px">（ML/壳策略生效）</span></label>
          <select class="input-field" id="bt_model">${modelOpts}</select>
        </div>
        <div class="form-group">
          <label class="form-label">回测标的</label>
          <input class="input-field" id="bt_symbols" placeholder="输入代码/名称/拼音搜索，逗号分隔多个标的" />
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">开始日期</label><input class="input-field" id="bt_start" placeholder="点击选择日期" readonly /></div>
          <div class="form-group"><label class="form-label">结束日期</label><input class="input-field" id="bt_end" placeholder="点击选择日期" readonly /></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">初始资金</label><input class="input-field" type="number" id="bt_capital" value="${state.initialCapital}" /></div>
          <div class="form-group"><label class="form-label">手续费率</label><input class="input-field" type="number" step="0.0001" id="bt_commission" value="${state.commission}" /></div>
          <div class="form-group"><label class="form-label">滑点</label><input class="input-field" type="number" step="0.001" id="bt_slippage" value="${state.slippage}" /></div>
          <div class="form-group"><label class="form-label">K线周期</label>
            <select class="input-field" id="bt_period">
              <option value="1d" ${state.period === "1d" ? "selected" : ""}>日线（隔夜策略）</option>
              <option value="1m" ${(state.period || "1m") === "1m" && state.period !== "1d" ? "selected" : ""}>1分钟（日内做T）</option>
              <option value="5m" ${state.period === "5m" ? "selected" : ""}>5分钟（日内）</option>
              <option value="15m" ${state.period === "15m" ? "selected" : ""}>15分钟（日内）</option>
              <option value="30m" ${state.period === "30m" ? "selected" : ""}>30分钟（日内）</option>
              <option value="60m" ${state.period === "60m" ? "selected" : ""}>60分钟（日内）</option>
            </select>
          </div>
        </div>
        ${state.period && state.period !== "1d" ? `
        <div class="form-row">
          <div class="form-group"><label class="form-label">底仓股数 <span style="color:var(--text-3);font-size:11px">（做T需先有底仓，回测首日以开盘价自动建立）</span></label><input class="input-field" type="number" step="100" id="bt_basePositionShares" value="${state.basePositionShares}" /></div>
          <div class="form-group" style="display:flex;align-items:flex-end;padding-bottom:2px">
            <label class="form-label" style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="bt_forceEodClose" ${state.forceEodClose ? "checked" : ""} /> 尾盘 14:55 强制平T仓（还原底仓）
            </label>
          </div>
          <div class="form-group" style="align-self:flex-end;font-size:12px;color:var(--text-3)">日内回测建议区间 ≤ 3 个月；分钟数据需在 QMT 客户端下载</div>
        </div>` : ""}
        <div class="form-row">
          <div class="form-group"><label class="form-label">撮合口径</label>
            <select class="input-field" id="bt_fillModel">
              <option value="next_open" ${state.fillModel === "next_open" ? "selected" : ""}>次日开盘成交（保守，推荐）</option>
              <option value="close" ${state.fillModel === "close" ? "selected" : ""}>收盘价成交（乐观）</option>
            </select>
          </div>
          <div class="form-group" style="display:flex;align-items:flex-end;padding-bottom:2px">
            <label class="form-label" style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="bt_allowMock" ${state.allowMock ? "checked" : ""} /> 允许使用模拟数据
            </label>
          </div>
          <div class="form-group" style="display:flex;align-items:flex-end;padding-bottom:2px">
            <label class="form-label" style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="bt_robustness" ${state.robustness ? "checked" : ""} /> 稳健性分析
            </label>
          </div>
        </div>
        <details style="margin:12px 0">
          <summary style="cursor:pointer;color:var(--text-3);font-size:13px">风控与仓位（高级，默认关闭）</summary>
          <div class="form-row" style="margin-top:10px">
            <div class="form-group"><label class="form-label">止损比例</label><input class="input-field" type="number" step="0.01" id="bt_stopLoss" value="${state.stopLoss}" /></div>
            <div class="form-group"><label class="form-label">止盈比例</label><input class="input-field" type="number" step="0.01" id="bt_takeProfit" value="${state.takeProfit}" /></div>
            <div class="form-group"><label class="form-label">最大持仓天数</label><input class="input-field" type="number" step="1" id="bt_maxHoldDays" value="${state.maxHoldDays}" /></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label class="form-label">连续亏损熔断</label><input class="input-field" type="number" step="1" id="bt_maxConsecLosses" value="${state.maxConsecLosses}" /></div>
            <div class="form-group"><label class="form-label">熔断冻结天数</label><input class="input-field" type="number" step="1" id="bt_cooldownDays" value="${state.cooldownDays}" /></div>
            <div class="form-group"><label class="form-label">单笔最大仓位</label><input class="input-field" type="number" step="0.05" id="bt_maxPositionPct" value="${state.maxPositionPct}" /></div>
          </div>
          <div class="form-row">
            <div class="form-group" style="display:flex;align-items:flex-end;padding-bottom:2px">
              <label class="form-label" style="display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="bt_strengthScaling" ${state.strengthScaling ? "checked" : ""} /> 按信号强度缩放仓位
              </label>
            </div>
            <div class="form-group" style="align-self:flex-end;font-size:12px;color:var(--text-3)">0 = 关闭；止损/止盈/时间止损按持仓成本与持有天数触发</div>
          </div>
          <div class="form-row" style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
            <div class="form-group" style="display:flex;align-items:flex-end;padding-bottom:2px">
              <label class="form-label" style="display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="bt_regimeEnabled" ${state.regimeEnabled ? "checked" : ""} /> 市场状态过滤（仅指数站上均线时做多）
              </label>
            </div>
            <div class="form-group"><label class="form-label">指数代码</label><input class="input-field" id="bt_regimeIndex" value="${esc(state.regimeIndex)}" /></div>
            <div class="form-group"><label class="form-label">指数均线周期</label><input class="input-field" type="number" step="1" id="bt_regimeFast" value="${state.regimeFast}" /></div>
            <div class="form-group" style="display:flex;align-items:flex-end;padding-bottom:2px">
              <label class="form-label" style="display:flex;align-items:center;gap:6px;cursor:pointer">
                <input type="checkbox" id="bt_regimeOwnMa" ${state.regimeOwnMa ? "checked" : ""} /> 个股也需站上均线
              </label>
            </div>
          </div>
        </details>
        <button class="btn btn-primary" id="bt_run" ${state.running ? "disabled" : ""}>${state.running ? "回测中..." : "开始回测"}</button>
        ${renderProgress(state)}
      </div>
    `;
  }

  function renderProgress(state) {
    if (!state.running || !state.progress) return "";
    const p = state.progress;
    const val = Math.max(0, Math.min(100, Number(p.progress) || 0));
    const stageLabel = { fetch: "加载数据", signal: "生成信号", match: "撮合交易", finish: "计算指标", start: "开始回测" }[p.stage] || "回测中";
    return `
      <div style="margin-top:16px">
        <div class="progress-bar-wrap">
          <div class="progress-bar-fill" style="width:${val}%"></div>
          <span class="progress-bar-text">${val}%</span>
        </div>
        <p style="color:var(--text-3);margin-top:8px;font-size:13px">${stageLabel}${p.detail ? " · " + p.detail : ""}</p>
      </div>
    `;
  }

  function renderResult(result) {
    if (!result) return "";
    const m = result.metrics || {};
    const trades = result.trades || [];
    const equity = result.equityCurve || [];
    const statsHtml = `
      <div class="stat-card"><div class="stat-label">总收益率</div><div class="stat-value ${cls(m.totalReturn)}">${fmtPct(m.totalReturn)}</div></div>
      <div class="stat-card"><div class="stat-label">区间涨幅</div><div class="stat-value ${cls(m.benchmarkReturn)}">${fmtPct(m.benchmarkReturn)}</div></div>
      <div class="stat-card"><div class="stat-label">超额收益</div><div class="stat-value ${cls(m.excessReturn)}">${fmtPct(m.excessReturn)}</div></div>
      <div class="stat-card"><div class="stat-label">年化收益率</div><div class="stat-value ${cls(m.annualReturn)}">${fmtPct(m.annualReturn)}</div></div>
      <div class="stat-card"><div class="stat-label">最大回撤</div><div class="stat-value down">${fmtPct(m.maxDrawdown)}</div></div>
      <div class="stat-card"><div class="stat-label">夏普比率</div><div class="stat-value">${fmtNum(m.sharpeRatio)}</div></div>
      <div class="stat-card"><div class="stat-label">索提诺比率</div><div class="stat-value">${fmtNum(m.sortinoRatio)}</div></div>
      <div class="stat-card"><div class="stat-label">卡玛比率</div><div class="stat-value">${fmtNum(m.calmarRatio)}</div></div>
      <div class="stat-card"><div class="stat-label">年化波动率</div><div class="stat-value">${fmtPct(m.annualVolatility)}</div></div>
      <div class="stat-card"><div class="stat-label">平均持仓天数</div><div class="stat-value">${fmtNum(m.avgHoldDays, 1)}</div></div>
      <div class="stat-card"><div class="stat-label">胜率</div><div class="stat-value">${fmtPct(m.winRate)}</div></div>
      <div class="stat-card"><div class="stat-label">胜率(含浮盈)</div><div class="stat-value">${fmtPct(m.winRateInclOpen)}<span style="font-size:10px;color:var(--text-3)"> 未平仓计入</span></div></div>
      <div class="stat-card"><div class="stat-label">盈亏比</div><div class="stat-value">${fmtNum(m.profitLossRatio)}</div></div>
      <div class="stat-card"><div class="stat-label">交易次数</div><div class="stat-value">${m.totalTrades || 0}</div></div>
      <div class="stat-card"><div class="stat-label">被跳过信号</div><div class="stat-value">${m.skippedSignals || 0}<span style="font-size:11px;color:var(--text-3)"> 涨跌停/T+1</span></div></div>
      <div class="stat-card"><div class="stat-label">最终资金</div><div class="stat-value">${fmtNum(m.finalCapital, 0)}</div></div>
    `;
    let curveHtml = `<div class="equity-curve">暂无净值数据</div>`;
    if (equity.length > 1) {
      curveHtml = `<div id="equityChart" style="width:100%;height:260px"></div>`;
    }
    const klineBars = result.bars || [];
    let klineHtml = `<div class="equity-curve">暂无K线数据</div>`;
    if (klineBars.length > 1) {
      klineHtml = `<div id="btKlineChart" style="width:100%"></div>`;
    }
    const sideBadge = (t) => {
      const map = { buy: ["buy", "买入"], sell: ["sell", "卖出"], restore: ["restore", "还原"] };
      const cfg = map[t.side] || ["", t.side || ""];
      return `<span class="badge badge-${cfg[0]}">${cfg[1]}</span>`;
    };
    const tradeRows = trades.slice(0, 50).map(t => `
      <tr><td>${esc(t.date)}</td><td>${sideBadge(t)}${t.lotTag ? `<span style="font-size:10px;color:var(--text-3);margin-left:4px">${t.lotTag === "core" ? "底仓" : "T仓"}</span>` : ""}</td><td>${esc(t.symbol)}</td><td class="num">${t.price != null ? fmtNum(t.price) : "-"}</td><td class="num">${t.quantity}</td><td class="num">${fmtNum(t.amount, 0)}</td><td class="${cls(t.pnl)} num">${t.pnl != null ? fmtNum(t.pnl) : "-"}</td></tr>
    `).join("");
    // D2.4：组合回测分标的贡献
    const perSym = result.perSymbol;
    let perSymHtml = "";
    if (perSym && Object.keys(perSym).length) {
      const rows = Object.entries(perSym).map(([sym, ps]) => `
        <tr><td>${esc(sym)}</td><td class="num">${fmtPct(ps.return)}</td><td class="num">${fmtNum(ps.realizedPnl)}</td><td class="num">${fmtNum(ps.openPnl)}</td><td class="num">${ps.trades}</td></tr>`).join("");
      perSymHtml = `<div class="card"><div class="card-title">分标的贡献</div><table class="data-table"><thead><tr><th>标的</th><th class="num">区间涨幅</th><th class="num">已实现盈亏</th><th class="num">浮动盈亏</th><th class="num">交易数</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    // 日内做T分账卡片（period=分钟 且 metrics.intraday 存在时展示）
    const intra = m.intraday;
    let intradayHtml = "";
    if (intra) {
      const tRet = intra.tPnl != null ? intra.tPnl : 0;
      const cRet = intra.corePnl != null ? intra.corePnl : 0;
      intradayHtml = `
      <div class="card"><div class="card-title">做T分账 <span style="font-size:12px;font-weight:normal;color:var(--text-3);margin-left:8px">底仓 ${intra.coreShares || 0} 股 · ${intra.tradingDays || 0} 个交易日</span></div>
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-label">做T收益</div><div class="stat-value ${cls(tRet)}">${fmtNum(tRet)}</div></div>
          <div class="stat-card"><div class="stat-label">底仓收益</div><div class="stat-value ${cls(cRet)}">${fmtNum(cRet)}</div></div>
          <div class="stat-card"><div class="stat-label">做T次数</div><div class="stat-value">${intra.tRounds || 0}</div></div>
          <div class="stat-card"><div class="stat-label">日均做T</div><div class="stat-value">${fmtNum(intra.tPerDay, 2)}</div></div>
          <div class="stat-card"><div class="stat-label">单次平均盈亏</div><div class="stat-value ${cls(intra.avgTRoundPnl)}">${fmtNum(intra.avgTRoundPnl)}</div></div>
          <div class="stat-card"><div class="stat-label">底仓成本</div><div class="stat-value">${fmtNum(intra.coreCost)}</div></div>
          ${intra.pendingRestoreShares > 0 ? `<div class="stat-card"><div class="stat-label">未还原底仓</div><div class="stat-value down">${intra.pendingRestoreShares} 股</div></div>` : ""}
        </div>
      </div>`;
    }
    const risk = result.risk || {};
    const riskActive = [];
    if (risk.stopLoss) riskActive.push("止损 " + Math.round(risk.stopLoss * 100) + "%");
    if (risk.takeProfit) riskActive.push("止盈 " + Math.round(risk.takeProfit * 100) + "%");
    if (risk.maxHoldDays) riskActive.push("时间止损 " + risk.maxHoldDays + "日");
    if (risk.maxConsecLosses) riskActive.push("熔断 " + risk.maxConsecLosses + "连亏/" + (risk.cooldownDays || 5) + "日");
    if (risk.maxPositionPct && risk.maxPositionPct < 1) riskActive.push("仓位≤" + Math.round(risk.maxPositionPct * 100) + "%");
    if (risk.strengthScaling) riskActive.push("强度缩放");
    const reg = result.regime;
    let regLabel = "";
    if (reg && reg.enabled) {
      regLabel = "市场状态过滤 " + esc(reg.index || "") + "·MA" + (reg.fast || 20) + (reg.ownMa ? "+个股MA" : "") + "（放行 " + (reg.allowedDays || 0) + "日 / 阻断 " + (reg.blockedDays || 0) + "日）" + (reg.warning ? " ⚠指数不可用已降级" : "");
    }
    return `
      <div class="card"><div class="card-title">${result.name || ""}（${result.symbol || ""}）${fillLabel ? `<span style="font-size:12px;font-weight:normal;color:var(--text-3);margin-left:8px">撮合口径：${esc(fillLabel)}</span>` : ""}${riskActive.length ? `<span style="font-size:12px;font-weight:normal;color:var(--text-3);margin-left:8px">风控：${esc(riskActive.join("、"))}</span>` : ""}${regLabel ? `<div style="font-size:12px;color:var(--text-3);margin-top:6px">${regLabel}</div>` : ""}</div>
        ${result.dataSource === "mock" ? '<div class="status error" style="margin:0 0 12px">⚠ 未能获取真实行情，本次回测使用模拟数据（已显式允许），结果仅供参考</div>' : ""}
        ${result.inSampleWarning ? `<div class="status error" style="margin:0 0 12px">⚠ ${esc(result.inSampleWarning)}</div>` : ""}
        ${m.annualReturnNote ? `<div class="status" style="margin:0 0 12px">⚠ ${esc(m.annualReturnNote)}</div>` : ""}
        <div class="stats-grid">${statsHtml}</div></div>
      <div class="card"><div class="card-title">净值曲线</div>${curveHtml}</div>
      <div class="kline-card" style="margin:0 0 16px 0">${klineHtml}</div>
      ${intradayHtml}
      <div class="card"><div class="card-title">交易记录 (前 50 笔)</div>${trades.length ? `<table class="data-table"><thead><tr><th>日期</th><th>方向</th><th>标的</th><th class="num">价格</th><th class="num">数量</th><th class="num">金额</th><th class="num">盈亏</th></tr></thead><tbody>${tradeRows}</tbody></table>` : `<div class="empty-state"><div class="empty-state-text">无交易记录</div></div>`}</div>
      ${perSymHtml}
      ${renderRobustness(result)}
    `;
  }

  function renderRobustness(result) {
    const rb = result.robustness;
    if (!rb) return "";
    const v = rb.verdict || {};
    const gradeMap = { green: ["稳健", "#22c55e"], yellow: ["一般", "#eab308"], red: ["脆弱", "#ef4444"] };
    const g = gradeMap[v.overall] || ["-", "var(--text-3)"];
    let html = `<div class="card"><div class="card-title">稳健性分析 <span class="badge" style="background:${g[1]}22;color:${g[1]};border:1px solid ${g[1]}66">${g[0]}</span></div>`;
    html += `<p style="color:var(--text-3);font-size:12px;margin:0 0 12px">${esc(v.summary || "")}</p>`;
    if (rb.cost && rb.cost.length) {
      html += `<div style="font-size:12px;color:var(--text-3);margin:8px 0 4px">成本敏感性（佣金/印花税/过户费缩放）</div><table class="data-table"><thead><tr><th>成本档</th><th class="num">总收益</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">交易数</th><th class="num">胜率</th></tr></thead><tbody>`;
      for (const c of rb.cost) html += `<tr><td>${esc(c.scale)}</td><td class="num">${fmtPct(c.totalReturn)}</td><td class="num">${fmtPct(c.annualReturn)}</td><td class="num">${fmtPct(c.maxDrawdown)}</td><td class="num">${c.totalTrades}</td><td class="num">${fmtPct(c.winRate)}</td></tr>`;
      html += `</tbody></table>`;
    }
    if (rb.params && rb.params.length) {
      html += `<div style="font-size:12px;color:var(--text-3);margin:8px 0 4px">参数敏感性（±20% 邻域，总收益%）</div><table class="data-table"><thead><tr><th>参数</th><th class="num">基准值</th><th class="num">×0.8</th><th class="num">×1.0</th><th class="num">×1.2</th></tr></thead><tbody>`;
      for (const p of rb.params) {
        const cells = { "x0.8": "-", "x1.0": "-", "x1.2": "-" };
        for (const x of p.runs) cells[x.factor] = fmtPct(x.totalReturn);
        html += `<tr><td>${esc(p.param)}</td><td class="num">${p.base}</td><td class="num">${cells["x0.8"]}</td><td class="num">${cells["x1.0"]}</td><td class="num">${cells["x1.2"]}</td></tr>`;
      }
      html += `</tbody></table>`;
    }
    if (rb.slices && rb.slices.length) {
      html += `<div style="font-size:12px;color:var(--text-3);margin:8px 0 4px">时间切片</div><table class="data-table"><thead><tr><th>时间段</th><th class="num">总收益</th><th class="num">交易数</th></tr></thead><tbody>`;
      for (const s of rb.slices) html += `<tr><td>${esc(s.slice)}</td><td class="num">${fmtPct(s.totalReturn)}</td><td class="num">${s.totalTrades}</td></tr>`;
      html += `</tbody></table>`;
    }
    html += `</div>`;
    return html;
  }

  function renderEquityCurve(equity, trades) {
    const container = document.getElementById("equityChart");
    if (_equityChart) { try { _equityChart.dispose(); } catch (e) {} _equityChart = null; }
    if (container) {
      var stale = echarts.getInstanceByDom(container);
      if (stale) { try { stale.dispose(); } catch (e) {} }
    }
    if (!container || !equity || equity.length < 2) return;

    _equityChart = echarts.init(container, "dark");
    var dates = equity.map(function (e) { return e.date; });
    var values = equity.map(function (e) { return Number(e.value); });

    var buyPts = [], sellPts = [];
    var eqMap = {};
    equity.forEach(function (e, i) { eqMap[e.date] = i; });
    if (trades && trades.length) {
      trades.forEach(function (t) {
        if (t.side !== "buy" && t.side !== "sell") return; // restore 等非交易事件不画点
        var idx = eqMap[t.date];
        var v = idx != null ? values[idx] : null;
        if (v == null) return;
        var pt = { value: [t.date, v], side: t.side, price: t.price, quantity: t.quantity, amount: t.amount, pnl: t.pnl };
        if (t.side === "buy") buyPts.push(pt);
        else sellPts.push(pt);
      });
    }

    _equityChart.setOption({
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        formatter: function (params) {
          if (!params || !params.length) return "";
          var date = params[0].axisValue;
          var lines = [date];
          for (var i = 0; i < params.length; i++) {
            var p = params[i];
            if (p.seriesType === "line" && p.value != null) {
              lines.push("\u51c0\u503c: " + fmtNum(p.value));
            }
            if (p.seriesType === "scatter" && p.data != null) {
              var d = p.data;
              var label = d.side === "buy" ? "\u4e70\u5165" : "\u5356\u51fa";
              var color = d.side === "buy" ? "#ef4444" : "#22c55e";
              lines.push('<span style="color:' + color + '">' + label + ": " + fmtNum(d.price) + " \u80a1 \u6570\u91cf: " + d.quantity + " \u91d1\u989d: " + fmtNum(d.amount, 0) + "</span>");
              if (d.pnl != null) lines.push('<span style="color:' + color + '">\u76c8\u4e8f: ' + fmtNum(d.pnl) + "</span>");
            }
          }
          return lines.join("<br/>");
        },
        backgroundColor: "rgba(26,26,37,0.95)",
        borderColor: "rgba(255,255,255,0.08)",
        textStyle: { color: "#e8e8ed", fontSize: 12 }
      },
      grid: { left: "8%", right: "6%", top: "8%", bottom: "18%" },
      xAxis: {
        type: "category", data: dates, boundaryGap: false,
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.1)" } },
        axisLabel: { color: "#5a5a68", fontSize: 10 }
      },
      yAxis: {
        scale: true,
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
        axisLabel: { color: "#5a5a68", fontSize: 10, formatter: function (v) { return fmtNum(v, 0); } }
      },
      dataZoom: [
        { type: "inside", start: 0, end: 100 },
        { show: true, type: "slider", start: 0, end: 100, height: 16, bottom: 8,
          borderColor: "transparent", backgroundColor: "rgba(255,255,255,0.03)",
          fillerColor: "rgba(99,102,241,0.15)", handleStyle: { color: "#6366f1" },
          textStyle: { color: "#5a5a68", fontSize: 10 } }
      ],
      series: [
        { name: "\u51c0\u503c", type: "line", data: values, smooth: false, symbol: "none",
          lineStyle: { color: "#6366f1", width: 2 },
          areaStyle: { color: "rgba(99,102,241,0.1)" } },
        { name: "\u4e70\u5165", type: "scatter", data: buyPts, symbol: "circle", symbolSize: 8,
          itemStyle: { color: "#ef4444" }, z: 10 },
        { name: "\u5356\u51fa", type: "scatter", data: sellPts, symbol: "circle", symbolSize: 8,
          itemStyle: { color: "#22c55e" }, z: 10 }
      ]
    }, true);

    if (!window._equityResizeBound) {
      window._equityResizeBound = true;
      window.addEventListener("resize", function () { if (_equityChart) _equityChart.resize(); });
    }
  }

  // Aggregate daily bars + trades into the selected display period (no re-backtest).
  // Daily trades merge into weekly/monthly bars, so a bar with both buy & sell shows "T".
  function aggregateForPeriod(bars, trades, period) {
    if (!bars || bars.length < 2) return { bars: bars || [], trades: trades || [] };
    if (period === "1d") return { bars: bars, trades: trades };
    function groupKey(dateStr) {
      var s = String(dateStr).slice(0, 10);
      if (period === "1mon") return s.slice(0, 7);
      var d = new Date(s + "T00:00:00");
      var day = (d.getDay() + 6) % 7; // 0 = Monday
      d.setDate(d.getDate() - day);
      var y = d.getFullYear();
      var m = String(d.getMonth() + 1).padStart(2, "0");
      var dd = String(d.getDate()).padStart(2, "0");
      return y + "-" + m + "-" + dd;
    }
    var groups = {}, order = [];
    bars.forEach(function (b) {
      var key = groupKey(b.date);
      if (!groups[key]) { groups[key] = { list: [], aggDate: b.date }; order.push(key); }
      groups[key].list.push(b);
    });
    var aggBars = order.map(function (key) {
      var g = groups[key].list;
      return {
        date: groups[key].aggDate,
        open: Number(g[0].open),
        high: Math.max.apply(null, g.map(function (b) { return Number(b.high); })),
        low: Math.min.apply(null, g.map(function (b) { return Number(b.low); })),
        close: Number(g[g.length - 1].close),
        volume: g.reduce(function (s, b) { return s + Number(b.volume || 0); }, 0),
      };
    });
    var dateToAgg = {};
    bars.forEach(function (b) { dateToAgg[b.date] = groups[groupKey(b.date)].aggDate; });
    var aggTrades = (trades || []).map(function (t) {
      return Object.assign({}, t, { date: dateToAgg[t.date] || t.date });
    });
    return { bars: aggBars, trades: aggTrades };
  }

  function renderKlineChart(bars, trades, vm) {
    const container = document.getElementById("btKlineChart");
    if (!container || !bars || bars.length < 2) return;
    const period = (vm && vm.state && vm.state.period) || "1d";
    const agg = aggregateForPeriod(bars, trades || [], period);
    var barCount = agg.bars.length;
    var visibleBars = Math.min(barCount, 150);
    var dzStart = Math.max(0, 100 - (visibleBars / barCount * 100));
    _klineChart = KlineChart.render(container, agg.bars, {
      trades: agg.trades,
      title: "B/S点",
      height: 380,
      periods: KlineChart.PERIOD_OPTIONS.filter(function (p) {
        return ["1d", "1w", "1mon"].indexOf(p.value) >= 0;
      }),
      period: period,
      dividendType: (vm && vm.state && vm.state.dividendType) || "front_ratio",
      dataZoomStart: dzStart,
      dataZoomEnd: 100,
      onSettingsChange: function (settings) {
        if (settings.dividendType && vm) {
          vm.setField("dividendType", settings.dividendType);
          vm.run();
        }
      },
      onPeriodChange: function (p) {
        if (vm) {
          vm.state.period = p;
          try { vm._saveConfig(); } catch (e) {}
          if (_klineChart) { try { _klineChart.destroy(); } catch (e) {} _klineChart = null; }
          renderKlineChart(vm.state.result ? vm.state.result.bars : null, vm.state.result ? vm.state.result.trades : null, vm);
        }
      }
    });
    if (!window._klineResizeBound) {
      window._klineResizeBound = true;
      window.addEventListener("resize", function () { if (_klineChart) _klineChart.resize(); });
    }
  }

  function renderHistory(state, vm) {
    const hist = state.history || [];
    const rows = hist.slice(0, 20).map(h => `
      <tr style="cursor:pointer" data-bid="${esc(h.backtestId)}">
        <td><input type="checkbox" data-bid="${esc(h.backtestId)}" ${state.compareSel[h.backtestId] ? "checked" : ""} /></td>
        <td>${esc((h.createdAt || "").slice(5, 16))}</td>
        <td>${esc(h.strategy || "")}${h.portfolio ? ' <span class="badge badge-buy">组合</span>' : ""}</td>
        <td>${esc(h.symbol || "")}</td>
        <td class="num">${fmtPct(h.metrics && h.metrics.totalReturn)}</td>
        <td class="num">${fmtPct(h.metrics && h.metrics.maxDrawdown)}</td>
        <td class="num">${(h.metrics && h.metrics.totalTrades) || 0}</td>
      </tr>`).join("");
    // 对比表
    let cmpHtml = "";
    const cmp = state.comparison;
    if (cmp && cmp.length) {
      const rows_ = [
        ["策略", c => esc(c.strategy || "")],
        ["标的", c => esc(c.symbol || "")],
        ["区间", c => esc((c.startDate || "") + " ~ " + (c.endDate || ""))],
        ["撮合口径", c => esc(c.fillModel || "")],
        ["总收益", c => fmtPct(c.metrics.totalReturn)],
        ["区间涨幅", c => fmtPct(c.metrics.benchmarkReturn)],
        ["超额收益", c => fmtPct(c.metrics.excessReturn)],
        ["年化收益", c => fmtPct(c.metrics.annualReturn)],
        ["最大回撤", c => fmtPct(c.metrics.maxDrawdown)],
        ["夏普", c => fmtNum(c.metrics.sharpeRatio)],
        ["胜率", c => fmtPct(c.metrics.winRate)],
        ["交易数", c => (c.metrics.totalTrades || 0)],
        ["最终资金", c => fmtNum(c.metrics.finalCapital, 0)],
      ];
      cmpHtml = `<div style="overflow:auto;margin-top:12px"><table class="data-table"><thead><tr><th>指标</th>${cmp.map(c => `<th>${esc((c.createdAt || "").slice(5, 16))} · ${esc(c.strategy || "")}</th>`).join("")}</tr></thead><tbody>${
        rows_.map(([label, get]) => `<tr><td>${label}</td>${cmp.map(c => `<td class="num">${get(c)}</td>`).join("")}</tr>`).join("")
      }</tbody></table></div>`;
    }
    const selCount = Object.keys(state.compareSel).length;
    return `
      <details id="bt_history" style="margin-top:16px">
        <summary style="cursor:pointer;color:var(--text-3);font-size:13px">历史回测（${hist.length} 次）· 勾选 2 个以上可 A/B 对比</summary>
        <div style="margin:10px 0">
          <button class="btn btn-ghost" id="bt_compare" ${selCount < 2 ? "disabled" : ""}>对比所选（${selCount}）</button>
        </div>
        ${hist.length ? `<div style="max-height:260px;overflow:auto"><table class="data-table"><thead><tr><th></th><th>时间</th><th>策略</th><th>标的</th><th class="num">总收益</th><th class="num">最大回撤</th><th class="num">交易数</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty-state"><div class="empty-state-text">暂无历史回测</div></div>`}
        ${cmpHtml}
      </details>
    `;
  }

  function render(root, vm) {
    var btSearchCtrl = null;
    function paint(state) {
      // Destroy old instances (before innerHTML wipes the DOM)
      if (_fpStart) { _fpStart.destroy(); _fpStart = null; }
      if (_fpEnd) { _fpEnd.destroy(); _fpEnd = null; }
      if (_klineChart) { try { _klineChart.destroy(); } catch (e) {} _klineChart = null; }
      if (_equityChart) { try { _equityChart.dispose(); } catch (e) {} _equityChart = null; }

      root.innerHTML = renderConfig(state, vm) + renderResult(state.result) + renderHistory(state, vm);

      // Bind strategy select
      const stratEl = document.getElementById("bt_strategy");
      if (stratEl) stratEl.addEventListener("change", () => vm.setField("selectedId", stratEl.value));
      const modelEl = document.getElementById("bt_model");
      if (modelEl) modelEl.addEventListener("change", () => vm.setField("modelId", modelEl.value));
      const symEl = document.getElementById("bt_symbols");
      if (symEl) {
        symEl.value = state.symbols || "";
        if (btSearchCtrl) btSearchCtrl.destroy();
        btSearchCtrl = StockSearch.mount(symEl, vm.facade, function (stock) {
          vm.setField("symbols", symEl.value);
        }, function (val) {
          vm.setField("symbols", val);
        }, { multi: true });
      }
      // Bind number inputs
      const numIds = { bt_capital: "initialCapital", bt_commission: "commission", bt_slippage: "slippage", bt_basePositionShares: "basePositionShares" };
      for (const [elId, field] of Object.entries(numIds)) {
        const el = document.getElementById(elId);
        if (el) el.addEventListener("change", () => vm.setField(field, el.value));
      }
      // K线周期：切换后重渲染（显示/隐藏日内配置区）
      const periodEl = document.getElementById("bt_period");
      if (periodEl) periodEl.addEventListener("change", () => { vm.setField("period", periodEl.value); paint(vm.state); });
      // 尾盘强平开关
      const eodEl = document.getElementById("bt_forceEodClose");
      if (eodEl) eodEl.addEventListener("change", () => vm.setField("forceEodClose", eodEl.checked));
      // D0.1/D0.3：撮合口径 & 允许模拟数据
      const fillEl = document.getElementById("bt_fillModel");
      if (fillEl) fillEl.addEventListener("change", () => vm.setField("fillModel", fillEl.value));
      const mockEl = document.getElementById("bt_allowMock");
      if (mockEl) mockEl.addEventListener("change", () => vm.setField("allowMock", mockEl.checked));
      // D4.2：稳健性分析
      const robEl = document.getElementById("bt_robustness");
      if (robEl) robEl.addEventListener("change", () => vm.setField("robustness", robEl.checked));
      // D2：风控与仓位
      const riskIds = { bt_stopLoss: "stopLoss", bt_takeProfit: "takeProfit", bt_maxHoldDays: "maxHoldDays", bt_maxConsecLosses: "maxConsecLosses", bt_cooldownDays: "cooldownDays", bt_maxPositionPct: "maxPositionPct" };
      for (const [elId, field] of Object.entries(riskIds)) {
        const el = document.getElementById(elId);
        if (el) el.addEventListener("change", () => vm.setField(field, el.value));
      }
      const ssEl = document.getElementById("bt_strengthScaling");
      if (ssEl) ssEl.addEventListener("change", () => vm.setField("strengthScaling", ssEl.checked));
      // D2.3：市场状态过滤
      const regEl = document.getElementById("bt_regimeEnabled");
      if (regEl) regEl.addEventListener("change", () => vm.setField("regimeEnabled", regEl.checked));
      const regIdxEl = document.getElementById("bt_regimeIndex");
      if (regIdxEl) regIdxEl.addEventListener("change", () => vm.setField("regimeIndex", regIdxEl.value));
      const regFastEl = document.getElementById("bt_regimeFast");
      if (regFastEl) regFastEl.addEventListener("change", () => vm.setField("regimeFast", regFastEl.value));
      const regOwnEl = document.getElementById("bt_regimeOwnMa");
      if (regOwnEl) regOwnEl.addEventListener("change", () => vm.setField("regimeOwnMa", regOwnEl.checked));
      // Init flatpickr on date inputs
      const startEl = document.getElementById("bt_start");
      const endEl = document.getElementById("bt_end");
      // flatpickr treats year/month switches as calendar navigation only; the selected date (and thus
      // the input box) is not updated until a day is picked. We extract a shared config and, via
      // onYearChange, sync the selected date's year to the current year and trigger onChange.
      const fpConfig = (field) => ({
        locale: "zh",
        dateFormat: "Y-m-d",
        theme: "dark",
        maxDate: "today",
        onChange: (dates, val) => vm.setField(field, val),
        onYearChange: (selectedDates, dateStr, instance) => {
          if (selectedDates.length > 0) {
            const d = selectedDates[0];
            const newDate = new Date(instance.currentYear, d.getMonth(), d.getDate());
            instance.setDate(newDate, true);
          }
        },
      });
      if (startEl) {
        startEl.value = state.startDate || "";
        _fpStart = flatpickr(startEl, fpConfig("startDate"));
      }
      if (endEl) {
        endEl.value = state.endDate || "";
        _fpEnd = flatpickr(endEl, fpConfig("endDate"));
      }
      // Run button
      const runBtn = document.getElementById("bt_run");
      if (runBtn) runBtn.addEventListener("click", () => vm.run());
      // D4.3 历史回测：勾选 + 对比
      document.querySelectorAll("#bt_history input[type=checkbox]").forEach(el => {
        el.addEventListener("change", () => vm.toggleCompare(el.getAttribute("data-bid")));
      });
      const cmpBtn = document.getElementById("bt_compare");
      if (cmpBtn) cmpBtn.addEventListener("click", () => vm.runCompare());
      // Render equity curve chart after DOM is ready
      renderEquityCurve(state.result ? state.result.equityCurve : null, state.result ? state.result.trades : null);
      renderKlineChart(state.result ? state.result.bars : null, state.result ? state.result.trades : null, vm);
    }
    // 订阅回测进度（离开页面时由 Router onLeave 清理）
    vm.subscribeProgress();
    vm.loadHistory();
    vm.subscribe(paint);
  }
  export { render };