#!/bin/sh
# mooquant Linux 客户端 · 打包脚本（开发用，不随分发包走）
#
# 产出两个东西，都落在 linux/dist/（已被 .gitignore 的 dist/ 规则忽略）：
#   mooquant-<版本>.tar.gz         压缩包，解压后里边有 install.sh
#   mooquant-install-<版本>.sh     自解压安装包，chmod +x 后直接跑
#
# 后者的负载是 base64 纯文本，所以整个文件是纯文本 —— 能过任何「只让传文本」的
# 通道（比如 pscp 不通、只能拿 plink 管道灌数据的场合）。

set -eu

SRC_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT_DIR=$SRC_DIR/dist

VER=$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$SRC_DIR/mooquant/__init__.py")
[ -n "$VER" ] || { printf 'build-dist.sh: 读不到 __version__，检查 mooquant/__init__.py\n' >&2; exit 1; }

NAME=mooquant-$VER
STAGE=$OUT_DIR/$NAME
TARBALL=$OUT_DIR/$NAME.tar.gz
SELF=$OUT_DIR/mooquant-install-$VER.sh

# ---------------------------------------------------------------- 装配目录

rm -rf "$STAGE"
mkdir -p "$STAGE"
# 用 tar 搬运而不是 cp -R，顺手把 __pycache__ / *.pyc 挡在外面
(cd "$SRC_DIR" && tar cf - --exclude=__pycache__ --exclude='*.pyc' mooquant bin) \
    | (cd "$STAGE" && tar xf -)
cp "$SRC_DIR/install.sh" "$SRC_DIR/README.md" "$STAGE/"
chmod +x "$STAGE/bin/mooquant" "$STAGE/install.sh"

# ---------------------------------------------------------------- 压缩包

tar czf "$TARBALL" -C "$OUT_DIR" "$NAME"

# ---------------------------------------------------------------- 自解压包

gen_header() {
    sed -e "s/@VERSION@/$VER/g" -e "s/@PAYLOAD_LINE@/$1/g" <<'HEADER'
#!/bin/sh
# mooquant Linux 客户端 · 自解压安装包 @VERSION@
#
# 用法：
#   chmod +x 本文件
#   ./本文件
#
# 本文件是纯文本（负载为 base64），可以过邮件、聊天、plink 管道这类通道。
# 它把自己解到临时目录、调里面的 install.sh，跑完自动清理临时目录。

set -eu

PAYLOAD_LINE=@PAYLOAD_LINE@

TMP_DIR=$(mktemp -d) || exit 1
trap 'rm -rf "$TMP_DIR"' EXIT HUP INT TERM

tail -n "+$PAYLOAD_LINE" "$0" | base64 -d | tar xzf - -C "$TMP_DIR"

sh "$TMP_DIR/mooquant-@VERSION@/install.sh"

printf '\n上面装好了。这个包本身留着没用，可以删掉。\n'

# exit 不能省：shell 是一行一行读本文件的，不退出就会接着把下面的 base64 负载
# 当成命令执行，报一串 "H4sIAAA...: not found"。install.sh 失败时 set -e 会抢先
# 退出，EXIT trap 照常清理临时目录。
exit 0
HEADER
}

# 先数一遍表头行数，才知道负载从第几行开始；表头里的占位符要换成同样的行数，
# 否则两遍长度不一致，行号就偏了。
gen_header 0 | sh -n \
    || { printf 'build-dist.sh: 自解压包表头语法有误\n' >&2; exit 1; }

HDR_LINES=$(gen_header 0 | wc -l | tr -d ' ')
PAYLOAD_LINE=$((HDR_LINES + 1))

gen_header "$PAYLOAD_LINE" > "$SELF"
base64 < "$TARBALL" >> "$SELF"

# ---------------------------------------------------------------- 自检

tail -n "+$PAYLOAD_LINE" "$SELF" | base64 -d | cmp -s - "$TARBALL" \
    || { printf 'build-dist.sh: 自解压包的负载和 tar.gz 对不上（PAYLOAD_LINE 数错了？）\n' >&2; exit 1; }

# ---------------------------------------------------------------- 汇报

printf '版本 %s\n\n' "$VER"
ls -lh "$TARBALL" "$SELF" | awk '{printf "  %-40s %s\n", $NF, $5}'
printf '\n分发包里只有 install.sh / README.md / bin / mooquant，本打包脚本不在其中。\n'
