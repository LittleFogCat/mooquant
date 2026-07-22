const PAGES = ["quote", "strategy", "backtest", "trade", "models", "settings"];
  let _currentPage = "quote";
  const _handlers = {};
  const _leaveHandlers = {};
  function switchPage(page) {
    if (!PAGES.includes(page)) page = "quote";
    if (_currentPage && _leaveHandlers[_currentPage]) {
      try { _leaveHandlers[_currentPage](); } catch (e) { console.error(e); }
    }
    _currentPage = page;
    document.querySelectorAll(".page").forEach(el => el.classList.toggle("page-active", el.id === "page-" + page));
    document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.page === page));
    if (_handlers[page]) _handlers[page].forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
    if (location.hash !== "#" + page) history.replaceState(null, "", "#" + page);
  }
  function onEnter(page, fn) { if (!_handlers[page]) _handlers[page] = []; _handlers[page].push(fn); }
  function onLeave(page, fn) { _leaveHandlers[page] = fn; }
  function currentPage() { return _currentPage; }
  function init() {
    document.querySelectorAll(".nav-item").forEach(el => el.addEventListener("click", () => switchPage(el.dataset.page)));
    const hash = location.hash.replace("#", "");
    switchPage(hash && PAGES.includes(hash) ? hash : "quote");
  }
  export { switchPage, onEnter, onLeave, currentPage, init, PAGES };