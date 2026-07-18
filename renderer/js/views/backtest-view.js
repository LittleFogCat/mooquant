import * as echarts from 'echarts';
import flatpickr from 'flatpickr';
import 'flatpickr/dist/flatpickr.min.css';
import { Mandarin } from 'flatpickr/dist/l10n/zh';

flatpickr.localize(Mandarin);
import * as KlineChart from './kline-chart.js';

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"'/]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;","/":"&#x2F;" }[c])); }
  function fmtPct(n) { if (n == null || isNaN(n)) return "-"; return (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%"; }
  function fmtNum(n, d) { d = d || 2; if (n == null || isNaN(n)) return "-"; return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function cls(n) { if (n > 0) return "up"; if (n < 0) return "down"; return "flat"; }

  let _fpStart = null, _fpEnd = null;
  let _equityChart = null;
  let _klineChart = null;

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
      <div class="stat-card"><div class="stat-label">区间涨幅</div><div class="stat-value ${cls(m.benchmarkReturn)}">${fmtPct(m.benchmarkReturn)}</div></div>
      <div class="stat-card"><div class="stat-label">超额收益</div><div class="stat-value ${cls(m.excessReturn)}">${fmtPct(m.excessReturn)}</div></div>
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
      curveHtml = `<div id="equityChart" style="width:100%;height:260px"></div>`;
    }
    const klineBars = result.bars || [];
    let klineHtml = `<div class="equity-curve">暂无K线数据</div>`;
    if (klineBars.length > 1) {
      klineHtml = `<div id="btKlineChart" style="width:100%;height:360px"></div>`;
    }
    const tradeRows = trades.slice(0, 50).map(t => `
      <tr><td>${esc(t.date)}</td><td><span class="badge badge-${t.side === "buy" ? "buy" : "sell"}">${t.side === "buy" ? "买入" : "卖出"}</span></td><td>${esc(t.symbol)}</td><td class="num">${fmtNum(t.price)}</td><td class="num">${t.quantity}</td><td class="num">${fmtNum(t.amount, 0)}</td><td class="${cls(t.pnl)} num">${t.pnl ? fmtNum(t.pnl) : "-"}</td></tr>
    `).join("");
    return `
      <div class="card"><div class="card-title">绩效指标</div><div class="stats-grid">${statsHtml}</div></div>
      <div class="card"><div class="card-title">净值曲线</div>${curveHtml}</div>
      <div class="card"><div class="card-title">B/S 点</div>${klineHtml}</div>
      <div class="card"><div class="card-title">交易记录 (前 50 笔)</div>${trades.length ? `<table class="data-table"><thead><tr><th>日期</th><th>方向</th><th>标的</th><th class="num">价格</th><th class="num">数量</th><th class="num">金额</th><th class="num">盈亏</th></tr></thead><tbody>${tradeRows}</tbody></table>` : `<div class="empty-state"><div class="empty-state-text">无交易记录</div></div>`}</div>
    `;
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

  function renderKlineChart(bars, trades, vm) {
    const container = document.getElementById("btKlineChart");
    _klineChart = KlineChart.render(container, bars, {
      trades: trades || [],
      dividendType: (vm && vm.state && vm.state.dividendType) || "front",
      onSettingsChange: function (settings) {
        if (settings.dividendType && vm) {
          vm.setField("dividendType", settings.dividendType);
          vm.run();
        }
      }
    });
    if (!window._klineResizeBound) {
      window._klineResizeBound = true;
      window.addEventListener("resize", function () { if (_klineChart) _klineChart.resize(); });
    }
  }

  function render(root, vm) {
    var btSearchCtrl = null;
    function paint(state) {
      // Destroy old instances (before innerHTML wipes the DOM)
      if (_fpStart) { _fpStart.destroy(); _fpStart = null; }
      if (_fpEnd) { _fpEnd.destroy(); _fpEnd = null; }
      if (_klineChart) { try { _klineChart.destroy(); } catch (e) {} _klineChart = null; }
      if (_equityChart) { try { _equityChart.dispose(); } catch (e) {} _equityChart = null; }

      root.innerHTML = renderConfig(state, vm) + renderResult(state.result);

      // Bind strategy select
      const stratEl = document.getElementById("bt_strategy");
      if (stratEl) stratEl.addEventListener("change", () => vm.setField("selectedId", stratEl.value));
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
      // Render equity curve chart after DOM is ready
      renderEquityCurve(state.result ? state.result.equityCurve : null, state.result ? state.result.trades : null);
      renderKlineChart(state.result ? state.result.bars : null, state.result ? state.result.trades : null, vm);
    }
    vm.subscribe(paint);
  }
  export { render };