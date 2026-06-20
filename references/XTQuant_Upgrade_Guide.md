# XTQuant 升级指南

## 原理

xtquant 是迅投 QMT 的 Python SDK，**不是 PyPI 包**，不能通过 pip 安装。安装方式为：从官方下载页面获取 RAR 压缩包 → 解压 → 复制到 Python 的 `site-packages`。

**关键约束：下载地址必须是动态获取的，不能硬编码。**
官方页面 `https://dict.thinktrader.net/nativeApi/download_xtquant.html` 始终包含最新版链接，格式为 `/packages/xtquant_XXXXXX.rar`。脚本通过正则从页面 HTML 中提取第一个（最新）匹配项，确保始终下载最新版本。

## 快速升级

```bash
# 一键升级到最新版：自动下载 → 解压 → 备份旧版 → 安装 → 验证
python scripts/install_xtquant.py

# 查看当前状态
python scripts/install_xtquant.py --status
```

## 手动升级步骤

如果自动脚本不可用，按以下步骤手动操作：

### 1. 获取最新下载地址

```bash
# 从官方页面抓取最新版链接
curl -sL "https://dict.thinktrader.net/nativeApi/download_xtquant.html" | grep -oE '"/packages/xtquant_[0-9]+[a-z]*\.rar"'
```

输出示例：`"/packages/xtquant_250807.rar"` → 完整 URL 为 `https://dict.thinktrader.net/packages/xtquant_250807.rar`

### 2. 下载 RAR

```bash
curl -L -o xtquant_latest.rar "https://dict.thinktrader.net/packages/xtquant_250807.rar"
```

### 3. 解压

需要 7-Zip（`C:\Program Files\7-Zip\7z.exe`）：

```bash
"C:\Program Files\7-Zip\7z.exe" x xtquant_latest.rar -o".\xtquant_extract" -y
```

### 4. 安装

```bash
# 备份旧版
cp -r "D:\software\python\Lib\site-packages\xtquant" "D:\software\python\Lib\site-packages\xtquant.bak"

# 移除旧版
rm -rf "D:\software\python\Lib\site-packages\xtquant"
rm -rf "D:\software\python\Lib\site-packages\xtquant-*.dist-info"

# 安装新版
cp -r .\xtquant_extract\xtquant "D:\software\python\Lib\site-packages\xtquant"
```

### 5. 验证

```bash
python -c "import xtquant; print(xtquant.__file__)"
```

## 回滚

如果新版出现问题：

```bash
# 自动回滚
python scripts/install_xtquant.py --rollback

# 手动回滚
rm -rf "D:\software\python\Lib\site-packages\xtquant"
cp -r "D:\software\python\Lib\site-packages\xtquant.bak" "D:\software\python\Lib\site-packages\xtquant"
```

## 版本历史

| 版本 | 发布日期 | 主要更新 |
|------|----------|----------|
| xtquant_250807 | 2025-12-19 | 智能算法接口、千档数据源模式、Token 模式调整 |
| xtquant_250516 | 2025-05 | — |
| xtquant_241014 | 2024-10-17 | — |

完整更新日志见官方页面：https://dict.thinktrader.net/nativeApi/download_xtquant.html

## 关键路径

| 项目 | 路径 |
|------|------|
| 官方下载页面 | https://dict.thinktrader.net/nativeApi/download_xtquant.html |
| Python site-packages | `D:\software\python\Lib\site-packages\` |
| xtquant 安装目录 | `D:\software\python\Lib\site-packages\xtquant\` |
| 旧版备份 | `D:\software\python\Lib\site-packages\xtquant.bak\` |
| 7-Zip | `C:\Program Files\7-Zip\7z.exe` |
| 项目安装脚本 | `scripts/install_xtquant.py` |
