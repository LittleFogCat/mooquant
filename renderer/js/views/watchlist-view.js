/**
 * mookquant · Watchlist View
 */
(function () {
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"'/]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;","/":"&#x2F;" }[c])); }
  function fmt(n, d) { d = d || 2; if (n == null || isNaN(n)) return "-"; return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d }); }
  function cls(n) { if (n > 0) return "up"; if (n < 0) return "down"; return "flat"; }

  function render(root, vm, quoteVM) {
    function paint(state) {
      if (!state.items.length) {
        root.innerHTML = `<div class="empty-state" style="padding:24px"><div class="empty-state-icon">⭐</div><div class="empty-state-text">还没有自选股，查询后点击“加入自选”</div></div>`;
        return;
      }
      const rows = state.items.map(item => `
        <div class="wl-item" data-symbol="${esc(item.symbol)}">
          <div class="wl-info">
            <span class="wl-name">${esc(item.name || item.symbol)}</span>
            <span class="wl-code">${esc(item.symbol)}</span>
          </div>
          <div class="wl-price-block">
            <span class="wl-price ${cls(item.change)}">${item.price ? fmt(item.price) : "--"}</span>
            <span class="wl-change ${cls(item.change)}">${item.changePercent != null ? (item.changePercent >= 0 ? "+" : "") + fmt(item.changePercent) + "%" : ""}</span>
          </div>
          <button class="wl-remove" data-remove="${esc(item.symbol)}">×</button>
        </div>
      `).join("");
      root.innerHTML = `<div class="wl-list">${rows}</div>`;
      root.querySelectorAll(".wl-item").forEach(el => {
        el.addEventListener("click", (e) => {
          if (e.target.dataset.remove) return;
          if (quoteVM) quoteVM.querySymbol(el.dataset.symbol);
        });
      });
      root.querySelectorAll("[data-remove]").forEach(el => {
        el.addEventListener("click", (e) => { e.stopPropagation(); vm.remove(el.dataset.remove); });
      });
    }
    vm.subscribe(paint);
  }
  window.WatchlistView = { render };
})();