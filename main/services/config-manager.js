/**
 * mookquant · Config Manager
 */
const path = require("path");
const fs2 = require("fs");

const CONFIG_PATH = path.join(__dirname, "..", "..", "config", "default.json");

class ConfigManager {
  constructor() { this._config = this._load(); }
  _load() {
    try { return JSON.parse(fs2.readFileSync(CONFIG_PATH, "utf-8")); }
    catch (e) { console.error("[config] load failed:", e.message); return { dataSource: "mock", tradeSource: "mock" }; }
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