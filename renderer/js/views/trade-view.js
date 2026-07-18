import * as StockSearch from './stock-search.js';

function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"'\/]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "/": "&#x2F;",
    }[c]));
  }

  function fmtNum(n, d = 2) {
    if (n == null || isNaN(n)) return "-";
    return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
  }

  function cls(n) {
    if (n > 0) return "up";
    if (n < 0) return "down";
    return "flat";
  }

  function renderOrderForm(state, vm) {
    const f = state.orderForm;
    return `
      <div class="card" style="position:relative;z-index:10">
        <div class="card-title">下单</div>
        ${state.error ? '<div class="status error" style="margin-bottom:16px">' + escapeHtml(state.error) + "</div>" : ""}
        <div class="form-row">
          <div class="form-group" style="flex:2">
            <label class="form-label">股票代码</label>
            <input class="input-field" id="td_symbol" value="${escapeHtml(f.symbol)}" placeholder="输入代码/名称/拼音，如：sh600519 / 茅台 / zs" autocomplete="off" />
          </div>
          <div class="form-group">
            <label class="form-label">方向</label>
            <select class="input-field" id="td_side">
              <option value="buy" ${f.side === "buy" ? "selected" : ""}>买入</option>
              <option value="sell" ${f.side === "sell" ? "selected" : ""}>卖出</option>
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">类型</label>
            <select class="input-field" id="td_type">
              <option value="limit" ${f.orderType === "limit" ? "selected" : ""}>限价</option>
              <option value="market" ${f.orderType === "market" ? "selected" : ""}>市价</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">价格</label>
            <input class="input-field" type="number" step="0.01" id="td_price" value="${escapeHtml(f.price)}" placeholder="市价单留空" ${f.orderType === "market" ? "disabled" : ""} />
          </div>
          <div class="form-group">
            <label class="form-label">数量（股）</label>
            <input class="input-field" type="number" step="100" id="td_qty" value="${escapeHtml(f.quantity)}" placeholder="100 的整数倍" />
          </div>
        </div>
        <button class="btn btn-primary" id="td_submit">提交订单</button>
      </div>
    `;
  }

  function renderAccount(state) {
    const a = state.account;
    if (!a) return "";
    return `
      <div class="card">
        <div class="card-title">账户</div>
        ${state.info ? '<div style="margin-bottom:12px"><span class="badge badge-draft">' + escapeHtml(state.info.description || state.info.mode) + "</span></div>" : ""}
        <div class="stats-grid">
          <div class="stat-card"><div class="stat-label">总资产</div><div class="stat-value">${fmtNum(a.totalAssets, 0)}</div></div>
          <div class="stat-card"><div class="stat-label">可用资金</div><div class="stat-value">${fmtNum(a.available, 0)}</div></div>
          <div class="stat-card"><div class="stat-label">持仓市值</div><div class="stat-value">${fmtNum(a.marketValue, 0)}</div></div>
          <div class="stat-card"><div class="stat-label">盈亏</div><div class="stat-value ${cls(a.totalPnl)}">${fmtNum(a.totalPnl, 0)}</div></div>
        </div>
      </div>
    `;
  }

  function renderPositions(state) {
    const positions = state.positions || [];
    if (!positions.length) return '<div class="card"><div class="card-title">持仓</div><div class="empty-state"><div class="empty-state-text">无持仓</div></div></div>';
    const rows = positions.map((p) => `
      <tr>
        <td>${escapeHtml(p.symbol)}</td>
        <td>${escapeHtml(p.name || "-")}</td>
        <td class="num">${p.quantity}</td>
        <td class="num">${fmtNum(p.costPrice)}</td>
        <td class="num">${fmtNum(p.currentPrice)}</td>
        <td class="num">${fmtNum(p.marketValue, 0)}</td>
        <td class="${cls(p.pnl)} num">${fmtNum(p.pnl, 0)}</td>
        <td class="${cls(p.pnlPct)} num">${p.pnlPct != null ? fmtNum(p.pnlPct) + "%" : "-"}</td>
      </tr>
    `).join("");
    return `
      <div class="card">
        <div class="card-title">持仓</div>
        <table class="data-table">
          <thead><tr><th>代码</th><th>名称</th><th class="num">持仓</th><th class="num">成本</th><th class="num">现价</th><th class="num">市值</th><th class="num">盈亏</th><th class="num">盈亏%</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function renderOrders(state, vm) {
    const orders = state.orders || [];
    if (!orders.length) return '<div class="card"><div class="card-title">委托</div><div class="empty-state"><div class="empty-state-text">无委托记录</div></div></div>';
    const rows = orders.slice(0, 30).map((o) => `
      <tr>
        <td>${escapeHtml(o.createdAt || "-")}</td>
        <td><span class="badge badge-${o.side === "buy" ? "buy" : "sell"}">${o.side === "buy" ? "买入" : "卖出"}</span></td>
        <td>${escapeHtml(o.symbol)}</td>
        <td class="num">${o.orderType === "market" ? "市价" : fmtNum(o.price)}</td>
        <td class="num">${o.quantity}</td>
        <td><span class="badge badge-${o.status}">${escapeHtml(o.status)}</span></td>
        <td>${o.status === "pending" ? '<button class="btn btn-danger btn-sm" data-cancel="' + escapeHtml(o.orderId) + '">撤单</button>' : ""}</td>
      </tr>
    `).join("");
    return `
      <div class="card">
        <div class="card-title">委托记录</div>
        <table class="data-table">
          <thead><tr><th>时间</th><th>方向</th><th>代码</th><th class="num">价格</th><th class="num">数量</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  function render(root, vm) {
    var searchCtrl = null;
    function paint(state) {
      root.innerHTML =
        renderOrderForm(state, vm) +
        renderAccount(state) +
        renderPositions(state) +
        renderOrders(state, vm);

      // 绑定表单
      const fieldMap = {
        "td_symbol": { key: "symbol", prop: "value" },
        "td_side": { key: "side", prop: "value" },
        "td_type": { key: "orderType", prop: "value" },
        "td_price": { key: "price", prop: "value" },
        "td_qty": { key: "quantity", prop: "value" },
      };
      for (const [elId, { key, prop }] of Object.entries(fieldMap)) {
        const el = document.getElementById(elId);
        if (el) {
          el.addEventListener("input", () => vm.setOrderField(key, el[prop]));
          el.addEventListener("change", () => {
            vm.setOrderField(key, el[prop]);
            if (key === "orderType") {
              const priceEl = document.getElementById("td_price");
              if (priceEl) priceEl.disabled = el.value === "market";
            }
          });
        }
      }
      const submitBtn = document.getElementById("td_submit");
      if (submitBtn) submitBtn.addEventListener("click", () => vm.placeOrder());

      root.querySelectorAll("[data-cancel]").forEach((el) => {
        el.addEventListener("click", () => vm.cancelOrder(el.dataset.cancel));
      });

      // 股票代码搜索候选 (reuses StockSearch component, self-contained)
      var symbolInput = root.querySelector("#td_symbol");
      if (symbolInput) {
        if (searchCtrl) searchCtrl.destroy();
        searchCtrl = StockSearch.mount(symbolInput, vm.facade, function (stock) {
          vm.setOrderField("symbol", stock.code);
        }, function (val) {
          vm.setOrderField("symbol", val);
        });
      }
    }

    vm.subscribe(paint);
  }

  export { render };