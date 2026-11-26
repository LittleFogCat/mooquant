/**
 * mookquant · 模拟盘视图（无人值守交易面板）
 *
 * 一屏回答四个问题：
 *   1. 引擎现在什么状态（交易日 / 时段 / 下一时点 / 是否持有交易权）
 *   2. 今天赚了多少（总资产、盈亏、净值）
 *   3. 今天做了什么（成交、信号、跳过原因、异常）
 *   4. 明天怎么复盘（历史日报）
 */
import { escapeHtml, fmtNum, cls } from '../utils/format.js';

const STATE_DOT = {
  open: "var(--up, #e5484d)",
  tail: "var(--up, #e5484d)",
  pre: "#f5a524",
  lunch: "var(--text-3)",
  settle: "var(--text-3)",
  idle: "var(--text-3)",
};

/** ISO → MM-DD HH:mm:ss */
function fmtTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

/** 引擎状态条 */
function renderEngineBar(state) {
  const e = state.engine;
  if (!e) {
    return '<div class="card"><div class="card-title">交易引擎</div><div class="empty-state"><div class="empty-state-text">未连接（观察模式或调度器未启动）</div></div></div>';
  }
  const dot = STATE_DOT[e.state] || "var(--text-3)";
  const roleText = e.role === "observer"
    ? '<span class="badge badge-error">观察模式</span>'
    : '<span class="badge badge-running">本机交易者</span>';
  const tradeOn = state.settings && state.settings.autoTrade && state.settings.autoTrade.enabled === false
    ? '<span class="badge badge-draft">自动下单已关闭（只算信号）</span>'
    : '<span class="badge badge-active">自动下单已开启</span>';
  const ch = state.info ? escapeHtml(state.info.description || state.info.mode) : "-";
  const ds = state.settings ? escapeHtml(state.settings.dataSource || "-") : "-";

  return `
    <div class="card">
      <div class="card-title">交易引擎 ${roleText} ${tradeOn}</div>
      <div style="display:flex;align-items:center;gap:10px;font-size:13px;margin-bottom:8px">
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${dot}"></span>
        <strong>${escapeHtml(e.stateLabel || e.state)}</strong>
        <span style="color:var(--text-3)">${e.isTradingDay ? "今日为交易日" : "今日非交易日"}</span>
        <span style="color:var(--text-3)">下一时点 ${escapeHtml(e.nextEventAt || "-")}</span>
        ${e.calibrated === false ? '<span style="color:#f5a524">节假日未校准</span>' : ""}
      </div>
      <div style="font-size:12px;color:var(--text-3)">
        交易通道：${ch} · 行情源：${ds} · 今日已触发：${escapeHtml((e.todayFired || []).join(" → ") || "无")}
      </div>
      ${state.report && state.report.generatedAt
        ? '<div style="font-size:12px;color:var(--text-3);margin-top:4px">今日报告：' + escapeHtml(fmtTime(state.report.generatedAt)) + " 生成</div>"
        : '<div style="font-size:12px;color:var(--text-3);margin-top:4px">今日报告：尚未生成（15:05 自动生成，也可手动生成）</div>'}
      ${e.lastError ? '<div style="font-size:12px;color:#e5484d;margin-top:4px">调度异常：' + escapeHtml(e.lastError) + "</div>" : ""}
    </div>
  `;
}

/** 关键数字 */
function renderStats(state) {
  const a = state.account;
  const q = state.quota;
  const rep = state.report;
  const pnl = a ? a.totalPnl : null;
  const pnlPct = a && a.totalAssets ? (pnl / (a.totalAssets - pnl)) * 100 : null;
  const nav = state.report && state.report.account ? state.report.account.nav : null;

  const cell = (label, value, cls2) =>
    '<div class="stat-card"><div class="stat-label">' + label + '</div><div class="stat-value ' + (cls2 || "") + '">' + value + '</div></div>';

  const quotaText = q ? (q.limit > 0 ? q.used + " / " + q.limit : "不限") : "-";
  const blocked = q && q.blocked;
  const quotaHint = blocked
    ? "交易通道已阻断（" + escapeHtml(String(q.blockCode || "")) + "）：" + escapeHtml(q.blockReason || "额度或授权异常")
    : q
    ? (q.openAllowed ? "额度充足可开仓" : q.closeAllowed ? "仅允许平仓" : "额度已用尽")
    : "额度未知";

  return `
    <div class="card">
      <div class="card-title">今日表现${blocked ? ' <span class="badge badge-error">通道阻断</span>' : ""}${state.snapshotAt ? ' <span style="font-size:11px;color:var(--text-3)">快照 ' + escapeHtml(fmtTime(state.snapshotAt)) + "</span>" : ""}</div>
      <div class="stats-grid">
        ${cell("总资产", a ? fmtNum(a.totalAssets, 0) : "-")}
        ${cell("今日盈亏", pnl != null ? fmtNum(pnl, 2) + (pnlPct != null ? "（" + pnlPct.toFixed(2) + "%）" : "") : "-", cls(pnl))}
        ${cell("净值", nav != null ? nav.toFixed(6) : "-")}
        ${cell("接口额度", quotaText)}
      </div>
      <div style="margin-top:10px;font-size:12px;color:${blocked ? "#e5484d" : "var(--text-3)"}">
        今日成交 ${rep ? rep.orders || 0 : 0} 笔 ·
        ${rep && rep.filled != null ? "成交成功 " + rep.filled + " 笔 · " : ""}
        策略信号 ${rep ? rep.signals || 0 : 0} 次 ·
        异常 ${rep ? (rep.errors || []).length : 0} 条 ·
        ${quotaHint}
      </div>
    </div>
  `;
}

/** 运行中策略 */
function renderStrategies(state) {
  const list = state.executors || [];
  if (!list.length) {
    return '<div class="card"><div class="card-title">运行中策略</div><div class="empty-state"><div class="empty-state-text">没有运行中的策略' +
      (state.engine && state.engine.role === "observer" ? "（当前为观察模式，交易由另一进程执行）" : "（去「策略」页启动）") +
      "</div></div></div>";
  }
  const rows = list.map((e) => {
    const skip = e.lastSkip ? escapeHtml(e.lastSkip) : "—";
    return "<tr>" +
      "<td><strong>" + escapeHtml(e.strategyName) + "</strong>" +
      (e.error ? '<div style="font-size:11px;color:#e5484d">⚠ ' + escapeHtml(e.error) + "</div>" : "") + "</td>" +
      "<td>" + escapeHtml(e.schedule ? e.schedule.label : "—") +
      (e.schedule && e.schedule.next ? '<div style="font-size:11px;color:var(--text-3)">下次 ' + escapeHtml(e.schedule.next.at) + "</div>" : "") + "</td>" +
      '<td class="num">' + (e.tickCount || 0) + "</td>" +
      "<td>" + (e.lastSignal ? escapeHtml(e.lastSignal.action) + "（" + escapeHtml(e.lastSignal.reason || "-") + "）" : "—") + "</td>" +
      '<td style="font-size:12px;color:var(--text-3)">' + skip + "</td>" +
      "</tr>";
  }).join("");
  return `
    <div class="card">
      <div class="card-title">运行中策略</div>
      <table class="data-table">
        <thead><tr><th>策略</th><th>调度</th><th class="num">已算信号</th><th>上次信号</th><th>最近跳过原因</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

/** 持仓 */
function renderPositions(state) {
  const list = state.positions || [];
  if (!list.length) {
    return '<div class="card"><div class="card-title">持仓</div><div class="empty-state"><div class="empty-state-text">' +
      (state.loading ? "加载中..." : "无持仓") + "</div></div></div>";
  }
  const rows = list.map((p) =>
    "<tr>" +
    "<td>" + escapeHtml(p.symbol) + "</td>" +
    "<td>" + escapeHtml(p.name || "-") + "</td>" +
    '<td class="num">' + p.quantity + "</td>" +
    '<td class="num">' + fmtNum(p.costPrice) + "</td>" +
    '<td class="num">' + fmtNum(p.currentPrice) + "</td>" +
    '<td class="num">' + fmtNum(p.marketValue, 0) + "</td>" +
    '<td class="num ' + cls(p.pnl) + '">' + fmtNum(p.pnl, 0) + "</td>" +
    '<td class="num ' + cls(p.pnlPct) + '">' + (p.pnlPct != null ? fmtNum(p.pnlPct) + "%" : "-") + "</td>" +
    "</tr>").join("");
  return `
    <div class="card">
      <div class="card-title">持仓</div>
      <table class="data-table">
        <thead><tr><th>代码</th><th>名称</th><th class="num">数量</th><th class="num">成本</th><th class="num">现价</th><th class="num">市值</th><th class="num">盈亏</th><th class="num">盈亏%</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

/** 今日委托 */
function renderOrders(state) {
  const list = (state.orders || []).slice(0, 15);
  if (!list.length) {
    return '<div class="card"><div class="card-title">今日委托</div><div class="empty-state"><div class="empty-state-text">' +
      (state.loading ? "加载中..." : "无委托") + "</div></div></div>";
  }
  const rows = list.map((o) =>
    "<tr>" +
    "<td>" + escapeHtml(fmtTime(o.createdAt)) + "</td>" +
    '<td><span class="badge badge-' + (o.side === "buy" ? "buy" : "sell") + '">' + (o.side === "buy" ? "买入" : "卖出") + "</span></td>" +
    "<td>" + escapeHtml(o.symbol) + "</td>" +
    '<td class="num">' + (o.price > 0 ? fmtNum(o.price) : "市价") + "</td>" +
    '<td class="num">' + o.quantity + "</td>" +
    "<td>" + escapeHtml(o.status) + "</td>" +
    "</tr>").join("");
  return `
    <div class="card">
      <div class="card-title">今日委托</div>
      <table class="data-table">
        <thead><tr><th>时间</th><th>方向</th><th>代码</th><th class="num">价格</th><th class="num">数量</th><th>状态</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

/** 日报 */
function renderReports(state) {
  const list = state.reports || [];
  const body = list.length
    ? list.map((r) =>
        "<tr>" +
        "<td>" + escapeHtml(r.date) + "</td>" +
        '<td class="num">' + (r.totalAssets != null ? fmtNum(r.totalAssets, 0) : "-") + "</td>" +
        '<td class="num">' + (r.orders || 0) + "</td>" +
        '<td class="num">' + (r.signals || 0) + "</td>" +
        '<td class="num"' + (r.errors ? ' style="color:#e5484d"' : "") + ">" + (r.errors || 0) + "</td>" +
        "</tr>").join("")
    : '<tr><td colspan="5" style="text-align:center;color:var(--text-3)">还没有日报</td></tr>';
  return `
    <div class="card">
      <div class="card-title">历史日报 <span style="font-size:11px;color:var(--text-3)">data/reports/</span></div>
      <table class="data-table">
        <thead><tr><th>日期</th><th class="num">总资产</th><th class="num">成交</th><th class="num">信号</th><th class="num">异常</th></tr></thead>
        <tbody>${body}</tbody>
      </table>
      ${state.report && (state.report.skips || []).length
        ? '<div style="margin-top:10px;font-size:12px;color:var(--text-3)">今日跳过原因：' +
          state.report.skips.map((s) => escapeHtml(s.strategy + " — " + s.reason)).join("；") + "</div>"
        : ""}
    </div>
  `;
}

function render(root, vm) {
  function paint(state) {
    root.innerHTML =
      (state.error ? '<div class="status error" style="margin-bottom:16px"><span>' + escapeHtml(state.error) + '</span><button class="status-dismiss" data-dismiss-error>&times;</button></div>' : "") +
      '<div style="display:flex;gap:8px;margin-bottom:12px">' +
        '<button class="btn btn-secondary btn-sm" data-action="refresh"' + (state.loading ? " disabled" : "") + ">" +
          (state.loading ? "刷新中..." : "刷新数据") + "</button>" +
        '<button class="btn btn-primary btn-sm" data-action="generate-report"' + (state.generating ? " disabled" : "") + ">" +
          (state.generating ? "生成中..." : "生成今日报告") + "</button>" +
        '<span style="font-size:11px;color:var(--text-3);align-self:center">刷新数据会消耗 3 次账户接口；调度/信号/额度是本地状态，自动刷新不耗额度</span>' +
      "</div>" +
      renderEngineBar(state) +
      renderStats(state) +
      renderStrategies(state) +
      renderPositions(state) +
      renderOrders(state) +
      renderReports(state);

    root.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const a = btn.dataset.action;
        if (a === "refresh") vm.refreshAll();
        if (a === "generate-report") vm.generateReport();
      });
    });
    const dismiss = root.querySelector("[data-dismiss-error]");
    if (dismiss) dismiss.addEventListener("click", () => vm.dismissError());
  }

  vm.subscribe(paint);
}

export { render };
