/**
 * mookquant · Strategy View（策略管理视图）
 *
 * 列表 + 编辑弹窗 + 启动/停止 + 导出 QMT 脚本。
 * 策略类型从后端 strategyTypes 动态加载；支持「编写自定义策略」。
 * 参数表单根据 params_schema 自动生成（替代裸 JSON 编辑）。
 */
import * as StockSearch from './stock-search.js';

const TYPE_LABELS = {
    shell: "壳策略",
    ma_cross: "双均线",
    momentum: "动量",
    mean_reversion: "均值回归",
    custom: "自定义",
  };

  /** 风控参数 schema（固定结构，表单化渲染） */
  const RISK_SCHEMA = [
    { key: "stopLoss", type: "float", default: 0.05, min: 0, max: 1, step: 0.01, label: "止损比例", hint: "亏损达到此比例时自动卖出（0.05 = 5%）" },
    { key: "stopProfit", type: "float", default: 0.15, min: 0, max: 1, step: 0.01, label: "止盈比例", hint: "盈利达到此比例时自动卖出（0.15 = 15%）" },
    { key: "maxOrderAmount", type: "float", default: 500000, min: 0, step: 1000, label: "单笔最大金额", hint: "单笔下单的最大金额（元）" },
    { key: "maxDailyTrades", type: "int", default: 10, min: 1, step: 1, label: "日内交易次数", hint: "每日最大交易次数" },
    { key: "maxPositionRatio", type: "float", default: 0.3, min: 0, max: 1, step: 0.01, label: "持仓比例上限", hint: "最大持仓占总资金比例（0.3 = 30%）" },
  ];

  /** 自定义策略代码模板（用户编写新策略的起点） */
  const STRATEGY_TEMPLATE = `from strategies.base import StrategyBase, Signal
from strategies.indicators import sma


class MyStrategy(StrategyBase):
    """策略说明：在这里描述你的策略"""
    name = "my_strategy"           # 策略类型名（须与文件名一致）
    display_name = "我的策略"
    description = "策略描述"
    version = "1.0"
    trigger_mode = "bar"
    params_schema = [
        {"key": "period", "type": "int", "default": 20, "min": 1, "max": 250, "label": "周期"},
    ]

    def on_bar(self, bar, ctx):
        bars = ctx.bars
        period = int(self.params.get("period", 20))
        closes = [b["close"] for b in bars]
        if len(closes) < period + 1:
            return None
        ma = sma(closes, period)
        prev_ma, now_ma = ma[-2], ma[-1]
        prev_close, now_close = closes[-2], closes[-1]
        # 示例：价格上穿均线买入，下穿卖出
        if prev_close <= prev_ma and now_close > now_ma:
            return Signal(action="buy", reason="上穿均线",
                          indicators={"ma": round(now_ma, 4)})
        if prev_close >= prev_ma and now_close < now_ma:
            return Signal(action="sell", reason="下穿均线",
                          indicators={"ma": round(now_ma, 4)})
        return None
`;

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"'\/]/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "/": "&#x2F;",
    }[c]));
  }

  /** 获取模型展示名：优先 name，unnamed 时 fallback 到 arch+日期，最后 model_id */
  function modelDisplayName(am) {
    if (!am) return "";
    if (am.meta && am.meta.name && am.meta.name !== "unnamed") return am.meta.name;
    if (am.meta && am.meta.arch) {
      var ts = (am.meta.created_at || "").slice(0, 10);
      return am.meta.arch + (ts ? " \u00b7 " + ts : "");
    }
    return am.model_id || "";
  }

  /** 模型列表项（/models 返回的 meta）展示名 */
  function modelName(m) {
    if (!m) return "";
    if (m.name && m.name !== "unnamed") return m.name;
    if (m.arch) {
      var ts = (m.created_at || "").slice(0, 10);
      return m.arch + (ts ? " · " + ts : "");
    }
    return m.model_id || "";
  }

  /** 绑定模型下拉（ML/壳策略）：空值 = 跟随激活模型 */
  function renderModelSelect(state, selectedModelId) {
    const models = state.models || [];
    const opts = ['<option value="" ' + (!selectedModelId ? "selected" : "") + ">跟随激活模型</option>"]
      .concat(models.map((m) => '<option value="' + escapeHtml(m.model_id) + '" ' + (selectedModelId === m.model_id ? "selected" : "") + ">" + escapeHtml(modelName(m)) + "</option>"))
      .join("");
    const footer = models.length ? "" : '<div style="font-size:11px;color:var(--text-3);margin-top:-6px;margin-bottom:8px">暂无可用模型，请先在模型管理页训练</div>';
    return '<div class="form-group"><label class="form-label">绑定模型</label>' +
      '<select class="input-field" id="st_model">' + opts + "</select></div>" + footer;
  }

  /** 从 state.strategyTypes 取展示名，fallback 到 TYPE_LABELS */
  function getTypeLabel(state, type) {
    const t = (state.strategyTypes || []).find((x) => x.name === type);
    return t ? t.display_name : (TYPE_LABELS[type] || type);
  }

  /**
   * 根据 params_schema 自动生成参数表单（替代裸 JSON 编辑）。
   * 无 schema 时 fallback 到 JSON textarea。
   */
  function renderParamsForm(state, currentParamsStr) {
    const types = state.strategyTypes || [];
    const type = types.find((t) => t.name === state.editing.type);
    // 无 schema 时 fallback 到 JSON textarea
    let schema = (type && type.params_schema) || [];
    let current = {};
    try { current = JSON.parse(currentParamsStr || "{}"); } catch (e) {}

    const isShell = type && type.is_shell;
    const isML = type && type.is_ml;
    let mlHint = "";
    let modelSelect = "";
    if (isShell || isML) {
      modelSelect = renderModelSelect(state, state.editing.modelId || "");
      const am = state.activeModel;
      if (am && am.model_id) {
        mlHint = '<div style="font-size:11px;color:var(--text-3);margin:-6px 0 8px">不绑定时自动使用激活模型：<strong>' + escapeHtml(modelDisplayName(am)) + '</strong></div>';
      } else {
        mlHint = '<div style="font-size:11px;color:var(--red);margin:-6px 0 8px">当前未激活模型，' + (isShell ? "壳策略" : "ML 策略") + "必须绑定一个模型才能运行</div>";
      }
    }
    if (isML) {
      // model_id 由「绑定模型」下拉统一管理，隐藏 schema 中的同名文本框
      schema = schema.filter((p) => p.key !== "model_id");
    }
    if (isShell) {
      return modelSelect + mlHint;
    }

    if (!schema.length) {
      return modelSelect + mlHint + '<div class="form-group"><label class="form-label">策略参数 (JSON)</label>' +
        '<textarea class="input-field" id="st_params" style="font-family:monospace;font-size:13px;min-height:80px">' + escapeHtml(currentParamsStr) + '</textarea></div>';
    }

    const fields = schema.map((p) => {
      const val = current[p.key] != null ? current[p.key] : p.default;
      const label = escapeHtml(p.label || p.key);
      const rangeHint = (p.min != null || p.max != null)
        ? ' <span style="color:var(--text-3);font-size:11px">(' + (p.min != null ? p.min : "") + "~" + (p.max != null ? p.max : "") + ")</span>" : "";
      if (p.type === "int" || p.type === "float") {
        const step = p.type === "float" ? "0.01" : "1";
        return '<div class="form-group"><label class="form-label">' + label + rangeHint + '</label>' +
          '<input class="input-field" id="st_param_' + escapeHtml(p.key) + '" type="number" step="' + step +
          '" value="' + val + '"' + (p.min != null ? ' min="' + p.min + '"' : "") +
          (p.max != null ? ' max="' + p.max + '"' : "") + " /></div>" + (p.description ? '<div style="font-size:11px;color:var(--text-3);margin-top:-6px;margin-bottom:8px">' + escapeHtml(p.description) + '</div>' : '');
      }
      if (p.type === "bool") {
        return '<div class="form-group"><label class="form-label">' + label + "</label>" +
          '<select class="input-field" id="st_param_' + escapeHtml(p.key) + '"><option value="true" ' +
          (val ? "selected" : "") + '>是</option><option value="false" ' + (!val ? "selected" : "") + ">否</option></select></div>" + (p.description ? '<div style="font-size:11px;color:var(--text-3);margin-top:-6px;margin-bottom:8px">' + escapeHtml(p.description) + '</div>' : '');
      }
      return '<div class="form-group"><label class="form-label">' + label + '</label>' +
        '<input class="input-field" id="st_param_' + escapeHtml(p.key) + '" type="text" value="' + escapeHtml(String(val)) + '" /></div>' + (p.description ? '<div style="font-size:11px;color:var(--text-3);margin-top:-6px;margin-bottom:8px">' + escapeHtml(p.description) + '</div>' : '');
    }).join("");

    return modelSelect + mlHint + '<div class="form-group"><label class="form-label">策略参数</label></div>' + fields +
      '<input type="hidden" id="st_param_keys" value="' + escapeHtml(schema.map((p) => p.key + ":" + p.type).join(",")) + '" />';
  }

  /** 根据 RISK_SCHEMA 生成风控参数表单（每项带说明） */
  function renderRiskForm(currentRiskStr) {
    var current = {};
    try { current = JSON.parse(currentRiskStr || "{}"); } catch (e) {}
    var fields = RISK_SCHEMA.map(function (p) {
      var val = current[p.key] != null ? current[p.key] : p.default;
      var step = p.step || (p.type === "float" ? "0.01" : "1");
      var hint = p.hint ? '<div style="font-size:11px;color:var(--text-3);margin-top:-6px;margin-bottom:8px">' + escapeHtml(p.hint) + '</div>' : '';
      return '<div class="form-group"><label class="form-label">' + escapeHtml(p.label) + '</label>' +
        '<input class="input-field" id="st_risk_' + escapeHtml(p.key) + '" type="number" step="' + step +
        '" value="' + val + '"' + (p.min != null ? ' min="' + p.min + '"' : '') +
        (p.max != null ? ' max="' + p.max + '"' : '') + ' /></div>' + hint;
    }).join("");
    return '<div class="form-group"><label class="form-label">风控参数</label></div>' + fields +
      '<input type="hidden" id="st_risk_keys" value="' + escapeHtml(RISK_SCHEMA.map(function (p) { return p.key + ":" + p.type; }).join(",")) + '" />';
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
      const tmeta = (state.strategyTypes || []).find((t) => t.name === s.type);
      let modelInfo = "";
      if (tmeta && (tmeta.is_ml || tmeta.is_shell)) {
        if (s.modelId) {
          const m = (state.models || []).find((x) => x.model_id === s.modelId);
          modelInfo = '<div style="font-size:11px;color:var(--text-3);margin-top:2px">模型: ' + escapeHtml(m ? modelName(m) : s.modelId) + "</div>";
        } else {
          const am = state.activeModel;
          modelInfo = '<div style="font-size:11px;color:var(--text-3);margin-top:2px">模型: 跟随激活' + (am && am.model_id ? "（" + escapeHtml(modelDisplayName(am)) + "）" : "") + "</div>";
        }
      }
      return '<tr>' +
        '<td><strong>' + escapeHtml(s.name) + '</strong>' + runInfo + '</td>' +
        '<td>' + escapeHtml(getTypeLabel(state, s.type)) + modelInfo + '</td>' +
        '<td style="font-size:12px;color:var(--text-3)">' + escapeHtml((s.symbols && s.symbols.length) ? s.symbols.join(", ") : "—") + '</td>' +
        '<td>' + statusBadge + '</td>' +
        '<td>' + escapeHtml(new Date(s.updatedAt).toLocaleDateString("zh-CN")) + '</td>' +
        '<td>' +
          runBtn + " " +
          '<button class="btn btn-secondary btn-sm" data-action="edit" data-id="' + escapeHtml(s.id) + '">编辑</button> ' +
          '<button class="btn btn-secondary btn-sm" data-action="duplicate" data-id="' + escapeHtml(s.id) + '">复制</button> ' +
          '<button class="btn btn-secondary btn-sm" data-action="export" data-id="' + escapeHtml(s.id) + '">导出</button> ' +
          '<button class="btn btn-danger btn-sm" data-action="delete" data-id="' + escapeHtml(s.id) + '">删除</button>' +
        '</td>' +
      '</tr>';
    }).join("");
    return '<table class="data-table">' +
      '<thead><tr><th>名称</th><th>类型</th><th>标的</th><th>状态</th><th>更新日期</th><th>操作</th></tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>';
  }

  function renderModal(state) {
    if (!state.editing) return "";
    const e = state.editing;
    const isCustom = !!e.customMode;
    const types = (state.strategyTypes && state.strategyTypes.length)
      ? state.strategyTypes
      : Object.entries(TYPE_LABELS).map(([v, l]) => ({ name: v, display_name: l }));
    let typeOptions = types.map((t) =>
      '<option value="' + escapeHtml(t.name) + '" ' + (e.type === t.name && !isCustom ? "selected" : "") + '>' + escapeHtml(t.display_name) + '</option>'
    ).join("");
    // 新建时才允许「编写自定义策略」
    if (!e.id) {
      typeOptions += '<option value="__custom__" ' + (isCustom ? "selected" : "") + ">✚ 编写自定义策略...</option>";
    }

    // 参数表单（已有类型）或自定义策略代码编辑区
    const paramsHtml = isCustom
      ? '<div class="form-group"><label class="form-label">策略类型名（英文标识符，如 my_macd）</label>' +
          '<input class="input-field" id="st_custom_name" value="' + escapeHtml(e.customName) + '" placeholder="my_strategy" /></div>' +
        '<div class="form-group"><label class="form-label">策略代码（Python，须含 StrategyBase 子类，类属性 name 须与类型名一致）</label>' +
          '<textarea class="input-field" id="st_custom_code" style="font-family:monospace;font-size:12px;min-height:320px;white-space:pre;resize:vertical">' + escapeHtml(e.customCode || STRATEGY_TEMPLATE) + '</textarea></div>' +
        '<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:12px">保存后将自动注册为新的策略类型，之后可在「类型」下拉中选用并调参。</div>'
      : renderParamsForm(state, e.params);

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
        paramsHtml +
        renderRiskForm(e.risk) +
        (isCustom ? '' :
          '<details class="form-group" style="margin-top:12px"><summary class="form-label" style="cursor:pointer">策略源码（可直接编辑）</summary>' +
          '<textarea class="input-field" id="st_code" style="font-family:monospace;font-size:12px;min-height:280px;white-space:pre;resize:vertical;margin-top:8px" placeholder="加载中...">' + escapeHtml(e.code || '') + '</textarea>' +
          '<input type="hidden" id="st_original_code" value="' + escapeHtml(e.originalCode || '') + '" />' +
          '<div style="font-size:11px;color:var(--text-tertiary);margin-top:4px">修改源码后保存将更新策略类型，不影响已有实例。</div>' +
          '</details>') +
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

  /** 启动策略弹窗（股票搜索选择执行标的） */
  function renderStartModal(state) {
    if (!state.startingId) return "";
    return '<div class="modal-overlay" id="startModal">' +
      '<div class="modal" style="max-width:480px">' +
        '<h2 class="modal-title">启动策略</h2>' +
        '<div class="form-group"><label class="form-label">执行标的</label>' +
          '<div class="search-input-wrap">' +
            '<input class="search-input" id="st_start_symbols" type="text" placeholder="输入代码/名称/拼音搜索，如：sh600036 / 茂台 / gzmt" autocomplete="off" spellcheck="false" value="' + escapeHtml(state.startingSymbols || "") + '" />' +
          '</div>' +
        '</div>' +
        '<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:12px">搜索选择股票，多个标的用逗号分隔</div>' +
        '<div class="modal-actions">' +
          '<button class="btn btn-secondary" data-action="cancel-start">取消</button>' +
          '<button class="btn btn-primary" data-action="confirm-start">启动</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  /** 收集弹窗表单数据 */
  function collectForm() {
    const customMode = !!document.getElementById("st_custom_code");
    const form = {
      name: document.getElementById("st_name").value,
      type: document.getElementById("st_type").value,
      status: document.getElementById("st_status").value,
      description: document.getElementById("st_desc").value,
      risk: (function () {
        var keysEl = document.getElementById("st_risk_keys");
        if (!keysEl) return "{}";
        var risk = {};
        keysEl.value.split(",").forEach(function (kt) {
          var idx = kt.lastIndexOf(":");
          var key = idx > 0 ? kt.slice(0, idx) : kt;
          var type = idx > 0 ? kt.slice(idx + 1) : "string";
          var el = document.getElementById("st_risk_" + key);
          if (el) {
            var v = el.value;
            if (type === "int") v = parseInt(v, 10);
            else if (type === "float") v = parseFloat(v);
            if (isNaN(v)) v = 0;
            risk[key] = v;
          }
        });
        return JSON.stringify(risk);
      })(),
      customMode: customMode,
      modelId: (function () { var el = document.getElementById("st_model"); return el ? el.value : ""; })(),
      code: (function() { var el = document.getElementById("st_code"); return el ? el.value : ""; })(),
      originalCode: (function() { var el = document.getElementById("st_original_code"); return el ? el.value : ""; })(),
    };
    if (customMode) {
      form.customName = document.getElementById("st_custom_name").value;
      form.customCode = document.getElementById("st_custom_code").value;
      form.params = "{}";
    } else {
      // 根据 params_schema 表单收集参数
      const keysEl = document.getElementById("st_param_keys");
      if (keysEl) {
        const params = {};
        keysEl.value.split(",").forEach((kt) => {
          const idx = kt.lastIndexOf(":");
          const key = idx > 0 ? kt.slice(0, idx) : kt;
          const type = idx > 0 ? kt.slice(idx + 1) : "string";
          const el = document.getElementById("st_param_" + key);
          if (el) {
            let v = el.value;
            if (type === "int") v = parseInt(v, 10);
            else if (type === "float") v = parseFloat(v);
            else if (type === "bool") v = v === "true";
            if ((type === "int" || type === "float") && isNaN(v)) v = 0;
            params[key] = v;
          }
        });
        form.params = JSON.stringify(params);
      } else {
        const paramsEl = document.getElementById("st_params");
        form.params = paramsEl ? paramsEl.value : "{}";
      }
    }
    return form;
  }

  function render(root, vm) {
    var startSearchCtrl = null;

    function paint(state) {
      // DOM 重建前销毁旧的搜索组件
      if (startSearchCtrl) { startSearchCtrl.destroy(); startSearchCtrl = null; }

      const header = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">' +
        '<h2 style="margin:0">策略管理</h2>' +
        '<button class="btn btn-primary" data-action="create">+ 新建策略</button>' +
      '</div>';
      root.innerHTML = header + renderList(state, vm) + renderModal(state) + renderExportModal(state) + renderStartModal(state);

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
            vm.startStart(id);
          } else if (action === "confirm-start") {
            const inp = document.getElementById("st_start_symbols");
            if (inp && inp.value.trim()) vm.startExecution(state.startingId, inp.value.trim());
          } else if (action === "cancel-start") {
            vm.cancelStart();
          }
          else if (action === "stop") vm.stopExecution(id);
          else if (action === "duplicate") vm.duplicate(id);
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

      // 类型下拉变化：切换自定义模式 / 更新参数默认值
      const typeSelect = root.querySelector("#st_type");
      if (typeSelect) {
        typeSelect.addEventListener("change", () => vm.onTypeChange(typeSelect.value));
      }

      // 启动弹窗：挂载股票搜索组件（多选模式）
      if (state.startingId) {
        const startInput = root.querySelector("#st_start_symbols");
        if (startInput) {
          startSearchCtrl = StockSearch.mount(startInput, vm.facade, null, null, { multi: true });
        }
      }
    }

    vm.subscribe(paint);
  }

  export { render };
