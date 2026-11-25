/**
 * mookquant · 单实例锁（交易主机互斥）
 *
 * 为什么需要：策略执行器、额度计数、下单节流、去重集合全部是**进程内存**状态，
 * 两个进程同时跑同一个策略就会各下一单。唯一可靠的办法是让「谁有权下单」这件事
 * 落到一个跨进程可见的地方——这里用一个带 pid 的锁文件。
 *
 * 语义：
 *  - 锁文件 data/trading.lock 记录 { pid, role, startedAt }
 *  - pid 仍活着 → 视为持有中，后来者拿不到
 *  - pid 不存在（进程被杀/断电）→ 视为陈旧锁，直接抢占
 *  - EPERM（进程存在但无权探测）→ 保守视为活着
 *
 * 注意：这只解决「谁是交易者」，不解决共享内存。拿到锁的一方才启动策略执行器，
 * 拿不到的降级为观察者（只读账户/看信号），这是设计上的取舍。
 */
const fs = require("fs");
const path = require("path");

const LOCK_FILE = path.join(__dirname, "..", "..", "data", "trading.lock");

/** 进程是否还活着（EPERM 说明存在但不可探测，按活着处理） */
function isAlive(pid) {
  const n = Number(pid);
  if (!n || n <= 0) return false;
  try {
    process.kill(n, 0);
    return true;
  } catch (e) {
    return e && e.code === "EPERM";
  }
}

class InstanceLock {
  /**
   * @param {object} [opts]
   * @param {string} [opts.file] 锁文件路径（测试用注入，避免污染真实锁）
   * @param {string} [opts.role] 角色名，仅用于日志/展示（app / daemon）
   */
  constructor(opts = {}) {
    this.file = opts.file || LOCK_FILE;
    this.role = opts.role || "app";
    this.held = false;
  }

  /** 当前持有者（无锁或锁文件损坏返回 null） */
  holder() {
    try {
      const raw = JSON.parse(fs.readFileSync(this.file, "utf-8"));
      if (!raw || !raw.pid) return null;
      return raw;
    } catch {
      return null;
    }
  }

  /**
   * 尝试获取交易权
   * @param {string} [role]
   * @returns {{ok:boolean, holder?:object, stale?:boolean}}
   */
  acquire(role) {
    const wantRole = role || this.role;
    const cur = this.holder();

    if (cur) {
      if (cur.pid === process.pid) {
        // 自己已经持有（重复调用）：更新角色即可
        this._write(wantRole);
        this.held = true;
        return { ok: true, holder: this.holder() };
      }
      if (isAlive(cur.pid)) {
        return { ok: false, holder: cur };
      }
      // 陈旧锁 → 抢占
      this._write(wantRole);
      this.held = true;
      return { ok: true, holder: this.holder(), stale: true };
    }

    this._write(wantRole);
    this.held = true;
    return { ok: true, holder: this.holder() };
  }

  /** 释放（只删自己的锁，避免误删别人的） */
  release() {
    try {
      const cur = this.holder();
      if (cur && cur.pid === process.pid) fs.unlinkSync(this.file);
    } catch {}
    this.held = false;
  }

  getStatus() {
    const cur = this.holder();
    return {
      file: this.file,
      held: !!(cur && cur.pid === process.pid),
      holder: cur,
      holderAlive: cur ? isAlive(cur.pid) : false,
    };
  }

  _write(role) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(
      this.file,
      JSON.stringify({ pid: process.pid, role, startedAt: new Date().toISOString() }, null, 2),
      "utf-8"
    );
  }
}

module.exports = { InstanceLock, LOCK_FILE, isAlive };
