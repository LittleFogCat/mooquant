/**
 * mookquant · 日志服务
 *
 * 负责 JSON 文件持久化：交易记录、策略执行日志、净值曲线。
 * 所有数据按日期分文件存储在 data/ 目录下。
 */
const path = require("path");
const fs = require("fs");

const DATA_DIR = path.join(__dirname, "..", "..", "data");
const TRADES_DIR = path.join(DATA_DIR, "trades");
const LOGS_DIR = path.join(DATA_DIR, "logs");
const EQUITY_DIR = path.join(DATA_DIR, "equity");

function ensureDirs() {
  for (const dir of [TRADES_DIR, LOGS_DIR, EQUITY_DIR]) {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  }
}

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

/**
 * 追加写入 JSON 数组文件
 */
function appendJsonArray(filePath, item) {
  ensureDirs();
  let arr = [];
  try {
    arr = JSON.parse(fs.readFileSync(filePath, "utf-8"));
  } catch {}
  arr.push(item);
  fs.writeFileSync(filePath, JSON.stringify(arr, null, 2), "utf-8");
}

class LogService {
  /** 记录交易（下单/成交/撤单） */
  logTrade(record) {
    const fp = path.join(TRADES_DIR, todayStr() + ".json");
    appendJsonArray(fp, { ...record, timestamp: new Date().toISOString() });
  }

  /** 记录策略执行 tick */
  logStrategyTick(strategyId, record) {
    const dir = path.join(LOGS_DIR, strategyId);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const fp = path.join(dir, todayStr() + ".json");
    appendJsonArray(fp, { ...record, timestamp: new Date().toISOString() });
  }

  /** 记录每日净值 */
  logEquity(strategyId, equity) {
    const dir = path.join(EQUITY_DIR, strategyId);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const fp = path.join(dir, "equity.json");
    appendJsonArray(fp, {
      date: todayStr(),
      equity: equity,
      timestamp: new Date().toISOString(),
    });
  }

  /** 读取交易记录 */
  readTrades(date) {
    const fp = path.join(TRADES_DIR, (date || todayStr()) + ".json");
    try {
      return JSON.parse(fs.readFileSync(fp, "utf-8"));
    } catch {
      return [];
    }
  }

  /** 读取策略执行日志 */
  readStrategyLogs(strategyId, date) {
    const fp = path.join(LOGS_DIR, strategyId, (date || todayStr()) + ".json");
    try {
      return JSON.parse(fs.readFileSync(fp, "utf-8"));
    } catch {
      return [];
    }
  }

  /** 读取净值曲线 */
  readEquity(strategyId) {
    const fp = path.join(EQUITY_DIR, strategyId, "equity.json");
    try {
      return JSON.parse(fs.readFileSync(fp, "utf-8"));
    } catch {
      return [];
    }
  }
}

module.exports = { LogService };
