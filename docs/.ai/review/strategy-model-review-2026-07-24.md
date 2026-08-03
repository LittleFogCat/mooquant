# 策略 & 模型业务逻辑审查

**审查日期**：2026-07-24
**审查范围**：`bridge/strategies/`、`bridge/backtest_engine.py`、`bridge/training/`、`bridge/strategies/ml/`、`bridge/model_server.py`、`bridge/qmt_server.py`（strategy.* 段）、`bridge/qmt_shell.py`、`main/strategies/ma_cross.js`

> 注：未发现开放 PR。本次审查针对模型/策略相关业务的整体代码，按严重程度分类列出问题与建议。

---

## 总体评价

策略框架整体设计合理：分层清晰（基类 → 内置 → 注册器 → 导出 → 回测/实盘），回测与实盘共用同一份 `on_bar`，导出用适配壳包装模式。ML 部分遵循训练/推理复用 `FeatureBuilder` 防止特征漂移。但**实现细节中存在若干会影响实盘正确性、性能与安全的隐患**，主要集中在回测执行、ML 训练数据对齐、模型并发缓存、导出器下单逻辑四个方面。

---

## 严重问题（建议立即修复）

### S1. ML 训练数据存在 look-ahead bias（`training/labels.py` + `strategies/ml/features.py` + `training/dataset.py`）

**位置**：
- `labels.py:6-19` `make_classification_labels` / `make_regression_labels` / `make_triple_barrier_labels`
- `features.py:170-182` `build_batch`
- `dataset.py:22-29` 对齐逻辑

**问题**：`labels[i]` 是从 `closes[i]` 出发对未来 `horizon` 天的收益率，但 `X[i]` 的特征窗口是 `bars[i : i+window]`（包含 `i+window-1` 时刻的指标）。**实际训练时模型在 i 时刻已经"看到"了 i+window-1 时刻的数据**，应预测 i+window+horizon 的收益，但 label 却是 i+horizon 的收益。这导致：
- 训练标签与特征窗口错位
- 模型在回测中表现虚高，实盘显著衰减

**修复建议**：
1. `labels[i]` 应改为对应 `closes[i+window]` 之后的 horizon 收益（推理时模型看到 `bars[-window:]`，对应时刻 `len-1`，应预测 `len-1+horizon`）
2. 或者将特征窗口右移：`X[i] = bars[i+1 : i+1+window]`，label 不变
3. 在 `dataset.py` 中显式注释对齐约定并单元测试覆盖

### S2. `Trainer` 对时序数据启用 `shuffle=True`（`training/trainer.py:134`）

**位置**：`train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)`

**问题**：train/val 已按时序切片，**但 train 内部仍 shuffle**。在窗口化时序数据中，相邻 batch 的样本窗口大量重叠，shuffle 让模型用"接近未来的窗口"训练、用"接近过去的窗口"验证，且同 batch 内部样本高度相关，导致：
- 训练 loss 虚低
- 过拟合到局部窗口结构
- val 指标不具代表性

**修复建议**：
- 时序预测场景应 `shuffle=False`（顺序遍历即可）
- 或按窗口起始日分组 batch（保持窗口间时间非重叠的样本分组）

### S3. 回归模型 `squeeze(-1)` 对 `output_size>1` 不安全（`strategies/ml/models/lstm.py:36`、`transformer.py:60`）

**位置**：
```python
if self.task == 'classification':
    return logits
return logits.squeeze(-1)
```

**问题**：当 `task='regression'` 且 `output_size > 1`（多输出回归）时，`squeeze(-1)` 仅压缩最后一维，会输出错误的 shape。这是静默 bug——会通过 `MSELoss` 的部分广播，但预测 shape 与预期不符。

**修复建议**：
```python
if self.task == 'classification':
    return logits
# 回归：仅当 output_size == 1 时 squeeze
if logits.shape[-1] == 1:
    return logits.squeeze(-1)
return logits
```
或在 `__init__` 时 `assert output_size == 1 for regression task`。

### S4. QMT 导出器实盘下单使用 K 线 close 价格（`strategies/exporters/qmt_exporter.py:100-109`）

**位置**：`order_target_percent` 实盘分支：
```python
data = self._ci.get_market_data_ex(['close'], [symbol], count=1)
price = float(data[symbol]['close'].iloc[-1]) if symbol in data else 0
```

**问题**：实盘下单价取自**历史 K 线收盘价**，而非 tick 最新价。在快速行情下会产生显著滑点，且 `get_market_data_ex` 是同步阻塞调用，可能延迟下单。

**修复建议**：
- 使用 `xtdata.get_full_tick([symbol])` 或回调中缓存的最新价
- 整个实盘分支应单独抽出 `_LiveOrderAdapter` 类，与回测分支清晰分离
- 添加价格偏离检查（如与 tick 价偏差 >0.5% 则报警）

### S5. `qmt_exporter.py` 实盘数量计算可能存在单位错误（`qmt_exporter.py:104-109`）

**位置**：
```python
target_qty = int((avail + hold_vol * price) * percent / price / 100) * 100
delta = target_qty - hold_vol
if delta > 0:
    passorder(23, 1101, self._accid, symbol, 5, -1, delta, self._ci)
```

**问题**：
1. `passorder(23/24, 1101, ..., 5, -1, delta)` 中的 magic number `23/24/1101/5` 直接硬编码，未引用 `xtconstant` 常量。xtquant 版本升级后常量值可能变化
2. `int(... / 100) * 100` 是 A 股整手逻辑，但现代 xtquant `passorder` 第 6 个参数（下单数量）按**股**为单位，所以 `delta` 已经是股数，最后的 `*100` 已经做了一次整手约束，没有问题。但仍需确认测试

**修复建议**：
```python
from xtquant import xtconstant
passorder(
    xtconstant.STOCK_BUY if delta > 0 else xtconstant.STOCK_SELL,
    xtconstant.FIX_PRICE_BUY_ORDER if ... else ...,
    self._accid, symbol,
    xtconstant.LATEST_PRICE,
    -1, abs(delta), self._ci,
)
```

### S6. `model_server.py` 缺少访问控制（`bridge/model_server.py`）

**位置**：整个 `ModelHandler`

**问题**：
1. CORS `Access-Control-Allow-Origin: *`，虽然监听 127.0.0.1，浏览器跨源访问仍可绕过（同源策略对 127.0.0.1 不限制）
2. `POST /strategy` 允许任意 Python 源码写入 `data/strategies/user/` 并即时 `exec_module` 执行，**无沙箱**。任何能访问 8765 端口的进程（包括恶意浏览器扩展、跨应用 SSRF）都可在用户机器上执行任意 Python 代码
3. `DELETE /strategy`、`DELETE /models` 无确认/备份机制，误删不可恢复

**修复建议**：
- 仅在 Electron 主进程本地访问（保持现状可接受），但应显式绑定 Unix domain socket 或 Windows named pipe，避免监听 TCP
- `/strategy` 端点应做 AST 静态检查：禁止 `import os / subprocess / shutil` 等危险模块
- `DELETE` 改为两步（先标记再确认）
- 至少加 token 鉴权

---

## 中等问题（建议尽快修复）

### M1. 回测引擎仅支持单标的（`backtest_engine.py:355`）

**位置**：
```python
symbol = symbols[0]  # 当前支持单标的
```

**问题**：`params["symbols"]` 接口接受 list 实际只取第一个，与 `Context.symbol` 单值一致，但文档未明确"暂只支持单标的"。用户传入多标的时静默丢弃后续标的，容易误判结果。

**修复建议**：
- 明确错误：`if len(symbols) > 1: raise ValueError("当前仅支持单标的回测，多标的请使用 PortfolioSignal 组合策略")`
- 或实现多标的循环回测

### M2. 回测忽略印花税与过户费（`backtest_engine.py:419, 433, 446`）

**位置**：佣金计算只考虑 `commission_rate`，未考虑：
- A 股印花税（卖出收取，2023 年 8 月后 0.05%）
- 过户费（沪市双向收取）

**问题**：高频策略回测收益与实际偏差 0.05%-0.1%/次，影响业绩评估真实性。

**修复建议**：
```python
STAMP_TAX = 0.0005  # 卖出印花税
TRANSFER_FEE = 0.00001  # 过户费
if side == "sell":
    cost_factor += STAMP_TAX
cost += cost_factor * price * qty
```

### M3. 回测最大回撤硬限 `-100%` 隐藏 bug（`backtest_engine.py:510-511`）

**位置**：
```python
if max_dd < -100:
    max_dd = -100
```

**问题**：正常回测最大回撤不可能 < -100%（除非破产）。硬限会**掩盖计算错误**（如净值跳变为 0）。对用户不诚实。

**修复建议**：
- 移除该限制
- 若确实出现 < -100%，记录 warning 并检查数据完整性（除零、缺失 bar 等）

### M4. `ModelRegistry` LRU 缓存并发不安全（`training/model_registry.py:77-106`）

**位置**：
```python
@classmethod
def load(cls, model_id):
    with cls._lock:  # 仅保护部分代码
        ...
        cls._cache[model_id] = (wrapper, config)
        cls._cache_order.append(model_id)
        if len(cls._cache_order) > cls._cache_max:
            old = cls._cache_order.pop(0)
            del cls._cache[old]
        return (wrapper, config)
```

**问题**：
1. `_cache_order.remove(model_id)`（L81）是 O(N) 操作
2. `delete`（L143）时若 `model_id` 已不在 `_cache_order`（被淘汰），`remove` 抛 `ValueError`
3. 加载过程中（`build_model` + `torch.load`）在锁内执行，**长 IO 期间锁被独占**，阻塞所有并发 load

**修复建议**：
- 用 `OrderedDict` 实现 LRU（O(1) move_to_end）
- `delete` 用 try/except 保护 remove
- 将重 IO 操作移出锁：先 lock 中查 cache，未命中则释放锁再加载，加载完重新获取锁写回（双重检查）

### M5. `TrainPipeline` 后台线程为 daemon + 任务无清理（`training/pipeline.py:20-22`）

**位置**：
```python
thread = threading.Thread(target=self._run, args=(task_id, config), daemon=True)
thread.start()
```

**问题**：
1. `daemon=True`：主进程退出时正在训练的任务会被强杀，模型文件半写入，状态停留在 `'running'`
2. `_tasks` 字典无限增长，旧的 `'done'` / `'error'` 任务长期占内存

**修复建议**：
- 改为 `daemon=False`，主进程退出前 `join` 所有进行中任务
- 维护 `_tasks` 上限（如 100 条），LRU 清理已完成任务
- 训练结果也落盘（崩溃恢复）

### M6. `Trainer.metrics['train_loss']` 命名误导（`training/trainer.py:197`）

**位置**：
```python
metrics = {
    'train_loss': train_log[-1]['train_loss'] if train_log else 0,
    'val_loss': round(best_val_loss, 6),
    ...
}
```

**问题**：`train_loss` 取的是**最后一轮的** train_loss，但 `val_loss` 取的是**历史最优** val_loss。两个指标不是同一 checkpoint 的，无法对应分析。

**修复建议**：
```python
# 记录最优 epoch 的 train_loss
best_train_loss = train_log[best_epoch]['train_loss'] if train_log else 0
metrics = {'train_loss': best_train_loss, 'val_loss': best_val_loss, ...}
```
或在 metrics 中明确标注 `val_loss_at_best`，避免歧义。

### M7. `qmt_shell.py` 限价单价格传 0（`qmt_shell.py:76-78`）

**位置**：
```python
xttrader.order_stock(ACCOUNT, SYMBOL, xtconstant.STOCK_BUY, 100, xtconstant.FIX_PRICE, 0)
```

**问题**：`FIX_PRICE + price=0` 是市价单语义，**与函数名语义不符**。若 QMT 客户端实际按限价处理则会导致委托失败（限价 0 不会被接受）。

**修复建议**：
- 显式使用 `xtconstant.LATEST_PRICE` 标记市价单意图
- 或者从 `get_full_tick` 取最新价作为限价

### M8. `_compute_signal` 每次 `/signal` 重新加载模型（`model_server.py:80-92`）

**位置**：ML 策略每次调用都 `on_after_init` → `ModelRegistry.load`，即使模型已在 LRU 缓存中仍有 dict 复制、wrapper.eval()、FeatureBuilder 实例化等开销

**问题**：高频实盘信号场景下，每次 HTTP 请求都执行完整 init 流程，毫秒级延迟累积

**修复建议**：
- 维护策略实例池：`{strategy_name: (strategy_instance, feature_builder)}`，按需懒加载
- 或在 `ModelHandler` 维护 per-strategy 的 last-init 时间，对相同 (strategy, model_id) 命中跳过 init

---

## 轻度问题（建议改进）

### L1. `macd` 表达式可读性（`indicators.py:57`）

```python
dea = [None] * pad + dea_valid if pad >= 0 else dea_valid[-len(dif):]
```

**问题**：依赖 `+` 优先级高于 `if-else`，**可读性差**，应加括号：
```python
dea = ([None] * pad + dea_valid) if pad >= 0 else dea_valid[-len(dif):]
```

### L2. `rsi` Wilder 平滑注释但首值用 SMA 平均（`indicators.py:62-86`）

**问题**：首值用 SMA 平均作为初始化，与 Wilder 标准做法（Wilder 用首 period 内的简单平均）一致，但首值的 `avg_gain = gains / period` 与递推 `avg_gain = (avg_gain * (period-1) + gain) / period` 风格不统一。建议在 docstring 明确这是简化 Wilder。

### L3. `Context` 字段类型为 `Any`（`base.py:80-90`）

**位置**：
```python
self.position: Any = None
self.account: Any = None
self.indicators: Any = None
```

**问题**：下游调用者无法静态检查，IDE 无补全

**修复建议**：用 Protocol 定义 `Position` / `Account` 接口，或至少给 `indicators: Optional[ModuleType]`

### L4. `qmt_server.py:1067` 文档字符串乱码

```python
def handle_strategy_get_code(params):
    """??????"""  # 显示为问号
    name = (params.get("name") or "").strip()
    if not name:
        return {"error": "????? name"}
```

**问题**：源代码为 GBK 编码中文被错误以 UTF-8 解读（历史遗留问题，整个文件有大量 `代码` 等 escape 序列）

**修复建议**：统一全文件为 UTF-8 源码，中文字符串用直接中文而非 escape

### L5. `regenerate_mock_bars` 与 `generate_mock_bars` 重复（`backtest_engine.py:62-99`、`qmt_server.py:277-305`）

**问题**：两份几乎相同的 mock 数据生成函数（`seed_val = sum(ord(c) for c in symbol) + 42`、`rng.gauss(0, 0.02)` 等参数完全一致）。任一处改动不会同步到另一处

**修复建议**：抽取到 `bridge/strategies/_mock_data.py`（或 `bridge/mock.py`）共享

### L6. `_ARCH_STRATEGY_MAP` 硬编码且不完整（`model_server.py:27-29`）

```python
_ARCH_STRATEGY_MAP = {
    'lstm': 'lstm_trend',
}
```

**问题**：`mlp` / `transformer` 等架构激活时**都映射到 `lstm_trend`**，这是错误的——用户训练 mlp 模型激活后跑的是 lstm 策略逻辑

**修复建议**：
- 完整映射：`{'lstm': 'lstm_trend', 'mlp': 'mlp_classifier', 'transformer': 'transformer_trend'}`
- 或读取 `meta.json` 中预存的 `strategy_name` 字段

### L7. `_to_xtcode` 重复实现（`backtest_engine.py:106-127`、`qmt_server.py:139-174`）

两份代码逻辑高度相似，但边界情况处理不一致：
- `backtest_engine` 的 6 位数字默认 `.SH` 当首字符不在 `6,9,5,0,2,3` 时
- `qmt_server` 的 6 位数字默认 `.SH`（无 else fallback）

**修复建议**：抽取到 `bridge/_codes.py` 共享

### L8. `FeatureBuilder.n_features` 计算错误（`features.py:30-32`）

```python
@property
def n_features(self) -> int:
    return len(self.raw_features) + len(self.indicators) + len(self.derived)
```

**问题**：每个 indicator 可能贡献多个特征列（macd 3 个、boll 3 个、kdj 3 个），但这里只算 1 个。会导致 `model_params.setdefault('input_size', fb.n_features)` 设置的 input_size 与实际特征维度不符

**修复建议**：按 `_column_order()` 的实际展开长度计算：
```python
@property
def n_features(self) -> int:
    return len(self._column_order())
```

### L9. `main/strategies/ma_cross.js` 仍保留 deprecated 文件

**位置**：`main/strategies/ma_cross.js`

**问题**：AGENTS.md 已说明"已 deprecated"，但文件未删除，可能误导新开发者

**修复建议**：删除文件或在文件头添加 `@deprecated 请使用 bridge/strategies/builtin/ma_cross.py` 并配合 lint 规则禁止 require

### L10. `Trainer._evaluate` 使用同一 device 但未显式（`trainer.py:224-244`）

**问题**：`device = torch.device('cpu')` 硬编码，未检测 GPU。若用户配置了 CUDA 环境，会浪费算力

**修复建议**：
```python
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
```
或读取环境变量 `MODEL_DEVICE`

---

## 建议优先级路线图

| 优先级 | 编号 | 影响 | 估算工作量 |
|--------|------|------|-----------|
| P0 | S1 (look-ahead bias) | 回测-实盘收益严重偏差 | 0.5 天 |
| P0 | S2 (shuffle=True) | ML 训练指标虚高 | 0.5 小时 |
| P0 | S3 (squeeze 维度) | 静默 bug，影响回归任务 | 0.5 小时 |
| P1 | S4-S6 (导出器/服务器) | 实盘正确性 + 安全 | 2 天 |
| P1 | M2 (印花税) | 业绩评估失真 | 0.5 天 |
| P1 | M3 (max_dd 硬限) | 隐藏 bug | 5 分钟 |
| P2 | M1, M4-M8 | 用户体验与稳定性 | 2-3 天 |
| P3 | L1-L10 | 代码质量 | 1-2 天 |

---

## 附：测试覆盖盲区

本次审查未发现自动化测试。建议至少补充：
1. `labels.py` + `dataset.py` 的对齐单测（给定已知 bars，验证 X[i] 与 label[i] 时间关系）
2. `indicators.py` 与 ta-lib / pandas_ta 已知实现的交叉验证
3. `backtest_engine.run_backtest` 的端到端测试（mock 数据 + mock 策略）
4. `qmt_exporter.export` 的语法正确性（ast.parse 通过）
5. `model_server` 的端点集成测试（pytest + requests）

---

**审查人**：小奶茉 🐱
**下次审查建议**：修复 P0 后做回归；新增策略前做架构对齐 review