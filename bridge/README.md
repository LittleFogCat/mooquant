# mookquant · bridge/

Python 桥接服务，提供给 Electron 主进程通过 stdio 调用。

## 启动

```bash
python qmt_server.py
```

主进程（`main.js` → `main/datasources/qmt.js`）会自动 spawn 这个脚本。

## 协议

- 行分隔的 JSON（每行一条）
- request：`{"id": "1", "method": "quote.snapshot", "params": {"code": "600519.SH"}}`
- response：`{"id": "1", "result": {...}}` 或 `{"id": "1", "error": "..."}`

## 已实现

- `ping` — 心跳，进程存活检查
- `quote.snapshot` — 单只股票快照（**当前为占位实现，请补全 xtquant 真实调用**）

## 接入真实行情

1. 安装 `xtquant`：
   ```bash
   pip install xtquant
   ```
2. 启动 miniQMT 客户端（miniQMT 与 xtquant 通过本地进程通信）
3. 修改 `qmt_server.py` 中的 `handle_quote_snapshot`，按 xtquant 实际函数签名接入