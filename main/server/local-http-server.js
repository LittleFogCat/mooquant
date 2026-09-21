/**
 * mookquant · 本地 HTTP 服务（传输层）
 *
 * 只读 GET 服务：把 QuoteService（P2 再加 MarketService）的数据暴露到局域网。
 * 与 main/ipc/index.js 平级 —— 只做路由/校验/包装，不含业务逻辑。
 *
 * 桥是单线程同步循环，本层负责替它挡住并发洪峰：分族并发闸门 + 队列上限 + 单 IP 限流。
 */
const http = require("http");
const os = require("os");
const M = require("./middleware");
const { buildRoutes } = require("./routes");
const { CODE, ApiError } = M;

const API_PREFIX = "/mooquant/api";
const QUEUE_LIMIT = 64;
const DEFAULT_PORT = 12678;
const RATE_DEFAULT = 20; // req/s per IP
const RATE_FULL = 1; // 全推类 req/s per IP

/** 归一化路径：去掉尾部多余的 `/`（`/mooquant/api/` → `/mooquant/api`） */
function normalizePath(p) {
  if (!p) return "/";
  return p.length > 1 ? p.replace(/\/+$/, "") : p;
}

/** 支持 `{name}` 占位符的路径匹配；命中返回 pathParams，未命中返回 null */
function matchPath(pattern, pathname) {
  if (pattern === pathname) return {};
  const p = pattern.split("/");
  const a = pathname.split("/");
  if (p.length !== a.length) return null;
  const out = {};
  for (let i = 0; i < p.length; i++) {
    const seg = p[i];
    if (seg.startsWith("{") && seg.endsWith("}")) {
      if (!a[i]) return null;
      try {
        out[seg.slice(1, -1)] = decodeURIComponent(a[i]);
      } catch (e) {
        return null;
      }
    } else if (seg !== a[i]) {
      return null;
    }
  }
  return out;
}

function matchRoute(routes, pathname) {
  for (const r of routes) {
    const pp = matchPath(r.path, pathname);
    if (pp) return { route: r, pathParams: pp };
  }
  return null;
}

/** 本机可用于局域网访问的 IPv4 地址（排除回环与 link-local） */
function lanAddrs() {
  const out = [];
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const ni of nets[name] || []) {
      if (ni.family !== "IPv4" || ni.internal) continue;
      if (ni.address.startsWith("169.254.")) continue;
      out.push(ni.address);
    }
  }
  return out;
}

class LocalHttpServer {
  constructor({ quoteService, config = {}, version = "" }) {
    this._quoteService = quoteService;
    this._config = config || {};
    this._version = version || "";
    this._server = null;
    this._state = "idle"; // idle | disabled | listening | failed | stopped
    this._error = null;
    this._startedAt = null;
    this._requestCount = 0;
    this._cache = new Map();
    this._gates = null;
    this._limiters = null;
    this._sweepTimer = null;
    this._host = "";
    this._port = 0;
    this._buildRoutes();
  }

  get state() {
    return this._state;
  }

  get listening() {
    return this._state === "listening";
  }

  _buildRoutes() {
    this.routes = buildRoutes({
      quoteService: this._quoteService,
      maxBatchCodes: this._num("maxBatchCodes", 50),
      maxHistoryCount: this._num("maxHistoryCount", 5000),
    });
  }

  _num(key, def) {
    const v = Number(this._config[key]);
    return Number.isFinite(v) && v > 0 ? v : def;
  }

  _sourceMode() {
    return (this._quoteService && this._quoteService.source && this._quoteService.source.mode) || "unknown";
  }

  // ---- 生命周期 ----

  async start() {
    if (this._server) return;
    if (!this._config.enabled) {
      this._state = "disabled";
      console.log("[local-http] 未启用（localServer.enabled=false）");
      return;
    }

    this._gates = {
      snapshot: new M.ConcurrencyGate({ concurrent: 4, queueLimit: QUEUE_LIMIT, name: "snapshot" }),
      history: new M.ConcurrencyGate({ concurrent: 1, queueLimit: QUEUE_LIMIT, name: "history" }),
      full: new M.ConcurrencyGate({ concurrent: 2, queueLimit: QUEUE_LIMIT, name: "full" }),
    };
    this._limiters = {
      default: new M.RateLimiter(RATE_DEFAULT),
      full: new M.RateLimiter(RATE_FULL),
    };

    const envPort = Number(process.env.MOOKQUANT_LOCAL_SERVER_PORT);
    const host = this._config.host || "0.0.0.0";
    const port = Number.isFinite(envPort) && envPort > 0 ? envPort : this._num("port", DEFAULT_PORT);
    this._host = host;
    this._port = port;

    const server = http.createServer((req, res) => this._onRequest(req, res));
    // 及时回收空闲连接 / 防御慢速攻击
    server.keepAliveTimeout = 5000;
    server.headersTimeout = 10000;
    server.requestTimeout = 60000;
    server.on("clientError", (_err, socket) => {
      try {
        socket.destroy();
      } catch (e) {
        /* 客户端异常断开，忽略 */
      }
    });
    this._server = server;

    await new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      server.once("error", (e) => {
        // 端口占用 / 权限不足：只降级本服务，不阻塞应用启动
        this._state = "failed";
        this._error = e.code === "EADDRINUSE" ? `端口 ${port} 已被占用` : `${e.code || "ERROR"}: ${e.message}`;
        console.warn(`[local-http] 启动失败：${this._error}；本地服务降级为不可用，应用其余功能不受影响`);
        this._server = null;
        done();
      });
      server.listen(port, host, () => {
        this._state = "listening";
        this._error = null;
        this._startedAt = Date.now();
        done();
      });
    });

    if (this._state !== "listening") {
      for (const k in this._gates) this._gates[k].dispose();
      return;
    }

    console.log(`[local-http] 已监听 http://${host}:${port}${API_PREFIX}  token=${this._config.token ? "已配置" : "未配置"}  gzip=${this._config.gzip !== false}`);
    for (const a of lanAddrs()) console.log(`[local-http]   → http://${a}:${port}${API_PREFIX}`);
    if (host === "0.0.0.0") {
      console.log("[local-http] 局域网设备若无法访问，请检查 Windows 防火墙是否放行了「专用网络」");
    }
    this._sweepTimer = setInterval(() => {
      for (const k in this._limiters) this._limiters[k].sweep();
    }, 30000);
    if (this._sweepTimer.unref) this._sweepTimer.unref();
  }

  async stop() {
    if (this._sweepTimer) {
      clearInterval(this._sweepTimer);
      this._sweepTimer = null;
    }
    if (this._gates) {
      for (const k in this._gates) this._gates[k].dispose();
      this._gates = null;
    }
    if (this._limiters) {
      for (const k in this._limiters) this._limiters[k].dispose();
      this._limiters = null;
    }
    this._cache.clear();

    const server = this._server;
    this._server = null;
    if (this._state !== "disabled" && this._state !== "failed") this._state = "stopped";
    if (!server) return;

    if (typeof server.closeAllConnections === "function") server.closeAllConnections();
    await new Promise((resolve) => {
      let done = false;
      const fin = () => {
        if (!done) {
          done = true;
          resolve();
        }
      };
      server.close(fin);
      setTimeout(fin, 1000).unref();
    });
  }

  /**
   * 仅更新配置（不重启监听）：用于 discovery 这类不需要重绑端口的字段热更新。
   * 若涉及 enabled / host / port，应改用 restart() —— applyConfig 不会重绑端口。
   */
  applyConfig(config) {
    if (!config) return;
    this._config = config;
    this._buildRoutes();
  }

  /** 配置热更新（重绑监听；会踢掉在线连接并重置 uptime/计数） */
  async restart(config) {
    await this.stop();
    if (config) {
      this._config = config;
      this._buildRoutes();
    }
    await this.start();
  }

  // ---- 对外信息 ----

  /** 服务运行态（不含 endpoints / addrs） */
  serverInfo() {
    return {
      host: this._host || this._config.host || "",
      port: this._port || this._num("port", DEFAULT_PORT),
      startedAt: this._startedAt ? M.isoNow(new Date(this._startedAt)) : null,
      uptimeMs: this._startedAt ? Date.now() - this._startedAt : 0,
      requestCount: this._requestCount,
      listening: this._state === "listening",
      error: this._error,
    };
  }

  /** 服务信息；传入 routes 时附带自描述的 endpoints 列表 */
  info(routes) {
    const base = {
      version: this._version,
      server: this.serverInfo(),
      addrs: this.listening ? lanAddrs() : [],
      source: this._sourceMode(),
      // 只对外暴露「是否需要 token」，token 明文仅在鉴权时内部比较
      tokenRequired: !!this._config.token,
    };
    if (!routes) return base;
    return Object.assign(base, {
      endpoints: routes.map((r) => ({
        path: r.path,
        method: "GET",
        family: r.family,
        desc: r.desc || "",
        stage: r.stage || "",
        params: (r.params || []).map((p) => ({
          name: p.name,
          type: p.type,
          required: !!p.required,
          default: p.default === undefined ? null : p.default,
          desc: p.desc || "",
        })),
      })),
    });
  }

  /** 极简 TTL 缓存（/status 等代价不低的只读结果） */
  async getCached(key, ttlMs, fn) {
    const hit = this._cache.get(key);
    if (hit && Date.now() - hit.ts < ttlMs) return hit.data;
    const data = await fn();
    this._cache.set(key, { data, ts: Date.now() });
    return data;
  }

  // ---- 请求处理 ----

  _onRequest(req, res) {
    const t0 = Date.now();
    const ip = M.clientIp(req);
    this._requestCount++;
    res.on("error", () => {
      /* 客户端提前断开，忽略 */
    });

    this._dispatch(req, res, ip)
      .then((r) => {
        if (!r) return;
        M.logRequest({ ip, method: req.method, url: req.url, status: r.status, code: r.code, ms: Date.now() - t0, source: this._sourceMode() });
      })
      .catch((e) => {
        console.error("[local-http] 未捕获异常:", (e && e.stack) || e);
      });
  }

  async _dispatch(req, res, ip) {
    const origin = M.resolveCorsOrigin(this._config.cors, req.headers.origin);
    const baseHeaders = {
      "X-Mooquant-Source": this._sourceMode(),
      "X-Mooquant-Version": this._version,
    };
    const common = { headers: baseHeaders, gzip: this._config.gzip !== false };

    let pathname = "/";
    try {
      pathname = normalizePath(new URL(req.url, "http://127.0.0.1").pathname);
    } catch (e) {
      return this._send(req, res, CODE.PARAM_INVALID, "非法请求路径", null, common, origin);
    }

    const isApi = pathname === API_PREFIX || pathname.startsWith(API_PREFIX + "/");
    if (!isApi) return this._send(req, res, CODE.NO_ROUTE, "接口不存在", null, common, origin);

    // CORS 预检：不校验 token、不计限流
    if (req.method === "OPTIONS") {
      M.sendPreflight(req, res, origin);
      return { status: 204, code: CODE.OK };
    }
    if (req.method !== "GET") {
      return this._send(req, res, CODE.METHOD_NOT_ALLOWED, "方法不允许（仅支持 GET）", null, common, origin);
    }

    const params = M.parseQuery(req.url);
    if (!M.checkToken(req, params, this._config.token)) {
      return this._send(req, res, CODE.AUTH, "鉴权失败", null, common, origin);
    }

    const hit = matchRoute(this.routes, pathname);
    if (!hit) return this._send(req, res, CODE.NO_ROUTE, "接口不存在", null, common, origin);

    const route = hit.route;
    const limiter = route.gate === "full" ? this._limiters.full : this._limiters.default;
    if (!limiter.allow(ip)) {
      return this._send(req, res, CODE.RATE_LIMIT, "请求过于频繁，请稍后重试", null, common, origin);
    }

    const gate = this._gates[route.gate] || this._gates.snapshot;
    const timeoutMs = route.timeoutMs || (route.gate === "history" ? 30000 : 10000);
    const deadline = Date.now() + timeoutMs;

    const ctx = {
      params,
      pathParams: hit.pathParams,
      server: this,
      quoteService: this._quoteService,
      req,
    };
    const opts = { headers: baseHeaders, cacheControl: route.cache || null, gzip: this._config.gzip !== false };

    let data;
    try {
      data = await gate.run(
        () => M.withTimeout(Promise.resolve().then(() => route.handler(ctx)), Math.max(1, deadline - Date.now()), "处理超时"),
        timeoutMs
      );
    } catch (e) {
      if (e instanceof ApiError) {
        return this._send(req, res, e.code, e.message, e.data, opts, origin);
      }
      // 未预期异常：完整堆栈只写本机日志，不外泄
      console.error(`[local-http] ${route.path} handler 异常:`, (e && e.stack) || e);
      return this._send(req, res, CODE.INTERNAL, "服务内部错误", null, opts, origin);
    }

    return this._send(req, res, CODE.OK, "success", data, opts, origin);
  }

  async _send(req, res, code, msg, data, opts, origin) {
    M.applyCors(res, origin);
    try {
      await M.sendCode(req, res, code, msg, data, opts);
    } catch (e) {
      console.error("[local-http] 响应写出失败:", e.message);
    }
    return { status: opts.status || M.httpStatusFor(code), code };
  }
}

module.exports = { LocalHttpServer, API_PREFIX, lanAddrs };
