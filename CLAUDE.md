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
│   ├── install_xtquant.py       # xtquant 安装/升级工具
│   ├── extract_tdx.py           # 通达信日线 zip → CSV 提取
│   ├── tdx_reader.py            # 通达信 .day 文件解析器
│   ├── data_fetcher.py          # QMT 行情数据获取
│   ├── data_validator.py        # 数据质量校验（停牌/异常值/缺失交易日）
│   ├── fundamental.py           # 基本面数据接入（换手率/PE/PB/市值）
│   ├── features.py              # 扩展特征工程（RSI/MACD/KDJ/布林带/ATR）
│   ├── ai_strategy.py           # AI 选股策略（ML 管线 + 模型持久化）
│   ├── backtest.py              # Walk-forward 回测 + 评估指标
│   ├── signals.py               # 信号生成（buy/hold/sell + 冷却期）
│   ├── selector.py              # Top-N 选股器
│   ├── config.py                # 项目配置中心（YAML 覆盖）
│   ├── run_daily.py             # 每日自动流水线
│   ├── trader.py                # QMT 交易接口封装 + Dry-run
│   ├── risk.py                  # 风控闸门（仓位/止损/熔断）
│   ├── qmt_env.py               # QMT 环境检测与连接
│   └── run_tests.py             # 测试运行器
├── data/
│   ├── sh/                      # 沪市 CSV（600000.csv, ...）
│   ├── sz/                      # 深市 CSV（000001.csv, ...）
│   └── bj/                      # 北交所 CSV（430017.csv, ...）
├── models/                      # 模型持久化目录（joblib + JSON 元数据）
├── reports/                     # 每日报告输出
├── logs/                        # 日志文件
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

## xtquant 安装与升级

xtquant 是迅投（thinktrader）/ 国金证券 QMT 的 Python SDK，**不是 PyPI 包，不能通过 pip 安装**。

下载页面：https://dict.thinktrader.net/nativeApi/download_xtquant.html

```bash
# 自动下载最新版并安装（需要 7-Zip 解压 RAR）
python scripts/install_xtquant.py

# 从本地 RAR 安装
python scripts/install_xtquant.py --rar path/to/xtquant_250807.rar

# 查看当前状态
python scripts/install_xtquant.py --status

# 回滚到上一版本
python scripts/install_xtquant.py --rollback
```

安装流程：
1. 从官方页面解析最新 RAR 下载链接（页面是 VuePress SPA，链接内嵌在 `<script>` 标签中，格式 `/packages/xtquant_XXXXXX.rar`）
2. 下载 RAR 文件到临时目录
3. 用 7-Zip 解压（`C:\Program Files\7-Zip\7z.exe`）
4. 备份旧版 `xtquant/` → `xtquant.bak/`
5. 替换 `D:\software\python\Lib\site-packages\xtquant/`
6. 验证导入成功

关键约束：
- xtquant 安装在系统 Python 的 `D:\software\python\Lib\site-packages`，而非项目 venv
- 每个使用 xtquant 的脚本都必须 `sys.path.insert(0, r"D:\software\python\Lib\site-packages")`
- 在线数据/交易功能需要 QMT 客户端 `XtItClient.exe` 在后台运行
- 当前安装版本：`xtquant_250807`（2025-12-19 发布）

## 架构

### 关键架构细节

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
    ↓
signals.py → signals → selector.py → Top-N 选股
    ↓
backtest.py（回测验证） / trader.py（实盘交易）+ risk.py（风控闸门）
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
