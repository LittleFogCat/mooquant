/**
 * 单实例锁测试
 *
 * 覆盖：首次获取、被活着的其他进程占用时拒绝、陈旧锁抢占、只删自己的锁。
 * 注意：必须用临时文件，不能碰真实的 data/trading.lock（运行中的 daemon 正持有它）。
 */
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { InstanceLock, isAlive } = require("../../main/services/instance-lock");

const TMP_LOCK = path.join(os.tmpdir(), "mookquant-instance-lock-test.lock");

function cleanup() {
  try {
    fs.unlinkSync(TMP_LOCK);
  } catch {}
}

/** 找一个确定不存在的 pid */
function freePid() {
  for (let pid = 999999; pid > 999900; pid -= 1) {
    if (!isAlive(pid)) return pid;
  }
  throw new Error("找不到空闲 pid 用于测试");
}

module.exports = {
  name: "单实例锁",
  cases: [
    {
      name: "无锁文件时可直接获取，锁内容记录 pid 与角色",
      fn() {
        cleanup();
        const lock = new InstanceLock({ file: TMP_LOCK, role: "app" });
        assert.strictEqual(lock.holder(), null);
        const r = lock.acquire("app");
        assert.strictEqual(r.ok, true);
        assert.strictEqual(r.holder.pid, process.pid);
        assert.strictEqual(r.holder.role, "app");
        assert.ok(r.holder.startedAt);
        cleanup();
      },
    },
    {
      name: "锁被活着的其他进程持有时拒绝（这正是防止双开下单的关键）",
      fn() {
        cleanup();
        const foreignPid = process.ppid; // 父进程一定活着
        assert.strictEqual(isAlive(foreignPid), true, "父进程应被判定为存活");
        fs.writeFileSync(TMP_LOCK, JSON.stringify({ pid: foreignPid, role: "daemon", startedAt: "x" }), "utf-8");

        const lock = new InstanceLock({ file: TMP_LOCK });
        const r = lock.acquire("app");
        assert.strictEqual(r.ok, false, "别人的锁不能抢");
        assert.strictEqual(r.holder.pid, foreignPid);
        assert.strictEqual(fs.readFileSync(TMP_LOCK, "utf-8").includes(String(foreignPid)), true, "不能篡改别人的锁");
        cleanup();
      },
    },
    {
      name: "持有者进程已不存在时按陈旧锁抢占",
      fn() {
        cleanup();
        const deadPid = freePid();
        fs.writeFileSync(TMP_LOCK, JSON.stringify({ pid: deadPid, role: "daemon", startedAt: "x" }), "utf-8");
        const lock = new InstanceLock({ file: TMP_LOCK });
        const r = lock.acquire("app");
        assert.strictEqual(r.ok, true, "陈旧锁应被接管");
        assert.strictEqual(r.stale, true);
        assert.strictEqual(r.holder.pid, process.pid);
        cleanup();
      },
    },
    {
      name: "同一进程重复获取视为续期",
      fn() {
        cleanup();
        const lock = new InstanceLock({ file: TMP_LOCK });
        assert.strictEqual(lock.acquire("app").ok, true);
        const again = lock.acquire("app");
        assert.strictEqual(again.ok, true);
        assert.strictEqual(again.stale, undefined);
        cleanup();
      },
    },
    {
      name: "release 只删自己的锁，不误删别人的",
      fn() {
        cleanup();
        const mine = new InstanceLock({ file: TMP_LOCK });
        mine.acquire("app");
        mine.release();
        assert.strictEqual(fs.existsSync(TMP_LOCK), false, "释放后锁文件应消失");

        // 写一个别人的锁，再调 release 不能删掉它
        fs.writeFileSync(TMP_LOCK, JSON.stringify({ pid: process.ppid, role: "daemon" }), "utf-8");
        const other = new InstanceLock({ file: TMP_LOCK });
        other.release();
        assert.strictEqual(fs.existsSync(TMP_LOCK), true, "不能删别人的锁");
        cleanup();
      },
    },
    {
      name: "锁文件损坏时按无锁处理（不会把应用卡死）",
      fn() {
        cleanup();
        fs.writeFileSync(TMP_LOCK, "{ 这不是 JSON", "utf-8");
        const lock = new InstanceLock({ file: TMP_LOCK });
        assert.strictEqual(lock.holder(), null);
        assert.strictEqual(lock.acquire("app").ok, true);
        cleanup();
      },
    },
  ],
};
