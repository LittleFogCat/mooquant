## 架构师审核意见（第1轮）

### 问题1: 模型加载效率 [严重]

当前设计每次 /signal 请求都创建策略实例并从磁盘加载模型。ML策略加载 .pt 文件是IO密集操作。

修复: ModelRegistry 增加内存缓存(LRU)，同一 model_id 只从磁盘加载一次。MLStrategyBase.on_after_init 从缓存获取模型。

### 问题2: 训练数据获取路径不明 [严重]

model_server.py 是独立进程，但训练需要历史K线数据。计划写通过 xtdata 或 HTTP 调 qmt_server，但未明确具体机制。

修复: 定义 DataFetcher 接口，model_server 通过 HTTP 调 qmt_server 的 quote.history 获取数据。这也满足可测试性(MockDataFetcher)。

### 问题3: 并发安全 [中等]

训练在后台线程运行，ModelRegistry 的文件操作可能冲突。index.json 更新可能竞争。

修复: ModelRegistry 的 index.json 更新加 threading.Lock。

### 问题4: HTTP 错误处理未定义 [中等]

/signal 未知策略、/train 参数错误、/models/id 不存在等情况未定义错误响应格式。

修复: 统一错误响应格式 {error: message, code: NOT_FOUND}，HTTP 状态码 400/404/500。

### 问题5: Builtin dummy_model 实现模糊 [轻微]

不依赖 torch 时如何实现 forward()？

修复: 定义 ModelWrapper 接口(forward(tensor)->tensor)，dummy_model 用纯 Python 实现。torch 模型自动包装为 ModelWrapper。

### 问题6: 缺少集成测试 [轻微]

测试计划有单元测试，但缺少端到端集成测试。

修复: 增加 tests/test_integration.py，测试完整流程: 创建策略 -> POST /signal -> 验证响应。

### 问题7: 模型版本管理缺失 [轻微]

目标提到版本，但设计未实现。

修复: meta.json 增加 parent_model_id 字段，表示从哪个模型改进而来。暂不实现复杂版本管理。

### 审核结论: 不通过

7个问题中2个严重、2个中等、3个轻微。需修复严重和中等问题后重新审核。
