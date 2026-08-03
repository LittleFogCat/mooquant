# 整体架构

mookquant 是一款基于 **Electron** 的量化投资桌面应用，采用 **MVVM + 进程分层 + 数据源可插拔 + 模型服务独立** 的架构。整体设计原则：

- **进程边界严格**：渲染层不能直接访问 Node API / QMT，全部走 IPC + 主进程
- **数据源可替换**：`DataSource` 抽象接口 + 多实现（QMT / Mock），工厂按 mode 决定
- **模型服务独立**：模型训练与管理通过独立 HTTP 子进程（`model_server.py`）暴露，与行情/交易进程解耦，不依赖 xtquant 连接状态
- **策略与模型统一**：规则策略和 ML 策略共享同一 `on_bar` 接口，信号计算路径对上层透明
- **UI 与逻辑解耦**：渲染层只声明"想要什么数据"，ViewModel 维护状态、View 只渲染 DOM
- **故障可降级**：QMT 不在线 -> 自动回落 Mock；模型服务不可用 -> 执行器回退 stdio RPC；桥接崩溃 -> 主进程负责重启

---

## 1. 进程拓扑

系统包含三个进程层：**渲染进程**（沙箱 UI）、**主进程**（Node.js 编排）、**Python 桥**（两个独立子进程）。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       渲染进程 Renderer（沙箱）                         │
│  ┌──────────┐  订阅   ┌─────────────┐  调用   ┌──────────────┐         │
│  │   View   │◀────────│  ViewModel  │────────▶│   Facade     │         │
│  │ (DOM+CSS)│         │  (状态+订阅) │        │ (preload暴露) │         │
│  └──────────┘         └─────────────┘        └──────┬───────┘         │
│                                                       │ contextBridge   │
└───────────────────────────────────────────────────────┼─────────────────┘
                                                        │ IPC
┌───────────────────────────────────────────────────────┼─────────────────┐
│                   主进程 Main（Node.js）               │                 │
│                                                        ▼                 │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                 IPC Router (main/ipc/index.js)                   │    │
│  └───┬───────┬────────┬────────┬────────┬──────────────────────────┘    │
│      ▼       ▼        ▼        ▼        ▼                               │
│  ┌────────┐┌────────┐┌────────┐┌────────┐┌──────────┐                  │
│  │QuoteSvc││StratSvc││Backtest││TradeSvc││ExecutorSvc│                  │
│  └───┬────┘└───┬────┘└────────┘└───┬────┘└────┬─────┘                  │
│      ▼         ▼                    ▼          │ HTTP /signal            │
│  ┌────────┐┌────────┐           ┌────────┐     ▼                         │
│  │DataSrc ││Strategy│           │TradeDS │ ┌───────────────┐             │
│  │├─Mock  ││Bridge  │           │├─Mock  │ │ ModelService  │             │
│  │└─QMT   ││(stdio) │           │└─QMT   │ │(spawn+HTTP代理)│            │
│  └───┬────┘└───┬────┘           └───┬────┘ └───────┬───────┘            │
│      │         │                    │              │                     │
└──────┼─────────┼────────────────────┼──────────────┼─────────────────────┘
       │         │                    │              │
   spawn(stdio)  │               spawn(stdio)    spawn+HTTP
       ▼         ▼                    ▼              ▼
┌──────────────────────┐  ┌──────────────────────────────────────────────┐
│  qmt_server.py       │  │  model_server.py (HTTP, port 8765)            │
│  (stdio JSON-RPC)    │  │  ── 模型层基建（独立 HTTP 服务）──             │
│  ├─ 行情快照/K线/订阅 │  │  ├─ 策略管理                                  │
│  ├─ 交易下单/撤单     │  │  ├─ 模型管理                                  │
│  ├─ 策略信号(RPC)     │  │  ├─ 训练管道                                  │
│  └─ 策略增删/导出     │  │  └─ 信号计算(/signal)                        │
└───────────┬──────────┘  └────┬──────────────────────┬───────────────────┘
            │ TCP               │ 文件系统              │ HTTP /signal
            │                   ▼                      │ (壳策略直接访问)
            │             ┌──────────────┐            │
            │             │  data/models/│            │
            │             │  ├─model.pt  │            │
            │             │  ├─meta.json │            │
            │             │  ├─config.json│           │
            │             │  ├─index.json│            │
            │             │  └─active.json│           │
            │             └──────────────┘            │
            │                                         │
            ▼                                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│  QMT 客户端 (国金 QMT)                                                   │
│                                                                          │
│  ┌────────────────┐      ┌──────────────────────────────────────────┐  │
│  │ miniQMT        │      │ qmt_shell.py (壳策略)                    │  │
│  │ (迅投 SDK)     │      │ 获取K线 → HTTP /signal → 下单            │  │
│  │ 行情/交易接口   │      │ (直接访问模型服务,不经主进程)            │  │
│  └────────────────┘      └──────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

**关键点**：行情/交易桥（`qmt_server.py`）和模型服务（`model_server.py`）是两个完全独立的 Python 子进程。模型服务不依赖 xtquant 连接，即使 QMT 离线，模型管理和训练仍然可用（训练使用 MockDataFetcher 生成模拟数据，或内置数据获取器）。

---

## 2. 分层职责

| 层 | 路径 | 职责 | 允许依赖 |
|---|---|---|---|
| **视图层 View** | `renderer/js/views/` | 纯 DOM 渲染、事件绑定、SVG 绘制 | ViewModel（通过 subscribe） |
| **视图模型层 ViewModel** | `renderer/js/viewmodels/` | 维护页面状态、调用 Facade、订阅 State 变更 | Facade |
| **门面层 Facade** | `renderer/js/services/facade.js` + `preload.js` | 把 IPC 命令封装成业务方法；浏览器演示模式自带 mock | IPC / window 全局 |
| **IPC 路由** | `main/ipc/index.js` | 集中注册所有 `ipcMain.handle`，做参数透传 | Service |
| **业务服务 Service** | `main/services/` | QuoteService / StrategyService / TradeService / BacktestService / ExecutorService / **ModelService** / ConfigManager / LogService | DataSource、HTTP 代理、文件系统 |
| **数据源层 DataSource** | `main/datasources/` | 抽象接口；Mock / QMT 实现 | Python 桥 / 文件 |
| **外部桥 Bridge** | `bridge/` | Python 进程；`qmt_server.py`（stdio JSON-RPC）+ `model_server.py`（HTTP） | xtquant / miniQMT / PyTorch |

---

## 3. 数据流：股票查询

```
用户在搜索框输入 "sh600519"
    |
    v
SearchPanel (View) -> 触发 ViewModel.querySymbol(code)
    |
    v
QuoteViewModel.querySymbol
    |- 写入 state.loading = true, state.symbol = code
    |- 调用 facade.quote.query(code)
    |      +- ipcRenderer.invoke("quote:query", code)
    v
主进程 IPC: quote:query -> QuoteService.query
    |- 查缓存（_cacheGet），命中直接返回
    |- 调 DataSource.getQuote(code)
    |      |- Mock: 本地表随机生成
    |      +- QMT: spawn qmt_server.py -> quote.snapshot -> xtquant
    |- 写入缓存（_cacheSet）
    +- 返回 { ok, data }
    |
    v
QuoteViewModel 收到结果 -> 更新 state.data
    |
    v
ResultCardView (订阅 state) -> 重渲染 DOM
```

---

## 4. 数据源可插拔设计

`main/datasources/index.js` 是工厂入口：

```js
createDataSource({ mode, qmt })      // mode in { mock, qmt, auto }
createTradeDataSource({ mode, qmt })  // 交易源同理
```

| mode | 行为 |
|---|---|
| `mock` | 始终使用 `MockDataSource` / `MockTradeDataSource`，离线演示 |
| `qmt` | 直接初始化 `QmtDataSource`；启动失败抛错 |
| `auto` | 优先 QMT；连接/初始化异常时自动回落 mock，并在控制台打印 warn |

`DataSource` 接口约定（行情）：

```js
class DataSource {
  mode: string           // "mock" | "qmt"
  description: string
  async getQuote(rawSymbol) -> object | null
  async getHistory(symbol, period, count, dividendType) -> { bars: [...] }
  async getStockList() -> { stocks: [...] }              // 可选
  async syncStocks() -> { total, stocks }                // 可选
  onPush(event, cb) -> unsubscribe                        // 实时推送
  dispose()
}
```

新增数据源（例如 Tushare / Wind）只需新增一个实现类并在工厂中注册，不影响上层代码。

---
## 5. 模型管理架构

模型管理是 mooquant 的核心子系统，负责量化模型的**训练、持久化、激活和信号计算**。设计目标是让上层（QMT 壳策略 / 本地策略执行器）无需关心模型细节，通过统一的 HTTP 接口消费信号。

### 5.1 架构总览

```
渲染层（Renderer）
  ModelViewModel <-> ModelView (模型管理页面)
       | facade.modelServer.*
主进程（Main）
  ModelService (main/services/model-service.js)
  |- spawn model_server.py (HTTP 子进程)
  |- 轮询 /health 等待就绪
  +- HTTP 代理（IPC 调用 -> HTTP 请求转发）
Python 模型服务（bridge/）
  model_server.py (HTTP, port 8765)
  |- strategies/          策略框架（规则 + ML 共用）
  |  |- base.py           StrategyBase + Signal + Context
  |  |- registry.py       自动扫描注册
  |  |- builtin/          内置规则策略
  |  +- ml/               ML 策略
  |     |- base.py        MLStrategyBase（延迟导入 torch）
  |     |- features.py    FeatureBuilder（特征工程）
  |     |- models/        模型架构（LSTM/Transformer/MLP）
  |     +- builtin/       内置 ML 策略
  |- training/            训练管道
  |  |- trainer.py        Trainer（训练循环+早停+学习率调度）
  |  |- dataset.py        FinancialDataset
  |  |- labels.py         标签生成（3 种）
  |  |- model_registry.py ModelRegistry（LRU 缓存+持久化）
  |  |- pipeline.py       TrainPipeline（异步训练）
  |  +- builtin_models.py 内置模型（Dummy/Linear/LSTM）
  +- qmt_shell.py         QMT 壳策略模板
消费方
  QMT 壳策略 (qmt_shell.py)     本地策略执行器 (executor-service.js)
  QMT 客户端内运行                主进程定时调度
  HTTP /signal                   HTTP /signal -> 回退 stdio RPC
```

### 5.2 三层通信链路

模型管理的调用链路严格分为三层，每层职责清晰：

| 层 | 组件 | 协议 | 职责 |
|---|---|---|---|
| **渲染层** | `ModelViewModel` + `ModelView` | IPC | UI 状态管理、训练表单、轮询训练状态 |
| **主进程代理** | `ModelService` | HTTP | spawn 子进程、健康检查、HTTP 请求转发 |
| **Python 服务** | `model_server.py` | HTTP | 策略/模型 CRUD、训练调度、信号计算 |

```
UI 操作 (训练/激活/删除)
  |
  v ModelViewModel
facade.modelServer.startTraining(config)
  |
  v preload.js (ipcRenderer.invoke)
IPC: model:train -> ModelService.startTraining(config)
  |
  v HTTP POST /train
model_server.py -> TrainPipeline.start_train(config)
  |
  v 后台线程
Trainer.train(config) -> ModelRegistry.save(model, ...)
```

### 5.3 模型服务（model_server.py）

`bridge/model_server.py` 是一个基于 Python stdlib `http.server` 的 HTTP 服务，**零外部依赖**（torch 为可选依赖）。

**启动流程**：
1. `_ensure_loaded()`：加载所有策略（`load_all()`）、导入 ML 模型模块（触发 `@register_model`）、创建内置模型（`ensure_builtin_models()`）
2. 绑定 `127.0.0.1:{port}`，开始 `serve_forever()`
3. 每个请求入口调用 `_ensure_loaded()`（幂等，仅首次执行）

**零依赖设计**：
- HTTP 服务使用 `http.server.HTTPServer` + `BaseHTTPRequestHandler`，不依赖 Flask/FastAPI
- torch 在 `MLStrategyBase`/`Trainer`/`FeatureBuilder` 中延迟导入，不可用时优雅降级
- `DummyModelWrapper` 提供纯 Python 模型推理，无需 torch 依赖

### 5.4 HTTP 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（返回策略数、模型数） |
| GET | `/strategies` | 列出所有策略元数据 |
| GET | `/strategy/{name}` | 获取单个策略元数据 |
| POST | `/strategy` | 添加策略（name + code） |
| DELETE | `/strategy/{name}` | 删除策略 |
| GET | `/models` | 列出所有模型（读 index.json） |
| GET | `/models/active` | 获取当前激活模型（model_id + strategy + meta） |
| GET | `/models/{id}` | 获取模型详情（meta + config） |
| PUT | `/models/{id}` | 更新模型元数据（名称等） |
| DELETE | `/models/{id}` | 删除模型（含文件 + 缓存清理） |
| POST | `/models/{id}/activate` | 激活模型（自动推断关联策略） |
| POST | `/train` | 启动异步训练（返回 task_id） |
| GET | `/train/{id}/status` | 查询训练状态（status/progress/result） |
| POST | `/signal` | 计算策略信号（不传 strategy 时自动用激活模型） |

**配置**：端口默认 8765，通过环境变量 `MODEL_SERVER_PORT` 或 `config/default.json` 的 `modelServer.port` 配置。

### 5.5 模型架构注册表

模型架构通过装饰器注册（`strategies/ml/models/registry.py`）：

```python
from strategies.ml.models.registry import register_model

@register_model("lstm")
class LSTMModel(nn.Module):
    ...
```

上层通过 `build_model(arch, params)` 统一实例化，不直接 import 具体类。

| 架构 | 文件 | 特点 |
|------|------|------|
| LSTM | `models/lstm.py` | 多层 LSTM，取最后时刻隐状态 -> FC，适用时序预测 |
| Transformer | `models/transformer.py` | 含 PositionalEncoding + TransformerEncoder，均值池化 -> FC |
| MLP | `models/mlp.py` | 展平输入 -> 多层感知机，基线模型 |

所有架构共享统一接口：
- **输入**：`(batch, seq_len, n_features)` 张量
- **输出**：分类任务返回 `(batch, output_size)` logits；回归任务返回 `(batch,)`
- **参数**：`input_size` / `output_size` / `task` 由 Trainer 根据标签类型自动注入

### 5.6 特征工程

`FeatureBuilder`（`strategies/ml/features.py`）负责将 K 线数据转换为归一化特征张量，**训练和推理共用同一个实例**，防止特征漂移。

**配置项**：

| 配置 | 类型 | 说明 |
|------|------|------|
| `window` | int | 特征窗口大小（默认 20） |
| `indicators` | list | 技术指标配置 `[{name, params}]` |
| `raw_features` | list | 原始字段 `['close', 'volume', ...]` |
| `derived` | list | 衍生特征 `['return_1d', 'return_5d', 'volatility_20']` |
| `normalize` | str | 归一化方式：`zscore` / `minmax` / `none` |
| `normalize_window` | int | 滚动归一化窗口（默认 20） |

**支持的技术指标**：MA / EMA / RSI / MACD / BOLL / KDJ（由 `strategies/indicators.py` 提供）。

**两个构建方法**：
- `build(bars)` -> 单样本推理，返回 `(1, window, n_features)` 张量
- `build_batch(bars)` -> 批量训练样本，返回 `(n_samples, window, n_features)` 张量

### 5.7 训练管道

训练分为异步调度层和同步执行层：

```
POST /train (config)
    |
    v
TrainPipeline.start_train(config)
    |- 生成 task_id（train_{timestamp}）
    |- 记录初始状态 {status: "running", progress: 0}
    +- 启动后台线程 -----------------+
                                      v
                              Trainer.train(config)
                                |- 1. 解析配置（数据/特征/标签/模型/训练参数）
                                |- 2. 获取数据（DataFetcher 接口）
                                |      +- XtdataDataFetcher：通过 xtquant 拉真实行情
                                |      +- MockDataFetcher：生成随机行情（测试/离线）
                                |- 3. 特征工程（FeatureBuilder.build_batch）
                                |- 4. 标签生成（classification/regression/triple_barrier）
                                |- 5. 数据集构建（FinancialDataset，时序分割，不 shuffle）
                                |- 6. 模型构建（build_model，自动推断 task/output_size）
                                |- 7. 训练循环
                                |      +- Adam 优化器 + weight_decay
                                |      +- ReduceLROnPlateau 学习率调度
                                |      +- CrossEntropyLoss（分类）/ MSELoss（回归）
                                |      +- 早停（patience 轮无改善则停止）
                                |      +- 保存最优模型 state_dict
                                |- 8. 恢复最优权重 -> ModelRegistry.save()
                                +- 返回 {model_id, metrics, train_log, ...}
```

**标签生成**（`training/labels.py`）：

| 类型 | 函数 | 说明 |
|------|------|------|
| classification | `make_classification_labels` | 未来 N 根 bar 收益率 > 阈值 -> 买入(2)；< -阈值 -> 卖出(0)；否则 -> 持有(1) |
| regression | `make_regression_labels` | 未来 N 根 bar 收益率（连续值） |
| triple_barrier | `make_triple_barrier_labels` | 三重障碍法（止盈/止损/时间止损），Lopez de Prado |

### 5.8 模型注册表与持久化

`ModelRegistry`（`training/model_registry.py`）管理模型的全生命周期：保存、加载、列表、更新、删除。

**存储结构**：
```
data/models/
|- {model_id}/              # 每个模型一个目录
|  |- model.pt             # PyTorch 权重（Dummy 模型无此文件）
|  |- meta.json            # 元数据（id/name/arch/created_at/metrics/symbols/status）
|  +- config.json          # 训练配置（arch/params/feature_config/label_config/train_config/data）
|- index.json               # 所有模型元数据索引（列表查询用）
+- active.json              # 激活模型（model_id + strategy）
```

**LRU 缓存**：
- 内存中最多缓存 10 个模型（`_cache_max = 10`）
- 加载时先查缓存（命中则移到队尾），未命中则从磁盘加载
- 超出上限时淘汰队首（最久未用）
- 线程安全：`threading.Lock` 保护所有读写操作

**ModelWrapper 抽象**：
- `TorchModelWrapper`：包装 `torch.nn.Module`，`forward()` 在 `torch.no_grad()` 下执行
- `DummyModelWrapper`：纯 Python 模型，返回输入不变，无需 torch 依赖

### 5.9 内置模型

`training/builtin_models.py` 在模型服务启动时自动创建三个内置模型（`ensure_builtin_models()`），确保无需训练即可演示信号计算：

| 模型 ID | 架构 | 说明 |
|---------|------|------|
| `builtin_dummy` | dummy | 纯 Python 模型，始终返回 hold，无 torch 依赖 |
| `builtin_linear` | mlp | 小型 MLP，Mock 数据快速训练（5 epochs） |
| `builtin_lstm` | lstm | 小型 LSTM，Mock 数据快速训练（5 epochs） |

内置模型的 `status` 字段标记为 `"builtin"`，训练参数和特征配置写入 `config.json`，可被正常加载和激活。

### 5.10 ML 策略

`MLStrategyBase`（`strategies/ml/base.py`）是 ML 策略的基类，继承自 `StrategyBase`：

```python
class MLStrategyBase(StrategyBase):
    is_ml = True
    model_arch = ''

    def on_after_init(self, ctx):
        # 1. 优先使用 params.model_id
        # 2. 未指定时自动读取激活模型
        # 3. 加载模型（ModelRegistry.load）+ 特征构建器

    def on_bar(self, bar, ctx):
        features = self.build_features(ctx.bars)   # -> FeatureBuilder.build()
        output = self._wrapper.forward(features)    # -> 模型推理
        return self.interpret_output(output, ...)   # -> 子类实现
```

**延迟导入 torch**：torch 仅在 `on_after_init` 加载模型时导入。如果 torch 不可用，ML 策略会在初始化时抛出异常，但不会影响规则策略和模型服务本身。

**内置 ML 策略**：

| 策略 | 文件 | 说明 |
|------|------|------|
| `lstm_trend` | `builtin/lstm_trend.py` | LSTM 趋势预测：softmax -> 上涨概率超阈值买入，低于阈值卖出 |

子类只需实现 `interpret_output(output, bar, ctx)`，将模型输出转换为 `Signal` 对象。

### 5.11 激活模型机制

模型管理支持「激活」功能，设定一个默认模型供所有 ML 策略使用：

```
POST /models/{id}/activate
    |
    v
_set_active_model(model_id)
    |- 读取模型 meta.arch
    |- 通过 _ARCH_STRATEGY_MAP 推断关联策略
    |     lstm -> lstm_trend（其他架构默认 lstm_trend）
    +- 写入 data/models/active.json: {model_id, strategy}
```

**消费方无需关心 model_id**：

| 消费方 | 路径 | 自动激活行为 |
|--------|------|-------------|
| HTTP `/signal`（不传 strategy） | model_server.py 读取 active.json | 自动填入 strategy + model_id |
| stdio RPC `strategy.signal` | MLStrategyBase.on_after_init | 无 model_id 时读 active.json |
| QMT 壳策略 | qmt_shell.py POST /signal | 不传 strategy，服务端自动处理 |
| 本地执行器（shell 类型） | executor-service.js | 不传 strategy，服务端自动处理 |

### 5.12 策略执行器集成

本地策略执行器（`executor-service.js`）的信号计算采用**双路径设计**：

```
Executor.tick()
    |
    |- 获取 K 线数据（QuoteService.getHistory）
    |
    v 信号计算（双路径）
    |- 路径 A：modelService.computeSignal(payload) -> HTTP POST /signal
    |     |- shell 策略：不传 strategy，服务端用激活模型
    |     +- 指定策略：传 strategy 名称，服务端用指定策略
    |
    +- 路径 B（回退）：strategyBridge.strategySignal() -> stdio RPC
          +- 当 modelService 为 null 时使用
    |
    v 根据 signal.action 执行交易
    |- buy -> 检查持仓 -> 下单
    |- sell -> 查持仓 -> 下单
    +- hold -> 跳过
```

**注意**：当前 `main.js` 中 `ExecutorService` 在 `ModelService.init()` 之前创建，导致 ExecutorService 持有的 `modelService` 为 null，信号计算实际走路径 B（stdio RPC 回退）。后续可通过调整初始化顺序或注入机制修复。

### 5.13 QMT 壳策略集成

`bridge/qmt_shell.py` 是在 QMT 客户端中运行的壳策略，**不含任何策略逻辑**，只做三件事：

1. **`init`**：设置标的池
2. **`handlebar`**（每根 K 线触发）：
   - 从 QMT 获取 K 线数据（`xtdata.get_market_data_ex`）
   - POST 到模型服务 `/signal` 端点（携带 strategy + bars + params + symbol）
   - 根据返回的 signal.action 执行交易（`xttrader.order_stock`）
3. **配置区**：顶部可修改 `SERVER_URL` / `STRATEGY` / `SYMBOL` / `PERIOD` / `COUNT` / `ACCOUNT`

ML 策略无需在壳策略中填 model_id，激活模型机制会自动处理。

### 5.14 主进程集成（ModelService）

`ModelService`（`main/services/model-service.js`）是主进程对模型服务的封装：

**生命周期**：
1. `init()` -> spawn `bridge/model_server.py`（使用 `MOOKQUANT_PYTHON` 环境变量指定的 Python）
2. 轮询 `GET /health`（最多 30 次 x 200ms = 6s 超时）
3. 就绪后标记 `_ready = true`
4. `dispose()` -> kill 子进程

**HTTP 代理方法**：所有公共方法返回 `{ ok, data? / error? }` 格式，封装了 GET/POST/PUT/DELETE 四种 HTTP 方法：

| 方法 | HTTP | 说明 |
|------|------|------|
| `status()` | GET /health | 服务状态 + 策略数 + 模型数 |
| `listStrategies()` | GET /strategies | 策略列表 |
| `getStrategy(name)` | GET /strategy/{name} | 单个策略元数据 |
| `addStrategy(payload)` | POST /strategy | 添加策略 |
| `deleteStrategy(name)` | DELETE /strategy/{name} | 删除策略 |
| `listModels()` | GET /models | 模型列表 |
| `getModel(id)` | GET /models/{id} | 模型详情（meta + config） |
| `updateModel(id, patch)` | PUT /models/{id} | 更新模型元数据 |
| `deleteModel(id)` | DELETE /models/{id} | 删除模型 |
| `activateModel(id)` | POST /models/{id}/activate | 激活模型 |
| `getActiveModel()` | GET /models/active | 获取激活模型 |
| `startTraining(config)` | POST /train | 启动训练 |
| `getTrainingStatus(taskId)` | GET /train/{id}/status | 查询训练状态 |
| `computeSignal(payload)` | POST /signal | 计算信号 |

### 5.15 IPC 路由与 Preload

**IPC 路由**（`main/ipc/index.js`）：

| IPC 通道 | 说明 |
|----------|------|
| `model:status` | 查询模型服务状态 |
| `model:strategies` | 列出策略 |
| `model:strategy` | 获取单个策略 |
| `model:addStrategy` | 添加策略 |
| `model:deleteStrategy` | 删除策略 |
| `model:models` | 列出模型 |
| `model:getModel` | 获取模型详情 |
| `model:updateModel` | 更新模型元数据 |
| `model:deleteModel` | 删除模型 |
| `model:activate` | 激活模型 |
| `model:active` | 获取激活模型 |
| `model:train` | 启动训练 |
| `model:trainStatus` | 查询训练状态 |
| `model:signal` | 计算信号 |

**Preload**（`preload.js`）：暴露 `window.mookquant.facade.modelServer` 对象，包含上述所有方法的 Promise 封装。浏览器演示模式下 facade.js 提供 mock 实现（全部返回空数据或 hold 信号）。

### 5.16 UI 页面

**模型管理页**（`renderer/js/views/model-view.js` + `viewmodels/model-viewmodel.js`）：

- **服务状态卡片**：状态灯 + 端口号 + 刷新按钮
- **模型列表表格**：名称 / model_id / 架构 / 创建时间 / 标的 / 状态 / 操作（激活/删除）
- **训练表单**：标的代码 / 周期 / 数据量 / 架构 / epochs / 学习率 / 隐藏层大小 / batch_size / 标签类型
- **训练状态**：轮询训练状态（2s 间隔），训练完成自动刷新模型列表
- **激活模型指示**：当前激活的 model_id

**ViewModel 状态管理**：
- `state.serviceReady` / `servicePort`：服务状态
- `state.models` / `modelsLoading`：模型列表
- `state.training`：`{ active, taskId, status, progress }` 训练状态
- `state.activeModelId`：激活模型 ID
- 进入页面时 `loadAll()`（加载状态 -> 加载模型 + 激活模型），离开时 `stopPolling()`

---
## 6. 配置与持久化

| 配置/数据 | 路径 | 说明 |
|---|---|---|
| 应用静态配置 | `config/default.json` | 窗口尺寸、QMT 连接参数、缓存 TTL、数据源 mode、`modelServer.port` |
| 策略定义 | `data/strategies/<id>.json` | StrategyService 管理 |
| 用户策略源码 | `data/strategies/user/*.py` | UI 编写/编辑保存，`strategy.add` RPC 写入 |
| 模型文件 | `data/models/{model_id}/` | `model.pt` + `meta.json` + `config.json` |
| 模型索引 | `data/models/index.json` | 所有模型元数据列表 |
| 激活模型 | `data/models/active.json` | `{model_id, strategy}` |
| 执行日志 | `data/logs/...` | LogService 落盘（交易日志 + 每日净值） |
| UI 偏好 | `localStorage` | 侧边栏折叠、最近搜索等 |

所有配置文件改动后通过 `ConfigManager.set(patch)` 立即生效，部分项（如 `dataSource`）需要重启应用。

---

## 7. 安全与沙箱

- `contextIsolation: true` + `nodeIntegration: false`，渲染层无 Node 能力
- preload 仅暴露 `window.mookquant.facade`，接口面最小化
- CSP 由 `main.js -> installCspHeader()` 注入：`default-src 'self' file: data: blob:`、`script-src 'self' file: 'unsafe-inline'`
- `setWindowOpenHandler` 拦截 `window.open`，外链统一走系统浏览器
- 渲染层只能访问 `dist/renderer/` 产物；主进程业务代码仅在主进程执行
- 模型服务仅监听 `127.0.0.1`，不对外暴露

---

## 8. 启动流程（main.js）

```
1. 解析 .env（手动解析，避免 dotenv 依赖）
2. app.whenReady()
   |- 安装 CSP header
   |- 创建 ConfigManager -> 读取 config/default.json
   |- 显示 splash 窗口（启动进度条）
   |- 初始化 StrategyService
   |- createDataSource({ mode: dataSource })
   |     +- 主进程订阅 source.onPush("tick") -> webContents.send("quote:tick", ...)
   |- 创建 QuoteService（含股票列表预加载 + QMT 后台同步）
   |- createTradeDataSource(...) + TradeService
   |- BacktestService + ExecutorService.restoreRunning()    // 恢复上次运行的策略
   |- ModelService.init()                                    // spawn model_server.py + 轮询 /health
   |     +- 失败时 modelService = null（不阻塞启动，模型功能降级）
   |- registerIpc(...)                                      // 注册全部 IPC 路由
   +- 关闭 splash，创建主窗口
3. mainWindow.once("ready-to-show") -> maximize + show
4. app.on("window-all-closed") -> 资源 dispose（含 modelService.dispose）-> 退出
```

**模型服务容错**：`ModelService.init()` 包裹在 try/catch 中，启动失败时 `modelService` 置为 null，不阻塞应用启动。IPC 路由中对 `modelService` 做 null 检查，模型相关功能降级为返回错误信息。

---

## 9. 目录速查

```
mooquant/
|- main.js                       # 主进程入口
|- preload.js                    # contextBridge 注入 facade
|- config/default.json           # 应用配置（含 modelServer.port）
|- main/
|  |- ipc/index.js              # IPC 路由（含 model:* 全部路由）
|  |- services/
|  |  |- quote-service.js
|  |  |- strategy-service.js
|  |  |- backtest-service.js
|  |  |- trade-service.js
|  |  |- executor-service.js    # 策略实盘调度（HTTP /signal 优先，回退 stdio RPC）
|  |  |- model-service.js       # 模型服务管理（spawn model_server.py + HTTP 代理）
|  |  |- config-manager.js
|  |  +- log-service.js
|  |- datasources/               # 数据源抽象与实现
|  |  |- index.js               # 工厂（auto 回落逻辑）
|  |  |- mock.js
|  |  |- qmt.js                 # QmtDataSource（stdio JSON-RPC + strategy.* RPC）
|  |  +- trade/
|  +- utils/pinyin.js
|- bridge/                        # Python 子进程
|  |- qmt_server.py              # stdio JSON-RPC（行情+策略信号+交易）
|  |- model_server.py            # HTTP 模型服务（模型管理+训练+信号计算）
|  |- qmt_shell.py               # QMT 壳策略模板（不含逻辑，仅 HTTP 转发）
|  |- backtest_engine.py         # 回测引擎
|  |- db.py                      # 本地行情缓存
|  |- strategies/                # 策略框架（回测与实盘共用）
|  |  |- base.py                # StrategyBase + Signal + Context（跨平台接口）
|  |  |- indicators.py          # 技术指标库（MA/EMA/MACD/RSI/KDJ/BOLL）
|  |  |- registry.py            # 自动扫描注册（builtin/ + data/strategies/user/）
|  |  |- builtin/               # 内置规则策略（ma_cross/momentum/mean_reversion）
|  |  |- ml/                    # ML 策略
|  |  |  |- base.py            # MLStrategyBase（延迟导入 torch，自动使用激活模型）
|  |  |  |- features.py        # FeatureBuilder（特征工程，训练/推理共用）
|  |  |  |- models/            # 模型架构（注册表模式）
|  |  |  |  |- registry.py    # @register_model + build_model
|  |  |  |  |- lstm.py        # LSTM 架构
|  |  |  |  |- transformer.py # Transformer 架构
|  |  |  |  +- mlp.py         # MLP 架构
|  |  |  +- builtin/           # 内置 ML 策略（lstm_trend）
|  |  +- exporters/             # 平台导出器（qmt_exporter 适配壳）
|  |- training/                  # 训练管道
|  |  |- trainer.py             # Trainer（训练循环+早停+学习率调度）
|  |  |- dataset.py             # FinancialDataset（多标的滑动窗口）
|  |  |- labels.py              # 标签生成（classification/regression/triple_barrier）
|  |  |- model_registry.py      # ModelRegistry（LRU 缓存+持久化+ModelWrapper）
|  |  |- pipeline.py            # TrainPipeline（异步训练，后台线程）
|  |  +- builtin_models.py      # 内置模型（Dummy/Linear/LSTM，启动时自动创建）
|  +- requirements.txt
|- renderer/                      # 渲染层（vite 构建）
|  |- index.html
|  |- splash.html
|  |- css/{base,app,flatpickr-override}.css
|  +- js/
|     |- main.js                # 入口
|     |- router.js              # 单页路由（6 个页面：quote/strategy/backtest/trade/models/settings）
|     |- services/facade.js     # 屏蔽 IPC 细节 + 浏览器 mock（含 modelServer mock）
|     |- viewmodels/            # 7 个 VM（quote/strategy/backtest/trade/model/settings/watchlist）
|     |- views/                 # 12 个视图组件（含 ModelView）
|     +- utils/pinyin.js
|- tests/                         # 单元测试（pytest，20 个）
+- docs/
   |- api/                       # API 接口文档（model-server-api.md）
   |- third/                     # 第三方 API 参考
   +- .ai/
      |- plan/                  # 项目计划文档
      +- knowledge-library/     # 本知识库
```

---

## 10. 后续扩展建议

- **多数据源**：在 `datasources/index.js` 注册新工厂分支；接口保持不变
- **更多策略**：在 `bridge/strategies/builtin/` 新增 `.py` 自动注册；或通过 UI 编写自定义策略；所有策略（含内置）均可在 UI 中编辑源码并保存覆盖
- **更多模型架构**：在 `bridge/strategies/ml/models/` 用 `@register_model` 注册新架构
- **更多 ML 策略**：在 `bridge/strategies/ml/builtin/` 新增，继承 `MLStrategyBase`，实现 `interpret_output`
- **修复执行器初始化顺序**：当前 `ExecutorService` 在 `ModelService.init()` 之前创建，导致 `modelService` 为 null。应调整 main.js 初始化顺序或改用 setter 注入
- **模型服务进程监控**：当前 model_server.py 崩溃后不会自动重启（不像 qmt_server 有重启机制），可增加进程监控 + 自动拉起
- **训练数据源扩展**：`DataFetcher` 接口已抽象，可新增 Tushare/AkShare 等数据获取器，不依赖 xtquant
- **替换 UI 框架**：当前 View 层是 vanilla ES6，渐进替换为 Vue/React 不影响主进程与 IPC 协议
- **跨端**：将渲染层打包为 Web 部署，主进程保留作为桌面端桥（需调整 IPC 通道）