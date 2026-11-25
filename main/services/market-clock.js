/**
 * mookquant · A 股交易时钟
 *
 * 只做「本地时间 → 交易时段状态」判断，不依赖外部数据源：
 *  - 交易日：周一至周五（节假日由调用方按需再校验）
 *  - 交易时段：09:30-11:30、13:00-15:00
 *  - 集合竞价/盘前：09:15-09:30 视为 pre（不下单）
 *
 * 无人值守执行器靠它决定「此刻能不能下单」，避免收盘后空耗接口额度。
 */

const SESSIONS = [
  { name: "morning", start: 9 * 60 + 30, end: 11 * 60 + 30 },
  { name: "afternoon", start: 13 * 60, end: 15 * 60 },
];

/** 当前分钟数（本地时区） */
function minutesOfDay(d) {
  return d.getHours() * 60 + d.getMinutes();
}

function isWeekend(d) {
  const w = d.getDay();
  return w === 0 || w === 6;
}

/**
 * 当前交易状态
 * @param {Date} [date]
 * @returns {'open'|'lunch'|'pre'|'after'|'weekend'} 交易时段状态
 */
function sessionState(date = new Date()) {
  if (isWeekend(date)) return "weekend";
  const m = minutesOfDay(date);
  for (const s of SESSIONS) {
    if (m >= s.start && m < s.end) return "open";
  }
  if (m >= 9 * 60 + 15 && m < 9 * 60 + 30) return "pre";
  if (m >= 11 * 60 + 30 && m < 13 * 60) return "lunch";
  if (m >= 15 * 60) return "after";
  return "pre";
}

/**
 * 是否允许下单
 * @param {object} [opts]
 * @param {boolean} [opts.allowOffHours=false] 调试用：非交易时段也放行
 * @param {Date} [opts.now]
 */
function canTrade(opts = {}) {
  if (opts.allowOffHours) return true;
  return sessionState(opts.now || new Date()) === "open";
}

/** 状态的中文说明（日志/界面用） */
const STATE_LABELS = {
  open: "交易中",
  lunch: "午间休市",
  pre: "盘前",
  after: "已收盘",
  weekend: "周末休市",
};

function stateLabel(state) {
  return STATE_LABELS[state] || state;
}

module.exports = { sessionState, canTrade, stateLabel, SESSIONS };
