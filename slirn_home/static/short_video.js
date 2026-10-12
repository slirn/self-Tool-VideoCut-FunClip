// REQ-20261003-098：单源 AI 短视频拆条 6 阶段管线（前端）
(function() {
  if (window.__slirnShortVideoBound) return;
  window.__slirnShortVideoBound = true;

  var API = '/slirn/api';

  function postJSON(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify(payload || {})
    }).then(function(r) {
      return r.json().catch(function() {
        return {ok: false, error: 'HTTP ' + r.status};
      });
    }).catch(function(e) {
      return {ok: false, error: String(e)};
    });
  }

  function postForm(url, formData) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData
    }).then(function(r) {
      return r.json().catch(function() {
        return {ok: false, error: 'HTTP ' + r.status};
      });
    }).catch(function(e) {
      return {ok: false, error: String(e)};
    });
  }

  function root() { return document.getElementById('slirn-tab-short-video'); }

  function setHTML(html) {
    var r = root();
    if (!r) return;
    r.innerHTML = html || '<div class="slirn-empty">加载失败</div>';
  }

  function toast(msg, type) {
    if (window.slirnToast) return window.slirnToast(msg, type || 'success');
    console.log(msg);
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ---------- 阶段已用时 ticker（REQ-20261004-UX） ----------
  // 每个 [data-stage-elapsed] 元素带 data-started-at（ISO）。每秒刷新一次文本。
  // 用 setInterval（不是 setTimeout 链，参见 REQ-093 经验）。
  function _fmtElapsed(sec) {
    if (sec < 0 || !isFinite(sec)) return '0:00';
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = Math.floor(sec % 60);
    if (h > 0) return h + ':' + (m < 10 ? '0' + m : m) + ':' + (s < 10 ? '0' + s : s);
    return m + ':' + (s < 10 ? '0' + s : s);
  }
  function _parseIsoSafe(s) {
    if (!s) return 0;
    var t = Date.parse(s);
    return isNaN(t) ? 0 : t;
  }
  function _tickElapsed() {
    var nodes = document.querySelectorAll('[data-stage-elapsed]');
    if (!nodes.length) return;
    var now = Date.now();
    nodes.forEach(function(el) {
      var started = _parseIsoSafe(el.getAttribute('data-started-at'));
      if (!started) { el.textContent = '0:00'; return; }
      el.textContent = _fmtElapsed((now - started) / 1000);
    });
  }
  function startElapsedTicker() {
    if (window.__slirnElapsedTicker) return;
    _tickElapsed();  // 先跑一次，避免空白 1 秒
    window.__slirnElapsedTicker = setInterval(_tickElapsed, 1000);
  }
  function stopElapsedTickerIfIdle() {
    if (!window.__slirnElapsedTicker) return;
    if (!document.querySelector('[data-stage-elapsed]')) {
      clearInterval(window.__slirnElapsedTicker);
      window.__slirnElapsedTicker = null;
    }
  }

  function innerCtx() {
    var inner = document.getElementById('slirn-short-video-inner');
    if (!inner) return null;
    return {
      taskId: inner.getAttribute('data-task-id') || '',
      projectId: inner.getAttribute('data-project-id') || ''
    };
  }

  // ---------- 列表 / 创建 / 打开 / 删除 ----------

  // 刷新保持当前页面（2026-10-12 修订）：记住短视频 tab 里最后打开的项目；
  // 「返回项目列表」/删除项目会清空记忆。router.js 刷新后恢复短视频 tab 时，
  // slirnShortVideoLoad 会按记忆直接重进项目编辑页而不是列表。
  var SV_VIEW_KEY = 'slirn.sv.view';
  function _saveSvView(taskId, projectId) {
    try {
      localStorage.setItem(SV_VIEW_KEY,
        JSON.stringify({t: taskId || '', p: projectId || ''}));
    } catch (e) { /* localStorage 不可用 → 仅失去记忆功能 */ }
  }
  function _readSvView() {
    try { return JSON.parse(localStorage.getItem(SV_VIEW_KEY) || 'null'); }
    catch (e) { return null; }
  }

  function switchSvStage(stageKey) {
    var root = document.getElementById('slirn-short-video-inner');
    if (!root || !stageKey) return;
    root.querySelectorAll('.slirn-sv-stage-nav').forEach(function(el) {
      var active = el.getAttribute('data-stage-key') === stageKey;
      el.classList.toggle('active', active);
      el.setAttribute('aria-current', active ? 'step' : 'false');
    });
    root.querySelectorAll('.slirn-sv-stage-pane').forEach(function(el) {
      el.classList.toggle('active', el.getAttribute('data-stage-key') === stageKey);
    });
  }

  function loadList(optScope) {
    // REQ-20261012-099 修订四：scope（我的任务/全部任务，仅 admin 有 radio）。
    // 不传时读当前选中 radio；都没有 → 交给后端按登录态决定。
    var scope = optScope || '';
    if (!scope) {
      var sR = document.querySelector('input[name="slirn-sv-scope"]:checked');
      if (sR) scope = sR.value;
    }
    _saveSvView('', '');
    postJSON(API + '/short_video_list', {scope: scope || 'all'}).then(function(r) {
      if (r && r.ok) setHTML(r.html);
      else setHTML('<div class="slirn-empty">' + esc((r && r.error) || '加载失败') + '</div>');
      stopElapsedTickerIfIdle();
    });
  }
  window.slirnShortVideoLoad = function(forceList) {
    if (forceList) return loadList();
    var v = _readSvView();
    if (v && v.t && v.p) openProject(v.t, v.p);
    else loadList();
  };

  function openProject(taskId, projectId) {
    _saveSvView(taskId, projectId);
    postJSON(API + '/short_video_get', {task_id: taskId, project_id: projectId}).then(function(r) {
      if (r && r.ok) setHTML(r.html);
      else {
        // 打不开（项目被删 / 无权限 / 会话过期）→ 清记忆并回列表，
        // 避免刷新后反复卡在打不开的视图上
        _saveSvView('', '');
        toast((r && r.error) || '打开失败', 'error');
        loadList();
      }
      startElapsedTicker();
      initSubtitleSortAll();
      // 修订九：restore/rerun/删除/拼接都走 openProject 重渲染 —— 渲染完
      // 恢复用户上次选中的短视频页签（该条已删则回默认第一条）
      _restoreHlTab(taskId, projectId);
    });
  }

  // ---------- 修订九：Stage 3 多条短视频逐条页签 ----------

  function _svProjKey() {
    var el = document.getElementById('slirn-short-video-inner');
    if (!el) return '';
    return (el.getAttribute('data-task-id') || '') + '/' +
           (el.getAttribute('data-project-id') || '');
  }

  function switchHlTab(idx) {
    var want = String(idx || '');
    var hit = false;
    Array.prototype.forEach.call(
      document.querySelectorAll('.slirn-stage3-hl-tab'),
      function(t) {
        var on = String(t.getAttribute('data-hl-index') || '') === want;
        if (on) hit = true;
        t.classList.toggle('active', on);
      });
    Array.prototype.forEach.call(
      document.querySelectorAll('.slirn-stage3-hl-pane'),
      function(p) {
        p.classList.toggle('active', String(p.getAttribute('data-hl-index') || '') === want);
      });
    // 只在确实命中时记忆（连同项目 key，换项目不串页签）
    if (hit) window._slirnSvHlTab = {k: _svProjKey(), hl: want};
  }

  function _restoreHlTab(taskId, projectId) {
    var mem = window._slirnSvHlTab;
    if (!mem || !mem.hl) return;
    if (mem.k !== String(taskId) + '/' + String(projectId)) return;
    var tab = document.querySelector('.slirn-stage3-hl-tab[data-hl-index="' + mem.hl + '"]');
    if (tab) switchHlTab(mem.hl);
    else window._slirnSvHlTab = null;   // 该条已删除 → 回默认第一条
  }

  function createProject() {
    var taskId = (document.getElementById('slirn-sv-create-task') || {}).value || '';
    var name = (document.getElementById('slirn-sv-create-name') || {}).value || '';
    if (!taskId) return toast('请选择来源任务', 'error');
    postJSON(API + '/short_video_create', {task_id: taskId, name: name}).then(function(r) {
      if (r && r.ok) {
        toast('项目已创建');
        openProject(taskId, r.project.id);
      } else toast((r && r.error) || '创建失败', 'error');
    });
  }

  function deleteProject(btn) {
    var taskId = btn.getAttribute('data-task-id') || '';
    var projectId = btn.getAttribute('data-project-id') || '';
    if (!window.confirm('确认删除该项目及其所有短视频产物？')) return;
    postJSON(API + '/short_video_delete', {task_id: taskId, project_id: projectId}).then(function(r) {
      if (r && r.ok) loadList();
      else toast((r && r.error) || '删除失败', 'error');
    });
  }

  // ---------- 上传素材（仍可用） ----------

  function uploadMaterial() {
    var ctx = innerCtx(); if (!ctx) return;
    var fileEl = document.getElementById('slirn-sv-upload-file');
    var file = fileEl && fileEl.files && fileEl.files[0];
    if (!file) return toast('请选择素材文件', 'error');
    var fd = new FormData();
    fd.append('task_id', ctx.taskId);
    fd.append('project_id', ctx.projectId);
    fd.append('kind', 'video');
    fd.append('file', file);
    toast('上传中…');
    postForm(API + '/short_video_upload_material', fd).then(function(r) {
      if (r && r.ok) { toast('素材已上传'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || '上传失败', 'error');
    });
  }

  // ---------- 6 阶段管线 ----------

  function withCtx(action, run) {
    var ctx = innerCtx();
    if (!ctx) { toast('页面未打开', 'error'); return; }
    run(ctx);
    return ctx;
  }

  function stage1Select() {
    var ctx = innerCtx(); if (!ctx) return;
    var sel = document.getElementById('slirn-stage1-source');
    var mat = sel ? sel.value : '';
    if (!mat) return toast('请先选一个视频素材', 'error');
    toast('正在选源…');
    postJSON(API + '/short_video_select_source', {
      task_id: ctx.taskId, project_id: ctx.projectId, material_id: mat
    }).then(function(r) {
      if (r && r.ok) {
        toast('已选源');
        if (r.html) setHTML(r.html);
        else openProject(ctx.taskId, ctx.projectId);
      } else toast((r && r.error) || '选源失败', 'error');
    });
  }

  function stage2Run() {
    var ctx = innerCtx(); if (!ctx) return;
    var model = (document.getElementById('slirn-stage2-model') || {}).value || 'paraformer';
    var lang = (document.getElementById('slirn-stage2-lang') || {}).value || 'zh';
    var hotwords = (document.getElementById('slirn-stage2-hotwords') || {}).value || '';
    toast('funasr 提取字幕中…');
    postJSON(API + '/short_video_stage2_run', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      model: model, lang: lang, hotwords: hotwords, sd_switch: 'no'
    }).then(function(r) {
      if (r && r.ok) { toast('Stage 2 完成'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || 'Stage 2 失败', 'error');
    });
  }

  // ---------- REQ-20261012-099：Stage 3 解析短视频内容 ----------

  function _stage3PromptText() {
    var ta = document.getElementById('slirn-stage3-prompt');
    return ta ? String(ta.value || '').trim() : '';
  }

  function _stage3UseImage() {
    var cb = document.getElementById('slirn-stage3-use-image');
    // 无复选框（没上传过图）→ false；有则按勾选
    return !!(cb && cb.checked);
  }

  function stage3Run() {
    var ctx = innerCtx(); if (!ctx) return;
    // REQ-099 修订：快速模板 UI 已移除，template 固定默认值（仅作后端兜底链）
    var template = 'hook_first';
    var n = parseInt((document.getElementById('slirn-stage3-n') || {}).value || '4', 10);
    var prompt = _stage3PromptText();
    var useImage = _stage3UseImage();
    toast('解析短视频内容中…（字幕' + (useImage ? ' + 截图' : '') + ' + 提示词发给 AI）');
    postJSON(API + '/short_video_stage3_run', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      template: template, n_clips: n,
      prompt: prompt, use_image: useImage
    }).then(function(r) {
      if (r && r.ok) { toast('解析完成'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || 'Stage 3 失败', 'error');
    });
  }

  function stage3RerunOne(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var hlIndex = parseInt(btn.getAttribute('data-hl-index') || '0', 10);
    if (!hlIndex) return toast('片段序号无效', 'error');
    var title = '';
    var row = btn.closest ? btn.closest('.slirn-stage3-hl-row') : null;
    var titleEl = row ? row.querySelector('.slirn-stage3-hl-title') : null;
    if (titleEl) title = titleEl.value || '';
    if (!window.confirm('针对「' + (title || '#' + hlIndex) + '」单独重新解析？\n'
        + '该片段的时间段与字幕内容将被替换，下游 Stage 4/5/6 重置。')) return;
    toast('片段 #' + hlIndex + ' 重新解析中…');
    postJSON(API + '/short_video_stage3_rerun_one', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hl_index: hlIndex,
      prompt: _stage3PromptText(),
      use_image: _stage3UseImage()
    }).then(function(r) {
      if (r && r.ok) { toast('片段 #' + hlIndex + ' 已更新'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || '重新解析失败', 'error');
    });
  }

  // REQ-099 修订：保存默认 / 存入列表后，用服务端返回的 history_html 原地
  // 刷新历史下拉（不重渲染整页 —— 文本域里用户正在编辑的内容不能丢），
  // 并自动展开让其立即看到新条目与 ⭐ 默认标记。
  function _refreshStage3History(historyHtml) {
    if (!historyHtml) return;
    var list = document.querySelector('.slirn-stage3-hist-list');
    if (list) list.innerHTML = historyHtml;
    var box = document.querySelector('.slirn-stage3-prompts-section');
    if (box) box.open = true;
  }

  function stage3SaveDefault() {
    var prompt = _stage3PromptText();
    if (!prompt) return toast('提示词为空，无法设为默认', 'error');
    postJSON(API + '/short_video_save_default_prompt', { prompt: prompt })
      .then(function(r) {
        if (r && r.ok) {
          _refreshStage3History(r.history_html);
          toast('已设为默认提示词（列表中 ⭐ 标出）');
        } else toast((r && r.error) || '保存失败', 'error');
      });
  }

  function stage3SaveHistory() {
    var prompt = _stage3PromptText();
    if (!prompt) return toast('提示词为空，无法存入列表', 'error');
    _postPromptHistory(prompt, false);
  }

  function _postPromptHistory(prompt, confirmOverwrite) {
    postJSON(API + '/short_video_prompt_history_save', {
      prompt: prompt, confirm: !!confirmOverwrite
    }).then(function(r) {
      if (!r || !r.ok) return toast((r && r.error) || '保存失败', 'error');
      if (r.status === 'need_confirm') {
        // 已满 10 条：由用户决定是否覆盖最旧一条
        if (window.confirm('提示词列表已满 ' + (r.max || 10) + ' 条。\n'
            + '继续保存将覆盖最旧的一条，是否覆盖？')) {
          _postPromptHistory(prompt, true);
        } else {
          toast('已取消保存');
        }
        return;
      }
      _refreshStage3History(r.history_html);
      toast('已存入提示词列表');
    });
  }

  // 历史条目：点击展开 / 收起完整内容（与「载入」分开，先看再决定）
  function stage3ToggleHist(btn) {
    var item = btn.closest ? btn.closest('.slirn-stage3-hist-item') : null;
    var full = item ? item.querySelector('.slirn-stage3-hist-full') : null;
    if (full) full.hidden = !full.hidden;
  }

  function stage3LoadPrompt(btn) {
    var ta = document.getElementById('slirn-stage3-prompt');
    if (!ta) return;
    var text = btn.getAttribute('data-prompt') || '';
    if (!text) return;
    if (ta.value && ta.value.trim() && !window.confirm(
        '文本域已有内容，用这条历史提示词覆盖它？')) return;
    ta.value = text;
    toast('已载入历史提示词');
  }

  function stage3ClearPrompt() {
    var ta = document.getElementById('slirn-stage3-prompt');
    if (!ta) return;
    if (ta.value && !window.confirm('清空当前提示词文本域？')) return;
    ta.value = '';
  }

  function stage3UploadImage() {
    var ctx = innerCtx(); if (!ctx) return;
    var fileEl = document.getElementById('slirn-stage3-image-file');
    var file = fileEl && fileEl.files && fileEl.files[0];
    if (!file) return toast('请先选择截图文件', 'error');
    var fd = new FormData();
    fd.append('task_id', ctx.taskId);
    fd.append('project_id', ctx.projectId);
    fd.append('file', file);
    toast('截图上传中…');
    postForm(API + '/short_video_stage3_upload_image', fd).then(function(r) {
      if (r && r.ok) { toast('截图已上传'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || '上传失败', 'error');
    });
  }

  function stage3RemoveImage() {
    var ctx = innerCtx(); if (!ctx) return;
    if (!window.confirm('移除当前截图？之后解析将不再携带图片。')) return;
    postJSON(API + '/short_video_stage3_remove_image', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (r && r.ok) { toast('截图已移除'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || '移除失败', 'error');
    });
  }

  function stage3VerifyRun() {
    // REQ-20261004-verify：跑 Stage 3.5 一致性核验（ffmpeg 抽音频 + funasr
    // 重 ASR + 与 Stage 3 字幕逐行对齐）。每个 highlight 几秒，全跑 ~10-30s；
    // toast 提示开始，done 后刷新页面看报告。
    var ctx = innerCtx(); if (!ctx) return;
    toast('字幕一致性核验中（每条片段几秒）…');
    postJSON(API + '/short_video_stage3_verify', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (r && r.ok) {
        var sum = ((r.stage3_verify || {}).summary) || {};
        toast('核验完成：' + (sum.ok || 0) + ' 一致 / '
              + (sum.warning || 0) + ' 部分 / '
              + (sum.failed || 0) + ' 不符');
        openProject(ctx.taskId, ctx.projectId);
      }
      else toast((r && r.error) || 'Stage 3.5 失败', 'error');
    });
  }

  function stage4Run() {
    var ctx = innerCtx(); if (!ctx) return;
    toast('粗剪合成中…');
    postJSON(API + '/short_video_stage4_run', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (r && r.ok) { toast('Stage 4 完成'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || 'Stage 4 失败', 'error');
    });
  }

  function stage5Asr(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var index = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!index) return;
    toast('重新 ASR 中…');
    postJSON(API + '/short_video_stage5_run', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: index
    }).then(function(r) {
      if (r && r.ok) { toast('ASR 完成'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || 'Stage 5 ASR 失败', 'error');
    });
  }

  function stage5Save(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var index = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!index) return;
    var ta = document.querySelector('.slirn-stage5-srt[data-clip-index="' + index + '"]');
    var srt = ta ? ta.value : '';
    if (!srt.trim()) return toast('字幕为空，请先编辑或重新 ASR', 'error');
    toast('保存字幕中…');
    postJSON(API + '/short_video_stage5_save', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: index, srt_text: srt
    }).then(function(r) {
      if (r && r.ok) { toast('Stage 5 #'+index+' 已保存'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || '保存失败', 'error');
    });
  }

  function stage6Run() {
    var ctx = innerCtx(); if (!ctx) return;
    // 自动从 stage5.highlights + stage4.highlights 拼 items
    postJSON(API + '/short_video_get', {task_id: ctx.taskId, project_id: ctx.projectId}).then(function(r) {
      if (!r || !r.ok) return toast('读取项目失败', 'error');
      var p = r.project || {};
      var s4 = (p.pipeline || {}).stage4_coarse || {};
      var s5 = (p.pipeline || {}).stage5_refine || {};
      var s4hls = s4.highlights || [];
      var s5hls = s5.highlights || [];
      var items = s4hls.map(function(h4) {
        var h5 = s5hls.find(function(x) { return x.index === h4.index; }) || {};
        return {
          index: h4.index,
          coarse_mp4: h4.coarse_mp4 || '',
          refined_srt: h5.refined_srt || ''
        };
      }).filter(function(it) { return it.coarse_mp4 && it.refined_srt; });
      if (!items.length) return toast('Stage 4 / 5 未就绪', 'error');
      toast('精剪 ' + items.length + ' 条中…');
      postJSON(API + '/short_video_stage6_run', {
        task_id: ctx.taskId, project_id: ctx.projectId, items: items
      }).then(function(x) {
        if (x && x.ok) { toast('Stage 6 完成'); openProject(ctx.taskId, ctx.projectId); }
        else toast((x && x.error) || 'Stage 6 失败', 'error');
      });
    });
  }

  // ---------- 预览小窗（SRT / 视频） ----------

  function onPreviewKey(ev) {
    if (ev.key === 'Escape') closePreview();
  }
  function closePreview() {
    var ov = document.getElementById('slirn-sv-preview-overlay');
    if (!ov) return;
    ov.querySelectorAll('video, audio').forEach(function(m) { try { m.pause(); } catch (e) {} });
    ov.remove();
    document.removeEventListener('keydown', onPreviewKey);
  }
  // 修订七：预览窗口可拖动位置 + 可调整大小（用户验收）。
  // 拖动 = 标题栏 mousedown 把卡片从 flex 居中切到绝对定位后跟手移动；
  // 调整大小 = 右下角把柄改 width（视频 max-width:100% 自动跟随），有持久化 key 则记忆。
  function _enableWinDrag(win, head) {
    head.addEventListener('mousedown', function(ev) {
      if (ev.button !== 0) return;
      if (ev.target.closest && ev.target.closest('button')) return;
      ev.preventDefault();
      var rect = win.getBoundingClientRect();
      var parent = win.offsetParent || document.body;
      var prect = parent.getBoundingClientRect();
      var baseL = rect.left - prect.left, baseT = rect.top - prect.top;
      win.style.position = 'absolute';
      win.style.margin = '0';
      win.style.left = baseL + 'px';
      win.style.top = baseT + 'px';
      var x0 = ev.clientX, y0 = ev.clientY;
      function move(e2) {
        var l = Math.max(0, Math.min(prect.width - 80, baseL + e2.clientX - x0));
        var t = Math.max(0, Math.min(prect.height - 40, baseT + e2.clientY - y0));
        win.style.left = l + 'px';
        win.style.top = t + 'px';
      }
      function up() {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }
  function _enableWinResize(win, handle, opts) {
    opts = opts || {};
    var minW = opts.minW || 300;
    if (opts.persistKey) {
      try {
        var saved = JSON.parse(localStorage.getItem(opts.persistKey) || 'null');
        if (saved && saved.width > minW) {
          win.style.width = saved.width + 'px';
          win.style.maxWidth = 'none';
        }
      } catch (e) { /* ignore */ }
    }
    handle.addEventListener('mousedown', function(ev) {
      if (ev.button !== 0) return;
      ev.preventDefault();
      ev.stopPropagation();
      var startW = win.getBoundingClientRect().width;
      var x0 = ev.clientX;
      win.style.maxWidth = 'none';  // 解除 max-width 上限，允许拉大
      function move(e2) {
        var w = Math.max(minW, Math.min(window.innerWidth - 24, startW + e2.clientX - x0));
        win.style.width = w + 'px';
      }
      function up() {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        if (opts.persistKey) {
          try {
            localStorage.setItem(opts.persistKey, JSON.stringify({ width: win.getBoundingClientRect().width }));
          } catch (e) { /* ignore */ }
        }
      }
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }

  function previewByUrl(url, name, body) {
    closePreview();
    if (!url) return;
    var ov = document.createElement('div');
    ov.className = 'slirn-modal-overlay';
    ov.id = 'slirn-sv-preview-overlay';
    ov.innerHTML =
      '<div class="slirn-modal-card slirn-sv-preview-card" role="dialog" aria-modal="true">' +
        '<div class="slirn-sv-preview-head">' +
          '<div class="slirn-sv-preview-title">' + esc(name || '预览') + '</div>' +
          '<button class="slirn-btn-mini" data-action="sv-preview-close" title="关闭 (Esc)">✕</button>' +
        '</div>' +
        '<div class="slirn-sv-preview-body">' + body + '</div>' +
        '<div class="slirn-sv-preview-resize" title="拖动调整窗口大小">◢</div>' +
      '</div>';
    ov.addEventListener('click', function(ev2) {
      if (ev2.target === ov) closePreview();
    });
    document.body.appendChild(ov);
    document.addEventListener('keydown', onPreviewKey);
    var card = ov.querySelector('.slirn-sv-preview-card');
    _enableWinDrag(card, ov.querySelector('.slirn-sv-preview-head'));
    _enableWinResize(card, ov.querySelector('.slirn-sv-preview-resize'), { minW: 300 });
  }

  function previewSrtByUrl(url) {
    // 拉 srt 内容后用 <pre> 显示
    fetch(url, {credentials: 'same-origin'}).then(function(r) {
      if (!r.ok) return toast('无法读取 SRT', 'error');
      return r.text();
    }).then(function(text) {
      if (text == null) return;
      previewByUrl(url, '字幕文件', '<pre class="slirn-srt-pre">' + esc(text) + '</pre>');
    }).catch(function(e) { toast('读取失败: ' + e, 'error'); });
  }

  function previewStage2Srt(btn) {
    previewSrtByUrl(btn.getAttribute('data-url') || '');
  }

  function previewVideo(btn) {
    var url = btn.getAttribute('data-url') || '';
    var name = btn.getAttribute('data-name') || '';
    previewByUrl(url, name, '<video controls autoplay playsinline src="' + esc(url) + '"></video>');
  }

  // REQ-20261004-UX：Stage 3 高亮片段预览 —— 浏览器侧 seek 到
  // start_ms 起点、timeupdate 到 end_ms 自动暂停。
  // **v2：可拖动悬浮窗口**，无遮盖层，用户可同时看 Stage 3 字幕行比对。
  function stage3PreviewHl(btn) {
    var url = btn.getAttribute('data-src-url') || '';
    if (!url) return toast('请先在 Stage 1 选源视频', 'error');
    var startMs = parseInt(btn.getAttribute('data-start-ms') || '0', 10) || 0;
    var endMs = parseInt(btn.getAttribute('data-end-ms') || '0', 10) || 0;
    var title = btn.getAttribute('data-title') || '预览片段';

    // 取父 hl-row 的 data-sub-text（每条 highlight 的完整字幕文本）
    var row = btn.closest ? btn.closest('.slirn-stage3-hl-row') : null;
    var subText = (row && row.getAttribute('data-sub-text')) || '';

    closeFloatingPreview();  // 防止多个悬浮窗叠加
    var fp = document.createElement('div');
    fp.className = 'slirn-sv-floating-player';
    fp.id = 'slirn-sv-floating-player';
    fp.innerHTML =
      '<div class="slirn-sv-fp-head">' +
        '<span class="slirn-sv-fp-title">🎬 ' + esc(title) + '</span>' +
        '<button class="slirn-sv-fp-close" data-action="sv-fp-close" title="关闭 (Esc)">✕</button>' +
      '</div>' +
      '<div class="slirn-sv-fp-body">' +
        '<video id="slirn-sv-fp-video" class="slirn-sv-fp-video" ' +
          'controls autoplay playsinline preload="auto" src="' + esc(url) + '"></video>' +
        '<div class="slirn-sv-fp-subs" id="slirn-sv-fp-subs"></div>' +
      '</div>' +
      '<div class="slirn-sv-fp-resize" title="拖动调整窗口大小">◢</div>';
    document.body.appendChild(fp);

    // 字幕块：从 data-sub-text 还原成行
    var subsEl = fp.querySelector('#slirn-sv-fp-subs');
    var lines = subText.split('\n').filter(function(l) { return l.trim(); });
    if (lines.length === 0) {
      subsEl.innerHTML = '<div class="slirn-sv-fp-subs-empty">无字幕</div>';
    } else {
      // 解析 `[src] text`，渲染成 <div class="slirn-sv-fp-sub">
      var html = '';
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var m = line.match(/^\[(\d+)\]\s*(.*)$/);
        if (m) {
          html += '<div class="slirn-sv-fp-sub">' +
                  '<span class="slirn-sv-fp-sub-src">[' + esc(m[1]) + ']</span>' +
                  esc(m[2]) + '</div>';
        } else {
          html += '<div class="slirn-sv-fp-sub">' + esc(line) + '</div>';
        }
      }
      subsEl.innerHTML = html;
    }

    // 拖动支持：header 即拖把；持久化位置到 localStorage
    var head = fp.querySelector('.slirn-sv-fp-head');
    var storageKey = 'slirn.sv.fp.pos';
    try {
      var saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved && typeof saved.left === 'number' && typeof saved.top === 'number') {
        fp.style.left = saved.left + 'px';
        fp.style.top = saved.top + 'px';
        fp.style.right = 'auto';
      }
    } catch (e) { /* 持久化失败忽略 */ }

    var dragState = null;
    head.addEventListener('mousedown', function(ev) {
      if (ev.target.closest && ev.target.closest('[data-action="sv-fp-close"]')) return;
      ev.preventDefault();
      var rect = fp.getBoundingClientRect();
      dragState = {
        startX: ev.clientX,
        startY: ev.clientY,
        baseLeft: rect.left,
        baseTop: rect.top,
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
    function onMove(ev) {
      if (!dragState) return;
      var dx = ev.clientX - dragState.startX;
      var dy = ev.clientY - dragState.startY;
      var left = Math.max(0, Math.min(window.innerWidth - 80, dragState.baseLeft + dx));
      var top = Math.max(0, Math.min(window.innerHeight - 40, dragState.baseTop + dy));
      fp.style.left = left + 'px';
      fp.style.top = top + 'px';
      fp.style.right = 'auto';
    }
    function onUp() {
      if (!dragState) return;
      try {
        var rect = fp.getBoundingClientRect();
        localStorage.setItem(storageKey, JSON.stringify({ left: rect.left, top: rect.top }));
      } catch (e) { /* ignore */ }
      dragState = null;
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }

    // 修订七：右下角把柄调窗口大小（宽度驱动，视频跟随），记忆到 localStorage
    _enableWinResize(fp, fp.querySelector('.slirn-sv-fp-resize'), {
      minW: 280, persistKey: 'slirn.sv.fp.size',
    });

    // 关闭按钮（事件冒泡到全局 click 分发，但这里直接绑，省一次查找）
    fp.querySelector('[data-action="sv-fp-close"]').addEventListener('click', function() {
      closeFloatingPreview();
    });

    // Esc 关闭：注册一次性监听（多个悬浮窗叠加时只关最上层——目前已强制唯一）
    var onEsc = function(ev) {
      if (ev.key === 'Escape') {
        closeFloatingPreview();
        document.removeEventListener('keydown', onEsc);
      }
    };
    document.addEventListener('keydown', onEsc);

    // video 逐段播放引擎（修订八）：按 data-segments 的切割窗逐段 seek 播放，
    // 与拼接产物一致；无 segments 时退回旧的「组起点连播到组终点」。
    var v = fp.querySelector('#slirn-sv-fp-video');
    if (!v) return;
    var segs = null;
    try {
      var raw = btn.getAttribute('data-segments');
      if (raw) {
        var parsed = JSON.parse(raw);
        if (Object.prototype.toString.call(parsed) === '[object Array]' && parsed.length) {
          segs = parsed.filter(function(sg) {
            return sg && isFinite(sg[0]) && isFinite(sg[1]) && sg[1] > sg[0];
          }).map(function(sg) { return [sg[0] / 1000, sg[1] / 1000]; });
          if (!segs.length) segs = null;
        }
      }
    } catch (e) { segs = null; }
    var startSec = (segs ? segs[0][0] : startMs / 1000);
    var cleared = false;
    var segIdx = 0;
    var manual = false;   // 用户自己拖进度条 → 停止自动逐段推进
    function onSeeking() {
      if (cleared || segIdx >= (segs ? segs.length : 0)) return;
      var t = v.currentTime;
      // 当前段的自动 seek 会先触发 seeking —— 只把「跳出现段」的视作手动
      var cur = segs[segIdx];
      if (t < cur[0] - 0.05 || t > cur[1] + 0.05) manual = true;
    }
    function onTime() {
      if (cleared || manual) return;
      if (segs) {
        if (v.currentTime >= segs[segIdx][1]) {
          segIdx++;
          if (segIdx >= segs.length) {
            v.pause();
            cleared = true;
            v.removeEventListener('timeupdate', onTime);
            v.removeEventListener('seeking', onSeeking);
          } else {
            try { v.currentTime = segs[segIdx][0]; } catch (e) { /* ignore */ }
          }
        }
      } else if (v.currentTime >= endSecFallback()) {
        v.pause();
        cleared = true;
        v.removeEventListener('timeupdate', onTime);
      }
    }
    function endSecFallback() {
      return Math.max(startMs / 1000 + 0.05, endMs / 1000);
    }
    function onLoaded() {
      try {
        v.currentTime = startSec;
        v.play().catch(function(){});
      } catch (e) { /* seek 偶发 NotSupported 忽略 */ }
      v.addEventListener('timeupdate', onTime);
      if (segs) v.addEventListener('seeking', onSeeking);
    }
    if (v.readyState >= 1) {
      onLoaded();
    } else {
      v.addEventListener('loadedmetadata', onLoaded, { once: true });
    }
  }

  // 关悬浮窗：拿掉 DOM 并暂停 video
  function closeFloatingPreview() {
    var fp = document.getElementById('slirn-sv-floating-player');
    if (!fp) return;
    fp.querySelectorAll('video, audio').forEach(function(m) { try { m.pause(); } catch (e) {} });
    fp.remove();
  }

  // ---------- REQ-20261004-stage3-restructure：字幕行拖拽排序 ----------
  // 用 HTML5 drag-and-drop API；drop 后立刻调 stage3_reorder_subtitles 端点。
  // 视觉反馈：拖动中 opacity:0.4；目标行上方加高亮蓝线。

  function _getDragCtx() {
    return window._slirnSvDragCtx || (window._slirnSvDragCtx = { srcEl: null, hlIndex: null, srcIndex: null });
  }

  function bindSubtitleSort(container) {
    // container 是 .slirn-stage3-hl-preview（一个 highlight 内的字幕行容器）
    var hlIndex = container.getAttribute('data-hl-index');
    if (!hlIndex) return;
    // 给每个 .slirn-stage3-hl-sub 注册拖拽事件（一次性注册；后续 DOM 变化由 initSubtitleSortAll 兜底）
    var subs = container.querySelectorAll('.slirn-stage3-hl-sub');
    subs.forEach(function(el) {
      if (el.getAttribute('data-sort-bound') === '1') return;
      el.setAttribute('data-sort-bound', '1');
      el.addEventListener('dragstart', function(ev) {
        var ctx = _getDragCtx();
        ctx.srcEl = el;
        ctx.hlIndex = hlIndex;
        ctx.srcIndex = el.getAttribute('data-src-index');
        try { ev.dataTransfer.setData('text/plain', String(ctx.srcIndex || '')); } catch (e) {}
        ev.dataTransfer.effectAllowed = 'move';
        el.classList.add('slirn-stage3-hl-sub-dragging');
      });
      el.addEventListener('dragend', function() {
        el.classList.remove('slirn-stage3-hl-sub-dragging');
        container.querySelectorAll('.slirn-stage3-hl-sub-drop-target').forEach(function(x) {
          x.classList.remove('slirn-stage3-hl-sub-drop-target');
        });
        var ctx = _getDragCtx();
        ctx.srcEl = null;
      });
      el.addEventListener('dragover', function(ev) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        el.classList.add('slirn-stage3-hl-sub-drop-target');
      });
      el.addEventListener('dragleave', function() {
        el.classList.remove('slirn-stage3-hl-sub-drop-target');
      });
      el.addEventListener('drop', function(ev) {
        ev.preventDefault();
        var ctx = _getDragCtx();
        if (!ctx.srcEl || !ctx.srcIndex) return;
        var targetSrc = el.getAttribute('data-src-index');
        if (String(targetSrc) === String(ctx.srcIndex)) return; // 原地拖动忽略
        // 立即在 DOM 上重排（乐观更新）—— 失败时 revert
        var movedEl = ctx.srcEl;
        var parent = movedEl.parentNode;
        parent.insertBefore(movedEl, el); // 移到目标之前
        // 调端点持久化
        var root = document.getElementById('slirn-short-video-inner');
        var taskId = root ? (root.getAttribute('data-task-id') || '') : '';
        var projectId = root ? (root.getAttribute('data-project-id') || '') : '';
        if (!taskId || !projectId) {
          showToast('缺少 task_id / project_id，拖拽未持久化', 'error');
          return;
        }
        fetch('/slirn/api/short_video_stage3_reorder_subtitles', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task_id: taskId,
            project_id: projectId,
            hl_index: parseInt(ctx.hlIndex || '0', 10),
            src_index: parseInt(ctx.srcIndex || '0', 10),
            before_src_index: parseInt(targetSrc || '0', 10),
          })
        }).then(function(r) { return r.json().then(function(j) { return { ok: r.ok, body: j }; }); })
          .then(function(data) {
            if (!data.ok) {
              // revert DOM
              parent.insertBefore(movedEl, el.nextSibling);
              showToast('字幕重排失败：' + (data.body && data.body.error || '未知错误'), 'error');
            } else {
              showToast('字幕顺序已更新（下游 Stage 4/5/6 已重置）', 'ok');
            }
          })
          .catch(function(err) {
            parent.insertBefore(movedEl, el.nextSibling);
            showToast('字幕重排失败：' + err, 'error');
          });
      });
    });
  }

  function initSubtitleSortAll() {
    // 给当前可见的所有 .slirn-stage3-hl-preview 容器绑定拖拽（幂等）
    document.querySelectorAll('.slirn-stage3-hl-preview').forEach(bindSubtitleSort);
  }

  // ---------- REQ-20261012-099 修订六：还原字幕顺序（回到大模型最初顺序） ----------
  // 修订五的「片段行 ⇕ 拖拽拼接」已随「N 组 = N 条独立短视频」语义移除；
  // 组间没有拼接顺序，组内行拖拽（bindSubtitleSortAll）保留。
  function stage3RestoreOrder(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var hlIndex = parseInt(btn.getAttribute('data-hl-index') || '0', 10);
    if (!hlIndex) { toast('缺少片段编号，未执行还原', 'error'); return; }
    postJSON(API + '/short_video_stage3_restore_order', {
      task_id: ctx.taskId, project_id: ctx.projectId, hl_index: hlIndex
    }).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || ('#' + hlIndex + ' 已还原为大模型最初顺序'));
        openProject(ctx.taskId, ctx.projectId);
      } else toast((r && r.error) || '还原顺序失败', 'error');
    });
  }

  function stage3Splice() {
    var ctx = innerCtx(); if (!ctx) return;
    toast('截取拼接中…（每条短视频按其字幕行时间戳独立切片拼接）');
    postJSON(API + '/short_video_stage3_splice', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || '拼接完成');
        openProject(ctx.taskId, ctx.projectId);
      } else toast((r && r.error) || '拼接失败', 'error');
    });
  }

  // 修订八：删除一条短视频 —— 必须先弹确认框，用户确认后才真正删除
  function stage3RemoveHl(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var hlIndex = parseInt(btn.getAttribute('data-hl-index') || '0', 10);
    if (!hlIndex) { toast('缺少片段编号，未执行删除', 'error'); return; }
    var row = btn.closest ? btn.closest('.slirn-stage3-hl-row') : null;
    var titleEl = row ? row.querySelector('.slirn-stage3-hl-title') : null;
    var title = titleEl ? (titleEl.value || titleEl.textContent || '').trim() : '';
    var msg = '确定要删除短视频 #' + hlIndex +
      (title ? '「' + title + '」' : '') + ' 吗？删除后需要重新解析才能恢复。';
    if (!window.confirm(msg)) return;
    postJSON(API + '/short_video_stage3_remove_hl', {
      task_id: ctx.taskId, project_id: ctx.projectId, hl_index: hlIndex
    }).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || ('#' + hlIndex + ' 已删除'));
        openProject(ctx.taskId, ctx.projectId);
      } else toast((r && r.error) || '删除失败', 'error');
    });
  }

  // ---------- 列表过滤 / 搜索（REQ-20261012-099 修订四，对齐长视频列表） ----------

  // 「我的任务 / 全部任务」radio 切换 → 按新 scope 重拉列表
  document.addEventListener('change', function(e) {
    if (e.target && e.target.name === 'slirn-sv-scope') {
      loadList(e.target.value);
    }
  });

  // 搜索框 client-side 过滤：按短视频任务名（项目名）/ 任务 ID / 项目 ID 匹配卡片
  function _filterSvCards(query) {
    var q = (query || '').trim().toLowerCase();
    var grid = document.querySelector('.slirn-sv-grid');
    if (!grid) return;
    var matchedCount = 0;
    grid.querySelectorAll('.slirn-sv-card').forEach(function(card) {
      var tid = (card.getAttribute('data-task-id') || '').toLowerCase();
      var pid = (card.getAttribute('data-project-id') || '').toLowerCase();
      var headEl = card.querySelector('.slirn-sv-card-head strong');
      var mutedEl = card.querySelector('.slirn-sv-muted');
      var name = headEl ? (headEl.textContent || '').toLowerCase() : '';
      var meta = mutedEl ? (mutedEl.textContent || '').toLowerCase() : '';
      var match = !q || (tid + ' ' + pid + ' ' + name + ' ' + meta).indexOf(q) >= 0;
      card.style.display = match ? '' : 'none';
      if (match) matchedCount++;
    });
    var existing = grid.querySelector('.slirn-search-empty');
    if (existing) existing.remove();
    if (q && matchedCount === 0) {
      var empty = document.createElement('div');
      empty.className = 'slirn-empty slirn-search-empty';
      empty.style.gridColumn = '1/-1';
      empty.innerHTML = '<div class="slirn-empty-icon">🔍</div>' +
        '<div class="slirn-empty-text">没有匹配「' + esc(q) + '」的短视频任务</div>';
      grid.appendChild(empty);
    }
  }
  // 事件代理挂监听（列表重渲染会换掉 input 元素，不能直接绑）
  document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'slirn-sv-search') {
      _filterSvCards(e.target.value);
    }
  });

  // ---------- 点击分发 ----------

  document.addEventListener('click', function(ev) {
    var btn = ev.target.closest ? ev.target.closest('[data-action]') : null;
    if (!btn) return;
    var action = btn.getAttribute('data-action');
    if (!action || action.indexOf('sv-') !== 0) return;
    ev.preventDefault();

    // 列表 / 创建 / 打开
    if (action === 'sv-new') {
      // 「➕ 新建短视频任务」（从 hero 挪来）：滚动到创建表单并聚焦项目名
      var createSec = document.getElementById('slirn-sv-create');
      if (createSec && createSec.scrollIntoView) {
        createSec.scrollIntoView({behavior: 'smooth', block: 'center'});
      }
      var nameInput = document.getElementById('slirn-sv-create-name');
      if (nameInput && nameInput.focus) nameInput.focus();
      return;
    }
    if (action === 'sv-stage-nav') {
      return switchSvStage(btn.getAttribute('data-stage-key') || '');
    }
    if (action === 'sv-refresh') return loadList();
    if (action === 'sv-create') return createProject();
    if (action === 'sv-open') {
      return openProject(btn.getAttribute('data-task-id'), btn.getAttribute('data-project-id'));
    }
    if (action === 'sv-back') return loadList();
    if (action === 'sv-delete') return deleteProject(btn);

    // 上传（Stage 1 选源前可上传）
    if (action === 'sv-upload') return uploadMaterial();

    // 6 阶段
    if (action === 'sv-stage1-select') return stage1Select();
    if (action === 'sv-stage2-run') return stage2Run();
    if (action === 'sv-stage2-preview-srt') return previewStage2Srt(btn);
    if (action === 'sv-stage3-run') return stage3Run();
    if (action === 'sv-stage3-splice') return stage3Splice();
    if (action === 'sv-stage3-restore-order') return stage3RestoreOrder(btn);
    if (action === 'sv-stage3-preview') return stage3PreviewHl(btn);
    if (action === 'sv-stage3-verify-run') return stage3VerifyRun();
    if (action === 'sv-stage3-rerun') return stage3RerunOne(btn);
    if (action === 'sv-stage3-remove-hl') return stage3RemoveHl(btn);
    if (action === 'sv-stage3-hl-tab') return switchHlTab(btn.getAttribute('data-hl-index'));
    if (action === 'sv-stage3-save-default') return stage3SaveDefault();
    if (action === 'sv-stage3-save-history') return stage3SaveHistory();
    if (action === 'sv-stage3-load-prompt') return stage3LoadPrompt(btn);
    if (action === 'sv-stage3-toggle-hist') return stage3ToggleHist(btn);
    if (action === 'sv-stage3-clear-prompt') return stage3ClearPrompt();
    if (action === 'sv-stage3-upload-image') return stage3UploadImage();
    if (action === 'sv-stage3-remove-image') return stage3RemoveImage();
    if (action === 'sv-stage4-run') return stage4Run();
    if (action === 'sv-stage5-asr') return stage5Asr(btn);
    if (action === 'sv-stage5-save') return stage5Save(btn);
    if (action === 'sv-stage6-run') return stage6Run();

    // 预览 / 素材
    if (action === 'sv-preview') return previewVideo(btn);
    if (action === 'sv-preview-close') return closePreview();
    if (action === 'sv-preview-srt') return previewSrtByUrl(btn.getAttribute('data-url') || '');
  });
})();
