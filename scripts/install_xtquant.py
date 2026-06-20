"""
安装 / 升级 xtquant（QMT Python SDK）。

从迅投官方下载页面获取最新版本，解压并安装到系统 Python 的 site-packages。
无需 pip — xtquant 不是 PyPI 包，直接从 RAR 解压安装。

用法：
    python scripts/install_xtquant.py                # 安装最新版（自动下载）
    python scripts/install_xtquant.py --rar path     # 从本地 RAR 安装
    python scripts/install_xtquant.py --rollback     # 回滚到备份版本
    python scripts/install_xtquant.py --status       # 查看当前版本和备份状态

下载来源：
    https://dict.thinktrader.net/nativeApi/download_xtquant.html
"""

import argparse
import glob
import os
import re
import shutil
import sys
import tempfile
from urllib.request import urlopen

# 项目约定的 xtquant 安装路径
SITE_PACKAGES = r"D:\software\python\Lib\site-packages"
XTQUANT_DIR = os.path.join(SITE_PACKAGES, "xtquant")
BACKUP_PATTERN = os.path.join(SITE_PACKAGES, "xtquant_*.bak")

# 官方下载页面 (VuePress SPA，实际链接在 JS bundle 中)
PAGE_URL = "https://dict.thinktrader.net/nativeApi/download_xtquant.html"
RAR_PATTERN = re.compile(r'"/packages/(xtquant_\d+[a-z]*\.rar)"')


def get_latest_download_url() -> tuple[str, str] | None:
    """从官方页面解析最新版 RAR 下载链接。返回 (url, version)。"""
    try:
        html = urlopen(PAGE_URL, timeout=15).read().decode("utf-8")
    except Exception as e:
        print(f"[错误] 无法访问下载页面: {e}")
        return None

    matches = RAR_PATTERN.findall(html)
    if not matches:
        print("[错误] 未在页面中找到下载链接")
        return None

    # 取第一个（最新）版本
    latest = matches[0]
    version = latest.replace(".rar", "")
    url = f"https://dict.thinktrader.net/packages/{latest}"
    return url, version


def download_rar(url: str, dest: str) -> bool:
    """下载 RAR 文件，带进度条。"""
    print(f"下载: {url}")
    try:
        resp = urlopen(url, timeout=60)
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {pct:.0f}% ({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB)",
                          end="", flush=True)
        print()
        return True
    except Exception as e:
        print(f"\n[错误] 下载失败: {e}")
        return False


def extract_rar(rar_path: str, dest_dir: str) -> bool:
    """使用 7-Zip 解压 RAR 文件。"""
    seven_zip = r"C:\Program Files\7-Zip\7z.exe"
    if not os.path.exists(seven_zip):
        print("[错误] 未找到 7-Zip，请安装: https://7-zip.org/")
        return False

    print(f"解压: {rar_path}")
    ret = os.system(f'"{seven_zip}" x "{rar_path}" -o"{dest_dir}" -y > nul')
    return ret == 0


def install_xtquant(source_dir: str) -> bool:
    """安装 xtquant：备份旧版 → 替换 → 验证。"""
    ver = os.path.basename(os.path.dirname(source_dir.rstrip("/\\")))
    if not os.path.isfile(os.path.join(source_dir, "__init__.py")):
        # source_dir 可能是包含 xtquant/ 子目录的父目录
        candidate = os.path.join(source_dir, "xtquant")
        if os.path.isfile(os.path.join(candidate, "__init__.py")):
            source_dir = candidate
        else:
            print(f"[错误] 在 {source_dir} 中未找到 xtquant/__init__.py")
            return False

    # 备份旧版
    if os.path.exists(XTQUANT_DIR):
        backup_dir = XTQUANT_DIR + ".bak"
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        print(f"备份: {XTQUANT_DIR} → {backup_dir}")
        shutil.copytree(XTQUANT_DIR, backup_dir)

    # 移除旧版
    if os.path.exists(XTQUANT_DIR):
        shutil.rmtree(XTQUANT_DIR)
    # 移除旧的 pip dist-info
    for d in glob.glob(os.path.join(SITE_PACKAGES, "xtquant-*.dist-info")):
        shutil.rmtree(d)

    # 安装新版
    print(f"安装: {source_dir} → {XTQUANT_DIR}")
    shutil.copytree(source_dir, XTQUANT_DIR)

    # 验证
    try:
        sys.path.insert(0, SITE_PACKAGES)
        import xtquant
        print(f"[OK] 安装成功: xtquant @ {xtquant.__file__}")
        return True
    except ImportError as e:
        print(f"[错误] 导入验证失败: {e}")
        return False


def rollback() -> bool:
    """回滚到备份版本。"""
    backup_dir = XTQUANT_DIR + ".bak"
    if not os.path.exists(backup_dir):
        print("[错误] 未找到备份，无法回滚")
        return False

    if os.path.exists(XTQUANT_DIR):
        shutil.rmtree(XTQUANT_DIR)
    for d in glob.glob(os.path.join(SITE_PACKAGES, "xtquant-*.dist-info")):
        shutil.rmtree(d)

    shutil.copytree(backup_dir, XTQUANT_DIR)
    print(f"[OK] 已回滚到 {backup_dir}")
    return True


def show_status():
    """显示当前安装状态。"""
    # 当前版本
    current = "无"
    if os.path.exists(XTQUANT_DIR):
        init_file = os.path.join(XTQUANT_DIR, "__init__.py")
        try:
            with open(init_file, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r'__version__\s*=\s*"([^"]*)"', content)
            current = match.group(1) if match else "unknown"
        except Exception:
            current = "unknown"

    # 备份
    backup_dir = XTQUANT_DIR + ".bak"
    backup = "存在" if os.path.exists(backup_dir) else "无"

    # 已安装的关键文件
    pyd_count = len(glob.glob(os.path.join(XTQUANT_DIR, "*.pyd"))) if os.path.exists(XTQUANT_DIR) else 0

    print(f"当前 xtquant: {current}")
    print(f"安装路径:     {XTQUANT_DIR}")
    print(f"原生扩展:     {pyd_count} 个 .pyd")
    print(f"备份:         {backup}")
    if os.path.exists(backup_dir):
        size = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, files in os.walk(backup_dir)
            for f in files
        )
        print(f"备份大小:     {size / 1024 / 1024:.1f} MB")


def main():
    parser = argparse.ArgumentParser(
        description="安装 / 升级 xtquant（QMT Python SDK）")
    parser.add_argument("--rar", help="从本地 RAR 文件安装")
    parser.add_argument("--rollback", action="store_true", help="回滚到备份版本")
    parser.add_argument("--status", action="store_true", help="查看当前状态")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.rollback:
        rollback()
        return

    # 确定 RAR 来源
    rar_path = args.rar
    cleanup_rar = False

    if not rar_path:
        # 自动下载最新版
        result = get_latest_download_url()
        if not result:
            sys.exit(1)

        url, version = result
        print(f"最新版本: {version}")
        rar_path = os.path.join(tempfile.gettempdir(), f"{version}.rar")
        if not download_rar(url, rar_path):
            sys.exit(1)
        cleanup_rar = True

    # 解压
    extract_dir = tempfile.mkdtemp(prefix="xtquant_install_")
    if not extract_rar(rar_path, extract_dir):
        sys.exit(1)

    # 安装
    ok = install_xtquant(extract_dir)

    # 清理
    shutil.rmtree(extract_dir)
    if cleanup_rar:
        os.remove(rar_path)

    if ok:
        print("\n安装完成。可通过 python scripts/install_xtquant.py --status 确认。")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
