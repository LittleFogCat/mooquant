/**
 * mookquant · Settings ViewModel
 */
class SettingsViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = { dataSource: "auto", tradeSource: "mock", loading: false, saved: false, error: null, theme: "dark" };
    this._subs = [];
  }
  subscribe(fn) { this._subs.push(fn); fn(this.state); return () => { this._subs = this._subs.filter(f => f !== fn); }; }
  _set(patch) { Object.assign(this.state, patch); this._notify(); }
  _notify() { for (const fn of this._subs) fn(this.state); }
  async load() {
    this._set({ loading: true });
    try {
      const resp = await this.facade.settings.get();
      this._set({ dataSource: resp.dataSource || "auto", tradeSource: resp.tradeSource || "mock", loading: false, theme: localStorage.getItem("mookquant.theme") || "dark" });
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

  /** 设置主题模式（前端 localStorage，无需后端持久化） */
  setTheme(theme) {
    // 临时启用 2s 颜色过渡动画
    var html = document.documentElement;
    html.classList.add("theme-transitioning");
    this.state.theme = theme;
    localStorage.setItem("mookquant.theme", theme);
    html.setAttribute("data-theme", theme);
    this._notify();
    // 过渡结束后移除 class，避免影响日常 hover 等交互动画
    setTimeout(function () { html.classList.remove("theme-transitioning"); }, 2100);
  }

  dismissSaved() {
    this._set({ saved: false });
  }
}
export { SettingsViewModel };