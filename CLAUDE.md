# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在这个仓库中工作时提供指引。

## 概述

QuantDemo 是一个 AI 量化交易 Skill，使用 QMT（国金证券模拟版）+ xtquant SDK + scikit-learn。提供行情数据获取、特征工程和基于机器学习的股票预测（RandomForest 分类器，预测未来 5 日涨跌）。

## 项目结构

```
quantdemo/
├── SKILL.md                     # Skill 定义（根目录）
├── CLAUDE.md                    # 项目指引
├── README.md
├── requirements.txt
├── scripts/
│   ├── extract_tdx.py           # 通达信日线 zip → CSV 提取
│   ├── tdx_reader.py            # 通达信 .day 文件解析器
│   ├── data_fetcher.py          # QMT 行情数据获取
│   ├── ai_strategy.py           # AI 选股策略（ML 管线）
│   ├── qmt_env.py               # QMT 环境检测与连接
│   └── run_tests.py             # 测试运行器
├── data/
│   ├── sh/                      # 沪市 CSV（600000.csv, ...）
│   ├── sz/                      # 深市 CSV（000001.csv, ...）
│   └── bj/                      # 北交所 CSV（430017.csv, ...）
└── tmp/                         # 临时文件（hsjday.zip 等）
```

## 常用命令

```bash
# 下载通达信日线数据并转换为 CSV
python scripts/extract_tdx.py                          # 下载 + 转换全部
python scripts/extract_tdx.py --download               # 仅下载 zip
python scripts/extract_tdx.py --force-download         # 强制重新下载
python scripts/extract_tdx.py --convert                # 仅转换已有 zip
python scripts/extract_tdx.py --convert --market sh    # 仅转换沪市
python scripts/extract_tdx.py --convert -v             # 详细进度
python scripts/extract_tdx.py --convert -w 8           # 指定 8 进程并行

# 运行全部测试（离线 + 在线）
python scripts/run_tests.py

# 仅离线测试（无需 QMT 客户端）
python scripts/run_tests.py --offline

# 仅在线测试（需要 QMT 客户端运行中）
python scripts/run_tests.py --online

# 直接运行 AI 策略
python scripts/ai_strategy.py              # 离线模式（模拟数据）
python scripts/ai_strategy.py --live       # 实盘模式（需要 QMT）

# 安装依赖
pip install -r requirements.txt
```

## 架构

### 关键架构细节

- **xtquant 未安装在项目 venv 中** — 它位于 `D:\software\python\Lib\site-packages`。每个使用 xtquant 的模块都通过 `sys.path.insert(0, ...)` 注入此路径。如果你新增了导入 xtquant 的模块，也必须做同样的处理。
- **在线数据操作需要 QMT 客户端运行**（`XtItClient.exe`）。桌面快捷方式位于 `D:\国金QMT交易端模拟\bin.x64\XtItClient.exe`。离线测试使用 `DataFetcher.mock_kline()`，通过 NumPy 生成模拟的 OHLCV 数据。
- `DataFetcher.connect()` 是惰性的 — `get_kline()` 会在未连接时自动连接，因此调用方不需要显式调用 connect。
- `ai_strategy._make_features()` 在离线预测和实盘预测路径中共享。它期望传入一个包含以下列的 DataFrame：`date`、`open`、`high`、`low`、`close`、`volume`。输出 8 个特征列 + 1 个二分类 `label`（未来 5 日收益率 > 0）。

### 数据流

```
通达信 hsjday.zip → extract_tdx.py → data/{sh,sz,bj}/{code}.csv  （历史数据）

QMT 客户端 (XtItClient.exe)                                    （实时数据）
    ↓ xtdata.connect()
DataFetcher.get_kline() → pd.DataFrame (OHLCV)
    ↓
ai_strategy._make_features() → 特征 DataFrame
    ↓
RandomForestClassifier → 预测结果（涨跌方向 + 置信度）
```

### 历史数据获取

通达信日线数据下载地址：https://data.tdx.com.cn/vipdoc/hsjday.zip

运行 `python scripts/extract_tdx.py` 自动下载并转换为独立 CSV 文件，按市场分目录存放：

```
data/sh/{code}.csv    # 沪市，如 data/sh/600000.csv
data/sz/{code}.csv    # 深市，如 data/sz/000001.csv
data/bj/{code}.csv    # 北交所，如 data/bj/430017.csv
```

每个 CSV 包含字段：`date`, `open`, `high`, `low`, `close`, `volume`, `amount`。

## Git 提交规范

本项目严格遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 规范：

```
<type>: <description>
```

常用 type：
- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档变更
- `refactor`: 重构（不改变功能）
- `test`: 测试相关
- `chore`: 构建、依赖等杂项

示例：`feat: 添加动量因子特征` / `fix: 修复 get_kline 日期索引丢失问题`

当用户要求"提交"时，除非特别声明"提交到本地"，则需要提交+推送。

### xtquant 数据注意事项

`xtdata.get_market_data()` 返回一个三维嵌套字典：`{字段: {代码: [值列表]}}`。`DataFetcher.get_kline()` 将其展平为标准 DataFrame。xtquant 的时间戳索引被丢弃 — 改用占位的 `date` 列代替。这是一个已知的局限性。
