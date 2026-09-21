/**
 * mookquant · 本地服务 · 传输层中间件
 *
 * envelope / 参数解析校验 / 编解码归一 / CORS / token / 限流 / 超时 / gzip / 日志。
 * 本文件只做传输层的事：不触达 Service，不含业务逻辑。
 */
const zlib = require("zlib");
const crypto = require("crypto");

/** 业务状态码（与 docs/.ai/plan/local_server.md「数据格式」表一致） */
const CODE = {
  OK: 0,
  PARAM_INVALID: 10001, // 参数缺失 / 非法
  PARAM_RANGE: 10002, // 参数越界
  NOT_FOUND: 20001, // 资源不存在
  SOURCE_NOT_READY: 30001, // 数据源未就绪
  UNSUPPORTED: 30002, // 数据源不支持该接口
  UPSTREAM_TIMEOUT: 30003, // 上游超时
  INCOMPLETE: 30004, // 数据不完整
  INTERNAL: 50000, // 服务内部错误
  AUTH: 40001, // 鉴权失败
  RATE_LIMIT: 40002, // 限流 / 队列已满
  NO_ROUTE: 40400, // 接口不存在
  METHOD_NOT_ALLOWED: 40500, // 方法不允许
};

/** 业务码 → HTTP 状态码。多数业务错误仍是 200，只有传输语义的才改状态码。 */
function httpStatusFor(code) {
  if (code === CODE.AUTH) return 401;
  if (code === CODE.RATE_LIMIT) return 429;
  if (code === CODE.NO_ROUTE) return 404;
  if (code === CODE.METHOD_NOT_ALLOWED) return 405;
  return 200;
}

/** 带业务码的异常，handler / 校验器抛出后由服务器统一转成响应 */
class ApiError extends Error {
  constructor(code, msg, data) {
    super(msg);
    this.name = "ApiError";
    this.code = code;
    this.data = data === undefined ? null : data;
  }
}

const badParam = (msg) => new ApiError(CODE.PARAM_INVALID, msg);
const outOfRange = (msg) => new ApiError(CODE.PARAM_RANGE, msg);

// ---- envelope ----

function ok(data) {
  return { code: CODE.OK, msg: "success", data: data === undefined ? null : data };
}

function fail(code, msg, data) {
  return { code, msg: msg || "error", data: data === undefined ? null : data };
}

// ---- 编解码归一 ----

/**
 * 标的代码归一为 UI 格式：`sh600519` / `sz000001` / `bj430047`。
 * 入参兼容 UI 格式与 xtquant 格式（`600519.SH`），出参统一 UI 格式。
 * 与 renderer 的约定一致（`renderer/js/services/facade.js` 用 sh/sz/bj 前缀小写）。
 */
function toUiCode(code) {
  const s = String(code == null ? "" : code).trim();
  if (!s) return "";
  if (s.includes(".")) {
    const idx = s.indexOf(".");
    const head = s.slice(0, idx);
    const market = s.slice(idx + 1).toLowerCase();
    if (market === "sh" || market === "sz" || market === "bj") return market + head;
    return head.toUpperCase(); // 美股等：AAPL.US → AAPL
  }
  const lower = s.toLowerCase();
  if (lower.startsWith("sh") || lower.startsWith("sz") || lower.startsWith("bj")) return lower;
  if (/^\d{6}$/.test(s)) return inferMarket(s) + s;
  return s.toUpperCase();
}

function inferMarket(sixDigits) {
  const f = sixDigits[0];
  if (f === "6" || f === "9" || f === "5") return "sh";
  if (f === "0" || f === "2" || f === "3") return "sz";
  if (f === "8" || f === "4") return "bj";
  return "sh";
}

function isoNow(d) {
  const t = d || new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
}

// ---- 参数解析 ----

/** 解析 query string 为普通对象（重复参数取首个） */
function parseQuery(url) {
  const out = Object.create(null);
  const q = url.indexOf("?");
  if (q < 0) return out;
  const sp = new URLSearchParams(url.slice(q + 1));
  for (const [k, v] of sp) if (!(k in out)) out[k] = v;
  return out;
}

function isBlank(v) {
  return v === undefined || v === null || String(v).trim() === "";
}

/** 文本参数；required 且缺失时报 10001 */
function strParam(params, name, { def = null, required = false, maxLen = 0 } = {}) {
  const raw = params[name];
  if (isBlank(raw)) {
    if (required) throw badParam(`参数 ${name} 必填`);
    return def;
  }
  const v = String(raw).trim();
  if (maxLen > 0 && v.length > maxLen) throw outOfRange(`参数 ${name} 超长（最大 ${maxLen} 字符）`);
  return v;
}

/** 整数参数；非数字报 10001，越界报 10002 */
function intParam(params, name, { def = 0, min = null, max = null } = {}) {
  const raw = params[name];
  if (isBlank(raw)) return def;
  const v = Number(raw);
  if (!Number.isFinite(v) || !Number.isInteger(v)) throw badParam(`参数 ${name} 必须是整数`);
  if (min !== null && v < min) throw outOfRange(`参数 ${name} 不能小于 ${min}`);
  if (max !== null && v > max) throw outOfRange(`参数 ${name} 不能大于 ${max}`);
  return v;
}

/** 0/1 开关参数 */
function flagParam(params, name, def) {
  return intParam(params, name, { def, min: 0, max: 1 });
}

/** 逗号分隔列表；逐项归一化（如标的代码），去空、去重 */
function csvParam(params, name, { def = [], required = false, maxItems = 0, normalize = null } = {}) {
  const raw = params[name];
  if (isBlank(raw)) {
    if (required) throw badParam(`参数 ${name} 必填`);
    return def.slice();
  }
  let items = String(raw)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (normalize) items = items.map(normalize);
  items = Array.from(new Set(items.filter(Boolean)));
  if (items.length === 0) {
    if (required) throw badParam(`参数 ${name} 不能为空`);
    return def.slice();
  }
  if (maxItems > 0 && items.length > maxItems) throw outOfRange(`参数 ${name} 最多 ${maxItems} 个`);
  return items;
}

/** 只接受的枚举值 */
function enumParam(params, name, allowed, def) {
  const v = strParam(params, name, { def: null });
  if (v === null) return def;
  if (!allowed.includes(v)) throw badParam(`参数 ${name} 只接受 ${allowed.join(" / ")}`);
  return v;
}

// ---- 鉴权 ----

/** token 校验：查询参数 ?token= 或请求头 X-Api-Token，任一满足即可。定长比较防时序侧信道。 */
function checkToken(req, params, expected) {
  if (!expected) return true;
  const provided = req.headers["x-api-token"] || (params && params.token) || "";
  const a = Buffer.from(String(provided), "utf8");
  const b = Buffer.from(String(expected), "utf8");
  if (a.length !== b.length) return false;
  try {
    return crypto.timingSafeEqual(a, b);
  } catch (e) {
    return false;
  }
}

// ---- CORS ----

/**
 * 计算应下发的 Access-Control-Allow-Origin。
 * corsCfg 为空 → 不下发；`*` → 通配；逗号分隔白名单 → 命中才回显来源。
 */
function resolveCorsOrigin(corsCfg, reqOrigin) {
  if (!corsCfg) return null;
  if (corsCfg === "*") return "*";
  const list = String(corsCfg)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (!reqOrigin) return null;
  return list.includes(reqOrigin) ? reqOrigin : null;
}

function applyCors(res, origin) {
  if (!origin) return;
  res.setHeader("Access-Control-Allow-Origin", origin);
  res.setHeader("Vary", "Origin");
}

/** CORS 预检响应：不校验 token、不计入限流 */
function sendPreflight(req, res, origin) {
  applyCors(res, origin);
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "X-Api-Token, Content-Type");
  res.setHeader("Access-Control-Max-Age", "600");
  res.writeHead(204);
  res.end();
}

// ---- 响应 ----

const MIN_GZIP_BYTES = 1024;

function gzipAsync(buf) {
  return new Promise((resolve, reject) => {
    zlib.gzip(buf, (err, out) => (err ? reject(err) : resolve(out)));
  });
}

function acceptsGzip(req) {
  return /\bgzip\b/i.test(req.headers["accept-encoding"] || "");
}

/**
 * 统一发送 JSON 响应。
 * opts: { status, cacheControl, headers, gzip }
 */
async function sendJson(req, res, payload, opts = {}) {
  if (res.writableEnded) return;
  const status = opts.status || 200;
  let body = Buffer.from(JSON.stringify(payload), "utf8");
  const headers = Object.assign({ "Content-Type": "application/json; charset=utf-8" }, opts.headers || {});
  if (opts.cacheControl) headers["Cache-Control"] = opts.cacheControl;

  if (opts.gzip !== false && acceptsGzip(req) && body.length >= MIN_GZIP_BYTES) {
    try {
      body = await gzipAsync(body);
      headers["Content-Encoding"] = "gzip";
    } catch (e) {
      console.warn("[local-http] gzip 失败，回退明文:", e.message);
    }
  }

  headers["Content-Length"] = body.length;
  res.writeHead(status, headers);
  res.end(body);
}

/** 发送业务码响应（HTTP 状态码由业务码推导） */
async function sendCode(req, res, code, msg, data, opts = {}) {
  const payload = code === CODE.OK ? ok(data) : fail(code, msg, data);
  const status = opts.status || httpStatusFor(code);
  await sendJson(req, res, payload, Object.assign({}, opts, { status }));
}

// ---- 限流 / 并发 ----

/** 单 IP 令牌桶限流。ratePerSec <= 0 表示不限。 */
class RateLimiter {
  constructor(ratePerSec) {
    this._rate = Number(ratePerSec) || 0;
    this._hits = new Map(); // ip -> { count, windowStart }
  }

  /** @returns {boolean} true = 放行 */
  allow(ip) {
    if (this._rate <= 0) return true;
    const now = Date.now();
    const rec = this._hits.get(ip);
    if (!rec || now - rec.windowStart >= 1000) {
      this._hits.set(ip, { count: 1, windowStart: now });
      return true;
    }
    rec.count++;
    return rec.count <= this._rate;
  }

  /** 顺带回收过期桶，避免 IP 表无限增长 */
  sweep() {
    const now = Date.now();
    for (const [ip, rec] of this._hits) if (now - rec.windowStart >= 5000) this._hits.delete(ip);
  }

  dispose() {
    this._hits.clear();
  }
}

/**
 * 并发闸门：最多 concurrent 个任务同时在跑，超出的进等待队列；
 * 队列长度超过 queueLimit 时立刻拒绝（转 429/40002）。
 *
 * `run()` 的 timeoutMs 覆盖「排队 + 执行」全程；若超时发生在排队阶段，
 * 会把该请求从队列摘除，绝不会等到名额后才空转执行。
 */
class ConcurrencyGate {
  constructor({ concurrent = 1, queueLimit = 64, name = "gate" } = {}) {
    this._concurrent = Math.max(1, concurrent);
    this._queueLimit = Math.max(0, queueLimit);
    this._name = name;
    this._active = 0;
    this._queue = [];
  }

  get active() {
    return this._active;
  }

  get pending() {
    return this._queue.length;
  }

  /**
   * @param {Function} task 拿到名额后执行，返回 Promise
   * @param {number} [timeoutMs] 0 / 空 = 不超时
   */
  async run(task, timeoutMs = 0) {
    const gate = this;
    let timer = null;
    let timedOut = false;
    let entry = null; // 排队中才有值

    const release = await new Promise((resolve, reject) => {
      const grant = () => resolve(gate._releaseHandle());
      if (gate._active < gate._concurrent) {
        gate._active++;
        grant();
        return;
      }
      if (gate._queue.length >= gate._queueLimit) {
        reject(new ApiError(CODE.RATE_LIMIT, "服务繁忙，请稍后重试"));
        return;
      }
      entry = {
        grant: () => {
          entry = null;
          grant();
        },
        reject,
      };
      gate._queue.push(entry);

      if (timeoutMs > 0) {
        timer = setTimeout(() => {
          timedOut = true;
          const i = gate._queue.indexOf(entry);
          if (i >= 0) gate._queue.splice(i, 1); // 排队中超时：摘除，不再占用名额
          entry = null;
          reject(new ApiError(CODE.UPSTREAM_TIMEOUT, "排队超时"));
        }, timeoutMs);
      }
    }).catch((e) => {
      if (timer) clearTimeout(timer);
      throw e;
    });

    if (timer) clearTimeout(timer);
    // 已超时但名额恰好交接过来：立刻归还，不执行任务
    if (timedOut) {
      release();
      throw new ApiError(CODE.UPSTREAM_TIMEOUT, "排队超时");
    }

    try {
      return await task();
    } finally {
      release();
    }
  }

  _releaseHandle() {
    const gate = this;
    let released = false;
    return () => {
      if (released) return;
      released = true;
      const next = gate._queue.shift();
      if (next) next.grant(); // 名额直接交接给队首，_active 不变
      else gate._active--;
    };
  }

  dispose() {
    for (const item of this._queue) item.reject(new ApiError(CODE.INTERNAL, "服务已停止"));
    this._queue.length = 0;
    this._active = 0;
  }
}

/** 给一个 Promise 加超时；超时抛 30003 */
function withTimeout(promise, ms, msg = "上游处理超时") {
  if (!ms || ms <= 0) return promise;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new ApiError(CODE.UPSTREAM_TIMEOUT, msg)), ms);
    promise.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      }
    );
  });
}

// ---- 日志 ----

function clientIp(req) {
  const raw = (req.socket && req.socket.remoteAddress) || "-";
  return raw.startsWith("::ffff:") ? raw.slice(7) : raw;
}

function logRequest({ ip, method, url, status, code, ms, source }) {
  console.log(`[local-http] ${ip} ${method} ${url} -> ${status} code=${code} ${ms}ms source=${source || "-"}`);
}

module.exports = {
  CODE,
  ApiError,
  badParam,
  outOfRange,
  httpStatusFor,
  ok,
  fail,
  toUiCode,
  isoNow,
  parseQuery,
  strParam,
  intParam,
  flagParam,
  csvParam,
  enumParam,
  checkToken,
  resolveCorsOrigin,
  applyCors,
  sendPreflight,
  sendJson,
  sendCode,
  RateLimiter,
  ConcurrencyGate,
  withTimeout,
  clientIp,
  logRequest,
};
