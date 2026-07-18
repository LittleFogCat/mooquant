import * as echarts from 'echarts';

// ---- 工具函数 ----
  function fmtPrice(v) {
    if (v == null || isNaN(v)) return "-";
    return v < 10 ? Number(v).toFixed(3) : Number(v).toFixed(2);
  }
  function fmtVol(v) {
    if (v == null || isNaN(v)) return "-";
    if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
    if (v >= 1e4) return (v / 1e4).toFixed(2) + "万";
    return String(v);
  }
  function fmtNum(n, d) {
    d = d || 2;
    if (n == null || isNaN(n)) return "-";
    return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function toDate(b) {
    if (b.date) return b.date;
    if (b.time) {
      var d = new Date(b.time * 1000);
      var yyyy = d.getFullYear();
      var MM = String(d.getMonth() + 1).padStart(2, "0");
      var dd = String(d.getDate()).padStart(2, "0");
      var HH = String(d.getHours()).padStart(2, "0");
      var mm = String(d.getMinutes()).padStart(2, "0");
      return yyyy + "-" + MM + "-" + dd + " " + HH + ":" + mm;
    }
    return "";
  }

  // ---- 常量 ----
  var PERIOD_OPTIONS = [
    { value: "1d", label: "日K" },
    { value: "1w", label: "周K" },
    { value: "1mon", label: "月K" },
    { value: "60m", label: "60分" },
    { value: "30m", label: "30分" },
    { value: "15m", label: "15分" },
    { value: "5m", label: "5分" },
    { value: "1m", label: "1分" },
  ];
  var LINE_CHART_THRESHOLD = 200; // 超过此数量降级为收盘价折线图

  // ---- 主函数：mount ----
  function mount(container, opts) {
    opts = opts || {};
    if (!container) return null;

    // 清理旧实例
    _cleanupContainer(container);

    var state = {
      bars: opts.bars || [],
      title: opts.title || "",
      period: opts.period || "1d",
      periods: opts.periods || null,
      trades: opts.trades || [],
      dividendType: opts.dividendType || "front",
      showTrades: opts.showTrades != null ? opts.showTrades : true,
      dataZoomStart: opts.dataZoomStart != null ? opts.dataZoomStart : 0,
      dataZoomEnd: opts.dataZoomEnd != null ? opts.dataZoomEnd : 100,
    };

    var chart = null;
    var resizeObserver = null;
    var settingsPanel = null;

    container.style.position = "relative";
    container.innerHTML = "";

    // ---- 构建 DOM ----
    // Header row: title + period selector + info + settings button
    var header = document.createElement("div");
    header.style.cssText =
      "display:flex;align-items:center;gap:8px;margin-bottom:6px;flex-shrink:0;";

    // Title
    var titleEl = document.createElement("span");
    titleEl.style.cssText = "font-size:14px;font-weight:600;color:var(--text-1);white-space:nowrap;";
    titleEl.textContent = state.title;
    header.appendChild(titleEl);

    // Period selector
    var periodSel = null;
    if (state.periods && state.periods.length) {
      periodSel = document.createElement("select");
      periodSel.className = "input-field";
      periodSel.style.cssText = "width:auto;height:28px;font-size:12px;padding:0 6px;flex-shrink:0;";
      periodSel.innerHTML = state.periods.map(function (p) {
        return '<option value="' + p.value + '"' + (p.value === state.period ? " selected" : "") + ">" + p.label + "</option>";
      }).join("");
      periodSel.addEventListener("change", function () {
        state.period = periodSel.value;
        if (typeof opts.onPeriodChange === "function") opts.onPeriodChange(state.period);
      });
      header.appendChild(periodSel);
    }

    // Info text (bar count, etc.)
    var infoEl = document.createElement("span");
    infoEl.style.cssText = "flex:1;font-size:12px;color:var(--text-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
    header.appendChild(infoEl);

    // Settings button
    var settingsBtn = document.createElement("div");
    settingsBtn.innerHTML = "⚙";
    settingsBtn.title = "设置";
    settingsBtn.style.cssText = "cursor:pointer;font-size:16px;color:var(--text-3);user-select:none;line-height:1;flex-shrink:0;padding:2px;";
    header.appendChild(settingsBtn);

    container.appendChild(header);

    // Settings panel (floating, below settings button)
    settingsPanel = document.createElement("div");
    settingsPanel.style.cssText =
      "position:absolute;top:36px;right:0;z-index:101;display:none;" +
      "background:rgba(26,26,37,0.98);border:1px solid rgba(255,255,255,0.12);" +
      "border-radius:8px;padding:12px 14px;min-width:170px;font-size:13px;color:#e8e8ed;" +
      "box-shadow:0 4px 16px rgba(0,0,0,0.4);";
    container.appendChild(settingsPanel);

    // Chart container
    var chartDiv = document.createElement("div");
    chartDiv.style.cssText = "width:100%;height:" + (opts.height || 380) + "px;";
    container.appendChild(chartDiv);

    // ---- Settings panel logic ----
    function rebuildSettingsPanel() {
      var hasTrades = state.trades.length > 0;
      var html = "";
      if (hasTrades) {
        html += '<div style="margin-bottom:10px"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">' +
          '<input type="checkbox" class="kline-cb-trades" ' + (state.showTrades ? "checked" : "") + ' style="cursor:pointer;accent-color:#6366f1" />' +
          "显示BS点</label></div>";
      }
      html += "<div><div style=\"margin-bottom:4px;color:#9ca3af\">复权类型</div>" +
        '<select class="kline-sel-dividend" style="width:100%;background:#1a1a25;color:#e8e8ed;border:1px solid rgba(255,255,255,0.15);border-radius:4px;padding:4px 6px;font-size:13px;cursor:pointer">' +
        '<option value="front"' + (state.dividendType === "front" ? " selected" : "") + ">前复权</option>" +
        '<option value="back"' + (state.dividendType === "back" ? " selected" : "") + ">后复权</option>" +
        '<option value="none"' + (state.dividendType === "none" ? " selected" : "") + ">不复权</option>" +
        "</select></div>";
      settingsPanel.innerHTML = html;

      var cbTrades = settingsPanel.querySelector(".kline-cb-trades");
      if (cbTrades) {
        cbTrades.onchange = function () {
          state.showTrades = cbTrades.checked;
          _toggleTrades(chart, state.showTrades, state._buyDots, state._sellDots, state._buyLabels, state._sellLabels, state._markLineData);
        };
      }
      var selDiv = settingsPanel.querySelector(".kline-sel-dividend");
      if (selDiv) {
        selDiv.onchange = function () {
          state.dividendType = selDiv.value;
          if (typeof opts.onSettingsChange === "function") opts.onSettingsChange({ dividendType: selDiv.value });
        };
      }
    }

    settingsBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      settingsPanel.style.display = settingsPanel.style.display === "none" ? "block" : "none";
    });

    // Outside click to close settings
    var outsideHandler = function (e) {
      if (settingsPanel.style.display === "block" && !settingsPanel.contains(e.target) && e.target !== settingsBtn) {
        settingsPanel.style.display = "none";
      }
    };
    document.addEventListener("click", outsideHandler);

    // ---- Chart rendering ----
    function renderChart() {
      var bars = state.bars;
      if (!bars || bars.length < 2) {
        if (chart) { try { chart.dispose(); } catch (e) {} chart = null; }
        chart = echarts.init(chartDiv, "dark");
        chart.setOption({
          backgroundColor: "transparent",
          title: { text: state.title || "暂无数据", left: "center", top: "center", textStyle: { color: "#5a5a68", fontSize: 14, fontWeight: "normal" } }
        });
        infoEl.textContent = "";
        return;
      }

      // Dispose old chart
      if (chart) { try { chart.dispose(); } catch (e) {} chart = null; }
      chart = echarts.init(chartDiv, "dark");

      var dates = bars.map(toDate);
      var ohlc = bars.map(function (b) { return [Number(b.open), Number(b.close), Number(b.low), Number(b.high)]; });
      var volumes = bars.map(function (b) { return Number(b.volume); });
      var closePrices = bars.map(function (b) { return Number(b.close); });
      var dzStart = state.dataZoomStart, dzEnd = state.dataZoomEnd;
      // 阈值基于可见条数（dataZoom 范围内），而非总条数
      var visibleCount = Math.ceil(bars.length * (dzEnd - dzStart) / 100);
      var useLineChart = visibleCount > LINE_CHART_THRESHOLD;

      // Update info
      infoEl.textContent = bars.length + " 根" + (useLineChart ? "（折线图模式）" : "");

      // ---- B/S markers (only in candlestick mode) ----
      var buyDots = [], sellDots = [], buyLabels = [], sellLabels = [], markLineData = [];
      var trades = state.trades || [];
      var showLine = !useLineChart && (opts.showLabel != null ? opts.showLabel : bars.length <= 60);
      if (!useLineChart && trades.length) {
        var barMap = {};
        bars.forEach(function (b, i) { barMap[toDate(b)] = i; });
        trades.forEach(function (t) {
          var idx = barMap[t.date];
          if (idx == null) return;
          var bar = bars[idx];
          var lo = Number(bar.low), hi = Number(bar.high);
          var price = Number(t.price);
          var offset = Math.max((hi - lo) * 0.5, price * 0.012);
          var info = { side: t.side, price: t.price, quantity: t.quantity, amount: t.amount, pnl: t.pnl };
          if (t.side === "buy") {
            var labelY = lo - offset;
            buyDots.push({ value: [t.date, price], info: info });
            buyLabels.push({ value: [t.date, labelY] });
            if (showLine) markLineData.push([{ coord: [t.date, price], itemStyle: { color: "#ef4444" } }, { coord: [t.date, labelY], itemStyle: { color: "#ef4444" } }]);
          } else {
            var labelY2 = hi + offset;
            sellDots.push({ value: [t.date, price], info: info });
            sellLabels.push({ value: [t.date, labelY2] });
            if (showLine) markLineData.push([{ coord: [t.date, price], itemStyle: { color: "#3b82f6" } }, { coord: [t.date, labelY2], itemStyle: { color: "#3b82f6" } }]);
          }
        });
      }
      // Store for toggleTrades
      state._buyDots = buyDots; state._sellDots = sellDots;
      state._buyLabels = buyLabels; state._sellLabels = sellLabels;
      state._markLineData = markLineData;

      var showTrades = state.showTrades && !useLineChart && trades.length > 0;

      // ---- Build series ----
      var series = [];
      if (useLineChart) {
        // 折线图模式：收盘价折线
        series.push({
          name: "收盘价", type: "line", data: closePrices, symbol: "none",
          lineStyle: { color: "#6366f1", width: 1.5 },
          areaStyle: { color: "rgba(99,102,241,0.08)" },
        });
      } else {
        // 蜡烛图模式
        series.push({
          name: "K线", type: "candlestick", data: ohlc, barMaxWidth: 10, barMinWidth: 2,
          itemStyle: {
            color: "transparent", color0: "#22c55e",
            borderColor: "#ef4444", borderColor0: "#22c55e",
            borderWidth: 1
          },
          markLine: showTrades ? { symbol: "none", silent: true, lineStyle: { type: "dashed", width: 1 }, data: markLineData } : undefined,
        });
        // B dots
        series.push({
          name: "B", type: "scatter", data: showTrades ? buyDots : [], symbol: "circle", symbolSize: 5,
          itemStyle: { color: "#ef4444" }, z: 10,
        });
        // S dots
        series.push({
          name: "S", type: "scatter", data: showTrades ? sellDots : [], symbol: "circle", symbolSize: 5,
          itemStyle: { color: "#3b82f6" }, z: 10,
        });
        // B labels
        series.push({
          name: "B标签", type: "scatter", data: showTrades ? buyLabels : [], symbol: "circle", symbolSize: 0,
          label: { show: true, formatter: "B", color: "#fff", backgroundColor: "#ef4444", borderRadius: 3, padding: [1, 3], fontSize: 9 },
          z: 11, silent: true,
        });
        // S labels
        series.push({
          name: "S标签", type: "scatter", data: showTrades ? sellLabels : [], symbol: "circle", symbolSize: 0,
          label: { show: true, formatter: "S", color: "#fff", backgroundColor: "#3b82f6", borderRadius: 3, padding: [1, 3], fontSize: 9 },
          z: 11, silent: true,
        });
      }

      // Volume sub-chart
      series.push({
        name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes,
        itemStyle: { color: function (p) { var d = ohlc[p.dataIndex]; return d && d[1] >= d[0] ? "rgba(239,68,68,0.5)" : "rgba(34,197,94,0.5)"; } },
      });

      // ---- Tooltip ----
      var tooltipFormatter;
      if (useLineChart) {
        tooltipFormatter = function (params) {
          if (!params || !params.length) return "";
          var date = params[0].axisValueLabel || params[0].axisValue || "";
          var lines = [date];
          for (var i = 0; i < params.length; i++) {
            var p = params[i];
            if (p.seriesType === "line") {
              var idx = p.dataIndex;
              var close = closePrices[idx];
              var prevClose = idx > 0 ? closePrices[idx - 1] : null;
              var pct = prevClose != null && prevClose !== 0 ? (close - prevClose) / prevClose * 100 : null;
              var color = pct != null && pct >= 0 ? "#ef4444" : "#22c55e";
              lines.push("收盘: " + fmtPrice(close));
              if (pct != null) lines.push('<span style="color:' + color + '">涨幅: ' + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%</span>");
              lines.push("成交量: " + fmtVol(volumes[idx]));
            }
          }
          return lines.join("<br/>");
        };
      } else {
        tooltipFormatter = function (params) {
          if (!params || !params.length) return "";
          var date = params[0].axisValueLabel || params[0].axisValue || "";
          var lines = [date];
          var candleDone = false;
          for (var i = 0; i < params.length; i++) {
            var p = params[i];
            if (p.seriesType === "candlestick" && p.dataIndex != null && ohlc[p.dataIndex] && !candleDone) {
              var d = ohlc[p.dataIndex];
              var open = d[0], close = d[1], low = d[2], high = d[3];
              var color = close >= open ? "#ef4444" : "#22c55e";
              var prevClose = (p.dataIndex > 0 && ohlc[p.dataIndex - 1]) ? ohlc[p.dataIndex - 1][1] : null;
              var change = prevClose != null ? close - prevClose : null;
              var pct = (prevClose != null && prevClose !== 0) ? (close - prevClose) / prevClose * 100 : null;
              lines.push("开盘: " + fmtPrice(open));
              lines.push("收盘: " + fmtPrice(close));
              lines.push("最低: " + fmtPrice(low));
              lines.push("最高: " + fmtPrice(high));
              if (pct != null) lines.push('<span style="color:' + color + '">涨幅: ' + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%</span>");
              if (change != null) lines.push('<span style="color:' + color + '">涨跌: ' + (change >= 0 ? "+" : "") + fmtPrice(change) + "</span>");
              lines.push("成交量: " + fmtVol(volumes[p.dataIndex]));
              candleDone = true;
            } else if (p.seriesType === "scatter" && p.data != null && p.data.info) {
              var info = p.data.info;
              var label = info.side === "buy" ? "买入" : "卖出";
              var c = info.side === "buy" ? "#ef4444" : "#3b82f6";
              lines.push('<span style="color:' + c + '">' + label + ": " + fmtNum(info.price) + " 股  数量: " + info.quantity + "  金额: " + fmtNum(info.amount, 0) + "</span>");
              if (info.pnl != null) lines.push('<span style="color:' + c + '">盈亏: ' + fmtNum(info.pnl) + "</span>");
            }
          }
          return lines.join("<br/>");
        };
      }

      // ---- Chart option ----
      chart.setOption({
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis", axisPointer: { type: "cross" },
          formatter: tooltipFormatter,
          backgroundColor: "rgba(26,26,37,0.95)",
          borderColor: "rgba(255,255,255,0.08)",
          textStyle: { color: "#e8e8ed", fontSize: 12 }
        },
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        grid: [
          { left: "8%", right: "6%", top: "5%", height: "55%" },
          { left: "8%", right: "6%", top: "68%", height: "18%" }
        ],
        xAxis: [
          { type: "category", data: dates, scale: true, boundaryGap: !useLineChart, splitLine: { show: false }, axisLine: { lineStyle: { color: "rgba(255,255,255,0.1)" } }, axisLabel: { color: "#5a5a68", fontSize: 10 } },
          { type: "category", gridIndex: 1, data: dates, scale: true, boundaryGap: true, splitLine: { show: false }, axisLabel: { show: false } }
        ],
        yAxis: [
          { scale: true, splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } }, axisLabel: { color: "#5a5a68", fontSize: 10, formatter: function (v) { return fmtPrice(v); } } },
          { gridIndex: 1, scale: true, splitNumber: 2, axisLabel: { color: "#5a5a68", fontSize: 10, formatter: function (v) { return fmtVol(v); } }, splitLine: { show: false } }
        ],
        dataZoom: [
          { type: "inside", xAxisIndex: [0, 1], start: dzStart, end: dzEnd },
          { show: true, type: "slider", xAxisIndex: [0, 1], top: "90%", start: dzStart, end: dzEnd, height: 16, borderColor: "transparent", backgroundColor: "rgba(255,255,255,0.03)", fillerColor: "rgba(99,102,241,0.15)", handleStyle: { color: "#6366f1" }, textStyle: { color: "#5a5a68", fontSize: 10 } }
        ],
        series: series
      }, true);

      rebuildSettingsPanel();
    }

    // ---- Public API ----
    function update(bars, updateOpts) {
      state.bars = bars || [];
      if (updateOpts) {
        if (updateOpts.title != null) { state.title = updateOpts.title; titleEl.textContent = state.title; }
        if (updateOpts.trades != null) state.trades = updateOpts.trades;
        if (updateOpts.dividendType != null) state.dividendType = updateOpts.dividendType;
        if (updateOpts.showTrades != null) state.showTrades = updateOpts.showTrades;
        if (updateOpts.dataZoomStart != null) state.dataZoomStart = updateOpts.dataZoomStart;
        if (updateOpts.dataZoomEnd != null) state.dataZoomEnd = updateOpts.dataZoomEnd;
      }
      renderChart();
    }

    function resize() { if (chart) chart.resize(); }

    function destroy() {
      if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }
      document.removeEventListener("click", outsideHandler);
      if (chart) { try { chart.dispose(); } catch (e) {} chart = null; }
      container.innerHTML = "";
    }

    // ---- ResizeObserver: 修复容器宽度变化时图表压缩 ----
    if (window.ResizeObserver) {
      resizeObserver = new ResizeObserver(function () { if (chart) chart.resize(); });
      resizeObserver.observe(chartDiv);
    }

    // ---- 初始渲染 ----
    renderChart();
    // 确保下一帧正确尺寸（修复页面切换后宽度为 0 的问题）
    requestAnimationFrame(function () { if (chart) chart.resize(); });

    return { update: update, resize: resize, destroy: destroy };
  }

  // ---- 辅助函数 ----
  function _cleanupContainer(container) {
    var ctrl = container._klineCtrl;
    if (ctrl && typeof ctrl.destroy === "function") { try { ctrl.destroy(); } catch (e) {} }
    var stale = echarts.getInstanceByDom(container);
    if (stale) { try { stale.dispose(); } catch (e) {} }
  }

  function _toggleTrades(chart, show, buyDots, sellDots, buyLabels, sellLabels, markLineData) {
    if (!chart) return;
    chart.setOption({
      series: [
        { name: "K线", markLine: { symbol: "none", silent: true, lineStyle: { type: "dashed", width: 1 }, data: show ? markLineData : [] } },
        { name: "B", data: show ? buyDots : [] },
        { name: "S", data: show ? sellDots : [] },
        { name: "B标签", data: show ? buyLabels : [] },
        { name: "S标签", data: show ? sellLabels : [] }
      ]
    });
  }

  // ---- 旧 API 兼容 ----
  function render(container, bars, opts) {
    opts = opts || {};
    var ctrl = mount(container, Object.assign({}, opts, { bars: bars }));
    container._klineCtrl = ctrl;
    return ctrl;
  }

  function renderEmpty(container, text) {
    var ctrl = mount(container, { bars: [], title: text || "暂无数据" });
    container._klineCtrl = ctrl;
    return ctrl;
  }

  export { mount, render, renderEmpty, fmtPrice, fmtVol, PERIOD_OPTIONS };