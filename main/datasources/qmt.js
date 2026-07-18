/**
 * mookquant · QMT 数据源
 *
 * 职责：
 *  1. spawn bridge/qmt_server.py（包含 UTF-8 stdout 修复）
 *  2. 用 stdio JSON-RPC 与之通信
 *  3. 把渲染层输入的 sh600519 等转换为 600519.SH
 *  4. 把 qmt_server 返回的字段映射到 UI 模型
 *
 * 失败处理：
 *  - spawn 失败 / ping 不通 → 上抛异常，让 main/datasources/index.js 的 auto 模式回落到 mock
 */

const { spawn } = require("child_process");
const path = require("path");
const readline = require("readline");
const fs = require("fs");

class QmtDataSource {
  constructor(options = {}) {
    this.mode = "qmt";
    this.description = "国金 QMT · 迅投 xtquant";
    this._options = options;
    this._proc = null;
    this._rl = null;
    this._reqSeq = 0;
    this._pending = new Map();
    this._ready = false;
    this._initPromise = null;
    this._exitHandlers = [];
    this._pushHandlers = {};  // push type -> [handler]
    this._lastError = null;
  }

  init() {
    if (this._initPromise) return this._initPromise;
    this._initPromise = this._spawn();
    return this._initPromise;
  }

  /**
   * spawn Python 子进程并建立握手
   */
  _spawn() {
    return new Promise((resolve, reject) => {
      const opts = this._options || {};
      const py = process.env.MOOKQUANT_PYTHON || opts.python || "python";
      // 优先使用同目录的脚本；缺省走 bridge/qmt_server.py
      const script = opts.bridgeScript ||
        path.join(__dirname, "..", "..", "bridge", "qmt_server.py");

      if (!fs.existsSync(script)) {
        return reject(new Error("桥接脚本不存在: " + script));
      }

      // 把主进程的 stdio 设定透传，并设置子进程的 UTF-8 环境
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

      let stderrBuf = "";
      proc.stderr.on("data", (chunk) => {
        const s = chunk.toString();
        stderrBuf += s;
        // 把 stderr 写到 Node 控制台以便调试
        process.stderr.write("[qmt-stderr] " + s);
      });

      this._rl = readline.createInterface({ input: proc.stdout });
      this._rl.on("line", (line) => this._onLine(line));

      proc.on("error", (err) => {
        this._lastError = err.message;
        if (!this._ready) reject(err);
      });

      proc.on("exit", (code, signal) => {
        const msg = `[qmt] 子进程退出 code=${code} signal=${signal}`;
        process.stderr.write(msg + "\n");
        this._ready = false;
        this._proc = null;
        // 拒绝所有等待中的请求
        for (const [, p] of this._pending) p.reject(new Error("QMT 桥进程已退出"));
        this._pending.clear();
        for (const h of this._exitHandlers) try { h(msg); } catch {}
      });

      // 握手：发一个 ping，超时 4 秒
      this._request("ping", { port: opts.port || 58610 })
        .then((r) => {
          this._ready = true;
          if (!r.connected) {
            // ping 通但 miniQMT 没接上
            this._lastError = "miniQMT 未连接：仅浏览器/Echo 模式可用";
            // 不立刻 reject，让 getQuote 真正调用时报错
          }
          resolve();
        })
        .catch((e) => {
          if (!stderrBuf) stderrBuf = e.message;
          reject(new Error("握手失败: " + (stderrBuf || e.message)));
        });
    });
  }

  _onLine(line) {
    if (!line.trim()) return;
    let msg;
    try { msg = JSON.parse(line); }
    catch (e) {
      process.stderr.write("[qmt] 无法解析 JSON: " + line + "\n");
      return;
    }
    // 识别推送消息（无 id，有 push 字段）
    if (msg.push && !msg.id) {
      this._onPush(msg.push, msg.data);
      return;
    }
    // 普通 RPC 响应
    if (msg.id && this._pending.has(msg.id)) {
      const p = this._pending.get(msg.id);
      this._pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error));
      else p.resolve(msg.result);
    }
  }

  _onPush(type, data) {
    const handlers = this._pushHandlers[type] || [];
    for (const h of handlers) {
      try { h(data); } catch (e) {
        process.stderr.write("[qmt] push handler error: " + e.message + "\n");
      }
    }
  }

  /** 注册推送事件处理器 */
  onPush(type, handler) {
    if (!this._pushHandlers[type]) this._pushHandlers[type] = [];
    this._pushHandlers[type].push(handler);
  }

  _send(obj) {
    const line = JSON.stringify(obj) + "\n";
    try {
      this._proc.stdin.write(line, "utf-8");
    } catch (e) {
      throw new Error("写入 QMT 桥失败: " + e.message);
    }
  }

  _request(method, params = {}) {
    const id = String(++this._reqSeq);
    return new Promise((resolve, reject) => {
      if (!this._proc) return reject(new Error("QMT 桥未启动"));
      this._pending.set(id, { resolve, reject });
      try {
        this._send({ id, method, params });
      } catch (e) {
        this._pending.delete(id);
        reject(e);
      }
    });
  }

  /**
   * 对外：查询快照
   * @param {string} rawSymbol  "sh600519"/"sz000001"/"AAPL"
   * @returns {Promise<object>}
   */
  async getQuote(rawSymbol) {
    if (!this._ready) await this.init();

    const port = this._options.port || 58610;
    const result = await this._request("quote.snapshot", {
      code: rawSymbol,
      port,
    });

    // qmt_server 已经做了字段映射，这里直接返回
    return result;
  }

  async getHistory(rawSymbol, period = "1d", count = 30, dividendType = "front") {
    if (!this._ready) await this.init();
    const port = this._options.port || 58610;
    return await this._request("quote.history", {
      code: rawSymbol, period, count, port, dividend_type: dividendType,
    });
  }

  async subscribe(rawSymbol, period = "tick") {
    if (!this._ready) await this.init();
    const port = this._options.port || 58610;
    return await this._request("quote.subscribe", {
      code: rawSymbol, period, port,
    });
  }

  /** 在桥子进程退出时注册回调 */

  /**
   * 同步全量 A 股列表到数据库（从 xtquant 拉取）
   * @returns {Promise<{count:number, stocks:Array, total:number}>}
   */
  async syncStocks() {
    if (!this._ready) await this.init();
    const port = this._options.port || 58610;
    return await this._request("stock.sync", { port });
  }

  /**
   * 从数据库读取全部股票列表
   * @returns {Promise<{count:number, stocks:Array}>}
   */
  async getStockList() {
    if (!this._ready) await this.init();
    const port = this._options.port || 58610;
    return await this._request("stock.list", { port });
  }

  /**
   * 列出所有已注册策略的元数据（供 UI 渲染参数表单）
   */
  async strategyList() {
    if (!this._ready) await this.init();
    return await this._request("strategy.list", {});
  }

  /**
   * 实盘信号计算：给定策略类型 + K线 + 参数，返回最新一根 bar 的信号。
   * 回测与实盘共用同一份策略代码（bridge/strategies/）。
   */
  async strategySignal({ type, bars, params, symbol }) {
    if (!this._ready) await this.init();
    return await this._request("strategy.signal", { type, bars, params, symbol: symbol || "" });
  }

  /**
   * 导出策略为目标平台脚本（如 QMT 单文件）
   */
  async strategyExport({ type, platform, params }) {
    if (!this._ready) await this.init();
    return await this._request("strategy.export", { type, platform: platform || "qmt", params });
  }

  onExit(fn) {
    this._exitHandlers.push(fn);
  }

  dispose() {
    this._exitHandlers = [];
    this._pushHandlers = {};  // push type -> [handler]
    if (this._proc) {
      try { this._proc.kill(); } catch {}
      this._proc = null;
    }
    this._rl = null;
    this._pending.clear();
  }
}

module.exports = { QmtDataSource };