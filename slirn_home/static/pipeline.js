// Slirn 流程配置 + 自动执行 — REQ-20260918-047
// 抽屉编辑器（5 tabs + 表单 + 模板 + 运行/停止/保存）+ 顶部状态条（进度/日志/轮询）。
//
// 与 router.js 互不依赖：复用其 postJSON / toast / refreshDetail 等 window.* 全局即可。
// 独立 <script src> 注入（同 router.js 的 _build_head 路径），不放在 head= 内联
// （反斜杠转义解码 bug，见 ROUTER_JS 注释）。
(function() {
  if (window.__slirnPipelineBound) return;
  window.__slirnPipelineBound = true;

  var SLIRN_API = '/slirn/api';

  // ---- stage label/cfg schema ----
  // 与 pipeline_service.STAGE_ORDER 对齐；label 用中文展示
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

  // ---- 3 套内置模板 ----
  var TEMPLATES = {
    // 人工全审：所有阶段停 = 全部要人工看一眼
    default_tpl: {
      label: '人工全审（默认）',
      config: {
        subtitle_generation: {speaker_diarization: false, stop_after: 'subtitle_generation'},
        subtitle_review: {accept_all_suggestions: false, skip_categories: [], stop_after: 'subtitle_review'},
        rough_cut: {delete_speakers: [], default_decision: 'keep', stop_after: 'rough_cut'},
        rough_compose: {stop_after: 'rough_compose'},
        optimize: {accept_all_replacements: false, stop_after: 'optimize'},
        stop_after: 'optimize'
      }
    },
    // 半自动：字幕修订人工、粗剪合成自动
    semi: {
      label: '半自动（修订人工，合成自动）',
      config: {
        subtitle_generation: {speaker_diarization: false, stop_after: 'subtitle_generation'},
        subtitle_review: {accept_all_suggestions: false, skip_categories: [], stop_after: 'subtitle_review'},
        rough_cut: {delete_speakers: [], default_decision: 'keep', stop_after: 'rough_cut'},
        rough_compose: {stop_after: 'optimize'},  // 跑到优化前停（让人看粗剪）
        optimize: {accept_all_replacements: true, stop_after: 'optimize'},
        stop_after: 'rough_compose'  // 流程层：粗剪合成后停
      }
    },
    // 全自动：所有阶段跑完
    full: {
      label: '全自动',
      config: {
        subtitle_generation: {speaker_diarization: false, stop_after: 'subtitle_generation'},
        subtitle_review: {accept_all_suggestions: true, skip_categories: [], stop_after: 'subtitle_review'},
        rough_cut: {delete_speakers: [], default_decision: 'keep', stop_after: 'rough_cut'},
        rough_compose: {stop_after: 'rough_compose'},
        optimize: {accept_all_replacements: true, stop_after: 'optimize'},
        stop_after: null  // 跑完
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
  function getInput(id) { var el = document.getElementById(id); return el ? el.value : ''; }
  function setInput(id, val) { var el = document.getElementById(id); if (el) el.value = val; }
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function toast(msg, type) {
    if (window.slirnToast) window.slirnToast(msg, type || 'success');
  }
  function curTaskId() {
    var drawer = document.getElementById('slirn-pipe-drawer');
    return drawer ? (drawer.getAttribute('data-task-id') || '') : '';
  }
  function fmtDur(ms) {
    if (!ms || ms < 0) return '-';
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + 's';
    var m = Math.floor(s / 60), ss = s % 60;
    if (m < 60) return m + 'm' + (ss < 10 ? '0' : '') + ss + 's';
    var h = Math.floor(m / 60), mm = m % 60;
    return h + 'h' + (mm < 10 ? '0' : '') + mm + 'm';
  }

  // ---- 抽屉渲染 ----
  function renderDrawer(taskId, data) {
    var drawer = document.getElementById('slirn-pipe-drawer');
    if (!drawer) return;
    drawer.setAttribute('data-task-id', taskId);
    var cfg = (data && data.config) || {};
    // 当前激活的 tab
    var activeTab = drawer.getAttribute('data-active-tab') || STAGES[0].key;

    var tabsHtml = STAGES.map(function(s) {
      var active = s.key === activeTab ? ' active' : '';
      return '<button type="button" class="slirn-pipe-tab' + active + '" data-pipe-tab="' + s.key + '">'
        + escapeHtml(s.label) + '</button>';
    }).join('');

    // 当前 stage 表单
    var stageIdx = STAGE_KEYS.indexOf(activeTab);
    var bodyHtml = renderStageForm(STAGES[stageIdx], cfg[activeTab] || {}, cfg);

    // 模板下拉（保留用户当前选择）
    var curTpl = drawer.getAttribute('data-template') || 'default_tpl';
    var tplOpts = Object.keys(TEMPLATES).map(function(k) {
      var sel = k === curTpl ? ' selected' : '';
      return '<option value="' + k + '"' + sel + '>' + escapeHtml(TEMPLATES[k].label) + '</option>';
    }).join('');

    // 流程层 stop_after 下拉
    var flowStop = cfg.stop_after || '';
    var flowOpts = '<option value=""' + (flowStop === '' ? ' selected' : '') + '>— 跑完 —</option>';
    STAGE_KEYS.forEach(function(k) {
      var sel = flowStop === k ? ' selected' : '';
      flowOpts += '<option value="' + k + '"' + sel + '>' + escapeHtml(STAGE_LABELS[k]) + '（完成后停）</option>';
    });

    var updatedAt = (data && data.updated_at) || '';

    drawer.innerHTML =
      '<header class="slirn-pipe-head">'
      + '<span class="slirn-pipe-title">⚙ 流程配置</span>'
      + '<select class="slirn-pipe-template" id="slirn-pipe-template">'
      + '<option value="default_tpl"' + (curTpl === 'default_tpl' ? ' selected' : '') + '>内置：人工全审</option>'
      + '<option value="semi"' + (curTpl === 'semi' ? ' selected' : '') + '>内置：半自动</option>'
      + '<option value="full"' + (curTpl === 'full' ? ' selected' : '') + '>内置：全自动</option>'
      + '<option value="custom"' + (curTpl === 'custom' ? ' selected' : '') + '>自定义</option>'
      + '</select>'
      + '<button type="button" class="slirn-btn slirn-btn-sm" data-action="pipe-close">✕</button>'
      + '</header>'
      + '<nav class="slirn-pipe-tabs">' + tabsHtml + '</nav>'
      + '<section class="slirn-pipe-body" id="slirn-pipe-body">' + bodyHtml + '</section>'
      + '<footer class="slirn-pipe-foot">'
      + '<label class="slirn-pipe-flow-stop">流程层总停点：'
      + '<select id="slirn-pipe-flow-stop">' + flowOpts + '</select></label>'
      + '<span class="slirn-pipe-updated">'
      + (updatedAt ? '最近保存：' + escapeHtml(updatedAt) : '尚未保存')
      + '</span>'
      + '<button type="button" class="slirn-btn" data-action="pipe-save">💾 保存配置</button>'
      + '<button type="button" class="slirn-btn slirn-btn-danger" data-action="pipe-stop">⏹ 停止</button>'
      + '<button type="button" class="slirn-btn slirn-btn-primary" data-action="pipe-run">▶ 从当前节点运行</button>'
      + '</footer>';

    drawer.hidden = false;
    // 同步选择模板值（与状态分离；data-template 是上次选中）
    var tplSel = document.getElementById('slirn-pipe-template');
    if (tplSel && curTpl) tplSel.value = curTpl;
  }

  function renderStageForm(stage, stageCfg, fullCfg) {
    // 每阶段显示「说明 + 该阶段可选项 + stop_after」
    var commonStop = '<label class="slirn-pipe-field"><span>本阶段完成后停：</span>'
      + stopAfterSelect(stageCfg.stop_after, 'stage-' + stage.key + '-stop')
      + '</label>';
    if (stage.key === 'subtitle_generation') {
      var sdOn = stageCfg.speaker_diarization ? ' checked' : '';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="slirn-pipe-sd" ' + sdOn + '> 区分说话人（默认关 — 单人视频减少误分）'
        + '</label>'
        + commonStop
        + '</div>';
    }
    if (stage.key === 'subtitle_review') {
      var aa = stageCfg.accept_all_suggestions ? ' checked' : '';
      var skip = (stageCfg.skip_categories || []).join(',');
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="slirn-pipe-accept-all" ' + aa + '> 默认接受所有建议（跑完后自动 save_revision 全量 accept）'
        + '</label>'
        + '<label class="slirn-pipe-field"><span>跳过建议类别（逗号分隔，空 = 全接受）：</span>'
        + '<input type="text" id="slirn-pipe-skip-cats" value="' + escapeHtml(skip) + '" placeholder="delete,review">'
        + '</label>'
        + commonStop
        + '</div>';
    }
    if (stage.key === 'rough_cut') {
      var delSpk = (stageCfg.delete_speakers || []).join(',');
      var defDec = stageCfg.default_decision || 'keep';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field"><span>删除某些说话人全部记录（逗号分隔的人员 ID）：</span>'
        + '<input type="text" id="slirn-pipe-del-spk" value="' + escapeHtml(delSpk) + '" placeholder="2,5">'
        + '</label>'
        + '<label class="slirn-pipe-field"><span>默认决策（修订阶段遗留未决策时）：</span>'
        + '<select id="slirn-pipe-def-dec">'
        + '<option value="keep"' + (defDec === 'keep' ? ' selected' : '') + '>保留</option>'
        + '<option value="delete"' + (defDec === 'delete' ? ' selected' : '') + '>删除</option>'
        + '</select></label>'
        + commonStop
        + '</div>';
    }
    if (stage.key === 'rough_compose') {
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<div class="slirn-pipe-hint">粗剪合成无需额外选项；按切分保留区间拼接。必做阶段（REQ-20260918-045）。</div>'
        + commonStop
        + '</div>';
    }
    if (stage.key === 'optimize') {
      var aaR = stageCfg.accept_all_replacements ? ' checked' : '';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="slirn-pipe-accept-rep" ' + aaR + '> 默认接受所有替换（跑完后自动 save_optimize_subtitle 全量 applied）'
        + '</label>'
        + commonStop
        + '</div>';
    }
    return '<div class="slirn-pipe-form">' + commonStop + '</div>';
  }

  function stopAfterSelect(cur, idBase) {
    // per-stage 停点下拉：可设本阶段名或「跑完」
    var html = '<select id="' + idBase + '">';
    html += '<option value="' + STAGE_KEYS[0] + '"'
      + (cur === STAGE_KEYS[0] ? ' selected' : '') + '>字幕生成（完成后停）</option>';
    html += '<option value="subtitle_review"' + (cur === 'subtitle_review' ? ' selected' : '') + '>字幕修订（完成后停）</option>';
    html += '<option value="rough_cut"' + (cur === 'rough_cut' ? ' selected' : '') + '>切分修剪（完成后停）</option>';
    html += '<option value="rough_compose"' + (cur === 'rough_compose' ? ' selected' : '') + '>粗剪合成（完成后停）</option>';
    html += '<option value="optimize"' + (cur === 'optimize' ? ' selected' : '') + '>优化字幕（完成后停）</option>';
    html += '<option value=""' + (!cur ? ' selected' : '') + '>— 跑完 —</option>';
    html += '</select>';
    return html;
  }

  // ---- 表单 → config ----
  // 安全读取字段（DOM 元素可能不在 — 当前只渲染了某一个 tab 的 body）
  function _val(id, fallback) {
    var el = document.getElementById(id);
    return el ? el.value : fallback;
  }
  function _checked(id, fallback) {
    var el = document.getElementById(id);
    return el ? !!el.checked : fallback;
  }
  function readCurrentConfig() {
    var drawer = document.getElementById('slirn-pipe-drawer');
    if (!drawer) return null;
    var cfg = {};
    cfg.subtitle_generation = {
      speaker_diarization: _checked('slirn-pipe-sd', false),
      stop_after: _val('stage-subtitle_generation-stop', 'subtitle_generation') || 'subtitle_generation'
    };
    cfg.subtitle_review = {
      accept_all_suggestions: _checked('slirn-pipe-accept-all', true),
      skip_categories: _val('slirn-pipe-skip-cats', '').split(',').map(function(x){return x.trim();}).filter(Boolean),
      stop_after: _val('stage-subtitle_review-stop', 'subtitle_review') || 'subtitle_review'
    };
    cfg.rough_cut = {
      delete_speakers: _val('slirn-pipe-del-spk', '').split(',').map(function(x){return parseInt(x.trim(), 10);}).filter(function(x){return !isNaN(x);}),
      default_decision: _val('slirn-pipe-def-dec', 'keep') || 'keep',
      stop_after: _val('stage-rough_cut-stop', 'rough_cut') || 'rough_cut'
    };
    cfg.rough_compose = {
      stop_after: _val('stage-rough_compose-stop', 'rough_compose') || 'rough_compose'
    };
    cfg.optimize = {
      accept_all_replacements: _checked('slirn-pipe-accept-rep', true),
      stop_after: _val('stage-optimize-stop', 'optimize') || 'optimize'
    };
    var flowV = _val('slirn-pipe-flow-stop', '');
    cfg.stop_after = flowV || null;
    return cfg;
  }

  // ---- 状态条 ----
  var pipeStatusTimer = null;
  function showStatus(taskId) {
    var box = document.getElementById('slirn-pipe-status');
    if (!box) return;
    box.hidden = false;
    box.setAttribute('data-task-id', taskId);
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
      // running 消失 → 停轮询
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
    // 取最近 3 条
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
  function openDrawer(taskId) {
    postJSON(SLIRN_API + '/pipeline_get', {task_id: taskId}).then(function(r) {
      if (!r || !r.ok) {
        toast('流程配置读取失败：' + (r && r.error || '未知错误'), 'error');
        return;
      }
      var drawer = document.getElementById('slirn-pipe-drawer');
      if (drawer) {
        drawer.setAttribute('data-task-id', taskId);
        drawer.setAttribute('data-active-tab', STAGE_KEYS[0]);
        if (!drawer.getAttribute('data-template')) drawer.setAttribute('data-template', 'default_tpl');
      }
      renderDrawer(taskId, {config: r.config, updated_at: r.updated_at});
      // 已存在 running job → 显示状态条
      if (r.status && r.status.state === 'running') {
        showStatus(taskId);
      }
    });
  }
  function closeDrawer() {
    var drawer = document.getElementById('slirn-pipe-drawer');
    if (drawer) drawer.hidden = true;
  }
  function saveConfig(taskId) {
    var cfg = readCurrentConfig();
    if (!cfg) { toast('未读取到配置', 'error'); return; }
    postJSON(SLIRN_API + '/pipeline_save', {task_id: taskId, config: cfg}).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || '⚙️ 已保存');
        var drawer = document.getElementById('slirn-pipe-drawer');
        if (drawer) {
          drawer.setAttribute('data-template', 'custom');  // 一旦手动编辑 → 切到自定义
        }
      } else {
        toast('保存失败：' + (r && r.error || '未知错误'), 'error');
      }
    });
  }
  function runPipeline(taskId) {
    postJSON(SLIRN_API + '/pipeline_run', {task_id: taskId}).then(function(r) {
      if (!r || !r.ok) { toast('启动失败：' + (r && r.error || '未知错误'), 'error'); return; }
      if (!r.started) {
        toast(r.toast || '已在运行');
        return;
      }
      toast(r.toast || '▶ 已启动');
      showStatus(taskId);
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

  // ---- 全局事件委托（与 router.js 同模式）----
  document.addEventListener('click', function(ev) {
    var t = ev.target;
    if (!t || !t.getAttribute) return;
    var tid = t.getAttribute('data-task-id') || curTaskId();
    var action = t.getAttribute('data-action');

    // tab 切换（按钮无 data-action 时只看 data-pipe-tab）
    var tab = t.getAttribute('data-pipe-tab');
    if (tab && STAGE_KEYS.indexOf(tab) >= 0 && (!action || action === 'pipe-tab')) {
      ev.preventDefault();
      var drawer = document.getElementById('slirn-pipe-drawer');
      if (drawer) {
        drawer.setAttribute('data-active-tab', tab);
        // 重新渲染 body（保留 cfg）
        var body = document.getElementById('slirn-pipe-body');
        var idx = STAGE_KEYS.indexOf(tab);
        var curCfg = readCurrentConfig();
        if (body && curCfg) {
          body.innerHTML = renderStageForm(STAGES[idx], curCfg[tab] || {}, curCfg);
        }
        Array.prototype.forEach.call(drawer.querySelectorAll('.slirn-pipe-tab'), function(el) {
          el.classList.toggle('active', el.getAttribute('data-pipe-tab') === tab);
        });
      }
      return;
    }

    if (!action) return;
    if (action === 'pipe-open') { ev.preventDefault(); openDrawer(tid); return; }
    if (action === 'pipe-close') { closeDrawer(); return; }
    if (action === 'pipe-save') { saveConfig(tid); return; }
    if (action === 'pipe-run') { runPipeline(tid); return; }
    if (action === 'pipe-stop') { stopPipeline(tid); return; }
    if (action === 'pipe-status-collapse') {
      var box = document.getElementById('slirn-pipe-status');
      if (box) box.hidden = true;
      return;
    }
  });

  // ---- 模板切换 ----
  document.addEventListener('change', function(ev) {
    var t = ev.target;
    if (!t || !t.getAttribute) return;
    if (t.id === 'slirn-pipe-template') {
      var drawer = document.getElementById('slirn-pipe-drawer');
      if (!drawer) return;
      var tplKey = t.value;
      drawer.setAttribute('data-template', tplKey);
      if (tplKey !== 'custom' && TEMPLATES[tplKey]) {
        var cfg = TEMPLATES[tplKey].config;
        // 重新渲染整个抽屉（cfg 来自模板）
        var taskId = drawer.getAttribute('data-task-id');
        renderDrawer(taskId, {config: cfg, updated_at: '模板 ' + TEMPLATES[tplKey].label});
        toast('已载入模板：' + TEMPLATES[tplKey].label);
      }
    }
  });

  // ---- 切任务时清空旧状态条 ----
  window.addEventListener('slirn:before-task-switch', function() {
    hideStatus();
    closeDrawer();
  });

  window.slirnPipelineOpen = openDrawer;
  window.slirnPipelineHideStatus = hideStatus;
})();