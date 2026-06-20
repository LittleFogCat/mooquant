---
name: quantdemo
description: AI 量化交易演示项目。提供通达信日线数据下载与转换、QMT 行情数据获取、基于 RandomForest 的股票涨跌预测。
---

# QuantDemo — AI 量化交易技能

基于 QMT（国金证券模拟版）+ xtquant + scikit-learn 的量化交易演示项目。

## 常用命令

```bash
# 通达信日线数据下载与转换
python scripts/extract_tdx.py                              # 下载 + 解压 + 转换（全部）
python scripts/extract_tdx.py --download                   # 仅下载
python scripts/extract_tdx.py --convert                    # 仅转换（跳过下载）
python scripts/extract_tdx.py --convert --market sh        # 仅沪市
python scripts/extract_tdx.py --convert -v                 # 详细进度
python scripts/extract_tdx.py --convert -w 8               # 指定并行进程数
python scripts/extract_tdx.py --force-download             # 强制重新下载

# 运行测试
python scripts/run_tests.py                                # 全部测试
python scripts/run_tests.py --offline                      # 仅离线（无需 QMT）
python scripts/run_tests.py --online                       # 仅在线（需 QMT）

# AI 策略
python scripts/ai_strategy.py                              # 离线模式（模拟数据）
python scripts/ai_strategy.py --live                       # 实盘模式（需 QMT）
```

## 数据获取

### 通达信历史日线数据

数据来源：https://data.tdx.com.cn/vipdoc/hsjday.zip

运行 `python scripts/extract_tdx.py` 自动完成：
1. **下载** — 检查 `tmp/meta.json` 记录的上次下载日期，同日则跳过
2. **解压** — 解压到 `tmp/extract/`，已解压则跳过
3. **转换** — 多进程并行解析 .day 文件，输出独立 CSV

输出结构：
```
data/
  sh/{code}.csv     # 沪市，如 600000.csv
  sz/{code}.csv     # 深市，如 000001.csv
  bj/{code}.csv     # 北交所，如 430017.csv
```

每个 CSV 包含字段：`date`, `open`, `high`, `low`, `close`, `volume`, `amount`

### QMT 实时数据

需运行 QMT 客户端（`XtItClient.exe`），通过 `DataFetcher` 获取 OHLCV 行情。

## 数据流

```
通达信 hsjday.zip
  → extract_tdx.py (下载 + 解压 + 多进程转换)
  → data/{sh,sz,bj}/{code}.csv

QMT 客户端 (XtItClient.exe)
  → xtdata.connect()
  → DataFetcher.get_kline()
  → ai_strategy._make_features()
  → RandomForestClassifier → 涨跌预测
```

## 关键依赖

- **xtquant** 未安装在项目 venv 中，位于 `D:\software\python\Lib\site-packages`，脚本通过 `sys.path.insert` 注入
- **QMT 客户端** 需要运行 `XtItClient.exe` 才能获取实时数据，快捷方式位于 `D:\国金QMT交易端模拟\bin.x64\XtItClient.exe`
- **Python** 使用系统 Python `D:\software\python\python.exe`（非 hermes venv）
