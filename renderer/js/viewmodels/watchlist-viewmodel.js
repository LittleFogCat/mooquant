/**
 * mookquant · Watchlist ViewModel
 */
class WatchlistViewModel {
  constructor(facade) {
    this.facade = facade;
    this._key = "mookquant_watchlist";
    this.state = { items: [], loading: false };
    this._subs = [];
    this._timer = null;
    this._load();
  }
  _load() { try { this.state.items = JSON.parse(localStorage.getItem(this._key) || "[]"); } catch { this.state.items = []; } }
  _save() { localStorage.setItem(this._key, JSON.stringify(this.state.items)); }
  subscribe(fn) { this._subs.push(fn); fn(this.state); return () => { this._subs = this._subs.filter(f => f !== fn); }; }
  _notify() { for (const fn of this._subs) fn(this.state); }
  has(symbol) { return this.state.items.some(i => i.symbol === symbol); }
  add(stock) {
    if (this.has(stock.symbol)) return;
    this.state.items.unshift({ symbol: stock.symbol || stock.code, name: stock.name, addedAt: Date.now() });
    this._save(); this._notify();
    this.refresh();
  }
  remove(symbol) {
    this.state.items = this.state.items.filter(i => i.symbol !== symbol);
    this._save(); this._notify();
  }
  toggle(stock) { if (this.has(stock.symbol || stock.code)) this.remove(stock.symbol || stock.code); else this.add(stock); }
  async refresh() {
    if (!this.state.items.length) return;
    this._set({ loading: true });
    const updated = await Promise.all(this.state.items.map(async (item) => {
      try {
        const r = await this.facade.quote.query(item.symbol);
        if (r.ok && r.data) {
          return { ...item, price: r.data.price, change: r.data.change, changePercent: r.data.changePercent, name: r.data.name };
        }
      } catch {} return item;
    }));
    this.state.items = updated;
    this._set({ loading: false });
  }
  _set(p) { Object.assign(this.state, p); this._notify(); }
  startAutoRefresh() { this._timer = setInterval(() => this.refresh(), 10000); }
  stopAutoRefresh() { if (this._timer) clearInterval(this._timer); }
}
export { WatchlistViewModel };