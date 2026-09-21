/**
 * mookquant · 本地服务 · LAN 服务发现信标（UDP）
 *
 * 与 local-http-server.js 同为传输层：只负责「把自己正在监听的地址喊出去」，
 * 不含业务逻辑、不触达 Service。纯 Node `dgram`，零新依赖。
 *
 * 两种获知途径：
 *   1. 周期广播——客户端被动等，最多等一个 intervalMs；
 *   2. 探测应答——客户端主动发一个探包，本机单播回给它（无需监听固定端口）。
 *
 * 只通过注入的 getInfo() 读取 HTTP 层的监听状态（端口/地址/数据源），
 * 因此广播出去的永远是**实际绑定端口**（含 MOOKQUANT_LOCAL_SERVER_PORT 覆盖），
 * 而不是配置文件里的值。
 */
const dgram = require("dgram");
const os = require("os");
const crypto = require("crypto");
const { API_PREFIX } = require("./local-http-server");

const MAGIC = "MOOQUANT-DISCOVERY";
const PROTO = 1;
const DEFAULT_PORT = 12679; // = HTTP 默认 12678 + 1
const DEFAULT_INTERVAL_MS = 3000;
const MIN_INTERVAL_MS = 1000;
const MAX_INTERVAL_MS = 60000;
const MAX_INBOUND_BYTES = 512; // 防放大
const REPLY_MIN_GAP_MS = 100; // 同 IP 应答最小间隔
const MAX_ADDRS = 8; // 限制报文长度，避免 IP 分片

// ---- IPv4 网段工具（定向广播地址计算）----

function ipToInt(s) {
  const p = String(s).split(".");
  if (p.length !== 4) return null;
  let n = 0;
  for (const seg of p) {
    const v = Number(seg);
    if (!Number.isInteger(v) || v < 0 || v > 255) return null;
    n = ((n << 8) | v) >>> 0;
  }
  return n;
}

function intToIp(n) {
  return [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join(".");
}

/** 定向广播地址 = (ip & mask) | ~mask，如 192.168.1.5/24 → 192.168.1.255 */
function directedBroadcasts() {
  const out = [];
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const ni of nets[name] || []) {
      if (ni.family !== "IPv4" || ni.internal) continue;
      if (ni.address.startsWith("169.254.")) continue;
      const ip = ipToInt(ni.address);
      const mask = ipToInt(ni.netmask);
      if (ip === null || mask === null) continue;
      const bcast = intToIp((((ip & mask) | ~mask) >>> 0));
      if (bcast !== ni.address && !out.includes(bcast)) out.push(bcast);
    }
  }
  return out;
}

class DiscoveryBeacon {
  constructor({ getInfo, config = {}, version = "" }) {
    this._getInfo = typeof getInfo === "function" ? getInfo : () => null;
    this._config = config || {};
    this._version = version || "";
    this._sock = null;
    this._state = "idle"; // idle | disabled | listening | failed | stopped
    this._error = null;
    this._port = 0;
    this._intervalMs = 0;
    this._broadcastOk = null; // null = 尚未尝试；setBroadcast 成功与否
    this._timer = null;
    this._sweepTimer = null;
    this._replyGate = new Map(); // ip -> 上次应答时间戳
    // 识别自己的广播回声（广播会回到本机 socket）
    this._sid = crypto.randomBytes(6).toString("hex");
  }

  get state() {
    return this._state;
  }

  get listening() {
    return this._state === "listening";
  }

  _num(key, def) {
    const v = Number(this._config[key]);
    return Number.isFinite(v) && v > 0 ? v : def;
  }

  // ---- 生命周期 ----

  /**
   * 启动信标。**永不 reject**：UDP 起不来只降级服务发现，
   * 不能因为发现功能不可用而拖垮应用启动（调用方 main.js 的包装器会 rethrow）。
   */
  async start() {
    if (this._sock) return;

    if (this._config.enabled !== true) {
      this._state = "disabled";
      console.log("[discovery] 未启用（localServer.discovery.enabled=false）");
      return;
    }

    const base = this._getInfo() || {};
    const server = base.server || {};
    if (!server.listening) {
      this._state = "failed";
      this._error = "HTTP 服务未监听，不广播";
      console.warn("[discovery] 已跳过：HTTP 服务未监听（广播一个不可达地址没有意义）");
      return;
    }
    if (!(base.addrs || []).length) {
      this._state = "failed";
      this._error = "未检测到局域网 IPv4 地址";
      console.warn("[discovery] 已跳过：未检测到局域网 IPv4 地址");
      return;
    }

    const port = this._num("port", DEFAULT_PORT);
    this._port = port;
    this._intervalMs = Math.min(MAX_INTERVAL_MS, Math.max(MIN_INTERVAL_MS, this._num("intervalMs", DEFAULT_INTERVAL_MS)));
    this._broadcastOk = null;
    this._error = null;

    const sock = dgram.createSocket({ type: "udp4", reuseAddr: true });
    this._sock = sock;
    sock.on("message", (buf, rinfo) => this._onMessage(buf, rinfo));
    // 运行期 socket 错误：记日志即可，已建立的 HTTP 服务不受影响
    sock.on("error", (e) => console.warn(`[discovery] socket 错误：${(e && (e.code || e.message)) || e}`));

    await new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      sock.once("error", (e) => {
        // 端口占用 / 权限不足：只降级本服务
        this._state = "failed";
        this._error = e.code === "EADDRINUSE" ? `发现端口 ${port} 已被占用` : `${e.code || "ERROR"}: ${e.message}`;
        console.warn(`[discovery] 启动失败：${this._error}；仅服务发现不可用，HTTP 服务与其余功能不受影响`);
        this._sock = null;
        try {
          sock.close();
        } catch (_) {
          /* 绑定就失败，close 可能抛，忽略 */
        }
        done();
      });
      sock.bind(port, "0.0.0.0", () => {
        // 必须在 bind 回调里调用：未 bound 时 setBroadcast 会抛 ERR_SOCKET_DGRAM_NOT_RUNNING
        try {
          sock.setBroadcast(true);
          this._broadcastOk = true;
        } catch (e) {
          // 系统策略/无可用网卡时可能拒绝。不中止：探测应答是单播，不依赖 SO_BROADCAST
          this._broadcastOk = false;
          this._error = `setBroadcast 失败（${(e && (e.code || e.message)) || e}）：仅探测应答可用`;
          console.warn(`[discovery] ${this._error}`);
        }
        this._state = "listening";
        done();
      });
    });

    if (this._state !== "listening") return;

    if (sock.unref) sock.unref(); // Electron 主进程由 app 保活，socket 不必保活
    this._timer = setInterval(() => this._broadcast(), this._intervalMs);
    if (this._timer.unref) this._timer.unref();
    this._sweepTimer = setInterval(() => this._sweep(), 30000);
    if (this._sweepTimer.unref) this._sweepTimer.unref();

    this._broadcast(); // 立即喊一次，客户端不必等一个周期

    console.log(`[discovery] UDP 信标已监听 *:${port}，广播间隔 ${this._intervalMs}ms`);
    for (const t of this._targets()) console.log(`[discovery]   → ${t}:${port}`);
    console.log(`[discovery] 客户端探测：向 255.255.255.255:${port} 发送 {"magic":"${MAGIC}","v":${PROTO},"probe":true}`);
    if (this._broadcastOk === false) {
      console.log("[discovery] 广播被系统阻止，当前仅响应探测（单播）；请检查防火墙是否放行「专用网络」");
    }
  }

  /** 停止信标并释放 UDP 端口。同样不抛。 */
  async stop() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    if (this._sweepTimer) {
      clearInterval(this._sweepTimer);
      this._sweepTimer = null;
    }
    this._replyGate.clear();

    const sock = this._sock;
    this._sock = null;
    if (this._state !== "disabled" && this._state !== "failed") this._state = "stopped";
    if (!sock) return;

    await new Promise((resolve) => {
      let done = false;
      const fin = () => {
        if (!done) {
          done = true;
          resolve();
        }
      };
      try {
        sock.close(fin);
      } catch (e) {
        fin();
      }
      setTimeout(fin, 500).unref();
    });
  }

  /** 配置热更新：仅重启信标本身，不影响 HTTP 监听 */
  async restart(config) {
    await this.stop();
    if (config) this._config = config;
    await this.start();
  }

  // ---- 对外信息 ----

  info() {
    return {
      state: this._state,
      listening: this._state === "listening",
      port: this._port || this._num("port", DEFAULT_PORT),
      intervalMs: this._intervalMs || this._num("intervalMs", DEFAULT_INTERVAL_MS),
      broadcastOk: this._broadcastOk,
      targets: this._state === "listening" ? this._targets() : [],
      error: this._error,
      magic: MAGIC,
      protocolVersion: PROTO,
    };
  }

  // ---- 报文 ----

  /** 广播目标：受限广播 + 各网卡定向广播（多网卡机器只发受限广播时另一个网段收不到） */
  _targets() {
    const out = ["255.255.255.255"];
    if (this._config.directedBroadcast !== false) {
      for (const a of directedBroadcasts()) if (!out.includes(a)) out.push(a);
    }
    return out;
  }

  _payload(isProbe) {
    const base = this._getInfo() || {};
    const server = base.server || {};
    const addrs = (base.addrs || []).slice(0, MAX_ADDRS);
    const port = server.port || this._port;
    const urls = addrs.map((a) => `http://${a}:${port}${API_PREFIX}`);
    return Buffer.from(
      JSON.stringify({
        magic: MAGIC,
        v: PROTO,
        probe: !!isProbe,
        sid: this._sid,
        name: "mookquant",
        appVersion: this._version,
        hostname: os.hostname(),
        host: server.host || "0.0.0.0",
        port,
        prefix: API_PREFIX,
        addrs,
        urls,
        source: base.source || "unknown",
        // 只暴露「是否需要 token」，绝不把 token 明文丢到未认证的广播通道上
        tokenRequired: !!base.tokenRequired,
        ts: new Date().toISOString(),
      }),
      "utf8"
    );
  }

  _broadcast() {
    if (this._state !== "listening" || !this._sock || !this._broadcastOk) return;
    const buf = this._payload(false);
    for (const t of this._targets()) {
      try {
        this._sock.send(buf, this._port, t, () => {});
      } catch (e) {
        /* 单个目标发失败不影响其余（如某网卡已断开） */
      }
    }
  }

  /** 探包处理：只应答「探测」，且严格限速，避免被当成放大反射器 */
  _onMessage(buf, rinfo) {
    if (!buf || buf.length > MAX_INBOUND_BYTES) return;
    let msg;
    try {
      msg = JSON.parse(buf.toString("utf8"));
    } catch (e) {
      return;
    }
    if (!msg || msg.magic !== MAGIC) return;
    if (msg.sid && msg.sid === this._sid) return; // 自己的广播回声
    if (msg.probe !== true) return;
    if (!this._allowReply(rinfo.address)) return;
    try {
      this._sock.send(this._payload(true), rinfo.port, rinfo.address, () => {});
    } catch (e) {
      /* 单播失败只丢这一条 */
    }
  }

  _allowReply(ip) {
    const now = Date.now();
    const last = this._replyGate.get(ip);
    if (last !== undefined && now - last < REPLY_MIN_GAP_MS) return false;
    this._replyGate.set(ip, now);
    return true;
  }

  _sweep() {
    const now = Date.now();
    for (const [ip, ts] of this._replyGate) if (now - ts >= 5000) this._replyGate.delete(ip);
  }
}

module.exports = {
  DiscoveryBeacon,
  DISCOVERY_MAGIC: MAGIC,
  DISCOVERY_PROTOCOL_VERSION: PROTO,
  DISCOVERY_DEFAULT_PORT: DEFAULT_PORT,
  directedBroadcasts,
};
