/**
 * mookquant · 回测引擎数据源
 *
 * spawn bridge/backtest_engine.py，通过 stdio JSON-RPC 通信。
 */
const { spawn } = require("child_process");
const path = require("path");
const readline = require("readline");
const fs = require("fs");

class BacktestEngine {
  constructor(options = {}) {
    this._options = options;
    this._proc = null;
    this._rl = null;
    this._reqSeq = 0;
    this._pending = new Map();
    this._ready = false;
    // 进度回调：onProgress({progress, stage, detail})，由 Python 推送 backtest.progress 触发
    this._onProgress = options.onProgress || null;
  }

  onProgress(cb) {
    this._onProgress = cb;
  }

  init() {
    if (this._proc) return Promise.resolve();
    return this._spawn();
  }

  _spawn() {
    return new Promise((resolve, reject) => {
      const py = this._options.python || process.env.MOOKQUANT_PYTHON || "python";
      const script = this._options.script ||
        path.join(__dirname, "..", "..", "bridge", "backtest_engine.py");

      if (!fs.existsSync(script)) {
        return reject(new Error("回测引擎脚本不存在: " + script));
      }

      const env = Object.assign({}, process.env, {
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1",
      });

      let proc;
      try {
        proc = spawn(py, [script], { stdio: ["pipe", "pipe", "pipe"], env });
      } catch (e) {
        return reject(new Error("无法启动 Python: " + e.message));
      }
      this._proc = proc;

      proc.stderr.on("data", (chunk) => {
        process.stderr.write("[backtest-stderr] " + chunk.toString());
      });

      this._rl = readline.createInterface({ input: proc.stdout });
      this._rl.on("line", (line) => this._onLine(line));

      proc.on("error", (err) => {
        if (!this._ready) reject(err);
      });

      proc.on("exit", (code) => {
        this._ready = false;
        this._proc = null;
        for (const [, p] of this._pending) p.reject(new Error("回测引擎进程已退出"));
        this._pending.clear();
      });

      // 握手
      this._request("ping")
        .then(() => { this._ready = true; resolve(); })
        .catch((e) => reject(new Error("回测引擎握手失败: " + e.message)));
    });
  }

  _onLine(line) {
    if (!line.trim()) return;
    let msg;
    try { msg = JSON.parse(line); }
    catch { return; }
    // 进度通知（无 id）→ 转发给上层回调，不视为 RPC 响应
    if (msg.id == null && msg.method === "backtest.progress") {
      if (this._onProgress) this._onProgress(msg.params || {});
      return;
    }
    if (msg.id && this._pending.has(msg.id)) {
      const p = this._pending.get(msg.id);
      this._pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error));
      else p.resolve(msg.result);
    }
  }

  _request(method, params = {}) {
    const id = String(++this._reqSeq);
    return new Promise((resolve, reject) => {
      if (!this._proc) return reject(new Error("回测引擎未启动"));
      this._pending.set(id, { resolve, reject });
      try {
        this._proc.stdin.write(JSON.stringify({ id, method, params }) + "\n", "utf-8");
      } catch (e) {
        this._pending.delete(id);
        reject(e);
      }
    });
  }

  async run(config) {
    if (!this._ready) await this.init();
    return await this._request("backtest.run", config);
  }

  // D4.3 回测历史管理：列表 + A/B 对比
  async list(limit = 50) {
    if (!this._ready) await this.init();
    return await this._request("backtest.list", { limit });
  }

  async compare(ids = []) {
    if (!this._ready) await this.init();
    return await this._request("backtest.compare", { ids });
  }

  dispose() {
    if (this._proc) {
      try { this._proc.kill(); } catch {}
      this._proc = null;
    }
    this._pending.clear();
  }
}

module.exports = { BacktestEngine };
