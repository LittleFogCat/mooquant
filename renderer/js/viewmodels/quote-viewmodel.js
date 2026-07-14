/**
 * mookquant · Quote ViewModel
 *
 * 极简 MVVM：
 *   - state 是单一事实源
 *   - subscribe 注册变化回调
 *   - notify 触发回调
 *   - query() 执行业务动作，更新 state 后 notify
 */
class QuoteViewModel {
  constructor(facade) {
    this.facade = facade;
    this.state = {
      symbol: "",
      loading: false,
      invalid: false,
      data: null,         // 行情数据
      error: null,        // 字符串或 null
      info: null,         // 数据源描述
    };
    this._subs = [];
  }

  subscribe(fn) {
    this._subs.push(fn);
    fn(this.state);  // 立即派发一次，让 View 渲染初始状态
    return () => {
      this._subs = this._subs.filter((f) => f !== fn);
    };
  }

  _set(patch) {
    Object.assign(this.state, patch);
    this._notify();
  }

  _notify() {
    for (const fn of this._subs) fn(this.state);
  }

  setSymbol(symbol) {
    this._set({ symbol, invalid: false, error: null });
  }

  async loadInfo() {
    try {
      const info = await this.facade.quote.info();
      this._set({ info });
    } catch (e) {
      // ignore
    }
  }

  async query() {
    const symbol = (this.state.symbol || "").trim();
    if (!symbol) {
      this._set({ invalid: true, error: "请输入股票代码", data: null });
      return;
    }
    this._set({ loading: true, invalid: false, error: null, data: null });

    try {
      const resp = await this.facade.quote.query(symbol);
      if (!resp.ok) {
        this._set({ loading: false, error: resp.error || "查询失败", data: null });
        return;
      }
      this._set({ loading: false, error: null, data: resp.data });
    } catch (e) {
      this._set({ loading: false, error: e.message || "查询失败", data: null });
    }
  }

  async querySymbol(symbol) {
    this._set({ symbol, invalid: false, error: null });
    await this.query();
  }
}

window.QuoteViewModel = QuoteViewModel;