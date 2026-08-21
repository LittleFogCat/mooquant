# 模型管理

## 概述

模型管理是 mooquant 的核心功能之一，负责量化模型的训练、管理和部署。架构设计遵循以下原则：

- **本地聚焦建模**：本地应用负责模型训练和管理，QMT 负责回测/实盘/图表
- **HTTP 服务暴露**：模型层通过 HTTP 服务暴露给上层调用（QMT 壳策略 / 本地策略执行器）
- **上层解耦**：模型管理不关心上层是 QMT 壳还是本地策略页，统一通过 HTTP /signal 接口消费
- **PyTorch 实验性**：PyTorch 作为新引入的建模工具，仅为实验性质
- **接口隔离**：常规量化和机器学习作为底层实现，对上层无感知

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    渲染层（Renderer）                     │
│  ModelViewModel ←→ ModelView                            │
│       ↕ facade.modelServer                              │
├─────────────────────────────────────────────────────────┤
│                    主进程（Main）                         │
│  ModelService (main/services/model-service.js)          │
│  - spawn model_server.py                                │
│  - HTTP 代理（IPC → HTTP）                              │
├─────────────────────────────────────────────────────────┤
│               Python 模型服务（bridge/）                 │
│  model_server.py (HTTP, port 8765)                      │
│  ├─ strategies/          策略框架                        │
│  │  ├─ ml/               ML 策略                         │
│  │  │  ├─ base.py        MLStrategyBase                  │
│  │  │  ├─ models/        模型架构                         │
│  │  │  └─ builtin/       内置 ML 策略                     │
│  │  └─ ...               规则策略                         │
│  ├─ training/            训练管道                         │
│  │  ├─ trainer.py        Trainer                         │
│  │  ├─ dataset.py        FinancialDataset                │
│  │  ├─ labels.py         标签生成                         │
│  │  ├─ model_registry.py ModelRegistry                   │
│  │  ├─ pipeline.py       TrainPipeline（异步训练）        │
│  │  └─ builtin_models.py 内置模型                         │
│  └─ qmt_shell.py         QMT 壳策略模板                   │
├─────────────────────────────────────────────────────────┤
│                    QMT 客户端                             │
│  qmt_shell.py → HTTP → model_server.py → 策略/模型       │
└─────────────────────────────────────────────────────────┘
```

## 模型服务（model_server.py）

### HTTP 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查（策略数、模型数） |
| GET | /strategies | 列出所有策略 |
| GET | /strategy/{name} | 获取策略元数据 |
| POST | /strategy | 添加策略 |
| DELETE | /strategy/{name} | 删除策略 |
| GET | /models | 列出所有模型 |
| GET | /models/{id} | 获取模型详情 |
| DELETE | /models/{id} | 删除模型 |
| PUT | /models/{id} | 更新模型元数据（名称等） |
| GET | /models/active | 获取当前激活的模型 |
| POST | /models/{id}/activate | 激活模型 |
| POST | /train | 启动训练（异步） |
| GET | /train/{id}/status | 查询训练状态 |
| POST | /signal | 计算策略信号（不传 strategy 时自动用激活模型） |

### 配置

- 端口：默认 8765，通过 `MODEL_SERVER_PORT` 环境变量或 `config/default.json` 的 `modelServer.port` 配置
- 依赖：Python stdlib（http.server），零外部依赖（torch 为可选依赖）

### 安全措施

| 措施 | 说明 |
|------|------|
| CORS 收紧 | 不发送 `Access-Control-Allow-Origin` 头，阻止恶意网页跨域访问本地 8765 端口。正常调用方（Python 脚本、Electron 主进程代理）不受同源策略限制 |
| 策略 AST 检查 | `save_strategy` 在写入/执行用户代码前，通过 AST 静态分析禁止导入危险模块（`os`/`subprocess`/`shutil`/`socket`/`pickle`/`ctypes` 等）。仅作用于新提交代码，不影响已加载策略 |
| 策略软删除 | `delete_strategy` 将文件移到 `data/strategies/user/.trash/` 而非直接删除，可恢复误删 |

## 模型架构

### 注册表模式

模型架构通过装饰器注册：

```python
from strategies.ml.models.registry import register_model

@register_model("lstm")
class LSTMModel(nn.Module):
    ...
```

### 内置架构

| 架构 | 文件 | 说明 |
|------|------|------|
| LSTM | models/lstm.py | 多层 LSTM，适用于时序预测 |
| Transformer | models/transformer.py | 含 PositionalEncoding，注意力机制 |
| MLP | models/mlp.py | 多层感知机，基线模型 |

## 特征工程

`FeatureBuilder`（`strategies/ml/features.py`）负责将 K 线数据转换为归一化张量：

- 输入：`bars`（OHLCV 列表）
- 输出：归一化张量 `(batch, seq_len, n_features)`
- 特征：收益率、高低价比例、成交量变化等
- 归一化：z-score 或 min-max，两种**归一化模式**：
  - `rolling`（旧，默认）：逐样本滚动窗口归一化。每个样本用自己的 20 根窗口的 mean/std 归一化，**跨样本几乎无区分度**（close 在窗口内永远是均值附近的 z-score），是「训练退化恒判 flat、回测无交易」的根因之一
  - `global`（新，推荐）：全序列**逐列**统计 mean/std（或 min/max）归一化，训练与推理共用同一套统计量，跨样本特征有区分度。`Trainer` 默认启用；训练时对全部标的全量 bars 统一 fit，统计量随模型 `config.json` 的 `feature_stats` 字段持久化，`MLStrategyBase.on_after_init` 加载后供推理使用

## 训练管道

### 流程

1. `TrainPipeline.start_train(config)` 返回 `task_id`
2. 后台线程执行 `Trainer.train(config)`
3. `TrainPipeline.get_status(task_id)` 查询进度

### Trainer

- 数据获取：`DataFetcher` 接口（`XtdataDataFetcher` / `MockDataFetcher`）；**无数据的标的自动跳过并记录**（结果 `symbols`/`skipped_symbols` 字段，config.json 只保留实际用于训练的标的）
- 数据集：`FinancialDataset`（多标的滑动窗口）
- 标签：classification / regression / triple_barrier
- 训练循环：前向传播 → 损失 → 反向传播 → 优化器步骤
- 早停：验证损失不再下降时停止
- 学习率调度：`ReduceLROnPlateau`
- 类别加权：分类任务用**频率的 sqrt 反比**做轻量 `CrossEntropyLoss(weight=...)`（flat 略降权）。**不要用激进反频加权或 WeightedRandomSampler**——实测会压迫模型偏押单类（all-buy / all-sell）
- 分类任务训练集 `shuffle=True`（窗口样本非严格时序，打乱利于 SGD 收敛）
- **默认特征**：空 `feature_config` 时默认补 `MACD/RSI/BOLL/KDJ` 技术指标（`raw: close/volume` + `derived: return_1d/return_5d` + 4 指标，共 7+ 特征）+ `normalize_mode: global`
- **指标真实性**：`metrics.accuracy` 记录的是**保存的 `best_state`（best_val_loss 点）对应的 val 准确率**，与最终模型一致（修复前误用最后一个 epoch 的 accuracy，会误导用户）
- 早停后 `train_log` 保留全程日志，`metrics` 反映 best 状态
- **数据口径（v0.1.15 修复）**：`XtdataDataFetcher` 统一 volume ×100（股）+ `dividend_type='front'`（前复权），与 `backtest_engine.fetch_real_bars`/`qmt_server` 一致。修复前训练用「手」+不复权而回测用「股」+前复权，导致推理特征整体偏移 ~100σ，模型恒输出 sell、回测零交易
- **OOD 防御**：`FeatureBuilder.build` 在 global 归一化下检测最新样本 |z|>10 的特征列并告警一次（`[feature-ood]`），提示输入数据口径与训练统计量可能不一致
- **旧模型注意**：v0.1.15 之前训练的模型（feature_stats 为「手」口径）需重新训练才能与新口径数据正确配合

### 标签生成

| 类型 | 说明 |
|------|------|
| classification | 未来 N 根 bar 收益率 > 阈值 → 买入；< -阈值 → 卖出（默认 horizon=5, threshold=0.01） |
| regression | 未来 N 根 bar 收益率（连续值） |
| triple_barrier | 三重障碍法（止盈/止损/时间止损），默认 horizon=5, up/down=3% |

> **已知局限**：日线级价格方向预测信噪比极低，即便增强特征 + 短 horizon，模型在样本外仍难稳定超越「押多数类」基线，常见表现为退化为单边（全 up / 全 flat / 全 down）→ 回测只买不卖或只卖不买。这属 ML 固有限制，非代码 bug。改进方向：更多标的/更长历史、walk-forward 训练、或降低策略 margin 阈值。

### 特征-标签对齐（消除 look-ahead bias）

`FinancialDataset` 中特征与标签的对齐约定：

- `X[i]` 的特征窗口为 `bars[i : i+window]`，窗口右端 = `i+window-1`
- `labels[j]` 是从 `closes[j]` 出发预测 `closes[j+horizon]` 的收益
- **X[i] 应配 `labels[i+window-1]`**（从窗口右端出发），而非 `labels[i]`
- 推理时 `build()` 取 `matrix[-window:]`（右端=len-1），对应 `labels[len-1]`，训练与推理对齐一致
- 时序训练使用 `shuffle=False`，避免相邻窗口样本混入验证集

> 修改 `dataset.py` 对齐逻辑时务必同步更新 `tests/training/test_dataset.py` 中的对齐测试。

## 模型注册表

`ModelRegistry`（`training/model_registry.py`）管理模型持久化：

- 存储路径：`data/models/{model_id}/`
- 文件：`model.pt`（权重）+ `meta.json`（元数据）+ `config.json`（训练配置）
- 索引：`data/models/index.json`
- 缓存：LRU 缓存（最多 10 个模型在内存中）
- 线程安全：`threading.Lock`

### ModelWrapper

统一模型推理接口：

- `TorchModelWrapper`：包装 `torch.nn.Module`
- `DummyModelWrapper`：纯 Python 模型（无 torch 依赖）

## ML 策略

### MLStrategyBase

`strategies/ml/base.py` 提供 ML 策略基类：

- 延迟导入 torch（torch 不可用时优雅降级）
- `on_after_init` 加载模型
- `on_bar` 调用模型 forward → 转换为 Signal

### 内置 ML 策略

| 策略 | 文件 | 说明 |
|------|------|------|
| lstm_trend | builtin/lstm_trend.py | LSTM 趋势策略 |

## 激活模型

模型管理支持「激活」功能，设为默认模型供 ML 策略使用：

- 激活的 model_id + 关联策略名持久化到 `data/models/active.json`
- 激活时根据模型 arch 自动选择策略（lstm -> lstm_trend）
- `MLStrategyBase.on_after_init` 无显式 model_id 时自动读取激活模型
- `/signal` 端点和 stdio RPC `strategy.signal` 均通过 MLStrategyBase 统一处理
- 上层（QMT 壳 / 本地策略执行器）无需关心 model_id，激活即可用

## 策略执行器集成

本地策略执行器（`executor-service.js`）的信号计算路径：

```
executor tick -> 获取K线 -> modelService.computeSignal() -> HTTP /signal -> 信号
                                          ↳ 回退 strategyBridge.strategySignal() -> stdio RPC
```

- 壳策略（type=shell）不传 strategy，模型服务自动用激活模型计算
- 手写策略传 strategy 名称，模型服务用指定策略计算
- 模型服务不可用时回退到 QMT 桥 stdio RPC
- 两条路径调用的是同一个策略类的同一个 `on_bar`

## QMT 壳策略

`bridge/qmt_shell.py` 是 QMT 客户端中运行的壳策略：

- 固定一份模板，配置区可修改
- ML 策略无需填 model_id，自动使用激活模型
- `init`：连接模型服务（HTTP）
- `handlebar`：每根 bar 通过 HTTP 调用 `/signal` 端点
- 模型服务返回信号 → 壳策略执行交易

## 主进程集成

### ModelService（main/services/model-service.js）

- spawn `bridge/model_server.py` 作为 HTTP 子进程
- 轮询 `/health` 等待服务就绪
- 提供 HTTP 代理方法供 IPC 层调用
- app quit 时 kill 子进程

### IPC 路由

| IPC 通道 | 说明 |
|----------|------|
| model:status | 查询模型服务状态 |
| model:strategies | 列出策略 |
| model:models | 列出模型 |
| model:train | 启动训练 |
| model:trainStatus | 查询训练状态 |
| model:deleteModel | 删除模型 |
| model:signal | 计算信号 |

### Preload

`preload.js` 暴露 `facade.modelServer` 对象，包含所有模型管理方法。

## UI 页面

### 模型管理页（renderer/js/views/model-view.js）

- 服务状态卡片（状态灯 + 端口 + 刷新）
- 模型列表表格（名称/架构/创建时间/标的/状态/操作）
- 训练表单（标的/周期/数据量/架构/超参数/标签类型）
  - **标的输入复用 `StockSearch` 多选模式**（`renderer/js/views/stock-search.js`）：与回测页/策略启动一致，输入代码/名称/拼音联想下拉，选中即以 `, ` 追加为逗号分隔的多标的标签式输入，支持删除重选
  - **每个字段末尾带圆形轮廓问号角标**（`.form-hint`，`vertical-align:super` 上标、悬浮不改变鼠标指针，悬浮/聚焦显示 `FIELD_TIPS` 中该指标的意义/用途/范围说明）
- **训练状态（进度条 + 阶段文本）**：进度实时更新——`Trainer.train` 通过 `on_progress(progress, stage)` 回调，`TrainPipeline._update` 写入 `task.progress/stage`（fetch 数据加载 → build 构建数据集 → train 按 epoch 推进 20%~95% → save 保存模型 → done 100%）；`get_status` 返回 `{status, progress, stage}`，UI 轮询显示阶段与百分比
- 策略列表

### 路由

- 页面标识：`models`
- 进入时加载模型和策略列表
- 离开时停止训练状态轮询

## 统一数据访问层（v0.1.16，M1）

`bridge/data/datafeed.py` 是全项目**唯一**的行情取数入口（trainer / backtest_engine / model_server 全部经由此模块），严禁绕过它直接调 xtquant：

- **统一口径**：volume=股（内部 ×100）、`dividend_type='front'` 默认前复权、UI码/xt码转换、bar 字段 `{date, open, high, low, close, volume}`
- **口径指纹**：`data_fingerprint(dividend_type)` 返回口径摘要哈希。训练时写入模型 config.json 的 `data_fingerprint`；`MLStrategyBase.on_after_init` 加载模型时用 `check_fingerprint()` 校验，不一致直接报错（防静默口径漂移——「回测无交易」事故的根因防线）。`DATA_SPEC_VERSION` 改口径时必须 +1
- **数据质量校验**：`validate_bars()` 检查零价/OHLC交叉/>15自然日缺口，返回问题列表；`summary()` 输出体检摘要（日期范围/缺失天数/涨跌停bar占比）
- **涨跌停判定**：`is_limit_up/is_limit_down(xt_code, close, prev_close)`，按板块幅度（主板10%/创业科创20%/北交30%）
- **mock 回落**：`fetch_bars_or_mock()` 仅供回测用，mock bars 带 `is_mock=True` 标记；训练路径**不回落 mock**（避免模拟数据污染训练集）

## 特征工程 2.0（v0.1.16，M3.1）

`FeatureBuilder` 新增尺度不变（scale-invariant）派生特征，天然对成交量单位（手/股）、价格水平漂移免疫：

| 特征 | 说明 |
|---|---|
| `log_volume` | log 成交量，单位 ×100 只造成常数平移 log(100) |
| `volume_ratio_5d` | 量比（当日量/5日均量），完全无量纲 |
| `ma_deviation` | 收盘相对 20 日均线偏离度 |
| `high_low_range` | 振幅 (high-low)/close |
| `close_return` | 相对首日累计对数收益 |

**默认特征配置**（trainer 空 `feature_config` 时）：`raw_features: []` + 上述尺度不变特征 + MACD/RSI/BOLL/KDJ + `normalize_mode: global`。v0.1.16 前的默认（裸 close/volume）已废弃。

**OOD 防御**：`build()` 在 global 归一化下检测最新样本 |z|>10 的特征列并告警一次（`[feature-ood]` stderr）。

## 标签工程 2.0（v0.1.16，M3.2）

`make_triple_barrier_labels(bars, horizon=5, up, down, adaptive=False, atr_mult=1.0)`：
- `adaptive=True` 时障碍按 ATR14 自适应（`障碍 = atr_mult * ATR / entry`），波动大的标的障碍宽、波动小的窄，跨标的可比
- 固定模式（adaptive=False）与旧版行为完全一致

训练结果与模型 config 均含 `label_dist`（三类样本计数，str key），UI 体检报告展示百分比分布。

## 训练体检报告（v0.1.16，M3.3）

每次分类训练自动产出 `health_report`（随 config.json 持久化 + train 结果返回）：

- **混淆矩阵**：验证集 3x3（实际/预测：卖出/持有/买入）
- **概率分布直方图**：`prob_hist` 10 桶，诊断「全部挤在一侧」的退化
- **分类别 precision/recall/f1** + 方向类 macro-F1（sell+buy）
- **质量评分**（0-100）：方向 F1 × 60 + 预测多样性一致性 × 20 + 置信度有效性 × 20
- **等级**：>=60 green / >=35 yellow / <35 red（red = degraded）

**degraded 守门**：
- meta.json 的 `metrics.degraded = true`（红档模型）
- 激活需 force：`POST /models/{id}/activate` body `{"force": true}`；UI 弹二次确认
- 模型列表显示红黄绿徽章 + 分数

## 训练数据留档（v0.1.16）

模型 config.json 新增字段：

| 字段 | 说明 |
|---|---|
| `data_fingerprint` | 数据口径指纹（加载时校验） |
| `data_date_range` | 训练数据起止日期 `{start, end}`（样本内回测检测用） |
| `data_summary` | 每标的体检摘要（n/start/end/缺失天数/涨跌停占比） |
| `data_problems` | 数据质量问题列表（可能为空） |
| `label_dist` | 标签分布 `{0: n, 1: n, 2: n}` |
| `health_report` | 体检报告（见上节） |

## 单元测试

| 测试文件 | 测试内容 |
|----------|----------|
| tests/features/test_features.py | 特征工程（5 个测试，含 global/rolling 归一化形状） |
| tests/models/test_models.py | 模型前向传播（6 个测试） |
| tests/strategies/test_registry.py | 策略注册 + 安全检查（8 个测试） |
| tests/strategies/test_ma_cross.py | MA 交叉策略（1 个测试） |
| tests/training/test_labels.py | 标签生成（3 个测试） |
| tests/training/test_model_registry.py | 模型注册表（3 个测试） |
| tests/training/test_dataset.py | 特征-标签对齐（2 个测试） |
| tests/training/test_trainer.py | 训练器（7 个测试）：快速训练、无数据标的跳过/全缺报错、global 归一化默认启用 + 统计量持久化、训练/推理特征一致、防退化机制（标签非单类 + 特征不漂移）、ML 策略 interpret_output 信号生成 |
| tests/training/test_ml_upgrade.py | M3 系列升级（7 个测试）：尺度不变特征（单位漂移不变性/量比/MA偏离）、ATR 自适应标签、体检报告生成、标签分布、指纹守门 |
| tests/data/test_datafeed.py | datafeed（13 个测试）：口径指纹、质量校验、涨跌停判定、体检摘要、mock 标记 |
| tests/backtest/test_backtest_engine.py | 回测引擎（8 个测试）：ma_cross 产生交易 + 净值/绩效指标、无真实数据回落 mock、空标的/数据不足/未知策略/多标的/壳策略无模型报错、壳策略绑定模型链路 |
| tests/backtest/test_backtest_rules.py | 撮合新规则（5 个测试）：tick 取整、新指标齐全、T+1 跳过、涨跌停跳过机制、结果持久化快照 |
| tests/test_integration.py | 端到端集成（2 个测试） |

共 71 个单元测试，全部通过。回测引擎使用 monkeypatch 隔离 xtquant 与数据库缓存，保证测试确定性且不依赖外部数据源。