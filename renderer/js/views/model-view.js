/**
 * mookquant * Model Management View
 *
 * Renders model list, training form, and strategy list.
 * Communicates with model_server.py via ModelViewModel.
 */
import * as StockSearch from './stock-search.js';

// 训练表单字段说明（问号图标悬浮提示）
const FIELD_TIPS = {
  symbol: "训练标的，支持代码/名称搜索选择，多个用英文逗号分隔。\n· 多标的样本更丰富、模型泛化更好（单标的通常学不到有效信号）\n· 建议 3-8 个流动性好的标的（如 600519 贵州茅台、300750 宁德时代）\n· 不同板块/行业组合可提升模型对不同行情的适应能力",
  period: "K线周期：1d 日线、1m/5m/15m/30m/60m 分钟线。\n· 周期越大每根K线时间跨度越大，信号越滞后，适合中长线\n· 分钟线适合日内/短线策略，但数据量需求更大\n· 训练与回测/实盘应使用同一周期，否则信号会错位",
  rangeMode: "训练数据范围模式：\n· 按数量：取每个标的最新的 N 根K线（快速、数据量可控）\n· 按时间区间：按起止日期取数，适合对齐多标的到同一时间窗\n· 按时间区间时不受 barCount 限制，改由起止日期决定",
  barCount: "每个标的取用的历史K线数量（50-5000）。\n· 数据越多样本越充足，建议 1000 以上\n· 过多会拖慢训练、占用内存；过少则样本不足，模型学不到有效规律",
  startDate: "训练数据起始日期（仅“按时间区间”模式生效）。\n· 区间越长数据越多，训练越慢\n· 多标的会按该日期对齐取数\n· 请勿晚于结束日期",
  endDate: "训练数据结束日期（仅“按时间区间”模式生效）。\n· 通常设为最近一个交易日，让模型学习最新行情\n· 请勿早于起始日期",
  architecture: "模型结构：\n· LSTM：擅长捕捉时序规律，对K线序列建模效果好，训练中等，推荐首选\n· Transformer：注意力机制表达力强，但需要更多数据，训练慢\n· MLP：轻量、训练最快，适合数据量小或快速验证\n· GBDT：sklearn 树集成（HistGradientBoosting），CPU 友好、小样本稳健，推荐做表格基线",
  epochs: "训练迭代轮数（1-500）。\n· 越大拟合越充分，过大容易过拟合（训练集好、样本外差）\n· 训练过程有早停机制，验证集不再提升会自动停止\n· 常用 50-150，数据量小时建议少一些",
  learningRate: "学习率（0.00001-1）。\n· 过大会震荡不收敛，过小收敛缓慢\n· 常用 0.001；训练不收敛可试着调小（如 0.0001）",
  hiddenSize: "隐藏层神经元数量（8-512）。\n· 越大表达力越强，也越容易过拟合\n· 数据量大可适当增大，数据量小建议保持较小值",
  batchSize: "每批样本数（4-256）。\n· 越大训练越稳定，但占用内存越多\n· 显存/内存不足时可调小（如 16）",
  labelType: "标签生成方式：\n· classification：预测未来涨/跌/平三分类，默认，直观易用\n· regression：回归预测未来收益率，输出连续值\n· triple_barrier：三重障碍法（止损/止盈/时间），更贴近实际交易但更复杂",
};

function fieldLabel(text, tip) {
  return '<label class="form-label">' + text + '<span class="form-hint" tabindex="0" data-tip="' + escapeHtml(tip) + '">?</span></label>';
}

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"'\/]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;", "/": "&#x2F;" }[c];
  });
}

function fmtDate(s) {
  if (!s) return "-";
  var d = s.replace("T", " ");
  return d.length > 16 ? d.slice(0, 16) : d;
}

function render(root, vm) {
  var searchCtrl = null;
  function paint(state) {
    var html = "";

    // ---- Error toast ----
    if (state.error) {
      html += '<div class="status error" style="margin-bottom:16px;display:flex;align-items:center;justify-content:space-between">';
      html += '<span>' + escapeHtml(state.error) + '</span>';
      html += '<button class="btn btn-sm" data-dismiss-error style="color:var(--text-3)">&times;</button>';
      html += '</div>';
    }

    // ---- Service status ----
    html += '<div class="card">';
    html += '<div class="card-title">\u6a21\u578b\u670d\u52a1</div>';
    html += '<div style="display:flex;align-items:center;gap:12px">';
    html += '<span class="status-dot ' + (state.serviceReady ? "online" : "offline") + '"></span>';
    html += '<span style="font-size:14px">' + (state.serviceReady ? "\u8fd0\u884c\u4e2d" : "\u672a\u542f\u52a8") + '</span>';
    html += '<span style="font-size:13px;color:var(--text-3)">\u7aef\u53e3 ' + state.servicePort + '</span>';
    html += '<button class="btn btn-sm btn-secondary" id="modelRefreshBtn" style="margin-left:auto">\u5237\u65b0</button>';
    html += '</div>';
    html += '</div>';

    if (!state.serviceReady) {
      // ---- Not ready ----
      html += '<div class="card">';
      html += '<div class="quote-empty-state">';
      html += '<p style="color:var(--text-3);font-size:14px">\u6a21\u578b\u670d\u52a1\u672a\u542f\u52a8</p>';
      html += '<p style="color:var(--text-3);font-size:13px;margin-top:8px">\u8bf7\u786e\u4fdd Python \u73af\u5883\u5df2\u6b63\u786e\u914d\u7f6e\uff0c\u5e76\u5b89\u88c5\u4e86\u5fc5\u8981\u4f9d\u8d56</p>';
      html += '</div>';
      html += '</div>';
    } else {

      // ---- Model list ----
      html += '<div class="card">';
      html += '<div class="card-title">\u6a21\u578b\u7ba1\u7406 (' + state.models.length + ')</div>';
      if (state.modelsLoading) {
        html += '<p style="color:var(--text-3)">\u52a0\u8f7d\u4e2d...</p>';
      } else if (state.models.length === 0) {
        html += '<p style="color:var(--text-3)">\u6682\u65e0\u6a21\u578b\uff0c\u8bf7\u5148\u8bad\u7ec3\u4e00\u4e2a\u6a21\u578b</p>';
      } else {
        html += '<div class="table-wrap"><table class="data-table">';
        html += '<thead><tr><th>\u540d\u79f0</th><th>model_id</th><th>\u67b6\u6784</th><th>\u4f53\u68c0</th><th>\u521b\u5efa\u65f6\u95f4</th><th>\u6807\u7684</th><th>\u72b6\u6001</th><th>\u64cd\u4f5c</th></tr></thead>';
        html += '<tbody>';
        for (var i = 0; i < state.models.length; i++) {
          var m = state.models[i];
          var quality = m.metrics ? (m.metrics.quality_score != null ? m.metrics.quality_score : null) : null;
          var grade = m.metrics && m.metrics.degraded ? "red" : (quality == null ? null : (quality >= 60 ? "green" : (quality >= 35 ? "yellow" : "red")));
          html += '<tr>';
          html += '<td>' + escapeHtml(m.name) + '</td>';
          html += '<td style="font-size:11px;color:var(--text-3);font-family:monospace">' + escapeHtml(m.model_id) + '</td>';
          html += '<td><span class="badge badge-active">' + escapeHtml(m.arch || "-") + '</span></td>';
          // 质量徽章：红黄绿三档 + 分数；旧模型无体检数据显示 -
          html += '<td>';
          if (grade) {
            var gLabel = { green: "\u2713 \u826f\u597d", yellow: "~ \u4e00\u822c", red: "\u2717 \u4e0d\u5408\u683c" }[grade];
            var gClass = { green: "badge-active", yellow: "badge-draft", red: "badge-inactive" }[grade];
            html += '<span class="badge ' + gClass + '" title="\u4f53\u68c0\u8bc4\u5206 ' + quality + '/100\uff08\u65b9\u5411F1+\u591a\u6837\u6027+\u7f6e\u4fe1\u5ea6\uff09">' + gLabel + ' ' + quality + '</span>';
          } else {
            html += '<span style="color:var(--text-3)">-</span>';
          }
          html += '</td>';
          html += '<td>' + fmtDate(m.created_at) + '</td>';
          html += '<td style="font-size:12px">' + escapeHtml((m.symbols || []).join(", ") || "-") + '</td>';
          html += '<td><span class="badge ' + (m.status === "active" ? "badge-active" : "badge-draft") + '">' + escapeHtml(m.status || "-") + '</span></td>';
          var isActive = state.activeModelId === m.model_id;
          html += '<td style="white-space:nowrap">';
          html += '<button class="btn btn-sm ' + (isActive ? "btn-active" : "") + '" data-activate-model="' + escapeHtml(m.model_id) + '"' + (isActive ? ' disabled' : '') + '>' + (isActive ? '\u2713 \u5df2\u6fc0\u6d3b' : '\u6fc0\u6d3b') + '</button> ';
          html += '<button class="btn btn-sm btn-secondary" data-edit-model="' + escapeHtml(m.model_id) + '" data-edit-name="' + escapeHtml(m.name) + '">\u7f16\u8f91</button> ';
          html += '<button class="btn btn-sm" style="color:var(--red)" data-delete-model="' + escapeHtml(m.model_id) + '">\u5220\u9664</button>';
          html += '</td>';
          html += '</tr>';
        }
        html += '</tbody></table></div>';
      }
      html += '</div>';

      // ---- Training form ----
      html += '<div class="card">';
      html += '<div class="card-title">\u8bad\u7ec3\u65b0\u6a21\u578b</div>';
      html += '<div class="train-form-grid">';

      // Symbol
      html += '<div class="form-group">' + fieldLabel('\u6807\u7684\u4ee3\u7801', FIELD_TIPS.symbol);
      html += '<input type="text" class="form-input" id="trainSymbol" value="' + escapeHtml(state.trainConfig.symbol) + '" placeholder="\u8f93\u5165\u4ee3\u7801/\u540d\u79f0/\u62fc\u97f3\u641c\u7d22\uff0c\u5982 \u8305\u53f0 / sh600519" autocomplete="off" spellcheck="false" /></div>';

      // Period
      html += '<div class="form-group">' + fieldLabel('K\u7ebf\u5468\u671f', FIELD_TIPS.period);
      html += '<select class="form-select" id="trainPeriod">';
      var periods = ["1d", "1m", "5m", "15m", "30m", "60m"];
      for (var pi = 0; pi < periods.length; pi++) {
        html += '<option value="' + periods[pi] + '"' + (state.trainConfig.period === periods[pi] ? " selected" : "") + '>' + periods[pi] + '</option>';
      }
      html += '</select></div>';

      // Range mode (数量 / 时间区间)
      html += '<div class="form-group">' + fieldLabel('\u8bad\u7ec3\u8303\u56f4', FIELD_TIPS.rangeMode);
      html += '<select class="form-select" id="trainRangeMode">';
      var rangeModes = ["count", "date"];
      for (var rmi = 0; rmi < rangeModes.length; rmi++) {
        html += '<option value="' + rangeModes[rmi] + '"' + (state.trainConfig.rangeMode === rangeModes[rmi] ? " selected" : "") + '>' + (rangeModes[rmi] === "count" ? '\u6309\u6570\u91cf' : '\u6309\u65f6\u95f4\u533a\u95f4') + '</option>';
      }
      html += '</select></div>';

      // Bar count (仅按数量模式显示)
      var showCount = state.trainConfig.rangeMode !== "date";
      html += '<div class="form-group" id="trainBarCountGroup"' + (showCount ? "" : ' style="display:none"') + '>' + fieldLabel('\u6570\u636e\u91cf', FIELD_TIPS.barCount);
      html += '<input type="number" class="form-input" id="trainBarCount" value="' + state.trainConfig.barCount + '" min="50" max="5000" /></div>';

      // Date range (仅按时间区间模式显示)
      var showDate = state.trainConfig.rangeMode === "date";
      html += '<div class="form-group" id="trainStartGroup"' + (showDate ? "" : ' style="display:none"') + '>' + fieldLabel('\u5f00\u59cb\u65e5\u671f', FIELD_TIPS.startDate);
      html += '<input type="date" class="form-input" id="trainStart" value="' + escapeHtml(state.trainConfig.startDate || "") + '" /></div>';
      html += '<div class="form-group" id="trainEndGroup"' + (showDate ? "" : ' style="display:none"') + '>' + fieldLabel('\u7ed3\u675f\u65e5\u671f', FIELD_TIPS.endDate);
      html += '<input type="date" class="form-input" id="trainEnd" value="' + escapeHtml(state.trainConfig.endDate || "") + '" /></div>';

      // Architecture
      html += '<div class="form-group">' + fieldLabel('\u6a21\u578b\u67b6\u6784', FIELD_TIPS.architecture);
      html += '<select class="form-select" id="trainArch">';
      var archs = ["lstm", "transformer", "mlp", "gbdt"];
      for (var ai = 0; ai < archs.length; ai++) {
        html += '<option value="' + archs[ai] + '"' + (state.trainConfig.architecture === archs[ai] ? " selected" : "") + '>' + archs[ai].toUpperCase() + '</option>';
      }
      html += '</select></div>';

      // Epochs
      html += '<div class="form-group">' + fieldLabel('Epochs', FIELD_TIPS.epochs);
      html += '<input type="number" class="form-input" id="trainEpochs" value="' + state.trainConfig.epochs + '" min="1" max="500" /></div>';

      // Learning rate
      html += '<div class="form-group">' + fieldLabel('\u5b66\u4e60\u7387', FIELD_TIPS.learningRate);
      html += '<input type="number" class="form-input" id="trainLR" value="' + state.trainConfig.learningRate + '" step="0.0001" min="0.00001" max="1" /></div>';

      // Hidden size
      html += '<div class="form-group">' + fieldLabel('\u9690\u85cf\u5c42\u5927\u5c0f', FIELD_TIPS.hiddenSize);
      html += '<input type="number" class="form-input" id="trainHidden" value="' + state.trainConfig.hiddenSize + '" min="8" max="512" /></div>';

      // Batch size
      html += '<div class="form-group">' + fieldLabel('\u6279\u6b21\u5927\u5c0f', FIELD_TIPS.batchSize);
      html += '<input type="number" class="form-input" id="trainBatch" value="' + state.trainConfig.batchSize + '" min="4" max="256" /></div>';

      // Label type
      html += '<div class="form-group">' + fieldLabel('\u6807\u7b7e\u7c7b\u578b', FIELD_TIPS.labelType);
      html += '<select class="form-select" id="trainLabel">';
      var labels = ["classification", "regression", "triple_barrier"];
      for (var li = 0; li < labels.length; li++) {
        html += '<option value="' + labels[li] + '"' + (state.trainConfig.labelType === labels[li] ? " selected" : "") + '>' + labels[li] + '</option>';
      }
      html += '</select></div>';

      // D3.4 walk-forward 滚动样本外验证
      html += '<div class="form-group" style="grid-column:1 / -1;display:flex;align-items:center;gap:10px;flex-wrap:wrap">';
      html += '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;color:var(--text-2)">';
      html += '<input type="checkbox" id="trainWalkForward"' + (state.trainConfig.walkForward ? ' checked' : '') + '/> walk-forward 稳健性验证';
      html += '</label>';
      html += '<span style="font-size:12px;color:var(--text-3)">段数</span>';
      html += '<input type="number" id="trainWalkSegments" value="' + state.trainConfig.walkForwardSegments + '" min="2" max="6" style="width:60px" />';
      html += '<span style="font-size:11px;color:var(--text-3)">按时间分段滚动训练+样本外评估，检验过拟合</span>';
      html += '</div>';

      html += '</div>'; // train-form-grid

      // Train button
      html += '<button class="btn btn-primary" id="trainStartBtn"' + (state.training.active ? " disabled" : "") + ' style="margin-top:16px">';
      html += state.training.active ? '\u8bad\u7ec3\u4e2d...' : '\u5f00\u59cb\u8bad\u7ec3';
      html += '</button>';

      // Training status
      if (state.training.active || state.training.status) {
        var ts = state.training.status || {};
        html += '<div style="margin-top:20px">';
        html += '<div class="progress-bar-wrap">';
        html += '<div class="progress-bar-fill" style="width:' + state.training.progress + '%"></div>';
        html += '<span class="progress-bar-text">' + state.training.progress + '%</span>';
        html += '</div>';
        if (ts.status === "error") {
          html += '<p style="color:var(--red);margin-top:8px;font-size:13px">\u9519\u8bef: ' + escapeHtml(ts.error || "\u672a\u77e5\u9519\u8bef") + '</p>';
        } else if (ts.status === "done") {
          html += '<p style="color:var(--green);margin-top:8px;font-size:13px">\u8bad\u7ec3\u5b8c\u6210</p>';
          // 体检报告（M4）：评分/等级/混淆矩阵/标签分布/数据问题
          var tr = ts.result || {};
          var hr = tr.health_report;
          var mm = tr.metrics || {};
          if (hr) {
            var gradeColor = hr.grade === "green" ? "var(--green)" : (hr.grade === "yellow" ? "#e6a700" : "var(--red)");
            var gradeText = hr.grade === "green" ? "\u826f\u597d" : (hr.grade === "yellow" ? "\u4e00\u822c" : "\u4e0d\u5408\u683c");
            html += '<div style="margin-top:12px;padding:12px;border:1px solid var(--border);border-radius:8px">';
            html += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">';
            html += '<span style="font-size:13px;font-weight:600">\u6a21\u578b\u4f53\u68c0\u62a5\u544a</span>';
            html += '<span class="badge" style="color:' + gradeColor + ';border:1px solid ' + gradeColor + '">' + gradeText + ' ' + hr.score + '/100</span>';
            html += '<span style="font-size:12px;color:var(--text-3)">\u65b9\u5411F1: ' + (hr.direction_f1 != null ? hr.direction_f1 : "-") + ' \u00b7 \u9a8c\u8bc1\u6807\u672c: ' + (hr.n_val || 0) + '</span>';
            html += '</div>';
            // 混淆矩阵（3x3 简表）
            if (hr.confusion_matrix) {
              var cm = hr.confusion_matrix;
              html += '<table class="data-table" style="font-size:11px;max-width:320px">';
              html += '<thead><tr><th>\\ \u9884\u6d4b</th><th>\u5356\u51fa</th><th>\u6301\u6709</th><th>\u4e70\u5165</th></tr></thead><tbody>';
              var rowNames = ["\u5b9e\u9645\u5356\u51fa", "\u5b9e\u9645\u6301\u6709", "\u5b9e\u9645\u4e70\u5165"];
              for (var ri = 0; ri < 3; ri++) {
                html += '<tr><td style="color:var(--text-3)">' + rowNames[ri] + '</td><td class="num">' + cm[ri][0] + '</td><td class="num">' + cm[ri][1] + '</td><td class="num">' + cm[ri][2] + '</td></tr>';
              }
              html += '</tbody></table>';
            }
            // 标签分布
            if (tr.label_dist) {
              var ld = tr.label_dist;
              var total = Object.keys(ld).reduce(function (s, k) { return s + ld[k]; }, 0) || 1;
              var ldNames = { "0": "\u5356\u51fa", "1": "\u6301\u6709", "2": "\u4e70\u5165" };
              html += '<p style="font-size:12px;color:var(--text-3);margin-top:8px">\u6807\u7b7e\u5206\u5e03\uff1a' +
                Object.keys(ld).map(function (k) { return (ldNames[k] || k) + ' ' + Math.round(ld[k] / total * 100) + '%'; }).join(' \u00b7 ') + '</p>';
            }
            // 数据质量问题
            if (tr.data_problems && tr.data_problems.length) {
              html += '<p style="font-size:12px;color:var(--red);margin-top:4px">\u6570\u636e\u95ee\u9898: ' + escapeHtml(tr.data_problems.slice(0, 3).join('\uff1b')) + '</p>';
            }
            if (hr.degraded) {
              html += '<p style="font-size:12px;color:var(--red);margin-top:4px">\u8be5\u6a21\u578b\u5df2\u88ab\u6807\u8bb0\u4e3a degraded\uff08\u4f53\u68c0\u4e0d\u5408\u683c\uff09\uff0c\u6fc0\u6d3b\u65f6\u9700\u4e8c\u6b21\u786e\u8ba4\uff0c\u5efa\u8bae\u91cd\u65b0\u8bad\u7ec3</p>';
            }
            html += '</div>';
          }
          // D3.4 walk-forward 稳定性报告
          var wfr = tr.walk_forward;
          if (wfr) {
            var wfColor = wfr.verdict === "green" ? "var(--green)" : (wfr.verdict === "yellow" ? "#e6a700" : "var(--red)");
            html += '<div style="margin-top:12px;padding:12px;border:1px solid var(--border);border-radius:8px">';
            html += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">';
            html += '<span style="font-size:13px;font-weight:600">walk-forward \u7a33\u5b9a\u6027\u62a5\u544a</span>';
            html += '<span class="badge" style="color:' + wfColor + ';border:1px solid ' + wfColor + '">' + escapeHtml(wfr.stable || "") + '</span>';
            html += '</div>';
            html += '<p style="font-size:12px;color:var(--text-3)">' + escapeHtml(wfr.summary || "") + '</p>';
            if (wfr.folds && wfr.folds.length) {
              html += '<table class="data-table" style="font-size:11px;max-width:560px">';
              html += '<thead><tr><th>\u6298\u53e0</th><th>\u8bad\u7ec3\u533a\u95f4</th><th>\u6d4b\u8bd5\u533a\u95f4</th><th class="num">\u6837\u672c\u5916\u51c6\u786e\u7387</th><th class="num">\u65b9\u5411F1</th><th class="num">\u6d4b\u8bd5\u6837\u672c</th></tr></thead><tbody>';
              for (var fi2 = 0; fi2 < wfr.folds.length; fi2++) {
                var f = wfr.folds[fi2];
                html += '<tr><td>' + f.fold + '</td><td style="font-size:10px">' + escapeHtml(f.trainRange || "") + '</td><td style="font-size:10px">' + escapeHtml(f.testRange || "") + '</td><td class="num">' + (f.accuracy != null ? (f.accuracy * 100).toFixed(1) + '%' : '-') + '</td><td class="num">' + (f.directionF1 != null ? f.directionF1.toFixed(2) : '-') + '</td><td class="num">' + (f.nTest || 0) + '</td></tr>';
              }
              html += '</tbody></table>';
            }
            html += '</div>';
          }
        } else if (ts.status === "running") {
          var stageLabel = ts.stage === "fetch" ? "\u6570\u636e\u52a0\u8f7d..." : (ts.stage === "build" ? "\u6784\u5efa\u6570\u636e\u96c6..." : (ts.stage === "save" ? "\u4fdd\u5b58\u6a21\u578b..." : "\u8bad\u7ec3\u4e2d..."));
          html += '<p style="color:var(--text-3);margin-top:8px;font-size:13px">' + stageLabel + ' ' + state.training.progress + '%</p>';
        }
        html += '</div>';
      }

      html += '</div>'; // card

    } // end if serviceReady

    // ---- Edit modal ----
    html += '<div class="modal-overlay" id="modelEditModal" style="display:none;z-index:1001">';
    html += '<div class="modal" style="max-width:400px">';
    html += '<h2 class="modal-title">\u7f16\u8f91\u6a21\u578b</h2>';
    html += '<div class="form-group"><label class="form-label">\u6a21\u578b\u540d\u79f0</label>';
    html += '<input type="text" class="form-input" id="modelEditName" /></div>';
    html += '<div class="modal-actions">';
    html += '<button class="btn btn-secondary" id="modelEditCancel">\u53d6\u6d88</button>';
    html += '<button class="btn btn-primary" id="modelEditSave">\u4fdd\u5b58</button>';
    html += '</div></div></div>';

    root.innerHTML = html;

    // ---- Event bindings ----
    // 标的代码：复用通用股票搜索（代码/名称/拼音，多选标签式），选中的标的以逗号追加
    var symEl = root.querySelector("#trainSymbol");
    if (symEl) {
      symEl.value = state.trainConfig.symbol || "";
      if (searchCtrl) searchCtrl.destroy();
      searchCtrl = StockSearch.mount(symEl, vm.facade, function () {
        vm.setTrainField("symbol", symEl.value);
      }, function (val) {
        vm.setTrainField("symbol", val);
      }, { multi: true });
    }

    var refreshBtn = root.querySelector("#modelRefreshBtn");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { vm.loadAll(); });

    var dismissBtn = root.querySelector("[data-dismiss-error]");
    if (dismissBtn) dismissBtn.addEventListener("click", function () { vm.dismissError(); });

    // Train form fields (symbol handled by StockSearch above)
    var fields = [
      ["#trainPeriod", "period"],
      ["#trainRangeMode", "rangeMode"],
      ["#trainBarCount", "barCount"],
      ["#trainStart", "startDate"],
      ["#trainEnd", "endDate"],
      ["#trainArch", "architecture"],
      ["#trainEpochs", "epochs"],
      ["#trainLR", "learningRate"],
      ["#trainHidden", "hiddenSize"],
      ["#trainBatch", "batchSize"],
      ["#trainLabel", "labelType"],
    ];
    for (var fi = 0; fi < fields.length; fi++) {
      (function (sel, key) {
        var el = root.querySelector(sel);
        if (el) el.addEventListener("input", function () { vm.setTrainField(key, el.value); });
      })(fields[fi][0], fields[fi][1]);
    }
    // D3.4 walk-forward 控件
    var wfEl = root.querySelector("#trainWalkForward");
    if (wfEl) wfEl.addEventListener("change", function () { vm.setTrainField("walkForward", wfEl.checked); });
    var wfSegEl = root.querySelector("#trainWalkSegments");
    if (wfSegEl) wfSegEl.addEventListener("input", function () { vm.setTrainField("walkForwardSegments", wfSegEl.value); });

    // Range mode 切换：按数量 / 按时间区间，联动显示对应字段
    var rangeModeEl = root.querySelector("#trainRangeMode");
    if (rangeModeEl) {
      rangeModeEl.addEventListener("change", function () {
        var isDate = rangeModeEl.value === "date";
        var countGroup = root.querySelector("#trainBarCountGroup");
        var startGroup = root.querySelector("#trainStartGroup");
        var endGroup = root.querySelector("#trainEndGroup");
        if (countGroup) countGroup.style.display = isDate ? "none" : "";
        if (startGroup) startGroup.style.display = isDate ? "" : "none";
        if (endGroup) endGroup.style.display = isDate ? "" : "none";
        vm.setTrainField("rangeMode", rangeModeEl.value);
      });
    }

    var trainBtn = root.querySelector("#trainStartBtn");
    if (trainBtn) trainBtn.addEventListener("click", function () { vm.startTraining(); });

    var delBtns = root.querySelectorAll("[data-delete-model]");
    for (var di = 0; di < delBtns.length; di++) {
      (function (btn) {
        btn.addEventListener("click", function () { vm.deleteModel(btn.getAttribute("data-delete-model")); });
      })(delBtns[di]);
    }
    // Activate buttons
    var actBtns = root.querySelectorAll("[data-activate-model]");
    for (var ai = 0; ai < actBtns.length; ai++) {
      (function (btn) {
        btn.addEventListener("click", function () { vm.activateModel(btn.getAttribute("data-activate-model")); });
      })(actBtns[ai]);
    }
    // Edit buttons
    var editBtns = root.querySelectorAll("[data-edit-model]");
    for (var ei2 = 0; ei2 < editBtns.length; ei2++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          var id = btn.getAttribute("data-edit-model");
          var name = btn.getAttribute("data-edit-name");
          var modal = root.querySelector("#modelEditModal");
          var nameInput = root.querySelector("#modelEditName");
          if (modal && nameInput) {
            nameInput.value = name;
            modal.style.display = "flex";
            var saveBtn = root.querySelector("#modelEditSave");
            if (saveBtn) saveBtn.onclick = function () {
              vm.updateModel(id, { name: nameInput.value });
              modal.style.display = "none";
            };
            var cancelBtn = root.querySelector("#modelEditCancel");
            if (cancelBtn) cancelBtn.onclick = function () {
              modal.style.display = "none";
            };
          }
        });
      })(editBtns[ei2]);
    }
  }
  vm.subscribe(paint);
}
export { render };