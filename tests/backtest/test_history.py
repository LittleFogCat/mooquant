# -*- coding: utf-8 -*-
"""回测历史管理（D4.3）测试：结果列表 / A/B 对比。

用 monkeypatch 把结果目录指向临时目录，隔离真实 data/backtest_results。
"""
import json
import os
import pytest

import backtest_engine as be


def _write_snapshot(result_dir, bid, **fields):
    snap = {
        "id": bid,
        "createdAt": "2026-08-24T10:00:00",
        "symbol": "sh600000",
        "strategy": "ma_cross",
        "startDate": "2024-01-01",
        "endDate": "2024-06-30",
        "fillModel": "next_open",
        "metrics": {"totalReturn": 1.0, "maxDrawdown": -2.0, "totalTrades": 3},
    }
    snap.update(fields)
    with open(os.path.join(result_dir, bid + ".json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)


def test_list_results_sorted_and_filtered(tmp_path, monkeypatch):
    """D4.3：列表按时间倒序、返回摘要字段、跳过损坏文件。"""
    monkeypatch.setattr(be, "_results_dir", lambda: str(tmp_path))
    _write_snapshot(str(tmp_path), "bt_2", createdAt="2026-08-24T11:00:00",
                    metrics={"totalReturn": 5.0})
    _write_snapshot(str(tmp_path), "bt_1", createdAt="2026-08-24T10:00:00",
                    metrics={"totalReturn": 2.0})
    with open(os.path.join(str(tmp_path), "bt_bad.json"), "w") as f:
        f.write("{not json")

    items = be.list_backtest_results()
    assert [i["backtestId"] for i in items] == ["bt_2", "bt_1"]  # 倒序
    assert items[0]["metrics"]["totalReturn"] == 5.0
    for i in items:
        assert "strategy" in i and "symbol" in i and "fillModel" in i


def test_list_limit(tmp_path, monkeypatch):
    """D4.3：limit 生效。"""
    monkeypatch.setattr(be, "_results_dir", lambda: str(tmp_path))
    for i in range(5):
        _write_snapshot(str(tmp_path), "bt_{}".format(i))
    assert len(be.list_backtest_results(limit=2)) == 2


def test_list_empty_when_no_dir(tmp_path, monkeypatch):
    """D4.3：结果目录不存在时返回空列表。"""
    monkeypatch.setattr(be, "_results_dir", lambda: str(tmp_path / "nope"))
    assert be.list_backtest_results() == []


def test_compare_results(tmp_path, monkeypatch):
    """D4.3：对比返回存在 id 的完整配置与指标，缺失 id 静默跳过。"""
    monkeypatch.setattr(be, "_results_dir", lambda: str(tmp_path))
    _write_snapshot(str(tmp_path), "bt_a", strategyParams={"fast": 5}, risk={"stopLoss": 0.05})
    _write_snapshot(str(tmp_path), "bt_b", strategy="momentum", portfolio=True)

    out = be.compare_backtest_results(["bt_a", "bt_b", "bt_missing"])
    assert [o["backtestId"] for o in out] == ["bt_a", "bt_b"]
    assert out[0]["strategyParams"] == {"fast": 5}
    assert out[0]["risk"] == {"stopLoss": 0.05}
    assert out[0]["metrics"]["totalReturn"] == 1.0
    assert out[1]["portfolio"] is True


def test_compare_empty_ids(tmp_path, monkeypatch):
    """D4.3：空 id 列表返回空。"""
    monkeypatch.setattr(be, "_results_dir", lambda: str(tmp_path))
    assert be.compare_backtest_results([]) == []
