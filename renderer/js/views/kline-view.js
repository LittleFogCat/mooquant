/**
 * mookquant · K-Line Chart View (ECharts)
 */
(function () {
  let chart = null;
  let currentSymbol = "";
  let currentPeriod = "1d";

  function render(root, vm) {
    root.innerHTML = `
      <div class="kline-header">
        <select class="input-field kline-period" id="klinePeriod" style="width:auto;height:32px;font-size:13px">
          <option value="1d">日K</option>
          <option value="1w">周K</option>
          <option value="1m">60分钟</option>
        </select>
        <span class="kline-info" id="klineInfo"></span>
      </div>
      <div id="klineChart" style="width:100%;height:380px"></div>
    `;

    const container = root.querySelector("#klineChart");
    const periodSel = root.querySelector("#klinePeriod");
    const infoEl = root.querySelector("#klineInfo");

    // Initialize ECharts
    if (chart) chart.dispose();
    chart = echarts.init(container, "dark");
    chart.setOption(getEmptyOption());

    window.addEventListener("resize", () => { if (chart) chart.resize(); });

    async function loadHistory(symbol) {
      if (!symbol) { chart.setOption(getEmptyOption()); infoEl.textContent = ""; return; }
      currentSymbol = symbol;
      infoEl.textContent = "加载中...";
      try {
        const resp = await vm.facade.quote.history(symbol, currentPeriod, 120);
        if (resp.ok && resp.data && resp.data.bars && resp.data.bars.length) {
          renderChart(resp.data.bars);
          infoEl.textContent = resp.data.bars.length + " 根";
        } else {
          chart.setOption(getEmptyOption());
          infoEl.textContent = resp.error || "无数据";
        }
      } catch (e) {
        chart.setOption(getEmptyOption());
        infoEl.textContent = e.message;
      }
    }

    function renderChart(bars) {
      const dates = bars.map(b => b.date || new Date(b.time * 1000).toISOString().slice(0, 10));
      const ohlc = bars.map(b => [b.open, b.close, b.low, b.high]);
      const volumes = bars.map(b => b.volume);
      const maxVol = Math.max(...volumes, 1);

      chart.setOption({
        backgroundColor: "transparent",
        title: { show: false },
        animation: false,
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross" },
          backgroundColor: "rgba(26,26,37,0.95)",
          borderColor: "rgba(255,255,255,0.08)",
          textStyle: { color: "#e8e8ed", fontSize: 12 },
        },
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        grid: [
          { left: "8%", right: "6%", top: "5%", height: "58%" },
          { left: "8%", right: "6%", top: "70%", height: "20%" },
        ],
        xAxis: [
          { type: "category", data: dates, scale: true, boundaryGap: false, splitLine: { show: false }, axisLine: { lineStyle: { color: "rgba(255,255,255,0.1)" } }, axisLabel: { color: "#5a5a68", fontSize: 10 } },
          { type: "category", gridIndex: 1, data: dates, scale: true, boundaryGap: false, splitLine: { show: false }, axisLabel: { show: false } },
        ],
        yAxis: [
          { scale: true, splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } }, axisLabel: { color: "#5a5a68", fontSize: 10 } },
          { gridIndex: 1, scale: true, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
        ],
        dataZoom: [
          { type: "inside", xAxisIndex: [0, 1], start: 50, end: 100 },
          { show: true, type: "slider", xAxisIndex: [0, 1], top: "92%", start: 50, end: 100, height: 16, borderColor: "transparent", backgroundColor: "rgba(255,255,255,0.03)", fillerColor: "rgba(99,102,241,0.15)", handleStyle: { color: "#6366f1" }, textStyle: { color: "#5a5a68", fontSize: 10 } },
        ],
        series: [
          {
            name: "K线",
            type: "candlestick",
            data: ohlc,
            itemStyle: {
              color: "#ef4444",
              color0: "#22c55e",
              borderColor: "#ef4444",
              borderColor0: "#22c55e",
            },
          },
          {
            name: "成交量",
            type: "bar",
            xAxisIndex: 1,
            yAxisIndex: 1,
            data: volumes.map((v, i) => ({
              value: v,
              itemStyle: { color: ohlc[i][1] >= ohlc[i][0] ? "rgba(239,68,68,0.4)" : "rgba(34,197,94,0.4)" },
            })),
          },
        ],
      });
    }

    function getEmptyOption() {
      return {
        backgroundColor: "transparent",
        title: { text: "查询股票后显示K线图", left: "center", top: "center", textStyle: { color: "#5a5a68", fontSize: 14, fontWeight: "normal" } },
      };
    }

    periodSel.addEventListener("change", () => {
      currentPeriod = periodSel.value;
      if (vm.state.data) loadHistory(vm.state.data.code);
    });

    vm.subscribe((state) => {
      if (state.data && state.data.code) loadHistory(state.data.code);
      else { chart.setOption(getEmptyOption()); infoEl.textContent = ""; }
    });
  }

  window.KlineView = { render };
})();