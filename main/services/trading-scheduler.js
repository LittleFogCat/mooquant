/**
 * mookquant · 交易调度器（无人值守）
 *
 * 职责边界：只回答「今天的这个时刻该做什么」，并向订阅者发事件。
 * **不直接调用行情/交易接口**——下单与否由执行器决定，调度器只管放行窗口。
 *
 * 为什么用「分钟级 tick + 时点比对」而不是长定时器：
 *   长 setTimeout 在电脑休眠、时钟漂移、进程重启时会直接错过时点；
 *   每分钟比对一次「当前时间落在哪个事件窗口」则可以自然补跑。
 *
 * 状态机（与 market-clock 互补）：
 *   idle → pre → open → lunch → open → tail → settle → idle
 *   market-clock 判「交易所开没开市」，本调度器判「我们今天打算做什么」。
 */
const path = require("path");
const fs = require("fs");

const LOG_DIR = path.join(__dirname, "..", "..", "data", "logs");
const LOG_FILE = path.join(LOG_DIR, "scheduler.log");

/** 事件窗口：每个事件从 at 起生效，直到下一个事件的 at（互相衔接、不重叠） */
const EVENTS = [
  { name: "pre_open", at: "09:15", label: "盘前检查", state: "pre" },
  { name: "market_open", at: "09:30", label: "开盘", state: "open" },
  { name: "lunch_break", at: "11:30", label: "午间休市", state: "lunch" },
  { name: "market_resume", at: "13:00", label: "恢复交易", state: "open" },
  { name: "tail", at: "14:55", label: "尾盘", state: "tail" },
  { name: "settle", at: "15:05", label: "日终结算", state: "settle" },
  { name: "day_end", at: "15:30", label: "收尾", state: "idle" },
];

/** 允许下单的状态：open（正常盘中）与 tail（尾盘调仓/止损） */
const TRADABLE_STATES = new Set(["open", "tail"]);

const STATE_LABELS = {
  idle: "空闲",
  pre: "盘前",
  open: "交易中",
  lunch: "午休",
  tail: "尾盘",
  settle: "结算",
};

/** 默认 20s 检查一次：比 60s 更抗抖动（长任务/GC 不会整分钟跳过） */
const DEFAULT_TICK_MS = 20000;

function toMinutes(hhmm) {
  const [h, m] = String(hhmm).split(":").map(Number);
  return h * 60 + (m || 0);
}

function minutesOfDay(d) {
  return d.getHours() * 60 + d.getMinutes();
}

function ymd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

function nowIso(d) {
  return d.toISOString();
}

class TradingScheduler {
  /**
   * @param {object} opts
   * @param {import('./trading-calendar').TradingCalendar} [opts.calendar]
   * @param {(name:string, payload:object)=>void} [opts.onEvent] 事件订阅回调
   * @param {object} [opts.autoTrade] autoTrade 配置块（目前只用 schedTickMs）
   * @param {() => Date} [opts.now] 注入时钟（测试用）
   * @param {boolean} [opts.autoTick=true] false = 不自动定时，由调用方驱动
   */
  constructor(opts = {}) {
    this._calendar = opts.calendar || null;
    this._onEvent = opts.onEvent || null;
    this._autoTrade = opts.autoTrade || {};
    this._now = opts.now || (() => new Date());
    this._tickMs = Number(this._autoTrade.schedTickMs) > 0
      ? Number(this._autoTrade.schedTickMs)
      : DEFAULT_TICK_MS;
    this._autoTick = opts.autoTick !== false;
    this._timer = null;
    this._running = false;

    this.state = "idle";
    this._dateKey = null;          // 当前已初始化的日期
    this._fired = new Set();       // 当日已触发事件名
    this._nonTradingDay = null;    // 当日是否非交易日（缓存判断结果）
    this._lastEvent = null;
    this._lastError = null;
    this._startedAt = null;
    this._eventCount = 0;
  }

  // ---------- 生命周期 ----------

  async start() {
    if (this._running) return;
    this._running = true;
    this._startedAt = nowIso(this._now());
    this._appendLog("scheduler start tick=" + this._tickMs + "ms");
    await this._refreshCalendar();
    // 启动即比对一次：进程在盘中重启时立刻补跑当前窗口事件
    this._tickAt(this._now());
    if (this._autoTick) {
      this._timer = setInterval(() => this._tickAt(this._now()), this._tickMs);
      if (this._timer.unref) this._timer.unref();
    }
  }

  stop() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    this._running = false;
    this._appendLog("scheduler stop");
  }

  /** 加载当年日历（失败降级，不影响调度继续跑） */
  async _refreshCalendar() {
    if (!this._calendar || typeof this._calendar.ensureLoaded !== "function") return;
    try {
      const r = await this._calendar.ensureLoaded(this._now().getFullYear());
      this._appendLog("calendar " + JSON.stringify({ year: r.year, source: r.source, calibrated: r.calibrated, error: r.error }));
    } catch (e) {
      this._lastError = "日历加载失败: " + e.message;
      this._appendLog("calendar error " + e.message);
    }
  }

  // ---------- 核心：比对当前时间 ----------

  /**
   * 手动驱动一次（测试可直接传时间）
   * @param {Date} [now]
   */
  tickAt(now) {
    return this._tickAt(now || this._now());
  }

  _tickAt(now) {
    const dateKey = ymd(now);
    if (dateKey !== this._dateKey) {
      // 跨日：重置当日状态
      this._dateKey = dateKey;
      this._fired.clear();
      this._nonTradingDay = null;
      this.state = "idle";
    }

    const isTradingDay = this._isTradingDay(now);
    if (!isTradingDay) {
      if (this.state !== "idle") {
        this.state = "idle";
        this._appendLog("non-trading-day " + dateKey + " → idle");
      }
      return null;
    }

    const idx = this._activeEventIndex(now);
    if (idx < 0) {
      // 09:15 之前或 15:30 之后
      this.state = "idle";
      return null;
    }

    const evt = EVENTS[idx];
    this.state = evt.state;
    if (this._fired.has(evt.name)) return null;

    this._fired.add(evt.name);
    this._eventCount++;
    const catchUp = minutesOfDay(now) > toMinutes(evt.at);
    const payload = {
      name: evt.name,
      label: evt.label,
      at: evt.at,
      date: dateKey,
      state: evt.state,
      catchUp,
      firedAt: nowIso(now),
    };
    this._lastEvent = payload;
    this._appendLog(
      "event " + evt.name + " (" + evt.label + ") at " + evt.at + (catchUp ? " [catch-up]" : "")
    );

    if (this._onEvent) {
      // 订阅者异常不能打断调度
      try {
        const ret = this._onEvent(evt.name, payload);
        if (ret && typeof ret.catch === "function") {
          ret.catch((e) => {
            this._lastError = "事件处理异常(" + evt.name + "): " + e.message;
            this._appendLog("handler error " + evt.name + ": " + e.message);
          });
        }
      } catch (e) {
        this._lastError = "事件处理异常(" + evt.name + "): " + e.message;
        this._appendLog("handler error " + evt.name + ": " + e.message);
      }
    }
    return payload;
  }

  _isTradingDay(now) {
    if (this._nonTradingDay !== null && this._dateKey === ymd(now)) return !this._nonTradingDay;
    if (!this._calendar) return true; // 无日历时信任 market-clock 的周末判断
    const ok = this._calendar.isTradingDay(now);
    this._nonTradingDay = !ok;
    return ok;
  }

  /** 当前时间落在哪个事件窗口（-1 = 不在任何窗口） */
  _activeEventIndex(now) {
    const m = minutesOfDay(now);
    for (let i = 0; i < EVENTS.length; i++) {
      const start = toMinutes(EVENTS[i].at);
      const end = i + 1 < EVENTS.length ? toMinutes(EVENTS[i + 1].at) : 24 * 60;
      if (m >= start && m < end) return i;
    }
    return -1;
  }

  // ---------- 对外查询 ----------

  /**
   * 此刻是否允许下单（执行器每次 tick 都要问）
   * 判断依据是「当前时间」而不是缓存下来的 state：
   * 长时间没有 tick（电脑休眠/进程卡住）时 state 可能过期，时间不会。
   */
  canPlaceOrder(now = this._now()) {
    if (!this._isTradingDay(now)) return false;
    const idx = this._activeEventIndex(now);
    if (idx < 0) return false;
    if (!TRADABLE_STATES.has(EVENTS[idx].state)) return false;
    // 尾盘窗口 14:55–15:05 跨越收盘，15:00 之后交易所不再接受委托
    const m = minutesOfDay(now);
    return m >= 9 * 60 + 30 && m < 15 * 60;
  }

  /** 下一个事件的时点（用于界面提示与休眠唤醒规划） */
  nextEvent(now = this._now()) {
    const m = minutesOfDay(now);
    for (const evt of EVENTS) {
      if (toMinutes(evt.at) > m) {
        return { name: evt.name, label: evt.label, at: evt.at, date: ymd(now), inMinutes: toMinutes(evt.at) - m };
      }
    }
    // 今天已无事件 → 下一个交易日
    const next = new Date(now.getTime());
    for (let i = 0; i < 30; i++) {
      next.setDate(next.getDate() + 1);
      if (!this._calendar || this._calendar.isTradingDay(next)) break;
    }
    const first = EVENTS[0];
    return {
      name: first.name,
      label: first.label,
      at: first.at,
      date: ymd(next),
      inMinutes: (24 * 60 - m) + toMinutes(first.at),
    };
  }

  getStatus() {
    const now = this._now();
    const next = this.nextEvent(now);
    const calibrated = this._calendar ? this._calendar.isCalibrated(now) : false;
    return {
      running: this._running,
      state: this.state,
      stateLabel: STATE_LABELS[this.state] || this.state,
      canPlaceOrder: this.canPlaceOrder(now),
      isTradingDay: this._isTradingDay(now),
      calibrated,
      date: ymd(now),
      now: nowIso(now),
      todayFired: Array.from(this._fired),
      eventCount: this._eventCount,
      lastEvent: this._lastEvent,
      nextEvent: next,
      nextEventAt: next.date + " " + next.at,
      lastError: this._lastError,
      startedAt: this._startedAt,
      calendar: this._calendar ? this._calendar.getStatus() : null,
    };
  }

  /** 供界面展示的时点表 */
  static get EVENTS() {
    return EVENTS.map((e) => ({ ...e }));
  }

  // ---------- 日志 ----------

  _appendLog(msg) {
    // 未启动（单测里手动 tick）时不写生产日志，避免测试污染 data/logs/scheduler.log
    if (!this._running) return;
    try {
      if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });
      fs.appendFileSync(LOG_FILE, "[" + new Date().toISOString() + "] " + msg + "\n", "utf-8");
    } catch {
      /* 日志失败不影响调度 */
    }
  }
}

module.exports = { TradingScheduler, EVENTS, STATE_LABELS, TRADABLE_STATES };
