"""
从通达信下载日线数据 zip 包，解压后按股票代码分文件转换为 CSV。

用法:
    python scripts/extract_tdx.py                          # 下载 + 解压 + 转换
    python scripts/extract_tdx.py --download               # 仅下载
    python scripts/extract_tdx.py --convert                # 仅转换（跳过下载）
    python scripts/extract_tdx.py --convert --market sh -v # 仅沪市 + 详细进度

目录结构:
    tmp/hsjday.zip              # 下载的 zip
    tmp/extract/s|sz|bj/lday/   # 解压后的 .day 文件
    data/sh/{code}.csv           # 转换后的 CSV
    data/sz/{code}.csv
    data/bj/{code}.csv
"""

import argparse
import datetime
import glob
import json
import os
import sys
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib.request import urlopen

import pandas as pd

from tdx_reader import parse_day_bytes

# 修复 Windows GBK 终端下的 Unicode 输出问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TDX_URL = "https://data.tdx.com.cn/vipdoc/hsjday.zip"
META_FILE = "tmp/meta.json"


def _load_meta() -> dict:
    """加载元数据文件"""
    if os.path.exists(META_FILE):
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_meta(meta: dict):
    """保存元数据文件"""
    os.makedirs(os.path.dirname(META_FILE), exist_ok=True)
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _parse_code(filename: str) -> tuple[str, str]:
    """从文件名解析市场和代码。'sh600000.day' -> ('SH', '600000')"""
    name = os.path.splitext(os.path.basename(filename))[0]
    market = name[:2].upper()
    code = name[2:]
    return market, code


def _convert_one_file(args: tuple) -> str | None:
    """单个 .day 文件转换（供多进程调用）。返回 code，失败返回 None。"""
    filepath, out_mkt_dir, verbose = args
    try:
        with open(filepath, "rb") as f:
            data = f.read()

        records = parse_day_bytes(data)

        if not records:
            return None

        _, code = _parse_code(filepath)
        df = pd.DataFrame(records)
        df = df.sort_values("date").reset_index(drop=True)
        csv_path = os.path.join(out_mkt_dir, f"{code}.csv")
        df.to_csv(csv_path, index=False, encoding="utf-8")
        return code
    except Exception:
        return None


def download(zip_path: str = "tmp/hsjday.zip", force: bool = False) -> str | None:
    """下载 zip 包（带进度条 + 超时）。如果已有当天下载的文件则跳过。"""
    meta = _load_meta()
    today = datetime.date.today().isoformat()

    if not force and meta.get("download_date") == today and os.path.exists(zip_path):
        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        print(f"今日已下载，跳过 ({zip_path}, {size_mb:.1f} MB)")
        return zip_path

    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    print(f"正在下载 {TDX_URL} ...")

    response = urlopen(TDX_URL, timeout=30)
    total = int(response.headers.get("content-length", 0))
    downloaded = 0
    chunk_size = 65536

    with open(zip_path, "wb") as f:
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                print(f"\r  下载中... {pct:.0f}% ({mb:.1f}/{total_mb:.1f} MB)", end="", flush=True)
    print()

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"已保存: {zip_path} ({size_mb:.1f} MB)")

    meta["download_date"] = today
    _save_meta(meta)
    return zip_path


def extract_zip(zip_path: str = "tmp/hsjday.zip", extract_dir: str = "tmp/extract") -> str:
    """解压 zip 到临时目录，返回解压后的根目录。已解压则跳过。"""
    os.makedirs(extract_dir, exist_ok=True)

    # 检查是否已解压（简单判断：目录下是否有 sh/sz/bj 子目录）
    existing = [d for d in ["sh", "sz", "bj"] if os.path.isdir(os.path.join(extract_dir, d))]
    if existing:
        print(f"已解压 ({', '.join(existing)})，跳过")
        return extract_dir

    print(f"正在解压 {zip_path} ...")
    zf = zipfile.ZipFile(zip_path, "r")
    zf.extractall(extract_dir)
    zf.close()
    print(f"已解压到 {extract_dir}/")
    return extract_dir


def convert(extract_dir: str = "tmp/extract", output_dir: str = "data",
            markets: list[str] | None = None, verbose: bool = False,
            workers: int | None = None) -> dict[str, int]:
    """多进程并行转换 .day 文件为独立 CSV。"""
    stats: dict[str, int] = {}

    market_dirs = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    for mkt_dir, mkt_label in market_dirs.items():
        if markets and mkt_label.lower() not in [m.lower() for m in markets]:
            continue

        lday_dir = os.path.join(extract_dir, mkt_dir, "lday")
        if not os.path.isdir(lday_dir):
            continue

        day_files = sorted(glob.glob(os.path.join(lday_dir, "*.day")))
        if not day_files:
            continue

        out_mkt_dir = os.path.join(output_dir, mkt_dir)
        os.makedirs(out_mkt_dir, exist_ok=True)

        total = len(day_files)
        if verbose:
            w = workers or os.cpu_count() or 1
            print(f"[{mkt_label}] {total} 个文件, {w} 进程并行...")

        tasks = [(f, out_mkt_dir, verbose) for f in day_files]
        count = 0

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_convert_one_file, t): t[0] for t in tasks}
            for i, future in enumerate(as_completed(futures)):
                code = future.result()
                if code:
                    count += 1
                if verbose and (i + 1) % 1000 == 0:
                    pct = (i + 1) / total * 100
                    print(f"  [{mkt_label}] {i + 1}/{total} ({pct:.0f}%)")

        stats[mkt_label] = count
        print(f"[{mkt_label}] {count} 个 CSV -> {out_mkt_dir}/")

    return stats


def main():
    parser = argparse.ArgumentParser(description="通达信日线数据下载与转换")
    parser.add_argument("--download", action="store_true", help="下载 hsjday.zip")
    parser.add_argument("--convert", action="store_true", help="转换已解压的 .day 文件为 CSV")
    parser.add_argument("--force-download", action="store_true", help="强制重新下载")
    parser.add_argument("--zip", default="tmp/hsjday.zip", help="zip 文件路径")
    parser.add_argument("--extract-dir", default="tmp/extract", help="解压目标目录")
    parser.add_argument("--output-dir", default="data", help="CSV 输出根目录")
    parser.add_argument("--market", nargs="+", choices=["sh", "sz", "bj"], help="仅处理指定市场")
    parser.add_argument("--workers", "-w", type=int, default=None, help="并行进程数（默认 CPU 核数）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细进度")
    args = parser.parse_args()

    if not args.download and not args.convert:
        args.download = True
        args.convert = True

    if args.download:
        zip_path = download(args.zip, force=args.force_download)

    if args.convert:
        if not os.path.exists(args.zip):
            print(f"错误: 找不到 zip: {args.zip}")
            print("请先用 --download 下载")
            sys.exit(1)
        extract_dir = extract_zip(args.zip, args.extract_dir)
        print()
        stats = convert(
            extract_dir=extract_dir,
            output_dir=args.output_dir,
            markets=args.market,
            verbose=args.verbose,
            workers=args.workers,
        )
        total = sum(stats.values())
        print(f"\n完成: {total} 个股票 CSV 文件")


def verify_data(extract_dir: str = "tmp/extract",
                output_dir: str = "data") -> dict:
    """校验 CSV 与原始 .day 文件的一致性。

    逐文件对比：.day 有效记录数 vs CSV 行数，检测截断或损坏。
    """
    from tdx_reader import parse_day_bytes

    market_dirs = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
    results: dict[str, list[dict]] = {"ok": [], "mismatch": [], "missing": []}

    for mkt_dir, mkt_label in market_dirs.items():
        lday_dir = os.path.join(extract_dir, mkt_dir, "lday")
        csv_dir = os.path.join(output_dir, mkt_dir)

        if not os.path.isdir(lday_dir):
            continue

        day_files = sorted(glob.glob(os.path.join(lday_dir, "*.day")))
        for day_path in day_files:
            _, code = _parse_code(day_path)
            csv_path = os.path.join(csv_dir, f"{code}.csv")

            # 读取 .day 有效记录数
            try:
                with open(day_path, "rb") as f:
                    raw = f.read()
                day_records = len(parse_day_bytes(raw))
            except Exception:
                results["missing"].append({
                    "code": code, "market": mkt_label,
                    "error": "failed to parse .day file",
                })
                continue

            # 读取 CSV 行数
            if not os.path.exists(csv_path):
                results["missing"].append({
                    "code": code, "market": mkt_label,
                    "day_records": day_records, "csv_rows": 0,
                })
                continue

            try:
                df = pd.read_csv(csv_path)
                csv_rows = len(df)
            except Exception:
                results["missing"].append({
                    "code": code, "market": mkt_label,
                    "error": "failed to read CSV",
                })
                continue

            if day_records == csv_rows:
                results["ok"].append({
                    "code": code, "market": mkt_label, "rows": csv_rows,
                })
            else:
                results["mismatch"].append({
                    "code": code, "market": mkt_label,
                    "day_records": day_records, "csv_rows": csv_rows,
                    "delta": csv_rows - day_records,
                })

    total = sum(len(v) for v in results.values())
    summary = {
        "total_checked": total,
        "ok": len(results["ok"]),
        "mismatch": len(results["mismatch"]),
        "missing": len(results["missing"]),
        "details": results,
    }

    print(f"\n[数据完整性校验]")
    print(f"  总计: {total} 个文件")
    print(f"  ✓ 一致: {summary['ok']}")
    if results["mismatch"]:
        print(f"  ✗ 不一致: {len(results['mismatch'])}")
        for m in results["mismatch"][:10]:
            print(f"    {m['code']}: .day={m['day_records']}, CSV={m['csv_rows']} (差{m['delta']})")
    if results["missing"]:
        print(f"  ✗ 缺失CSV: {len(results['missing'])}")
        for m in results["missing"][:5]:
            print(f"    {m['code']}: {m.get('error', 'no CSV')}")

    return summary


if __name__ == "__main__":
    main()
