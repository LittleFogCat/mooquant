# 模型服务 API 文档

> 模型服务（`bridge/model_server.py`）是 mooquant 的核心 HTTP 服务，负责模型管理、训练和信号计算。
> 上层消费者（QMT 壳策略 / 本地策略执行器）通过 HTTP 接口与模型服务交互，模型管理与上层完全解耦。

## 基础信息

| 项 | 值 |
|---|---|
| 基地址 | `http://127.0.0.1:8765`（可通过 `MODEL_SERVER_PORT` 环境变量或 `config/default.json` 的 `modelServer.port` 配置） |
| 内容类型 | `application/json; charset=utf-8` |
| CORS | 允许所有来源（`Access-Control-Allow-Origin: *`） |
| 依赖 | Python stdlib（`http.server`），零外部依赖；PyTorch 为可选依赖 |

## 统一响应格式

### 成功

各端点返回对应的 JSON 数据，HTTP 状态码 200。

### 错误

```json
{
  "error": "错误描述",
  "code": "ERROR_CODE"
}
```

| HTTP 状态码 | code | 说明 |
|---|---|---|
| 400 | `BAD_REQUEST` | 请求参数缺失或无效 |
| 404 | `NOT_FOUND` | 资源不存在 |
| 500 | `INTERNAL` | 服务器内部错误 |

---

## 端点一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/strategies` | 列出所有已注册策略 |
| GET | `/strategy/{name}` | 获取策略元数据 |
| POST | `/strategy` | 添加自定义策略 |
| DELETE | `/strategy/{name}` | 删除策略 |
| GET | `/models` | 列出所有模型 |
| GET | `/models/{id}` | 获取模型详情 |
| PUT | `/models/{id}` | 更新模型元数据 |
| DELETE | `/models/{id}` | 删除模型 |
| GET | `/models/active` | 获取当前激活的模型 |
| POST | `/models/{id}/activate` | 激活模型 |
| POST | `/signal` | 计算策略信号 |
| POST | `/train` | 启动异步训练 |
| GET | `/train/{task_id}/status` | 查询训练状态 |

---

## 健康检查

### `GET /health`

检查模型服务运行状态。

**响应**

```json
{
  "status": "ok",
  "strategies": 5,
  "models": 3
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | 固定 `"ok"` |
| `strategies` | int | 已注册策略数量 |
| `models` | int | 已保存模型数量 |

---

## 策略管理

### `GET /strategies`

列出所有已注册策略（含规则策略和 ML 策略）。

**响应**

```json
[
  {
    "name": "ma_cross",
    "display_name": "双均线",
    "description": "短期均线上穿长期均线买入",
    "version": "1.0",
    "trigger_mode": "bar",
    "params_schema": [
      { "key": "fast", "type": "int", "default": 5, "min": 1, "max": 60, "label": "快线周期" }
    ]
  },
  {
    "name": "lstm_trend",
    "display_name": "LSTM趋势预测",
    "description": "基于LSTM模型预测涨跌概率",
    "version": "1.0",
    "trigger_mode": "bar",
    "is_ml": true,
    "model_arch": "lstm",
    "params_schema": [
      { "key": "model_id", "type": "string", "label": "模型ID", "description": "在模型管理中训练并获取模型ID" }
    ]
  }
]
```

### `GET /strategy/{name}`

获取单个策略的元数据。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | string | 策略类型名（如 `ma_cross`） |

**响应**：同上单个策略对象。

**错误**：404 `NOT_FOUND` - 策略不存在。

### `POST /strategy`

添加用户自定义策略（写源码到 `data/strategies/user/` 并即时注册）。

**请求体**

```json
{
  "name": "my_strategy",
  "code": "from strategies.base import StrategyBase, Signal\n..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 策略类型名（英文标识符） |
| `code` | string | 是 | Python 策略代码（须含 StrategyBase 子类） |

**响应**

```json
{
  "strategy": { "name": "my_strategy", "display_name": "...", ... }
}
```

### `DELETE /strategy/{name}`

删除用户自定义策略（仅限 `data/strategies/user/` 目录下的）。

**响应**：`{ "ok": true }`

---

## 模型管理

### `GET /models`

列出所有已保存的模型。

**响应**

```json
[
  {
    "model_id": "m_1784739514060_b09c81",
    "name": "LSTM趋势模型",
    "arch": "lstm",
    "created_at": "2026-07-23T12:00:00",
    "metrics": { "accuracy": 0.65, "loss": 0.123 },
    "n_samples": 500,
    "symbols": ["sh600519"],
    "status": "active",
    "parent_model_id": null
  }
]
```

### `GET /models/{id}`

获取模型详情（元数据 + 训练配置）。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `id` | string | 模型 ID（如 `m_1784739514060_b09c81`） |

**响应**

```json
{
  "meta": {
    "model_id": "m_xxx",
    "name": "LSTM趋势模型",
    "arch": "lstm",
    "created_at": "2026-07-23T12:00:00",
    "metrics": { ... },
    "status": "active"
  },
  "config": {
    "model_arch": "lstm",
    "model_params": { "input_size": 6, "hidden_size": 64, "num_layers": 2 },
    "feature_config": { ... },
    "data": { "symbols": ["sh600519"], "period": "1d" },
    "label_type": "classification"
  }
}
```

**错误**：404 `NOT_FOUND` - 模型不存在。

### `PUT /models/{id}`

更新模型元数据（部分更新）。

**请求体**（只需传要更新的字段）

```json
{
  "name": "新名称",
  "status": "archived"
}
```

| 可更新字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 模型名称 |
| `status` | string | 模型状态（`active` / `archived`） |

**响应**：更新后的完整元数据。

### `DELETE /models/{id}`

删除模型（删除模型目录及缓存）。

**响应**：`{ "ok": true }`

### `GET /models/active`

获取当前激活的模型。

**响应**（有激活模型）

```json
{
  "model_id": "m_1784739514060_b09c81",
  "strategy": "lstm_trend",
  "meta": {
    "model_id": "m_xxx",
    "name": "LSTM趋势模型",
    "arch": "lstm",
    ...
  }
}
```

**响应**（无激活模型）

```json
{
  "model_id": "",
  "strategy": "",
  "meta": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `model_id` | string | 激活的模型 ID，空字符串表示未激活 |
| `strategy` | string | 激活时自动关联的策略名（根据模型 arch 映射：lstm -> lstm_trend） |
| `meta` | object \| null | 模型元数据 |

### `POST /models/{id}/activate`

激活模型。激活后，`/signal` 端点在不传 `strategy` 参数时会自动使用此模型计算信号。

激活时根据模型 `arch` 自动选择关联策略：

| 模型架构 | 关联策略 |
|---|---|
| `lstm` | `lstm_trend` |
| 其他 | `lstm_trend`（默认） |

**响应**

```json
{
  "ok": true,
  "model_id": "m_1784739514060_b09c81"
}
```

---

## 信号计算

### `POST /signal`

计算策略信号。这是模型服务的核心端点，上层消费者（QMT 壳策略 / 本地策略执行器）通过此端点获取交易信号。

**请求体**

```json
{
  "strategy": "lstm_trend",
  "bars": [
    { "date": "2026-07-01", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.3, "volume": 1000000 }
  ],
  "params": { "buy_threshold": 0.6 },
  "symbol": "sh600519"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `bars` | array | 是 | K 线数据序列，每根 bar 含 `date/open/high/low/close/volume` |
| `strategy` | string | 否 | 策略类型名。**不传时自动使用激活模型的关联策略**（壳策略模式） |
| `params` | object | 否 | 策略参数（覆盖默认值）。ML 策略不传 `model_id` 时自动用激活模型 |
| `symbol` | string | 否 | 标的代码 |

**两种调用模式**

1. **壳策略模式**（不传 `strategy`）：
   - 服务读取 `active.json`，使用激活模型 + 关联策略计算
   - 适用于 QMT 壳策略和本地壳策略实例

2. **显式策略模式**（传 `strategy`）：
   - 服务用指定策略名实例化策略，计算信号
   - 适用于手写策略、规则策略

**响应**

```json
{
  "action": "buy",
  "reason": "LSTM预测上涨概率0.85",
  "strength": 0.85,
  "targetPosition": null,
  "indicators": {
    "prob_up": 0.85,
    "prob_down": 0.10
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `action` | string | 交易动作：`buy` / `sell` / `hold` |
| `reason` | string | 信号原因（用于日志/展示） |
| `strength` | float | 信号强度 [0, 1] |
| `targetPosition` | float \| null | 目标仓位比例 [0, 1]，null 表示不调仓 |
| `indicators` | object | 当前指标快照 |

**错误**

| 状态码 | 说明 |
|---|---|
| 400 | 缺少 `bars`；且无 `strategy` 且无激活模型 |
| 404 | 指定的 `strategy` 不存在 |
| 500 | 策略计算过程中出错 |

---

## 模型训练

### `POST /train`

启动异步训练任务。立即返回 `task_id`，训练在后台线程执行。

**请求体**

```json
{
  "symbol": "sh600519",
  "period": "1d",
  "bar_count": 500,
  "architecture": "lstm",
  "epochs": 50,
  "learning_rate": 0.001,
  "hidden_size": 64,
  "batch_size": 32,
  "label_type": "classification"
}
```

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `symbol` | string | 是 | - | 标的代码（如 `sh600519`） |
| `period` | string | 否 | `"1d"` | K 线周期 |
| `bar_count` | int | 否 | `500` | 训练数据量（K 线根数） |
| `architecture` | string | 否 | `"lstm"` | 模型架构：`lstm` / `transformer` / `mlp` |
| `epochs` | int | 否 | `50` | 训练轮数 |
| `learning_rate` | float | 否 | `0.001` | 学习率 |
| `hidden_size` | int | 否 | `64` | 隐藏层大小 |
| `batch_size` | int | 否 | `32` | 批次大小 |
| `label_type` | string | 否 | `"classification"` | 标签类型：`classification` / `regression` / `triple_barrier` |

**响应**

```json
{
  "task_id": "train_1784739514060"
}
```

### `GET /train/{task_id}/status`

查询训练任务状态。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 训练任务 ID |

**响应**（训练中）

```json
{
  "status": "running",
  "progress": 0
}
```

**响应**（训练完成）

```json
{
  "status": "done",
  "progress": 100,
  "result": { "model_id": "m_xxx", "metrics": { ... } }
}
```

**响应**（训练失败）

```json
{
  "status": "error",
  "progress": 0,
  "error": "错误信息",
  "traceback": "..."
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | `running` / `done` / `error` |
| `progress` | int | 进度 0-100 |
| `result` | object | 训练结果（仅 `done` 时有） |
| `error` | string | 错误信息（仅 `error` 时有） |
| `traceback` | string | 错误堆栈（仅 `error` 时有） |

---

## 数据模型

### Signal

```json
{
  "action": "buy",
  "reason": "买入原因",
  "strength": 0.85,
  "targetPosition": 0.3,
  "indicators": { "ma5": 10.5, "ma20": 10.2 }
}
```

### Model Meta

```json
{
  "model_id": "m_1784739514060_b09c81",
  "name": "LSTM趋势模型",
  "arch": "lstm",
  "created_at": "2026-07-23T12:00:00",
  "metrics": { "accuracy": 0.65, "loss": 0.123 },
  "n_samples": 500,
  "symbols": ["sh600519"],
  "status": "active",
  "parent_model_id": null
}
```

### Strategy Meta

```json
{
  "name": "ma_cross",
  "display_name": "双均线",
  "description": "短期均线上穿长期均线买入",
  "version": "1.0",
  "trigger_mode": "bar",
  "is_ml": false,
  "params_schema": [
    {
      "key": "fast",
      "type": "int",
      "default": 5,
      "min": 1,
      "max": 60,
      "label": "快线周期",
      "description": "短期均线周期"
    }
  ]
}
```

### params_schema 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 参数键名 |
| `type` | string | 参数类型：`int` / `float` / `string` / `bool` |
| `default` | any | 默认值 |
| `min` | number | 最小值（int/float 类型） |
| `max` | number | 最大值（int/float 类型） |
| `label` | string | UI 显示名称 |
| `description` | string | 参数说明 |

---

## QMT 壳策略集成

QMT 客户端中运行 `bridge/qmt_shell.py` 壳策略，通过 HTTP 调用 `/signal` 端点：

```
QMT handlebar -> 获取K线 -> POST /signal { bars, symbol } -> 返回信号 -> 下单
```

壳策略配置区：

```python
SERVER_URL = 'http://127.0.0.1:8765'
STRATEGY = 'ma_cross'       # 或不传，使用激活模型（壳策略模式）
PARAMS = {}                   # ML 策略的 model_id 自动从激活模型填充
SYMBOL = '600036.SH'
```

> **壳策略模式**：`STRATEGY` 留空或 `/signal` 不传 `strategy` 参数时，服务自动使用激活模型计算。需先在模型管理页激活一个模型。