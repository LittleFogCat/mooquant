/**
 * mookquant · 简易路由
 */
(function () {
  const PAGES = ["quote", "strategy", "backtest", "trade", "settings"];
  let _currentPage = "quote";
  const _handlers = {};
  function switchPage(page) {
    if (!PAGES.includes(page)) page = "quote";
    _currentPage = page;
    document.querySelectorAll(".page").forEach(el => el.classList.toggle("page-active", el.id === "page-" + page));
    document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.page === page));
    if (_handlers[page]) _handlers[page].forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
    if (location.hash !== "#" + page) history.replaceState(null, "", "#" + page);
  }
  function onEnter(page, fn) { if (!_handlers[page]) _handlers[page] = []; _handlers[page].push(fn); }
  function currentPage() { return _currentPage; }
  function init() {
    document.querySelectorAll(".nav-item").forEach(el => el.addEventListener("click", () => switchPage(el.dataset.page)));
    const hash = location.hash.replace("#", "");
    switchPage(hash && PAGES.includes(hash) ? hash : "quote");
  }
  window.AppRouter = { switchPage, onEnter, currentPage, init, PAGES };
})();