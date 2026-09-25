#!/bin/sh
# mooquant Linux 客户端 · 安装脚本
#
# 装到本用户下，不需要 sudo：
#   ~/mooquant              程序本体（用 MOOQUANT_HOME 可改）
#   ~/.local/bin/mooquant   指向入口脚本的软链（用 XDG_BIN_HOME 可改）
#
# 用法：
#   ./install.sh            在源码目录、或解压后的包目录里直接跑
#   ./mooquant-install-*.sh 自解压包会解到临时目录再调本脚本，不用手动解压
#
# 重复执行是安全的：程序文件原地覆盖，配置在 ~/.config/mooquant/ 下不受影响。

set -eu

PY=python3
INSTALL_DIR=${MOOQUANT_HOME:-$HOME/mooquant}
BIN_DIR=${XDG_BIN_HOME:-$HOME/.local/bin}
LINK=$BIN_DIR/mooquant

die() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

say() { printf '%s\n' "$1"; }

# 先查 python。下面定位自身目录要用 dirname，而 PATH 要是坏的，在这里给出
# 可读的提示，比让 set -e 闷掉强。

command -v "$PY" >/dev/null 2>&1 \
    || die "找不到 python3。本工具只用标准库，装个 Python 3.10+ 就行，不需要 pip。"

PY_VER=$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || true)
[ -n "$PY_VER" ] \
    || die "python3 在（$(command -v "$PY")），但跑不起来。先确认 python3 --version 正常。"

"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 \
    || die "需要 Python 3.10 及以上，当前是 $PY_VER。"

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

[ -f "$SRC_DIR/mooquant/cli.py" ] && [ -f "$SRC_DIR/bin/mooquant" ] \
    || die "在 $SRC_DIR 里找不到 mooquant 的程序文件。请在解压后的包目录（或仓库的 linux/ 目录）里运行本脚本。"

[ "$SRC_DIR" = "$INSTALL_DIR" ] \
    && die "安装目录和源目录是同一个（$SRC_DIR），换个位置：MOOQUANT_HOME=... $0"

# 落盘之前把所有会失败的情况过一遍，免得装到一半留个烂摊子

if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ] \
   && [ ! -f "$INSTALL_DIR/mooquant/__init__.py" ]; then
    die "$INSTALL_DIR 已存在，且里面不像 mooquant 的安装目录。确认一下，或换个位置：MOOQUANT_HOME=/别的/路径 $0"
fi

if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
    die "$LINK 已存在，而且不是软链。要装的话先把它挪走（rm 或改名都行）。"
fi

# 开始装

mkdir -p "$INSTALL_DIR"
rm -rf "$INSTALL_DIR/mooquant" "$INSTALL_DIR/bin"
cp -R "$SRC_DIR/mooquant" "$INSTALL_DIR/mooquant"
cp -R "$SRC_DIR/bin" "$INSTALL_DIR/bin"
cp "$SRC_DIR/README.md" "$INSTALL_DIR/README.md"
chmod +x "$INSTALL_DIR/bin/mooquant"
rm -rf "$INSTALL_DIR/mooquant/__pycache__"

mkdir -p "$BIN_DIR"
ln -sfn "$INSTALL_DIR/bin/mooquant" "$LINK"

# 结尾自检：这一步会把 cli / api / config / models / render / discovery 全导一遍，
# 能跑出 --version 就说明文件没在传输里损坏、依赖也没漏。
# 走 $LINK 而不是真实路径：用户敲的就是这个软链，得连软链一起验过才算数。
VER=$("$PY" "$LINK" --version) \
    || die "装完了但跑不起来，请把上面的报错发我。"

say ""
say "已装到   $INSTALL_DIR"
say "入口     $INSTALL_DIR/bin/mooquant"
say "命令     $LINK"

case ":${PATH:-}:" in
    *":$BIN_DIR:"*)
        say "         （已在 PATH 里，直接敲 mooquant 就行）"
        ;;
    *)
        say ""
        say "注意：$BIN_DIR 不在 PATH 里，现在敲 mooquant 会找不到。补一行到 ~/.bashrc："
        say "  export PATH=\"$BIN_DIR:\$PATH\""
        say "改完 source ~/.bashrc，或者重开一个终端。"
        ;;
esac

say ""
say "自检通过：$VER"
say ""
say "接下来："
say "  mooquant discover     # 找到局域网内的桌面端，地址会自动记进配置"
say "  mooquant status       # 确认数据源与连接状态"
say ""
say "配置在 ~/.config/mooquant/config.json（权限 600）。"
say "卸载就是删掉 $INSTALL_DIR 和 $LINK。"
