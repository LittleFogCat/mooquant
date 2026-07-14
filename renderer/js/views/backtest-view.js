/**
 * mookquant · Backtest View (flatpickr)
 */
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"'/]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;","/":"&#x2F;" }[c])); }
  function fmtPct(n) { if (n == null || isNaN(n)) return "-"; return (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%"; }
  function fmtNum(n, d) { d = d || 2; if (n == null || isNaN(n)) return "-"; return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function cls(n) { if (n > 0) return "up"; if (n < 0) return "down"; return "flat"; }

  let _fpStart = null, _fpEnd = null;

  function renderConfig(state, vm) {
    const opts = state.strategies.map(s => `<option value="${esc(s.id)}" ${state.selectedId === s.id ? "selected" : ""}>${esc(s.name)}</option>`).join("");
    return `
      <div class="card">
        <div class="card-title">回测配置</div>
        ${state.error ? `<div class="status error" style="margin-bottom:16px">${esc(state.error)}</div>` : ""}
        <div class="form-group">
          <label class="form-label">选择策略</label>
          <select class="input-field" id="bt_strategy">${opts || `<option value="">请先在策略页创建策略</option>`}</select>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">开始日期</label><input class="input-field" id="bt_start" placeholder="点击选择日期" readonly /></div>
          <div class="form-group"><label class="form-label">结束日期</label><input class="input-field" id="bt_end" placeholder="点击选择日期" readonly /></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">初始资金</label><input class="input-field" type="number" id="bt_capital" value="${state.initialCapital}" /></div>
          <div class="form-group"><label class="form-label">手续费率</label><input class="input-field" type="number" step="0.0001" id="bt_commission" value="${state.commission}" /></div>
          <div class="form-group"><label class="form-label">滑点</label><input class="input-field" type="number" step="0.001" id="bt_slippage" value="${state.slippage}" /></div>
        </div>
        <button class="btn btn-primary" id="bt_run" ${state.running ? "disabled" : ""}>${state.running ? "回测中..." : "开始回测"}</button>
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
      <div class="stat-card"><div class="stat-label">年化收益率</div><div class="stat-value ${cls(m.annualReturn)}">${fmtPct(m.annualReturn)}</div></div>
      <div class="stat-card"><div class="stat-label">最大回撤</div><div class="stat-value down">${fmtPct(m.maxDrawdown)}</div></div>
      <div class="stat-card"><div class="stat-label">夏普比率</div><div class="stat-value">${fmtNum(m.sharpeRatio)}</div></div>
      <div class="stat-card"><div class="stat-label">胜率</div><div class="stat-value">${fmtPct(m.winRate)}</div></div>
      <div class="stat-card"><div class="stat-label">盈亏比</div><div class="stat-value">${fmtNum(m.profitLossRatio)}</div></div>
      <div class="stat-card"><div class="stat-label">交易次数</div><div class="stat-value">${m.totalTrades || 0}</div></div>
      <div class="stat-card"><div class="stat-label">最终资金</div><div class="stat-value">${fmtNum(m.finalCapital, 0)}</div></div>
    `;
    let curveHtml = `<div class="equity-curve">暂无净值数据</div>`;
    if (equity.length > 1) {
      const w = 800, h = 200, pad = 20;
      const vals = equity.map(e => e.value);
      const minV = Math.min(...vals), maxV = Math.max(...vals), range = maxV - minV || 1;
      const pts = equity.map((e, i) => { const x = pad + (i / (equity.length - 1)) * (w - 2 * pad); const yy = h - pad - ((e.value - minV) / range) * (h - 2 * pad); return x.toFixed(1) + "," + yy.toFixed(1); }).join(" ");
      curveHtml = `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:200px"><polyline fill="none" stroke="#6366f1" stroke-width="2" points="${pts}" /></svg>`;
    }
    const tradeRows = trades.slice(0, 50).map(t => `
      <tr><td>${esc(t.date)}</td><td><span class="badge badge-${t.side === "buy" ? "buy" : "sell"}">${t.side === "buy" ? "买入" : "卖出"}</span></td><td>${esc(t.symbol)}</td><td class="num">${fmtNum(t.price)}</td><td class="num">${t.quantity}</td><td class="num">${fmtNum(t.amount, 0)}</td><td class="${cls(t.pnl)} num">${t.pnl ? fmtNum(t.pnl) : "-"}</td></tr>
    `).join("");
    return `
      <div class="card"><div class="card-title">绩效指标</div><div class="stats-grid">${statsHtml}</div></div>
      <div class="card"><div class="card-title">净值曲线</div>${curveHtml}</div>
      <div class="card"><div class="card-title">交易记录 (前 50 笔)</div>${trades.length ? `<table class="data-table"><thead><tr><th>日期</th><th>方向</th><th>标的</th><th class="num">价格</th><th class="num">数量</th><th class="num">金额</th><th class="num">盈亏</th></tr></thead><tbody>${tradeRows}</tbody></table>` : `<div class="empty-state"><div class="empty-state-text">无交易记录</div></div>`}</div>
    `;
  }

  function render(root, vm) {
    function paint(state) {
      // Destroy old flatpickr instances
      if (_fpStart) { _fpStart.destroy(); _fpStart = null; }
      if (_fpEnd) { _fpEnd.destroy(); _fpEnd = null; }

      root.innerHTML = renderConfig(state, vm) + renderResult(state.result);

      // Bind strategy select
      const stratEl = document.getElementById("bt_strategy");
      if (stratEl) stratEl.addEventListener("change", () => vm.setField("selectedId", stratEl.value));
      // Bind number inputs
      const numIds = { bt_capital: "initialCapital", bt_commission: "commission", bt_slippage: "slippage" };
      for (const [elId, field] of Object.entries(numIds)) {
        const el = document.getElementById(elId);
        if (el) el.addEventListener("change", () => vm.setField(field, el.value));
      }
      // Init flatpickr on date inputs
      const startEl = document.getElementById("bt_start");
      const endEl = document.getElementById("bt_end");
      if (startEl) {
        startEl.value = state.startDate || "";
        _fpStart = flatpickr(startEl, {
          locale: "zh",
          dateFormat: "Y-m-d",
          theme: "dark",
          maxDate: "today",
          onChange: (dates, val) => vm.setField("startDate", val),
        });
      }
      if (endEl) {
        endEl.value = state.endDate || "";
        _fpEnd = flatpickr(endEl, {
          locale: "zh",
          dateFormat: "Y-m-d",
          theme: "dark",
          maxDate: "today",
          onChange: (dates, val) => vm.setField("endDate", val),
        });
      }
      // Run button
      const runBtn = document.getElementById("bt_run");
      if (runBtn) runBtn.addEventListener("click", () => vm.run());
    }
    vm.subscribe(paint);
  }
  window.BacktestView = { render };
})();