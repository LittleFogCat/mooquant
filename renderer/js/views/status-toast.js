import { escapeHtml } from '../utils/format.js';

function render(root, state, vm) {
    if (state.error) {
      root.innerHTML = '<span>' + escapeHtml(state.error) + '</span><button class="status-dismiss" data-status-dismiss>&times;</button>';
      root.className = "status error";
      const btn = root.querySelector("[data-status-dismiss]");
      if (btn && vm && vm.dismissError) {
        btn.addEventListener("click", () => vm.dismissError());
      }
    } else {
      root.className = "status hidden";
    }
  }
  export { render };
