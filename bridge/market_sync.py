# -*- coding: utf-8 -*-
"""全市场日线增量同步 CLI（D1.4 轻量版数据地基）。

用法（需 miniQMT 已连接）：
    python market_sync.py                # 全市场同步
    python market_sync.py --limit 50     # 试跑前 50 只
    python market_sync.py --period 1d    # 指定周期

幂等可反复执行；单标的失败不中断，结果打印错误列表。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.datafeed import sync_market


def main():
    ap = argparse.ArgumentParser(description='全市场日线增量同步（D1.4）')
    ap.add_argument('--limit', type=int, default=None, help='只同步前 N 只（试跑）')
    ap.add_argument('--period', default='1d', help='周期（默认 1d）')
    args = ap.parse_args()

    result = sync_market(
        limit=args.limit,
        period=args.period,
        progress=lambda done, total, code: print(
            '[{}/{}] {}'.format(done, total, code)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
