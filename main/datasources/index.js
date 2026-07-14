/**
 * mookquant · 数据源抽象层
 *
 * 设计：
 *   - 每个数据源实现必须实现同名接口，便于插拔
 *   - 由工厂 createDataSource 根据模式/mode 决定具体实现
 *   - auto 模式：尝试 QMT，连不上时回落到 mock
 *
 * 接口：
 *   class DataSource {
 *     mode: string
 *     description: string
 *     async getQuote(rawSymbol): object
 *     dispose(): void
 *   }
 */

const path = require("path");

const { MockDataSource } = require("./mock");

/**
 * 工厂
 * @param {{mode: 'mock'|'qmt'|'auto', qmt?:object}} opts
 */
async function createDataSource(opts) {
  const mode = opts.mode || "mock";

  if (mode === "mock") {
    return new MockDataSource();
  }

  if (mode === "qmt") {
    const { QmtDataSource } = require("./qmt");
    const source = new QmtDataSource(opts.qmt || {});
    await source.init();
    return source;
  }

  if (mode === "auto") {
    const { QmtDataSource } = require("./qmt");
    try {
      const qmt = new QmtDataSource(opts.qmt || {});
      await qmt.init();
      console.log("[datasource] 使用 QMT 数据源");
      return qmt;
    } catch (e) {
      console.warn("[datasource] QMT 不可用，回落到 mock:", e.message);
      return new MockDataSource();
    }
  }

  throw new Error(`未知数据源模式: ${mode}`);
}

module.exports = { createDataSource };