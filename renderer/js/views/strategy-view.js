/**
 * mookquant · Strategy View（策略管理视图）
 *
 * 列表 + 编辑弹窗 + 启动/停止 + 导出 QMT 脚本。
 * 策略类型从后端 strategyTypes 动态加载（替代硬编码），参数仍用 JSON 编辑。
 */
const TYPE_LABELS = {
    ma_cross: "双均线",
    momentum: "动量",
    mean_reversion: "均值回归",
    custom: "自定义",
  };

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"'\/]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "/": "&#x2F;",
    }[c]));
  }

  /** 从 state.strategyTypes 取展示名，fallback 到 TYPE_LABELS */
  function getTypeLabel(state, type) {
    const t = (state.strategyTypes || []).find((x) => x.name === type);
    return t ? t.display_name : (TYPE_LABELS[type] || type);
  }

  function renderList(state, vm) {
    if (state.loading && !state.list.length) {
      return '<div class="empty-state"><div class="empty-state-icon">⏳</div><div class="empty-state-text">加载中...</div></div>';
    }
    if (!state.list.length) {
      return '<div class="empty-state"><div class="empty-state-icon">📋</div><div class="empty-state-text">还没有策略，点击「新建策略」开始</div></div>';
    }
    const rows = state.list.map((s) => {
      const exec = state.executorStatus[s.id];
      const isRunning = exec && exec.status === "running";
      const statusBadge = isRunning
        ? '<span class="badge badge-running">运行中</span>'
        : exec && exec.status === "error"
        ? '<span class="badge badge-error">异常</span>'
        : '<span class="badge badge-' + escapeHtml(s.status) + '">' + escapeHtml(s.status) + "</span>";
      const runInfo = exec
        ? '<div style="font-size:11px;color:var(--text-tertiary);margin-top:2px">' +
          (exec.lastSignal ? exec.lastSignal.action + " (" + exec.lastSignal.reason + ")" : "无信号") +
          " · tick#" + (exec.tickCount || 0) +
          (exec.error ? " · ⚠ " + escapeHtml(exec.error) : "") +
          "</div>"
        : "";
      const runBtn = isRunning
        ? '<button class="btn btn-danger btn-sm" data-action="stop" data-id="' + escapeHtml(s.id) + '">停止</button>'
        : '<button class="btn btn-primary btn-sm" data-action="start" data-id="' + escapeHtml(s.id) + '">启动</button>';
      return '<tr>' +
        '<td><strong>' + escapeHtml(s.name) + '</strong>' + runInfo + '</td>' +
        '<td>' + escapeHtml(getTypeLabel(state, s.type)) + '</td>' +
        '<td>' + statusBadge + '</td>' +
        '<td>' + escapeHtml(new Date(s.updatedAt).toLocaleDateString("zh-CN")) + '</td>' +
        '<td>' +
          runBtn + " " +
          '<button class="btn btn-secondary btn-sm" data-action="edit" data-id="' + escapeHtml(s.id) + '">编辑</button> ' +
          '<button class="btn btn-secondary btn-sm" data-action="export" data-id="' + escapeHtml(s.id) + '">导出</button> ' +
          '<button class="btn btn-danger btn-sm" data-action="delete" data-id="' + escapeHtml(s.id) + '">删除</button>' +
        '</td>' +
      '</tr>';
    }).join("");
    return '<table class="data-table">' +
      '<thead><tr><th>名称</th><th>类型</th><th>状态</th><th>更新日期</th><th>操作</th></tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>';
  }

  function renderModal(state) {
    if (!state.editing) return "";
    const e = state.editing;
    const typeOptions = (state.strategyTypes && state.strategyTypes.length)
      ? state.strategyTypes.map((t) =>
          '<option value="' + escapeHtml(t.name) + '" ' + (e.type === t.name ? "selected" : "") + '>' + escapeHtml(t.display_name) + '</option>'
        ).join("")
      : Object.entries(TYPE_LABELS).map(([v, l]) =>
          '<option value="' + v + '" ' + (e.type === v ? "selected" : "") + '>' + l + '</option>'
        ).join("");
    return '<div class="modal-overlay" id="strategyModal">' +
      '<div class="modal">' +
        '<h2 class="modal-title">' + (e.id ? "编辑策略" : "新建策略") + '</h2>' +
        (state.error ? '<div class="status error" style="margin-bottom:16px">' + escapeHtml(state.error) + '</div>' : '') +
        '<div class="form-group"><label class="form-label">策略名称</label>' +
          '<input class="input-field" id="st_name" value="' + escapeHtml(e.name) + '" placeholder="如：双均线策略" /></div>' +
        '<div class="form-row">' +
          '<div class="form-group"><label class="form-label">类型</label>' +
            '<select class="input-field" id="st_type">' + typeOptions + '</select></div>' +
          '<div class="form-group"><label class="form-label">状态</label>' +
            '<select class="input-field" id="st_status">' +
              '<option value="draft" ' + (e.status === "draft" ? "selected" : "") + '>草稿</option>' +
              '<option value="active" ' + (e.status === "active" ? "selected" : "") + '>启用</option>' +
              '<option value="archived" ' + (e.status === "archived" ? "selected" : "") + '>归档</option>' +
            '</select></div>' +
        '</div>' +
        '<div class="form-group"><label class="form-label">描述</label>' +
          '<textarea class="input-field" id="st_desc" placeholder="策略描述...">' + escapeHtml(e.description) + '</textarea></div>' +
        '<div class="form-group"><label class="form-label">策略参数 (JSON)</label>' +
          '<textarea class="input-field" id="st_params" style="font-family:monospace;font-size:13px;min-height:80px">' + escapeHtml(e.params) + '</textarea></div>' +
        '<div class="form-group"><label class="form-label">风控参数 (JSON)</label>' +
          '<textarea class="input-field" id="st_risk" style="font-family:monospace;font-size:13px;min-height:100px">' + escapeHtml(e.risk) + '</textarea></div>' +
        '<div style="font-size:11px;color:var(--text-tertiary);margin-top:-8px;margin-bottom:12px">' +
          'stopLoss: 止损比例(0.05=5%) · stopProfit: 止盈 · maxOrderAmount: 单笔最大金额 · maxDailyTrades: 日内次数 · maxPositionRatio: 持仓比例' +
        '</div>' +
        '<div class="modal-actions">' +
          '<button class="btn btn-secondary" data-action="cancel">取消</button>' +
          '<button class="btn btn-primary" data-action="save">保存</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  /** 导出 QMT 脚本弹窗 */
  function renderExportModal(state) {
    if (!state.exportedScript) return "";
    return '<div class="modal-overlay" id="exportModal">' +
      '<div class="modal" style="max-width:760px">' +
        '<h2 class="modal-title">导出 QMT 脚本 · ' + escapeHtml(state.exportedName || "") + '</h2>' +
        '<div style="font-size:12px;color:var(--text-tertiary);margin-bottom:8px">' +
          '复制以下脚本，粘贴到 QMT 客户端「模型研究」新建模型即可运行。策略逻辑与框架内回测/实盘完全一致。' +
        '</div>' +
        '<textarea class="input-field" id="export_script" style="font-family:monospace;font-size:12px;min-height:380px;white-space:pre;resize:vertical" readonly>' + escapeHtml(state.exportedScript) + '</textarea>' +
        '<div class="modal-actions">' +
          '<button class="btn btn-secondary" data-action="copy-export">复制到剪贴板</button>' +
          '<button class="btn btn-primary" data-action="close-export">关闭</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  function collectForm() {
    return {
      name: document.getElementById("st_name").value,
      type: document.getElementById("st_type").value,
      status: document.getElementById("st_status").value,
      description: document.getElementById("st_desc").value,
      params: document.getElementById("st_params").value,
      risk: document.getElementById("st_risk").value,
    };
  }

  function render(root, vm) {
    function paint(state) {
      const header = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">' +
        '<h2 style="margin:0">策略管理</h2>' +
        '<button class="btn btn-primary" data-action="create">+ 新建策略</button>' +
      '</div>';
      root.innerHTML = header + renderList(state, vm) + renderModal(state) + renderExportModal(state);

      root.querySelectorAll("[data-action]").forEach((el) => {
        el.addEventListener("click", () => {
          const action = el.dataset.action;
          const id = el.dataset.id;
          if (action === "create") vm.startCreate();
          else if (action === "edit") {
            const s = state.list.find((x) => x.id === id);
            if (s) vm.startEdit(s);
          } else if (action === "delete") {
            if (confirm("确认删除策略「" + (state.list.find((x) => x.id === id) || {}).name + "」？")) vm.remove(id);
          } else if (action === "start") {
            const symbols = prompt("请输入执行标的（逗号分隔），如：600036, 000001");
            if (symbols && symbols.trim()) vm.startExecution(id, symbols.trim());
          }
          else if (action === "stop") vm.stopExecution(id);
          else if (action === "export") vm.exportStrategy(id);
          else if (action === "close-export") vm.closeExport();
          else if (action === "copy-export") {
            const ta = document.getElementById("export_script");
            if (ta) { ta.select(); document.execCommand("copy"); }
          }
          else if (action === "cancel") vm.cancelEdit();
          else if (action === "save") vm.save(collectForm());
        });
      });
    }

    vm.subscribe(paint);
  }

  export { render };
