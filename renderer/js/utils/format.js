/** 渲染层公共格式化工具（空值占位符统一为 "-"） */

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"'\/]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "/": "&#x2F;" }[c];
  });
}

function fmtNum(n, d) {
  d = d == null ? 2 : d;
  if (n == null || isNaN(n)) return "-";
  return Number(n).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtPct(n) {
  if (n == null || isNaN(n)) return "-";
  return (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%";
}

/** 金额缩写（带空格，用于「成交额」等） */
function fmtBig(n) {
  if (n == null || isNaN(n)) return "-";
  if (n >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
  if (n >= 1e4) return (n / 1e4).toFixed(2) + " 万";
  return Number(n).toLocaleString("zh-CN");
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return "-";
  return v < 10 ? Number(v).toFixed(3) : Number(v).toFixed(2);
}

/** 成交量缩写（无空格，用于图表轴标签） */
function fmtVol(v) {
  if (v == null || isNaN(v)) return "-";
  if (v >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (v >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return String(v);
}

/** 涨跌方向 → 颜色类（红涨绿跌） */
function cls(n) {
  if (n > 0) return "up";
  if (n < 0) return "down";
  return "flat";
}

export { escapeHtml, fmtNum, fmtPct, fmtBig, fmtPrice, fmtVol, cls };
