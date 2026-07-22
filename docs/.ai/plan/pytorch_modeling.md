# 模型管理与建模系统详细设计

> 日期：2026-07-23
> 状态：待审核

## 一、目标与定位

### 1.1 核心目标

本系统聚焦**工程设计质量**：模型管理、通用性、易用性。

- **模型管理**：统一的策略/模型注册、版本、参数配置、生命周期管理
- **通用性**：策略接口不绑定具体实现（规则/ML/实验性PyTorch均通过同一接口）
- **易用性**：丢文件即注册、UI 表单化、HTTP 一键暴露

### 1.2 分层定位

| 层级 | 定位 | 说明 |
|------|------|------|
| 接口层 | StrategyBase + Signal + Context | 跨平台统一接口，上层唯一依赖 |
| 实现层-规则 | builtin/ 中的传统策略 | MA交叉、动量、均值回归等 |
| 实现层-ML | ml/ 中的机器学习策略 | 特征工程 + 模型推理 |
| 实现层-实验 | ml/models/ 中的 PyTorch 架构 | LSTM/Transformer/MLP，实验性质 |
| 服务层 | model_server.py (HTTP) | 对外暴露，不感知实现细节 |
| 接入层 | qmt_shell.py | QMT 壳策略，只调 HTTP |

### 1.3 设计原则

1. **接口隔离**：上层只依赖 StrategyBase 接口，不感知底层是规则还是 ML
2. **增量修改**：现有 strategies/ 框架不动，新能力作为子模块扩展
3. **可测试性**：每个业务模块可独立测试，依赖可注入，有 mock 实现
4. **训练与推理解耦**：训练是离线任务，推理是在线计算，通过模型文件连接
5. **PyTorch 为实验性质**：不作为系统核心依赖，缺失时系统仍可正常运行
## 二、架构设计

### 2.1 分层架构

接入层: QMT壳策略(qmt_shell.py) + 本地UI(renderer)
服务层: model_server.py(HTTP) + main.js(IPC路由)
管理层: registry.py + model_registry.py + trainer.py + pipeline.py
接口层: base.py(StrategyBase+Signal+Context) + ml/base.py(可选)
实现层: builtin/(规则) + ml/builtin/(ML) + ml/models/(实验PyTorch)

### 2.2 接口隔离设计

上层不感知底层实现：
- registry.get 返回的策略类，无论规则还是ML，都走同一个 on_bar -> Signal 流程
- 服务层(HTTP/UI)完全不感知策略实现细节
- ModelRegistry.load 返回统一的 (model, config) 二元组

### 2.3 PyTorch 可选依赖设计

- ml/base.py 中延迟导入 torch：只有 ML 策略 on_after_init 才导入
- model_server.py 启动时不强制导入 torch
- 规则策略的 /signal 完全不依赖 torch
- 缺失 torch 时系统仍可正常运行
## 三、接口定义

### 3.1 策略接口 (StrategyBase)

已有，不修改。核心契约：
- 类属性: name, display_name, description, params_schema
- 生命周期: on_init, on_after_init, on_bar(必须实现), on_stop
- on_bar 返回 Signal 或 None
- metadata() 返回元数据字典

### 3.2 信号接口 (Signal)

已有，不修改。
- action: buy | sell | hold
- reason: str
- strength: float [0,1]
- target_position: float | None
- indicators: dict

### 3.3 模型管理接口 (ModelRegistry)

- save(model, config, metrics, name) -> model_id
- load(model_id) -> (model, config)
- list_models() -> List[dict]
- get_meta(model_id) -> dict
- delete(model_id) -> bool

### 3.4 训练接口 (Trainer)

- train(config: dict) -> dict
- config 包含: data, feature, label, model, train 配置
- 返回: model_id, metrics, train_log

### 3.5 HTTP 服务接口

策略管理:
- GET /strategies -> 策略列表
- GET /strategy/<name> -> 策略元数据
- POST /strategy -> 注册新策略
- DELETE /strategy/<name> -> 删除策略

信号计算:
- POST /signal -> {strategy, bars, params, symbol} -> {action, reason, indicators}

模型管理:
- GET /models -> 模型列表
- GET /models/<id> -> 模型详情
- DELETE /models/<id> -> 删除模型

训练:
- POST /train -> {task_id}
- GET /train/<task_id>/status -> {status, progress, result}

心跳:
- GET /health -> {status, strategies, models}
## 四、模块详细设计

### 4.1 ML 策略基类 (bridge/strategies/ml/base.py)

职责：ML 策略通用骨架，子类实现特征构建和输出解释。

生命周期：
- on_init: 读取 model_id 参数
- on_after_init: 延迟导入 torch，加载模型 state_dict，创建 FeatureBuilder
- on_bar: build_features -> model.forward -> interpret_output -> Signal
- on_stop: 释放模型引用

子类必须实现：
- build_features(bars) -> torch.Tensor | None
- interpret_output(output, bar, ctx) -> Signal | None

类属性：
- is_ml = True：标记为 ML 策略
- model_arch: str：关联模型架构名

设计要点：
- torch 延迟导入：缺失时规则策略不受影响
- 模型通过 ModelRegistry.load(model_id) 加载
- FeatureBuilder 配置从模型 config.json 自动恢复
- model.eval() + torch.no_grad() 推理模式

### 4.2 特征工程 (bridge/strategies/ml/features.py)

职责：bars -> 归一化特征张量，训练推理共用。

配置：
- window: 特征窗口（默认20）
- indicators: 技术指标列表
- raw_features: 原始字段（close, volume等）
- derived: 衍生特征（return_1d, volatility_20等）
- normalize: zscore | minmax | none
- normalize_window: 归一化窗口

方法：
- build(bars): 推理用，取最近window根，返回 (1, window, n_features)
- build_batch(bars): 训练用，滑动窗口全序列，返回 (n_samples, window, n_features)
- n_features: 特征维度

特征列表：
- 原始: close/open/high/low/volume
- 衍生: return_1d, return_5d, volatility_20
- 指标: ma(N), rsi(N), macd, boll, kdj

归一化：
- zscore: (x - rolling_mean) / rolling_std
- minmax: (x - rolling_min) / (rolling_max - rolling_min)
- none: 模型内 BatchNorm

### 4.3 PyTorch 模型架构 (bridge/strategies/ml/models/)

实验性质，可替换。通过 registry 注册，上层不感知具体架构。

models/registry.py:
- register_model(name): 装饰器注册
- build_model(arch, params): 构建实例
- list_models(): 列出架构名

models/lstm.py - LSTMModel:
- 输入: (batch, seq_len, n_features)
- 结构: LSTM -> 最后时间步 -> Dropout -> Linear
- 参数: input_size, hidden_size(64), num_layers(2), dropout(0.1), output_size, task

models/transformer.py - TransformerModel:
- 结构: Linear投影 -> PositionalEncoding -> TransformerEncoder -> 池化 -> Linear
- 参数: input_size, d_model(64), nhead(4), num_layers(2), dim_feedforward(128)

models/mlp.py - MLPModel:
- 结构: Flatten -> Linear -> ReLU -> Dropout -> Linear
- 适用于特征少、不需要时序建模的场景
### 4.4 标签生成 (bridge/training/labels.py)

三种标签方式：
1. make_classification_labels(bars, horizon=5, threshold=0.02)
   - 未来horizon天涨跌幅分类: buy(2)/hold(1)/sell(0)
2. make_regression_labels(bars, horizon=5)
   - 未来horizon天收益率回归值
3. make_triple_barrier_labels(bars, horizon=10, up=0.05, down=0.05)
   - 三重屏障标注（参考 Lopez de Prado）

### 4.5 数据集 (bridge/training/dataset.py)

FinancialDataset(torch.utils.data.Dataset):
- 从 bars + FeatureBuilder + 标签函数 构建训练样本
- 支持多标的拼接
- 滑动窗口采样，X和y对齐截取

### 4.6 训练器 (bridge/training/trainer.py)

Trainer.train(config) 完整流程：
1. 拉取历史数据
2. 构建 FeatureBuilder + 生成标签 + 构建 Dataset
3. 时序分割训练/验证集（不随机打乱）
4. build_model(arch, params)
5. 训练循环: CrossEntropyLoss/MSELoss + Adam + ReduceLROnPlateau + 早停
6. ModelRegistry.save 保存最优模型

返回: model_id, metrics(train/val loss, accuracy), train_log

### 4.7 模型注册表 (bridge/training/model_registry.py)

模型持久化管理，工程重点。

目录结构:
- data/models/{model_id}/model.pt
- data/models/{model_id}/meta.json
- data/models/{model_id}/config.json
- data/models/index.json

meta.json: model_id, name, arch, created_at, metrics, n_samples, symbols, status
config.json: model_arch, model_params, feature_config, label_config, train_config

方法: save, load, list_models, get_meta, delete

### 4.8 训练管道 (bridge/training/pipeline.py)

TrainPipeline: 异步训练任务管理。
- start_train(config) -> task_id（非阻塞，后台线程）
- get_status(task_id) -> running/done/error + progress/result

### 4.9 HTTP 模型服务 (bridge/model_server.py)

Python 标准库 http.server，零依赖。
复用 registry.py 管理策略，ModelRegistry 管理模型。
POST /signal 复用现有信号计算逻辑。
POST /train 异步，立即返回 task_id。
端口: MODEL_SERVER_PORT 环境变量，默认 8765。

### 4.10 QMT 壳策略 (bridge/qmt_shell.py)

固定一份 .py，放 QMT 策略目录。
配置区: SERVER_URL, STRATEGY, PARAMS, SYMBOL, PERIOD, COUNT, ACCOUNT
执行: xtdata取K线 -> POST /signal -> xttrader下单
对规则/ML策略完全透明。

### 4.11 Builtin 模型 (bridge/training/builtin_models/)

用于测试的预置模型，无需训练即可使用：

1. dummy_model: 恒定输出 hold 信号
   - 测试系统流程，不依赖 torch
   - 实现: 简单 Python 类，模拟 nn.Module 接口

2. linear_model: 线性回归模型
   - 用少量数据快速训练，验证训练管道
   - 实现: torch.nn.Linear 单层

3. small_lstm: 小型 LSTM 预训练模型
   - 在示例数据上预训练，验证 ML 推理流程
   - 实现: LSTMModel(hidden_size=16, num_layers=1)

Builtin 模型通过 ModelRegistry 注册到 data/models/，
首次启动时自动检测并初始化（如果不存在则创建）。
## 五、可测试性设计

### 5.1 依赖注入

所有业务模块通过构造函数注入依赖，不直接 import 具体实现：

- Trainer 接收 data_fetcher（可 mock 数据源）
- ModelRegistry 接收 storage_dir（可指向临时目录）
- FeatureBuilder 接收 config（可构造任意配置）
- model_server 的 handler 接收 registry 和 model_registry（可注入 mock）

### 5.2 Mock 实现

- MockStrategy(StrategyBase): 固定返回指定 Signal，测试信号计算流程
- MockModelRegistry: 内存存储，不写文件，测试训练管道
- MockDataFetcher: 返回预置 K 线数据，不依赖 xtdata
- MockModel: 模拟 nn.Module.forward，不依赖 torch

### 5.3 测试数据

- tests/fixtures/bars_60.json: 60根日线K线（招商银行示例数据）
- tests/fixtures/bars_500.json: 500根日线（足够训练小模型）
- tests/fixtures/expected_signal_ma_cross.json: MA交叉策略预期信号

### 5.4 单元测试计划

tests/ 目录结构：

    tests/
    +-- conftest.py              # pytest fixtures（MockStrategy, 测试数据等）
    +-- strategies/
    |   +-- test_registry.py     # 策略注册/加载/删除
    |   +-- test_ma_cross.py     # MA交叉策略信号正确性
    |   +-- test_ml_base.py      # ML策略基类生命周期
    +-- training/
    |   +-- test_labels.py       # 标签生成正确性
    |   +-- test_dataset.py      # 数据集构建和对齐
    |   +-- test_trainer.py      # 训练循环（用MockModel）
    |   +-- test_model_registry.py  # 模型持久化CRUD
    |   +-- test_pipeline.py     # 异步训练管道
    +-- features/
    |   +-- test_features.py     # 特征构建+归一化
    +-- models/
    |   +-- test_lstm.py         # LSTM前向传播
    |   +-- test_transformer.py  # Transformer前向传播
    |   +-- test_mlp.py          # MLP前向传播
    +-- server/
    |   +-- test_model_server.py # HTTP端点（用mock registry）
    +-- test_builtin_models.py   # Builtin模型可用性

测试原则：
- 规则策略测试不依赖 torch
- ML模型测试标记 @pytest.mark.torch，缺失 torch 时 skip
- HTTP服务测试用 mock registry，不依赖真实数据源
- 每个模块的测试可独立运行

## 六、数据流

### 6.1 训练流程

用户UI配置 -> IPC -> POST /train -> TrainPipeline后台线程:
1. 拉取历史K线
2. FeatureBuilder.build_batch 构建特征
3. 标签生成
4. FinancialDataset -> 时序分割
5. build_model -> 训练循环
6. ModelRegistry.save
UI轮询 GET /train/<id>/status -> 显示结果

### 6.2 推理流程

QMT壳策略 handlebar:
1. xtdata获取K线 -> bars
2. POST /signal
3. model_server: 实例化策略 -> on_after_init(加载模型) -> 逐bar喂到最后一根 -> 返回Signal
4. QMT根据Signal下单

### 6.3 策略验证流程

UI点击验证 -> 选标的+周期 -> facade.quote.history获取K线 -> POST /signal -> 显示信号
## 七、文件结构

    bridge/
    +-- strategies/
    |   +-- base.py                 [已有] 不修改
    |   +-- indicators.py           [已有] 不修改
    |   +-- registry.py             [已有] 不修改
    |   +-- builtin/                [已有] 规则策略
    |   +-- ml/                     [新增] ML策略
    |   |   +-- base.py             MLStrategyBase
    |   |   +-- features.py         FeatureBuilder
    |   |   +-- builtin/            内置ML策略
    |   |   |   +-- lstm_trend.py   LSTM趋势预测
    |   |   +-- models/             PyTorch模型(实验)
    |   |       +-- registry.py     模型架构注册表
    |   |       +-- lstm.py
    |   |       +-- transformer.py
    |   |       +-- mlp.py
    +-- training/                   [新增] 训练管道
    |   +-- dataset.py
    |   +-- labels.py
    |   +-- trainer.py
    |   +-- pipeline.py
    |   +-- model_registry.py
    |   +-- builtin_models/         [新增] 测试用预置模型
    |       +-- dummy.py            恒定hold
    |       +-- linear.py           线性回归
    |       +-- small_lstm.py       小型预训练LSTM
    +-- model_server.py             [新增] HTTP服务
    +-- qmt_server.py               [已有] stdio RPC
    +-- qmt_shell.py                [新增] QMT壳策略
    +-- requirements.txt            [修改] 加 torch(可选)

    tests/                          [新增] 单元测试
    +-- conftest.py
    +-- strategies/
    +-- training/
    +-- features/
    +-- models/
    +-- server/
    +-- fixtures/

    data/
    +-- strategies/user/            [已有]
    +-- models/                     [新增] 模型存储

## 八、UI 增量计划

### 8.1 模型管理页（新增）
- 模型列表: 名称/架构/训练日期/指标/状态/操作
- 模型详情: 训练配置/特征配置/训练曲线
- 删除模型

### 8.2 训练页（新增）
- 数据选择: 标的搜索+周期+数据量
- 架构选择: LSTM/Transformer/MLP下拉
- 超参数表单: hidden_size/num_layers/lr/epochs/batch_size
- 特征配置: 勾选指标+窗口+归一化
- 标签配置: 分类/回归+horizon+threshold
- 训练进度: 进度条+日志+loss曲线

### 8.3 策略管理增强
- ML策略创建: 类型下拉出现ML策略
- 模型选择: model_id下拉选择已训练模型
- 策略验证: 验证按钮->选标的->显示信号

## 九、实施阶段

### Phase 1: 模型架构 + 特征工程
1. strategies/ml/models/registry.py
2. strategies/ml/models/lstm.py + transformer.py + mlp.py
3. strategies/ml/features.py
4. 验证: 构建模型+前向传播+特征构建

### Phase 2: 训练管道 + Builtin模型
1. training/labels.py + dataset.py
2. training/trainer.py + model_registry.py + pipeline.py
3. training/builtin_models/ (dummy/linear/small_lstm)
4. 验证: builtin模型可加载可推理

### Phase 3: ML策略 + HTTP服务 + QMT壳
1. strategies/ml/base.py + builtin/lstm_trend.py
2. model_server.py + qmt_shell.py
3. 验证: HTTP /signal端到端

### Phase 4: 单元测试
1. tests/conftest.py + fixtures
2. 各模块单元测试
3. 验证: pytest全通过

### Phase 5: 主进程集成
1. main.js spawn model_server.py
2. preload.js + ipc/index.js
3. 侧边栏状态指示

### Phase 6: UI
1. 模型管理页 + 训练页
2. 策略验证增强
3. 端到端验证

## 十、依赖变更

    # bridge/requirements.txt
    xtquant>=1.0
    + torch>=2.0  # 可选，仅ML策略需要
    + numpy>=1.20
    + pytest>=7.0  # 测试

PyTorch 为可选依赖。缺失时规则策略正常工作，ML策略报明确错误。

## 十一、关键设计决策

1. ML策略仍为StrategyBase子类？
   - 复用注册/管理/调用流程，零侵入
   - HTTP /signal不区分规则/ML
   - QMT壳策略无需修改

2. PyTorch为可选依赖？
   - 系统核心是模型管理，不是深度学习
   - 规则策略是主力，ML是实验
   - 降低安装门槛

3. Builtin模型的意义？
   - 无需训练即可测试完整流程
   - dummy不依赖torch，验证基础流程
   - linear/small_lstm验证ML流程

4. 特征配置保存在模型config中？
   - 训练推理一致性保障
   - 加载模型自动恢复FeatureBuilder
   - 避免特征偏移

5. 用http.server而非Flask？
   - 零依赖，标准库
   - 本地服务，低并发

## 十二、审核修订记录（第1轮）

根据架构师审核意见修订如下：

### 修订1: ModelRegistry 增加 LRU 缓存

load(model_id) 增加内存缓存，避免每次 /signal 都从磁盘加载模型。

    class ModelRegistry:
        _cache = {}  # model_id -> (model, config)
        _cache_order = []  # LRU 顺序
        _cache_max = 10  # 最多缓存10个模型
        _lock = threading.Lock()  # 线程安全

        @classmethod
        def load(cls, model_id):
            with cls._lock:
                if model_id in cls._cache:
                    cls._cache_order.remove(model_id)
                    cls._cache_order.append(model_id)
                    return cls._cache[model_id]
                # 从磁盘加载...
                cls._cache[model_id] = (model, config)
                cls._cache_order.append(model_id)
                if len(cls._cache_order) > cls._cache_max:
                    old = cls._cache_order.pop(0)
                    del cls._cache[old]
                return (model, config)

### 修订2: DataFetcher 接口

定义数据获取接口，解耠训练与数据源：

    class DataFetcher:
        def fetch_bars(self, symbol, period, count) -> list:
            获取历史K线，返回统一 bar dict 格式

    class HttpDataFetcher(DataFetcher):
        通过 HTTP 调 qmt_server 的 quote.history 获取数据
        def fetch_bars(self, symbol, period, count):
            resp = requests.get(f{QMT_URL}/quote/history, ...)
            return resp.json().get(bars, [])

    class MockDataFetcher(DataFetcher):
        测试用，返回预置数据

Trainer 接收 data_fetcher 参数（依赖注入）。

### 修订3: 线程安全

ModelRegistry 所有写操作(save/delete/index更新)加 threading.Lock。
已在修订1中体现。

### 修订4: HTTP 错误处理

统一错误响应格式：

    # 400 Bad Request
    {error: 参数错误描述, code: BAD_REQUEST}
    # 404 Not Found
    {error: 资源不存在, code: NOT_FOUND}
    # 500 Internal Error
    {error: 服务器错误描述, code: INTERNAL}

model_server.py 的所有 handler 统一使用此格式。

### 修订5: ModelWrapper 接口

定义统一模型接口，解耠 torch 依赖：

    class ModelWrapper:
        模型推理接口，不绑定具体框架
        def forward(self, tensor) -> tensor: ...
        def eval(self): ...

    class TorchModelWrapper(ModelWrapper):
        包装 torch.nn.Module
        def forward(self, tensor):
            with torch.no_grad():
                return self._model(tensor)

    class DummyModelWrapper(ModelWrapper):
        纯Python实现，不依赖torch
        def forward(self, tensor):
            return tensor  # 原样返回

ModelRegistry.load 返回 ModelWrapper，不是原始 torch 模型。
MLStrategyBase.on_after_init 使用 ModelWrapper，不直接接触 torch。

### 修订6: 集成测试

测试计划增加：
    tests/
    +-- test_integration.py  # 端到端: 创建策略 -> POST /signal -> 验证
    +-- test_http_errors.py  # HTTP错误处理

### 修订7: 模型版本关联

meta.json 增加 parent_model_id 字段（可选）：
- 新训练的模型可指定父模型
- UI 展示模型谱系（暂不实现复杂版本管理）
