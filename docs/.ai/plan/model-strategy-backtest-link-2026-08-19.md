# 模型·策略·回测链路打通 & 会话内修复记录

**日期**：2026-08-19
**背景**：用户反馈「模型、策略、回测很难用」。调研确认核心原因是三者链路断裂：回测完全忽略模型、模型绑定靠全局隐式 `active.json`、数据降级静默无提示。本次执行其中两项最高优先级修复（回测支持模型 + 策略显式绑定模型），外加会话内两个独立小修复。

---

## 一、需求清单

### 主任务：链路打通（用户批准执行）

| # | 需求 | 动机 |
|---|------|------|
| R1 | 回测页支持选择模型，`model_id` 一路传到回测引擎 | 原回测实例化策略时不传 `model_id`，ML 策略在回测里是空架子，训练成果无法验证 |
| R2 | 策略实例显式绑定模型（`modelId` 字段） | 原靠全局 `data/models/active.json` 隐式串联，激活 A 模型所有 ML 策略同时变，无法「甲策略用模型1、乙策略用模型2」，且无任何页面解释该隐式关系 |

### 会话内附带修复

| # | 需求 | 动机 |
|---|------|------|
| R3 | 收缩侧边栏图标悬浮文字提示失效 | `.nav-list` 的 `overflow-y:auto` 使 `overflow-x` 被强制算为 `auto`，横向伸出的 tooltip 被裁剪 |
| R4 | 模型服务提示「未启动」 | 端口 8765 被另一项目（classifier 的 uvicorn 服务）占用，`model_server.py` 绑定失败（WinError 10013） |

---

## 二、执行逻辑

### R1 + R2 总体设计

模型解析优先级（全链路统一）：**回测页显式选择 > 策略实例绑定 `modelId` > ML 策略内部回退激活模型（`active.json`）**。

```
策略实例 (st_*.json, 新增 modelId 字段)
   │
   ├─ 回测：backtest-view 下拉选择 ──> backtest-service.run({modelId})
   │         注入 params.model_id ──> backtest_engine.py
   │           ├─ type=shell：按 model_id 的 arch 解析为具体 ML 策略
   │           └─ type=ML：params.model_id 直接生效（MLStrategyBase 消费）
   │
   └─ 实盘：executor-service.tick() 注入 params.model_id
             ──> model_server.py /signal
                   ├─ 壳策略带 model_id：按该模型 arch 解析策略（不再固定用 active.json）
                   └─ 壳策略不带：回退激活模型
```

### 关键发现（执行中确认的根因）

1. **`registry.load_all()` 不加载 `strategies/ml/builtin/`**（只扫 `strategies/builtin/` 和 `data/strategies/user/`）。因此回测引擎和 qmt 桥里 `lstm_trend` 根本未注册，回测 ML 策略时静默回落 `ma_cross`——比"忽略模型"更严重，结果是彻头彻尾的错误策略。`model_server.py` 靠 `_ensure_loaded` 手工 `import strategies.ml.builtin.lstm_trend` 才正常。
2. `MLStrategyBase.on_after_init` 本就支持 `params.model_id` 并回退激活模型，所以打通链路只需把 `model_id` 注入 params，无需改策略框架。
3. 壳策略（`shell`）在 Python 侧没有真实策略类，只是 UI 伪类型（`strategy-viewmodel.js` 手工 unshift），必须在引擎/服务端解析。

### 逐文件改动

**R2：策略显式绑定模型**

| 文件 | 改动 |
|------|------|
| `main/services/strategy-service.js` | `create`/`update` 持久化 `modelId` 字段 |
| `renderer/js/views/strategy-view.js` | ML/壳策略编辑弹窗新增「绑定模型」下拉（空值=跟随激活模型），替代原"去模型页复制 ID"的裸文本框（隐藏 lstm_trend schema 中的 `model_id` 文本框避免双入口）；列表类型列下方显示实际使用模型 |
| `renderer/js/viewmodels/strategy-viewmodel.js` | 加载模型列表（`model:models`）；编辑/复制/保存携带 `modelId` |
| `main/services/executor-service.js` | 信号计算注入 `params.model_id`（HTTP `/signal` 与 stdio RPC 两条路径） |
| `bridge/model_server.py` | `/signal` 壳策略分支：带 `params.model_id` 时按该模型 `arch` 解析策略，模型不存在返回 404；不带时回退激活模型 |
| `bridge/strategies/ml/base.py` | 新增共享常量 `ARCH_STRATEGY_MAP`（arch -> 策略类型映射） |
| `bridge/qmt_server.py` | 预加载补充导入 `strategies.ml.models` 和 `strategies.ml.builtin.lstm_trend`（策略类型下拉可见 ML 策略且带 `is_ml` 标志） |

**R1：回测支持模型**

| 文件 | 改动 |
|------|------|
| `renderer/js/views/backtest-view.js` | 配置区新增「回测模型」下拉（标注"ML/壳策略生效"）；结果页 `dataSource === "mock"` 时显示红色警示条（消除静默降级） |
| `renderer/js/viewmodels/backtest-viewmodel.js` | `modelId` 状态 + localStorage 持久化 + 模型列表加载，`run()` 传递 |
| `main/services/backtest-service.js` | 按优先级解析 boundModel 并注入 `params.model_id` |
| `bridge/backtest_engine.py` | ① 导入 ML 策略（修复静默回落 ma_cross 的 bug）；② `type=shell` 时按绑定/激活模型解析为具体 ML 策略，无模型时报「壳策略需要绑定模型或激活模型才能回测」；③ 未知策略类型由静默回落 ma_cross 改为报错 `未知策略类型: xxx` |

**R3：侧边栏 tooltip**

| 文件 | 改动 |
|------|------|
| `renderer/css/app.css` | `.nav-list` 的 `overflow-y:auto` 改为 `overflow:visible`（导航仅 6 个固定项，无滚动需求；CSS 规范下一个轴设为非 visible 时另一轴强制 auto，导致 tooltip 被裁剪） |

**R4：模型服务端口**

| 文件 | 改动 |
|------|------|
| `config/default.json` | `modelServer.port` 8765 -> 8766（8765 被本机 classifier 项目的 uvicorn 占用，用户选择改端口共存） |
| `bridge/qmt_shell.py` | 硬编码 `SERVER_URL` 改为读 `MODEL_SERVER_PORT` 环境变量，默认 8766 |

---

## 三、执行结果

### 验证记录（全部通过）

| 验证项 | 方法 | 结果 |
|--------|------|------|
| Python 语法 | `py_compile` 4 个改动文件 | 通过 |
| 主进程语法 | `node -e require(...)` 3 个 service | 通过 |
| 渲染层构建 | `npx vite build` | 通过 |
| 回测·壳策略+绑定模型 | stdio 直调引擎：`shell + model_id=builtin_lstm` | 正确解析为 lstm_trend，真实数据 57 根生成 36 个信号 |
| 回测·普通策略 | `ma_cross` | 正常 |
| 回测·未知类型 | `no_such_type` | 返回明确错误（不再静默回落 ma_cross） |
| /signal·壳+绑定模型 | HTTP POST（隔离端口 8799） | 200，按绑定模型 arch 解析策略 |
| /signal·壳+无绑定 | HTTP POST | 200，回退激活模型 |
| /signal·壳+模型不存在 | HTTP POST | 404 `Bound model not found` |
| 模型服务·新端口 | `MODEL_SERVER_PORT=8766` 启动 + `/health` | `{"status":"ok","strategies":4,"models":5}` |

### 用户视角变化（修复前 -> 修复后）

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 回测 ML 策略 | 静默用 ma_cross 跑（结果完全无意义） | 用选定/绑定/激活的模型跑，模型缺失时报清晰错误 |
| 回测降级 mock 数据 | 无任何提示 | 结果页红色警示条 |
| 策略用哪个模型 | 隐式全局激活模型，无从查看 | 策略列表直接显示；可按策略独立绑定 |
| ML 策略指定模型 | 手工去模型页复制 model_id 粘贴文本框 | 下拉选择（显示模型名） |
| 模型服务 | 端口冲突启动失败 | 8766 独立端口，与 classifier 项目共存 |

### 遗留事项（本次未做，属原分析第 3/4 条）

- 训练/回测/实盘三套数据获取入口尚未统一（训练按根数、回测按区间走 xtdata/db 缓存、实盘走 quoteService）
- 策略双存储（JS 实例 JSON + Python 用户策略 py 文件）与改源码需重启桥的问题未处理
- `ARCH_STRATEGY_MAP` 中 `mlp -> mlp_classifier`、`transformer -> transformer_trend` 尚无对应策略类实现（绑定 mlp 模型到壳策略会报 Unknown strategy，属既有缺口）
- 文档 `docs/api/model-server-api.md` 等仍写默认 8765
