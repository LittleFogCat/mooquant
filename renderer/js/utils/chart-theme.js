/**
 * 读取 CSS 变量供 ECharts 使用（Canvas 渲染无法直接引用 var()）。
 * 主题切换后需重新调用并 setOption 才能生效。
 */
function getThemeColors() {
  var s = getComputedStyle(document.documentElement);
  var isDark = document.documentElement.getAttribute("data-theme") === "dark";
  function v(name, fallback) { var val = s.getPropertyValue(name).trim(); return val || fallback; }
  return {
    bg: v("--bg-0", isDark ? "#212121" : "#ffffff"),
    text1: v("--text-1", isDark ? "#ececec" : "#0d0d0d"),
    text2: v("--text-2", isDark ? "#b4b4b4" : "#5d5d5d"),
    text3: v("--text-3", "#8e8e8e"),
    border: v("--border", isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)"),
    accent: v("--accent", isDark ? "#ececec" : "#0d0d0d"),
    red: v("--red", "#ef4444"),
    green: v("--green", "#22c55e"),
    orange: v("--orange", "#f59e0b"),
    yellow: v("--yellow", "#eab308"),
    isDark: isDark,
    tooltipBg: isDark ? "rgba(46,46,46,0.95)" : "rgba(255,255,255,0.95)",
    gridLine: isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.04)",
    axisLine: isDark ? "rgba(255,255,255,0.1)" : "rgba(0,0,0,0.1)",
    axisLabel: "#8e8e8e",
    zoomBg: isDark ? "rgba(255,255,255,0.03)" : "rgba(0,0,0,0.03)",
    zoomFiller: isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)",
    areaFill: isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.04)",
  };
}

export { getThemeColors };
