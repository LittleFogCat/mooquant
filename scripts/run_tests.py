"""
quantdemo - 一键测试入口

运行所有测试：
    python scripts/run_tests.py

仅运行离线测试（不需要启动 QMT）：
    python scripts/run_tests.py --offline

仅运行 QMT 在线测试：
    python scripts/run_tests.py --online
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

# 压制 xtquant 的 pkg_resources 弃用警告
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="xtquant")

import argparse
from pathlib import Path


def header(title: str):
    """打印带分隔线的标题"""
    print()
    print("=" * 56)
    print(f"  {title}")
    print("=" * 56)


def test_01_env_check():
    """测试1：环境检测"""
    header("测试1: QMT 环境检测")
    from qmt_env import full_check
    result = full_check()

    xt = result["xtquant"]
    print(f"  xtquant  : {'[OK] v' + xt['version'] if xt['ok'] else '[X] 未安装'}")

    proc = result["qmt_process"]
    print(f"  QMT进程  : {'[OK] 运行中' if proc['ok'] else '[-] 未启动'}")

    ds = result["data_service"]
    print(f"  数据服务  : {'[OK] ' + ds['msg'] if ds['ok'] else '[-] ' + ds['msg']}")

    ok = xt["ok"]
    print(f"\n  -> 环境状态: {'[OK] 就绪' if ok else '[X] 缺失组件'}")

    if ok and proc["ok"]:
        print("  [*] QMT 已运行，可进行在线测试")
    elif ok and not proc["ok"]:
        print("  [*] 请先启动 QMT 客户端再跑在线测试")

    return result


def test_02_mock_data():
    """测试2：模拟数据生成"""
    header("测试2: 模拟数据生成")
    from data_fetcher import DataFetcher
    df = DataFetcher.mock_kline(100)
    print(f"  形状: {df.shape[0]} 行 x {df.shape[1]} 列")
    print(f"  字段: {list(df.columns)}")
    print(f"  日期范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    print(f"  收盘价范围: {df['close'].min():.2f} ~ {df['close'].max():.2f}")
    print("  [OK] 模拟数据生成成功")
    return {"ok": True, "shape": df.shape}


def test_03_ai_model_offline():
    """测试3：离线 AI 模型训练与预测"""
    header("测试3: AI 模型离线训练")
    from ai_strategy import offline_predict
    result = offline_predict(show_detail=True)
    print(f"\n  [OK] AI 策略离线测试完成")
    return result


def test_04_qmt_data_online():
    """测试4：QMT 在线数据获取（需 QMT 运行）"""
    header("测试4: QMT 在线数据获取")
    from qmt_env import check_qmt_process, connect_data

    proc = check_qmt_process()
    if not proc["ok"]:
        print("  [-] QMT 未运行，跳过在线测试")
        return {"ok": False, "msg": "QMT not running"}

    conn = connect_data()
    if not conn["ok"]:
        print(f"  [X] 数据服务连接失败: {conn['msg']}")
        return conn

    from qmt_env import get_markets
    markets = get_markets()
    if markets["ok"]:
        market_list = ", ".join(f"{k}({v})" for k, v in markets["markets"].items()[:8])
        print(f"  [OK] 已连接，支持市场: {market_list}...")
    return markets


def main():
    parser = argparse.ArgumentParser(description="QuantDemo 测试入口")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline", action="store_true", help="仅跑离线测试")
    group.add_argument("--online", action="store_true", help="仅跑在线测试（需 QMT）")
    args = parser.parse_args()

    print("=" * 50)
    print("  QuantDemo - AI 量化测试套件")
    print("=" * 50)

    if args.online:
        test_01_env_check()
        test_04_qmt_data_online()
    elif args.offline:
        test_01_env_check()
        test_02_mock_data()
        test_03_ai_model_offline()
    else:
        test_01_env_check()
        test_02_mock_data()
        test_03_ai_model_offline()
        test_04_qmt_data_online()

    print()
    print("=" * 56)
    print("  所有测试完成！")
    print("=" * 56)


if __name__ == "__main__":
    main()
