import { getInitials } from '../utils/pinyin.js';

function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"'\/]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "/": "&#x2F;" }[c];
    });
  }

  // ----------------------------------------------------------------------
  // LRU 缓存（全局共享，localStorage 持久化）
  // ----------------------------------------------------------------------
  var LRU_KEY = "mooquant:search-lru";
  var LRU_MAX = 50;

  function getLRU() {
    try {
      var data = localStorage.getItem(LRU_KEY);
      if (!data) return [];
      var arr = JSON.parse(data);
      return Array.isArray(arr) ? arr : [];
    } catch (e) { return []; }
  }

  function saveLRU(list) {
    try { localStorage.setItem(LRU_KEY, JSON.stringify(list)); } catch (e) {}
  }

  function addToLRU(stock) {
    if (!stock || !stock.code) return;
    var list = getLRU();
    // 移除已存在的同 code 条目
    list = list.filter(function (s) { return s.code !== stock.code; });
    // 计算拼音首字母
    var pinyin = "";
    try {
      pinyin = getInitials(stock.name || "");
    } catch (e) {}
    // 插入到最前面
    list.unshift({
      code: stock.code,
      name: stock.name,
      industry: stock.industry || "",
      type: stock.type || "",
      pinyin: pinyin,
      ts: Date.now(),
    });
    // 超过上限截断
    if (list.length > LRU_MAX) list = list.slice(0, LRU_MAX);
    saveLRU(list);
  }

  function matchQuery(stock, q) {
    if (!q) return false;
    var isDigit = /^\d+$/.test(q);
    var isChinese = /[\u4e00-\u9fa5]/.test(q);
    if (isDigit) {
      var codeNum = (stock.code || "").replace(/^(sh|sz|bj)/, "");
      return codeNum.includes(q) || (stock.code || "").includes(q);
    } else if (isChinese) {
      return (stock.name || "").includes(q);
    } else {
      var py = stock.pinyin || "";
      return py.startsWith(q) || py.includes(q) || (stock.code || "").toLowerCase().includes(q);
    }
  }

  /**
   * 将搜索结果与 LRU 缓存合并：
   * LRU 中匹配当前搜索词的排前面，搜索结果中不重复的排后面
   */
  function mergeWithLRU(searchResults, query) {
    var q = (query || "").toLowerCase().trim();
    if (!q) return searchResults;

    var lru = getLRU();
    var lruMatches = lru.filter(function (s) { return matchQuery(s, q); });

    if (!lruMatches.length) return searchResults;

    // 去重：搜索结果中移除已在 LRU 匹配列表中的
    var lruCodes = {};
    lruMatches.forEach(function (s) { lruCodes[s.code] = true; });
    var nonLru = searchResults.filter(function (s) { return !lruCodes[s.code]; });

    // 标记 LRU 项（供 UI 显示「最近」标签）
    lruMatches.forEach(function (s) { s._fromLRU = true; });
    // LRU 匹配项在前 + 搜索结果在后，截断到 20 条
    return lruMatches.concat(nonLru).slice(0, 20);
  }

  // ----------------------------------------------------------------------
  // 组件主体
  // ----------------------------------------------------------------------

  function mount(inputEl, facade, onSelect, onInput, options) {
    var multi = options && options.multi;
    var suggTimer = null;
    var suggList = [];
    var suggIdx = -1;
    var isDestroyed = false;

    // --- 多选模式：提取当前词和前缀 ---
    function getCurrentTerm() {
      if (!multi) return inputEl.value;
      var val = inputEl.value;
      // 支持中英文逗号
      var lastSep = Math.max(val.lastIndexOf(","), val.lastIndexOf("，"));
      if (lastSep === -1) return val.trim();
      return val.substring(lastSep + 1).trim();
    }

    function getPrefix() {
      if (!multi) return "";
      var val = inputEl.value;
      var lastSep = Math.max(val.lastIndexOf(","), val.lastIndexOf("，"));
      if (lastSep === -1) return "";
      return val.substring(0, lastSep + 1);
    }

    // --- 创建下拉菜单 DOM（挂到 body，position:fixed） ---
    var dropdown = document.createElement("div");
    dropdown.className = "stock-search-dropdown";
    document.body.appendChild(dropdown);

    // --- 定位 ---
    function positionDropdown() {
      var rect = inputEl.getBoundingClientRect();
      dropdown.style.left = rect.left + "px";
      dropdown.style.top = (rect.bottom + 4) + "px";
      dropdown.style.width = Math.max(rect.width, 280) + "px";
    }

    function showDropdown() {
      positionDropdown();
      dropdown.style.display = "block";
    }

    function hideDropdown() {
      dropdown.style.display = "none";
      suggIdx = -1;
    }

    // --- 选中股票（更新 LRU + 回调） ---
    function pickStock(stock) {
      if (multi) {
        // 多选模式：替换当前词，保留前缀，追加逗号方便继续输入
        var prefix = getPrefix();
        var needsSep = prefix.length > 0 && !/[，,]\s*$/.test(prefix);
        inputEl.value = prefix + (needsSep ? " " : "") + stock.code + ", ";
      } else {
        inputEl.value = stock.code;
      }
      delete stock._fromLRU;
      addToLRU(stock);
      if (onInput) onInput(inputEl.value);
      if (onSelect) onSelect(stock);
      hideDropdown();
    }

    // --- 渲染候选项 ---
    function showSugg(items) {
      suggList = items;
      suggIdx = -1;
      if (!items.length) { hideDropdown(); return; }
      dropdown.innerHTML = items.map(function (s, i) {
        var typeBadge = s.type
          ? '<span class="sugg-type">' + escapeHtml(s.type) + '</span>'
          : '';
        var lruBadge = s._fromLRU
          ? '<span class="sugg-lru">最近</span>'
          : '';
        return '<div class="suggestion-item" data-sugg-idx="' + i + '">' +
          '<span class="sugg-code">' + escapeHtml(s.code) + '</span>' +
          '<span class="sugg-name">' + escapeHtml(s.name) + '</span>' +
          typeBadge +
          lruBadge +
          '<span class="sugg-industry">' + escapeHtml(s.industry || "") + '</span></div>';
      }).join("");
      showDropdown();
      dropdown.querySelectorAll(".suggestion-item").forEach(function (el) {
        el.addEventListener("click", function () {
          var idx = parseInt(el.dataset.suggIdx);
          if (suggList[idx]) pickStock(suggList[idx]);
        });
      });
    }

    function updateHighlight() {
      var items = dropdown.querySelectorAll(".suggestion-item");
      items.forEach(function (el, i) {
        el.classList.toggle("active", i === suggIdx);
      });
      if (suggIdx >= 0 && items[suggIdx]) {
        items[suggIdx].scrollIntoView({ block: "nearest" });
      }
    }

    // --- 事件处理 ---

    function onInputHandler() {
      var fullVal = inputEl.value;
      if (onInput) onInput(fullVal);
      var term = multi ? getCurrentTerm() : fullVal;
      term = term.trim();
      if (suggTimer) clearTimeout(suggTimer);
      if (!term) { hideDropdown(); return; }
      suggTimer = setTimeout(async function () {
        if (isDestroyed) return;
        try {
          var resp = await facade.quote.search(term);
          if (isDestroyed) return;
          if (resp && resp.ok && resp.data && resp.data.length > 0) {
            // 合并 LRU 缓存：最近搜索的排前面
            var merged = mergeWithLRU(resp.data, term);
            showSugg(merged);
          } else {
            // 搜索无结果时，仍尝试从 LRU 中匹配
            var lruMatches = getLRU()
              .filter(function (s) { return matchQuery(s, term.toLowerCase()); })
              .slice(0, 20);
            lruMatches.forEach(function (s) { s._fromLRU = true; });
            if (lruMatches.length) showSugg(lruMatches);
            else hideDropdown();
          }
        } catch (e) { hideDropdown(); }
      }, 200);
    }

    function onKeyDownHandler(e) {
      if (e.key === "ArrowDown" && suggList.length) {
        e.preventDefault();
        suggIdx = Math.min(suggIdx + 1, suggList.length - 1);
        updateHighlight();
      } else if (e.key === "ArrowUp" && suggList.length) {
        e.preventDefault();
        suggIdx = Math.max(suggIdx - 1, -1);
        updateHighlight();
      } else if (e.key === "Enter" && suggIdx >= 0 && suggList[suggIdx]) {
        e.preventDefault();
        pickStock(suggList[suggIdx]);
      } else if (e.key === "Escape") {
        hideDropdown();
      }
    }

    // mousedown 阻止默认行为，防止点击候选项时 input 失焦
    function onDropdownMouseDown(e) {
      e.preventDefault();
    }

    // 失焦时隐藏（延迟 150ms 以确保 click 事件先触发）
    function onBlurHandler() {
      setTimeout(function () {
        if (!isDestroyed) hideDropdown();
      }, 150);
    }

    // 点击外部关闭
    function onDocClickHandler(e) {
      if (e.target !== inputEl && !dropdown.contains(e.target)) {
        hideDropdown();
      }
    }

    // 滚动/缩放时重新定位
    function onWindowHandler() {
      if (dropdown.style.display === "block") {
        positionDropdown();
      }
    }

    // --- 绑定事件 ---
    inputEl.addEventListener("input", onInputHandler);
    inputEl.addEventListener("keydown", onKeyDownHandler);
    inputEl.addEventListener("blur", onBlurHandler);
    dropdown.addEventListener("mousedown", onDropdownMouseDown);
    document.addEventListener("click", onDocClickHandler);
    window.addEventListener("scroll", onWindowHandler, true);
    window.addEventListener("resize", onWindowHandler);

    return {
      hide: hideDropdown,
      destroy: function () {
        isDestroyed = true;
        if (suggTimer) clearTimeout(suggTimer);
        inputEl.removeEventListener("input", onInputHandler);
        inputEl.removeEventListener("keydown", onKeyDownHandler);
        inputEl.removeEventListener("blur", onBlurHandler);
        dropdown.removeEventListener("mousedown", onDropdownMouseDown);
        document.removeEventListener("click", onDocClickHandler);
        window.removeEventListener("scroll", onWindowHandler, true);
        window.removeEventListener("resize", onWindowHandler);
        if (dropdown.parentNode) dropdown.parentNode.removeChild(dropdown);
        dropdown = null;
      },
    };
  }

  // 旧 API 兼容：attach(inputEl, suggBox, facade, onSelect, onInput)
  function attach(inputEl, suggBox, facade, onSelect, onInput) {
    return mount(inputEl, facade, onSelect, onInput);
  }

  function clearLRU() { saveLRU([]); }

  export { mount, attach, escapeHtml, getLRU, clearLRU };