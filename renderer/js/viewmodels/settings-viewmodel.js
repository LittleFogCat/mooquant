/**
 * mookquant · Settings ViewModel
 */
class SettingsViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      dataSource: "auto", tradeSource: "mock", loading: false, saved: false, error: null, theme: "dark",
      // 本地服务 / 服务发现
      localServerInfo: null,
      localServerUrls: [],
      discovery: null,
      discoveryBusy: false,
      copyHint: null,
      localServerError: null,
    };
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
      await this.loadLocalServer();
    } catch (e) { this._set({ loading: false, error: e.message }); }
  }

  /** 拉取本地服务 + 信标状态（与 load() 分开，便于切换开关后只刷新这一块） */
  async loadLocalServer() {
    if (!this.facade.localServer) return;
    try {
      const r = await this.facade.localServer.info();
      if (!r || !r.ok) { this._set({ localServerError: (r && r.error) || "本地服务不可用" }); return; }
      this._set({ localServerInfo: r.data, discovery: r.data.discovery, localServerUrls: r.data.urls || [], localServerError: null });
    } catch (e) { this._set({ localServerError: e.message }); }
  }

  /** 信标开关：即时生效，不走 save()（save 会触发「重启生效」弹窗，语义不同） */
  async setDiscovery(enabled) {
    if (this.state.discoveryBusy) return;
    this._set({ discoveryBusy: true, localServerError: null });
    try {
      const r = await this.facade.localServer.setDiscovery({ enabled: !!enabled });
      if (!r || !r.ok) throw new Error((r && r.error) || "切换失败");
      this._set({ localServerInfo: r.data, discovery: r.data.discovery, localServerUrls: r.data.urls || [], discoveryBusy: false });
    } catch (e) { this._set({ discoveryBusy: false, localServerError: e.message }); }
  }

  /** 复制地址：clipboard 优先，execCommand 兜底（Electron 走 file://，一般能用前者） */
  async copyUrl(url) {
    let ok = false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(url);
        ok = true;
      }
    } catch (e) { /* 权限/焦点问题，落到兜底 */ }
    if (!ok) {
      const ta = document.createElement("textarea");
      ta.value = url;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
    }
    if (ok) {
      this._set({ copyHint: url });
      setTimeout(() => { if (this.state.copyHint === url) this._set({ copyHint: null }); }, 1500);
    } else {
      this._set({ localServerError: "复制失败，请手动选中复制" });
    }
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