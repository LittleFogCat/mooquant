function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"'/]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;","/":"&#x2F;" }[c])); }
  function render(root, vm) {
    function paint(state) {
      root.innerHTML = `
        <div class="card">
          <div class="card-title">数据源设置</div>
          ${state.error ? `<div class="status error" style="margin-bottom:16px">${escapeHtml(state.error)}</div>` : ""}
          ${state.saved ? `
        <div class="modal-overlay" id="restartModal" style="z-index:1001">
          <div class="modal" style="max-width:400px">
            <h2 class="modal-title">重启生效</h2>
            <p style="color:var(--text-2);font-size:14px;line-height:1.6;margin-bottom:20px">设置已保存，切换数据源需要重启应用才能生效。是否立即重启？</p>
            <div class="modal-actions">
              <button class="btn btn-secondary" id="restartLater">稍后</button>
              <button class="btn btn-primary" id="restartNow">立即重启</button>
            </div>
          </div>
        </div>
      ` : ""}
          <div class="form-group">
            <label class="form-label">行情数据源</label>
            <div class="radio-group">
              <label class="radio-item ${state.dataSource === "auto" ? "checked" : ""}"><input type="radio" name="ds" value="auto" ${state.dataSource === "auto" ? "checked" : ""} /><span class="radio-label">Auto（优先 QMT，失败回落 mock）</span></label>
              <label class="radio-item ${state.dataSource === "qmt" ? "checked" : ""}"><input type="radio" name="ds" value="qmt" ${state.dataSource === "qmt" ? "checked" : ""} /><span class="radio-label">QMT（真实行情）</span></label>
              <label class="radio-item ${state.dataSource === "mock" ? "checked" : ""}"><input type="radio" name="ds" value="mock" ${state.dataSource === "mock" ? "checked" : ""} /><span class="radio-label">Mock（本地模拟，无需 QMT）</span></label>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">交易数据源</label>
            <div class="radio-group">
              <label class="radio-item ${state.tradeSource === "mock" ? "checked" : ""}"><input type="radio" name="ts" value="mock" ${state.tradeSource === "mock" ? "checked" : ""} /><span class="radio-label">Mock（模拟交易）</span></label>
              <label class="radio-item ${state.tradeSource === "qmt" ? "checked" : ""}"><input type="radio" name="ts" value="qmt" ${state.tradeSource === "qmt" ? "checked" : ""} /><span class="radio-label">QMT（实盘交易）</span></label>
            </div>
          </div>
          <button class="btn btn-primary" id="settingsSave" ${state.loading ? "disabled" : ""}>${state.loading ? "保存中..." : "保存设置"}</button>
        </div>
        <div class="card">
          <div class="card-title">说明</div>
          <ul style="padding-left:20px;line-height:2;color:var(--text-2);font-size:14px">
            <li>切换数据源后需要重启应生效</li>
            <li>QMT 模式需要启动 miniQMT 客户端</li>
            <li>Mock 模式适合开发调试，无需任何外部依赖</li>
            <li>Auto 模式会自动尝试 QMT，失败后静默回落到 mock</li>
          </ul>
        </div>
      `;
      root.querySelectorAll('input[name="ds"]').forEach(el => { el.addEventListener("change", () => {
        vm.setField("dataSource", el.value);
        root.querySelectorAll('label.radio-item').forEach(l => { const inp = l.querySelector("input"); l.classList.toggle("checked", inp && inp.checked); });
      }); });
      root.querySelectorAll('input[name="ts"]').forEach(el => { el.addEventListener("change", () => {
        vm.setField("tradeSource", el.value);
        root.querySelectorAll('label.radio-item').forEach(l => { const inp = l.querySelector("input"); l.classList.toggle("checked", inp && inp.checked); });
      }); });
      const saveBtn = root.querySelector("#settingsSave");
      if (saveBtn) saveBtn.addEventListener("click", () => vm.save());
      const restartNow = root.querySelector("#restartNow");
      if (restartNow) restartNow.addEventListener("click", () => { if (vm.facade.app && vm.facade.app.restart) vm.facade.app.restart(); });
      const restartLater = root.querySelector("#restartLater");
      if (restartLater) restartLater.addEventListener("click", () => vm.dismissSaved());
    }
    vm.subscribe(paint);
  }
  export { render };