# mooquant · Linux 命令行客户端

把桌面端（Electron）的**只读局域网 HTTP 服务**包装成命令行工具，供无图形界面的
Linux 服务器使用：终端里查行情、搜标的、拉 K 线，并能自动发现局域网内的桌面端。

**只用 Python 标准库**，不依赖任何第三方包（目标机器通常没有 pip）。
Python 3.10+ 即可。

---

## 部署

目标机只要有 **Python 3.10+**（`python3 -V` 看一眼）就能跑：全走标准库，
**不需要 pip、不需要 sudo**。这是个跑完就退出的命令行工具，**没有常驻进程**，
所以也没有开机启动、没有守护进程要管。

### 方式一：用安装包（推荐）

发布产物在 `linux/dist/`，两个任挑一个：

| 产物 | 特点 |
|---|---|
| `mooquant-install-<版本>.sh` | 自解压安装包，`chmod +x` 后直接跑。**整个文件是纯文本**（负载 base64），能过邮件、聊天、管道这类通道 |
| `mooquant-<版本>.tar.gz` | 压缩包，解开后里面是 `install.sh` + 程序文件 |

```bash
# 自解压包
chmod +x mooquant-install-0.1.35.sh
./mooquant-install-0.1.35.sh

# 或者压缩包
tar xzf mooquant-0.1.35.tar.gz
cd mooquant-0.1.35 && sh install.sh
```

压缩包里的 `install.sh` 是有执行位的，`./install.sh` 也行；写成 `sh install.sh`
是因为 git clone 拿到的工作区在部分平台上会丢执行位，而 `sh install.sh` 两种情况都能跑。

`install.sh` 做的事：检查 `python3` 版本 → 拷到 `~/mooquant` →
软链到 `~/.local/bin/mooquant` → 提示 PATH 有没有配好 → 跑一次 `--version` 自检。

装完的布局：

```
~/mooquant/                        程序本体
~/.local/bin/mooquant              指向 ~/mooquant/bin/mooquant 的软链
~/.config/mooquant/config.json     配置，首次运行自动生成，权限 600
```

- 想装到别处：`MOOQUANT_HOME=/opt/mooquant ./install.sh`（软链位置用 `XDG_BIN_HOME` 改）。
- **重复执行是安全的**：程序文件原地覆盖，配置不受影响。更新版本就是把新包再跑一遍。
- **卸载**：删掉 `~/mooquant` 和 `~/.local/bin/mooquant` 就行（配置想一起清就删 `~/.config/mooquant`）。

### 方式二：源码直接用

不装也能跑，把 `linux/` 目录拷过去即可：

```bash
python3 linux/bin/mooquant status      # 直接跑入口
cd linux && python3 -m mooquant status # 或者按模块跑
```

入口脚本会自己把 `linux/` 插进 `sys.path`，所以**不用设 `PYTHONPATH`**，
也**不用管当前工作目录**——在哪个目录敲都行。

### 把文件送到目标机

```bash
# git —— 之后还能 git pull 更新
git clone <仓库地址> ~/mooquant

# scp / rsync —— 目标机装了 openssh-client 时最省事
scp mooquant-install-0.1.35.sh <用户>@<主机>:~/
rsync -av mooquant-install-0.1.35.sh <用户>@<主机>:~/
```

从 Windows 用 PuTTY 工具链时，`pscp` 可能报
`ssh_init: Network error: Cannot assign requested address`。那就绕开它，
把安装包走 `plink` 的 stdin 灌过去（本工具在目标机上实测过这条路）：

```bash
cd /d/project/quant/mooquant/linux/dist
base64 -w0 mooquant-install-0.1.35.sh > /tmp/pkg.b64
"/c/Program Files/PuTTY/plink" -ssh -batch \
  -pw '<密码>' -hostkey '<首次连接时显示的指纹>' \
  <用户>@<主机> 'base64 -d > ~/mooquant-install.sh && chmod +x ~/mooquant-install.sh && ~/mooquant-install.sh' \
  < /tmp/pkg.b64
```

### 前置条件

桌面端已启动，且**设置里已开启本地服务**（默认 `0.0.0.0:12678`）。
`mooquant status` 能连上就说明通了。

---

## 快速上手

```bash
mooquant discover            # 找到局域网内的桌面端
mooquant status              # 数据源与连接状态
mooquant quote 600519        # 单只行情（裸代码会自动补交易所前缀）
mooquant quotes 600519,000001,601318
mooquant search gzmt         # 拼音首字母 / 名称 / 代码都能搜
mooquant history 600519 -p 1d -n 30
mooquant watch               # 自选股行情
```

首次运行时如果还没配服务地址，会**自动广播发现**并把结果记进配置文件，
之后免配（与 Android 端行为一致）。不想自动发现就加 `--no-discover`。

---

## 命令

| 命令 | 说明 |
|---|---|
| `endpoints` | 列出服务端全部接口（服务端自描述，新增接口无需改本工具） |
| `status [--no-probe]` | 服务与数据源状态 |
| `quote CODE` | 单只行情快照 |
| `quotes CODE[,CODE…]` | 批量行情，**超过 50 只自动切片** |
| `history CODE [-p PERIOD] [-n COUNT] [--dividend TYPE]` | K 线历史 |
| `search KEYWORD [-n N]` | 按代码 / 名称 / 拼音首字母搜索 |
| `stocks [--offset --limit --type --exchange -q]` | 股票列表分页 |
| `discover [--probe SEC] [--listen SEC]` | 局域网发现（`--probe 0` 只被动监听） |
| `config [show\|set-url\|set-token\|set-color]` | 本地配置 |
| `watch [list\|add\|remove\|reset]` | 自选股；不带子命令即拉行情 |

全局参数（写在子命令前或后都可以）：

```
--url URL        服务地址，覆盖配置文件
--token TOKEN    鉴权 token，覆盖配置文件
--json           输出服务端原始 JSON（data 字段），便于脚本消费
--no-color       关闭配色（管道 / 重定向时本就会自动关闭）
--timeout SEC    单次 HTTP 超时，默认 8
--no-discover    未配置地址时不要自动发现，直接报错
-v, --verbose    把诊断信息打到 stderr
--version
```

### 代码写法

带前缀的 `sh600519`、`sz000001`、`bj920819` 原样传入。**纯 6 位数字**会按首位
自动补交易所前缀，方便手敲：

| 首位 | 交易所 | 说明 |
|---|---|---|
| `6` `5` `9` | `sh` | 沪市主板 / 科创板 / 场内基金 / 900 B 股 |
| `0` `1` `2` `3` | `sz` | 深市主板 / 基金 / 可转债 / B 股 / 创业板 |
| `4` `8` `92` | `bj` | 北交所（`920xxx` 优先按北交所处理） |

例如 `mooquant quote 600519` 等价于 `mooquant quote sh600519`。

---

## 配置

位置：`$MOOQUANT_CONFIG` > `$XDG_CONFIG_HOME/mooquant/config.json` >
`~/.config/mooquant/config.json`，权限 `0600`（可能存 token）。

服务地址与 token 的取值优先级：

**命令行参数 > 环境变量（`MOOQUANT_API_URL` / `MOOQUANT_API_TOKEN`）> 配置文件 > 自动发现**

```bash
mooquant config                      # 查看（token 会打码）
mooquant config set-url http://192.168.1.5:12678/mooquant/api
mooquant config set-token <token>    # 服务端未开鉴权时留空即可
mooquant config set-color green-up   # 绿涨红跌；默认 red_up（红涨绿跌）
mooquant config show --json          # 机器可读；注意这里**不含**打码
```

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 服务端返回错误（`code != 0`），或本地可预期失败（配置读写等） |
| `2` | 用法错误 |
| `3` | 连不上服务端 / 发现失败 / 尚未配置地址 |

---

## 关于发现

对应桌面端的 UDP 广播信标（默认 `12679`）。两条路：

1. **主动探测** —— 向 `255.255.255.255` 与每个网卡的定向广播地址发探测包，
   信标单播回探测包的源端口。
2. **被动监听** —— 绑定 `12679` 等一条周期广播（`--probe 0 --listen 6`）。

绑定 12679 是非特权端口，**不需要 root**。信标报文里**不含 token 明文**，
只给一个 `tokenRequired` 布尔；真的需要 token 时本工具会提示去 `config set-token`。

发现不到时依次检查：桌面端是否在跑 → 设置里「本地服务」与「发现信标」是否开着 →
两台机器是否同一局域网 → Windows 防火墙是否放行了「专用网络」。
也可以直接 `mooquant config set-url http://<主机>:12678/mooquant/api` 绕开发现。

---

## 发布（维护者看）

版本号取自 `mooquant/__init__.py`，改完跟着仓库根目录 `config.js` 一起走。

```bash
cd linux && sh build-dist.sh
```

产出落在 `linux/dist/`（已被 `.gitignore` 的 `dist/` 规则忽略，不会误提交）：
`mooquant-<版本>.tar.gz` 和 `mooquant-install-<版本>.sh`。
打包脚本会把自解压包再解一遍，跟 tar.gz 逐字节比对，对不上就报错退出。
