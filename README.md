# mookquant · 量化投资桌面工具集

苹果设计风格的量化投资 **Electron 桌面应用**。

> 📄 初版规划见 [docs/.ai/plan/first_demo.md](docs/.ai/plan/first_demo.md)

## ✨ 特性

- 🖥 **Electron 跨平台桌面应用**（Windows / macOS / Linux）
- 🎨 完整 Apple Human Interface 设计语言
  - macOS 上隐藏式标题栏 + 原生 traffic-light 按钮
  - 毛玻璃卡片、SF Pro 字体、柔色渐变背景
  - 红涨绿跌的中国市场配色
- 🔍 输入股票代码（A 股 / 美股）查询实时行情
  - **QMT 数据源**（国金 QMT · 迅投 xtquant）
  - **Mock 数据源**：本地模拟，离线也能演示
  - **Auto 模式**：优先 QMT，连接不上时自动回落 mock
- 🏗 **MVVM + 分层架构**，预留扩展位：
  - 数据源接口可插拔
  - ViewModel 独立于视图
  - 主进程业务可独立测试

## 🗂 项目结构

```
mooquant/
├── package.json
├── main.js                       # Electron 主进程入口
├── preload.js                    # 安全桥接
├── config/
│   └── default.json              # 应用配置
├── main/                         # 主进程业务代码
│   ├── ipc/index.js              # IPC 路由器
│   ├── services/quote-service.js # 行情业务服务
│   └── datasources/              # 数据源适配层
│       ├── index.js              # 抽象接口 + 工厂
│       ├── mock.js               # 模拟数据
│       └── qmt.js                # QMT 桥接
├── bridge/                       # 外部进程桥
│   ├── qmt_server.py             # Python 桥（stdio JSON-RPC）
│   ├── requirements.txt
│   └── README.md
├── renderer/                     # 渲染层
│   ├── index.html
│   ├── css/{base,app}.css
│   └── js/
│       ├── bootstrap.js          # 启动入口
│       ├── services/facade.js    # 屏蔽 IPC 细节
│       ├── viewmodels/           # 视图模型层
│       │   └── quote-viewmodel.js
│       └── views/                # 视图（仅 DOM 渲染）
│           ├── search-panel.js
│           ├── result-card.js
│           └── status-toast.js
└── assets/
    ├── icon.png
    └── icon-512.png
```

## 🚀 快速开始

### 1. 安装依赖

需要 Node.js 18+。

```powershell
cd D:\project\quant\mooquant
npm install
```

### 2. 默认启动（Auto 模式）

```powershell
npm start
```

启动后会自动打开 980x720 的窗口，默认走 `auto` 数据源：尝试 QMT，连不上回落到 mock。

### 3. 强制 mock 模式（不连 QMT，演示用）

修改 `config/default.json`：

```json
{ "dataSource": "mock" }
```

### 4. 调试渲染层

应用窗口打开后：`Cmd/Ctrl + Shift + I`

## 🔌 QMT 接入

详见 [bridge/README.md](bridge/README.md)

## 📅 路线图

- [x] MVVM + 分层架构骨架
- [x] Apple Human Interface 设计
- [x] 抽象数据源接口
- [x] Mock 数据源
- [ ] QMT 数据源（主人需要把真实 xtquant 调用填入 `bridge/qmt_server.py`）
- [ ] K 线图
- [ ] 自选股（localStorage）
- [ ] 历史数据
- [ ] 多股票对比