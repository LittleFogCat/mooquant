/**
 * mookquant * Model Management View
 *
 * Renders model list, training form, and strategy list.
 * Communicates with model_server.py via ModelViewModel.
 */
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
        html += '<thead><tr><th>\u540d\u79f0</th><th>\u67b6\u6784</th><th>\u521b\u5efa\u65f6\u95f4</th><th>\u6807\u7684</th><th>\u72b6\u6001</th><th>\u64cd\u4f5c</th></tr></thead>';
        html += '<tbody>';
        for (var i = 0; i < state.models.length; i++) {
          var m = state.models[i];
          html += '<tr>';
          html += '<td>' + escapeHtml(m.name) + '</td>';
          html += '<td><span class="badge badge-active">' + escapeHtml(m.arch || "-") + '</span></td>';
          html += '<td>' + fmtDate(m.created_at) + '</td>';
          html += '<td style="font-size:12px">' + escapeHtml((m.symbols || []).join(", ") || "-") + '</td>';
          html += '<td><span class="badge ' + (m.status === "active" ? "badge-active" : "badge-draft") + '">' + escapeHtml(m.status || "-") + '</span></td>';
          html += '<td><button class="btn btn-sm" style="color:var(--red)" data-delete-model="' + escapeHtml(m.model_id) + '">\u5220\u9664</button></td>';
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
      html += '<div class="form-group"><label class="form-label">\u6807\u7684\u4ee3\u7801</label>';
      html += '<input type="text" class="form-input" id="trainSymbol" value="' + escapeHtml(state.trainConfig.symbol) + '" placeholder="\u5982 sh600519" /></div>';

      // Period
      html += '<div class="form-group"><label class="form-label">K\u7ebf\u5468\u671f</label>';
      html += '<select class="form-select" id="trainPeriod">';
      var periods = ["1d", "1m", "5m", "15m", "30m", "60m"];
      for (var pi = 0; pi < periods.length; pi++) {
        html += '<option value="' + periods[pi] + '"' + (state.trainConfig.period === periods[pi] ? " selected" : "") + '>' + periods[pi] + '</option>';
      }
      html += '</select></div>';

      // Bar count
      html += '<div class="form-group"><label class="form-label">\u6570\u636e\u91cf</label>';
      html += '<input type="number" class="form-input" id="trainBarCount" value="' + state.trainConfig.barCount + '" min="50" max="5000" /></div>';

      // Architecture
      html += '<div class="form-group"><label class="form-label">\u6a21\u578b\u67b6\u6784</label>';
      html += '<select class="form-select" id="trainArch">';
      var archs = ["lstm", "transformer", "mlp"];
      for (var ai = 0; ai < archs.length; ai++) {
        html += '<option value="' + archs[ai] + '"' + (state.trainConfig.architecture === archs[ai] ? " selected" : "") + '>' + archs[ai].toUpperCase() + '</option>';
      }
      html += '</select></div>';

      // Epochs
      html += '<div class="form-group"><label class="form-label">Epochs</label>';
      html += '<input type="number" class="form-input" id="trainEpochs" value="' + state.trainConfig.epochs + '" min="1" max="500" /></div>';

      // Learning rate
      html += '<div class="form-group"><label class="form-label">\u5b66\u4e60\u7387</label>';
      html += '<input type="number" class="form-input" id="trainLR" value="' + state.trainConfig.learningRate + '" step="0.0001" min="0.00001" max="1" /></div>';

      // Hidden size
      html += '<div class="form-group"><label class="form-label">\u9690\u85cf\u5c42\u5927\u5c0f</label>';
      html += '<input type="number" class="form-input" id="trainHidden" value="' + state.trainConfig.hiddenSize + '" min="8" max="512" /></div>';

      // Batch size
      html += '<div class="form-group"><label class="form-label">\u6279\u6b21\u5927\u5c0f</label>';
      html += '<input type="number" class="form-input" id="trainBatch" value="' + state.trainConfig.batchSize + '" min="4" max="256" /></div>';

      // Label type
      html += '<div class="form-group"><label class="form-label">\u6807\u7b7e\u7c7b\u578b</label>';
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
        } else if (ts.status === "running") {
          html += '<p style="color:var(--text-3);margin-top:8px;font-size:13px">\u8bad\u7ec3\u4e2d... ' + state.training.progress + '%</p>';
        }
        html += '</div>';
      }

      html += '</div>'; // card

      // ---- Strategy list ----
      html += '<div class="card">';
      html += '<div class="card-title">\u7b56\u7565\u5217\u8868 (' + state.strategies.length + ')</div>';
      if (state.strategiesLoading) {
        html += '<p style="color:var(--text-3)">\u52a0\u8f7d\u4e2d...</p>';
      } else if (state.strategies.length === 0) {
        html += '<p style="color:var(--text-3)">\u6682\u65e0\u7b56\u7565</p>';
      } else {
        html += '<div class="strategy-list-grid">';
        for (var si = 0; si < state.strategies.length; si++) {
          var s = state.strategies[si];
          var sname = s.name || s.strategy_name || "-";
          var sdesc = s.description || s.desc || "";
          var stype = s.type || "";
          html += '<div class="strategy-item">';
          html += '<div style="font-weight:600;font-size:14px">' + escapeHtml(sname) + '</div>';
          if (sdesc) html += '<div style="font-size:12px;color:var(--text-3);margin-top:4px">' + escapeHtml(sdesc) + '</div>';
          if (stype) html += '<span class="badge badge-active" style="margin-top:6px;display:inline-block">' + escapeHtml(stype) + '</span>';
          html += '</div>';
        }
        html += '</div>';
      }
      html += '</div>';

    } // end if serviceReady

    root.innerHTML = html;

    // ---- Event bindings ----
    var refreshBtn = root.querySelector("#modelRefreshBtn");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { vm.loadAll(); });

    var dismissBtn = root.querySelector("[data-dismiss-error]");
    if (dismissBtn) dismissBtn.addEventListener("click", function () { vm.dismissError(); });

    // Train form fields
    var fields = [
      ["#trainSymbol", "symbol"],
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
  }
  vm.subscribe(paint);
}
export { render };