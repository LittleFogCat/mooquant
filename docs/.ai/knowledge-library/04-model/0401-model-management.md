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
| POST | /signal | 计算策略信号 |

### 配置

- 端口：默认 8765，通过 `MODEL_SERVER_PORT` 环境变量或 `config/default.json` 的 `modelServer.port` 配置
- 依赖：Python stdlib（http.server），零外部依赖（torch 为可选依赖）

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
- 归一化：min-max 或 z-score

## 训练管道

### 流程

1. `TrainPipeline.start_train(config)` 返回 `task_id`
2. 后台线程执行 `Trainer.train(config)`
3. `TrainPipeline.get_status(task_id)` 查询进度

### Trainer

- 数据获取：`DataFetcher` 接口（`XtdataDataFetcher` / `MockDataFetcher`）
- 数据集：`FinancialDataset`（多标的滑动窗口）
- 标签：classification / regression / triple_barrier
- 训练循环：前向传播 → 损失 → 反向传播 → 优化器步骤
- 早停：验证损失不再下降时停止
- 学习率调度：`ReduceLROnPlateau`

### 标签生成

| 类型 | 说明 |
|------|------|
| classification | 未来 N 根 bar 收益率 > 阈值 → 买入；< -阈值 → 卖出 |
| regression | 未来 N 根 bar 收益率（连续值） |
| triple_barrier | 三重障碍法（止盈/止损/时间止损） |

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

- 激活的 model_id 持久化到 `data/models/active.json`
- `MLStrategyBase.on_after_init` 无显式 model_id 时自动读取激活模型
- `/signal` 端点和 stdio RPC `strategy.signal` 均通过 MLStrategyBase 统一处理
- 上层（QMT 壳 / 本地策略执行器）无需关心 model_id，激活即可用

## 策略执行器集成

本地策略执行器（`executor-service.js`）的信号计算路径：

```
executor tick -> 获取K线 -> modelService.computeSignal() -> HTTP /signal -> 信号
                                          ↳ 回退 strategyBridge.strategySignal() -> stdio RPC
```

- 优先通过 HTTP 调用模型服务（与 QMT 壳策略走同一条路）
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
- 训练状态（进度条 + 状态文本）
- 策略列表

### 路由

- 页面标识：`models`
- 进入时加载模型和策略列表
- 离开时停止训练状态轮询

## 单元测试

| 测试文件 | 测试内容 |
|----------|----------|
| tests/features/test_features.py | 特征工程（4 个测试） |
| tests/models/test_models.py | 模型前向传播（3 个测试） |
| tests/strategies/test_registry.py | 策略注册（3 个测试） |
| tests/strategies/test_ma_cross.py | MA 交叉策略（1 个测试） |
| tests/training/test_labels.py | 标签生成（3 个测试） |
| tests/training/test_model_registry.py | 模型注册表（3 个测试） |
| tests/training/test_trainer.py | 训练器（1 个测试） |
| tests/test_integration.py | 端到端集成（2 个测试） |

共 20 个单元测试，全部通过。