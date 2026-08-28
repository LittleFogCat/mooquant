# -*- coding: utf-8 -*-
"""D1.4 数据健康监控与全市场同步测试（轻量版，无新依赖）。

用 monkeypatch 把 SQLite 指向临时目录，种子 stocks 表与日线缓存。
"""
import pytest


def _seed(monkeypatch, tmp_path, stocks=None, bars=None):
    import db as db_cache
    monkeypatch.setattr(db_cache, 'DB_PATH', str(tmp_path / 'm.db'))
    monkeypatch.setattr(db_cache, '_initialized', False)
    db_cache.init_db()
    for s in stocks or []:
        db_cache.save_stocks([s])
    for code, rows in (bars or {}).items():
        db_cache.save_bars(code, '1d', rows, 'front_ratio', spec_version=3)
    return db_cache


def _daily(dates):
    return [{'date': d, 'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 1e6}
            for d in dates]


def test_coverage_report_basic(monkeypatch, tmp_path):
    """D1.4：覆盖率/分市场/最新日期/陈旧标的统计。"""
    _seed(monkeypatch, tmp_path,
          stocks=[{'code': '600000.SH', 'xtcode': '600000.SH', 'name': 'A', 'exchange': 'SH'},
                  {'code': '000001.SZ', 'xtcode': '000001.SZ', 'name': 'B', 'exchange': 'SZ'},
                  {'code': '600001.SH', 'xtcode': '600001.SH', 'name': 'C', 'exchange': 'SH'}],
          bars={'600000.SH': _daily(['2026-01-01', '2026-01-02']),
                '000001.SZ': _daily(['2026-01-01'])})
    from data import datafeed
    rep = datafeed.coverage_report(ref_date='2026-01-10')
    assert rep['total_stocks'] == 3
    assert rep['covered_stocks'] == 2
    assert abs(rep['coverage_rate'] - 0.6667) < 1e-3  # round(2/3, 4)
    assert rep['newest_date'] == '2026-01-02'
    assert rep['stale_stocks'] >= 2  # 参考日 01-10，01-01/02 超 7 天
    assert rep['by_market']['SH']['total'] == 2
    assert rep['by_market']['SZ']['covered'] == 1


def test_coverage_report_index_bars_not_counted_as_stocks(monkeypatch, tmp_path):
    """D1.4：指数等非股票缓存不计入股票覆盖率。"""
    _seed(monkeypatch, tmp_path,
          stocks=[{'code': '600000.SH', 'xtcode': '600000.SH', 'name': 'A', 'exchange': 'SH'}],
          bars={'600000.SH': _daily(['2026-01-01']),
                '000300.SH': _daily(['2026-01-01', '2026-01-02'])})
    from data import datafeed
    rep = datafeed.coverage_report(ref_date='2026-01-03')
    assert rep['total_stocks'] == 1
    assert rep['covered_stocks'] == 1
    assert rep['newest_date'] == '2026-01-02'  # 指数也计入"最新日期"参考


def test_coverage_report_problems_on_low_coverage(monkeypatch, tmp_path):
    """D1.4：覆盖率过低与数据陈旧给出人话问题。"""
    _seed(monkeypatch, tmp_path,
          stocks=[{'code': '600000.SH', 'xtcode': '600000.SH', 'name': 'A', 'exchange': 'SH'},
                  {'code': '600001.SH', 'xtcode': '600001.SH', 'name': 'C', 'exchange': 'SH'},
                  {'code': '600002.SH', 'xtcode': '600002.SH', 'name': 'D', 'exchange': 'SH'}],
          bars={'600000.SH': _daily(['2026-01-01', '2026-01-02'])})
    from data import datafeed
    rep = datafeed.coverage_report(ref_date='2026-01-03')
    assert any('覆盖率' in p for p in rep['problems'])  # 1/3 = 33% < 50%
    # 数据新鲜时无陈旧提示
    rep2 = datafeed.coverage_report(ref_date='2026-01-02')
    assert not any('未更新' in p for p in rep2['problems'])


def test_sync_market_counts(monkeypatch, tmp_path):
    """D1.4：全市场同步计数（有数据/无数据/失败），单标的失败不中断。"""
    _seed(monkeypatch, tmp_path,
          stocks=[{'code': '600000.SH', 'xtcode': '600000.SH', 'name': 'A', 'exchange': 'SH'},
                  {'code': '000001.SZ', 'xtcode': '000001.SZ', 'name': 'B', 'exchange': 'SZ'},
                  {'code': '600002.SH', 'xtcode': '600002.SH', 'name': 'X', 'exchange': 'SH'}])
    from data import datafeed
    real = {'600000.SH': _daily(['2026-01-01']),
            '000001.SZ': _daily(['2026-01-02', '2026-01-03']),
            '600002.SH': []}

    def fake_fetch(code, period='1d', count=None, start_date=None, end_date=None,
                   dividend_type='front_ratio', **k):
        return (real.get(code, []), 'xtquant' if real.get(code) else 'empty')

    monkeypatch.setattr(datafeed, 'fetch_bars', fake_fetch)
    seen = []
    res = datafeed.sync_market(progress=lambda d, t, c: seen.append(c))
    assert res['total'] == 3
    assert res['synced'] == 2
    assert res['skipped'] == 1
    assert res['updated_bars'] == 3
    assert len(seen) == 3


def test_sync_market_limit(monkeypatch, tmp_path):
    """D1.4：--limit 只同步前 N 只。"""
    _seed(monkeypatch, tmp_path,
          stocks=[{'code': '600000.SH', 'xtcode': '600000.SH', 'name': 'A', 'exchange': 'SH'},
                  {'code': '000001.SZ', 'xtcode': '000001.SZ', 'name': 'B', 'exchange': 'SZ'}])
    from data import datafeed
    monkeypatch.setattr(datafeed, 'fetch_bars',
                        lambda code, period='1d', count=None, start_date=None,
                        end_date=None, dividend_type='front_ratio', **k:
                        (_daily(['2026-01-01']), 'xtquant'))
    res = datafeed.sync_market(limit=1)
    assert res['total'] == 1
    assert res['synced'] == 1
