# QuantDemo — AI 量化交易入门项目

**环境：** QMT (国金证券模拟版) + xtquant + Python AI 栈

## 快速开始

```bash
# 1. 先启动 QMT 客户端（桌面快捷方式）
#    国金QMT交易端模拟.lnk → D:\国金QMT交易端模拟\bin.x64\XtItClient.exe

# 2. 运行测试
python run_tests.py
```

## 项目结构

```
quantdemo/
├── run_tests.py           # 主测试入口（一键运行）
├── qmt_env.py             # QMT 连接 + 环境检测
├── data_fetcher.py        # 行情数据获取工具
├── ai_strategy.py         # AI 选股策略（ML模型）
├── requirements.txt       # 依赖
└── README.md
```

## 测试清单

| 测试 | 需要QMT运行 | 说明 |
|------|-----------|------|
| 环境检测 | ✗ | 检查 xtquant、Python、依赖 |
| 数据接口 | ✓ | 获取行情、K线、股票列表 |
| AI 策略(离线) | ✗ | 用历史数据做 ML 预测回测 |
| AI 策略(实盘) | ✓ | 接入 QMT 分钟级数据做预测 |
