/**
 * mookquant · Search Panel View（带名称搜索）
 */
(function () {
  const QUICK_TAGS = [
    { symbol: "sh600519", label: "贵州茅台" },
    { symbol: "sz000001", label: "平安银行" },
    { symbol: "sh601318", label: "中国平安" },
    { symbol: "sz300750", label: "宁德时代" },
    { symbol: "sh600036", label: "招商银行" },
    { symbol: "sz002594", label: "比亚迪" },
  ];

  function render(root, viewModel) {
    root.innerHTML = `
      <div class="search-row">
        <div class="search-input-wrap">
          <input id="symbolInput" class="search-input" type="text" placeholder="输入股票代码或名称，如：sh600519 / 茅台" autocomplete="off" spellcheck="false" />
          <div class="suggestions" id="suggestions" hidden></div>
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
    const suggBox = root.querySelector("#suggestions");
    let suggTimer = null;
    let selectedIdx = -1;
    let currentSuggestions = [];

    function applyState(state) {
      if (input.value !== state.symbol) input.value = state.symbol || "";
      input.classList.toggle("invalid", !!state.invalid);
      btn.classList.toggle("loading", !!state.loading);
      btn.disabled = !!state.loading;
    }
    viewModel.subscribe(applyState);

    function showSuggestions(items) {
      currentSuggestions = items;
      selectedIdx = -1;
      if (!items.length) { suggBox.hidden = true; return; }
      suggBox.innerHTML = items.map((s, i) => `
        <div class="suggestion-item" data-idx="${i}">
          <span class="sugg-code">${s.code}</span>
          <span class="sugg-name">${s.name}</span>
          <span class="sugg-industry">${s.industry || ""}</span>
        </div>
      `).join("");
      suggBox.hidden = false;
      suggBox.querySelectorAll(".suggestion-item").forEach(el => {
        el.addEventListener("click", () => {
          const idx = parseInt(el.dataset.idx);
          const s = currentSuggestions[idx];
          if (s) { viewModel.querySymbol(s.code); suggBox.hidden = true; }
        });
      });
    }

    input.addEventListener("input", () => {
      viewModel.setSymbol(input.value);
      const val = input.value.trim();
      if (suggTimer) clearTimeout(suggTimer);
      if (!val || val.length < 1) { suggBox.hidden = true; return; }
      suggTimer = setTimeout(async () => {
        try {
          const resp = await viewModel.facade.quote.search(val);
          if (resp.ok && resp.data && resp.data.length > 0) showSuggestions(resp.data);
          else suggBox.hidden = true;
        } catch (e) { suggBox.hidden = true; }
      }, 200);
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" && currentSuggestions.length) {
        e.preventDefault();
        selectedIdx = Math.min(selectedIdx + 1, currentSuggestions.length - 1);
        updateSuggHighlight();
      } else if (e.key === "ArrowUp" && currentSuggestions.length) {
        e.preventDefault();
        selectedIdx = Math.max(selectedIdx - 1, -1);
        updateSuggHighlight();
      } else if (e.key === "Enter") {
        if (selectedIdx >= 0 && currentSuggestions[selectedIdx]) {
          e.preventDefault();
          viewModel.querySymbol(currentSuggestions[selectedIdx].code);
          suggBox.hidden = true;
        } else {
          suggBox.hidden = true;
          viewModel.query();
        }
      } else if (e.key === "Escape") {
        suggBox.hidden = true;
      }
    });

    function updateSuggHighlight() {
      suggBox.querySelectorAll(".suggestion-item").forEach((el, i) => {
        el.classList.toggle("active", i === selectedIdx);
      });
      if (selectedIdx >= 0) {
        const el = suggBox.querySelectorAll(".suggestion-item")[selectedIdx];
        if (el) el.scrollIntoView({ block: "nearest" });
      }
    }

    document.addEventListener("click", (e) => {
      if (!root.contains(e.target)) suggBox.hidden = true;
    });

    btn.addEventListener("click", () => { suggBox.hidden = true; viewModel.query(); });
    root.querySelectorAll(".tag").forEach(t => {
      t.addEventListener("click", () => { suggBox.hidden = true; viewModel.querySymbol(t.dataset.symbol); });
    });
  }
  window.SearchPanelView = { render };
})();