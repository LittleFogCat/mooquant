# 开发计划

## 项目概述

QuantDemo 是一个 AI 量化交易 Skill，基于 QMT（国金证券模拟版）+ xtquant + scikit-learn。

**已实现：**
- 通达信日线数据下载、解析、多进程转换为独立 CSV（~12,000 只股票）
- QMT 实时行情获取（OHLCV）+ 多周期支持（日/周/月/分钟线）
- 基于 8 个量价特征 + 扩展因子（RSI/MACD/KDJ/布林带/ATR 等）的 ML 涨跌预测
- 离线模拟模式 + 实盘推理模式
- Walk-forward 回测框架（T+1/成本/涨跌停）+ 6 项评估指标
- Top-N 选股 + 信号生成 + 冷却期
- 模型持久化（joblib）+ 时序交叉验证 + 多模型对比
- 配置中心 + 日志系统 + 每日自动流水线 + 报告输出
- 交易执行层（xttrader 封装）+ 风控闸门 + Dry-run 开关

## 全局工程纪律（贯穿所有 Phase，不可违反）

> 这是量化系统的"红线"。任何 Phase 的代码（特征、回测、信号、实盘）都必须满足，
> 否则所有评估指标都是幻觉。Code review / 测试需以此为验收标准。

1. **防前视偏差（look-ahead bias）**：特征只能使用 t 时刻及之前的数据；标签
   `shift(-5)` 产生的最后 5 行在训练与回测中必须切除，绝不可进入"已知样本"。
2. **复权一致性**：训练历史数据与实盘当下数据必须使用相同复权方式（front/back/none），
   否则特征分布漂移导致模型在实盘失效。
3. **回测撮合纪律**：每根 bar 模型只能用"该 bar 收盘后已知"的信息；下单按**次日开盘价**
   撮合，禁止用当日收盘价"作弊买入"；从第一版起内置 T+1、涨跌停无法成交、印花税
   （卖出 0.05%）、佣金、过户费、滑点。
4. **训练 / 推理分离**：模型统一离线训练并持久化，实盘与回测只做 inference。
   禁止"现取 K 线→当场训练→预测最后一行"（样本量过小且过拟合噪声，见 TD4）。
5. **实盘安全默认**：交易代码默认 dry-run（只打印不下单），需显式开关才动真实账户；
   风控闸门在下单前强制校验，不可绕过。

## 架构

```
                    ┌─────────────────────────────┐
                    │     extract_tdx.py          │  下载 hsjday.zip, 解压,
                    │     (内置 .day 解析器)       │  多进程 .day → CSV
                    └──────────┬──────────────────┘
                               ↓
                    ┌─────────────────────────────┐
                    │  data/{sh,sz,bj}/{code}.csv │  ~12,000 个 CSV
                    │  date/open/high/low/close/  │  (历史数据 + 回测输入)
                    │  volume/amount              │
                    └──────────┬──────────────────┘
                               ↓                    ┌──────────────────────┐
                    ┌─────────────────────┐          │  QMT 实时数据         │
                    │  ai_strategy.py     │ ←─────── │  data_fetcher.py     │
                    │  _make_features()   │          │  get_kline()         │
                    │  RandomForest       │          │  已修复：真实交易日期  │
                    └──────────┬──────────┘          └──────────────────────┘
                               ↓
                    ┌─────────────────────┐
                    │  signals.py         │  预测 → buy/hold/sell 信号
                    │  selector.py        │  Top-N 选股
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │  backtest.py        │  Walk-forward 回测
                    │  risk.py            │  风控闸门
                    │  trader.py          │  QMT 交易执行
                    └─────────────────────┘
```

## 当前状态

| 模块 | 状态 | 可以工作 | 已知问题 |
|------|------|----------|----------|
| `extract_tdx.py` | 可用 | 下载、解压、多进程 .day→CSV、增量下载、数据完整性校验(verify_data) | — |
| `tdx_reader.py` | 可用 | 独立 .day 解析、批量读取、vipdoc 发现、`parse_day_bytes()` 标准解析入口 | 被 `extract_tdx.py` 导入调用 |
| `data_validator.py` | 可用 | 停牌检测、异常值过滤、缺失交易日检测、`run_all()` 一键校验 | 基于日期间隔推算，非精确交易日历 |
| `fundamental.py` | 可用 | 换手率/PE_TTM/PB/总市值获取、特征合并、mock 离线模式 | 在线需 QMT 运行 |
| `data_fetcher.py` | 可用 | mock 数据生成、QMT 连接、股票列表获取、K线获取（含真实交易日期） | — |
| `qmt_env.py` | 可用 | xtquant 检测、QMT 进程检测、full_check() | — |
| `ai_strategy.py` | 可用 | 时序交叉验证(TimeSeriesSplit)、模型持久化(joblib)、离线/实盘预测、特征重要性 | 实盘推理需先运行离线训练 |
| `features.py` | 可用 | 动量因子(RSI/MACD/KDJ)、量价因子(OBV/量价相关/成交量突破)、价格因子(布林带/ATR/斜率) | — |
| `backtest.py` | 可用 | Walk-forward 回测、T+1/成本/涨跌停、持仓追踪、6 项评估指标 | — |
| `signals.py` | 可用 | 预测→买卖信号、置信度阈值、冷却期、多级过滤 | — |
| `selector.py` | 可用 | 股票池扫描→Top-N 选股、惰性模型加载、置信度排序 | — |
| `config.py` | 可用 | 项目配置中心：路径/模型/回测/信号/报告，支持 YAML 覆盖 | — |
| `run_daily.py` | 可用 | 每日流水线：下载→校验→训练→选股→报告 | 需 QMT 在线以获取实时数据 |
| `trader.py` | 可用 | QMT 交易接口封装：下单/撤单/查询/回调；Dry-run 模式；订单状态机 | 需 QMT 在线 + 交易账号 |
| `risk.py` | 可用 | 风控闸门：仓位上限/日亏损熔断/止损止盈/黑名单；下单前强制校验 | — |
| `run_tests.py` | 冒烟测试 | 运行所有模块并打印输出 | 零断言 |
| `requirements.txt` | 可用 | 5 个包声明 | — |

## 已知 Bug（已修复，归档）

### Bug 1 (已修复): `get_kline()` 丢弃真实交易日期
- **修复:** 在 field_list 中加入 "time"，通过 `xtdata.timetag_to_datetime()` 提取真实时间戳；回退方案利用 `get_trading_dates` 交易日历

### Bug 2 (已修复): `connect_data()` 参数未生效
- **修复:** 移除未使用的 `timeout_sec` 参数（xtdata.connect 不支持超时参数）

## 技术债（已清理，归档）

| # | 项目 | 状态 |
|---|------|------|
| TD1 | .day 解析器代码重复 | ✓ 已统一到 `tdx_reader.py::parse_day_bytes()` |
| TD2 | 无时序交叉验证 | ✓ 已改用 `TimeSeriesSplit(n_splits=5)` |
| TD3 | 无模型持久化 | ✓ 已使用 `joblib` 保存/加载 |
| TD4 | 实盘用训练数据做预测 | ✓ 已改为加载模型纯推理 |
| TD5 | 冗余依赖 | ✓ 已移除 `matplotlib`，新增 `joblib` |
| TD6 | Windows 强依赖 | ⚠ QMT 仅支持 Windows，无法消除 |

## 后续计划

### Phase 1: 数据层增强

> 全部完成 ✓

| # | 任务 | 描述 | 前置 |
|---|------|------|------|
| 1.1 | 修复 `get_kline()` 日期提取 | 从 xtdata 返回结构中提取真实交易日期 | — |
| 1.2 | 统一 .day 解析器 | 将 `extract_tdx.py` 中的解析逻辑合并到 `tdx_reader.py`，消除代码重复 | — |
| 1.3 | 数据质量校验 | 新建 `data_validator.py`：停牌检测（连续相同 OHLC）、异常值过滤（日收益率 >20% 或 volume=0）、缺失交易日检测 | — |
| 1.4 | 基本面数据接入 | 新建 `fundamental.py`：通过 xtdata 获取换手率、市盈率、市净率、总市值，集成到 `_make_features()` | 1.3 |
| 1.5 | 多周期数据支持 | 扩展 `DataFetcher` 支持周线、月线、分钟线便捷方法；`_make_features()` 兼容不同频率；mock 数据支持多周期 | 1.1 |
| 1.6 | 历史数据完整性校验 | 在 `extract_tdx.py` 中添加 `verify_data()`：对比 CSV 行数与 .day 记录数，检测截断或损坏文件 | 1.2 |

### Phase 2: 模型层增强

> ⚠ **顺序提示**：在做 2.4「多模型对比」之前，应先有一个最小可用回测框架（见 3.1，
> 建议前移）。用 accuracy 选模型是错的——交易看的是夏普/回撤/盈亏比，不是分类准确率。
> 没有回测验证之前，对比 XGBoost vs LightGBM 的准确率没有意义。

| # | 任务 | 描述 | 前置 |
|---|------|------|------|
| 2.1 | 模型持久化 | 使用 `joblib.dump/load` 保存/加载模型到 `models/` 目录；记录元数据（训练日期、特征列表、股票代码、准确率） | — |
| 2.2 | 时序交叉验证 | 替换 `train_test_split` 为 `TimeSeriesSplit`；确保训练集所有日期严格早于测试集 | — |
| 2.3 | 扩展特征组 | 新建 `features.py` 模块：动量因子（RSI、MACD、KDJ）、量价相关性（OBV、量价相关、成交量突破）、价格因子（布林带、ATR、价格斜率）；可组合的特征生成器 | 1.4 |
| 2.4 | 多模型对比 | `compare_models()`：XGBoost / LightGBM / RandomForest 备选分类器，可选安装；模型注册表架构 | 2.1, 2.2 |
| 2.5 | 多股票批量预测 | 扩展 `batch_predict()` 接受股票列表，批量打分；避免为每只股票重新加载模型 | 2.1, 2.4 |

### Phase 3: 策略层

| # | 任务 | 描述 | 前置 |
|---|------|------|------|
| 3.1 | 回测框架 | 新建 `backtest.py`：walk-forward 模拟，可配置买卖规则、T+1/涨跌停/印花税/佣金/滑点，持仓追踪 | 1.6, 2.2 |
| 3.2 | 信号生成 | 新建 `signals.py`：预测概率 + 置信度 → buy/hold/sell 信号；冷却期、多级阈值 | 2.1 |
| 3.3 | Top-N 选股 | 新建 `selector.py`：`StockSelector` 遍历股票池 → 模型打分 → 置信度最高的 Top-N；惰性加载模型 | 2.5, 3.2 |
| 3.4 | 评估指标 | `backtest.py` 内建：累计收益率、年化收益率、夏普比率、最大回撤、胜率、盈亏比、净值曲线 | 3.1 |

### Phase 4: 工程化

| # | 任务 | 描述 | 前置 |
|---|------|------|------|
| 4.1 | 配置文件化 | 新建 `config.py`：`ProjectConfig` 数据中心，统一管理路径/参数/模型/回测/信号配置，支持 YAML 覆盖 | — |
| 4.2 | 日志系统 | `setup_logging()`：替换 `print()` 为结构化 logging，INFO/DEBUG/ERROR 分级，同时输出到控制台和文件 | — |
| 4.3 | 移除冗余依赖 | `requirements.txt` 已清理，移除 `matplotlib`，新增 `joblib`，`xgboost`/`lightgbm` 可选 | — |
| 4.4 | 定时任务 | 新建 `run_daily.py`：每日下载数据 → 校验 → 重训模型 → Top-N 选股 → 输出报告 | 4.1, 4.2 |
| 4.5 | 结果通知 | 输出到 `reports/` 目录（CSV/JSON）；控制台报告总结；latest.json 符号链接 | 4.4 |

### Phase 5: 交易执行层（实盘操作 — 项目核心目标，最高风险）

> ⚠ 当前代码仅使用 xtquant 的 **xtdata（行情）** 模块，完全未接触 **xttrader（交易）** 模块，
> 全仓搜索 order/buy/sell/委托 零命中。实盘是涉及真金白银的高风险环节，必须排在
> 「回测验证有效」之后，并严格按 模拟盘 → dry-run → 小资金实盘 逐级放开。

| # | 任务 | 描述 | 前置 |
|---|------|------|------|
| 5.1 | 封装 xttrader | 新建 `trader.py`：账户登录、资金/持仓查询、下单（限价/市价）、撤单、`TraderCallback` 回调；Dry-run 开关；订单状态机追踪 | 3.2 |
| 5.2 | 模拟盘联调 | 在国金 QMT 模拟环境跑通「信号 → 下单 → 回报 → 持仓更新」完整闭环（代码已就绪，待实盘验证） | 5.1 |
| 5.3 | 风控前置闸门 | 新建 `risk.py`：单票仓位上限、总仓位上限、单日亏损熔断、止损/止盈、黑名单；下单前强制校验 | 5.1 |
| 5.4 | 状态机与对账 | `trader.py` 内置：`Order` 数据类追踪 9 种订单状态（未报→已成/已撤/废单）；持仓与 QMT 对账 | 5.2 |
| 5.5 | dry-run / 实盘开关 | `Trader(dry_run=True)` 默认只打印不真实下单；`dry_run=False` 显式开关才动账户 | 5.3 |

## 实施记录

所有 5 个 Phase 已完成，对应 Git 提交：

| Phase | 内容 | 提交 |
|-------|------|------|
| Sprint 1 | Bug 修复 + 目录重构 | 52d8266, 4bbe248 |
| Sprint 2 | 技术债清理（解析器统一/时序CV/模型持久化/训推分离） | 3cea6d2 |
| Sprint 3 | 数据质量层（校验/基本面/完整性） | 48fd01d |
| Phase 3 | 策略层（回测/信号/选股/评估） | e417113 |
| Phase 4 | 工程化（配置/日志/流水线/报告） | 88be546 |
| Phase 5 | 交易执行层（trader/风控） | 758ab8b |

## 测试策略

### 短期（完善现有 run_tests.py）

- 为每个测试函数添加断言（当前全是 print，永远"通过"）
- 新增 `test_extract_tdx.py`：验证 `_parse_day_file()` 对合成 32 字节记录的正确性、验证 CSV 输出格式
- 新增 `test_data_fetcher.py`：验证 `mock_kline()` 形状和列类型、验证 `get_kline()` 日期有效性（Bug 1 修复后）
- 新增 `test_ai_strategy.py`：验证 `_make_features()` 输出维度和列名、验证无 NaN

### 中期（测试基础设施）

- 迁移到 `pytest`（最小配置：`pytest.ini`）
- 添加 CI 配置（GitHub Actions `.github/workflows/test.yml`，仅离线测试）
- 添加覆盖率追踪（`pytest-cov`）

## 已知局限

- **pytest 迁移未完成:** `run_tests.py` 仍为零断言冒烟测试，未迁移到 pytest + CI
- **路径硬编码:** `D:\software\python\Lib\site-packages` 和 `D:\国金QMT交易端模拟\bin.x64\XtItClient.exe` 不可移植（QMT 本身仅支持 Windows，此局限无法完全消除）
- **无特征缓存:** 选股/批量预测时每只股票独立拉取 K 线并计算特征，未做缓存复用
- **交易日历不精确:** `data_validator.py` 基于日期间隔推算缺失交易日，非精确交易日历
- **实盘交易未验证:** trader.py 代码已就绪，但模拟盘联调闭环（5.2）尚未在真实 QMT 环境中验证
