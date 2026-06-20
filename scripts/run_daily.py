"""
每日自动运行流水线 (Phase 4.4 + 4.5)

流程：
  1. 下载通达信日线数据 → 转换为 CSV
  2. 数据质量校验
  3. AI 模型训练（时序交叉验证 + 持久化）
  4. Top-N 选股
  5. 输出报告到 reports/ 目录

用法：
    python scripts/run_daily.py              # 完整流水线
    python scripts/run_daily.py --skip-download   # 跳过下载
    python scripts/run_daily.py --quick           # 快速模式（仅训练+选股）
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config, setup_logging

logger = setup_logging("run_daily")


def step_download_data() -> bool:
    """步骤 1: 下载 + 转换通达信数据。"""
    logger.info("=" * 50)
    logger.info("步骤 1/5: 下载通达信日线数据")
    try:
        from extract_tdx import download, extract_zip, convert
        zip_path = download(config.data.tdx_zip, force=False)
        extract_dir = extract_zip(zip_path, config.data.tdx_extract)
        stats = convert(extract_dir, config.data.root)
        total = sum(stats.values())
        logger.info(f"数据转换完成: {total} 个 CSV")
        return True
    except Exception as e:
        logger.error(f"数据下载失败: {e}")
        return False


def step_validate_data() -> bool:
    """步骤 2: 数据质量校验。"""
    logger.info("=" * 50)
    logger.info("步骤 2/5: 数据质量校验")
    try:
        from extract_tdx import verify_data
        result = verify_data(config.data.tdx_extract, config.data.root)
        total = result["total_checked"]
        ok = result["ok"]
        mismatch = result["mismatch"]
        logger.info(f"校验完成: {total} 文件, ✓={ok}, ✗={mismatch}")
        if mismatch:
            logger.warning(f"发现 {mismatch} 个不一致文件")
        return True
    except Exception as e:
        logger.error(f"数据校验失败: {e}")
        return False


def step_train_model() -> bool:
    """步骤 3: AI 模型训练。"""
    logger.info("=" * 50)
    logger.info("步骤 3/5: AI 模型训练")
    try:
        from ai_strategy import offline_predict
        result = offline_predict(show_detail=False)
        acc = result.get("accuracy", 0)
        logger.info(f"训练完成: 准确率={acc:.2%}, folds={result.get('fold_count', 0)}")
        return True
    except Exception as e:
        logger.error(f"模型训练失败: {e}")
        return False


def step_select_stocks() -> dict | None:
    """步骤 4: Top-N 选股。"""
    logger.info("=" * 50)
    logger.info("步骤 4/5: Top-N 选股")
    try:
        from selector import StockSelector
        selector = StockSelector()
        # 从 CSV 目录加载股票池
        for market in ["sh", "sz", "bj"]:
            mkt_dir = os.path.join(config.data.root, market)
            if os.path.isdir(mkt_dir) and os.listdir(mkt_dir):
                selector.load_stock_pool_from_csv(config.data.root, market)

        if not selector.stock_pool:
            logger.warning("股票池为空，跳过选股")
            return None

        df = selector.select_top_n(n=10, min_confidence=0.55, verbose=False)
        if df.empty:
            logger.warning("无符合条件的股票")
            return None

        logger.info(f"选股完成: {len(df)} 只")
        return df.to_dict(orient="records")
    except Exception as e:
        logger.error(f"选股失败: {e}")
        return None


def step_generate_report(training_result: dict | None = None,
                         picks: list[dict] | None = None) -> str:
    """步骤 5: 生成报告。"""
    logger.info("=" * 50)
    logger.info("步骤 5/5: 生成报告")

    os.makedirs(config.report.dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")

    report = {
        "date": datetime.now().isoformat(),
        "pipeline_version": "1.0",
        "training": training_result,
        "top_picks": picks,
    }

    # JSON 报告
    json_path = os.path.join(config.report.dir, f"daily_{today}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"报告已保存: {json_path}")

    # CSV 选股结果
    if picks:
        import pandas as pd
        csv_path = os.path.join(config.report.dir, f"picks_{today}.csv")
        pd.DataFrame(picks).to_csv(csv_path, index=False, encoding="utf-8-sig")
        logger.info(f"选股CSV: {csv_path}")

    # 控制台摘要
    _print_summary(report)

    # 最新报告链接
    latest_json = os.path.join(config.report.dir, "latest.json")
    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    return json_path


def _print_summary(report: dict):
    """打印控制台摘要。"""
    print()
    print("=" * 60)
    print("  QuantDemo 每日报告")
    print(f"  日期: {report['date'][:10]}")
    print("=" * 60)

    if report.get("training"):
        t = report["training"]
        print(f"  模型准确率: {t.get('accuracy', 'N/A')}")
        print(f"  训练样本:   {t.get('train_samples', 'N/A')}")

    picks = report.get("top_picks") or []
    if picks:
        print(f"\n  Top-{len(picks)} 选股结果:")
        for i, p in enumerate(picks[:10]):
            d = "↑" if p.get("prediction") == "up" else "↓"
            print(f"  {i+1:2d}. {p['code']:>12s} {d}  "
                  f"置信度={p.get('confidence', 0):.2%}  "
                  f"收盘={p.get('close', 0)}")
    else:
        print("\n  (无选股结果)")

    print("=" * 60)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def run_pipeline(skip_download: bool = False,
                 quick: bool = False) -> str | None:
    """执行完整流水线，返回报告路径。"""
    start = datetime.now()
    logger.info(f"流水线启动: {start.isoformat()}")

    results = {"training": None, "picks": None}

    if quick:
        logger.info("快速模式：仅训练 + 选股")
        if step_train_model():
            results["picks"] = step_select_stocks()
    else:
        if not skip_download:
            step_download_data()
        step_validate_data()
        if step_train_model():
            results["picks"] = step_select_stocks()

    # 从模型元数据获取训练结果
    meta_path = os.path.join(config.models_dir, "latest_metadata.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            results["training"] = json.load(f)

    report_path = step_generate_report(
        training_result=results["training"],
        picks=results["picks"],
    )

    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"流水线完成: {elapsed:.0f}s")

    return report_path


def main():
    parser = argparse.ArgumentParser(description="QuantDemo 每日自动流水线")
    parser.add_argument("--skip-download", action="store_true",
                        help="跳过通达信数据下载")
    parser.add_argument("--quick", action="store_true",
                        help="快速模式（仅训练+选股）")
    args = parser.parse_args()

    run_pipeline(skip_download=args.skip_download, quick=args.quick)


if __name__ == "__main__":
    main()
