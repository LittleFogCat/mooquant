# -*- coding: utf-8 -*-
"""mookquant · 诊断包导出（D6.2）：一键收集日志/配置/版本/回测摘要/环境 → zip。

用于排查与支持：把 data/logs、config/default.json、最近回测结果摘要、
运行环境信息打包为 data/diagnostics/diagnose_*.zip。

CLI：
    python diagnose.py            # 导出诊断包并打印路径
"""
import json
import os
import sys
import time
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(PROJECT_ROOT, 'data', 'logs')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config', 'default.json')
RESULTS_DIR = os.path.join(PROJECT_ROOT, 'data', 'backtest_results')
OUT_DIR = os.path.join(PROJECT_ROOT, 'data', 'diagnostics')


def _collect_env():
    """运行环境摘要（不含任何密钥/敏感配置）。"""
    import platform
    env = {
        'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'platform': platform.platform(),
        'python': sys.version.split()[0],
        'qmt_port': os.environ.get('QMT_PORT', ''),
    }
    try:
        cfgjs = os.path.join(PROJECT_ROOT, 'config.js')
        if os.path.exists(cfgjs):
            with open(cfgjs, encoding='utf-8') as f:
                for line in f:
                    if 'version' in line and "'" in line:
                        env['version'] = line.split("'")[1]
                        break
    except Exception:
        pass
    return env


def build_diagnostic_package(out_dir=None):
    """构建诊断包 zip，返回文件路径。"""
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    path = os.path.join(out_dir, 'diagnose_{}.zip'.format(stamp))

    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # 日志
        if os.path.isdir(LOGS_DIR):
            for fn in sorted(os.listdir(LOGS_DIR)):
                if fn.endswith('.log'):
                    try:
                        zf.write(os.path.join(LOGS_DIR, fn), os.path.join('logs', fn))
                    except Exception:
                        pass
        # 配置（不含密钥字段由调用方裁剪；默认配置通常无密钥）
        if os.path.exists(CONFIG_PATH):
            try:
                zf.write(CONFIG_PATH, 'config/default.json')
            except Exception:
                pass
        # 环境摘要
        zf.writestr('env.json', json.dumps(_collect_env(), ensure_ascii=False, indent=2))
        # 最近回测摘要（前 20 条）
        summaries = []
        if os.path.isdir(RESULTS_DIR):
            for fn in sorted(os.listdir(RESULTS_DIR), reverse=True)[:20]:
                if not fn.endswith('.json'):
                    continue
                try:
                    with open(os.path.join(RESULTS_DIR, fn), encoding='utf-8') as f:
                        snap = json.load(f)
                    m = snap.get('metrics', {})
                    summaries.append({
                        'id': snap.get('id'),
                        'createdAt': snap.get('createdAt'),
                        'symbol': snap.get('symbol'),
                        'strategy': snap.get('strategy'),
                        'totalReturn': m.get('totalReturn'),
                        'maxDrawdown': m.get('maxDrawdown'),
                        'totalTrades': m.get('totalTrades'),
                    })
                except Exception:
                    continue
        zf.writestr('backtest_summary.json',
                    json.dumps(summaries, ensure_ascii=False, indent=2))
    return path


def main():
    path = build_diagnostic_package()
    print('诊断包已导出: {}'.format(path))


if __name__ == '__main__':
    main()
