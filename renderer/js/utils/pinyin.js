/**
 * mookquant · Pinyin utility (powered by pinyin-pro)
 */
import { pinyin } from 'pinyin-pro';

export function getInitials(str) {
  if (!str) return "";
  return pinyin(str, { pattern: "first", toneType: "none" }).replace(/\s/g, "");
}
