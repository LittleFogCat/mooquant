/**
 * mookquant · Settings ViewModel
 */
class SettingsViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = { dataSource: "auto", tradeSource: "mock", loading: false, saved: false, error: null };
    this._subs = [];
  }
  subscribe(fn) { this._subs.push(fn); fn(this.state); return () => { this._subs = this._subs.filter(f => f !== fn); }; }
  _set(patch) { Object.assign(this.state, patch); this._notify(); }
  _notify() { for (const fn of this._subs) fn(this.state); }
  async load() {
    this._set({ loading: true });
    try {
      const resp = await this.facade.settings.get();
      this._set({ dataSource: resp.dataSource || "auto", tradeSource: resp.tradeSource || "mock", loading: false });
    } catch (e) { this._set({ loading: false, error: e.message }); }
  }
  setField(key, value) {
    this.state[key] = value;
    this.state.saved = false;
    // Silent: no re-render to preserve input focus
  }
  async save() {
    this._set({ loading: true, error: null });
    try {
      const resp = await this.facade.settings.set({ dataSource: this.state.dataSource, tradeSource: this.state.tradeSource });
      this._set({ loading: false, saved: true });
    } catch (e) { this._set({ loading: false, error: e.message }); }
  }

  dismissSaved() {
    this._set({ saved: false });
  }
}
export { SettingsViewModel };