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
  /** 东财妙想模拟盘配置（baseUrl 等；密钥从 .env 读取，不入配置） */
  get eastmoney() { return this._config.eastmoney || {}; }
  /**
   * 无人值守交易配置（时段放行 / 下单节流 / 调度节拍）
   * 缺省值集中在 config/default.json，这里只做类型兜底，避免策略侧拿到 undefined。
   */
  get autoTrade() {
    const a = this._config.autoTrade || {};
    return {
      enabled: a.enabled !== false,
      allowOffHours: a.allowOffHours === true,
      minOrderIntervalMs: Number(a.minOrderIntervalMs) > 0 ? Number(a.minOrderIntervalMs) : 30000,
      tickMs: Number(a.tickMs) > 0 ? Number(a.tickMs) : 15000,
      schedTickMs: Number(a.schedTickMs) > 0 ? Number(a.schedTickMs) : 20000,
      calendarOverrides: a.calendarOverrides || {},
    };
  }
}
module.exports = { ConfigManager };