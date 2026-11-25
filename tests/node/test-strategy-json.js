/**
 * 策略文件读取测试（BOM 容忍）
 *
 * 背景：白天的真实故障——用 PowerShell 写出的策略 JSON 带 UTF-8 BOM，
 * `JSON.parse` 抛错后被列表读取的 try/catch 吞掉，策略**静默消失**：
 * 界面上看不到、执行器也不会恢复它。这类问题不写测试基本查不出来。
 */
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { readJsonLoose } = require("../../main/services/strategy-service");

const TMP = path.join(os.tmpdir(), "mookquant-strategy-bom-test.json");

module.exports = {
  name: "策略文件读取",
  cases: [
    {
      name: "普通 UTF-8 JSON 正常解析",
      fn() {
        fs.writeFileSync(TMP, JSON.stringify({ id: "a", status: "running" }), "utf-8");
        assert.deepStrictEqual(readJsonLoose(TMP), { id: "a", status: "running" });
        fs.unlinkSync(TMP);
      },
    },
    {
      name: "带 UTF-8 BOM 的文件也能解析（记事本/PowerShell 写出来的）",
      fn() {
        const body = JSON.stringify({ id: "b", status: "running" });
        fs.writeFileSync(TMP, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(body, "utf-8")]));
        assert.deepStrictEqual(readJsonLoose(TMP), { id: "b", status: "running" }, "BOM 不该让策略消失");
        fs.unlinkSync(TMP);
      },
    },
    {
      name: "BOM 与换行/空格混在一起也能解析",
      fn() {
        const body = "\r\n  " + JSON.stringify({ id: "c" }) + "\r\n";
        fs.writeFileSync(TMP, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(body, "utf-8")]));
        assert.deepStrictEqual(readJsonLoose(TMP), { id: "c" });
        fs.unlinkSync(TMP);
      },
    },
    {
      name: "内容非法时仍然抛错（不能把真错误吞掉）",
      fn() {
        fs.writeFileSync(TMP, "{ 这不是 JSON", "utf-8");
        assert.throws(() => readJsonLoose(TMP));
        fs.unlinkSync(TMP);
      },
    },
  ],
};
