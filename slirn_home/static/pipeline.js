// Slirn 流程配置 + 自动执行 — REQ-20260918-047（v4：全局停止阶段下拉 + 每阶段从本阶段起跑）
//
// 设计要点：
// - 整个面板用 <details> 包裹，summary 是头部（含标题 + 模板 + 全局停止阶段下拉 + 主操作 + 折叠箭头）
// - 5 个阶段每个内部又是一个 <details open>，可独立折叠
// - 顶部「完成到哪个阶段停」下拉：6 个选项（5 阶段后停 + 不停跑到底），替换 v3 的 per-stage checkbox
// - 每阶段 summary 右侧加「▶ 从本阶段开始往下执行」按钮，调 /pipeline_run?since=<stage_key>
// - 模板切换：3 套内置模板对应不同顶层 stop_after
// - checkbox click 委托用 closest('label') 早退，确保不被外层 click 抢
//
// 与 router.js 互不依赖：复用其 postJSON / toast / refreshDetail 等 window.* 全局即可。
(function() {
  if (window.__slirnPipelineBound) return;
  window.__slirnPipelineBound = true;

  var SLIRN_API = '/slirn/api';

  // ---- stage label/cfg schema ----
  var STAGES = [
    {key: 'subtitle_generation', label: '字幕生成', desc: 'FunASR 识别视频字幕；勾选「区分说话人」可识别人员编号'},
    {key: 'subtitle_review', label: '字幕修订', desc: '大模型分析字幕；可设「默认接受所有建议」'},
    {key: 'rough_cut', label: '切分修剪', desc: '按修订决策带入保留/更正段；可设「删除某些说话人全部记录」'},
    {key: 'rough_compose', label: '粗剪合成', desc: '按切分保留区间用上游 VideoClipper 合成粗剪成片（必做）'},
    {key: 'optimize', label: '优化字幕', desc: '重新识别粗剪成片字幕，提取不明确字词；可设「全部接受替换」'}
  ];
  var STAGE_KEYS = STAGES.map(function(s) { return s.key; });
  var STAGE_LABELS = {};
  STAGES.forEach(function(s) { STAGE_LABELS[s.key] = s.label; });

  // ---- 3 套内置模板（v4：顶层 stop_after 字符串；null = 跑到底）----
  var TEMPLATES = {
    // 人工全审：字幕修订后停（让用户审 LLM 建议）
    default_tpl: {
      label: '人工全审（字幕修订后停）',
      config: {
        subtitle_generation: {speaker_diarization: false},
        subtitle_review: {accept_all_suggestions: false, skip_categories: []},
        rough_cut: {delete_speakers: [], default_decision: 'keep'},
        rough_compose: {},
        optimize: {accept_all_replacements: false},
        stop_after: 'subtitle_review'
      }
    },
    // 半自动：粗剪合成后停（让人看完粗剪再决定后续）
    semi: {
      label: '半自动（粗剪合成后停）',
      config: {
        subtitle_generation: {speaker_diarization: false},
        subtitle_review: {accept_all_suggestions: true, skip_categories: []},
        rough_cut: {delete_speakers: [], default_decision: 'keep'},
        rough_compose: {},
        optimize: {accept_all_replacements: true},
        stop_after: 'rough_compose'
      }
    },
    // 全自动：跑到底
    full: {
      label: '全自动（跑到底）',
      config: {
        subtitle_generation: {speaker_diarization: false},
        subtitle_review: {accept_all_suggestions: true, skip_categories: []},
        rough_cut: {delete_speakers: [], default_decision: 'keep'},
        rough_compose: {},
        optimize: {accept_all_replacements: true},
        stop_after: null
      }
    }
  };

  // ---- 工具 ----
  function postJSON(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {})
    }).then(function(r) { return r.json(); }).catch(function(e) { return {ok: false, error: String(e)}; });
  }
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>')
      .replace(/"/g, '"').replace(/'/g, '&#39;');
  }
  function toast(msg, type) {
    if (window.slirnToast) window.slirnToast(msg, type || 'success');
  }
  function curTaskId() {
    var panel = document.getElementById('slirn-pipe-panel');
    return panel ? (panel.getAttribute('data-task-id') || '') : '';
  }

  // ---- 阶段字段 ID 命名 ----
  function fieldId(stageKey, fieldName) { return 'slirn-pipe-' + stageKey + '-' + fieldName; }

  // ---- 渲染「全局停止阶段」下拉 option 列表 ----
  function renderFlowStopOptions(selectedValue) {
    var opts = '<option value="">不停（跑到底）</option>';
    STAGES.forEach(function(s) {
      var sel = (selectedValue === s.key) ? ' selected' : '';
      opts += '<option value="' + s.key + '"' + sel + '>'
            + escapeHtml(s.label) + '后停</option>';
    });
    return opts;
  }

  // ---- 单个 stage 的表单（v4：不再有 stop_after 字段）----
  function renderStageForm(stage, stageCfg) {
    if (stage.key === 'subtitle_generation') {
      var sdOn = stageCfg.speaker_diarization ? ' checked' : '';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'sd') + '"' + sdOn + '> 区分说话人（默认关 — 单人视频减少误分）'
        + '</label>'
        + '</div>';
    }
    if (stage.key === 'subtitle_review') {
      var aa = stageCfg.accept_all_suggestions ? ' checked' : '';
      var skip = (stageCfg.skip_categories || []).join(',');
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'accept-all') + '"' + aa + '> 默认接受所有建议（跑完后自动 save_revision 全量 accept）'
        + '</label>'
        + '<label class="slirn-pipe-field"><span>跳过建议类别（逗号分隔，空 = 全接受）：</span>'
        + '<input type="text" id="' + fieldId(stage.key, 'skip-cats') + '" value="' + escapeHtml(skip) + '" placeholder="delete,review">'
        + '</label>'
        + '</div>';
    }
    if (stage.key === 'rough_cut') {
      var delSpk = (stageCfg.delete_speakers || []).join(',');
      var defDec = stageCfg.default_decision || 'keep';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field"><span>删除某些说话人全部记录（逗号分隔的人员 ID）：</span>'
        + '<input type="text" id="' + fieldId(stage.key, 'del-spk') + '" value="' + escapeHtml(delSpk) + '" placeholder="2,5">'
        + '</label>'
        + '<label class="slirn-pipe-field"><span>默认决策（修订阶段遗留未决策时）：</span>'
        + '<select id="' + fieldId(stage.key, 'def-dec') + '">'
        + '<option value="keep"' + (defDec === 'keep' ? ' selected' : '') + '>保留</option>'
        + '<option value="delete"' + (defDec === 'delete' ? ' selected' : '') + '>删除</option>'
        + '</select></label>'
        + '</div>';
    }
    if (stage.key === 'rough_compose') {
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<div class="slirn-pipe-hint">粗剪合成无需额外选项；按切分保留区间拼接。必做阶段（REQ-20260918-045）。</div>'
        + '</div>';
    }
    if (stage.key === 'optimize') {
      var aaR = stageCfg.accept_all_replacements ? ' checked' : '';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'accept-rep') + '"' + aaR + '> 默认接受所有替换（跑完后自动 save_optimize_subtitle 全量 applied）'
        + '</label>'
        + '</div>';
    }
    return '<div class="slirn-pipe-form"></div>';
  }

  // ---- 整个面板渲染（v4：details 包裹整个面板 + 头部下拉 + 每阶段 run-since 按钮）----
  function renderPanel(taskId, data) {
    var panel = document.getElementById('slirn-pipe-panel');
    if (!panel) return;
    panel.setAttribute('data-task-id', taskId);
    var cfg = (data && data.config) || {};
    var updatedAt = (data && data.updated_at) || '';

    // v4 兼容：v3 per-stage boolean / v2 per-stage string 字段丢弃；顶层 stop_after 提升
    cfg = normalizeCfg(cfg);
    var flowStopValue = cfg.stop_after || '';

    var sectionsHtml = STAGES.map(function(stage, idx) {
      return '<details class="slirn-pipe-section" data-pipe-section="' + stage.key + '" open>'
        + '<summary class="slirn-pipe-section-summary">'
        + '<span class="slirn-pipe-section-num">' + (idx + 1) + '</span>'
        + '<span class="slirn-pipe-section-label">' + escapeHtml(stage.label) + '</span>'
        + '<span class="slirn-pipe-section-hint">' + escapeHtml(stage.desc) + '</span>'
        + '<button type="button" class="slirn-btn slirn-btn-primary slirn-btn-sm slirn-pipe-stage-run"'
        + ' data-action="pipe-run-since" data-since="' + stage.key + '"'
        + ' title="保存并从本阶段开始往后执行（跳过之前所有阶段）">▶ 从本阶段开始</button>'
        + '</summary>'
        + '<div class="slirn-pipe-section-body">' + renderStageForm(stage, cfg[stage.key] || {}) + '</div>'
        + '</details>';
    }).join('');

    panel.innerHTML =
      '<details class="slirn-pipe-panel-details">'
      + '<summary class="slirn-pipe-head">'
      + '<span class="slirn-pipe-title">⚙ 流程配置</span>'
      + '<span class="slirn-pipe-updated">'
      + (updatedAt ? '最近保存：' + escapeHtml(updatedAt) : '尚未保存')
      + '</span>'
      + '<span class="slirn-pipe-head-acts">'
      + '<label class="slirn-pipe-flow-stop-label">'
      + '完成到哪个阶段停：'
      + '<select class="slirn-pipe-flow-stop" id="slirn-pipe-flow-stop">'
      + renderFlowStopOptions(flowStopValue)
      + '</select>'
      + '</label>'
      + '<select class="slirn-pipe-template" id="slirn-pipe-template" data-pipe-action="template">'
      + '<option value="default_tpl">内置：人工全审</option>'
      + '<option value="semi">内置：半自动</option>'
      + '<option value="full">内置：全自动</option>'
      + '<option value="custom">自定义</option>'
      + '</select>'
      + '<button type="button" class="slirn-btn slirn-btn-danger slirn-btn-sm" data-action="pipe-stop">⏹ 停止</button>'
      + '<button type="button" class="slirn-btn slirn-btn-sm" data-action="pipe-save">💾 保存配置</button>'
      + '<button type="button" class="slirn-btn slirn-btn-primary slirn-btn-sm" data-action="pipe-run" title="保存并按当前配置顺序执行所有阶段">▶ 运行流程</button>'
      + '</span>'
      + '</summary>'
      + '<div class="slirn-pipe-sections">' + sectionsHtml + '</div>'
      + '</details>';
  }

  // ---- v4 兼容：把 v3 per-stage boolean / v2 per-stage string 字段丢弃；顶层 stop_after 保留 ----
  function normalizeCfg(cfg) {
    if (!cfg || typeof cfg !== 'object') return cfg;
    // 顶层 stop_after 已经就是 v4 权威字段，无需迁移
    // 阶段内旧字段（v3 boolean / v2 string）会被 validate_config 在 backend 丢弃；
    // 这里也预处理以保持前端 config 一致
    STAGE_KEYS.forEach(function(k) {
      var s = cfg[k];
      if (s && typeof s === 'object' && 'stop_after' in s) {
        delete s.stop_after;
      }
    });
    return cfg;
  }

  // ---- 表单 → config ----
  function _val(id, fallback) {
    var el = document.getElementById(id);
    return el ? el.value : fallback;
  }
  function _checked(id, fallback) {
    var el = document.getElementById(id);
    return el ? !!el.checked : fallback;
  }
  function readCurrentConfig() {
    var panel = document.getElementById('slirn-pipe-panel');
    if (!panel) return null;
    var cfg = {};
    cfg.subtitle_generation = {
      speaker_diarization: _checked(fieldId('subtitle_generation', 'sd'), false)
    };
    cfg.subtitle_review = {
      accept_all_suggestions: _checked(fieldId('subtitle_review', 'accept-all'), true),
      skip_categories: _val(fieldId('subtitle_review', 'skip-cats'), '').split(',').map(function(x){return x.trim();}).filter(Boolean)
    };
    cfg.rough_cut = {
      delete_speakers: _val(fieldId('rough_cut', 'del-spk'), '').split(',').map(function(x){return parseInt(x.trim(), 10);}).filter(function(x){return !isNaN(x);}),
      default_decision: _val(fieldId('rough_cut', 'def-dec'), 'keep') || 'keep'
    };
    cfg.rough_compose = {};
    cfg.optimize = {
      accept_all_replacements: _checked(fieldId('optimize', 'accept-rep'), true)
    };
    // v4 顶层 stop_after："" → null；其他保留原值
    var flowStop = _val('slirn-pipe-flow-stop', '');
    cfg.stop_after = flowStop || null;
    return cfg;
  }

  // ---- 状态条 ----
  var pipeStatusTimer = null;
  function showStatus(taskId) {
    var box = document.getElementById('slirn-pipe-status');
    if (!box) return;
    box.hidden = false;
    box.setAttribute('data-task-id', taskId);
    if (pipeStatusTimer) { clearInterval(pipeStatusTimer); pipeStatusTimer = null; }
    pipeStatusTimer = setInterval(function() { pollStatus(taskId); }, 1500);
    pollStatus(taskId);
  }
  function hideStatus() {
    var box = document.getElementById('slirn-pipe-status');
    if (!box) return;
    box.hidden = true;
    if (pipeStatusTimer) { clearInterval(pipeStatusTimer); pipeStatusTimer = null; }
    box.innerHTML = '';
  }
  function pollStatus(taskId) {
    postJSON(SLIRN_API + '/pipeline_status', {task_id: taskId}).then(function(r) {
      if (!r || !r.ok) return;
      renderStatusBar(r);
      if (r.state !== 'running') {
        if (pipeStatusTimer) { clearInterval(pipeStatusTimer); pipeStatusTimer = null; }
      }
    });
  }
  function renderStatusBar(st) {
    var box = document.getElementById('slirn-pipe-status');
    if (!box) return;
    var stageLabel = st.current_stage ? (STAGE_LABELS[st.current_stage] || st.current_stage) : '—';
    var pct = Math.max(0, Math.min(100, Number(st.percent || 0)));
    var log = Array.isArray(st.log) ? st.log : [];
    var last3 = log.slice(-3).map(function(e) {
      var lvl = e.level || 'info';
      var sign = lvl === 'error' ? '❌' : (lvl === 'ok' ? '✅' : (lvl === 'warn' ? '⚠️' : '•'));
      return '<div class="slirn-pipe-status-log-row slirn-pipe-status-log-' + lvl + '">'
        + '<span class="slirn-pipe-status-log-sign">' + sign + '</span>'
        + '<span class="slirn-pipe-status-log-msg">' + escapeHtml(e.msg || '') + '</span></div>';
    }).join('');
    var stateLabel = ({
      running: '运行中',
      done: '已完成',
      error: '出错',
      stopped: '已停止',
      idle: '空闲'
    })[st.state] || st.state;
    box.innerHTML =
      '<div class="slirn-pipe-status-head">'
      + '<span class="slirn-pipe-status-state slirn-pipe-status-state-' + st.state + '">'
      + (st.state === 'running' ? '⏳' : (st.state === 'done' ? '✅' : (st.state === 'error' ? '❌' : (st.state === 'stopped' ? '⏹' : '•'))))
      + ' ' + escapeHtml(stateLabel) + '</span>'
      + '<span class="slirn-pipe-status-stage">当前阶段：' + escapeHtml(stageLabel) + '</span>'
      + '<progress class="slirn-pipe-status-bar" max="100" value="' + pct + '"></progress>'
      + '<button type="button" class="slirn-pipe-status-collapse" data-action="pipe-status-collapse" title="收起状态条">▾</button>'
      + '</div>'
      + (last3 ? '<div class="slirn-pipe-status-log">' + last3 + '</div>' : '')
      + (st.error ? '<div class="slirn-pipe-status-err">' + escapeHtml(st.error) + '</div>' : '');
  }

  // ---- 入口 ----
  function loadPanel(taskId) {
    postJSON(SLIRN_API + '/pipeline_get', {task_id: taskId}).then(function(r) {
      if (!r || !r.ok) {
        toast('流程配置读取失败：' + (r && r.error || '未知错误'), 'error');
        return;
      }
      renderPanel(taskId, {config: r.config, updated_at: r.updated_at});
      if (r.status && r.status.state === 'running') {
        showStatus(taskId);
      }
    });
  }
  function saveConfig(taskId) {
    var cfg = readCurrentConfig();
    if (!cfg) { toast('未读取到配置', 'error'); return; }
    postJSON(SLIRN_API + '/pipeline_save', {task_id: taskId, config: cfg}).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || '⚙️ 已保存');
        var panel = document.getElementById('slirn-pipe-panel');
        if (panel) {
          var sel = document.getElementById('slirn-pipe-template');
          if (sel) sel.value = 'custom';
          var u = panel.querySelector('.slirn-pipe-updated');
          if (u && r.updated_at) u.textContent = '最近保存：' + r.updated_at;
        }
      } else {
        toast('保存失败：' + (r && r.error || '未知错误'), 'error');
      }
    });
  }
  function runPipeline(taskId, since) {
    var cfg = readCurrentConfig();
    if (!cfg) { toast('未读取到配置', 'error'); return; }
    // 运行前先保存（确保服务端拿到的是表单最新值）
    postJSON(SLIRN_API + '/pipeline_save', {task_id: taskId, config: cfg}).then(function(s) {
      if (!s || !s.ok) {
        toast('保存失败，无法启动：' + (s && s.error || '未知错误'), 'error');
        return;
      }
      var payload = {task_id: taskId};
      if (since) payload.since = since;
      postJSON(SLIRN_API + '/pipeline_run', payload).then(function(r) {
        if (!r || !r.ok) { toast('启动失败：' + (r && r.error || '未知错误'), 'error'); return; }
        if (!r.started) {
          toast(r.toast || '已在运行');
          return;
        }
        var msg = since ? ('▶ 已从「' + (STAGE_LABELS[since] || since) + '」开始') : '▶ 已启动';
        toast(r.toast || msg);
        showStatus(taskId);
      });
    });
  }
  function stopPipeline(taskId) {
    postJSON(SLIRN_API + '/pipeline_stop', {task_id: taskId}).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || '⏹ 已请求停止');
      } else {
        toast('停止失败：' + (r && r.error || '未知错误'), 'error');
      }
    });
  }

  // ---- 全局 click 委托 ----
  // v4：每阶段 summary 内有「▶ 从本阶段开始」按钮（data-action="pipe-run-since"），
  // 不放 data-pipe-action，因此不会被 closest('button[data-pipe-action]') 早退抢走。
  document.addEventListener('click', function(ev) {
    var t = ev.target;
    if (!t || !t.getAttribute) return;
    // label / input / select / option / textarea 内部点击 → 早退（让浏览器原生行为生效）
    if (t.closest && t.closest('label,input,select,option,textarea,button[data-pipe-action]')) {
      return;
    }
    // details summary（折叠/展开）→ 浏览器原生处理
    if (t.tagName === 'SUMMARY') return;

    var tid = t.getAttribute('data-task-id') || curTaskId();
    var action = t.getAttribute('data-action');
    if (!action) return;
    if (action === 'pipe-save') { ev.preventDefault(); saveConfig(tid); return; }
    if (action === 'pipe-run') { ev.preventDefault(); runPipeline(tid, null); return; }
    if (action === 'pipe-run-since') {
      ev.preventDefault();
      var since = t.getAttribute('data-since') || null;
      runPipeline(tid, since);
      return;
    }
    if (action === 'pipe-stop') { ev.preventDefault(); stopPipeline(tid); return; }
    if (action === 'pipe-open') { ev.preventDefault(); loadPanel(tid); return; }
    if (action === 'pipe-status-collapse') {
      var box = document.getElementById('slirn-pipe-status');
      if (box) box.hidden = true;
      return;
    }
  });

  // ---- 模板切换：v4 直接重渲染整个面板 ----
  document.addEventListener('change', function(ev) {
    var t = ev.target;
    if (!t || !t.getAttribute) return;
    if (t.id === 'slirn-pipe-template') {
      var panel = document.getElementById('slirn-pipe-panel');
      if (!panel) return;
      var tplKey = t.value;
      if (tplKey !== 'custom' && TEMPLATES[tplKey]) {
        var cfg = TEMPLATES[tplKey].config;
        var taskId = panel.getAttribute('data-task-id');
        renderPanel(taskId, {config: cfg, updated_at: '模板 ' + TEMPLATES[tplKey].label, _fromTemplate: true});
        toast('已载入模板：' + TEMPLATES[tplKey].label);
      }
    }
  });

  // ---- 切任务时清空旧状态条 ----
  window.addEventListener('slirn:before-task-switch', function() {
    hideStatus();
  });

  // ---- 工作台加载时自动渲染面板 ----
  function tryMount(taskId) {
    var panel = document.getElementById('slirn-pipe-panel');
    if (!panel) return false;
    var cur = panel.getAttribute('data-task-id') || '';
    if (cur === taskId && panel.innerHTML.trim()) return true;
    panel.setAttribute('data-task-id', taskId);
    loadPanel(taskId);
    return true;
  }
  window.slirnPipelineMount = tryMount;

  // 兜底：监听 DOM 变化，捕获路由异步插入的情况
  var observer = new MutationObserver(function() {
    var panel = document.getElementById('slirn-pipe-panel');
    if (!panel) return;
    var tid = panel.getAttribute('data-task-id') || '';
    if (!tid) return;
    if (!panel.innerHTML.trim()) tryMount(tid);
  });
  if (document.body) {
    observer.observe(document.body, {childList: true, subtree: true});
  } else {
    document.addEventListener('DOMContentLoaded', function() {
      observer.observe(document.body, {childList: true, subtree: true});
    });
  }

  window.slirnPipelineHideStatus = hideStatus;
})();