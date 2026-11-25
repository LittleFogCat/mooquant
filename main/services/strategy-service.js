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

/**
 * 读取 JSON 文件，容忍 UTF-8 BOM
 *
 * 为什么必须处理：Windows 记事本/PowerShell 5.1 的 `Set-Content -Encoding utf8`
 * 都会写 BOM，而 `JSON.parse('\uFEFF{...}')` 直接抛错。策略列表的读取包在 try/catch 里，
 * 结果是这个策略**静默消失**（列表里看不到、执行器也不会恢复它），排查起来非常费劲。
 */
function readJsonLoose(fp) {
  const raw = fs.readFileSync(fp, "utf-8");
  return JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
}

class StrategyService {
  constructor() {
    ensureDir();
  }

  async list() {
    const files = fs.readdirSync(STRATEGY_DIR).filter((f) => f.endsWith(".json"));
    const items = files.map((f) => {
      try {
        return readJsonLoose(path.join(STRATEGY_DIR, f));
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
    return readJsonLoose(fp);
  }

  async create(payload) {
    const now = new Date().toISOString();
    const strategy = {
      id: genId(),
      name: payload.name,
      description: payload.description || "",
      type: payload.type || "custom",
      params: payload.params || {},
      modelId: payload.modelId || "",
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
      modelId: payload.modelId ?? existing.modelId ?? "",
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

module.exports = { StrategyService, readJsonLoose, STRATEGY_DIR };
