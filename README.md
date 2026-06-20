# QuantDemo — AI 量化交易入门项目

**环境：** QMT (国金证券模拟版) + xtquant + Python AI 栈

## 快速开始

```bash
# 1. 下载并提取通达信历史数据
python scripts/extract_tdx.py

# 2. 运行测试
python scripts/run_tests.py
```

## 项目结构

```
quantdemo/
├── SKILL.md                   # Claude Code skill 定义
├── CLAUDE.md
├── README.md
├── requirements.txt
├── scripts/
│   ├── extract_tdx.py         # 通达信 zip → CSV
│   ├── tdx_reader.py          # .day 解析器
│   ├── data_fetcher.py        # QMT 数据获取
│   ├── ai_strategy.py         # AI 策略（ML）
│   ├── qmt_env.py             # QMT 环境检测
│   └── run_tests.py           # 测试入口
├── data/
│   ├── sh/                    # 沪市，如 600000.csv
│   ├── sz/                    # 深市，如 000001.csv
│   └── bj/                    # 北交所，如 430017.csv
└── tmp/                       # zip + 解压文件
```

## 数据获取

### 通达信历史日线数据

```bash
# 下载 + 转换（一步到位）
python scripts/extract_tdx.py

# 仅下载
python scripts/extract_tdx.py --download

# 仅转换已有 zip
python scripts/extract_tdx.py --convert

# 仅转换沪市，显示详细进度
python scripts/extract_tdx.py --convert --market sh -v
```

输出结构：`data/{market}/{code}.csv`，每个 CSV 包含 `date`, `open`, `high`, `low`, `close`, `volume`, `amount`。

## 测试清单

| 测试 | 需要QMT运行 | 说明 |
|------|-----------|------|
| 环境检测 | ✗ | 检查 xtquant、Python、依赖 |
| 数据接口 | ✓ | 获取行情、K线、股票列表 |
| AI 策略(离线) | ✗ | 用历史数据做 ML 预测回测 |
| AI 策略(实盘) | ✓ | 接入 QMT 分钟级数据做预测 |
