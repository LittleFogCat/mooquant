/**
 * mookquant · 模拟盘 ViewModel
 *
 * 「模拟」页是无人值守交易的面板，数据分两类，刻意分开加载：
 *   · loadLive()     —— 零接口开销：调度器状态、执行器状态、今日报告预览、额度、通道信息
 *   · loadSnapshot() —— 有接口开销（账户/持仓/委托各 1 次）：进入页面、手动刷新、以及日终后各一次
 * 这样常开这个页面也不会把东财每天 1000 次的额度吃掉。
 */
class SimViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      engine: null,      // 调度器状态（含 role / 日历校准 / 下一时点）
      executors: [],     // 运行中策略
      report: null,      // 今日报告（未生成时为盘中预览）
      reports: [],       // 历史日报摘要
      quota: null,       // 接口额度
      info: null,        // 交易通道信息
      settings: null,    // 数据源/交易源/自动交易开关
      account: null,     // 账户快照（有开销）
      positions: [],     // 持仓快照（有开销）
      orders: [],        // 委托快照（有开销）
      snapshotAt: null,  // 最近一次快照时间
      loading: false,
      generating: false,
      error: null,
    };
    this._subs = [];
  }

  subscribe(fn) {
    this._subs.push(fn);
    fn(this.state);
    return () => { this._subs = this._subs.filter((f) => f !== fn); };
  }

  _set(patch) {
    Object.assign(this.state, patch);
    this._notify();
  }

  _notify() {
    for (const fn of this._subs) fn(this.state);
  }

  dismissError() {
    this._set({ error: null });
  }

  /** 零开销部分：本地状态 + 文件读取，可高频轮询 */
  async loadLive() {
    try {
      const [engine, executors, report, quota, info] = await Promise.all([
        this.facade.scheduler ? this.facade.scheduler.status() : Promise.resolve({ ok: false }),
        this.facade.executor ? this.facade.executor.list() : Promise.resolve({ ok: false }),
        this.facade.report ? this.facade.report.today() : Promise.resolve({ ok: false }),
        this.facade.trade.quota ? this.facade.trade.quota() : Promise.resolve({ ok: false }),
        this.facade.trade.info(),
      ]);
      this._set({
        engine: engine && engine.ok ? engine.data : null,
        executors: executors && executors.ok ? executors.data : [],
        report: report && report.ok ? report.data : null,
        quota: quota && quota.ok ? quota.data : null,
        info: info && info.ok ? info.data : null,
      });
    } catch (e) {
      this._set({ error: e.message });
    }
  }

  /** 有开销部分：账户/持仓/委托（3 次接口调用，只在必要时拉） */
  async loadSnapshot() {
    this._set({ loading: true, error: null });
    try {
      const [account, positions, orders] = await Promise.all([
        this.facade.trade.getAccount(),
        this.facade.trade.getPositions(),
        this.facade.trade.getOrders(),
      ]);
      this._set({
        account: account.ok ? account.data : null,
        positions: positions.ok ? positions.data : [],
        orders: orders.ok ? orders.data : [],
        snapshotAt: new Date().toISOString(),
        loading: false,
      });
    } catch (e) {
      this._set({ loading: false, error: e.message });
    }
  }

  /** 日报列表（读本地文件，无接口开销） */
  async loadReports() {
    try {
      if (!this.facade.report) return;
      const r = await this.facade.report.list(10);
      if (r && r.ok) this._set({ reports: r.data || [] });
    } catch {}
  }

  /** 读取通道/数据源配置（本地配置读取，无接口开销） */
  async loadSettings() {
    try {
      const s = await this.facade.settings.get();
      this._set({ settings: s || null });
    } catch {}
  }

  /** 进入页面：免费部分 + 一次快照 + 日报列表 */
  async enter() {
    await Promise.all([this.loadLive(), this.loadSettings(), this.loadReports()]);
    await this.loadSnapshot();
  }

  /** 手动刷新：包含接口开销部分 */
  async refreshAll() {
    await this.enter();
  }

  /** 手动生成今日报告（2 次接口调用：账户 + 持仓） */
  async generateReport() {
    if (this.state.generating) return;
    this._set({ generating: true, error: null });
    try {
      const r = await this.facade.report.generate();
      if (!r || !r.ok) {
        this._set({ generating: false, error: (r && r.error) || "生成失败" });
        return;
      }
      this._set({ generating: false, report: r.data });
      await this.loadReports();
    } catch (e) {
      this._set({ generating: false, error: e.message });
    }
  }

  startPolling(intervalMs = 5000) {
    this.stopPolling();
    this._timer = setInterval(() => this.loadLive(), intervalMs);
    return this._timer;
  }

  stopPolling() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }
}

export { SimViewModel };
