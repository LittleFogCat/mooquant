import * as StockSearch from './stock-search.js';

const QUICK_TAGS = [
    { symbol: "sh600519", label: "贵州茅台" },
    { symbol: "sz000001", label: "平安银行" },
    { symbol: "sh601318", label: "中国平安" },
    { symbol: "sz300750", label: "宁德时代" },
    { symbol: "sh600036", label: "招商银行" },
    { symbol: "sz002594", label: "比亚迪" },
  ];

  function render(root, viewModel) {
    var searchCtrl = null;
    root.innerHTML = `
      <div class="search-row">
        <div class="search-input-wrap">
          <input id="symbolInput" class="search-input" type="text" placeholder="输入代码/名称/拼音，如：sh600519 / 茅台 / gzmt" autocomplete="off" spellcheck="false" />
        </div>
        <button id="queryBtn" class="search-btn"><span class="btn-text">查询</span></button>
      </div>
      <div class="quick-tags">
        <span class="tag-label">热门：</span>
        ${QUICK_TAGS.map(t => `<button class="tag" data-symbol="${t.symbol}">${t.label}</button>`).join("")}
      </div>
    `;

    const input = root.querySelector("#symbolInput");
    const btn = root.querySelector("#queryBtn");

    function applyState(state) {
      if (input.value !== state.symbol) input.value = state.symbol || "";
      input.classList.toggle("invalid", !!state.invalid);
      btn.classList.toggle("loading", !!state.loading);
      btn.disabled = !!state.loading;
    }
    viewModel.subscribe(applyState);

    if (searchCtrl) searchCtrl.destroy();
    searchCtrl = StockSearch.mount(input, viewModel.facade, function (stock) {
      viewModel.querySymbol(stock.code);
    }, function (val) {
      viewModel.setSymbol(val);
    });

    btn.addEventListener("click", () => { searchCtrl.hide(); viewModel.query(); });
    root.querySelectorAll(".tag").forEach(t => {
      t.addEventListener("click", () => { searchCtrl.hide(); viewModel.querySymbol(t.dataset.symbol); });
    });
  }
  export { render };