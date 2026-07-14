/**
 * mookquant · Trade ViewModel
 *
 * 交易：下单、持仓、委托、账户信息。
 */
class TradeViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      info: null,
      account: null,
      positions: [],
      orders: [],
      orderForm: {
        symbol: "",
        side: "buy",
        price: "",
        quantity: "",
        orderType: "limit",
      },
      loading: false,
      error: null,
    };
    this._subs = [];
  }

  subscribe(fn) {
    this._subs.push(fn);
    fn(this.state);
    return () => { this._subs = this._subs.filter((f) => f !== fn); };
  }

  _set(patch) {
    Object.assign(this.state, patch);
    this._notify();
  }

  _notify() {
    for (const fn of this._subs) fn(this.state);
  }

  async loadAll() {
    this._set({ loading: true, error: null });
    try {
      const [info, account, positions, orders] = await Promise.all([
        this.facade.trade.info(),
        this.facade.trade.getAccount(),
        this.facade.trade.getPositions(),
        this.facade.trade.getOrders(),
      ]);
      this._set({
        info: info.ok ? info.data : null,
        account: account.ok ? account.data : null,
        positions: positions.ok ? positions.data : [],
        orders: orders.ok ? orders.data : [],
        loading: false,
      });
    } catch (e) {
      this._set({ loading: false, error: e.message });
    }
  }

  setOrderField(key, value) {
    this.state.orderForm[key] = value;
    // Silent: no re-render to preserve input focus
  }

  async placeOrder() {
    const f = this.state.orderForm;
    if (!f.symbol.trim()) { this._set({ error: "请输入股票代码" }); return; }
    if (!f.quantity || Number(f.quantity) <= 0) { this._set({ error: "请输入有效数量" }); return; }
    if (f.orderType === "limit" && (!f.price || Number(f.price) <= 0)) {
      this._set({ error: "限价单请输入价格" }); return;
    }

    this._set({ error: null });
    try {
      const order = {
        symbol: f.symbol.trim().toLowerCase(),
        side: f.side,
        price: f.orderType === "limit" ? Number(f.price) : 0,
        quantity: Number(f.quantity),
        orderType: f.orderType,
      };
      const resp = await this.facade.trade.placeOrder(order);
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      // 清空表单
      this._set({
        orderForm: { symbol: "", side: "buy", price: "", quantity: "", orderType: "limit" },
      });
      await this.loadAll();
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  async cancelOrder(orderId) {
    try {
      const resp = await this.facade.trade.cancelOrder(orderId);
      if (!resp.ok) { this._set({ error: resp.error }); return; }
      await this.loadAll();
    } catch (e) {
      this._set({ error: e.message });
    }
  }
}

window.TradeViewModel = TradeViewModel;
