# 策略模型审查评估 & 修复计划

**评估日期**：2026-08-09
**评估对象**：`docs/.ai/review/strategy-model-review-2026-07-24.md`
**评估方法**：逐项对照当前代码实际状态，确认是否已修复

---

## 一、逐项评估

### 严重问题（S1-S6）

| 编号 | 标题 | 状态 | 采纳 | 理由 |
|------|------|------|------|------|
| S1 | ML 训练数据 look-ahead bias | 已修复 | 不采纳 | `dataset.py` 已实现正确的对齐逻辑：`offset = feature_builder.window - 1`，`X[i]` 配 `labels[i+window-1]`，训练与推理对齐一致。代码中有详细注释说明对齐约定。 |
| S2 | Trainer shuffle=True | 已修复 | 不采纳 | `trainer.py` 中 `train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=False)`，已改为顺序遍历。 |
| S3 | squeeze(-1) 对 output_size>1 不安全 | 部分修复 | **采纳** | `lstm.py` 和 `transformer.py` 已修复（检查 `logits.shape[-1] == 1`），但 **`mlp.py` 仍直接 `return logits.squeeze(-1)`**，多输出回归场景存在同样的静默 bug。 |
| S4 | QMT 导出器实盘用 K 线 close 价格 | 已修复 | 不采纳 | `qmt_exporter.py` 已改用 `xtdata.get_full_tick([symbol])` 获取 tick 最新价，tick 不可用时回退到 K 线收盘价。 |
| S5 | passorder magic number 硬编码 | 已修复 | 不采纳 | 顶部已定义常量 `_OP_BUY`/`_OP_SELL`/`_ORDER_TYPE`/`_PRICE_LATEST`，passorder 调用引用常量而非 magic number。 |
| S6 | model_server.py 缺少访问控制 | 部分修复 | **部分采纳** | CORS 通配符已移除（`_send_json` 中不再发送 `Access-Control-Allow-Origin`）。但 `/strategy` 端点 `exec_module` 仍无 AST 静态检查。**采纳 AST 检查**；不采纳 token 鉴权 / named pipe（本地服务场景过度设计）；不采纳 DELETE 两步确认（本地工具误操作风险低）。 |

### 中等问题（M1-M8）

| 编号 | 标题 | 状态 | 采纳 | 理由 |
|------|------|------|------|------|
| M1 | 回测引擎仅支持单标的 | 已修复 | 不采纳 | 已添加 `if len(symbols) > 1: raise ValueError(...)`，明确报错而非静默丢弃。 |
| M2 | 回测忽略印花税与过户费 | 已修复 | 不采纳 | 已添加 `stamp_tax` 和 `transfer_fee` 参数，`buy_cost_rate`/`sell_cost_rate` 分别计入。 |
| M3 | max_dd 硬限 -100% 隐藏 bug | 已修复 | 不采纳 | 改为 `log("warning: max_dd ...")` 记录警告，不再硬限。 |
| M4 | ModelRegistry LRU 并发不安全 | 已修复 | 不采纳 | 已用 `OrderedDict`（O(1) move_to_end），`delete` 用 `pop(key, None)` 避免 ValueError，慢路径磁盘加载在锁外执行 + 双重检查写回。 |
| M5 | TrainPipeline daemon + 无清理 | 部分修复 | 不采纳 | 已添加 `_MAX_TASKS=50` + `_cleanup_tasks()`。`daemon=True` 保留：主进程退出时强杀训练任务是合理设计（避免半写入模型文件），配合训练结果落盘可接受。 |
| M6 | train_loss 命名误导 | 已修复 | 不采纳 | 已改为记录 `best_train_loss`（最优 epoch 的 train_loss），与 `best_val_loss` 对应。 |
| M7 | qmt_shell.py 限价单价格传 0 | 未修复 | **采纳** | `qmt_shell.py` 仍用 `xtconstant.FIX_PRICE, 0`，限价 0 不会被 QMT 接受。应改用 `xtconstant.LATEST_PRICE`。 |
| M8 | _compute_signal 每次重新加载模型 | 未修复 | **采纳** | 每次 HTTP `/signal` 请求都重新实例化策略 + `on_after_init`。虽然 `ModelRegistry` 有 LRU 缓存避免重复磁盘 IO，但仍需实例化策略对象、FeatureBuilder 等。维护策略实例池可减少开销。 |

### 轻度问题（L1-L10）

| 编号 | 标题 | 状态 | 采纳 | 理由 |
|------|------|------|------|------|
| L1 | macd 表达式可读性 | 已修复 | 不采纳 | 已加括号：`dea = ([None] * pad + dea_valid) if pad >= 0 else dea_valid[-len(dif):]`。 |
| L2 | rsi Wilder 平滑注释 | 已修复 | 不采纳 | docstring 已说明"首值用前 period 内简单平均初始化（SMA），后续用 Wilder 递推平滑"。 |
| L3 | Context 字段类型为 Any | 已修复 | 不采纳 | 已改为 `Optional[object]` / `Optional[ModuleType]`，不再用 `Any`。Protocol 接口属于过度设计，当前阶段 `Optional[object]` 足够。 |
| L4 | qmt_server.py 文档字符串乱码 | 基本修复 | **采纳** | 绝大多数中文已正常显示，仅剩 1 行 escape 序列 `"code \u4e0d\u80fd\u4e3a\u7a7a"`（"code 不能为空"）。 |
| L5 | regenerate/generate_mock_bars 重复 | 已修复 | 不采纳 | 已抽取到 `bridge/_shared.py`，backtest_engine.py 和 qmt_server.py 均从 `_shared` 导入。 |
| L6 | _ARCH_STRATEGY_MAP 不完整 | 已修复 | 不采纳 | 已补全为 `{'lstm': 'lstm_trend', 'mlp': 'mlp_classifier', 'transformer': 'transformer_trend'}`。 |
| L7 | _to_xtcode 重复实现 | 部分修复 | **采纳** | `backtest_engine.py` 已从 `_shared` 导入，但 **`qmt_server.py` 仍保留自己的 `to_xtcode` 定义**（约 30 行），且边界处理与 `_shared` 版本略有差异。应统一使用 `_shared.to_xtcode`。 |
| L8 | FeatureBuilder.n_features 计算错误 | 已修复 | 不采纳 | 已改为 `return len(self._column_order())`，`_column_order()` 正确展开 macd/boll/kdj 的多列。 |
| L9 | ma_cross.js deprecated 文件 | 部分修复 | 不采纳 | 文件头已添加 `@deprecated` 标注。保留文件作为历史参考是合理的，无需删除。lint 规则属于额外工程，当前项目无 lint 配置，投入产出比低。 |
| L10 | Trainer._evaluate device 硬编码 | 已修复 | 不采纳 | 已改为 `device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')`。 |

---

## 二、修复计划

### 需修复项目总览

| # | 来源 | 文件 | 修复内容 | 估算 |
|---|------|------|----------|------|
| 1 | S3 | `bridge/strategies/ml/models/mlp.py` | squeeze(-1) 增加 output_size 检查 | 5 min |
| 2 | S6 | `bridge/model_server.py` | `/strategy` 端点添加 AST 危险导入检查 | 30 min |
| 3 | M7 | `bridge/qmt_shell.py` | FIX_PRICE+0 改为 LATEST_PRICE | 5 min |
| 4 | M8 | `bridge/model_server.py` | 策略实例池缓存 | 30 min |
| 5 | L4 | `bridge/qmt_server.py` | 修复残留 escape 序列 | 5 min |
| 6 | L7 | `bridge/qmt_server.py` | 统一使用 `_shared.to_xtcode` | 10 min |

### 详细修复方案

#### 1. S3: mlp.py squeeze(-1) 修复

**文件**：`bridge/strategies/ml/models/mlp.py`
**位置**：`forward` 方法末尾
**改动**：
```python
# 修改前
return logits.squeeze(-1)

# 修改后
if logits.shape[-1] == 1:
    return logits.squeeze(-1)
return logits
```
与 `lstm.py` / `transformer.py` 保持一致。

#### 2. S6: /strategy 端点 AST 危险导入检查

**文件**：`bridge/model_server.py`
**位置**：`do_POST` 中 `path == '/strategy'` 分支，`save_strategy` 调用前
**改动**：新增 `_check_code_safety(code)` 函数，用 `ast` 模块解析源码，检查是否包含危险导入（`os`/`subprocess`/`shutil`/`socket`/`ctypes`/`builtins` 的 `__import__`/`eval`/`exec`/`compile`/`open` 等）。检测到危险调用时返回错误，拒绝保存。

#### 3. M7: qmt_shell.py 限价单修复

**文件**：`bridge/qmt_shell.py`
**位置**：`_place_order` 函数
**改动**：
```python
# 修改前
xttrader.order_stock(ACCOUNT, SYMBOL, xtconstant.STOCK_BUY, 100, xtconstant.FIX_PRICE, 0)

# 修改后
xttrader.order_stock(ACCOUNT, SYMBOL, xtconstant.STOCK_BUY, 100, xtconstant.LATEST_PRICE, -1)
```
`LATEST_PRICE` + `-1` 表示以最新价市价委托，语义正确。

#### 4. M8: 策略实例池缓存

**文件**：`bridge/model_server.py`
**位置**：`_compute_signal` 函数
**改动**：维护全局 `_strategy_pool = {}`（`{strategy_name: (strat_instance, feature_builder)}`）。`_compute_signal` 先查池中是否有可用实例，命中则复用（调 `on_init`/`on_after_init` 重置状态后直接喂 bar）。池容量限制 10，LRU 淘汰。线程安全用 `threading.Lock`。

#### 5. L4: qmt_server.py 残留 escape 序列

**文件**：`bridge/qmt_server.py`
**位置**：搜索 `\u4e0d\u80fd\u4e3a\u7a7a`
**改动**：替换为直接中文 `"code 不能为空"`。

#### 6. L7: qmt_server.py 统一 to_xtcode

**文件**：`bridge/qmt_server.py`
**位置**：删除本地 `to_xtcode` 函数定义，改为从 `_shared` 导入
**改动**：
```python
# 在文件头部添加
from _shared import to_xtcode
# 删除本地 to_xtcode 函数定义（约 30 行）
```
注意：`qmt_server.py` 版本有额外的 `return lower + ".SH"` fallback（6 位纯数字但首字符不在已知范围时），`_shared.py` 版本无此分支。需确认是否需要在 `_shared.py` 中补充此 fallback。经检查，6 位数字首字符只能是 0-9，其中 0/2/3 -> SZ，6/9/5 -> SH，剩余 1/4/7/8 无对应市场，fallback 到 `.SH` 是安全兜底。应在 `_shared.py` 中补充此分支后统一引用。

---

## 三、执行顺序

1. L7 → L4（同文件 qmt_server.py，一次修改）
2. S3（mlp.py，独立文件）
3. M7（qmt_shell.py，独立文件）
4. S6 + M8（model_server.py，同文件，一次修改）

---

**评估人**：小奶茉
