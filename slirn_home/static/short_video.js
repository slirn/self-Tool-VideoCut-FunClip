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
    // REQ-20261013-116：用户主动点 tab → 记下当前阶段，openProject 重渲染后
    // 按此恢复（不依赖 server focus_key 自动算到「第一个未 done 的阶段」）。
    try { localStorage.setItem(SV_STAGE_KEY, stageKey); } catch (e) {}
  }

  // REQ-20261013-116：短视频同步长视频「完成后自动打开下一阶段界面」开关
  // （参考 REQ-20260918-046 long video 实现 .slirn-wb-autonext）。
  // - 开关默认关：保持 server focus_key 自动跳下阶段的原行为
  // - 用户勾选：当前焦点阶段完成时自动切下一阶段 + toast
  // - 用户取消勾选：当前阶段完成时保持在原页面（用户手动切）
  var SV_STAGE_KEY = 'slirn.sv.currentStage';
  var SV_AUTONEXT_KEY = 'slirn.sv.autonext';

  function _svAutoNextOn() {
    try { return localStorage.getItem(SV_AUTONEXT_KEY) === '1'; }
    catch (err) { return false; }
  }
  function _saveSvAutoNext(on) {
    try { localStorage.setItem(SV_AUTONEXT_KEY, on ? '1' : ''); }
    catch (err) {}
  }
  function _readSvStage() {
    try { return localStorage.getItem(SV_STAGE_KEY) || ''; }
    catch (err) { return ''; }
  }
  function applySvAutoNextState() {
    var el = document.getElementById('slirn-sv-autonext');
    if (el) el.checked = _svAutoNextOn();
  }
  // 改 checkbox → 写 localStorage + toast 提示
  document.addEventListener('change', function(e) {
    if (e.target && e.target.id === 'slirn-sv-autonext') {
      _saveSvAutoNext(!!e.target.checked);
      toast(e.target.checked
        ? '✅ 已开启：当前阶段完成后自动打开下一阶段界面'
        : '↩️ 已关闭：阶段完成后保持在原页面（需手动切下一阶段）');
    }
  });
  // openProject 渲染后调用：对比 prevDone 快照，若「当前焦点阶段刚完成 + 开关开
  // + 没到底」则切下一阶段。toast 同步长视频风格。
  function svAutoNextMaybe(prevDone, hadSv, prevActive) {
    if (!hadSv || !_svAutoNextOn() || !prevActive) return;
    var stages = [];
    document.querySelectorAll('#slirn-short-video-inner .slirn-sv-stage-nav').forEach(function(s) {
      stages.push(s.getAttribute('data-stage-key') || '');
    });
    var justDone = '';
    stages.forEach(function(pk) {
      var nav = document.querySelector(
        '#slirn-short-video-inner .slirn-sv-stage-nav[data-stage-key="' + pk + '"]');
      if (nav && nav.classList.contains('done') && !prevDone[pk] && pk === prevActive) {
        justDone = pk;
      }
    });
    if (!justDone) return;
    var idx = stages.indexOf(justDone);
    if (idx < 0 || idx + 1 >= stages.length) return;  // 最后一个阶段完成 → 不切
    switchSvStage(stages[idx + 1]);
    toast('✅ 「' + justDone + '」完成 — 已自动打开下一阶段界面'
      + '（可关掉阶段上方的「完成后自动打开下一阶段界面」开关改为手动切）');
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
    // REQ-20261013-116：渲染前快照（哪些阶段已完成 + 用户当前所在阶段）——
    // 渲染后对比实现「当前阶段刚完成 → 按开关决定是否切下一阶段」。
    var prevDone = {}, prevActive = '';
    var hadSv = !!document.querySelector('#slirn-short-video-inner .slirn-sv-stage-nav');
    if (hadSv) {
      document.querySelectorAll('#slirn-short-video-inner .slirn-sv-stage-nav.done').forEach(function(s) {
        prevDone[s.getAttribute('data-stage-key') || ''] = 1;
      });
      document.querySelectorAll('#slirn-short-video-inner .slirn-sv-stage-pane').forEach(function(p) {
        if (p.classList.contains('active')) {
          prevActive = p.getAttribute('data-stage-key') || '';
        }
      });
    }
    postJSON(API + '/short_video_get', {task_id: taskId, project_id: projectId}).then(function(r) {
      if (r && r.ok) setHTML(r.html);
      else {
        // 打不开（项目被删 / 无权限 / 会话过期）→ 清记忆并回列表，
        // 避免刷新后反复卡在打不开的视图上
        _saveSvView('', '');
        toast((r && r.error) || '打开失败', 'error');
        loadList();
        return;
      }
      startElapsedTicker();
      initSubtitleSortAll();
      // 修订九：restore/rerun/删除/拼接都走 openProject 重渲染 —— 渲染完
      // 恢复用户上次选中的短视频页签（该条已删则回默认第一条）
      _restoreHlTab(taskId, projectId);
      // REQ-20261013-108：Stage 4 提示词 textarea + 历史列表加载（不覆盖用户已编辑）
      loadStage4Prompts();
      // REQ-20261013-116：① 恢复「自动打开下一阶段」开关状态 ② 按开关决定是否
      // 切下一阶段（只在「当前焦点阶段刚完成」时触发；只是首次进入或手动点
      // tab 不会触发）。最后用用户记忆的 stage 覆盖 server focus_key 默认
      // 焦点（避免 server 把焦点算到「第一个未 done 阶段」抢走用户当前页）。
      applySvAutoNextState();
      svAutoNextMaybe(prevDone, hadSv, prevActive);
      var saved = _readSvStage();
      if (saved) {
        // 校验：saved 必须是 server 渲染出来的 7 个 stage 之一
        var exists = document.querySelector(
          '#slirn-short-video-inner .slirn-sv-stage-pane[data-stage-key="' + saved + '"]');
        if (exists) switchSvStage(saved);
      }
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
      document.querySelectorAll('.slirn-stage3-hl-tab, .slirn-stage4-hl-tab, .slirn-stage6-hl-tab'),
      function(t) {
        var on = String(t.getAttribute('data-hl-index') || '') === want;
        if (on) hit = true;
        t.classList.toggle('active', on);
      });
    Array.prototype.forEach.call(
      document.querySelectorAll('.slirn-stage3-hl-pane, .slirn-stage4-hl-pane, .slirn-stage6-hl-pane'),
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
    var tab = document.querySelector(
      '.slirn-stage3-hl-tab[data-hl-index="' + mem.hl + '"], ' +
      '.slirn-stage4-hl-tab[data-hl-index="' + mem.hl + '"], ' +
      '.slirn-stage6-hl-tab[data-hl-index="' + mem.hl + '"]');
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

  function stage2Run(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    if (btn && btn.disabled) return;
    // REQ-20261016-125：防重入双保险（闭包标志 + 按钮 disabled），
    // 提取是 5-30 分钟的阻塞调用，必须能看见进度且不能误点第二次。
    if (window._slirnSvStage2Running) {
      toast('字幕提取正在执行中，请勿重复点击', 'warn'); return;
    }
    window._slirnSvStage2Running = true;

    // REQ-20261015-122：模型/语言不再由用户选择，固定 paraformer-zh（后端默认值）。
    var hotwords = (document.getElementById('slirn-stage2-hotwords') || {}).value || '';
    var status = document.getElementById('slirn-sv-stage2-status');
    var msg = status ? status.querySelector('.slirn-sv-stage2-msg') : null;
    var bar = status ? status.querySelector('.slirn-sv-stage2-bar') : null;
    var setBtn = function(txt, dis) { if (btn) { btn.textContent = txt; btn.disabled = !!dis; } };
    var show = function(t, pct) {
      if (status) status.style.display = '';
      if (msg) { msg.style.display = ''; msg.textContent = t; }
      if (bar && pct != null) bar.style.width = pct + '%';
    };
    var t0 = Date.now();
    var elapsedTxt = function() { return '已耗时 ' + _fmtElapsed((Date.now() - t0) / 1000); };

    setBtn('🎧 提取中…', true);
    show('⏳ 启动 funasr 模型（约 10-30 秒）… · ' + elapsedTxt(), 4);
    toast('funasr 提取字幕中…（长视频可能 5-30 分钟，进度见下方状态卡）');

    // 阶段文字 + 真实已耗时（1s 节奏）。识别是大头且时长随视频长度浮动，
    // 进度条按已耗时缓慢爬升、90% 封顶等后端响应，完成时一次性跳 100%。
    var timer = setInterval(function() {
      var sec = (Date.now() - t0) / 1000;
      if (sec < 20) show('⏳ 加载 funasr 模型… · ' + elapsedTxt(), Math.min(8, 4 + sec * 0.2));
      else if (sec < 60) show('⏳ 提取音频（ffmpeg 单声道 16kHz）… · ' + elapsedTxt(), 10 + (sec - 20) * 0.3);
      else show('⏳ 语音识别中（最耗时一步，视频越长越久）… · ' + elapsedTxt(), 20 + Math.min(70, (sec - 60) / 60 * 7));
    }, 1000);

    postJSON(API + '/short_video_stage2_run', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hotwords: hotwords, sd_switch: 'no'
    }).then(function(r) {
      clearInterval(timer);
      var sec = Math.round((Date.now() - t0) / 1000);
      if (r && r.ok) {
        var st = r.stage2 || {};
        var chars = st.srt_chars || 0;
        if (bar) { bar.style.width = '100%'; bar.classList.add('slirn-progress-bar--done'); }
        show('✅ 字幕提取完成：' + chars + ' 字 · 总耗时 ' + _fmtElapsed(sec), 100);
        setBtn('提取字幕', false);
        toast('Stage 2 提取完成（' + _fmtElapsed(sec) + '）');
        openProject(ctx.taskId, ctx.projectId);
      } else {
        var e = (r && ((r.stage2 && r.stage2.error) || r.error)) || 'Stage 2 提取失败';
        if (bar) { bar.style.width = '100%'; bar.classList.add('slirn-progress-bar--failed'); }
        show('❌ ' + e + ' · 总耗时 ' + _fmtElapsed(sec), 100);
        setBtn('提取字幕', false);
        toast(e, 'error');
      }
    }).catch(function(err) {
      clearInterval(timer);
      if (bar) { bar.style.width = '100%'; bar.classList.add('slirn-progress-bar--failed'); }
      show('❌ 网络错误：' + (err && err.message || err), 100);
      setBtn('提取字幕', false);
      toast('网络错误：' + (err && err.message || err), 'error');
    }).then(function() {
      window._slirnSvStage2Running = false;
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

  // REQ-20261015-120：读时长区间输入（[下限, 上限] 秒）；输入框缺失/非法
  // → null（后端回退到上次运行区间或默认 60-90）。只做最基本的反转保护，
  // 完整钳制由后端 _norm_duration_range 兜底。
  function _stage3DurationRange() {
    var mn = document.getElementById('slirn-stage3-dmin');
    var mx = document.getElementById('slirn-stage3-dmax');
    if (!mn || !mx) return [null, null];
    var a = parseFloat(mn.value), b = parseFloat(mx.value);
    if (isNaN(a) || isNaN(b)) return [null, null];
    if (a > b) { var t = a; a = b; b = t; }
    return [a, b];
  }

  // REQ-20261015-121：系统提示词模板按当前三个输入框的值渲染。
  // 占位符与后端 stage3_system_prompt() 同一套（{n}/{dmin}/{dmax}），
  // 公式双端一致：值取整展示（与后端 {dmin:.0f} 口径一致）。
  // REQ-20261016-127：去掉「行数 × 平均 2s」折算类占位符
  // （{dmin2}/{dmax2}/{ex_sec}/{ex_lines}）。
  function _stage3RenderSystemPrompt(tmpl) {
    var dur = _stage3DurationRange();
    var dmin = dur[0], dmax = dur[1];
    if (!dmin || !dmax) { dmin = 60; dmax = 90; }
    if (dmax < dmin) { var t = dmin; dmin = dmax; dmax = t; }
    var nEl = document.getElementById('slirn-stage3-n');
    var n = parseInt(nEl ? nEl.value : '', 10);
    if (!n || n < 1) n = 4;
    dmin = Math.round(dmin); dmax = Math.round(dmax);
    var vals = { n: n, dmin: dmin, dmax: dmax };
    var out = tmpl;
    Object.keys(vals).forEach(function(k) {
      out = out.split('{' + k + '}').join(String(vals[k]));
    });
    return out;
  }

  // REQ-20261013-111：Stage 3 解析 — 进度卡 + 防重入 + 阶段文字式自估算
  function stage3Run(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    // 防重入：闭包标志 + 按钮 disabled 双保险
    if (btn && btn.disabled) return;
    if (window._slirnSvStage3Running) {
      toast('解析正在执行中，请勿重复点击', 'warn'); return;
    }
    window._slirnSvStage3Running = true;
    // REQ-099 修订：快速模板 UI 已移除，template 固定默认值（仅作后端兜底链）
    var template = 'hook_first';
    var n = parseInt((document.getElementById('slirn-stage3-n') || {}).value || '4', 10);
    // REQ-20261015-120：时长区间（秒）直接取用户设置，后端拼提示词并按此核查
    var dur = _stage3DurationRange();
    var prompt = _stage3PromptText();
    var useImage = _stage3UseImage();

    var status = document.getElementById('slirn-sv-stage3-status');
    var msg = status ? status.querySelector('.slirn-sv-stage3-msg') : null;
    var bar = status ? status.querySelector('.slirn-sv-stage3-bar') : null;
    var setBtn = function(txt, dis) {
      if (btn) { btn.textContent = txt; btn.disabled = !!dis; }
    };
    var show = function(t, pct) {
      if (status) status.style.display = '';
      if (msg) { msg.style.display = ''; msg.textContent = t; }
      if (bar && pct != null) bar.style.width = pct + '%';
    };

    setBtn('🔍 解析中…', true);
    show('⏳ 正在启动解析（请稍候，单次多模态大模型调用可能 10s-3min）…', 5);
    toast('解析短视频内容中…（字幕' + (useImage ? ' + 截图' : '') + ' + 提示词发给 AI）');

    // 阶段进度（每 3s 推一档；后端单次调用耗时方差大，自估算保 UX 流畅）
    var stages = [
      { pct: 15, txt: '⏳ 准备字幕 + 提示词…' },
      { pct: 35, txt: useImage ? '⏳ OCR 解析截图…' : '⏳ 构造请求载荷…' },
      { pct: 60, txt: '⏳ 调用大模型（单次多模态，可能耗时较久）…' },
      { pct: 85, txt: '⏳ 解析模型响应（分组 + 校验）…' }
    ];
    var stIdx = 0;
    var stageTimer = setInterval(function() {
      if (stIdx < stages.length) {
        show(stages[stIdx].txt, stages[stIdx].pct);
        stIdx++;
      } else {
        // 卡在 90% 等待响应
        show('⏳ 仍在等待大模型响应（请耐心，可能 1-3 分钟）…', 90);
      }
    }, 3000);

    postJSON(API + '/short_video_stage3_run', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      template: template, n_clips: n,
      prompt: prompt, use_image: useImage,
      duration_min_sec: dur[0], duration_max_sec: dur[1]
    }).then(function(r) {
      clearInterval(stageTimer);
      if (r && r.ok) {
        if (bar) {
          bar.style.width = '100%';
          bar.classList.add('slirn-progress-bar--done');
        }
        var st = r.stage3 || {};
        var hlCount = (st.highlights || []).length || (st.count || 0);
        show('✅ 解析完成：共 ' + hlCount + ' 条短视频片段', 100);
        setBtn('🔍 解析', false);
        toast('解析完成');
        openProject(ctx.taskId, ctx.projectId);
      } else {
        if (bar) {
          bar.style.width = '100%';
          bar.classList.add('slirn-progress-bar--failed');
        }
        show('❌ ' + ((r && r.error) || 'Stage 3 解析失败'), 100);
        setBtn('🔍 解析', false);
        toast((r && r.error) || 'Stage 3 失败', 'error');
      }
    }).catch(function(err) {
      clearInterval(stageTimer);
      if (bar) {
        bar.style.width = '100%';
        bar.classList.add('slirn-progress-bar--failed');
      }
      show('❌ 网络错误：' + (err && err.message || err), 100);
      setBtn('🔍 解析', false);
      toast('网络错误：' + (err && err.message || err), 'error');
    }).then(function() {  // finally
      window._slirnSvStage3Running = false;
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
        + '该片段的时间段与字幕内容将被替换，下游 Stage 4/5/6/7 重置。')) return;
    toast('片段 #' + hlIndex + ' 重新解析中…');
    // REQ-20261015-120：单条重解析同样带当前时长区间设置
    var dur = _stage3DurationRange();
    postJSON(API + '/short_video_stage3_rerun_one', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hl_index: hlIndex,
      prompt: _stage3PromptText(),
      use_image: _stage3UseImage(),
      duration_min_sec: dur[0], duration_max_sec: dur[1]
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

  // 修订十：一键引用系统级提示词（内置在系统里，独立于默认/历史两级存储；
  // 填入文本域后可继续编辑，再按需「设为默认」——覆盖确认逻辑与「载入」一致）。
  // REQ-20261015-121：优先用 data-prompt-tmpl 模板 + 当前输入框值渲染
  // （片段数 / 每条时长上下限动态代入）；模板缺失时回退 data-prompt 默认全文。
  function stage3UseSystemPrompt(btn) {
    var ta = document.getElementById('slirn-stage3-prompt');
    if (!ta) return;
    var tmpl = btn.getAttribute('data-prompt-tmpl') || '';
    var text = tmpl ? _stage3RenderSystemPrompt(tmpl)
      : (btn.getAttribute('data-prompt') || '');
    if (!text) return;
    if (ta.value && ta.value.trim() && !window.confirm(
        '文本域已有内容，用系统提示词覆盖它？')) return;
    ta.value = text;
    toast('已引用系统提示词（条数 / 时长上下限按当前设置代入）');
  }

  function stage3ClearPrompt() {
    var ta = document.getElementById('slirn-stage3-prompt');
    if (!ta) return;
    if (ta.value && !window.confirm('清空当前提示词文本域？')) return;
    ta.value = '';
  }

  // REQ-20261016-128：从历史列表删除一条；若是默认则同步清空默认标记。
  function stage3DeletePrompt(btn) {
    if (!btn) return;
    var prompt = btn.getAttribute('data-prompt') || '';
    var stage = btn.getAttribute('data-stage') || 'stage3';
    if (!prompt) return toast('该条目无 prompt 文本', 'error');
    if (!window.confirm('从历史列表删除这条提示词？\n（若是默认将同时清除默认标记）')) return;
    postJSON(API + '/short_video_prompt_history_delete', {
      prompt: prompt, stage: stage
    }).then(function(r) {
      if (!r || !r.ok) return toast((r && r.error) || '删除失败', 'error');
      _refreshStage3History(r.history_html);
      toast(r.toast || '已删除');
    });
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

  // REQ-20261013-111：Stage 4 重新优化 — 进度卡 + 防重入 + N/M 计数式自估算
  function stage4OptimizeRun(btn) {
    // REQ-20261013-100 + REQ-20261013-108：Stage 4 内容优化 — LLM 按标题逐条
    // 优化 Stage 3 字幕（切分长行 / 修改文字 / 删除冗余行），结果写回
    // highlights.json；完成后刷新页面。文本域中的提示词作为「补充要求」
    // 追加到 system prompt 尾部（也写 pipeline.stage4_optimize.prompt_user）。
    var ctx = innerCtx(); if (!ctx) return;
    // 防重入：闭包标志 + 按钮 disabled 双保险
    if (btn && btn.disabled) return;
    if (window._slirnSvStage4Running) {
      toast('内容优化正在执行中，请勿重复点击', 'warn'); return;
    }
    window._slirnSvStage4Running = true;

    // N = highlights.length（从 DOM tab 数量取，因为 innerCtx 不带 project）
    var total = document.querySelectorAll('.slirn-stage4-hl-tab').length || 1;
    var prompt = _stage4PromptText();

    var status = document.getElementById('slirn-sv-stage4-status');
    var msg = status ? status.querySelector('.slirn-sv-stage4-msg') : null;
    var bar = status ? status.querySelector('.slirn-sv-stage4-bar') : null;
    var setBtn = function(txt, dis) {
      if (btn) { btn.textContent = txt; btn.disabled = !!dis; }
    };
    var show = function(t) {
      if (status) status.style.display = '';
      if (msg) { msg.style.display = ''; msg.textContent = t; }
    };

    setBtn('✨ 重新优化中…', true);
    show('⏳ 正在启动内容优化…');
    toast('内容优化中（每条短视频调一次大模型）…');

    // 前端自估算进度（每 1500ms 递增 idx 1/2/3.../total）
    var idx = 0;
    var pollTimer = setInterval(function() {
      if (idx < total) idx++;
      var pct = Math.round(idx / total * 100);
      if (bar) bar.style.width = pct + '%';
      show('⏳ 正在优化第 ' + idx + '/' + total + ' 条字幕（每条约 5-30s，请耐心等待）');
    }, 1500);

    postJSON(API + '/short_video_stage4_optimize', {
      task_id: ctx.taskId, project_id: ctx.projectId, prompt: prompt
    }).then(function(r) {
      clearInterval(pollTimer);
      if (r && r.ok) {
        var st = r.stage4_optimize || {};
        var hls = st.highlights || [];
        var fixedTot = 0, splitTot = 0, delTot = 0;
        hls.forEach(function(h) {
          fixedTot += h.fixed || 0; splitTot += h.split || 0; delTot += h.deleted || 0;
        });
        if (bar) {
          bar.style.width = '100%';
          bar.classList.add('slirn-progress-bar--done');
        }
        show('✅ 内容优化完成：修改 ' + fixedTot + ' 行 · 切分 ' + splitTot
          + ' 行 · 删除 ' + delTot + ' 行');
        setBtn('✨ 重新优化', false);
        toast('内容优化完成：修改 ' + fixedTot + ' 行 · 切分 ' + splitTot
              + ' 行 · 删除 ' + delTot + ' 行');
        openProject(ctx.taskId, ctx.projectId);
      } else {
        if (bar) {
          bar.style.width = '100%';
          bar.classList.add('slirn-progress-bar--failed');
        }
        show('❌ ' + ((r && r.error) || 'Stage 4 内容优化失败'));
        setBtn('✨ 重新优化', false);
        toast((r && r.error) || 'Stage 4 内容优化失败', 'error');
      }
    }).catch(function(err) {
      clearInterval(pollTimer);
      if (bar) {
        bar.style.width = '100%';
        bar.classList.add('slirn-progress-bar--failed');
      }
      show('❌ 网络错误：' + (err && err.message || err));
      setBtn('✨ 重新优化', false);
      toast('网络错误：' + (err && err.message || err), 'error');
    }).then(function() {  // finally
      window._slirnSvStage4Running = false;
    });
  }

  // ---------- REQ-20261013-108：Stage 4 提示词 + 手动编辑 + 预览 + 初剪 ----------

  function _stage4PromptText() {
    var ta = document.getElementById('slirn-stage4-prompt');
    return ta ? String(ta.value || '').trim() : '';
  }

  function _refreshStage4History(historyHtml) {
    if (!historyHtml) return;
    var list = document.querySelector('.slirn-stage4-hist-list');
    if (list) list.innerHTML = historyHtml;
    var box = document.querySelector('.slirn-stage4-prompts-section');
    if (box) box.open = true;
  }

  function stage4UseSystemPrompt(btn) {
    var ta = document.getElementById('slirn-stage4-prompt');
    if (!ta) return;
    var text = btn.getAttribute('data-prompt') || '';
    if (!text) return;
    if (ta.value && ta.value.trim() && !window.confirm(
        '文本域已有内容，用系统提示词覆盖它？')) return;
    ta.value = text;
    toast('已引用系统提示词');
  }

  function stage4ClearPrompt() {
    var ta = document.getElementById('slirn-stage4-prompt');
    if (!ta) return;
    if (ta.value && !window.confirm('清空当前提示词文本域？')) return;
    ta.value = '';
  }

  function stage4LoadPrompt(btn) {
    var ta = document.getElementById('slirn-stage4-prompt');
    if (!ta) return;
    var text = btn.getAttribute('data-prompt') || '';
    if (!text) return;
    if (ta.value && ta.value.trim() && !window.confirm(
        '文本域已有内容，用这条历史提示词覆盖它？')) return;
    ta.value = text;
    toast('已载入历史提示词');
  }

  function stage4ToggleHist(btn) {
    var item = btn.closest ? btn.closest('.slirn-stage4-hist-item') : null;
    var full = item ? item.querySelector('.slirn-stage4-hist-full') : null;
    if (full) full.hidden = !full.hidden;
  }

  function stage4SaveDefault() {
    var prompt = _stage4PromptText();
    if (!prompt) return toast('提示词为空，无法设为默认', 'error');
    postJSON(API + '/short_video_save_default_prompt', {
      prompt: prompt, stage: 'stage4'
    }).then(function(r) {
      if (r && r.ok) {
        _refreshStage4History(r.history_html);
        toast('已设为 Stage 4 默认提示词');
      } else toast((r && r.error) || '保存失败', 'error');
    });
  }

  function stage4SaveHistory() {
    var prompt = _stage4PromptText();
    if (!prompt) return toast('提示词为空，无法存入列表', 'error');
    _postStage4PromptHistory(prompt, false);
  }

  function _postStage4PromptHistory(prompt, confirmOverwrite) {
    postJSON(API + '/short_video_prompt_history_save', {
      prompt: prompt, confirm: !!confirmOverwrite, stage: 'stage4'
    }).then(function(r) {
      if (!r || !r.ok) return toast((r && r.error) || '保存失败', 'error');
      if (r.status === 'need_confirm') {
        if (window.confirm('提示词列表已满 ' + (r.max || 10) + ' 条。\n'
            + '继续保存将覆盖最旧的一条，是否覆盖？')) {
          _postStage4PromptHistory(prompt, true);
        } else {
          toast('已取消保存');
        }
        return;
      }
      _refreshStage4History(r.history_html);
      toast('已存入 Stage 4 提示词列表');
    });
  }

  // 拉取 stage3 + stage4 提示词 + 系统常量 + 项目级 prompt_user
  // 写入对应文本域与历史列表（不覆盖正在编辑的内容 — 只在为空时填）
  function loadStage4Prompts() {
    var ctx = innerCtx(); if (!ctx) return;
    postJSON(API + '/short_video_load_prompts', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (!r || !r.ok) return;
      var settings = r.settings || {};
      var sys = r.system_prompts || {};
      var proj = r.project_prompts || {};
      var hh = r.history_html || {};
      // stage4 历史列表
      _refreshStage4History(hh.stage4);
      // 文本域：项目级 prompt_user 优先；为空 → 全局默认；再空 → 留空（不强行填系统）
      var ta4 = document.getElementById('slirn-stage4-prompt');
      if (ta4 && !ta4.value.trim()) {
        if (proj.stage4) {
          ta4.value = proj.stage4;
        } else {
          var def4 = (settings.stage4 || {}).default_prompt;
          if (def4 && def4.prompt) ta4.value = def4.prompt;
        }
      }
    });
  }

  // 整行替换 text（fix）
  function stage4LineEdit(btn) {
    var row = btn.closest ? btn.closest('.slirn-stage4-sub-row') : null;
    if (!row) return;
    var orig = row.getAttribute('data-orig-text') || row.getAttribute('data-eff-text') || '';
    var newText = window.prompt('整行替换文字（原行内容已预填）：', orig);
    if (newText === null) return;
    var hl = parseInt(row.getAttribute('data-hl-index') || '0', 10);
    var sid = parseInt(row.getAttribute('data-seg-id') || '0', 10);
    if (!hl || !sid) return;
    var ctx = innerCtx(); if (!ctx) return;
    postJSON(API + '/short_video_stage4_line_edit', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hl_index: hl, src_index: sid, text: newText
    }).then(function(r) {
      if (r && r.ok) {
        row.setAttribute('data-eff-text', newText);
        row.setAttribute('data-orig-text', newText);
        var textEl = row.querySelector('.slirn-stage4-sub-text');
        if (textEl) textEl.textContent = newText;
        toast('已保存');
      } else toast((r && r.error) || '保存失败', 'error');
    });
  }

  // 整行删除/恢复（toggle）
  function stage4LineToggle(btn) {
    var row = btn.closest ? btn.closest('.slirn-stage4-sub-row') : null;
    if (!row) return;
    var hl = parseInt(row.getAttribute('data-hl-index') || '0', 10);
    var sid = parseInt(row.getAttribute('data-seg-id') || '0', 10);
    if (!hl || !sid) return;
    var ctx = innerCtx(); if (!ctx) return;
    postJSON(API + '/short_video_stage4_line_toggle', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hl_index: hl, src_index: sid
    }).then(function(r) {
      if (!r || !r.ok) return toast((r && r.error) || '操作失败', 'error');
      var marked = !!r.marked;
      row.setAttribute('data-deleted', marked ? '1' : '0');
      row.classList.toggle('slirn-stage4-sub-row-deleted', marked);
      // 切按钮图标与 title
      var btn2 = row.querySelector('[data-action="sv-stage4-line-toggle"]');
      if (btn2) {
        btn2.textContent = marked ? '✚' : '🗑️';
        btn2.setAttribute('title', marked ? '恢复本行' : '删除本行');
      }
      // 增删 badge
      var actions = row.querySelector('.slirn-stage4-sub-actions');
      var oldBadge = row.querySelector('.slirn-sv-badge-failed');
      if (oldBadge) oldBadge.remove();
      if (marked && actions) {
        var b = document.createElement('span');
        b.className = 'slirn-sv-badge slirn-sv-badge-failed';
        b.textContent = '🗑 已删';
        actions.insertBefore(b, actions.firstChild);
      }
      toast(marked ? '已删除' : '已恢复');
    });
  }

  // 一行切分为多段（split）— 用户输入每段文字（按 | 分割）
  function stage4LineResplit(btn) {
    var row = btn.closest ? btn.closest('.slirn-stage4-sub-row') : null;
    if (!row) return;
    var orig = row.getAttribute('data-eff-text') || row.getAttribute('data-orig-text') || '';
    var raw = window.prompt(
      '切分本行为多段（用 | 分隔；按字数比例内插时间戳）：', orig);
    if (raw === null) return;
    var parts = String(raw).split('|').map(function(s) { return s.trim(); })
      .filter(function(s) { return s; });
    if (parts.length < 2) return toast('至少需要 2 段（用 | 分隔）', 'error');
    var hl = parseInt(row.getAttribute('data-hl-index') || '0', 10);
    var sid = parseInt(row.getAttribute('data-seg-id') || '0', 10);
    if (!hl || !sid) return;
    var ctx = innerCtx(); if (!ctx) return;
    postJSON(API + '/short_video_stage4_resplit', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hl_index: hl, src_index: sid, subs: parts
    }).then(function(r) {
      if (r && r.ok) {
        toast('已切分 — 刷新页面查看完整编辑面');
        openProject(ctx.taskId, ctx.projectId);
      } else toast((r && r.error) || '切分失败', 'error');
    });
  }

  // 段内重排 — 用户输入 src_index 列表（白名单：未列入的按原序追加）
  // data-order="1,3,2" 形式；不传 data-order 则清空重排
  function stage4LineReorder(btn) {
    var card = btn.closest ? btn.closest('.slirn-stage4-clip') : null;
    if (!card) return;
    var hl = parseInt(card.getAttribute('data-hl-index') || '0', 10);
    if (!hl) return;
    var orderAttr = btn.getAttribute('data-order') || '';
    var order = [];
    if (orderAttr) {
      order = orderAttr.split(',').map(function(s) { return parseInt(s.trim(), 10); })
        .filter(function(n) { return n > 0; });
    }
    var ctx = innerCtx(); if (!ctx) return;
    postJSON(API + '/short_video_stage4_line_reorder', {
      task_id: ctx.taskId, project_id: ctx.projectId,
      hl_index: hl, order: order
    }).then(function(r) {
      if (r && r.ok) {
        toast(order.length ? ('已重排 ' + order.length + ' 行') : '已清除重排');
        openProject(ctx.taskId, ctx.projectId);
      } else toast((r && r.error) || '重排失败', 'error');
    });
  }

  // 预览该高亮（按 per-hl 编辑后拼一段 mp4）
  function stage4Preview(btn) {
    var card = btn.closest ? btn.closest('.slirn-stage4-clip') : null;
    if (!card) return;
    var hl = parseInt(card.getAttribute('data-hl-index') || '0', 10);
    if (!hl) return;
    var ctx = innerCtx(); if (!ctx) return;
    var status = card.querySelector('.slirn-stage4-clip-preview-status');
    if (status) status.textContent = '（生成中…）';
    btn.disabled = true;
    postJSON(API + '/short_video_stage4_preview', {
      task_id: ctx.taskId, project_id: ctx.projectId, hl_index: hl
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) {
        if (status) status.textContent = '失败：' + ((r && r.error) || '');
        return toast((r && r.error) || '预览失败', 'error');
      }
      if (status) status.textContent = '✅ 已生成 ' + (r.segments_count || 0)
        + ' 段 / ' + (r.duration_sec || 0).toFixed(1) + 's';
      // 移除旧 video
      var old = card.querySelector('video.slirn-stage4-clip-preview-video');
      if (old) old.remove();
      var v = document.createElement('video');
      v.className = 'slirn-stage4-clip-preview-video';
      v.src = r.mp4_url;
      v.controls = true;
      v.preload = 'metadata';
      v.style.maxWidth = '100%';
      v.style.marginTop = '8px';
      card.appendChild(v);
      toast('预览已生成');
    });
  }

  // 跳到 Stage 5 — 单条粗剪
  function stage4GoCoarse(btn) {
    var card = btn.closest ? btn.closest('.slirn-stage4-clip') : null;
    if (!card) return;
    var hl = parseInt(card.getAttribute('data-hl-index') || '0', 10);
    if (!hl) return;
    var ctx = innerCtx(); if (!ctx) return;
    if (typeof switchStage === 'function') switchStage('stage5');
    else location.hash = '#stage5';
    toast('启动仅该条粗剪…');
    postJSON(API + '/short_video_stage4_go_coarse', {
      task_id: ctx.taskId, project_id: ctx.projectId, hl_index: hl
    }).then(function(r) {
      if (r && r.ok) {
        toast('已切到 Stage 5，单条粗剪已启动');
        if (typeof openProject === 'function') {
          setTimeout(function() { openProject(ctx.taskId, ctx.projectId); }, 800);
        }
      } else toast((r && r.error) || '启动失败', 'error');
    });
  }

  // 跳到 Stage 5 — 全量粗剪
  function stage4GoCoarseAll() {
    var ctx = innerCtx(); if (!ctx) return;
    if (typeof switchStage === 'function') switchStage('stage5');
    else location.hash = '#stage5';
    toast('启动全量粗剪…');
    postJSON(API + '/short_video_stage4_go_coarse', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (r && r.ok) {
        toast('已切到 Stage 5，全量粗剪已启动');
        if (typeof openProject === 'function') {
          setTimeout(function() { openProject(ctx.taskId, ctx.projectId); }, 800);
        }
      } else toast((r && r.error) || '启动失败', 'error');
    });
  }

  function stage4Run() {
    var ctx = innerCtx(); if (!ctx) return;
    toast('粗剪合成中…');
    postJSON(API + '/short_video_stage4_run', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (r && r.ok) { toast('Stage 5 完成'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || 'Stage 5 失败', 'error');
    });
  }

  // REQ-20261013-103：Stage 5 粗剪异步导出（仿长视频 compose_rough + setInterval 轮询）
  function stage5Export(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var tid = btn.getAttribute('data-task-id') || ctx.taskId;
    var pid = btn.getAttribute('data-project-id') || ctx.projectId;
    var setBtn = function(txt, dis) { btn.textContent = txt; btn.disabled = !!dis; };
    var status = document.getElementById('slirn-sv-stage5-status');
    var msg = status ? status.querySelector('.slirn-sv-stage5-msg') : null;
    var bar = status ? status.querySelector('.slirn-progress-bar') : null;
    var show = function(t) { if (msg) { msg.style.display = ''; msg.textContent = t; } };
    setBtn('🎬 合成中…', true);
    show('⏳ 正在启动合成…');
    postJSON(API + '/short_video_stage5_export', {
      task_id: tid, project_id: pid
    }).then(function(r) {
      if (!r || !r.ok) {
        setBtn('🎬 合成粗剪', false);
        show('');
        if (msg) msg.style.display = 'none';
        toast('❌ ' + (r && r.error ? r.error : '启动失败'), 'error');
        return;
      }
      toast(r.toast || '🎬 粗剪已启动');
      var timer = setInterval(function() {
        postJSON(API + '/short_video_stage5_export_status', {
          task_id: tid, project_id: pid
        }).then(function(s) {
          if (!s || !s.ok || !s.job) return;
          var j = s.job;
          if (j.state === 'running') {
            var pct = Math.max(1, Math.min(99, Math.round(j.progress || 0)));
            setBtn('🎬 合成中… ' + pct + '%', true);
            if (bar) bar.style.width = pct + '%';
            show('⏳ ' + (j.stage || '合成中') + ' ' + pct + '% — 可继续其它操作，完成后自动显示预览');
            return;
          }
          clearInterval(timer);
          if (j.state === 'done') {
            setBtn('🎬 重新合成', false);
            if (bar) bar.style.width = '100%';
            show('✅ 粗剪成片已生成 — 下方预览每条 clip');
            toast('✅ 粗剪完成');
            openProject(tid, pid);
          } else {
            setBtn('🎬 重新合成', false);
            show('❌ ' + (j.error || '合成失败'));
            toast('❌ ' + (j.error || '合成失败'), 'error');
          }
        });
      }, 1500);
    });
  }
  // REQ-20261013-103：Stage 5 删除（仿 compose_rough_delete）
  function stage5Delete(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var tid = btn.getAttribute('data-task-id') || ctx.taskId;
    var pid = btn.getAttribute('data-project-id') || ctx.projectId;
    if (!window.confirm('删除「粗剪成品」（' + (btn.getAttribute('data-count') || 'N') + ' 条 mp4 + srt）？\n'
        + '删除后 Stage 6/7 也会重置，需重新合成。')) return;
    btn.disabled = true;
    postJSON(API + '/short_video_stage5_delete', {
      task_id: tid, project_id: pid
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) {
        toast('❌ ' + (r && r.error ? r.error : '删除失败'), 'error');
        return;
      }
      toast(r.toast || '🗑️ 已删除');
      openProject(tid, pid);
    });
  }
  // 连续 SRT 复制（从预览面板 <pre> 读，或服务端拼）
  function stage5SubsCopy(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var tid = btn.getAttribute('data-task-id') || ctx.taskId;
    var pid = btn.getAttribute('data-project-id') || ctx.projectId;
    postJSON(API + '/short_video_stage5_preview_subs', {
      task_id: tid, project_id: pid
    }).then(function(r) {
      if (!r || !r.ok || !r.srt) {
        toast('❌ ' + (r && r.error ? r.error : '获取 SRT 失败'), 'error');
        return;
      }
      var srt = r.srt;
      var done = function() { toast('📋 已复制 ' + r.clips + ' 段连续 SRT'); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(srt).then(done, function() { _svFallbackCopy(srt, done); });
      } else {
        _svFallbackCopy(srt, done);
      }
    });
  }
  function _svFallbackCopy(text, cb) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); cb && cb(); } catch (e) { toast('❌ 复制失败', 'error'); }
    document.body.removeChild(ta);
  }
  // 连续 SRT 下载
  function stage5SubsDownload(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var tid = btn.getAttribute('data-task-id') || ctx.taskId;
    var pid = btn.getAttribute('data-project-id') || ctx.projectId;
    btn.disabled = true;
    postJSON(API + '/short_video_stage5_preview_subs', {
      task_id: tid, project_id: pid
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok || !r.srt) {
        toast('❌ ' + (r && r.error ? r.error : '获取 SRT 失败'), 'error');
        return;
      }
      var blob = new Blob([r.srt], {type: 'text/plain;charset=utf-8'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'short_video_coarse_' + tid + '_' + pid + '.srt';
      document.body.appendChild(a); a.click();
      setTimeout(function() { URL.revokeObjectURL(url); document.body.removeChild(a); }, 100);
      toast('⬇️ 已下载 ' + r.clips + ' 段连续 SRT');
    });
  }
  // 单条 clip 的 SRT 复制（按 coarse_srt 文件路径）
  function stage5ClipSubsCopy(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var tid = btn.getAttribute('data-task-id') || ctx.taskId;
    var pid = btn.getAttribute('data-project-id') || ctx.projectId;
    var idx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!idx) return;
    // 文件路径在 hl.coarse_srt（project 内的相对路径），先拿项目查路径
    postJSON(API + '/short_video_get', {task_id: tid, project_id: pid}).then(function(r) {
      if (!r || !r.ok) return toast('读取项目失败', 'error');
      var coarse = ((r.project || {}).pipeline || {}).stage5_coarse || {};
      var hl = (coarse.highlights || []).find(function(h) { return h.index === idx; }) || {};
      if (!hl.coarse_srt) return toast('该 clip 没有 SRT', 'error');
      // 通过 file_url 直接拉内容
      var srtName = (hl.coarse_srt.split('/').pop() || '').split('\\').pop();
      fetch('/gradio_api/file=tasks/' + tid + '/short_video/' + pid + '/stage4/' + srtName, {credentials: 'same-origin'})
        .then(function(rr) { return rr.ok ? rr.text() : Promise.reject(new Error('HTTP ' + rr.status)); })
        .then(function(srt) {
          if (!srt || !srt.trim()) return toast('SRT 为空', 'error');
          var done = function() { toast('📋 已复制 #' + idx + ' SRT'); };
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(srt).then(done, function() { _svFallbackCopy(srt, done); });
          } else {
            _svFallbackCopy(srt, done);
          }
        })
        .catch(function(e) { toast('❌ 读取 SRT 失败: ' + e, 'error'); });
    });
  }
  // 单条 clip 的 SRT 下载（直接 <a download>）
  function stage5ClipSubsDownload(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var tid = btn.getAttribute('data-task-id') || ctx.taskId;
    var pid = btn.getAttribute('data-project-id') || ctx.projectId;
    var idx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!idx) return;
    postJSON(API + '/short_video_get', {task_id: tid, project_id: pid}).then(function(r) {
      if (!r || !r.ok) return toast('读取项目失败', 'error');
      var coarse = ((r.project || {}).pipeline || {}).stage5_coarse || {};
      var hl = (coarse.highlights || []).find(function(h) { return h.index === idx; }) || {};
      if (!hl.coarse_srt) return toast('该 clip 没有 SRT', 'error');
      var srtName = (hl.coarse_srt.split('/').pop() || '').split('\\').pop();
      var url = '/gradio_api/file=tasks/' + tid + '/short_video/' + pid + '/stage4/' + srtName;
      var a = document.createElement('a');
      a.href = url; a.download = srtName;
      document.body.appendChild(a); a.click();
      setTimeout(function() { document.body.removeChild(a); }, 100);
      toast('⬇️ 已下载 #' + idx + ' SRT');
    });
  }

  // REQ-20261016-132：Stage 5 视频预览浮窗（不遮父页 + 可拖动 + 可调大小）
  // 单一浮窗（lazy 创建在 document.body），同一时刻只能开 1 个；点 × 或再点
  // 同一按钮关闭。拖动通过 title-bar mousedown；调大小通过 CSS resize 句柄。
  function ensureSvVideoPopup() {
    var el = document.getElementById('slirn-sv-video-popup');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'slirn-sv-video-popup';
    el.className = 'slirn-sv-video-popup';
    el.hidden = true;
    el.innerHTML =
      '<div class="slirn-sv-video-popup-head">' +
        '<span class="slirn-sv-video-popup-title">预览</span>' +
        '<button type="button" class="slirn-sv-video-popup-close" ' +
        'aria-label="关闭" title="关闭">×</button>' +
      '</div>' +
      '<div class="slirn-sv-video-popup-body">' +
        '<video controls preload="metadata"></video>' +
      '</div>' +
      '<div class="slirn-sv-video-popup-resize" aria-hidden="true"></div>';
    document.body.appendChild(el);
    // 关闭按钮
    el.querySelector('.slirn-sv-video-popup-close').addEventListener('click', function() {
      el.hidden = true;
      var v = el.querySelector('video');
      if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
    });
    // ESC 关闭
    el.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        el.hidden = true;
        var v = el.querySelector('video');
        if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
      }
    });
    // 拖动（按 title bar）
    bindSvPopupDrag(el);
    return el;
  }

  function bindSvPopupDrag(el) {
    var head = el.querySelector('.slirn-sv-video-popup-head');
    if (!head) return;
    var dragging = false, offX = 0, offY = 0;
    head.addEventListener('mousedown', function(e) {
      if (e.target.closest('.slirn-sv-video-popup-close')) return;
      dragging = true;
      var rect = el.getBoundingClientRect();
      offX = e.clientX - rect.left;
      offY = e.clientY - rect.top;
      // 转成 left/top 定位（拖动 + 调大小都依赖绝对坐标）
      el.style.left = rect.left + 'px';
      el.style.top = rect.top + 'px';
      el.style.right = 'auto';
      el.style.bottom = 'auto';
      el.style.width = rect.width + 'px';
      el.style.height = rect.height + 'px';
      el.setAttribute('tabindex', '-1');
      el.focus({preventScroll: true});
      e.preventDefault();
    });
    document.addEventListener('mousemove', function(e) {
      if (!dragging) return;
      var w = el.offsetWidth, h = el.offsetHeight;
      var maxX = Math.max(0, window.innerWidth - w);
      var maxY = Math.max(0, window.innerHeight - h);
      var nx = Math.max(0, Math.min(maxX, e.clientX - offX));
      var ny = Math.max(0, Math.min(maxY, e.clientY - offY));
      el.style.left = nx + 'px';
      el.style.top = ny + 'px';
    });
    document.addEventListener('mouseup', function() {
      dragging = false;
    });
  }

  function svStage5Preview(btn) {
    if (!btn) return;
    var url = btn.getAttribute('data-url') || '';
    var name = btn.getAttribute('data-clip-name') || '预览';
    if (!url) return toast('视频 URL 缺失', 'error');
    var popup = ensureSvVideoPopup();
    // 二次点击同一按钮 → 关闭（UX 友好）
    if (!popup.hidden && popup.getAttribute('data-url') === url) {
      popup.hidden = true;
      var vv = popup.querySelector('video');
      if (vv) { vv.pause(); vv.removeAttribute('src'); vv.load(); }
      return;
    }
    popup.setAttribute('data-url', url);
    popup.querySelector('.slirn-sv-video-popup-title').textContent = name;
    var v = popup.querySelector('video');
    v.src = url;
    v.load();
    // 首次打开 → 右上角定位 + 60% 宽 / 50% 高
    // REQ-20261016-137 bug fix：之前用 popup.offsetWidth 算 left，但此时 popup
    // 还在 hidden=true 状态（display:none），offsetWidth=0 → left=innerWidth-24，
    // 弹窗从屏幕右外开始，宽度 60% 向右延伸，绝大多数不可见。
    // 修复：直接用刚算出的 w/h 推 left，不再读 offsetWidth。
    if (!popup.style.width) {
      var w = Math.min(640, Math.round(window.innerWidth * 0.6));
      var h = Math.min(480, Math.round(window.innerHeight * 0.5));
      popup.style.width = w + 'px';
      popup.style.height = h + 'px';
      popup.style.left = (window.innerWidth - w - 24) + 'px';
      popup.style.top = '80px';
    }
    popup.hidden = false;
    v.play().catch(function() { /* autoplay 阻止忽略 */ });
  }

  // REQ-20261013-104：Stage 6 字幕优化（行级 + 词频 + 批量替换 + 行内切分）
  // 单条 LLM 优化：调 optimize 端点 → 渲染 chips + 行级建议
  function stage6Optimize(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var idx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!idx) return;
    btn.disabled = true;
    var oldText = btn.textContent;
    btn.textContent = '✨ 优化中…';
    postJSON(API + '/short_video_stage6_optimize', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: idx
    }).then(function(r) {
      btn.disabled = false;
      btn.textContent = oldText;
      if (!r || !r.ok) return toast('❌ ' + (r && r.error ? r.error : 'LLM 优化失败'), 'error');
      toast(r.toast || '✨ LLM 优化完成');
      _renderStage6Words(idx, r.words || []);
      _renderStage6Rows(idx, r.occurrences || {});
    });
  }
  // 词频 chips 渲染
  function _renderStage6Rows(clipIdx, occurrences) {
    var wrap = document.querySelector('.slirn-sv-stage6-rows[data-clip-index="' + clipIdx + '"]');
    if (!wrap) return;
    var rows = [];
    Object.keys(occurrences).forEach(function(rid) {
      var occs = occurrences[rid] || [];
      occs.forEach(function(o, i) {
        rows.push(
          '<div class="slirn-sv-stage6-row" data-rid="' + rid + '" data-oi="' + i + '">'
          + '<span class="slirn-sv-muted">行 ' + rid + '</span> '
          + '<code>' + _escHtml(o.before) + '</code> → '
          + '<input type="text" class="slirn-sv-stage6-edit" value="' + _escHtml(o.after) + '" '
          + 'data-rid="' + rid + '" data-oi="' + i + '" style="width:160px; padding:2px 6px;"> '
          + '<button class="slirn-btn-mini" data-action="sv-stage6-row-accept" '
          + 'data-clip-index="' + clipIdx + '" data-rid="' + rid + '" data-oi="' + i + '">✓ 采纳</button> '
          + '<button class="slirn-btn-mini" data-action="sv-stage6-row-reject" '
          + 'data-clip-index="' + clipIdx + '" data-rid="' + rid + '" data-oi="' + i + '">✕ 拒绝</button> '
          + '<button class="slirn-btn-mini" data-action="sv-stage6-row-save" '
          + 'data-clip-index="' + clipIdx + '" data-rid="' + rid + '" data-oi="' + i + '">💾 保存</button>'
          + '</div>'
        );
      });
    });
    wrap.innerHTML = rows.length
      ? rows.join('') + '<button class="slirn-btn slirn-btn-primary" data-action="sv-stage6-save-decisions" '
        + 'data-clip-index="' + clipIdx + '" style="margin-top:6px;">💾 保存本条全部决定</button>'
      : '<div class="slirn-sv-muted">无建议</div>';
  }
  function _escHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function _renderStage6Words(clipIdx, words) {
    var wrap = document.querySelector('.slirn-sv-stage6-words[data-clip-index="' + clipIdx + '"]');
    if (!wrap) return;
    if (!words.length) { wrap.innerHTML = ''; return; }
    var chips = words.slice(0, 12).map(function(w) {
      return '<span class="slirn-sv-chip" title="出现 ' + w.count + ' 次 · 行 ' + (w.lines || []).join(',') + '">'
        + _escHtml(w.after) + ' <small>×' + w.count + '</small></span>';
    });
    wrap.innerHTML = '<div class="slirn-sv-chips">' + chips.join('') + '</div>';
  }
  // 单条 row 保存（直接后端 save_decisions 接受）
  function _stage6RowSave(btn, accept) {
    var ctx = innerCtx(); if (!ctx) return;
    var clipIdx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    var rid = btn.getAttribute('data-rid') || '';
    var editInput = document.querySelector(
      '.slirn-sv-stage6-edit[data-rid="' + rid + '"][data-oi="' + btn.getAttribute('data-oi') + '"]');
    var edit = editInput ? editInput.value : '';
    var decisions = {};
    decisions[rid] = {accept: !!accept, edit: edit};
    postJSON(API + '/short_video_stage6_save_decisions', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: clipIdx, decisions: decisions
    }).then(function(r) {
      if (r && r.ok) { toast('💾 行 ' + rid + ' 已' + (accept ? '采纳' : '拒绝')); }
      else toast('❌ ' + (r && r.error ? r.error : '保存失败'), 'error');
    });
  }
  // 本条全部保存（收集所有 row 的当前 edit + accept 默认 True）
  function stage6SaveDecisions(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var clipIdx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!clipIdx) return;
    var decisions = {};
    document.querySelectorAll('.slirn-sv-stage6-rows[data-clip-index="' + clipIdx + '"] .slirn-sv-stage6-row').forEach(function(r) {
      var rid = r.getAttribute('data-rid');
      var edit = r.querySelector('.slirn-sv-stage6-edit');
      if (rid && edit) decisions[rid] = {accept: true, edit: edit.value};
    });
    postJSON(API + '/short_video_stage6_save_decisions', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: clipIdx, decisions: decisions
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '💾 已保存'); openProject(ctx.taskId, ctx.projectId); }
      else toast('❌ ' + (r && r.error ? r.error : '保存失败'), 'error');
    });
  }
  // 行内切分（占位 — 由前端 prompt 拿 splits 后调后端）
  function stage6Resplit(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var segId = btn.getAttribute('data-seg-id') || prompt('切分行 id（数字）');
    if (!segId) return;
    var splits = prompt('切分子文本（用 | 分隔）', '前半|后半');
    if (!splits) return;
    var clipIdx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    postJSON(API + '/short_video_stage6_resplit', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: clipIdx,
      seg_id: String(segId), splits: splits.split('|').map(function(s) { return s.trim(); })
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '✂️ 已切分'); openProject(ctx.taskId, ctx.projectId); }
      else toast('❌ ' + (r && r.error ? r.error : '切分失败'), 'error');
    });
  }
  // 取消切分
  function stage6Unsplit(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var segId = btn.getAttribute('data-seg-id') || prompt('取消切分的行 id');
    if (!segId) return;
    var clipIdx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    postJSON(API + '/short_video_stage6_unsplit', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: clipIdx, seg_id: String(segId)
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '↩ 已取消'); openProject(ctx.taskId, ctx.projectId); }
      else toast('❌ ' + (r && r.error ? r.error : '取消失败'), 'error');
    });
  }
  // 批量搜索
  function stage6BatchSearch(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var word = (document.querySelector('.slirn-sv-batch-word') || {}).value || '';
    if (!word.trim()) return toast('请输入搜索词', 'error');
    // 当前 clip 数（取当前项目下所有 highlights）
    postJSON(API + '/short_video_get', {task_id: ctx.taskId, project_id: ctx.projectId}).then(function(r) {
      if (!r || !r.ok) return toast('读取项目失败', 'error');
      var hls = ((r.project || {}).pipeline || {}).stage6_refine || {};
      var list = (hls.highlights || []);
      var promises = list.map(function(hl) {
        return postJSON(API + '/short_video_stage6_batch_search', {
          task_id: ctx.taskId, project_id: ctx.projectId,
          index: hl.index || 0, word: word
        });
      });
      Promise.all(promises).then(function(results) {
        var total = 0;
        var lines = [];
        results.forEach(function(r2, i) {
          if (r2 && r2.matches) {
            total += r2.count || 0;
            if (r2.matches.length) lines.push('Clip ' + (i + 1) + ': ' + r2.matches.length + ' 处');
          }
        });
        var out = document.querySelector('.slirn-sv-batch-result');
        if (out) out.innerHTML = '<div class="slirn-sv-muted">共 ' + total + ' 处 · ' + (lines.join(' / ') || '无') + '</div>';
        toast('🔍 找到 ' + total + ' 处');
      });
    });
  }
  // 批量替换（按当前所有 clip 逐条替换）
  function stage6BatchReplace(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var word = (document.querySelector('.slirn-sv-batch-word') || {}).value || '';
    var rep = (document.querySelector('.slirn-sv-batch-replacement') || {}).value || '';
    if (!word.trim()) return toast('请输入搜索词', 'error');
    if (!window.confirm('把当前所有 clip 的 refined srt 中「' + word + '」全部替换为「' + (rep || '（空）') + '」？')) return;
    postJSON(API + '/short_video_get', {task_id: ctx.taskId, project_id: ctx.projectId}).then(function(r) {
      if (!r || !r.ok) return toast('读取项目失败', 'error');
      var hls = (((r.project || {}).pipeline || {}).stage6_refine || {}).highlights || [];
      var promises = hls.map(function(hl) {
        return postJSON(API + '/short_video_stage6_batch_replace', {
          task_id: ctx.taskId, project_id: ctx.projectId,
          index: hl.index || 0, word: word, replacement: rep
        });
      });
      Promise.all(promises).then(function(results) {
        var total = 0;
        results.forEach(function(r2) { if (r2) total += r2.replaced || 0; });
        toast('✅ 替换完成 · ' + total + ' 处');
        openProject(ctx.taskId, ctx.projectId);
      });
    });
  }
  // 行级 row 采纳/拒绝（派发 — 用 data-action 委托）
  document.addEventListener('click', function(ev) {
    var a = ev.target.closest && ev.target.closest('[data-action]');
    if (!a) return;
    var act = a.getAttribute('data-action');
    if (act === 'sv-stage6-row-accept') { ev.preventDefault(); _stage6RowSave(a, true); }
    else if (act === 'sv-stage6-row-reject') { ev.preventDefault(); _stage6RowSave(a, false); }
  });

  // REQ-20261013-105：Stage 7 精剪 v1 完整版
  // 收集当前面板的 params（6 组子字段从 DOM 读）
  function _collectStage7Params() {
    var params = {
      materials: {}, font: {}, output: {}, audio: {}, preview: {}, layout: {},
    };
    // materials
    document.querySelectorAll('.slirn-sv-mat-input').forEach(function(inp) {
      var slot = inp.getAttribute('data-mat-slot');
      if (slot) params.materials[slot] = inp.value || '';
    });
    // font
    document.querySelectorAll('.slirn-sv-font-input').forEach(function(inp) {
      var key = inp.getAttribute('data-font-key');
      if (!key) return;
      var v = inp.value;
      // 布尔字段
      if (key === 'bold' || key === 'bg_enabled') {
        params.font[key] = (v === 'true' || v === true);
      } else if (['size', 'stroke_width', 'bg_opacity', 'bg_radius', 'offset_ms'].includes(key)) {
        params.font[key] = parseFloat(v) || 0;
      } else {
        params.font[key] = v;
      }
    });
    // output
    var res = document.querySelector('.slirn-sv-output-resolution');
    if (res) params.output.resolution = res.value || '720p';
    // audio
    var ae = document.querySelector('.slirn-sv-audio-enabled');
    if (ae) params.audio.enabled = !!ae.checked;
    var av = document.querySelector('.slirn-sv-audio-volume');
    if (av) params.audio.volume_db = parseFloat(av.value) || -6;
    var afi = document.querySelector('.slirn-sv-audio-fadein');
    if (afi) params.audio.fade_in_ms = parseFloat(afi.value) || 500;
    // preview
    var pd = document.querySelector('.slirn-sv-preview-duration');
    if (pd) params.preview.duration_sec = parseInt(pd.value, 10) || 5;
    return params;
  }
  // 保存 6 组子字段
  function stage7SaveParams(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var params = _collectStage7Params();
    postJSON(API + '/short_video_stage7_save_params', {
      task_id: ctx.taskId, project_id: ctx.projectId, params: params
    }).then(function(r) {
      if (r && r.ok) toast(r.toast || '💾 已保存');
      else toast('❌ ' + (r && r.error ? r.error : '保存失败'), 'error');
    });
  }
  // 异步导出（仿 Stage 5 setInterval 轮询）
  function stage7Export(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    // 先保存 params
    stage7SaveParams(btn);
    var setBtn = function(t, d) { btn.textContent = t; btn.disabled = !!d; };
    var status = document.getElementById('slirn-sv-stage7-status');
    var msg = status ? status.querySelector('.slirn-sv-stage7-msg') : null;
    var bar = status ? status.querySelector('.slirn-progress-bar') : null;
    var show = function(t) { if (msg) { msg.style.display = ''; msg.textContent = t; } };
    setBtn('🎬 导出中…', true);
    show('⏳ 正在启动…');
    postJSON(API + '/short_video_stage7_export', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      if (!r || !r.ok) {
        setBtn('💾 导出最终视频', false);
        show('');
        if (msg) msg.style.display = 'none';
        return toast('❌ ' + (r && r.error ? r.error : '启动失败'), 'error');
      }
      toast(r.toast || '🎬 精剪已启动');
      var timer = setInterval(function() {
        postJSON(API + '/short_video_stage7_export_status', {
          task_id: ctx.taskId, project_id: ctx.projectId
        }).then(function(s) {
          if (!s || !s.ok || !s.job) return;
          var j = s.job;
          if (j.state === 'running') {
            var pct = Math.max(1, Math.min(99, Math.round(j.progress || 0)));
            setBtn('🎬 导出中… ' + pct + '%', true);
            if (bar) bar.style.width = pct + '%';
            show('⏳ ' + (j.stage || '导出中') + ' ' + pct + '%');
            return;
          }
          clearInterval(timer);
          if (j.state === 'done') {
            setBtn('💾 重新导出', false);
            if (bar) bar.style.width = '100%';
            show('✅ 精剪完成');
            toast('✅ 精剪完成');
            openProject(ctx.taskId, ctx.projectId);
          } else {
            setBtn('💾 重新导出', false);
            show('❌ ' + (j.error || '失败'));
            toast('❌ ' + (j.error || '失败'), 'error');
          }
        });
      }, 2000);
    });
  }
  // 预览（按 index 调 ffmpeg 切段）
  function stage7Preview(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var idx = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!idx) return;
    var dur = parseInt((document.querySelector('.slirn-sv-preview-duration') || {}).value || '5', 10);
    btn.disabled = true;
    postJSON(API + '/short_video_stage7_preview', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: idx, duration_sec: dur
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) return toast('❌ ' + (r && r.error ? r.error : '预览失败'), 'error');
      toast(r.toast || '🎬 预览已生成');
      // 弹视频预览
      var url = r.preview_path;
      if (url) {
        var fname = url.split(/[\\/]/).pop();
        var fileUrl = '/gradio_api/file=tasks/' + ctx.taskId + '/short_video/' + ctx.projectId + '/stage6/' + fname;
        previewVideo({getAttribute: function() { return fileUrl; }});
      }
    });
  }
  // bg 检测
  function stage7DetectBg(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    btn.disabled = true;
    postJSON(API + '/short_video_stage7_detect_bg', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) return toast('❌ ' + (r && r.error ? r.error : '检测失败'), 'error');
      toast(r.toast || '🔍 检测完成');
      openProject(ctx.taskId, ctx.projectId);
    });
  }
  // 应用 detected 到 layout.video
  function stage7ApplyDetected(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    btn.disabled = true;
    postJSON(API + '/short_video_stage7_apply_detected', {
      task_id: ctx.taskId, project_id: ctx.projectId
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) return toast('❌ ' + (r && r.error ? r.error : '填充失败'), 'error');
      toast(r.toast || '✅ 已填充');
      openProject(ctx.taskId, ctx.projectId);
    });
  }
  // AI 智能布局
  function stage7ParseLayout(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var ref = (document.querySelector('.slirn-sv-mat-input[data-mat-slot="reference"]') || {}).value || '';
    btn.disabled = true;
    postJSON(API + '/short_video_stage7_parse_layout', {
      task_id: ctx.taskId, project_id: ctx.projectId, reference_path: ref
    }).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) return toast('❌ ' + (r && r.error ? r.error : 'AI 布局失败'), 'error');
      toast(r.toast || '🤖 AI 布局已应用');
      openProject(ctx.taskId, ctx.projectId);
    });
  }
  // BGM 列表（弹窗选）
  function stage7ListBgm(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    postJSON(API + '/short_video_stage7_list_bgm', {}).then(function(r) {
      if (!r || !r.ok) return toast('❌ 获取 BGM 失败', 'error');
      var bgms = r.bgms || [];
      if (!bgms.length) return toast('❌ 暂无系统 BGM', 'error');
      var opts = bgms.map(function(b, i) {
        return (i + 1) + '. ' + (b.label || b.filename) + (b.available ? '' : ' (缺失)');
      }).join('\n');
      var pick = window.prompt('选 BGM（输入序号）：\n' + opts, '1');
      var n = parseInt(pick, 10);
      if (!n || n < 1 || n > bgms.length) return;
      var b = bgms[n - 1];
      if (!b.available) return toast('❌ BGM 文件缺失', 'error');
      postJSON(API + '/short_video_stage7_select_bgm', {
        task_id: ctx.taskId, project_id: ctx.projectId, bgm_id: b.id
      }).then(function(r2) {
        if (r2 && r2.ok) { toast(r2.toast || '🎵 已选'); openProject(ctx.taskId, ctx.projectId); }
        else toast('❌ ' + (r2 && r2.error ? r.error : '选 BGM 失败'), 'error');
      });
    });
  }
  // ---- 模板 6 端点 ----
  function stage7ProfilesList(btn) {
    postJSON(API + '/short_video_stage7_profiles_list', {}).then(function(r) {
      if (!r || !r.ok) return toast('❌ 列表失败', 'error');
      var sel = document.querySelector('.slirn-sv-profile-select');
      if (!sel) return;
      sel.innerHTML = '<option value="">-- 选模板 --</option>' +
        (r.profiles || []).map(function(p) {
          return '<option value="' + p.id + '">' + p.name + '</option>';
        }).join('');
      toast('📋 共 ' + (r.profiles || []).length + ' 个模板');
    });
  }
  function stage7ProfilesSave(btn) {
    var name = (document.querySelector('.slirn-sv-profile-name') || {}).value || '';
    if (!name.trim()) return toast('请输入模板名', 'error');
    var params = _collectStage7Params();
    postJSON(API + '/short_video_stage7_profiles_save', {
      name: name, params: params
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '💾 已保存'); stage7ProfilesList(btn); }
      else toast('❌ ' + (r && r.error ? r.error : '保存失败'), 'error');
    });
  }
  function stage7ProfilesApply(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var sel = document.querySelector('.slirn-sv-profile-select');
    var pid = sel ? sel.value : '';
    if (!pid) return toast('先选模板', 'error');
    postJSON(API + '/short_video_stage7_profiles_apply', {
      task_id: ctx.taskId, project_id: ctx.projectId, profile_id: pid
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '📋 已应用'); openProject(ctx.taskId, ctx.projectId); }
      else toast('❌ ' + (r && r.error ? r.error : '应用失败'), 'error');
    });
  }
  function stage7ProfilesRename(btn) {
    var sel = document.querySelector('.slirn-sv-profile-select');
    var pid = sel ? sel.value : '';
    if (!pid) return toast('先选模板', 'error');
    var opt = sel.querySelector('option[value="' + pid + '"]');
    var newName = window.prompt('新名字', opt ? opt.textContent : '');
    if (!newName) return;
    postJSON(API + '/short_video_stage7_profiles_rename', {
      profile_id: pid, new_name: newName
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '✏️ 已重命名'); stage7ProfilesList(btn); }
      else toast('❌ ' + (r && r.error ? r.error : '重命名失败'), 'error');
    });
  }
  function stage7ProfilesExport(btn) {
    var sel = document.querySelector('.slirn-sv-profile-select');
    var pid = sel ? sel.value : '';
    if (!pid) return toast('先选模板', 'error');
    postJSON(API + '/short_video_stage7_profiles_export', {
      profile_id: pid
    }).then(function(r) {
      if (!r || !r.ok) return toast('❌ 导出失败', 'error');
      var blob = new Blob([JSON.stringify(r.profile, null, 2)],
                          {type: 'application/json'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = (r.profile.name || 'template') + '.json';
      document.body.appendChild(a); a.click();
      setTimeout(function() { URL.revokeObjectURL(url); document.body.removeChild(a); }, 100);
      toast('📤 已导出');
    });
  }
  function stage7ProfilesDelete(btn) {
    var sel = document.querySelector('.slirn-sv-profile-select');
    var pid = sel ? sel.value : '';
    if (!pid) return toast('先选模板', 'error');
    if (!window.confirm('确定删除该模板？')) return;
    postJSON(API + '/short_video_stage7_profiles_delete', {
      profile_id: pid
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '🗑️ 已删除'); stage7ProfilesList(btn); }
      else toast('❌ 删除失败', 'error');
    });
  }


  function stage5Asr(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var index = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!index) return;
    // REQ-20261016-136：按钮名「重新 ASR」→「提取短视频字幕」，toast 同步
    toast('提取短视频字幕 中…');
    postJSON(API + '/short_video_stage5_run', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: index
    }).then(function(r) {
      if (r && r.ok) { toast('ASR 完成'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || 'Stage 6 ASR 失败', 'error');
    });
  }

  function stage5Save(btn) {
    var ctx = innerCtx(); if (!ctx) return;
    var index = parseInt(btn.getAttribute('data-clip-index') || '0', 10);
    if (!index) return;
    // REQ-20261016-133：保存前先把 per-row 编辑状态（删/改/切）写回 textarea
    // （textarea 是后端 stage5_save 端点的输入）
    stage6RebuildSrt(index);
    var ta = document.querySelector('.slirn-stage5-srt[data-clip-index="' + index + '"]');
    var srt = ta ? ta.value : '';
    if (!srt.trim()) return toast('字幕为空，请先编辑或提取字幕', 'error');
    toast('保存字幕中…');
    postJSON(API + '/short_video_stage5_save', {
      task_id: ctx.taskId, project_id: ctx.projectId, index: index, srt_text: srt
    }).then(function(r) {
      if (r && r.ok) { toast('Stage 6 #'+index+' 已保存'); openProject(ctx.taskId, ctx.projectId); }
      else toast((r && r.error) || '保存失败', 'error');
    });
  }

  // ============ REQ-20261016-133：Stage 6 per-row 编辑（仿 Stage 4） ============
  // 整行替换文字：prompt 输入新文 → 更新 data-eff-text + 文本节点
  function stage6LineEdit(btn) {
    var row = btn.closest ? btn.closest('.slirn-stage6-srt-row') : null;
    if (!row) return;
    var orig = row.getAttribute('data-eff-text') || row.getAttribute('data-orig-text') || '';
    var newText = window.prompt('整行替换文字（原内容已预填）：', orig);
    if (newText === null) return;
    newText = String(newText || '').trim();
    if (!newText) return toast('文字不能为空', 'error');
    row.setAttribute('data-eff-text', newText);
    row.setAttribute('data-deleted', '0');
    row.classList.remove('slirn-stage6-srt-row-deleted');
    var textEl = row.querySelector('.slirn-stage6-srt-text');
    if (textEl) textEl.textContent = newText;
  }

  // 整行删除/恢复（toggle）：CSS 标 + data-deleted 标；保存时剔除
  function stage6LineToggle(btn) {
    var row = btn.closest ? btn.closest('.slirn-stage6-srt-row') : null;
    if (!row) return;
    var del = row.getAttribute('data-deleted') === '1';
    if (del) {
      row.setAttribute('data-deleted', '0');
      row.classList.remove('slirn-stage6-srt-row-deleted');
    } else {
      row.setAttribute('data-deleted', '1');
      row.classList.add('slirn-stage6-srt-row-deleted');
    }
  }

  // 整行切分：prompt 输入 N 段子段（用 | 分隔），替换为多 sub-row，
  // 时间段按 (end-start)/N 等分。
  function stage6LineResplit(btn) {
    var row = btn.closest ? btn.closest('.slirn-stage6-srt-row') : null;
    if (!row) return;
    var orig = row.getAttribute('data-eff-text') || row.getAttribute('data-orig-text') || '';
    var partsRaw = window.prompt(
      '切分子段（用 | 分隔，如 "前半|后半"）：', orig + '|');
    if (partsRaw === null) return;
    var parts = String(partsRaw || '').split('|').map(function(s) { return s.trim(); }).filter(Boolean);
    if (parts.length < 2) return toast('切分需要至少 2 段（用 | 分隔）', 'error');
    if (parts.length > 6) return toast('最多支持 6 段', 'error');
    var sm = parseInt(row.getAttribute('data-start-ms') || '0', 10);
    var em = parseInt(row.getAttribute('data-end-ms') || sm, 10);
    var span = Math.max(1, em - sm);
    var each = Math.floor(span / parts.length);
    var clipIdx = row.getAttribute('data-clip-index') || '0';
    var segNoBase = parseInt(row.getAttribute('data-seg-no') || '0', 10);
    // 找父容器；新 sub-row 插在原行后面
    var parent = row.parentNode;
    var after = row.nextSibling;
    // 先把原行替换为第 1 段（保持 DOM 顺序稳定）
    var firstStart = sm;
    var firstEnd = sm + each;
    row.setAttribute('data-start-ms', firstStart);
    row.setAttribute('data-end-ms', firstEnd);
    row.setAttribute('data-eff-text', parts[0]);
    row.setAttribute('data-orig-text', parts[0]);
    row.setAttribute('data-deleted', '0');
    row.classList.remove('slirn-stage6-srt-row-deleted');
    var tEl = row.querySelector('.slirn-stage6-srt-text');
    if (tEl) tEl.textContent = parts[0];
    var timeEl = row.querySelector('.slirn-stage6-srt-time');
    if (timeEl) timeEl.textContent = _fmtSrtTime(firstStart) + ' → ' + _fmtSrtTime(firstEnd);
    // 后续 sub-row 插到原行后面
    for (var i = 1; i < parts.length; i++) {
      var ssm = sm + i * each;
      var eem = (i === parts.length - 1) ? em : (sm + (i + 1) * each);
      var div = document.createElement('div');
      div.className = 'slirn-stage6-srt-row';
      div.setAttribute('data-seg-no', segNoBase * 1000 + i);
      div.setAttribute('data-clip-index', clipIdx);
      div.setAttribute('data-start-ms', ssm);
      div.setAttribute('data-end-ms', eem);
      div.setAttribute('data-orig-text', parts[i]);
      div.setAttribute('data-eff-text', parts[i]);
      div.setAttribute('data-deleted', '0');
      div.innerHTML =
        '<span class="slirn-stage6-srt-no" title="切分产生的子段（保存时按出现顺序重编）">#'
        + (segNoBase * 1000 + i) + '</span>'
        + '<span class="slirn-stage6-srt-time" title="本段时间戳">'
        + _fmtSrtTime(ssm) + ' → ' + _fmtSrtTime(eem) + '</span>'
        + '<span class="slirn-stage6-srt-text">' + _escHtml(parts[i]) + '</span>'
        + '<span class="slirn-stage6-srt-actions">'
        + '<button type="button" class="slirn-btn-mini" data-action="sv-stage6-line-edit" '
        + 'data-clip-index="' + clipIdx + '" data-seg-no="' + (segNoBase * 1000 + i) + '">✏️</button>'
        + '<button type="button" class="slirn-btn-mini" data-action="sv-stage6-line-resplit" '
        + 'data-clip-index="' + clipIdx + '" data-seg-no="' + (segNoBase * 1000 + i) + '">✂️</button>'
        + '<button type="button" class="slirn-btn-mini" data-action="sv-stage6-line-toggle" '
        + 'data-clip-index="' + clipIdx + '" data-seg-no="' + (segNoBase * 1000 + i) + '">🗑️</button>'
        + '</span>';
      if (after) parent.insertBefore(div, after);
      else parent.appendChild(div);
    }
  }

  // REQ-20261016-138：移除 stage6ToggleSource（按钮「📝 切换源」已删除；
  // 用户原话：字幕直接显示在下面，为什么要切换？per-row 编辑器已足够）

  // 从当前 per-row DOM 重建 SRT 文本，写回 textarea（保存时 stage5Save 读它）
  function stage6RebuildSrt(clipIndex) {
    var wrap = document.querySelector('.slirn-stage6-srt-rows-wrap[data-clip-index="' + clipIndex + '"]');
    var ta = document.querySelector('.slirn-stage5-srt[data-clip-index="' + clipIndex + '"]');
    if (!wrap || !ta) return;
    var rows = wrap.querySelectorAll('.slirn-stage6-srt-row');
    if (!rows.length) return;
    var out = [];
    var no = 1;
    rows.forEach(function(r) {
      if (r.getAttribute('data-deleted') === '1') return;
      var sm = parseInt(r.getAttribute('data-start-ms') || '0', 10);
      var em = parseInt(r.getAttribute('data-end-ms') || sm, 10);
      var text = r.getAttribute('data-eff-text') || '';
      out.push(no);
      out.push(_fmtSrtTime(sm) + ' --> ' + _fmtSrtTime(em));
      out.push(text);
      out.push('');
      no++;
    });
    ta.value = out.join('\n');
  }

  // 局部时间格式（hh:mm:ss,mmm）；与后端 _fmt_srt_time 一致
  function _fmtSrtTime(ms) {
    ms = Math.max(0, parseInt(ms || 0, 10));
    var h = Math.floor(ms / 3600000); ms -= h * 3600000;
    var m = Math.floor(ms / 60000);   ms -= m * 60000;
    var s = Math.floor(ms / 1000);    ms -= s * 1000;
    function pad(n, w) { var s = String(n); while (s.length < w) s = '0' + s; return s; }
    return pad(h, 2) + ':' + pad(m, 2) + ':' + pad(s, 2) + ',' + pad(ms, 3);
  }

  function stage6Run() {
    var ctx = innerCtx(); if (!ctx) return;
    // 自动从 stage5_coarse.highlights + stage6_refine.highlights 拼 items
    postJSON(API + '/short_video_get', {task_id: ctx.taskId, project_id: ctx.projectId}).then(function(r) {
      if (!r || !r.ok) return toast('读取项目失败', 'error');
      var p = r.project || {};
      var coarse = (p.pipeline || {}).stage5_coarse || {};
      var refine = (p.pipeline || {}).stage6_refine || {};
      var cHls = coarse.highlights || [];
      var rHls = refine.highlights || [];
      var items = cHls.map(function(hc) {
        var hr = rHls.find(function(x) { return x.index === hc.index; }) || {};
        return {
          index: hc.index,
          coarse_mp4: hc.coarse_mp4 || '',
          refined_srt: hr.refined_srt || ''
        };
      }).filter(function(it) { return it.coarse_mp4 && it.refined_srt; });
      if (!items.length) return toast('Stage 5 / 6 未就绪', 'error');
      toast('精剪 ' + items.length + ' 条中…');
      postJSON(API + '/short_video_stage6_run', {
        task_id: ctx.taskId, project_id: ctx.projectId, items: items
      }).then(function(x) {
        if (x && x.ok) { toast('Stage 7 完成'); openProject(ctx.taskId, ctx.projectId); }
        else toast((x && x.error) || 'Stage 7 失败', 'error');
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
    // REQ-20261013-113：标记「下一次 seeking 是我自己发起的自动跳转」——
    // 旧版用「t 跳出现段」启发式判定手动，越界就把 manual 置 true，导致
    // 行序重排后 segs 顺序≠时间序时第一段就 false-trigger，之后再也不跳段。
    // 现在：自动跳转前先把 expectedSeek=true；seeking 触发时若为 true，
    // 说明是我们自己跳的，吞掉这次事件；只有 user 真的拖进度条才会置 manual。
    var expectedSeek = false;
    function onSeeking() {
      if (cleared) return;
      if (expectedSeek) { expectedSeek = false; return; }
      if (!segs) return;
      if (segIdx >= segs.length) return;
      var t = v.currentTime;
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
            expectedSeek = true;
            try { v.currentTime = segs[segIdx][0]; } catch (e) { expectedSeek = false; /* ignore */ }
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
      expectedSeek = true;
      try {
        v.currentTime = startSec;
        v.play().catch(function(){});
      } catch (e) { expectedSeek = false; /* seek 偶发 NotSupported 忽略 */ }
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
              // 同步刷新「↩️ 还原顺序」按钮状态：行序变了，禁用态可能已失效
              // （per slirn-real-time-vs-persistent-state：拖拽改 server 状态后
              // 必须就地同步 UI 状态，不能只靠下次 openProject 重渲染）
              _refreshRestoreBtn(parseInt(ctx.hlIndex || '0', 10));
              showToast('字幕顺序已更新（下游 Stage 4-7 已重置）', 'ok');
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

  // REQ-20261013-115：拖拽完成后，必须就地刷新「↩️ 还原顺序」按钮的禁用态。
  // 否则用户看到行序已乱但按钮仍 disabled（被 server 旧渲染的 HTML 锁住），
  // 体验上等同「按钮失灵」。计算口径与 short_video_ui.py:571 的
  // is_original = nos == sorted(nos) 一致（读每个 .slirn-stage3-hl-no 节点的
  // 文本号 = dict['no']）。
  function _refreshRestoreBtn(hlIndex) {
    if (!hlIndex) return;
    var btn = document.querySelector(
      '.slirn-stage3-hl-row[data-hl-index="' + hlIndex + '"] ' +
      '[data-action="sv-stage3-restore-order"]'
    );
    if (!btn) return;
    var rows = document.querySelectorAll(
      '.slirn-stage3-hl-preview[data-hl-index="' + hlIndex + '"] ' +
      '.slirn-stage3-hl-sub'
    );
    var nos = [];
    rows.forEach(function(r) {
      var noEl = r.querySelector('.slirn-stage3-hl-no');
      if (!noEl) return;
      var m = (noEl.textContent || '').match(/(\d+)/);
      if (m) nos.push(parseInt(m[1], 10));
    });
    var isOriginal = nos.length > 0 && nos.every(function(n, i, arr) {
      return i === 0 || n >= arr[i - 1];
    });
    if (isOriginal) {
      btn.setAttribute('disabled', 'disabled');
      btn.setAttribute('title', '当前已是大模型最初顺序');
    } else {
      btn.removeAttribute('disabled');
      btn.setAttribute('title', '把该条短视频的字幕行还原为大模型最初给出的顺序（编号不变）');
    }
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
    if (action === 'sv-stage2-run') return stage2Run(btn);
    if (action === 'sv-stage3-run') return stage3Run(btn);
    if (action === 'sv-stage3-restore-order') return stage3RestoreOrder(btn);
    if (action === 'sv-stage3-preview') return stage3PreviewHl(btn);
    if (action === 'sv-stage4-optimize-run') return stage4OptimizeRun(btn);
    // REQ-20261013-108：Stage 4 提示词 / 手动编辑 / 预览 / 跳 Stage 5
    if (action === 'sv-stage4-use-system-prompt') return stage4UseSystemPrompt(btn);
    if (action === 'sv-stage4-save-default') return stage4SaveDefault();
    if (action === 'sv-stage4-save-history') return stage4SaveHistory();
    if (action === 'sv-stage4-clear-prompt') return stage4ClearPrompt();
    if (action === 'sv-stage4-load-prompt') return stage4LoadPrompt(btn);
    if (action === 'sv-stage4-toggle-hist') return stage4ToggleHist(btn);
    if (action === 'sv-stage4-line-edit') return stage4LineEdit(btn);
    if (action === 'sv-stage4-line-resplit') return stage4LineResplit(btn);
    if (action === 'sv-stage4-line-toggle') return stage4LineToggle(btn);
    if (action === 'sv-stage4-line-reorder') return stage4LineReorder(btn);
    if (action === 'sv-stage4-preview') return stage4Preview(btn);
    if (action === 'sv-stage4-go-coarse') return stage4GoCoarse(btn);
    if (action === 'sv-stage4-go-coarse-all') return stage4GoCoarseAll();
    if (action === 'sv-stage3-rerun') return stage3RerunOne(btn);
    if (action === 'sv-stage3-remove-hl') return stage3RemoveHl(btn);
    if (action === 'sv-stage3-hl-tab') return switchHlTab(btn.getAttribute('data-hl-index'));
    if (action === 'sv-stage4-hl-tab') return switchHlTab(btn.getAttribute('data-hl-index'));
    if (action === 'sv-stage6-hl-tab') return switchHlTab(btn.getAttribute('data-hl-index'));
    if (action === 'sv-stage3-save-default') return stage3SaveDefault();
    if (action === 'sv-stage3-save-history') return stage3SaveHistory();
    if (action === 'sv-stage3-load-prompt') return stage3LoadPrompt(btn);
    if (action === 'sv-stage3-use-system-prompt') return stage3UseSystemPrompt(btn);
    if (action === 'sv-stage3-toggle-hist') return stage3ToggleHist(btn);
    if (action === 'sv-stage3-clear-prompt') return stage3ClearPrompt();
    if (action === 'sv-stage3-delete-prompt') return stage3DeletePrompt(btn);
    if (action === 'sv-stage3-upload-image') return stage3UploadImage();
    if (action === 'sv-stage3-remove-image') return stage3RemoveImage();
    if (action === 'sv-stage4-run') return stage4Run();
    if (action === 'sv-stage5-asr') return stage5Asr(btn);
    if (action === 'sv-stage5-save') return stage5Save(btn);
    // REQ-20261016-133：Stage 6 per-row 编辑
    if (action === 'sv-stage6-line-edit') return stage6LineEdit(btn);
    if (action === 'sv-stage6-line-toggle') return stage6LineToggle(btn);
    if (action === 'sv-stage6-line-resplit') return stage6LineResplit(btn);
    if (action === 'sv-stage6-run') return stage6Run();
    // REQ-20261013-103：Stage 5 粗剪 异步导出/删除/连续 SRT/单条 SRT
    if (action === 'sv-stage5-export') return stage5Export(btn);
    if (action === 'sv-stage5-delete') return stage5Delete(btn);
    if (action === 'sv-stage5-subs-copy') return stage5SubsCopy(btn);
    if (action === 'sv-stage5-subs-download') return stage5SubsDownload(btn);
    if (action === 'sv-stage5-clip-subs-copy') return stage5ClipSubsCopy(btn);
    if (action === 'sv-stage5-clip-subs-download') return stage5ClipSubsDownload(btn);
    // REQ-20261016-132：Stage 5 视频预览浮窗
    if (action === 'sv-stage5-preview') return svStage5Preview(btn);
    // REQ-20261013-104：Stage 6 字幕优化（行级 + 词频 + 批量替换 + 行内切分）
    if (action === 'sv-stage6-optimize') return stage6Optimize(btn);
    if (action === 'sv-stage6-save-decisions') return stage6SaveDecisions(btn);
    if (action === 'sv-stage6-resplit') return stage6Resplit(btn);
    if (action === 'sv-stage6-unsplit') return stage6Unsplit(btn);
    if (action === 'sv-stage6-batch-search') return stage6BatchSearch(btn);
    if (action === 'sv-stage6-batch-replace') return stage6BatchReplace(btn);
    // REQ-20261013-105：Stage 7 精剪 v1 完整版（6 素材 + 字体 + BGM + 预览 + 异步 + 模板 + 检测 + AI 布局）
    if (action === 'sv-stage7-export') return stage7Export(btn);
    if (action === 'sv-stage7-save-params') return stage7SaveParams(btn);
    if (action === 'sv-stage7-preview') return stage7Preview(btn);
    if (action === 'sv-stage7-detect-bg') return stage7DetectBg(btn);
    if (action === 'sv-stage7-apply-detected') return stage7ApplyDetected(btn);
    if (action === 'sv-stage7-parse-layout') return stage7ParseLayout(btn);
    if (action === 'sv-stage7-list-bgm') return stage7ListBgm(btn);
    if (action === 'sv-stage7-profiles-list') return stage7ProfilesList(btn);
    if (action === 'sv-stage7-profiles-save') return stage7ProfilesSave(btn);
    if (action === 'sv-stage7-profiles-apply') return stage7ProfilesApply(btn);
    if (action === 'sv-stage7-profiles-rename') return stage7ProfilesRename(btn);
    if (action === 'sv-stage7-profiles-export') return stage7ProfilesExport(btn);
    if (action === 'sv-stage7-profiles-delete') return stage7ProfilesDelete(btn);

    // 预览 / 素材
    if (action === 'sv-preview') return previewVideo(btn);
    if (action === 'sv-preview-close') return closePreview();
    if (action === 'sv-preview-srt') return previewSrtByUrl(btn.getAttribute('data-url') || '');
  });
})();
