/**
 * mookquant · Strategy View
 *
 * 策略管理界面：列表 + 编辑模态框。
 */
(function () {
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

  function renderList(state, vm) {
    if (state.loading && !state.list.length) {
      return '<div class="empty-state"><div class="empty-state-icon">⏳</div><div class="empty-state-text">加载中...</div></div>';
    }
    if (!state.list.length) {
      return '<div class="empty-state"><div class="empty-state-icon">📋</div><div class="empty-state-text">还没有策略，点击「新建策略」开始</div></div>';
    }
    const rows = state.list.map((s) => `
      <tr>
        <td><strong>${escapeHtml(s.name)}</strong></td>
        <td>${escapeHtml(TYPE_LABELS[s.type] || s.type)}</td>
        <td>${escapeHtml((s.symbols || []).join(", "))}</td>
        <td><span class="badge badge-${escapeHtml(s.status)}">${escapeHtml(s.status)}</span></td>
        <td>${escapeHtml(new Date(s.updatedAt).toLocaleDateString("zh-CN"))}</td>
        <td>
          <button class="btn btn-secondary btn-sm" data-action="edit" data-id="${escapeHtml(s.id)}">编辑</button>
          <button class="btn btn-danger btn-sm" data-action="delete" data-id="${escapeHtml(s.id)}">删除</button>
        </td>
      </tr>
    `).join("");
    return `
      <table class="data-table">
        <thead>
          <tr><th>名称</th><th>类型</th><th>标的</th><th>状态</th><th>更新日期</th><th>操作</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  function renderModal(state) {
    if (!state.editing) return "";
    const e = state.editing;
    return `
      <div class="modal-overlay" id="strategyModal">
        <div class="modal">
          <h2 class="modal-title">${e.id ? "编辑策略" : "新建策略"}</h2>
          ${state.error ? '<div class="status error" style="margin-bottom:16px">' + escapeHtml(state.error) + "</div>" : ""}
          <div class="form-group">
            <label class="form-label">策略名称</label>
            <input class="input-field" id="st_name" value="${escapeHtml(e.name)}" placeholder="如：双均线策略" />
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">类型</label>
              <select class="input-field" id="st_type">
                ${Object.entries(TYPE_LABELS).map(([v, l]) =>
                  `<option value="${v}" ${e.type === v ? "selected" : ""}>${l}</option>`
                ).join("")}
              </select>
            </div>
            <div class="form-group">
              <label class="form-label">状态</label>
              <select class="input-field" id="st_status">
                <option value="draft" ${e.status === "draft" ? "selected" : ""}>草稿</option>
                <option value="active" ${e.status === "active" ? "selected" : ""}>启用</option>
                <option value="archived" ${e.status === "archived" ? "selected" : ""}>归档</option>
              </select>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">标的代码（逗号分隔）</label>
            <input class="input-field" id="st_symbols" value="${escapeHtml(e.symbols)}" placeholder="如：sh600519, sz000001" />
          </div>
          <div class="form-group">
            <label class="form-label">描述</label>
            <textarea class="input-field" id="st_desc" placeholder="策略描述...">${escapeHtml(e.description)}</textarea>
          </div>
          <div class="form-group">
            <label class="form-label">策略参数 (JSON)</label>
            <textarea class="input-field" id="st_params" style="font-family:monospace;font-size:13px;min-height:120px">${escapeHtml(e.params)}</textarea>
          </div>
          <div class="modal-actions">
            <button class="btn btn-secondary" data-action="cancel">取消</button>
            <button class="btn btn-primary" data-action="save">保存</button>
          </div>
        </div>
      </div>
    `;
  }

  function collectForm() {
    return {
      id: document.getElementById("st_name") ? null : null, // id 从 vm.state.editing 取
      name: document.getElementById("st_name").value,
      type: document.getElementById("st_type").value,
      status: document.getElementById("st_status").value,
      symbols: document.getElementById("st_symbols").value,
      description: document.getElementById("st_desc").value,
      params: document.getElementById("st_params").value,
    };
  }

  function render(root, vm) {
    function paint(state) {
      root.innerHTML = `
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <span class="card-title" style="margin:0">策略列表</span>
            <button class="btn btn-primary btn-sm" data-action="create">+ 新建策略</button>
          </div>
          ${state.error ? '<div class="status error" style="margin-bottom:16px">' + escapeHtml(state.error) + "</div>" : ""}
          ${renderList(state, vm)}
        </div>
        ${renderModal(state)}
      `;

      // 事件绑定
      root.querySelectorAll("[data-action]").forEach((el) => {
        el.addEventListener("click", () => {
          const action = el.dataset.action;
          if (action === "create") vm.startCreate();
          else if (action === "cancel") vm.cancelEdit();
          else if (action === "edit") vm.startEdit(vm.state.list.find((s) => s.id === el.dataset.id));
          else if (action === "delete") {
            if (confirm("确认删除此策略？")) vm.remove(el.dataset.id);
          } else if (action === "save") {
            const form = collectForm();
            form.id = vm.state.editing.id;
            vm.save(form);
          }
        });
      });
    }

    vm.subscribe(paint);
  }

  window.StrategyView = { render };
})();
