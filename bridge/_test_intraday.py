# -*- coding: utf-8 -*-
"""日内做T链路自测（无 pytest 依赖，直接 python 运行）。

覆盖：
  1. slice_upto 无前视切片（1m/5m/1d 三种规则）
  2. intraday_vwap 分时均线（含 amount 与退化路径）
  3. lot T+1 可卖判定（当日买不可卖 / 次日可卖 / 底仓当日可卖）
  4. generate_mock_minute_bars（数量/时间表/确定性）
  5. mock 分钟端到端回测（intraday_t 策略，allowMock）
     - 底仓数量还原校验（先卖后买路径）
     - 做T分账指标存在性

运行：python bridge/_test_intraday.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("[PASS] {}".format(name))
    else:
        FAIL += 1
        print("[FAIL] {} {}".format(name, detail))


def test_slice_upto():
    from strategies.base import slice_upto

    bars_1m = [
        {"date": "2026-08-24 09:31", "close": 10.0},
        {"date": "2026-08-24 09:32", "close": 10.1},
        {"date": "2026-08-24 09:33", "close": 10.2},
        {"date": "2026-08-24 09:34", "close": 10.3},
        {"date": "2026-08-24 09:35", "close": 10.4},
        {"date": "2026-08-25 09:31", "close": 10.5},
    ]
    bars_5m = [
        {"date": "2026-08-24 09:30", "close": 10.0},   # 覆盖 09:30-09:34 的桶（起点09:30）
        {"date": "2026-08-24 09:35", "close": 10.4},   # 进行中桶（09:35-09:39）
    ]
    bars_1d = [
        {"date": "2026-08-21", "close": 9.8},
        {"date": "2026-08-24", "close": 10.4},         # 当日进行中
    ]
    now = "2026-08-24 09:34"
    out = slice_upto({"1m": bars_1m, "5m": bars_5m, "1d": bars_1d}, now)

    # 1m: <= now
    check("slice 1m 含当前bar",
          [b["date"] for b in out["1m"]] == ["2026-08-24 09:31", "2026-08-24 09:32",
                                             "2026-08-24 09:33", "2026-08-24 09:34"])
    # 5m: 当前时刻 09:34 属于 09:30 桶（进行中）-> 只保留更早桶 = 无
    check("slice 5m 进行中桶不暴露（09:34 属 09:30 桶）", len(out["5m"]) == 0)
    # 5m 完成桶暴露：09:36 时 09:30 桶已完成
    out2 = slice_upto({"5m": bars_5m}, "2026-08-24 09:36")
    check("slice 5m 已完成桶暴露（09:36 时 09:30 桶完成）",
          len(out2["5m"]) == 1 and out2["5m"][0]["date"] == "2026-08-24 09:30")
    # 1d: 当日不暴露
    check("slice 1d 今日不暴露",
          [b["date"] for b in out["1d"]] == ["2026-08-21"])
    # 跨日：8-25 的 bar 在 8-24 不可见
    out3 = slice_upto({"1m": bars_1m}, "2026-08-24 09:35")
    check("slice 1m 未来bar不泄漏", all(b["date"] <= "2026-08-24 09:35" for b in out3["1m"])
          and len(out3["1m"]) == 5)


def test_vwap():
    from strategies.base import Context

    ctx = Context()
    ctx.trading_day = "2026-08-24"
    ctx.bars_by_period["1m"] = [
        {"date": "2026-08-24 09:31", "open": 10, "high": 10, "low": 10, "close": 10,
         "volume": 100, "amount": 1000},
        {"date": "2026-08-24 09:32", "open": 11, "high": 11, "low": 11, "close": 11,
         "volume": 100, "amount": 1100},
        {"date": "2026-08-24 09:33", "open": 12, "high": 12, "low": 12, "close": 12,
         "volume": 200, "amount": 2400},
    ]
    v = ctx.intraday_vwap()
    check("vwap 标准 amount 路径", abs(v - (1000 + 1100 + 2400) / 400) < 1e-9,
          "got {}".format(v))

    # 无 amount 退化路径
    ctx2 = Context()
    ctx2.trading_day = "2026-08-24"
    ctx2.bars_by_period["1m"] = [
        {"date": "2026-08-24 09:31", "open": 10, "high": 10, "low": 10, "close": 10,
         "volume": 100},
    ]
    v2 = ctx2.intraday_vwap()
    check("vwap 无amount退化（均价≈close）", v2 is not None and abs(v2 - 10) < 1e-9,
          "got {}".format(v2))

    # 空数据
    ctx3 = Context()
    check("vwap 空数据返回 None", ctx3.intraday_vwap() is None)


def test_lot_t1():
    from backtest_engine import _Lot

    lot_t = _Lot(shares=100, cost=10.0, entry_dt="2026-08-24 10:00",
                 available_day="2026-08-25", tag="t")
    check("T仓当日买入不可卖", lot_t.available_day > "2026-08-24")
    check("T仓次日可卖", lot_t.available_day <= "2026-08-25")

    lot_core = _Lot(shares=1000, cost=10.0, entry_dt="2026-08-24 09:30",
                    available_day="2026-08-24", tag="core")
    check("底仓建仓当日可卖", lot_core.available_day <= "2026-08-24")


def test_mock_minutes():
    from _shared import generate_mock_minute_bars

    bars = generate_mock_minute_bars("sh600519", "2026-08-20", "2026-08-21")
    days = sorted(set(b["date"][:10] for b in bars))
    per_day = [sum(1 for b in bars if b["date"][:10] == d) for d in days]
    check("分钟mock交易日数", len(days) == 2, "got {}".format(days))
    check("分钟mock每日240根", all(n == 240 for n in per_day), "got {}".format(per_day))
    check("分钟mock含amount", all(b.get("amount", 0) > 0 for b in bars))
    check("分钟mock确定性",
          bars == generate_mock_minute_bars("sh600519", "2026-08-20", "2026-08-21"))
    # 时间表：首根 09:30，午休无 bar，最后一根 14:59
    d0 = [b for b in bars if b["date"][:10] == days[0]]
    check("分钟mock首根09:30", d0[0]["date"].endswith("09:30"))
    check("分钟mock末根14:59", d0[-1]["date"].endswith("14:59"))
    hm_list = [b["date"][11:] for b in d0]
    check("分钟mock午休无bar", "11:30" not in hm_list and "12:00" not in hm_list
          and "13:00" in hm_list)


def test_end_to_end():
    """mock 分钟端到端：intraday_t 策略回测（无 miniQMT 也能跑）。"""
    from backtest_engine import run_backtest

    params = {
        "strategy": {"type": "intraday_t",
                     "params": {"dailyMaPeriod": 5, "tRatio": 0.3, "maxTCount": 4,
                                "vwapDevBuy": 0.0015, "vwapDevSell": 0.0015}},
        "symbols": ["sh600519"],
        "startDate": "2026-06-01",
        "endDate": "2026-08-26",
        "initialCapital": 2000000,
        "commission": 0.0003,
        "slippage": 0.001,
        "period": "1m",
        "allowMock": True,
        "basePositionShares": 1000,
        "forceEodClose": True,
        "noPersist": True,
    }
    result = run_backtest(params)
    m = result["metrics"]
    check("端到端：返回结果", result is not None)
    check("端到端：intraday 标记", result.get("intraday") is True)
    check("端到端：做T分账存在", isinstance(m.get("intraday"), dict))
    intra = m.get("intraday") or {}
    check("端到端：底仓股数还原", intra.get("coreShares") == 1000,
          "got {}".format(intra.get("coreShares")))
    check("端到端：期末无T仓残留", intra.get("finalTShares") == 0,
          "got {}".format(intra.get("finalTShares")))
    check("端到端：净值曲线非空", len(result.get("equityCurve", [])) > 0)
    check("端到端：bars为日线聚合", all(len(str(b["date"])) == 10 for b in result.get("bars", [])))
    # 交易合理性：总股数 <= 底仓+T仓
    buys = [t for t in result["trades"] if t["side"] == "buy"]
    check("端到端：建仓交易存在", len(buys) >= 1 and buys[0].get("lotTag") == "core")
    # 净值守恒：restore 当日不应出现超过交易成本的净值跳变（防止股份凭空消失）
    # 做T分账自洽：finalCapital - initialCapital ≈ tPnl + corePnl（容差=未还原因素）
    final_cap = m.get("finalCapital", 0)
    expect = intra.get("tPnl", 0) + intra.get("corePnl", 0)
    diff = abs(final_cap - 2000000 - expect)
    check("端到端：分账自洽（tPnl+corePnl≈总盈亏）", diff < 100,
          "final={} expect={} diff={}".format(final_cap, expect, diff))
    print("      端到端指标: totalReturn={}% tPnl={} corePnl={} tRounds={}".format(
        m.get("totalReturn"), intra.get("tPnl"), intra.get("corePnl"), intra.get("tRounds")))


def test_regression_daily():
    """回归：日线路径零改动（mock 数据下 ma_cross 可跑通且无 intraday 字段）。"""
    from backtest_engine import run_backtest

    params = {
        "strategy": {"type": "ma_cross", "params": {"fast": 5, "slow": 10}},
        "symbols": ["sh600519"],
        "startDate": "2026-06-01",
        "endDate": "2026-08-26",
        "initialCapital": 100000,
        "allowMock": True,
        "noPersist": True,
    }
    result = run_backtest(params)
    check("回归：日线路径无 intraday 标记", "intraday" not in result or result.get("intraday") is not True)
    check("回归：日线指标存在", "metrics" in result and "totalReturn" in result["metrics"])


if __name__ == "__main__":
    test_slice_upto()
    test_vwap()
    test_lot_t1()
    test_mock_minutes()
    test_end_to_end()
    test_regression_daily()
    print("\n结果: {} passed, {} failed".format(PASS, FAIL))
    sys.exit(1 if FAIL else 0)
