"""
数据质量校验模块。

对 K 线 DataFrame 做三项检查：
  1. detect_halt()     — 停牌检测（连续相同 OHLC 超过阈值天数）
  2. filter_outliers() — 异常值过滤（日收益率 > 阈值 或 volume = 0）
  3. detect_missing_dates() — 缺失交易日检测

用法：
    from data_validator import DataValidator
    dv = DataValidator(df)
    issues = dv.run_all()
"""

import sys
sys.path.insert(0, r"D:\software\python\Lib\site-packages")

import pandas as pd
import numpy as np
from typing import Optional


DEFAULT_HALT_DAYS = 5          # 连续相同 OHLC 天数阈值
DEFAULT_RETURN_THRESHOLD = 0.2  # 日收益率异常阈值（20%）


class DataValidator:
    """数据质量校验器，对单个股票的 K 线 DataFrame 做检查。"""

    def __init__(self, df: pd.DataFrame, code: str = ""):
        """
        :param df: K 线 DataFrame，需包含 date/open/high/low/close/volume 列
        :param code: 股票代码（用于报错信息）
        """
        if df.empty:
            raise ValueError("DataFrame is empty")
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        self.df = df.sort_values("date").reset_index(drop=True)
        self.code = code
        self.issues: list[dict] = []

    # ── 1. 停牌检测 ─────────────────────────────────────────────
    def detect_halt(self, threshold_days: int = DEFAULT_HALT_DAYS) -> list[dict]:
        """检测连续相同 OHLC 超过阈值天数（疑似停牌或数据损坏）。

        逻辑：找到 open == high == low == close 的连续区间，
        如果区间长度 >= threshold_days，记录为停牌区。
        """
        halt_periods = []
        is_flat = (
            (self.df["open"] == self.df["high"])
            & (self.df["high"] == self.df["low"])
            & (self.df["low"] == self.df["close"])
        )

        in_halt = False
        start_idx = 0
        for i, flat in enumerate(is_flat):
            if flat and not in_halt:
                in_halt = True
                start_idx = i
            elif not flat and in_halt:
                in_halt = False
                span = i - start_idx
                if span >= threshold_days:
                    halt_periods.append({
                        "type": "halt",
                        "start": str(self.df.iloc[start_idx]["date"].date()),
                        "end": str(self.df.iloc[i - 1]["date"].date()),
                        "days": span,
                        "code": self.code,
                    })
        if in_halt:
            span = len(is_flat) - start_idx
            if span >= threshold_days:
                halt_periods.append({
                    "type": "halt",
                    "start": str(self.df.iloc[start_idx]["date"].date()),
                    "end": str(self.df.iloc[-1]["date"].date()),
                    "days": span,
                    "code": self.code,
                })

        self.issues.extend(halt_periods)
        return halt_periods

    # ── 2. 异常值过滤 ───────────────────────────────────────────
    def filter_outliers(self,
                        return_threshold: float = DEFAULT_RETURN_THRESHOLD
                        ) -> list[dict]:
        """检测日收益率异常和成交量为零的行。

        :param return_threshold: 日收益率绝对值超过此值视为异常
        """
        outliers = []
        daily_ret = self.df["close"].pct_change()

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            date_str = str(row["date"].date())
            ret = daily_ret.iloc[i]

            # 检查日收益率异常
            if not pd.isna(ret) and abs(ret) > return_threshold:
                outliers.append({
                    "type": "outlier_return",
                    "date": date_str,
                    "return": round(float(ret), 4),
                    "close": round(float(row["close"]), 2),
                    "code": self.code,
                })

            # 检查成交量为零
            if row["volume"] == 0:
                outliers.append({
                    "type": "outlier_zero_volume",
                    "date": date_str,
                    "code": self.code,
                })

        self.issues.extend(outliers)
        return outliers

    # ── 3. 缺失交易日检测 ───────────────────────────────────────
    def detect_missing_dates(self) -> list[dict]:
        """检测日期序列中的缺失交易日。

        通过计算日期间隔来发现跳空（缺失天数 > 预期最大间隔）。
        注意：不导入交易日历，仅用日期间隔推算。
        """
        gaps = []
        dates = pd.to_datetime(self.df["date"])
        max_expected_gap = 4  # 周末+节假日最多约4天

        for i in range(1, len(dates)):
            gap_days = (dates.iloc[i] - dates.iloc[i - 1]).days
            if gap_days > max_expected_gap:
                gaps.append({
                    "type": "missing_dates",
                    "from": str(dates.iloc[i - 1].date()),
                    "to": str(dates.iloc[i].date()),
                    "gap_days": gap_days,
                    "missing_estimate": gap_days - 1,
                    "code": self.code,
                })

        self.issues.extend(gaps)
        return gaps

    # ── 综合运行 ────────────────────────────────────────────────
    def run_all(self, halt_days: int = DEFAULT_HALT_DAYS,
                return_threshold: float = DEFAULT_RETURN_THRESHOLD) -> dict:
        """运行全部校验，返回汇总报告。"""
        self.issues = []
        halt = self.detect_halt(threshold_days=halt_days)
        outliers = self.filter_outliers(return_threshold=return_threshold)
        gaps = self.detect_missing_dates()

        by_type = {"halt": len(halt), "outlier": len(outliers), "gap": len(gaps)}
        return {
            "code": self.code,
            "total_rows": len(self.df),
            "date_range": (
                str(self.df["date"].iloc[0].date()),
                str(self.df["date"].iloc[-1].date()),
            ),
            "issues_found": len(self.issues),
            "by_type": by_type,
            "issues": self.issues,
            "is_clean": len(self.issues) == 0,
        }


def validate_dataframe(df: pd.DataFrame, code: str = "",
                       halt_days: int = DEFAULT_HALT_DAYS,
                       return_threshold: float = DEFAULT_RETURN_THRESHOLD) -> dict:
    """便捷函数：对单个 DataFrame 做全量校验。"""
    dv = DataValidator(df, code=code)
    return dv.run_all(halt_days=halt_days, return_threshold=return_threshold)


if __name__ == "__main__":
    # 自测：用 mock 数据跑一遍
    from data_fetcher import DataFetcher
    df = DataFetcher.mock_kline(200)
    report = validate_dataframe(df, code="000001.SZ")
    print(f"股票: {report['code']}")
    print(f"行数: {report['total_rows']}")
    print(f"日期范围: {report['date_range']}")
    print(f"问题数: {report['issues_found']}  分类: {report['by_type']}")
    print(f"数据质量: {'✓ 干净' if report['is_clean'] else '✗ 有问题'}")
    if report["issues"]:
        for issue in report["issues"][:5]:
            print(f"  - {issue}")
