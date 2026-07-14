/**
 * mookquant · QMT 交易数据源
 *
 * 通过 bridge/qmt_server.py 的交易方法进行实盘交易。
 * 需要 miniQMT 客户端运行。
 *
 * 注意：当前 qmt_server.py 尚未实现交易方法，
 * 此类为骨架，连接后会报错并回落到 mock。
 */

const { spawn } = require("child_process");
const path = require("path");
const readline = require("readline");
const fs = require("fs");
const crypto = require("crypto");

class QmtTradeDataSource {
  constructor(options = {}) {
    this.mode = "qmt";
    this.description = "QMT 实盘交易";
    this._options = options;
    this._proc = null;
    this._rl = null;
    this._reqSeq = 0;
    this._pending = new Map();
    this._ready = false;
  }

  init() {
    return this._spawn();
  }

  _spawn() {
    return new Promise((resolve, reject) => {
      const py = process.env.MOOKQUANT_PYTHON || this._options.python || "python";
      const script = this._options.bridgeScript ||
        path.join(__dirname, "..", "..", "..", "bridge", "qmt_server.py");

      if (!fs.existsSync(script)) {
        return reject(new Error("桥接脚本不存在: " + script));
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
        process.stderr.write("[qmt-trade-stderr] " + chunk.toString());
      });

      this._rl = readline.createInterface({ input: proc.stdout });
      this._rl.on("line", (line) => this._onLine(line));

      proc.on("error", (err) => {
        if (!this._ready) reject(err);
      });

      proc.on("exit", (code) => {
        this._ready = false;
        this._proc = null;
        for (const [, p] of this._pending) p.reject(new Error("QMT 交易进程已退出"));
        this._pending.clear();
      });

      this._request("ping", { port: this._options.port || 58610 })
        .then(() => { this._ready = true; resolve(); })
        .catch((e) => reject(new Error("QMT 交易握手失败: " + e.message)));
    });
  }

  _onLine(line) {
    if (!line.trim()) return;
    let msg;
    try { msg = JSON.parse(line); }
    catch { return; }
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
      if (!this._proc) return reject(new Error("QMT 交易未启动"));
      this._pending.set(id, { resolve, reject });
      try {
        this._proc.stdin.write(JSON.stringify({ id, method, params }) + "\n", "utf-8");
      } catch (e) {
        this._pending.delete(id);
        reject(e);
      }
    });
  }

  async placeOrder(order) {
    const port = this._options.port || 58610;
    return await this._request("trade.order", { ...order, port });
  }

  async cancelOrder(orderId) {
    return await this._request("trade.cancel", { orderId });
  }

  async getPositions() {
    return await this._request("trade.positions", {});
  }

  async getOrders() {
    return await this._request("trade.orders", {});
  }

  async getAccount() {
    return await this._request("trade.account", {});
  }

  dispose() {
    if (this._proc) {
      try { this._proc.kill(); } catch {}
      this._proc = null;
    }
    this._pending.clear();
  }
}

module.exports = { QmtTradeDataSource };
