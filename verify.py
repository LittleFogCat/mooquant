"""
mookquant · 一键验证脚本
"""
import subprocess
import sys
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def section(title):
    print("\n=== " + title + " ===")


def ok(msg): print("  [OK]   " + msg)
def fail(msg): print("  [FAIL] " + msg)


def test_node():
    section("Node 端依赖版本")
    code = """
const p = require('./package.json');
const fs = require('fs');
for (const dep of Object.keys(p.devDependencies||{})) {
  const hasPkg = fs.existsSync('node_modules/'+dep+'/package.json');
  console.log((hasPkg?'  [OK]  ':'  [FAIL] ') + dep);
}
try { console.log('  [OK]  electron version:', require('electron/package.json').version); }
catch(e){ console.log('  [FAIL] electron require:', e.message); }
try { console.log('  [OK]  electron-builder version:', require('electron-builder/package.json').version); }
catch(e){ console.log('  [FAIL] electron-builder require:', e.message); }
"""
    r = subprocess.run(["node", "-e", code], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout, end="")


def test_modules():
    section("主进程模块加载")
    code = """
try { require('./main/ipc'); console.log('  [OK]  main/ipc'); } catch(e){ console.log('  [FAIL] main/ipc:', e.message); }
try { require('./main/services/quote-service'); console.log('  [OK]  quote-service'); } catch(e){ console.log('  [FAIL] quote-service:', e.message); }
try { require('./main/datasources'); console.log('  [OK]  datasources'); } catch(e){ console.log('  [FAIL] datasources:', e.message); }
const {MockDataSource} = require('./main/datasources/mock');
console.log('  [OK]  MockDataSource ready, mode=' + new MockDataSource().mode);
const {QmtDataSource} = require('./main/datasources/qmt');
console.log('  [OK]  QmtDataSource ready, mode=' + new QmtDataSource().mode);
"""
    r = subprocess.run(["node", "-e", code], cwd=ROOT, capture_output=True, text=True)
    print(r.stdout, end="")


def test_qmt_live():
    section("QMT 真实数据（要求 miniQMT 在线）")
    code = """
process.env.MOOKQUANT_PYTHON = 'python';
const { QmtDataSource } = require('./main/datasources/qmt');
(async () => {
  const src = new QmtDataSource();
  try {
    await src.init();
    const codes = ['sh600519', 'sz000001', 'sz300750'];
    for (const code of codes) {
      const r = await src.getQuote(code);
      console.log('  [OK]  ' + r.rawSymbol + ' -> ' + r.name + ' (' + r.marketName + ') price=' + r.price + ' change=' + r.changePercent + '%');
    }
    src.dispose();
  } catch (e) {
    console.log('  [SKIP] miniQMT 未启动或 xtquant 未就绪: ' + e.message);
    src.dispose();
  }
})();
"""
    r = subprocess.run(["node", "-e", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
    print(r.stdout, end="")


def test_bridge_protocol():
    section("Python 桥协议（独立测试，可不依赖 miniQMT）")
    py = sys.executable
    proc = subprocess.Popen(
        [py, os.path.join(ROOT, "bridge", "qmt_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    def send(req):
        proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))

    def read_one():
        line = proc.stdout.readline().decode("utf-8")
        return json.loads(line)

    try:
        send({"id": "1", "method": "ping", "params": {}})
        obj = read_one()
        if "result" in obj and obj["result"].get("alive"):
            ok("bridge ping: alive (miniQMT=%s)" % obj["result"].get("connected"))
        else:
            print("  [INFO] ping: " + json.dumps(obj))
    except Exception as e:
        fail("bridge test crashed: " + str(e))
    finally:
        try: proc.stdin.close()
        except: pass
        try: proc.wait(timeout=5)
        except: proc.kill()


def main():
    print("\nmookquant · 一键验证\n" + "=" * 50)
    test_node()
    test_modules()
    test_bridge_protocol()
    test_qmt_live()
    print("\n" + "=" * 50 + "\n验证结束\n")


if __name__ == "__main__":
    main()