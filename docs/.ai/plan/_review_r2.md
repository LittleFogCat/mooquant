## 架构师审核意见（第2轮）

### 审核范围

检查第1轮的7个问题是否已充分解决：

1. [解决] 模型加载效率: LRU缓存设计清晰，_cache+_cache_order+_lock。
   - 补充: delete(model_id) 时应同步清除缓存。实现时注意。
2. [解决] DataFetcher 接口: 接口定义清晰，HttpDataFetcher + MockDataFetcher 满足可测试性。
   - 补充: 需确认 qmt_server 有 quote.history 的 HTTP 端点（当前是 stdio RPC，需在 model_server 中通过 qmt_server 的 stdio 或另建 HTTP 端口）。
   - 简化方案: model_server 启动时接收 qmt_server 的 stdio 管道引用，或直接导入 xtdata（同一 Python 环境）。实现时决定。
3. [解决] 线程安全: threading.Lock 覆盖所有写操作。
4. [解决] HTTP错误: 统一 {error, code} + HTTP状态码。
5. [解决] ModelWrapper: 接口隔离 torch，DummyModelWrapper 不依赖 torch。
6. [解决] 集成测试: test_integration.py + test_http_errors.py。
7. [解决] 版本关联: parent_model_id 字段。

### 新发现问题

### 问题8: qmt_server 数据获取机制 [中等]

qmt_server.py 是 stdio JSON-RPC 服务，没有 HTTP 端口。DataFetcher 的 HttpDataFetcher 需要 HTTP 端点，但该端点不存在。

解决方案: model_server.py 启动时直接导入 xtdata（与 qmt_server 同一 Python 环境）。
DataFetcher 接口不变，实现改为 XtdataDataFetcher，直接调 xtdata.get_market_data_ex。
MockDataFetcher 保留用于测试。

### 审核结论: 条件通过

第1轮的7个问题已充分解决。
新发现的问题8 为实现细节，可在实现时解决（直接导入 xtdata 而非 HTTP）。
设计整体架构清晰，接口隔离到位，可测试性充分，PyTorch 可选依赖设计合理。

**审核通过，可以开始执行。**
