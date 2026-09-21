/**
 * mookquant · Config Manager
 */
const path = require("path");
const fs2 = require("fs");

const CONFIG_PATH = path.join(__dirname, "..", "..", "config", "default.json");

class ConfigManager {
  constructor() { this._config = this._load(); }
  _load() {
    try { return this._normalizeDiscovery(JSON.parse(fs2.readFileSync(CONFIG_PATH, "utf-8"))); }
    catch (e) { console.error("[config] load failed:", e.message); return { dataSource: "mock", tradeSource: "mock" }; }
  }
  /** 补齐 localServer.discovery 缺失键：手改过或旧版本的 default.json 不能让信标读到 undefined */
  _normalizeDiscovery(cfg) {
    const ls = cfg && cfg.localServer;
    if (!ls) return cfg;
    const d = ls.discovery || {};
    ls.discovery = {
      enabled: d.enabled !== false,
      port: Number.isFinite(Number(d.port)) && Number(d.port) > 0 ? Number(d.port) : 12679,
      intervalMs: Number.isFinite(Number(d.intervalMs)) && Number(d.intervalMs) > 0 ? Number(d.intervalMs) : 3000,
      directedBroadcast: d.directedBroadcast !== false,
    };
    return cfg;
  }
  _save() {
    try { fs2.writeFileSync(CONFIG_PATH, JSON.stringify(this._config, null, 2), "utf-8"); }
    catch (e) { console.error("[config] save failed:", e.message); }
  }
  get() { return { ...this._config }; }
  set(patch) { Object.assign(this._config, patch); this._save(); return this.get(); }
  get dataSource() { return this._config.dataSource || "auto"; }
  get tradeSource() { return this._config.tradeSource || "mock"; }
  get qmt() { return this._config.qmt || {}; }
}
module.exports = { ConfigManager };