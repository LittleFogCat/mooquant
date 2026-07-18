/**
 * mookquant · Pinyin utility (powered by pinyin-pro)
 */
const { pinyin } = require("pinyin-pro");

function getInitials(str) {
  if (!str) return "";
  return pinyin(str, { pattern: "first", toneType: "none" }).replace(/\s/g, "");
}

module.exports = { getInitials };
