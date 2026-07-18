import * as KlineChart from './kline-chart.js';

let ctrl = null;
  let currentSymbol = "";
  let currentPeriod = "1d";
  let dividendType = "front";

  function render(root, vm) {
    // 清理旧实例
    if (ctrl) { try { ctrl.destroy(); } catch (e) {} ctrl = null; }

    root.innerHTML = '<div id="klineContainer" style="width:100%"></div>';
    const container = root.querySelector("#klineContainer");

    // 挂载 K线组件（含标题行、周期选择器、设置按钮）
    ctrl = KlineChart.mount(container, {
      bars: [],
      title: "查询股票后显示K线图",
      periods: KlineChart.PERIOD_OPTIONS,
      period: currentPeriod,
      dividendType: dividendType,
      height: 380,
      onPeriodChange: function (period) {
        currentPeriod = period;
        if (currentSymbol) loadHistory(currentSymbol);
      },
      onSettingsChange: function (settings) {
        if (settings.dividendType && settings.dividendType !== dividendType) {
          dividendType = settings.dividendType;
          if (currentSymbol) loadHistory(currentSymbol);
        }
      },
    });

    async function loadHistory(symbol) {
      if (!symbol) {
        ctrl.update([], { title: "查询股票后显示K线图" });
        return;
      }
      currentSymbol = symbol;
      ctrl.update([], { title: symbol + " 加载中..." });
      try {
        // count = -1: 无上限加载全部历史数据
        const resp = await vm.facade.quote.history(symbol, currentPeriod, -1, dividendType);
        if (resp.ok && resp.data && resp.data.bars && resp.data.bars.length) {
          var name = vm.state.data ? vm.state.data.name : "";
          var barCount = resp.data.bars.length;
          // 默认显示最近 150 根（不超过总条数）
          var visibleBars = Math.min(barCount, 150);
          var dzStart = Math.max(0, 100 - (visibleBars / barCount * 100));
          ctrl.update(resp.data.bars, {
            title: (name ? name + " " : "") + symbol,
            dividendType: dividendType,
            dataZoomStart: dzStart,
            dataZoomEnd: 100,
          });
        } else {
          ctrl.update([], { title: resp.error || "无数据" });
        }
      } catch (e) {
        ctrl.update([], { title: e.message });
      }
    }

    vm.subscribe((state) => {
      if (state.data && state.data.code) loadHistory(state.data.code);
      else ctrl.update([], { title: "查询股票后显示K线图" });
    });
  }

  export { render };