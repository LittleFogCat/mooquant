/**
 * mookquant * Model Management View
 *
 * Renders model list, training form, and strategy list.
 * Communicates with model_server.py via ModelViewModel.
 */
import * as StockSearch from './stock-search.js';

// 训练表单字段说明（问号图标悬浮提示）
const FIELD_TIPS = {
  symbol: "训练标的，支持代码/名称搜索选择，多个用英文逗号分隔。多标的样本更丰富、模型泛化更好（单标的通常学不到有效信号）。",
  period: "K线周期：1d 日线、1m/5m/15m/30m/60m 分钟线。周期越大每根K线时间跨度越大，信号越滞后。",
  barCount: "每个标的取用的历史K线数量（50-5000）。数据越多样本越充足，建议 1000 以上；过多会拖慢训练。",
  architecture: "模型结构：LSTM 擅长捕捉时序规律，MLP 轻量训练快，Transformer 能力强但需要更多数据。",
  epochs: "训练迭代轮数（1-500）。越大拟合越充分，过大容易过拟合。",
  learningRate: "学习率（0.00001-1）。过大会震荡不收敛，过小收敛缓慢，常用 0.001。",
  hiddenSize: "隐藏层神经元数量（8-512）。越大表达力越强，也越容易过拟合。",
  batchSize: "每批样本数（4-256）。越大训练越稳定，但占用内存越多。",
  labelType: "标签生成方式：classification 预测涨跌分类（默认）、regression 回归预测收益率、triple_barrier 三重障碍法，更贴近实际交易。",
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

      // Bar count
      html += '<div class="form-group">' + fieldLabel('\u6570\u636e\u91cf', FIELD_TIPS.barCount);
      html += '<input type="number" class="form-input" id="trainBarCount" value="' + state.trainConfig.barCount + '" min="50" max="5000" /></div>';

      // Architecture
      html += '<div class="form-group">' + fieldLabel('\u6a21\u578b\u67b6\u6784', FIELD_TIPS.architecture);
      html += '<select class="form-select" id="trainArch">';
      var archs = ["lstm", "transformer", "mlp"];
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
      ["#trainBarCount", "barCount"],
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