"""
量化回测框架 + 评估指标 (Phase 3.1 + 3.4)

Walk-forward 回测引擎，支持：
  - 逐日推进，训练→预测→模拟交易
  - T+1 执行、交易成本（印花税/佣金/滑点）、涨跌停限制
  - A 股做多模式（默认），可扩展做空

评估指标：
  - 累计收益率、年化收益率
  - 夏普比率、最大回撤
  - 胜率、盈亏比 (Profit Factor)
  - 日度净值曲线

用法：
    from backtest import BacktestEngine, backtest_single

    engine = BacktestEngine(initial_capital=100000)
    engine.load_csv_data("data/sh/", ["600000", "600001"])
    report = engine.run()
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# 交易成本配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class CostConfig:
    """A 股交易成本。"""
    stamp_duty: float = 0.0005       # 印花税（卖出 0.05%）
    commission: float = 0.0003       # 佣金（双向 0.03%）
    slippage: float = 0.001          # 滑点（0.1%）
    min_commission: float = 5.0      # 最低佣金（元）


# ═══════════════════════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════════════════════

@dataclass
class Position:
    """持仓记录。"""
    code: str
    shares: int = 0                # 持仓股数（100 的整数倍）
    cost_basis: float = 0.0        # 成本均价
    buy_date: Optional[str] = None # 买入日期（T+1 判断用）


@dataclass
class Trade:
    """交易记录。"""
    date: str
    code: str
    action: str                    # "buy" / "sell"
    price: float
    shares: int
    amount: float
    cost: float                    # 交易费用
    pnl: float = 0.0               # 平仓盈亏（仅 sell）


class BacktestEngine:
    """Walk-forward 回测引擎。"""

    def __init__(self, initial_capital: float = 100000,
                 cost: CostConfig | None = None,
                 max_position_pct: float = 0.2,
                 top_n: int = 5):
        """
        :param initial_capital: 初始资金
        :param cost: 交易成本配置
        :param max_position_pct: 单票最大仓位占比
        :param top_n: 每日最多持仓数
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.cost = cost or CostConfig()
        self.max_position_pct = max_position_pct
        self.top_n = top_n

        # 状态
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.nav_history: list[dict] = []  # {date, nav, cash, market_value}

        # 数据
        self.price_data: dict[str, pd.DataFrame] = {}  # code → OHLCV DataFrame
        self.predictions: pd.DataFrame | None = None   # 每日预测

    # ── 数据加载 ────────────────────────────────────────────────
    def load_csv_data(self, data_dir: str, codes: list[str]):
        """从 data/ 目录加载 CSV 数据。

        :param data_dir: 如 "data/sh/" （不含尾缀）
        :param codes: 股票代码列表，如 ["600000", "600001"]
        """
        for code in codes:
            csv_path = os.path.join(data_dir, f"{code}.csv")
            if not os.path.exists(csv_path):
                print(f"[跳过] 文件不存在: {csv_path}")
                continue
            df = pd.read_csv(csv_path, parse_dates=["date"])
            df = df.sort_values("date").reset_index(drop=True)
            # 确保有 amount 列
            if "amount" not in df.columns:
                df["amount"] = df["close"] * df["volume"]
            self.price_data[code] = df

        print(f"[数据加载] {len(self.price_data)} 只股票")

    def load_price_data(self, data: dict[str, pd.DataFrame]):
        """直接传入价格 DataFrame 字典。"""
        self.price_data = data

    def load_predictions(self, pred_df: pd.DataFrame):
        """加载预测结果 DataFrame（需含 date, code, prediction, confidence 列）。

        prediction: "up" / "down"; confidence: 0~1
        """
        self.predictions = pred_df

    # ── 运行回测 ────────────────────────────────────────────────
    def run(self, start_date: str | None = None,
            end_date: str | None = None,
            verbose: bool = True) -> dict:
        """执行回测。

        遍历所有可用交易日，按信号买卖，记录净值和交易。
        """
        if not self.price_data:
            return {"error": "无价格数据"}

        # 获取所有交易日（取所有股票日期的并集，排序）
        all_dates = set()
        for df in self.price_data.values():
            all_dates.update(df["date"].dt.strftime("%Y-%m-%d"))
        date_list = sorted(all_dates)

        if start_date:
            date_list = [d for d in date_list if d >= start_date]
        if end_date:
            date_list = [d for d in date_list if d <= end_date]

        if verbose:
            print(f"[回测] {date_list[0]} ~ {date_list[-1]}, "
                  f"{len(date_list)} 天, 初始资金 {self.cash:,.0f}")

        for i, date in enumerate(date_list):
            # 1. 先处理卖出（需要卖出的持仓）
            self._process_sells(date)

            # 2. 计算当前市值和可用资金
            market_value = self._market_value(date)
            available = self.cash + self._sellable_value(date)

            # 3. 生成买入信号并执行
            if self.predictions is not None:
                self._process_buys(date, available)

            # 4. 更新持仓中不可卖的股票（标记为可卖）
            self._unlock_positions(date)

            # 5. 记录净值
            nav = self.cash + self._market_value(date)
            self.nav_history.append({
                "date": date,
                "nav": round(nav, 2),
                "cash": round(self.cash, 2),
                "market_value": round(self._market_value(date), 2),
            })

            if verbose and (i + 1) % 50 == 0:
                ret = (nav / self.initial_capital - 1) * 100
                print(f"  {date}  净值={nav:,.0f}  收益={ret:+.1f}%  "
                      f"持仓={len(self.positions)}")

        return self.report()

    # ── 内部方法 ─────────────────────────────────────────────────
    def _get_price(self, code: str, date: str) -> dict | None:
        """获取某只股票在某日的价格数据（用于模拟成交）。"""
        df = self.price_data.get(code)
        if df is None:
            return None
        row = df[df["date"].dt.strftime("%Y-%m-%d") == date]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
            "pre_close": float(r.get("pre_close", r["close"])),
        }

    def _calc_limit_prices(self, price_data: dict) -> tuple[float, float]:
        """计算涨跌停价（A股 ±10%，科创板/创业板 ±20%，ST ±5%）。

        简化判断：科创板(688)/创业板(300)为±20%，其余主板为±10%。
        ST 精确判断需股票名称，此处暂按代码前缀近似。
        """
        pre_close = price_data.get("pre_close", price_data["close"])
        code = price_data.get("code", "")
        if code.startswith(("688", "300")):
            limit = 0.20  # 科创板 / 创业板
        else:
            limit = 0.10  # 主板
        return pre_close * (1 - limit), pre_close * (1 + limit)

    def _exec_price(self, price_data: dict, action: str) -> float | None:
        """计算成交价：买入按 open+滑点，卖出按 open-滑点。"""
        px = price_data["open"]
        down_limit, up_limit = self._calc_limit_prices(price_data)

        if action == "buy":
            # 涨停无法买入
            if px >= up_limit * 0.999:
                return None
            return min(px * (1 + self.cost.slippage), up_limit)
        else:
            # 跌停无法卖出
            if px <= down_limit * 1.001:
                return None
            return max(px * (1 - self.cost.slippage), down_limit)

    def _process_sells(self, date: str):
        """处理当日卖出信号。"""
        to_sell = []

        if self.predictions is not None:
            pred_today = self.predictions[
                self.predictions["date"] == date
            ]
            sell_codes = set(
                pred_today[pred_today["prediction"] == "down"]["code"]
            )
        else:
            sell_codes = set()

        for code, pos in list(self.positions.items()):
            if pos.shares <= 0:
                continue
            if pos.buy_date == date:
                continue  # T+1 限制
            if code in sell_codes:
                to_sell.append(code)

        for code in to_sell:
            price_data = self._get_price(code, date)
            if price_data is None:
                continue

            px = self._exec_price(price_data, "sell")
            if px is None:
                continue

            pos = self.positions[code]
            amount = px * pos.shares
            cost = amount * self.cost.stamp_duty + max(
                amount * self.cost.commission, self.cost.min_commission
            )
            pnl = (px - pos.cost_basis) * pos.shares - cost

            self.cash += amount - cost
            self.trades.append(Trade(
                date=date, code=code, action="sell",
                price=px, shares=pos.shares,
                amount=amount, cost=cost, pnl=pnl,
            ))
            del self.positions[code]

    def _process_buys(self, date: str, available: float):
        """处理当日买入信号。"""
        if self.predictions is None:
            return

        pred_today = self.predictions[self.predictions["date"] == date]
        buy_signals = pred_today[pred_today["prediction"] == "up"]
        buy_signals = buy_signals.sort_values("confidence", ascending=False)

        current_positions = len([p for p in self.positions.values() if p.shares > 0])
        slots = self.top_n - current_positions
        if slots <= 0:
            return

        max_per_stock = available * self.max_position_pct

        for _, signal in buy_signals.iterrows():
            if slots <= 0:
                break

            code = signal["code"]
            if code in self.positions and self.positions[code].shares > 0:
                continue

            price_data = self._get_price(code, date)
            if price_data is None:
                continue

            px = self._exec_price(price_data, "buy")
            if px is None:
                continue

            # 按手买入（100 股整数倍）
            target_amount = min(max_per_stock, available * 0.5)
            shares = int(target_amount / px / 100) * 100
            if shares < 100:
                continue

            amount = px * shares
            cost = amount * self.cost.commission  # A股买入无印花税
            total = amount + cost

            if total > available:
                continue

            self.cash -= total
            self.positions[code] = Position(
                code=code, shares=shares, cost_basis=px, buy_date=date,
            )
            self.trades.append(Trade(
                date=date, code=code, action="buy",
                price=px, shares=shares, amount=amount, cost=cost,
            ))
            slots -= 1

    def _market_value(self, date: str) -> float:
        """计算总持仓市值。"""
        total = 0.0
        for code, pos in self.positions.items():
            if pos.shares <= 0:
                continue
            price_data = self._get_price(code, date)
            if price_data:
                total += price_data["close"] * pos.shares
        return total

    def _sellable_value(self, date: str) -> float:
        """可卖出持仓市值（排除 T+1 锁定的）。"""
        total = 0.0
        for code, pos in self.positions.items():
            if pos.shares <= 0 or pos.buy_date == date:
                continue
            price_data = self._get_price(code, date)
            if price_data:
                total += price_data["close"] * pos.shares
        return total

    def _unlock_positions(self, date: str):
        """解锁：当日买入的持仓次日可卖（不需要额外操作，
        因为 _process_sells 中比较 buy_date != date）。"""
        pass

    # ── 评估指标 ─────────────────────────────────────────────────
    def report(self) -> dict:
        """生成回测报告。"""
        if not self.nav_history:
            return {"error": "无回测记录"}

        nav_df = pd.DataFrame(self.nav_history)
        nav_df["date"] = pd.to_datetime(nav_df["date"])
        nav_df = nav_df.sort_values("date")

        # 日收益率
        nav_df["daily_ret"] = nav_df["nav"].pct_change()

        # 累计收益
        final_nav = nav_df["nav"].iloc[-1]
        cumulative_return = (final_nav / self.initial_capital - 1)

        # 年化收益
        days = (nav_df["date"].iloc[-1] - nav_df["date"].iloc[0]).days
        years = max(days / 365, 0.02)  # 最少 0.02 年避免除零
        annualized_return = (1 + cumulative_return) ** (1 / years) - 1

        # 夏普比率（假设无风险利率 2.5%）
        rf_daily = 0.025 / 252
        excess = nav_df["daily_ret"].dropna() - rf_daily
        sharpe = np.sqrt(252) * excess.mean() / (excess.std() + 1e-8)

        # 最大回撤
        peak = nav_df["nav"].expanding().max()
        drawdown = (nav_df["nav"] - peak) / peak
        max_drawdown = drawdown.min()

        # 胜率 & 盈亏比
        sell_trades = [t for t in self.trades if t.action == "sell"]
        wins = [t for t in sell_trades if t.pnl > 0]
        win_rate = len(wins) / len(sell_trades) if sell_trades else 0
        total_profit = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in sell_trades if t.pnl <= 0))
        profit_factor = total_profit / (total_loss + 1e-8)

        # 交易统计
        buy_trades = [t for t in self.trades if t.action == "buy"]
        total_cost = sum(t.cost for t in self.trades)

        report = {
            "initial_capital": self.initial_capital,
            "final_nav": round(float(final_nav), 2),
            "cumulative_return": round(float(cumulative_return), 4),
            "annualized_return": round(float(annualized_return), 4),
            "sharpe_ratio": round(float(sharpe), 4),
            "max_drawdown": round(float(max_drawdown), 4),
            "win_rate": round(float(win_rate), 4),
            "profit_factor": round(float(profit_factor), 4),
            "total_trades": len(self.trades),
            "buy_count": len(buy_trades),
            "sell_count": len(sell_trades),
            "total_cost": round(float(total_cost), 2),
            "days": days,
            "nav_curve": [
                {"date": str(r["date"].date()), "nav": r["nav"]}
                for _, r in nav_df.iterrows()
            ],
        }

        self._print_report(report)
        return report

    def _print_report(self, r: dict):
        """打印回测报告。"""
        print()
        print("=" * 60)
        print("  回测报告")
        print("=" * 60)
        print(f"  初始资金:     {r['initial_capital']:>12,.0f}")
        print(f"  最终净值:     {r['final_nav']:>12,.0f}")
        print(f"  累计收益率:   {r['cumulative_return']:>+11.2%}")
        print(f"  年化收益率:   {r['annualized_return']:>+11.2%}")
        print(f"  夏普比率:     {r['sharpe_ratio']:>12.2f}")
        print(f"  最大回撤:     {r['max_drawdown']:>+11.2%}")
        print(f"  胜率:         {r['win_rate']:>11.1%}")
        print(f"  盈亏比:       {r['profit_factor']:>12.2f}")
        print(f"  交易次数:     {r['total_trades']:>12}")
        print(f"  交易成本:     {r['total_cost']:>12,.0f}")
        print(f"  回测天数:     {r['days']:>12}")
        print("=" * 60)


# ── 便捷函数 ────────────────────────────────────────────────────

def backtest_single(code: str, data_dir: str,
                    initial_capital: float = 100000) -> dict:
    """对单只股票做简单回测（使用随机信号模拟）。"""
    engine = BacktestEngine(initial_capital=initial_capital, top_n=1)

    # 加载数据
    market = "sh" if code.startswith(("6", "9")) else "sz"
    full_dir = os.path.join(data_dir, market)
    engine.load_csv_data(full_dir, [code])

    if code not in engine.price_data:
        return {"error": f"未找到数据: {code}"}

    # 生成简单模拟信号：随机 buy/hold
    import random
    random.seed(42)
    df = engine.price_data[code]
    dates = df["date"].dt.strftime("%Y-%m-%d")
    preds = pd.DataFrame({
        "date": dates,
        "code": code,
        "prediction": [random.choice(["up", "down"]) for _ in range(len(dates))],
        "confidence": [random.uniform(0.5, 0.9) for _ in range(len(dates))],
    })
    engine.load_predictions(preds)

    return engine.run(verbose=False)


if __name__ == "__main__":
    # 自测：用 mock 数据跑回测
    from data_fetcher import DataFetcher

    print("=== 回测框架自测（mock 数据）===\n")

    # 生成 3 只股票的 mock 数据
    codes = ["600000", "600001", "000001"]
    price_data = {}
    np.random.seed(99)

    base_prices = {"600000": 15.0, "600001": 8.0, "000001": 25.0}
    for code in codes:
        df = DataFetcher.mock_kline(252, stock_code=code)
        # 调整基准价格到合理范围
        ratio = base_prices[code] / df["close"].iloc[0]
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] * ratio
        price_data[code] = df

    engine = BacktestEngine(initial_capital=100000, top_n=2)
    engine.load_price_data(price_data)

    # 生成随机预测信号
    import random
    random.seed(42)
    all_preds = []
    for code in codes:
        df = price_data[code]
        for _, row in df.iterrows():
            all_preds.append({
                "date": str(row["date"].date()),
                "code": code,
                "prediction": random.choice(["up", "down"]),
                "confidence": random.uniform(0.5, 0.95),
            })
    engine.load_predictions(pd.DataFrame(all_preds))

    report = engine.run(verbose=True)
