/**
 * mookquant · 策略调度规则（纯函数，可单测）
 *
 * 解决的问题：执行器原来对所有策略统一 60s 轮询，日线策略一天算 1440 次信号，
 * 既浪费算力也浪费接口额度。这里把「策略想按什么周期出信号」显式表达出来。
 *
 * 三种调度类型：
 *  - daily   ：period="1d"，按 schedule.at 的固定时点触发（缺省 09:31 / 14:55）
 *  - bar     ：period="1m|5m|15m|30m|60m"，对应 bar **收盘后 5 秒**触发一次
 *  - interval：未配置 period，沿用 schedule.intervalMs（兼容旧策略）
 *
 * 统一用 firedKeys 去重：key 形如 `2026-11-25#09:31`，一天内同一时点只算一次。
 */

/** A 股连续竞价时段（秒） */
const SESSIONS = [
  { start: 9 * 3600 + 30 * 60, end: 11 * 3600 + 30 * 60 },
  { start: 13 * 3600, end: 15 * 3600 },
];

/** period → bar 长度（秒） */
const PERIOD_BAR_SEC = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "60m": 3600,
  "1h": 3600,
};

/** 日线策略缺省信号点：开盘 1 分钟后 + 尾盘 */
const DEFAULT_DAILY_POINTS = ["09:31", "14:55"];

/** bar 收盘后延迟，避免收盘瞬间拿到半根 bar */
const BAR_DELAY_SEC = 5;

function ymd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

function secondsOfDay(d) {
  return d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds();
}

function secToHHMM(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0");
}

/** "9:31" / "09:31" / "0931" → "09:31"；非法返回 null */
function normalizePoint(p) {
  const s = String(p || "").trim();
  let m = s.match(/^(\d{1,2}):(\d{2})$/);
  if (!m) m = s.match(/^(\d{1,2})(\d{2})$/);
  if (!m) return null;
  const h = Number(m[1]);
  const mi = Number(m[2]);
  if (h > 23 || mi > 59) return null;
  return String(h).padStart(2, "0") + ":" + String(mi).padStart(2, "0");
}

/**
 * 解析策略的 schedule 配置
 * @param {object} schedule 策略 JSON 里的 schedule 字段
 * @returns {{kind:'daily'|'bar'|'interval', points?:string[], barSec?:number, intervalMs?:number, label:string}}
 */
function resolveSchedule(schedule = {}) {
  const raw = String((schedule && schedule.period) || "").trim().toLowerCase();

  if (raw === "1d" || raw === "d" || raw === "day" || raw === "daily") {
    const points = (Array.isArray(schedule.at) ? schedule.at : DEFAULT_DAILY_POINTS)
      .map(normalizePoint)
      .filter(Boolean)
      .sort();
    const uniq = Array.from(new Set(points));
    const use = uniq.length ? uniq : DEFAULT_DAILY_POINTS.slice();
    return { kind: "daily", points: use, label: "日线 " + use.join(" / ") };
  }

  if (PERIOD_BAR_SEC[raw]) {
    return { kind: "bar", period: raw, barSec: PERIOD_BAR_SEC[raw], label: raw + " 收盘" };
  }

  const intervalMs = Number(schedule && schedule.intervalMs) > 0 ? Number(schedule.intervalMs) : 60000;
  return { kind: "interval", intervalMs, label: "每 " + Math.round(intervalMs / 1000) + "s" };
}

/**
 * 最近一个已收盘 bar 的收盘秒数（无则 null）
 * bar 与每个交易时段起点对齐：1m → 09:31…11:30 / 13:01…15:00；60m → 10:30,11:30 / 14:00,15:00
 */
function latestBarCloseSec(nowSec, barSec, delaySec = BAR_DELAY_SEC) {
  let best = null;
  for (const s of SESSIONS) {
    const span = s.end - s.start;
    if (span < barSec) continue;
    const avail = Math.min(nowSec - delaySec, s.end);
    if (avail - s.start < barSec) continue;
    const k = Math.floor((avail - s.start) / barSec);
    const close = s.start + Math.min(k * barSec, Math.floor(span / barSec) * barSec);
    if (close <= avail && (best === null || close > best)) best = close;
  }
  return best;
}

/** 下一个 bar 收盘秒数（当天无则 null） */
function nextBarCloseSec(nowSec, barSec, delaySec = BAR_DELAY_SEC) {
  for (const s of SESSIONS) {
    const span = s.end - s.start;
    if (span < barSec) continue;
    const candidates = Math.floor(span / barSec);
    for (let k = 1; k <= candidates; k++) {
      const close = s.start + k * barSec;
      if (close + delaySec > nowSec) return close;
    }
  }
  return null;
}

/**
 * 判断此刻是否该执行一次（纯函数）
 * @param {object} plan resolveSchedule 的结果
 * @param {Date} now
 * @param {Set<string>} [fired] 已触发 key 集合（会被读取，不修改）
 * @returns {{due:boolean, key:string, kind:string, label:string, point?:string}}
 */
function evaluate(plan, now, fired) {
  const date = ymd(now);
  const nowSec = secondsOfDay(now);
  const seen = fired || new Set();

  if (plan.kind === "daily") {
    // 取「已过的最新时点」：重启补跑时只补一次，不重放当天全部时点
    let latest = null;
    for (const p of plan.points) {
      const [h, m] = p.split(":").map(Number);
      if (h * 3600 + m * 60 <= nowSec) latest = p;
    }
    if (!latest) return { due: false, key: "", kind: plan.kind, label: plan.label };
    const key = date + "#" + latest;
    return { due: !seen.has(key), key, kind: plan.kind, label: plan.label, point: latest };
  }

  if (plan.kind === "bar") {
    const closeSec = latestBarCloseSec(nowSec, plan.barSec);
    if (closeSec === null) return { due: false, key: "", kind: plan.kind, label: plan.label };
    const point = secToHHMM(closeSec);
    const key = date + "#" + point;
    return { due: !seen.has(key), key, kind: plan.kind, label: plan.label, point };
  }

  // interval：按固定步长分桶，同一桶只跑一次
  const bucket = Math.floor(now.getTime() / plan.intervalMs);
  const key = date + "#i" + bucket;
  return { due: !seen.has(key), key, kind: plan.kind, label: plan.label };
}

/**
 * 下一次预计触发时间（仅用于界面提示，不参与判断）
 * @returns {{date:string, at:string}|null}
 */
function nextFireAt(plan, now) {
  const date = ymd(now);
  const nowSec = secondsOfDay(now);

  if (plan.kind === "daily") {
    for (const p of plan.points) {
      const [h, m] = p.split(":").map(Number);
      if (h * 3600 + m * 60 > nowSec) return { date, at: p };
    }
    return null; // 今天已无时点
  }

  if (plan.kind === "bar") {
    const closeSec = nextBarCloseSec(nowSec, plan.barSec);
    if (closeSec === null) return null;
    return { date, at: secToHHMM(closeSec) };
  }

  const intervalMs = plan.intervalMs;
  const next = (Math.floor(now.getTime() / intervalMs) + 1) * intervalMs;
  const d = new Date(next);
  return { date: ymd(d), at: secToHHMM(secondsOfDay(d)) };
}

module.exports = {
  resolveSchedule,
  evaluate,
  nextFireAt,
  normalizePoint,
  latestBarCloseSec,
  nextBarCloseSec,
  SESSIONS,
  PERIOD_BAR_SEC,
  DEFAULT_DAILY_POINTS,
  BAR_DELAY_SEC,
};
