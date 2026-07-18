/**
 * mookquant · Strategy Service（主进程）
 *
 * 策略管理：CRUD + JSON 文件持久化到 data/strategies/。
 */
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const STRATEGY_DIR = path.join(__dirname, "..", "..", "data", "strategies");

function ensureDir() {
  if (!fs.existsSync(STRATEGY_DIR)) {
    fs.mkdirSync(STRATEGY_DIR, { recursive: true });
  }
}

function genId() {
  return "st_" + Date.now().toString(36) + "_" + crypto.randomBytes(3).toString("hex");
}

function filePath(id) {
  return path.join(STRATEGY_DIR, id + ".json");
}

class StrategyService {
  constructor() {
    ensureDir();
  }

  async list() {
    const files = fs.readdirSync(STRATEGY_DIR).filter((f) => f.endsWith(".json"));
    const items = files.map((f) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(STRATEGY_DIR, f), "utf-8"));
      } catch {
        return null;
      }
    }).filter(Boolean);
    items.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
    return items;
  }

  async getById(id) {
    const fp = filePath(id);
    if (!fs.existsSync(fp)) return null;
    return JSON.parse(fs.readFileSync(fp, "utf-8"));
  }

  async create(payload) {
    const now = new Date().toISOString();
    const strategy = {
      id: genId(),
      name: payload.name,
      description: payload.description || "",
      type: payload.type || "custom",
      params: payload.params || {},
      symbols: payload.symbols || [],
      schedule: payload.schedule || {},
      risk: payload.risk || {},
      runtime: payload.runtime || {},
      status: payload.status || "draft",
      createdAt: now,
      updatedAt: now,
    };
    fs.writeFileSync(filePath(strategy.id), JSON.stringify(strategy, null, 2), "utf-8");
    return strategy;
  }

  async update(id, payload) {
    const existing = await this.getById(id);
    if (!existing) throw new Error("策略不存在: " + id);
    const updated = {
      ...existing,
      name: payload.name ?? existing.name,
      description: payload.description ?? existing.description,
      type: payload.type ?? existing.type,
      params: payload.params ?? existing.params,
      symbols: payload.symbols ?? existing.symbols,
      schedule: payload.schedule ?? existing.schedule,
      risk: payload.risk ?? existing.risk,
      runtime: payload.runtime ?? existing.runtime,
      status: payload.status ?? existing.status,
      updatedAt: new Date().toISOString(),
    };
    fs.writeFileSync(filePath(id), JSON.stringify(updated, null, 2), "utf-8");
    return updated;
  }

  async remove(id) {
    const fp = filePath(id);
    if (fs.existsSync(fp)) fs.unlinkSync(fp);
    return { id };
  }
}

module.exports = { StrategyService };
