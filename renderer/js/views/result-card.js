function escapeHtml(s) {
    return String(s).replace(/[&<>"'\/]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
      "\"": "&quot;", "'": "&#39;", "/": "&#x2F;",
    }[c]));
  }

  function fmtNumber(n, digits = 2) {
    if (n === null || n === undefined || isNaN(n)) return "—";
    return n.toLocaleString("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  function fmtBig(n) {
    if (n === null || n === undefined) return "—";
    if (n >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
    if (n >= 1e4) return (n / 1e4).toFixed(2) + " 万";
    return n.toLocaleString("zh-CN");
  }

  function cls(change) {
    if (change > 0) return "up";
    if (change < 0) return "down";
    return "flat";
  }

  function badge(market) {
    if (market === "sh") return "badge-sh";
    if (market === "sz") return "badge-sz";
    if (market === "us") return "badge-us";
    return "badge-other";
  }

  function render(root, state, mode) {
    if (!state.data) {
      root.innerHTML = `
        <div class="quote-empty-state">
          <svg class="empty-icon" width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M3 3v18h18"/><path d="m7 14 4-4 4 4 5-5"/>
          </svg>
          <div class="empty-title">开始查询股票</div>
          <div class="empty-desc">在上方搜索栏输入股票代码或名称，或点击下方热门股票快速查看</div>
          <div class="empty-quick-tags">
            <span class="quick-tag" onclick="window._quickQuery&&window._quickQuery('sh600519')">贵州茅台</span>
            <span class="quick-tag" onclick="window._quickQuery&&window._quickQuery('sh600036')">招商银行</span>
            <span class="quick-tag" onclick="window._quickQuery&&window._quickQuery('sz000001')">平安银行</span>
            <span class="quick-tag" onclick="window._quickQuery&&window._quickQuery('sz300750')">宁德时代</span>
            <span class="quick-tag" onclick="window._quickQuery&&window._quickQuery('sh601318')">中国平安</span>
            <span class="quick-tag" onclick="window._quickQuery&&window._quickQuery('sz000858')">五粮液</span>
          </div>
        </div>`;
      return;
    }
    const d = state.data;
    const k = cls(d.change);
    const arrow = k === "up" ? "▲" : k === "down" ? "▼" : "—";
    const sign = d.change > 0 ? "+" : "";
    const timeStr = new Date(d.timestamp).toLocaleString("zh-CN", { hour12: false });

    const tag = mode === "qmt"
      ? `<span class="source-tag">● 实时</span>`
      : `<span class="source-tag mock">● 演示</span>`;

    root.innerHTML = `
      <article class="stock-card" role="article" aria-label="${escapeHtml(d.name)} 股票信息">
        <header class="stock-header">
          <div>
            <div class="stock-name-cn">${escapeHtml(d.name)}${tag}</div>
            <div class="stock-symbol">${escapeHtml(d.code)}</div>
            <span class="stock-market-badge ${badge(d.market)}">${escapeHtml(d.marketName)} · ${escapeHtml(d.currency)}</span>
          </div>
          <button class="btn btn-secondary btn-sm wl-toggle-btn" id="wlToggleBtn">⭐ 加入自选</button>
          <div class="price-block">
            <div class="current-price ${k}">${fmtNumber(d.price)}</div>
            <div class="change-row ${k}">
              <span class="arrow">${arrow}</span>
              ${sign}${fmtNumber(d.change)}
              &nbsp;${sign}${fmtNumber(d.changePercent)}%
            </div>
          </div>
        </header>
        <div class="metrics-grid">
          <div class="metric"><span class="metric-label">今开</span><span class="metric-value">${fmtNumber(d.open)}</span></div>
          <div class="metric"><span class="metric-label">昨收</span><span class="metric-value">${fmtNumber(d.prevClose)}</span></div>
          <div class="metric"><span class="metric-label">最高</span><span class="metric-value ${d.high >= d.prevClose ? "up" : ""}">${fmtNumber(d.high)}</span></div>
          <div class="metric"><span class="metric-label">最低</span><span class="metric-value ${d.low <= d.prevClose ? "down" : ""}">${fmtNumber(d.low)}</span></div>
          <div class="metric"><span class="metric-label">成交量</span><span class="metric-value">${fmtBig(d.volume)}</span></div>
          <div class="metric"><span class="metric-label">成交额</span><span class="metric-value">${escapeHtml(d.currency)} ${fmtBig(d.turnover)}</span></div>
          <div class="metric"><span class="metric-label">行业</span><span class="metric-value">${escapeHtml(d.industry || "—")}</span></div>
          <div class="metric"><span class="metric-label">振幅</span><span class="metric-value">${fmtNumber(((d.high - d.low) / d.prevClose) * 100)}%</span></div>
        </div>
        <div class="update-time">最后更新：${escapeHtml(timeStr)}</div>
      </article>
    `;
  }

  const _origRender = render;
  function renderWithWatchlist(root, state, mode) {
    _origRender(root, state, mode);
    const btn = root.querySelector("#wlToggleBtn");
    if (btn && state.data) {
      const wl = window._watchlistVM;
      if (wl) {
        const sym = state.data.code.toLowerCase();
        if (wl.has(sym)) { btn.textContent = "✔ 已加入自选"; btn.classList.add("active"); }
        btn.onclick = () => { wl.toggle({ symbol: sym, name: state.data.name }); };
      }
    } else if (btn) { btn.style.display = "none"; }
  }
  export { render };