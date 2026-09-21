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
  // REQ-20260921-NNN：6 阶段（fine_cut = 精剪合成 / 最终导出视频；assets 不进 pipeline）。
  var STAGES = [
    {key: 'subtitle_generation', label: '字幕生成', desc: 'FunASR 识别视频字幕；勾选「区分说话人」可识别人员编号'},
    {key: 'subtitle_review', label: '字幕修订', desc: '大模型分析字幕；可设「自动关联人员ID」「默认接受所有建议」'},
    {key: 'rough_cut', label: '切分修剪', desc: '按修订决策带入保留/更正段；可设「自动关联人员ID」「删除某些说话人」'},
    {key: 'rough_compose', label: '粗剪合成', desc: '按切分保留区间用上游 VideoClipper 合成粗剪成片（必做）'},
    {key: 'optimize', label: '优化字幕', desc: '重新识别粗剪成片字幕，提取不明确字词；可设「全部接受替换」'},
    {key: 'fine_cut', label: '精剪合成', desc: '最终导出视频：封面图/背景图/背景音乐（可选）+ 设置参数模板 + 全篇或区间'}
  ];
  var STAGE_KEYS = STAGES.map(function(s) { return s.key; });
  var STAGE_LABELS = {};
  STAGES.forEach(function(s) { STAGE_LABELS[s.key] = s.label; });

  // ---- 3 套内置模板（v5：顶层 run_mode + stop_after；null = 跑到底）----
  // REQ-20260918-049：subtitle_review 加 rigor 字段
  // REQ-20260921-NNN：加 run_mode + fine_cut（默认 enabled=false）+ link_person_ids
  var TEMPLATES = {
    // 人工全审：字幕修订后停（让用户审 LLM 建议；默认严谨性 = medium）
    default_tpl: {
      label: '人工全审（字幕修订后停）',
      config: {
        subtitle_generation: {speaker_diarization: false},
        subtitle_review: {accept_all_suggestions: false, skip_categories: [], rigor: 'medium', link_person_ids: false},
        rough_cut: {delete_speakers: [], default_decision: 'keep', link_person_ids: false},
        rough_compose: {},
        optimize: {accept_all_replacements: false},
        fine_cut: {enabled: false, cover_image: '', bg_image: '', bgm: '', params_source: 'current', preview_start: 0.0, duration: null},
        run_mode: 'stop_after',
        stop_after: 'subtitle_review'
      }
    },
    // 半自动：粗剪合成后停（让人看完粗剪再决定后续；严谨性 = medium）
    semi: {
      label: '半自动（粗剪合成后停）',
      config: {
        subtitle_generation: {speaker_diarization: false},
        subtitle_review: {accept_all_suggestions: true, skip_categories: [], rigor: 'medium', link_person_ids: true},
        rough_cut: {delete_speakers: [], default_decision: 'keep', link_person_ids: true},
        rough_compose: {},
        optimize: {accept_all_replacements: false},
        fine_cut: {enabled: false, cover_image: '', bg_image: '', bgm: '', params_source: 'current', preview_start: 0.0, duration: null},
        run_mode: 'stop_after',
        stop_after: 'rough_compose'
      }
    },
    // 全自动：跑到底（严谨性 = medium — 全自动模式建议用户手工调高）
    full: {
      label: '全自动（跑到底）',
      config: {
        subtitle_generation: {speaker_diarization: false},
        subtitle_review: {accept_all_suggestions: true, skip_categories: [], rigor: 'medium', link_person_ids: true},
        rough_cut: {delete_speakers: [], default_decision: 'keep', link_person_ids: true},
        rough_compose: {},
        optimize: {accept_all_replacements: true},
        fine_cut: {enabled: false, cover_image: '', bg_image: '', bgm: '', params_source: 'current', preview_start: 0.0, duration: null},
        run_mode: 'to_end',
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

  // ---- 单个 stage 的表单（v5：加 link_person_ids / fine_cut 配置）----
  // REQ-20260921-NNN：fine_cut 是新增的第 6 阶段
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
      var lp = stageCfg.link_person_ids ? ' checked' : '';
      // REQ-20260918-049：4 卡 rigor picker（与工作台 _render_rigor_picker 同源；
      // pipe 通道对 custom 档降级为 medium，desc 标注清楚避免误导）
      var rigorVal = stageCfg.rigor || 'medium';
      var rigorCards = [
        {v: 'high',   t: '高 · 严格打磨', d: '逐句精修到成品口播稿水平：语气词、口癖、重复、无意义寒暄全部处理',
         e: '「嗯呃，那个，就是说，我们今天讲一下神经网络」→ 「我们今天讲一下神经网络」'},
        {v: 'medium', t: '中 · 意思正确即可', d: '只处理影响理解的问题：明显口误、连续重复、明显不通顺的句子做小幅修剪；小语气词可保留',
         e: '「嗯，我们今天讲一下神经网络」→ 整行保留（小语气词「嗯」不影响意思）'},
        {v: 'low',    t: '低 · 只去严重问题', d: '最大限度保留原文：只处理大段纯重复和意思混乱的句子，语气词、轻微口误都保留',
         e: '「所以我们所以我们所以我们看到」→ 「所以我们看到」（只去掉连续重复）'},
        {v: 'custom', t: '自 · 自定义', d: 'pipe 通道暂不支持（需在工作台「字幕修订」有独立 textarea 才能填提示词）；选此项时服务端自动降级为 medium',
         e: '可在工作台自定义严谨性级别后再回 pipe-panel 选择 high/medium/low'}
      ];
      var cardsHtml = '<div class="slirn-pipe-subhead">分析严谨性级别（决定大模型挑毛病的严格程度）：</div>'
        + '<div class="slirn-rigor-cards slirn-pipe-rigor">';
      rigorCards.forEach(function(c) {
        var checked = (rigorVal === c.v) ? ' checked' : '';
        cardsHtml += '<label class="slirn-rigor-card" title="' + escapeHtml(c.d) + '">'
          + '<input type="radio" name="slirn-rev-rigor" value="' + c.v + '"' + checked + ' />'
          + '<span class="slirn-rigor-card-title">' + escapeHtml(c.t) + '</span>'
          + '<span class="slirn-rigor-card-desc">' + escapeHtml(c.d) + '</span>'
          + '<span class="slirn-rigor-card-example">例：' + escapeHtml(c.e) + '</span>'
          + '</label>';
      });
      cardsHtml += '</div>';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        + cardsHtml
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'accept-all') + '"' + aa + '> 默认接受所有建议（跑完后自动 save_revision 全量 accept）'
        + '</label>'
        // REQ-20260921-NNN：自动关联人员ID（调 /rev_speaker_link；不依赖 save_revision 全接受）
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'link-person') + '"' + lp + '> 自动关联人员ID（调 /rev_speaker_link — 字幕生成时需开启「区分说话人」）'
        + '</label>'
        + '<label class="slirn-pipe-field"><span>跳过建议类别（逗号分隔，空 = 全接受）：</span>'
        + '<input type="text" id="' + fieldId(stage.key, 'skip-cats') + '" value="' + escapeHtml(skip) + '" placeholder="delete,review">'
        + '</label>'
        + '</div>';
    }
    if (stage.key === 'rough_cut') {
      var delSpk = (stageCfg.delete_speakers || []).join(',');
      var defDec = stageCfg.default_decision || 'keep';
      var lp2 = stageCfg.link_person_ids ? ' checked' : '';
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
        // REQ-20260921-NNN：自动关联人员ID（仅在 delete_speakers 为空时单独生效）
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'link-person') + '"' + lp2 + '> 自动关联人员ID（仅当上面「删除说话人」为空时生效 — 调 /cut_speaker_link）'
        + '</label>'
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
    // REQ-20260921-NNN：精剪合成（最终导出视频）— 6 阶段最后一站
    if (stage.key === 'fine_cut') {
      var fc0 = stageCfg || {};
      var enabledOn = fc0.enabled ? ' checked' : '';
      var cover = fc0.cover_image || '';
      var bgImg = fc0.bg_image || '';
      var bgmSel = fc0.bgm || '';
      var paramsSrc = fc0.params_source || 'current';
      var startV = (fc0.preview_start != null) ? fc0.preview_start : 0;
      var durV = (fc0.duration != null) ? fc0.duration : '';
      return '<div class="slirn-pipe-form">'
        + '<div class="slirn-pipe-desc">' + escapeHtml(stage.desc) + '</div>'
        // 启用开关（默认关，防误跑几小时重编码）
        + '<label class="slirn-pipe-field">'
        + '<input type="checkbox" id="' + fieldId(stage.key, 'enabled') + '"' + enabledOn + '> 启用自动最终导出（默认关 — 启用会触发几小时重编码）'
        + '</label>'
        // 封面图（手动路径；空=用任务现有）
        + '<label class="slirn-pipe-field"><span>封面图片路径（空=用任务现有）：</span>'
        + '<input type="text" id="' + fieldId(stage.key, 'cover') + '" value="' + escapeHtml(cover) + '" placeholder="封面.jpg">'
        + '</label>'
        // 背景图
        + '<label class="slirn-pipe-field"><span>背景图片路径（空=用任务现有）：</span>'
        + '<input type="text" id="' + fieldId(stage.key, 'bg') + '" value="' + escapeHtml(bgImg) + '" placeholder="背景.jpg">'
        + '</label>'
        // 背景音乐（下拉，loadPanel 时填充）
        + '<label class="slirn-pipe-field"><span>背景音乐（可选；空=不配）：</span>'
        + '<select id="' + fieldId(stage.key, 'bgm') + '" data-bgm-select>'
        + '<option value="">— 不配 —</option>'
        + '<option value="' + escapeHtml(bgmSel) + '" selected>' + escapeHtml(bgmSel || '(已选)') + '</option>'
        + '</select></label>'
        // 设置参数模板（下拉，loadPanel 时填充）
        + '<label class="slirn-pipe-field"><span>设置参数（=精剪面板保存的全局模板）：</span>'
        + '<select id="' + fieldId(stage.key, 'params-src') + '" data-params-select>'
        + '<option value="current"' + (paramsSrc === 'current' ? ' selected' : '') + '>当前参数</option>'
        + '</select></label>'
        // 导出区间（秒）
        + '<label class="slirn-pipe-field"><span>导出起点（秒，0=全篇）：</span>'
        + '<input type="number" id="' + fieldId(stage.key, 'start') + '" min="0" step="0.1" value="' + Number(startV) + '">'
        + '</label>'
        + '<label class="slirn-pipe-field"><span>导出时长（秒，空=全篇）：</span>'
        + '<input type="number" id="' + fieldId(stage.key, 'dur') + '" min="0" step="0.1" value="' + escapeHtml(String(durV)) + '" placeholder="留空=全篇">'
        + '</label>'
        + '<div class="slirn-pipe-hint">⚠ 启用后会自动调 /export_fine_video，输出 fine_export.mp4。默认关。</div>'
        + '</div>';
    }
    return '<div class="slirn-pipe-form"></div>';
  }

  // ---- 整个面板渲染（v4：details 包裹整个面板 + 头部下拉 + 每阶段 run-since 按钮）----
  // REQ-20260918-049：data.history 透传进来，按钮文案自适应（智能续跑 / 从头跑）
  function renderPanel(taskId, data) {
    var panel = document.getElementById('slirn-pipe-panel');
    if (!panel) return;
    panel.setAttribute('data-task-id', taskId);
    var cfg = (data && data.config) || {};
    var updatedAt = (data && data.updated_at) || '';
    var history = (data && data.history) || [];

    // v4 兼容：v3 per-stage boolean / v2 per-stage string 字段丢弃；顶层 stop_after 提升
    cfg = normalizeCfg(cfg);
    var flowStopValue = cfg.stop_after || '';

    // REQ-20260918-049：智能续跑按钮（自适应文案）
    var sinceKey = computeNextSince(history);
    var runBtnHtml = sinceKey
      ? '<button type="button" class="slirn-btn slirn-btn-primary slirn-btn-sm slirn-pipe-run"'
        + ' data-action="pipe-run" data-since="' + sinceKey + '"'
        + ' title="上次跑到了「' + escapeHtml(STAGE_LABELS[sinceKey] || sinceKey) + '」之前；点此从该处开始（跳过已完成阶段）">'
        + '▶ 续跑 (从「' + escapeHtml(STAGE_LABELS[sinceKey] || sinceKey) + '」开始)</button>'
      : '<button type="button" class="slirn-btn slirn-btn-primary slirn-btn-sm slirn-pipe-run"'
        + ' data-action="pipe-run"'
        + ' title="保存并按当前配置顺序执行所有阶段">▶ 从头跑</button>';

    // REQ-20260921-NNN：清理所有阶段产物按钮 — 加在「字幕生成」section 上方
    // 两次确认（按钮旁 warning 文案 + onClick 内 confirm 弹窗）。
    var resetBlockHtml =
      '<div class="slirn-pipe-reset-block">'
      + '<button type="button" class="slirn-btn slirn-btn-danger slirn-btn-sm slirn-pipe-reset"'
      + ' data-action="pipe-reset-stages"'
      + ' title="删除本任务所有阶段的生成产物（subtitle.json / revision.json / cutlist.json / rough_compose.mp4 / optimize_subtitle.json / fine_compose.json / fine_export*.mp4 等），并清空内存中的 stages_done 与 execution_history。原视频 / 时间截取 / 热词 / 任务 metadata 保留。">'
      + '🧹 清理所有阶段产物</button>'
      + '<div class="slirn-pipe-reset-warning">'
      + '⚠ 清理后：字幕 / 修订 / 切分 / 粗剪 / 优化 / 精剪合成 6 个阶段的所有产物都会被删除，'
      + '内存中的「已完成阶段」标记也会清空。下次跑流程会从头开始。'
      + '</div>'
      + '</div>';

    var sectionsHtml = resetBlockHtml + STAGES.map(function(stage, idx) {
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
      // REQ-20260921-NNN：run_mode 顶层下拉（to_end / stop_after）
      // - to_end 模式：flow-stop 下拉禁用（强制跑完所有阶段）
      // - stop_after 模式：flow-stop 下拉启用（用户选择停在哪）
      + '<label class="slirn-pipe-run-mode-label">'
      + '运行模式：'
      + '<select class="slirn-pipe-run-mode" id="slirn-pipe-run-mode" data-pipe-action="run-mode">'
      + '<option value="stop_after"' + (cfg.run_mode !== 'to_end' ? ' selected' : '') + '>停在指定阶段</option>'
      + '<option value="to_end"' + (cfg.run_mode === 'to_end' ? ' selected' : '') + '>一键跑到底</option>'
      + '</select>'
      + '</label>'
      + '<label class="slirn-pipe-flow-stop-label">'
      + '完成到哪个阶段停：'
      + '<select class="slirn-pipe-flow-stop" id="slirn-pipe-flow-stop"' + (cfg.run_mode === 'to_end' ? ' disabled' : '') + '>'
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
      + '<button type="button" class="slirn-btn slirn-btn-sm" data-action="pipe-status-show" hidden title="显示状态条（点状态条 ▾ 收起后点此恢复）">📊 状态</button>'
      + runBtnHtml
      + '</span>'
      + '</summary>'
      + '<div class="slirn-pipe-sections">' + sectionsHtml + '</div>'
      + '</details>';
    // REQ-20260918-049 v2：渲染完后同步「📊 状态」按钮可见性（status hidden 时显示）
    refreshStatusToggleBtn();
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

  // ---- REQ-20260921-NNN：填充 fine_cut 下拉（背景音乐 + 设置参数模板）----
  // loadPanel 末尾调用，调用 _pipePanelPopulateDeps(taskId)。
  // - bgm：从 /list_bgm_files 拿默认 + 上传；保留面板当前选中
  // - params：从 /list_fine_global_profiles 拿模板；追加「当前参数」/「导入 JSON」选项
  var _pipePanelPopulateDeps = async function(taskId) {
    var bgmSel = document.querySelector('select[data-bgm-select]');
    var paramSel = document.querySelector('select[data-params-select]');
    if (bgmSel) {
      // 读出当前选中（若存在），加载完后恢复
      var curBgm = bgmSel.value || '';
      try {
        var r = await fetch('/slirn/api/list_bgm_files', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({task_id: taskId || ''})
        });
        var j = await r.json();
        bgmSel.innerHTML = '';
        var noneOpt = document.createElement('option');
        noneOpt.value = ''; noneOpt.textContent = '— 不配 —';
        bgmSel.appendChild(noneOpt);
        var files = (j && j.ok && j.files) ? j.files : [];
        files.forEach(function(f) {
          var opt = document.createElement('option');
          opt.value = f.name || f.path || '';
          opt.textContent = (f.kind === 'uploaded' ? '📁 ' : '🎵 ') + (f.label || f.name || '');
          if ((f.name || f.path) === curBgm) opt.selected = true;
          bgmSel.appendChild(opt);
        });
        if (!curBgm) bgmSel.value = '';
      } catch (e) { console.warn('[pipe-bgm-load]', e); }
    }
    if (paramSel) {
      var curParam = paramSel.value || 'current';
      try {
        var r2 = await fetch('/slirn/api/list_fine_global_profiles', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({})
        });
        var j2 = await r2.json();
        paramSel.innerHTML = '';
        var curOpt = document.createElement('option');
        curOpt.value = 'current'; curOpt.textContent = '当前参数（用面板保存的）';
        if (curParam === 'current') curOpt.selected = true;
        paramSel.appendChild(curOpt);
        var profs = (j2 && j2.profiles) ? j2.profiles : [];
        profs.forEach(function(p) {
          var opt = document.createElement('option');
          var id = p.id || p.name || '';
          opt.value = 'template:' + id;
          opt.textContent = '📋 模板：' + (p.name || id);
          if (curParam === 'template:' + id) opt.selected = true;
          paramSel.appendChild(opt);
        });
        var importOpt = document.createElement('option');
        importOpt.value = 'import'; importOpt.textContent = '📥 导入 JSON（手动）';
        if (curParam === 'import') importOpt.selected = true;
        paramSel.appendChild(importOpt);
      } catch (e) { console.warn('[pipe-params-load]', e); }
    }
  };

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
      accept_all_suggestions: _checked(fieldId('subtitle_review', 'accept-all'), false),
      // REQ-20260921-NNN：自动关联人员ID（后端 handler_subtitle_review 末尾触发 /rev_speaker_link）
      link_person_ids: _checked(fieldId('subtitle_review', 'link-person'), false),
      skip_categories: _val(fieldId('subtitle_review', 'skip-cats'), '').split(',').map(function(x){return x.trim();}).filter(Boolean),
      // REQ-20260918-049：从 radio cards 读 rigor（pipe 通道对 custom 档降级为 medium，
      // 与后端 handler_subtitle_review 校验一致）
      rigor: (function() {
        var el = document.querySelector('input[name="slirn-rev-rigor"]:checked');
        var v = el ? el.value : 'medium';
        return (v === 'high' || v === 'medium' || v === 'low') ? v : 'medium';
      })()
    };
    cfg.rough_cut = {
      delete_speakers: _val(fieldId('rough_cut', 'del-spk'), '').split(',').map(function(x){return parseInt(x.trim(), 10);}).filter(function(x){return !isNaN(x);}),
      default_decision: _val(fieldId('rough_cut', 'def-dec'), 'keep') || 'keep',
      // REQ-20260921-NNN：自动关联人员ID（与 delete_speakers 互斥，handler 优先 delete_speakers）
      link_person_ids: _checked(fieldId('rough_cut', 'link-person'), false)
    };
    cfg.rough_compose = {};
    cfg.optimize = {
      accept_all_replacements: _checked(fieldId('optimize', 'accept-rep'), false)
    };
    // REQ-20260921-NNN：精剪合成（最终导出视频）— 默认 enabled=False 防误触发
    var fcEnabled = _checked(fieldId('fine_cut', 'enabled'), false);
    var fcBgm = _val(fieldId('fine_cut', 'bgm'), '') || '';
    var fcParams = _val(fieldId('fine_cut', 'params-src'), 'current') || 'current';
    var fcStartRaw = _val(fieldId('fine_cut', 'start'), '0');
    var fcDurRaw = _val(fieldId('fine_cut', 'dur'), '');
    var fcStart = parseFloat(fcStartRaw);
    var fcDur = fcDurRaw === '' ? null : parseFloat(fcDurRaw);
    cfg.fine_cut = {
      enabled: fcEnabled,
      cover_image: _val(fieldId('fine_cut', 'cover'), '') || '',
      bg_image: _val(fieldId('fine_cut', 'bg'), '') || '',
      bgm: fcBgm,
      params_source: fcParams,
      preview_start: isNaN(fcStart) ? 0 : fcStart,
      duration: (fcDur === null || isNaN(fcDur)) ? null : fcDur
    };
    // v5 顶层 run_mode + stop_after：
    // - run_mode="to_end" → 服务端 validate_config 强制 stop_after=None
    // - run_mode="stop_after" → stop_after 由用户选（"" → null = 跑到结尾但不停）
    var runMode = _val('slirn-pipe-run-mode', 'stop_after');
    cfg.run_mode = (runMode === 'to_end' || runMode === 'stop_after') ? runMode : 'stop_after';
    var flowStop = _val('slirn-pipe-flow-stop', '');
    cfg.stop_after = flowStop || null;
    return cfg;
  }

  // ---- REQ-20260918-049：智能续跑 + 阶段状态回显 helper ----
  // 从 history 最后一条 summary 算出「下一该跑的 stage key」。
  // history 顺序：append_history 是 append 到尾部，最新 summary = history[-1]（非 reverse）。
  // 返回 null 表示「从头跑」（从未跑过 / 首阶段就挂 / 5 阶段全跑完）。
  function computeNextSince(history) {
    if (!Array.isArray(history) || history.length === 0) return null;
    var last = history[history.length - 1];
    if (!last || typeof last !== 'object') return null;
    var done = Array.isArray(last.stages_done) ? last.stages_done : [];
    // 全跑完 → 从头跑
    if (done.length >= STAGE_KEYS.length) return null;
    // 取 STAGE_KEYS 里最后一个已被 done 覆盖的索引 + 1 = 下个该跑
    var lastIdx = -1;
    for (var i = 0; i < done.length; i++) {
      var ix = STAGE_KEYS.indexOf(done[i]);
      if (ix > lastIdx) lastIdx = ix;
    }
    return STAGE_KEYS[lastIdx + 1] || null;
  }

  // 从 status 对象推导已完成的 stage key 列表：
  // 1) summary.stages_done（已完成 run 才有）→ 2) log 里 '✅ X 完成' 标记（running 中）
  function deriveStagesDone(st) {
    if (!st) return [];
    if (st.summary && Array.isArray(st.summary.stages_done)) {
      return st.summary.stages_done.slice();
    }
    var done = [];
    var seen = {};
    var log = Array.isArray(st.log) ? st.log : [];
    for (var i = 0; i < log.length; i++) {
      var e = log[i] || {};
      var k = e.stage, m = e.msg || '';
      if (!k || seen[k]) continue;
      // 通用判定：✅/完成 字样视为该阶段已完成
      if (m.indexOf('✅') >= 0 && m.indexOf('完成') >= 0) {
        seen[k] = 1;
        done.push(k);
      }
    }
    return done;
  }

  // 单个 stage 当前状态：error / running / done / pending
  function stageStateOf(stageKey, st, doneSet, currentStage) {
    if (!st) return 'pending';
    if (st.state === 'error' && currentStage === stageKey) return 'error';
    if (st.state === 'running' && currentStage === stageKey) return 'running';
    if (doneSet.indexOf(stageKey) >= 0) return 'done';
    return 'pending';
  }

  // 格式化时长（秒 → "X秒" / "X分Y秒" / "X时Y分"）
  function fmtElapsed(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    if (seconds < 60) return seconds + '秒';
    if (seconds < 3600) return Math.floor(seconds / 60) + '分' + (seconds % 60) + '秒';
    return Math.floor(seconds / 3600) + '时' + Math.floor((seconds % 3600) / 60) + '分';
  }

  // 给每个 stage section 加状态徽章（⏳/✅/⏸/❌）+ 当前阶段百分比 + 已耗时。
  // 接入 pollStatus（running 中轮询）和 loadPanel（idle 终态读 history）两路径。
  function updateStageBadges(st) {
    if (!st) return;
    var doneSet = deriveStagesDone(st);
    var currentStage = st.current_stage || null;
    STAGES.forEach(function(stage, idx) {
      var sec = document.querySelector(
        '#slirn-pipe-panel .slirn-pipe-section[data-pipe-section="' + stage.key + '"]'
      );
      if (!sec) return;
      var s = stageStateOf(stage.key, st, doneSet, currentStage);
      sec.setAttribute('data-pipe-state', s);

      // num 圆形：done → ✓，running → ⏳，其他 → 原始序号
      var num = sec.querySelector('.slirn-pipe-section-num');
      if (num) {
        num.textContent = s === 'done' ? '✓' : s === 'running' ? '⏳' : String(idx + 1);
      }

      // 徽章：在 label 后插/更新
      var lbl = sec.querySelector('.slirn-pipe-section-label');
      if (!lbl) return;
      var badge = sec.querySelector('.slirn-pipe-section-state');
      if (!badge) {
        badge = document.createElement('span');
        // 插在 label 之后（hint 之前）
        if (lbl.nextSibling) {
          lbl.parentNode.insertBefore(badge, lbl.nextSibling);
        } else {
          lbl.parentNode.appendChild(badge);
        }
      }
      badge.className = 'slirn-pipe-section-state slirn-pipe-section-state-' + (
        s === 'running' ? 'running' :
        s === 'done'    ? 'done'    :
        s === 'error'   ? 'error'   : 'pending'
      );
      var pctTxt = '';
      if (s === 'running' && st.percent != null) {
        pctTxt = ' · ' + Math.round(Number(st.percent || 0)) + '%';
      }
      var elapsedTxt = '';
      if (s === 'running' && st.started_at) {
        elapsedTxt = ' · 已耗时 ' + fmtElapsed((Date.now() / 1000) - Number(st.started_at));
      }
      var textMap = {
        running: '⏳ 运行中' + pctTxt + elapsedTxt,
        done:    '✅ 已完成',
        error:   '❌ 出错',
        pending: '⏸ 待执行'
      };
      badge.textContent = textMap[s] || s;
    });
  }

  // ---- 状态条 ----
  var pipeStatusTimer = null;
  // 状态条位置持久化 key（per-task，跨 session 保留）
  function _statusPosKey() {
    var t = curTaskId() || 'global';
    return 'slirn-pipe-status-pos-' + t;
  }
  function _restoreStatusPos(box) {
    if (!box) return;
    try {
      var raw = localStorage.getItem(_statusPosKey());
      if (!raw) return;
      var p = JSON.parse(raw);
      if (typeof p.top === 'number')   box.style.top   = p.top + 'px';
      if (typeof p.left === 'number')  box.style.left  = p.left + 'px';
      // 用 left/top 定位后清掉 right，避免冲突
      box.style.right = 'auto';
      box.style.bottom = 'auto';
    } catch (e) { /* localStorage 不可用时静默 */ }
  }
  function _saveStatusPos(box) {
    if (!box) return;
    try {
      var rect = box.getBoundingClientRect();
      localStorage.setItem(_statusPosKey(), JSON.stringify({
        top: Math.round(rect.top),
        left: Math.round(rect.left)
      }));
    } catch (e) { /* 静默 */ }
  }
  // 状态条拖动：head 是手柄（除了 collapse 按钮）
  function _bindStatusDrag(box) {
    if (!box || box.__dragBound) return;
    var head = box.querySelector('.slirn-pipe-status-head');
    if (!head) return;  // 没有 head 就不要标记，等 renderStatusBar 写入后再绑
    box.__dragBound = true;
    var startX = 0, startY = 0, startLeft = 0, startTop = 0, dragging = false;
    head.addEventListener('pointerdown', function(ev) {
      // 拖手柄排除：collapse 按钮本身（它有 data-action）
      if (ev.target.closest('[data-action]')) return;
      dragging = true;
      startX = ev.clientX;
      startY = ev.clientY;
      var rect = box.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      // 用 left/top 定位（清掉 right/bottom）
      box.style.left = startLeft + 'px';
      box.style.top = startTop + 'px';
      box.style.right = 'auto';
      box.style.bottom = 'auto';
      try { head.setPointerCapture(ev.pointerId); } catch (e) {}
      ev.preventDefault();
    });
    head.addEventListener('pointermove', function(ev) {
      if (!dragging) return;
      var dx = ev.clientX - startX;
      var dy = ev.clientY - startY;
      var newLeft = Math.max(0, Math.min(window.innerWidth - 100,  startLeft + dx));
      var newTop  = Math.max(0, Math.min(window.innerHeight - 40, startTop  + dy));
      box.style.left = newLeft + 'px';
      box.style.top = newTop + 'px';
    });
    var _endDrag = function(ev) {
      if (!dragging) return;
      dragging = false;
      _saveStatusPos(box);
      try { head.releasePointerCapture(ev.pointerId); } catch (e) {}
    };
    head.addEventListener('pointerup', _endDrag);
    head.addEventListener('pointercancel', _endDrag);
  }
  function showStatus(taskId) {
    var box = document.getElementById('slirn-pipe-status');
    if (!box) return;
    box.hidden = false;
    box.setAttribute('data-task-id', taskId);
    _restoreStatusPos(box);
    _bindStatusDrag(box);
    refreshStatusToggleBtn();
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
    refreshStatusToggleBtn();
  }
  // pipe-panel 头部「📊 显示状态」按钮的可见性（仅 hidden 时显示）
  function refreshStatusToggleBtn() {
    var box = document.getElementById('slirn-pipe-status');
    var btn = document.querySelector('#slirn-pipe-panel [data-action="pipe-status-show"]');
    if (!btn) return;
    btn.hidden = !(box && box.hidden);
  }
  function pollStatus(taskId) {
    postJSON(SLIRN_API + '/pipeline_status', {task_id: taskId}).then(function(r) {
      if (!r || !r.ok) return;
      renderStatusBar(r);
      // REQ-20260918-049：每个 stage section 加徽章回显
      updateStageBadges(r);
      if (r.state !== 'running') {
        if (pipeStatusTimer) { clearInterval(pipeStatusTimer); pipeStatusTimer = null; }
        // 终态再画一次（确保徽章显示最终态而非中间态）
        updateStageBadges(r);
        // REQ-20260918-049 v3：终态后刷新头部「续跑」按钮（让按钮反映最新 stages_done）
        refreshRunBtnFromStatus(r);
      }
    });
  }
  // REQ-20260918-049 v3：终态后从 status 推导 stages_done，更新头部按钮（避免按钮停在「从头跑」误判）
  function refreshRunBtnFromStatus(r) {
    var btn = document.querySelector('#slirn-pipe-panel [data-action="pipe-run"]');
    if (!btn) return;
    var done = deriveStagesDone(r);
    var fakeHistory = [{stages_done: done, status: r.state}];
    var sinceKey = computeNextSince(fakeHistory);
    if (sinceKey) {
      btn.setAttribute('data-since', sinceKey);
      btn.textContent = '▶ 续跑 (从「' + (STAGE_LABELS[sinceKey] || sinceKey) + '」开始)';
      btn.setAttribute('title', '上次跑到了「' + (STAGE_LABELS[sinceKey] || sinceKey) + '」之前；点此从该处开始（跳过已完成阶段）');
    } else {
      btn.removeAttribute('data-since');
      btn.textContent = '▶ 从头跑';
      btn.setAttribute('title', '保存并按当前配置顺序执行所有阶段');
    }
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
      '<div class="slirn-pipe-status-head" title="拖动此处移动状态条">'
      + '<span class="slirn-pipe-status-state slirn-pipe-status-state-' + st.state + '">'
      + (st.state === 'running' ? '⏳' : (st.state === 'done' ? '✅' : (st.state === 'error' ? '❌' : (st.state === 'stopped' ? '⏹' : '•'))))
      + ' ' + escapeHtml(stateLabel) + '</span>'
      + '<span class="slirn-pipe-status-stage">当前阶段：' + escapeHtml(stageLabel) + '</span>'
      + '<progress class="slirn-pipe-status-bar" max="100" value="' + pct + '"></progress>'
      + '<button type="button" class="slirn-pipe-status-collapse" data-action="pipe-status-collapse" title="收起状态条（在流程配置头部按钮恢复显示）">▾</button>'
      + '</div>'
      + (last3 ? '<div class="slirn-pipe-status-log">' + last3 + '</div>' : '')
      + (st.error ? '<div class="slirn-pipe-status-err">' + escapeHtml(st.error) + '</div>' : '');
    // 重渲染时重新绑拖动事件（innerHTML 替换后旧 listener 不在了）
    _bindStatusDrag(box);
  }

  // ---- 入口 ----
  function loadPanel(taskId) {
    postJSON(SLIRN_API + '/pipeline_get', {task_id: taskId}).then(function(r) {
      if (!r || !r.ok) {
        toast('流程配置读取失败：' + (r && r.error || '未知错误'), 'error');
        return;
      }
      // REQ-20260918-049：透传 history 给 renderPanel（智能按钮文案 + 兜底徽章）
      renderPanel(taskId, {
        config: r.config,
        updated_at: r.updated_at,
        history: r.history || []
      });
      // 状态条 + 阶段徽章回显
      if (r.status && r.status.state === 'running') {
        showStatus(taskId);
        // running 时立刻画一次徽章（不等下一轮 poll）
        updateStageBadges(r.status);
      } else if (r.status) {
        // idle 态但内存有 job（如刚 stop 完）→ 用 last status 画徽章
        updateStageBadges(r.status);
      } else {
        // 没内存 job 但有 history → 用 history 最后一条做兜底
        var last = (r.history || []).slice(-1)[0];
        if (last) {
          updateStageBadges({
            state: 'idle',
            current_stage: null,
            log: [],
            summary: last
          });
        }
      }
      // REQ-20260920-082：loadPanel 完成时触发默认 BGM 列表加载。
      // 修复 REQ-20260920-078 遗留 bug：fineDefaultBgmLoad 函数存在但从未被调用，
      // 导致下拉只有「— 不选 —」一个 option。
      if (typeof window.fineDefaultBgmLoad === 'function') {
        try { window.fineDefaultBgmLoad(); } catch (e) { console.warn('[bgm-load]', e); }
      }
      // REQ-20260921-NNN：填充 fine_cut 阶段的背景音乐 + 设置参数模板下拉
      try { _pipePanelPopulateDeps(taskId); } catch (e) { console.warn('[pipe-deps-load]', e); }
      // REQ-20260920-084：检查是否有 in-flight export job，有则挂回进度条
      // （页面刷新 / 服务重启后自动恢复进度显示）
      try {
        fetch(SLIRN_API + '/active_export_for_task?task_id=' + encodeURIComponent(taskId))
          .then(function(r) { return r.json(); })
          .then(function(j) {
            if (!j || !j.ok || !j.job) return;
            if (j.job.state !== 'queued' && j.job.state !== 'running') return;
            var btn = document.getElementById('slirn-fine-export-btn');
            if (btn && typeof startFineExportInline === 'function') {
              startFineExportInline(taskId, j.job.job_id, btn);
              if (j.job.source === 'disk' && j.job.warning) {
                toast('⚠ ' + j.job.warning, 'warn');
              }
            }
          })
          .catch(function(e) { console.warn('[active_export_for_task]', e); });
      } catch (e) { /* 静默：不影响主流程 */ }
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

  // ---- REQ-20260921-NNN：清理所有阶段产物（两次确认）----
  function resetStages(taskId) {
    if (!taskId) { toast('缺少 task_id', 'error'); return; }
    // 第一次确认：解释会删哪些东西
    var firstConfirm = window.confirm(
      '🧹 确认要清理本任务所有阶段的生成产物吗？\n\n'
      + '会删除以下产物：\n'
      + '• 字幕生成：subtitle.json\n'
      + '• 字幕修订：revision.json + rev_speaker_link.json\n'
      + '• 切分修剪：cutlist.json + cut_speaker_link.json\n'
      + '• 粗剪合成：rough_compose.mp4\n'
      + '• 优化字幕：optimize_subtitle.json（+ 旧流程 fine_revision.json）\n'
      + '• 精剪合成：fine_compose.json + fine_export*.mp4 + fine_preview*.mp4\n'
      + '• 内存中的「已完成阶段」标记 + 执行历史 + 任务状态回退到 DRAFT\n\n'
      + '会保留：原视频 / 时间截取 / 热词 / 任务 metadata。'
    );
    if (!firstConfirm) return;
    // 第二次确认：最后一道
    var secondConfirm = window.confirm(
      '⚠ 最后确认：此操作不可撤销。\n\n'
      + '真的要清理全部阶段产物吗？\n'
      + '（点取消则保留，下次跑流程会从上次完成的地方继续）'
    );
    if (!secondConfirm) return;
    // 真的发了
    postJSON(SLIRN_API + '/pipeline_reset_stages', {task_id: taskId}).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || ('🧹 已清理 ' + (r.deleted || []).length + ' 个产物'));
        // 刷新面板：让 stages_done / history 全部重新加载（清空后）
        loadPanel(taskId);
        // REQ-20260921-NNN：服务端把 t.status 也降回 DRAFT 后，工作台顶部
        // 「阶段」tab 的绿色对号（_wb_stage_states 看 t.status + 磁盘产物）
        // 必须整页重渲才消失。loadPanel 只刷流程配置面板，不刷 wb —— 显式
        // 触发 openWorkbench 重拉整张 wb HTML（router.js: window.slirnOpenWorkbench）。
        if (typeof window.slirnOpenWorkbench === 'function') {
          try { window.slirnOpenWorkbench(taskId); } catch (e) {}
        }
      } else {
        toast('清理失败：' + (r && r.error || '未知错误'), 'error');
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
    if (action === 'pipe-run') {
      ev.preventDefault();
      // REQ-20260918-049：智能按钮 data-since 在按钮上（renderPanel 按 history 自适应生成）
      var since = t.getAttribute('data-since') || null;
      runPipeline(tid, since);
      return;
    }
    if (action === 'pipe-run-since') {
      ev.preventDefault();
      var since = t.getAttribute('data-since') || null;
      runPipeline(tid, since);
      return;
    }
    if (action === 'pipe-stop') { ev.preventDefault(); stopPipeline(tid); return; }
    if (action === 'pipe-reset-stages') { ev.preventDefault(); resetStages(tid); return; }
    if (action === 'pipe-open') { ev.preventDefault(); loadPanel(tid); return; }
    if (action === 'pipe-status-collapse') {
      ev.preventDefault();
      hideStatus();
      return;
    }
    if (action === 'pipe-status-show') {
      ev.preventDefault();
      // 重新显示状态条：恢复保存的位置并立即拉一次状态
      var box = document.getElementById('slirn-pipe-status');
      if (!box) return;
      box.hidden = false;
      _restoreStatusPos(box);
      _bindStatusDrag(box);
      refreshStatusToggleBtn();
      // 立即拉一次 + 起轮询
      var t = box.getAttribute('data-task-id') || tid;
      if (t) {
        if (pipeStatusTimer) { clearInterval(pipeStatusTimer); pipeStatusTimer = null; }
        pipeStatusTimer = setInterval(function() { pollStatus(t); }, 1500);
        pollStatus(t);
      }
      return;
    }
  });

  // ---- 模板切换 + run_mode 切换：v4/v5 直接重渲染整个面板 ----
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
      return;
    }
    // REQ-20260921-NNN：run_mode 切换 — to_end → 禁用 flow-stop；stop_after → 启用
    if (t.id === 'slirn-pipe-run-mode') {
      var flowSel = document.getElementById('slirn-pipe-flow-stop');
      if (!flowSel) return;
      if (t.value === 'to_end') {
        flowSel.value = '';  // 强制选「不停（跑到底）」
        flowSel.disabled = true;
        toast('已切到「一键跑到底」：流程会跑完全部 6 阶段');
      } else {
        flowSel.disabled = false;
        toast('已切到「停在指定阶段」：从下面下拉选择停在哪个阶段');
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
  // REQ-20260918-049：把徽章更新函数暴露到 window（E2E 测试用，可手动触发刷新）
  window.slirnPipelineUpdateStageBadges = updateStageBadges;
})();