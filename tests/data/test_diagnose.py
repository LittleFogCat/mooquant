# -*- coding: utf-8 -*-
"""D6 工程化测试：日志落盘（D6.1）+ 诊断包导出（D6.2）。"""
import json
import os
import zipfile

import pytest


def test_log_writes_to_file(tmp_path, monkeypatch):
    """D6.1：日志落盘含时间/级别/模块/内容。"""
    import _logging
    monkeypatch.setattr(_logging, 'LOGS_DIR', str(tmp_path))
    _logging.info('backtest', 'hello 日志内容')
    _logging.error('backtest', 'boom')
    path = os.path.join(str(tmp_path), 'backtest.log')
    assert os.path.exists(path)
    content = open(path, encoding='utf-8').read()
    assert 'hello 日志内容' in content and 'INFO' in content
    assert 'boom' in content and 'ERROR' in content


def test_log_rotation(tmp_path, monkeypatch):
    """D6.1：超过上限轮转保留 .1。"""
    import _logging
    monkeypatch.setattr(_logging, 'LOGS_DIR', str(tmp_path))
    monkeypatch.setattr(_logging, '_MAX_BYTES', 100)
    for i in range(50):
        _logging.info('backtest', 'x' * 20)
    assert os.path.exists(os.path.join(str(tmp_path), 'backtest.log.1'))


def test_diagnose_package(tmp_path, monkeypatch):
    """D6.2：诊断包 zip 含日志/配置/环境/回测摘要。"""
    import diagnose
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'backtest.log').write_text('[2026] INFO backtest x\n', encoding='utf-8')
    cfg = tmp_path / 'config'
    cfg.mkdir()
    (cfg / 'default.json').write_text('{"dataSource":"auto"}\n', encoding='utf-8')
    res = tmp_path / 'results'
    res.mkdir()
    (res / 'bt_1.json').write_text(
        json.dumps({'id': 'bt_1', 'strategy': 'ma_cross', 'metrics': {'totalReturn': 1.2}}),
        encoding='utf-8')
    out = tmp_path / 'out'

    monkeypatch.setattr(diagnose, 'LOGS_DIR', str(logs))
    monkeypatch.setattr(diagnose, 'CONFIG_PATH', str(cfg / 'default.json'))
    monkeypatch.setattr(diagnose, 'RESULTS_DIR', str(res))
    monkeypatch.setattr(diagnose, 'OUT_DIR', str(out))

    zpath = diagnose.build_diagnostic_package()
    assert os.path.exists(zpath) and zpath.endswith('.zip')
    with zipfile.ZipFile(zpath) as zf:
        names = zf.namelist()
        assert 'logs/backtest.log' in names
        assert 'config/default.json' in names
        assert 'env.json' in names
        assert 'backtest_summary.json' in names
        summary = json.loads(zf.read('backtest_summary.json'))
        assert summary[0]['totalReturn'] == 1.2
        env = json.loads(zf.read('env.json'))
        assert 'platform' in env and 'version' in env


def test_backtest_log_routes_to_file(tmp_path, monkeypatch):
    """D6.1：backtest_engine.log 也写文件（集成）。"""
    import _logging
    import backtest_engine
    monkeypatch.setattr(_logging, 'LOGS_DIR', str(tmp_path))
    backtest_engine.log('集成测试日志')
    path = os.path.join(str(tmp_path), 'backtest.log')
    assert os.path.exists(path)
    assert '集成测试日志' in open(path, encoding='utf-8').read()
