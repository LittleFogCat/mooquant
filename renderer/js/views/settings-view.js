import { escapeHtml } from '../utils/format.js';

  function render(root, vm) {
    function paint(state) {
      root.innerHTML = `
        <div class="card">
          <div class="card-title">外观</div>
          <div class="form-group">
            <label class="form-label">主题模式</label>
            <div class="radio-group">
              <label class="radio-item ${state.theme === "light" ? "checked" : ""}"><input type="radio" name="theme" value="light" ${state.theme === "light" ? "checked" : ""} /><span class="radio-label">浅色模式</span></label>
              <label class="radio-item ${state.theme === "dark" ? "checked" : ""}"><input type="radio" name="theme" value="dark" ${state.theme === "dark" ? "checked" : ""} /><span class="radio-label">深色模式</span></label>
            </div>
          </div>
        </div>
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
              <label class="radio-item ${state.dataSource === "eastmoney" ? "checked" : ""}"><input type="radio" name="ds" value="eastmoney" ${state.dataSource === "eastmoney" ? "checked" : ""} /><span class="radio-label">东财公开行情（免 Key，无需客户端）</span></label>
              <label class="radio-item ${state.dataSource === "qmt" ? "checked" : ""}"><input type="radio" name="ds" value="qmt" ${state.dataSource === "qmt" ? "checked" : ""} /><span class="radio-label">QMT（真实行情）</span></label>
              <label class="radio-item ${state.dataSource === "mock" ? "checked" : ""}"><input type="radio" name="ds" value="mock" ${state.dataSource === "mock" ? "checked" : ""} /><span class="radio-label">Mock（本地模拟，无需 QMT）</span></label>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">交易数据源</label>
            <div class="radio-group">
              <label class="radio-item ${state.tradeSource === "mock" ? "checked" : ""}"><input type="radio" name="ts" value="mock" ${state.tradeSource === "mock" ? "checked" : ""} /><span class="radio-label">Mock（模拟交易）</span></label>
              <label class="radio-item ${state.tradeSource === "eastmoney" ? "checked" : ""}"><input type="radio" name="ts" value="eastmoney" ${state.tradeSource === "eastmoney" ? "checked" : ""} /><span class="radio-label">东财妙想模拟盘（.env 需 MX_APIKEY）</span></label>
              <label class="radio-item ${state.tradeSource === "qmt" ? "checked" : ""}"><input type="radio" name="ts" value="qmt" ${state.tradeSource === "qmt" ? "checked" : ""} /><span class="radio-label">QMT（实盘交易）</span></label>
            </div>
          </div>
          <button class="btn btn-primary" id="settingsSave" ${state.loading ? "disabled" : ""}>${state.loading ? "保存中..." : "保存设置"}</button>
        </div>
        <div class="card">
          <div class="card-title">本地服务（局域网）</div>
          ${state.localServerError ? `<div class="status error" style="margin-bottom:16px">${escapeHtml(state.localServerError)}</div>` : ""}
          ${!state.localServerInfo ? `
            <div class="form-hint-text">正在读取本地服务状态...</div>
          ` : !state.localServerInfo.listening ? `
            <div class="status" style="margin-bottom:16px">本地服务未监听${state.localServerInfo.error ? "：" + escapeHtml(state.localServerInfo.error) : "（localServer.enabled=false 或端口被占用）"}</div>
          ` : `
            <div class="form-group">
              <label class="form-label">局域网接口地址（点击复制）</label>
              ${(state.localServerUrls || []).length ? (state.localServerUrls || []).map((u) => `
                <div class="ls-url-row">
                  <span class="ls-url mono">${escapeHtml(u)}</span>
                  <button class="btn btn-secondary btn-sm ls-copy" data-url="${escapeHtml(u)}">${state.copyHint === u ? "已复制" : "复制"}</button>
                </div>
              `).join("") : `<div class="ls-url-row"><span class="ls-url">未检测到局域网 IPv4 地址（本机可能未连接网络）</span></div>`}
              <div class="form-hint-text">本机回环：http://127.0.0.1:${state.localServerInfo.port}/mooquant/api${state.localServerInfo.tokenRequired ? "（已启用 token，客户端需携带 X-Api-Token）" : ""}<br />数据源：${escapeHtml(state.localServerInfo.source)}</div>
            </div>
          `}
          <div class="form-group">
            <label class="form-label">服务发现（UDP 广播信标）</label>
            ${!state.discovery || state.discovery.state === "absent" ? `
              <div class="form-hint-text">信标不可用（当前运行环境不支持）</div>
            ` : `
              <div class="radio-group">
                <label class="radio-item ${state.discovery.listening ? "checked" : ""}"><input type="radio" name="disc" value="on" ${state.discovery.listening ? "checked" : ""} ${state.discoveryBusy ? "disabled" : ""} /><span class="radio-label">开启 — 局域网设备可自动发现本机</span></label>
                <label class="radio-item ${state.discovery.listening ? "" : "checked"}"><input type="radio" name="disc" value="off" ${state.discovery.listening ? "" : "checked"} ${state.discoveryBusy ? "disabled" : ""} /><span class="radio-label">关闭</span></label>
              </div>
              <div class="form-hint-text">${state.discovery.listening ? `UDP ${state.discovery.port} · 每 ${Math.round((state.discovery.intervalMs || 3000) / 1000)}s 广播一次${state.discovery.broadcastOk === false ? " · 广播被系统阻止，仅探测应答可用" : ""}` : "关闭后不占用任何 UDP 端口"}</div>
            `}
          </div>
        </div>
        <div class="card">
          <div class="card-title">说明</div>
          <ul style="padding-left:20px;line-height:2;color:var(--text-2);font-size:14px">
            <li>切换数据源后需要重启才能生效</li>
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
      root.querySelectorAll('input[name="theme"]').forEach(el => { el.addEventListener("change", () => {
        vm.setTheme(el.value);
        root.querySelectorAll('label.radio-item').forEach(l => { const inp = l.querySelector("input"); l.classList.toggle("checked", inp && inp.checked); });
      }); });
      root.querySelectorAll('input[name="disc"]').forEach(el => { el.addEventListener("change", () => {
        vm.setDiscovery(el.value === "on");
        root.querySelectorAll('label.radio-item').forEach(l => { const inp = l.querySelector("input"); l.classList.toggle("checked", inp && inp.checked); });
      }); });
      root.querySelectorAll(".ls-copy").forEach(btn => { btn.addEventListener("click", () => vm.copyUrl(btn.getAttribute("data-url"))); });
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