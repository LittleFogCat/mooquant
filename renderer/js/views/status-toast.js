function render(root, state) {
    if (state.error) {
      root.textContent = state.error;
      root.className = "status error";
    } else {
      root.className = "status hidden";
    }
  }
  export { render };