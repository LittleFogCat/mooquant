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
| GBDT | models/gbdt.py | sklearn HistGradientBoosting 表格强基线（D3.3，v0.1.23） |

### GBDT 表格基线（D3.3，v0.1.23）

- 实现：`strategies/ml/models/gbdt.py` 的 `GBDTModel`，基于 **sklearn `HistGradientBoostingClassifier`**（LightGBM 的免依赖替代，CPU 友好、小样本稳健）。
- 输入 `(n, seq_len, n_features)` → 展平为 `(n, seq_len*n_features)`；仅支持分类任务（回归请用 torch 模型）。
- `predict_proba` 返回类别概率（与标签 0=sell/1=flat/2=buy 对齐，缺失类别补零）；`forward` 返回概率张量（`output_is_probability=True`，上层跳过 softmax）。
- **持久化**：`ModelRegistry` 按 `config.model_type == 'sklearn'` 走 pickle 整体保存（`model.pkl`），加载时包 `SklearnModelWrapper`（与 `DummyModelWrapper` 并列的非 torch 路径）。
- **训练**：`Trainer` 对 `is_sklearn` 模型跳过 torch 训练循环，直接 fit + 验证集准确率 + 体检报告（`_health_report_sklearn` 与 torch 版共用 `_score_health` 评分逻辑）。
- **策略**：`strategies/ml/builtin/gbdt_classifier.py`（`gbdt_classifier`），`ARCH_STRATEGY_MAP['gbdt']='gbdt_classifier'`，interpret 直接消费概率（不 softmax）。
- UI 模型页架构下拉已含 GBDT。

> 测试注意：本机环境在 sklearn 载入/训练后，同进程后续 torch 训练测试会异常变慢（环境性问题）——GBDT 测试放在 `tests/zz_gbdt/`（排序最后）执行。

## 期望收益决策（D3.5，v0.1.24）

替换 ML 策略的粗糙 `margin` 三分类判决，改为**期望收益语义**（`strategies/ml/base.py`）：

```
ev = P(up) × up_return − P(down) × down_return − roundtrip_cost
```

- **触发条件**：方向为最大类（P(up) 同时 > flat 与 down，或 P(down) 同时 > flat 与 up）且净期望收益覆盖阈值（`ev > min_ev` / `ev < −min_ev`）。
- **期望幅度来源**：策略参数 `upReturn`/`downReturn`（0=自动）→ 从模型 `label_config` 推导（classification → ±threshold；triple_barrier → ±up/down）。
- **策略参数**（4 个 ML 策略共用，UI 参数表单自动生成）：

| 参数 | 默认 | 语义 |
|---|---|---|
| `upReturn` | 0 | 判"涨"时的期望幅度（0=自动用训练标签阈值） |
| `downReturn` | 0 | 判"跌"时的期望幅度（0=自动用训练标签阈值） |
| `roundtripCost` | 0.003 | 一次往返交易成本，期望收益需覆盖它才触发 |
| `minEv` | 0 | 净期望收益额外阈值（0=覆盖成本即可） |

- 信号 reason 含「期望收益+x.xxx%」，indicators 含 `ev`（净期望收益），`strength` = 方向概率（供 D2.2 强度缩放仓位）。
- **意义**：把"概率差多少才交易"变成"期望收益是否覆盖成本才交易"——交易次数自动与成本结构对齐，低置信/高成本场景自动减少交易（直接改善"交易越多亏越多"）。
- 概率校准（temperature scaling，用验证集拟合）列为后续增强项（D3.5 可选部分）。

## Walk-forward 滚动样本外评估（D3.4，v0.1.25）

`Trainer.walk_forward(config, n_segments=3)`：数据按时间等分 `n_segments` 段，对每段 i≥1 用「该段之前」的数据重训模型、该段做**样本外评估**（不参与训练），输出各段准确率的均值/方差与稳定性结论。

- **无前视**：折叠训练区间必在测试区间之前（`test_walk_forward_folds_are_out_of_sample` 验证）。
- **实现**：折叠训练复用 `self.train()`（`save_model=False` / `return_model=True`，不落盘）；全部子训练统一区间模式保证数据同源；预测兼容 torch 与 sklearn（`_predict_labels`）；方向 F1 用 `_class_f1`。
- **触发**：训练配置 `walk_forward: true`（`walk_forward_segments` 默认 3）时，主训练完成后自动运行并随结果返回 `walk_forward` 报告；主模型照常落盘，折叠模型不落盘。
- **报告**：`{folds, meanAccuracy, stdAccuracy, nFolds, stable, verdict, summary}`；`stdAccuracy ≤0.05 且 mean ≥0.4` → green。
- **链路**：UI 训练表单「walk-forward 稳健性验证」复选框 + 段数 → model-server `_normalize_train_config` 透传 → 训练结果卡片展示稳定性徽章/摘要/折叠表。
- 仅支持分类标签（classification / triple_barrier）；数据量不足（每段 <20 根）或回归标签时报错（主训练不阻断，错误记入 `walk_forward_error`）。
- **意义**：把"样本外稳健性"从诊断工具（D4.2）升级为训练流程本身——各段准确率方差大 = 过拟合信号，是模型能否进入实盘验证的关键门槛之一。

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
  - **两种取数模式**：`fetch_bars(symbol, period, count)` 按数量取最近 N 根；`fetch_bars_range(symbol, period, start_date, end_date)` 按起止日期区间取数。`trainer.train` 在 `data.start_date`/`end_date` 同时存在时走区间模式，否则走 count 模式。两者都经统一 `datafeed`，口径（volume=股、front_ratio 前复权比例版）完全一致
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
- 训练表单（标的/周期/训练范围/数据量/起止日期/架构/超参数/标签类型）
  - **标的输入复用 `StockSearch` 多选模式**（`renderer/js/views/stock-search.js`）：与回测页/策略启动一致，输入代码/名称/拼音联想下拉，选中即以 `, ` 追加为逗号分隔的多标的标签式输入，支持删除重选
  - **训练范围（rangeMode）**：`count` 按数量取最近 N 根K线 / `date` 按起止日期区间取数（对齐多标的时间窗）。切到 `date` 模式时隐藏数据量、显示起止日期；ViewModel 校验起止日期必填且 start ≤ end，随训练请求传 `start_date`/`end_date`
  - **每个字段末尾带圆形轮廓问号角标**（`.form-hint`，`vertical-align:super` 上标、悬浮不改变鼠标指针，悬浮/聚焦显示 `FIELD_TIPS` 中该指标的意义/用途/范围/注意事项）
  - **数字输入框隐藏上下步进按钮**：`app.css` 统一 `input[type=number]` 移除 spinner（`-webkit-appearance:none` + 隐藏 `::-webkit-inner-spin-button`），训练表单与回测配置的数字框都不再显示上下箭头
- **训练状态（进度条 + 阶段文本）**：进度实时更新——`Trainer.train` 通过 `on_progress(progress, stage)` 回调，`TrainPipeline._update` 写入 `task.progress/stage`（fetch 数据加载 → build 构建数据集 → train 按 epoch 推进 20%~95% → save 保存模型 → done 100%）；`get_status` 返回 `{status, progress, stage}`，UI 轮询显示阶段与百分比
- 策略列表

### 路由

- 页面标识：`models`
- 进入时加载模型和策略列表
- 离开时停止训练状态轮询

## 统一数据访问层（v0.1.16，M1）

`bridge/data/datafeed.py` 是全项目**唯一**的行情取数入口（trainer / backtest_engine / model_server 全部经由此模块），严禁绕过它直接调 xtquant：

- **统一口径**：volume=股（内部 ×100）、`dividend_type='front_ratio'` 默认前复权比例版、UI码/xt码转换、bar 字段 `{date, open, high, low, close, volume}`
  - **为什么用 front_ratio（v0.1.16 修复）**：`front`（前复权）对早期历史数据（如茅台 2001-2016）会复权出**负/零价格**（60% 坏数据），导致训练样本被污染/不足、模型退化为单边预测。`front_ratio`（前复权比例版）无此 bug，且价格与 front 几乎一致（差异 <0.2%），全链路（训练/回测/推理/实盘）统一用它
  - `_df_to_bars` 增加**清洗**：丢弃价格 ≤0/NaN 的 bar，脏数据不再写入缓存或进入训练集
- **口径指纹**：`data_fingerprint(dividend_type)` 返回口径摘要哈希（`DATA_SPEC_VERSION=3`）。训练时写入模型 config.json 的 `data_fingerprint`；`MLStrategyBase.on_after_init` 加载模型时用 `check_fingerprint()` 校验，不一致直接报错（防静默口径漂移——「回测无交易」事故的根因防线）。`DATA_SPEC_VERSION` 改口径时必须 +1
- **数据质量校验**：`validate_bars()` 检查零价/OHLC交叉/>15自然日缺口，返回问题列表；`summary()` 输出体检摘要（日期范围/缺失天数/涨跌停bar占比）
- **涨跌停判定**：`is_limit_up/is_limit_down(xt_code, close, prev_close)`，按板块幅度（主板10%/创业科创20%/北交30%）
- **mock 回落**：`fetch_bars_or_mock()` 仅供回测用，mock bars 带 `is_mock=True` 标记；训练路径**不回落 mock**（避免模拟数据污染训练集）

### 数据健康监控 + 全市场同步（D1.4，v0.1.26）

轻量版数据地基（无 DuckDB/parquet 新依赖）：

- **`datafeed.coverage_report(ref_date=None, days_back=7)`**：数据健康/覆盖率报告——股票总数/已覆盖数/覆盖率、分市场统计（SH/SZ/BJ/US）、缓存最新日期、陈旧标的数、人话问题列表（覆盖率过低/数据陈旧/股票列表为空）。
- **`datafeed.sync_market(limit=None, period='1d', progress=None)`**：全市场日线增量同步——遍历 stocks 表逐标的走 `fetch_bars` 更新缓存（幂等，单标的失败记入 `errors` 不中断），返回 `{total, synced, skipped, updated_bars, errors}`。
- **CLI**：`python bridge/market_sync.py [--limit N] [--period 1d]`（需 miniQMT 连接）。
- 底层依赖 `db.get_kline_coverage()`（日线 front_ratio 缓存覆盖摘要）。

> 数据是模型上限的前提：覆盖率低/数据陈旧应先跑全市场同步再谈训练，避免"训练数据不完整"导致模型质量虚低或误判。

## 日志与诊断（D6，v0.1.27）

- **`bridge/_logging.py`（D6.1）**：轻量日志落盘——`data/logs/{module}.log`，带时间/级别/模块，超 5MB 轮转保留 `.1`；只写文件与 stderr（不碰 stdout，不影响 JSON-RPC）；任何日志失败静默降级。`backtest_engine.log()` 已接入（`data/logs/backtest.log`）。
- **`bridge/diagnose.py`（D6.2）**：一键诊断包导出——收集 `data/logs`、`config/default.json`、运行环境摘要（`env.json`，不含密钥）、最近 20 条回测摘要 → `data/diagnostics/diagnose_*.zip`。
  - CLI：`python bridge/diagnose.py`；Python API：`diagnose.build_diagnostic_package()`。
  - 用于排查/支持：拿到诊断包即可还原运行环境与近期回测行为。

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