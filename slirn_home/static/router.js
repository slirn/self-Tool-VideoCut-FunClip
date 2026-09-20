// Slirn 事件路由 — 走自定义 /slirn/api/* 同步路由，绕开 Gradio 队列
//
// 本文件以静态文件方式经 <script src> 注入（app.py 的 _build_head 注册引用）。
// 不能作为内联脚本放进 gr.HTML(head=...)：Gradio 6.17.3 的 head 传输链路会把
// 脚本文本里的反斜杠转义解码（\n -> 换行、\x20 -> 空格、\\ -> \、孤立的 \ 被删除），
// 含反斜杠的内联 JS 会变成非法语法而整体失效（2026-09-17 排查结论）。
(function() {
  // 幂等保护：同一页面被注入两次时避免重复绑定
  if (window.__slirnRouterBound) return;
  window.__slirnRouterBound = true;

  var SLIRN_API = '/slirn/api';

  function postJSON(url, payload) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload || {})
    }).then(function(r) { return r.json(); }).catch(function(e) { return {ok: false, error: String(e)}; });
  }

  function postForm(url, formData) {
    return fetch(url, {
      method: 'POST',
      body: formData
    }).then(function(r) { return r.json(); }).catch(function(e) { return {ok: false, error: String(e)}; });
  }

  function getInput(id) {
    var el = document.getElementById(id);
    return el ? el.value : '';
  }

  function toast(msg, type) {
    type = type || 'success';
    var existing = document.querySelector('.slirn-toast');
    if (existing) existing.remove();
    var t = document.createElement('div');
    t.className = 'slirn-toast ' + type;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function() { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; }, 2400);
    setTimeout(function() { t.remove(); }, 2800);
  }
  window.slirnToast = toast;

  // 工作台加载等待弹窗（REQ-20260918-051）：任务列表点「剪辑」时弹出。
  // 内容多的任务接口耗时长，用户需要明确反馈（全屏半透明遮罩 + spinner +
  // 任务名）。Fetch 完成（成功 / 失败 / 超时）自动关闭；多次点击同一个
  // tid 不会叠出多个弹窗。
  var _wbLoaderTimer = null;
  var _wbLoaderTid = null;
  function showWbLoader(tid, label) {
    hideWbLoader();  // 去重：先关旧弹窗（避免叠层 + 状态错乱）
    _wbLoaderTid = tid || '';
    var ov = document.createElement('div');
    ov.className = 'slirn-wb-loader-overlay';
    ov.setAttribute('data-wb-loader-for', _wbLoaderTid);
    ov.innerHTML =
      '<div class="slirn-wb-loader-card">'
      + '<div class="slirn-wb-loader-spinner"></div>'
      + '<p class="slirn-wb-loader-title">加载工作台…</p>'
      + '<p class="slirn-wb-loader-task">' + escapeHtml(label || tid || '') + '</p>'
      + '<p class="slirn-wb-loader-hint">内容较多时可能需要几秒，请稍候</p>'
      + '</div>';
    // 阻止遮罩自身点击穿透到下层（用户可能误点关不掉）
    ov.addEventListener('click', function(ev) { ev.stopPropagation(); });
    document.body.appendChild(ov);
    // 60s 超时兜底（防接口挂死导致弹窗永远关不掉）
    _wbLoaderTimer = setTimeout(function() {
      var cur = document.querySelector('.slirn-wb-loader-overlay');
      if (cur) {
        cur.querySelector('.slirn-wb-loader-title').textContent = '加载超时';
        cur.querySelector('.slirn-wb-loader-hint').textContent = '已等待 60 秒仍未返回，请检查网络或刷新页面';
      }
    }, 60000);
  }
  function hideWbLoader() {
    if (_wbLoaderTimer) { clearTimeout(_wbLoaderTimer); _wbLoaderTimer = null; }
    _wbLoaderTid = null;
    var ov = document.querySelector('.slirn-wb-loader-overlay');
    if (ov) ov.remove();
  }
  window.slirnShowWbLoader = showWbLoader;
  window.slirnHideWbLoader = hideWbLoader;

  // 更新分类标题里"删除选中"按钮上的计数
  function updateDeleteCount(section) {
    if (!section) return;
    var btn = section.querySelector('[data-action="hw-cat-delete"]');
    if (!btn) return;
    var n = section.querySelectorAll('.slirn-hotword-cell.selected').length;
    btn.textContent = n > 0 ? ('🗑 删除选中 (' + n + ')') : '🗑 删除选中';
    btn.classList.toggle('has-selection', n > 0);
  }
  window.slirnUpdateDeleteCount = updateDeleteCount;

  // ========== 任务级热词「选择器」逻辑（新建任务页） ==========

  // 收集当前「选中」状态的公共库词（按 DOM 出现顺序去重）
  function collectPickedHotwords() {
    var seen = {};
    var out = [];
    var cells = document.querySelectorAll('#slirn-hw-picker .slirn-pick-cell.selected');
    cells.forEach(function(c) {
      var w = c.getAttribute('data-word');
      if (w && !seen[w]) { seen[w] = 1; out.push(w); }
    });
    return out;
  }

  // 收集「继承公共库所有热词」时按显示顺序的词列表（用于预览 chips）
  function collectAllPublicHotwords() {
    var seen = {};
    var out = [];
    document.querySelectorAll('#slirn-hw-picker .slirn-pick-cell').forEach(function(c) {
      var w = c.getAttribute('data-word');
      if (w && !seen[w]) { seen[w] = 1; out.push(w); }
    });
    return out;
  }

  // 解析手动输入的词
  function parseManual(text) {
    if (!text) return [];
    return (text.replace(/\n/g, ' ').split(/[\s,，;；、]+/)).filter(Boolean);
  }

  // 渲染「最终生效热词」的 chips（继承 + 选中 + 手动，去重）
  function renderHwChips() {
    var box = document.getElementById('slirn-hw-chips');
    var sum = document.getElementById('slirn-hw-summary');
    if (!box) return;
    var inheritEl = document.getElementById('slirn-hw-inherit-all');
    var inherit = inheritEl && inheritEl.checked;
    var baseWords = inherit ? collectAllPublicHotwords() : collectPickedHotwords();
    var manualRaw = document.getElementById('slirn-hotwords-manual');
    var manual = parseManual(manualRaw ? manualRaw.value : '');

    var seen = {};
    var ordered = [];
    baseWords.forEach(function(w) {
      var k = w.trim();
      if (k && !seen[k]) { seen[k] = 1; ordered.push({w: k, src: inherit ? 'inherit' : 'pick'}); }
    });
    manual.forEach(function(w) {
      var k = w.trim();
      if (k && !seen[k]) { seen[k] = 1; ordered.push({w: k, src: 'manual'}); }
    });

    box.innerHTML = '';
    if (!ordered.length) {
      box.innerHTML = '<div class="slirn-hw-chip-empty">（还没有热词 — 勾选继承 / 从公共库选 / 或在下方输入）</div>';
    } else {
      ordered.forEach(function(o) {
        var chip = document.createElement('span');
        var cls = 'slirn-hw-chip src-' + o.src;
        chip.className = cls;
        chip.setAttribute('data-word', o.w);
        var label = o.src === 'inherit' ? '继承' : (o.src === 'pick' ? '已选' : '手动');
        var xBtn = (o.src === 'inherit')
          ? ''  // 继承来的词不可单独移除（要排除就关掉「继承」）
          : '<button class="slirn-hw-chip-x" data-action="hw-chip-remove" data-word="' + escapeAttr(o.w) + '" title="移除">✕</button>';
        chip.innerHTML = '<span class="slirn-hw-chip-label">' + label + '</span>' +
                         '<span class="slirn-hw-chip-text">' + escapeHtml(o.w) + '</span>' +
                         xBtn;
        box.appendChild(chip);
      });
    }
    if (sum) {
      sum.textContent = '共 ' + ordered.length + ' 个词' +
        (inherit ? ' · 来自继承全部公共库' : '');
    }
  }
  window.slirnRenderHwChips = renderHwChips;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
    });
  }
  function escapeAttr(s) {
    return escapeHtml(s);
  }

  // 切换继承勾选 → 控制选择器显示/隐藏，并刷新 chips
  function syncInheritVisibility() {
    var inheritEl = document.getElementById('slirn-hw-inherit-all');
    var wrap = document.getElementById('slirn-hw-picker-wrap');
    if (!inheritEl || !wrap) return;
    if (inheritEl.checked) {
      wrap.classList.add('slirn-inherit-mode');
    } else {
      wrap.classList.remove('slirn-inherit-mode');
    }
    renderHwChips();
  }
  window.slirnSyncInherit = syncInheritVisibility;

  // 初始化新建任务页的热词选择器（每次切换到该 tab 时调用一次）
  function initHotwordPicker() {
    var picker = document.getElementById('slirn-hw-picker');
    if (!picker) return;
    var inheritEl = document.getElementById('slirn-hw-inherit-all');
    var manualEl = document.getElementById('slirn-hotwords-manual');
    if (inheritEl && !inheritEl.dataset.slirnBound) {
      inheritEl.dataset.slirnBound = '1';
      inheritEl.addEventListener('change', syncInheritVisibility);
    }
    if (manualEl && !manualEl.dataset.slirnBound) {
      manualEl.dataset.slirnBound = '1';
      manualEl.addEventListener('input', renderHwChips);
    }
    // 点击 picker 单元切换选中（事件代理）
    if (!picker.dataset.slirnClickBound) {
      picker.dataset.slirnClickBound = '1';
      picker.addEventListener('click', function(ev) {
        var cell = ev.target.closest('.slirn-pick-cell');
        if (!cell) return;
        cell.classList.toggle('selected');
        renderHwChips();
      });
    }
    // chips 容器上的 ✕ 移除（事件代理）
    var chipsBox = document.getElementById('slirn-hw-chips');
    if (chipsBox && !chipsBox.dataset.slirnClickBound) {
      chipsBox.dataset.slirnClickBound = '1';
      chipsBox.addEventListener('click', function(ev) {
        var x = ev.target.closest('[data-action="hw-chip-remove"]');
        if (!x) return;
        ev.stopPropagation();
        var w = x.getAttribute('data-word') || '';
        // 找到对应 picker cell，去掉 .selected（picked 来源）
        var cell = picker.querySelector('.slirn-pick-cell[data-word="' + cssEscape(w) + '"]');
        if (cell) cell.classList.remove('selected');
        // 如果是手动来源，从 textarea 里去掉这个词
        var manualEl2 = document.getElementById('slirn-hotwords-manual');
        if (manualEl2 && manualEl2.value) {
          var tokens = manualEl2.value.split(/(\s+)/);
          manualEl2.value = tokens.filter(function(t) { return t.trim() !== w; }).join('');
        }
        renderHwChips();
      });
    }
    syncInheritVisibility();
  }

  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/(["\\.#:>+~*[\]()'])/g, '\\$1');
  }
  window.slirnInitHotwordPicker = initHotwordPicker;

  function secondsToHMS(s) {
    if (s == null || isNaN(s) || s < 0) return '';
    var ms = Math.round(s * 1000);
    var hh = Math.floor(ms / 3600000); ms = ms % 3600000;
    var mm = Math.floor(ms / 60000); ms = ms % 60000;
    var ss = Math.floor(ms / 1000); var mss = ms % 1000;
    return (hh<10?'0':'')+hh+':'+(mm<10?'0':'')+mm+':'+(ss<10?'0':'')+ss+'.'+(mss<10?'00':mss<100?'0':'')+mss;
  }
  window.slirnSecToHMS = secondsToHMS;

  // HH:MM:SS.mmm / HH:MM:SS → 秒数（用于客户端校验 start<end）
  // 返回 null 表示格式无效
  function hmsToSeconds(str) {
    if (!str) return null;
    var m = String(str).match(/^(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$/);
    if (!m) return null;
    var h = parseInt(m[1], 10);
    var mi = parseInt(m[2], 10);
    var se = parseInt(m[3], 10);
    var ms = m[4] ? parseInt(m[4].padEnd(3, '0'), 10) : 0;
    if (mi >= 60 || se >= 60) return null;
    return h * 3600 + mi * 60 + se + ms / 1000;
  }

  // ===== 当前选中的视频路径（来自文件选择）=====
  window.slirnSelectedFile = '';

  // ===== Tab 切换 — 纯客户端 CSS toggle，HTML 已在初始 DOM 中 =====
  var TAB_BUTTONS = {
    'goto-dashboard': 'slirn-tab-dashboard',
    'goto-tasks':     'slirn-tab-tasks',
    'goto-create':    'slirn-tab-create',
    'goto-hotwords':  'slirn-tab-hotwords',
  };
  var ALL_TABS = ['slirn-tab-dashboard','slirn-tab-tasks','slirn-tab-create','slirn-tab-hotwords','slirn-tab-workbench'];

  function showTab(targetCell) {
    ALL_TABS.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.style.display = (id === targetCell) ? '' : 'none';
    });
    var detail = document.getElementById('slirn-tab-detail');
    if (detail) detail.style.display = 'none';
    // 切到「新建任务」时初始化热词选择器（刷新 chip 显示）；
    // 若上次是「编辑任务」占用了本页 → 重新拉取全新建页
    if (targetCell === 'slirn-tab-create') {
      try { window.slirnInitHotwordPicker && window.slirnInitHotwordPicker(); } catch (e) {}
      if (window.slirnEditTaskId) {
        window.slirnEditTaskId = null;
        postJSON(SLIRN_API + '/create_page', {}).then(function(r) {
          if (r && r.ok && r.html) {
            var c = document.getElementById('slirn-tab-create');
            if (c) c.innerHTML = r.html;
            initTaskEdit();  // 无 edit-state 时仅清状态
          }
        });
      }
    }
    window.scrollTo({top: 0, behavior: 'smooth'});
  }

  // REQ-20260920-087：任务列表搜索框过滤（client-side）
  // 监听 #slirn-task-search 的 input 事件，按 task_id / name / original_video 文本匹配卡片
  // 全空字符串 → 显示全部；非空 → 隐藏不匹配的卡片 + 显示「无匹配」空态
  function _filterTaskCards(query) {
    var q = (query || '').trim().toLowerCase();
    var grid = document.querySelector('.slirn-task-grid');
    if (!grid) return;
    var cards = grid.querySelectorAll('.slirn-task-card');
    var matchedCount = 0;
    cards.forEach(function(card) {
      if (!q) {
        card.style.display = '';
        matchedCount++;
        return;
      }
      var tid = (card.getAttribute('data-task-id') || '').toLowerCase();
      var nameEl = card.querySelector('.slirn-task-name');
      var videoEl = card.querySelector('.slirn-task-video');
      var name = nameEl ? (nameEl.textContent || '').toLowerCase() : '';
      var video = videoEl ? (videoEl.textContent || '').toLowerCase() : '';
      var hay = tid + ' ' + name + ' ' + video;
      var match = hay.indexOf(q) >= 0;
      card.style.display = match ? '' : 'none';
      if (match) matchedCount++;
    });
    // 处理「无匹配」空态
    var existing = grid.querySelector('.slirn-search-empty');
    if (existing) existing.remove();
    if (q && matchedCount === 0) {
      var empty = document.createElement('div');
      empty.className = 'slirn-empty slirn-search-empty';
      empty.style.gridColumn = '1/-1';
      empty.innerHTML = '<div class="slirn-empty-icon">🔍</div>' +
        '<div class="slirn-empty-text">没有匹配「' + q.replace(/[<>&"']/g, function(c) {
          return ({'<':'<','>':'>','&':'&','"':'"',"'":'&#39;'})[c];
        }) + '」的任务</div>';
      grid.appendChild(empty);
    }
  }

  // 用事件代理挂监听（避免重复绑定 — input 元素可能在刷新后被替换）
  document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'slirn-task-search') {
      _filterTaskCards(e.target.value);
    }
  });

  function refreshCell(cellId, html) {
    var cell = document.getElementById(cellId);
    if (!cell) return;
    if (html) cell.innerHTML = html;
  }

  // ===== 上传进度弹窗 =====
  function openUploadDialog(file) {
    var existing = document.getElementById('slirn-upload-modal');
    if (existing) existing.remove();
    var overlay = document.createElement('div');
    overlay.id = 'slirn-upload-modal';
    overlay.className = 'slirn-modal-overlay';
    var sizeMb = (file.size / 1024 / 1024).toFixed(2);
    overlay.innerHTML =
      '<div class="slirn-modal-card">' +
        '<div class="slirn-modal-title">⏳ 上传视频中…</div>' +
        '<div class="slirn-modal-filename">' + escapeHtml(file.name) + '（' + sizeMb + ' MB）</div>' +
        '<div class="slirn-progress-track"><div id="slirn-progress-bar" class="slirn-progress-bar"></div></div>' +
        '<div id="slirn-progress-text" class="slirn-progress-text">准备中…</div>' +
      '</div>';
    document.body.appendChild(overlay);
    return overlay;
  }
  function setUploadProgress(loaded, total) {
    var bar = document.getElementById('slirn-progress-bar');
    var text = document.getElementById('slirn-progress-text');
    if (!bar || !text) return;
    var pct = total > 0 ? Math.min(100, Math.round(loaded / total * 100)) : 0;
    bar.style.width = pct + '%';
    var sentMb = (loaded / 1024 / 1024).toFixed(2);
    var totalMb = (total / 1024 / 1024).toFixed(2);
    text.textContent = pct + '%  ·  ' + sentMb + ' / ' + totalMb + ' MB';
  }
  function closeUploadDialog() {
    var m = document.getElementById('slirn-upload-modal');
    if (m) m.remove();
  }

  // ===== 大模型设置弹窗（REQ-20260915-008：多厂商模型动态注册；REQ-20260916-001：协议 + 编辑）=====
  // 已注册模型列表（当前 ⭐ 高亮 + 设为当前/测试/编辑/删除）+ 表单（模型名/厂商/
  // 协议/Base URL/Key 环境变量名，添加与编辑共用）。Key 本身始终从系统环境变量
  // 读取，界面只填「环境变量名」。
  var LLM_EDIT_ID = null;   // 非空 = 表单处于编辑该模型状态
  var LLM_MODELS = [];      // 最近一次列表数据（编辑时回填表单用）
  function llmFormReset() {
    LLM_EDIT_ID = null;
    ['slirn-llm-in-id', 'slirn-llm-in-provider', 'slirn-llm-in-url', 'slirn-llm-in-env']
      .forEach(function(i) { var el = document.getElementById(i); if (el) el.value = ''; });
    var proto = document.getElementById('slirn-llm-in-proto');
    if (proto) proto.value = 'openai';
    // REQ-20260919-061 用户补充：重置时取消勾选「支持图片」
    var visChk = document.getElementById('slirn-llm-in-vision');
    if (visChk) visChk.checked = false;
    var t = document.getElementById('slirn-llm-form-title');
    if (t) t.textContent = '添加模型';
    var btn = document.getElementById('slirn-llm-add-btn');
    if (btn) btn.textContent = '➕ 添加';
    var cancel = document.getElementById('slirn-llm-cancel-btn');
    if (cancel) cancel.style.display = 'none';
  }
  function openLLMSettings() {
    var existing = document.getElementById('slirn-llm-modal');
    if (existing) existing.remove();
    var overlay = document.createElement('div');
    overlay.id = 'slirn-llm-modal';
    overlay.className = 'slirn-modal-overlay';
    overlay.innerHTML =
      '<div class="slirn-modal-card slirn-llm-card">' +
        '<div class="slirn-modal-title">⚙️ 大模型设置</div>' +
        '<div class="slirn-llm-subtitle">当前模型：<b id="slirn-llm-current">…</b></div>' +
        '<div class="slirn-llm-list" id="slirn-llm-list"></div>' +
        '<div class="slirn-llm-section" id="slirn-llm-form-title">添加模型</div>' +
        '<div class="slirn-llm-form" id="slirn-llm-form">' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">模型名</span>' +
            '<input id="slirn-llm-in-id" class="slirn-llm-input" placeholder="如 deepseek-chat" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">厂商</span>' +
            '<input id="slirn-llm-in-provider" class="slirn-llm-input" placeholder="如 DeepSeek / 阿里云百炼" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">协议</span>' +
            '<select id="slirn-llm-in-proto" class="slirn-llm-input">' +
              '<option value="openai">OpenAI 兼容（{Base URL}/chat/completions）</option>' +
              '<option value="anthropic">Anthropic（{Base URL}/v1/messages）</option>' +
            '</select></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">Base URL</span>' +
            '<input id="slirn-llm-in-url" class="slirn-llm-input" placeholder="https://api.deepseek.com/v1" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">Key 环境变量</span>' +
            '<input id="slirn-llm-in-env" class="slirn-llm-input" placeholder="如 DEEPSEEK_API_KEY" /></div>' +
          '<div class="slirn-llm-row"><span class="slirn-llm-label">支持图片</span>' +
            '<label class="slirn-llm-chk"><input type="checkbox" id="slirn-llm-in-vision" /> ' +
            '可接收图片输入（多模态 — 精剪视频 AI 智能布局需此项）</label></div>' +
        '</div>' +
        '<div class="slirn-llm-tip">按所选协议调用（OpenAI 兼容 <code>{Base URL}/chat/completions</code> / Anthropic <code>{Base URL}/v1/messages</code>）；API Key 从上面填写的系统环境变量读取，界面不存储 Key。</div>' +
        '<div id="slirn-llm-test-result" class="slirn-llm-test-result"></div>' +
        '<div class="slirn-llm-actions">' +
          '<button class="slirn-btn slirn-btn-primary" data-action="llm-add" id="slirn-llm-add-btn">➕ 添加</button>' +
          '<button class="slirn-btn" data-action="llm-cancel-edit" id="slirn-llm-cancel-btn" style="display:none">取消编辑</button>' +
          '<button class="slirn-btn" data-action="llm-close">关闭</button>' +
        '</div>' +
      '</div>';
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) overlay.remove();  // 点遮罩关闭
    });
    document.body.appendChild(overlay);
    llmFormReset();
    fetch(SLIRN_API + '/llm_config').then(function(r) { return r.json(); })
      .then(function(r) {
        if (r && r.ok) renderLLMList(r.models || [], r.current || '');
      })
      .catch(function() {});
  }
  function renderLLMList(models, current) {
    LLM_MODELS = models;
    var cur = document.getElementById('slirn-llm-current');
    if (cur) cur.textContent = current || '（无）';
    var list = document.getElementById('slirn-llm-list');
    if (!list) return;
    if (!models.length) {
      list.innerHTML = '<div class="slirn-llm-empty">尚未注册模型，请在下方添加</div>';
      return;
    }
    list.innerHTML = models.map(function(m) {
      var isCur = m.id === current;
      var proto = m.protocol === 'anthropic' ? 'Anthropic' : 'OpenAI 兼容';
      // REQ-20260919-061 用户补充：UI 显式显示「多模态」徽章，一眼看清哪些能用图片
      var visionBadge = m.vision ? '<span class="slirn-llm-item-vision" title="支持图片输入（多模态）">🖼 多模态</span>' : '';
      return '<div class="slirn-llm-item' + (isCur ? ' current' : '') + '">' +
        '<div class="slirn-llm-item-head">' +
          '<span class="slirn-llm-item-name">' + (isCur ? '⭐ ' : '') + escapeHtml(m.id) +
            '<span class="slirn-llm-item-prov">' + escapeHtml(m.provider || '') + '</span>' +
            '<span class="slirn-llm-item-proto">' + proto + '</span>' +
            visionBadge + '</span>' +
          '<span class="slirn-llm-item-key ' + (m.key_present ? 'ok' : 'miss') + '">' +
            (m.key_present ? '✅ Key 已配置' : '❌ 未配置 ' + escapeHtml(m.api_key_env)) + '</span>' +
        '</div>' +
        '<div class="slirn-llm-item-url">' + escapeHtml(m.base_url) +
          ' <code>' + escapeHtml(m.api_key_env) + '</code></div>' +
        '<div class="slirn-llm-item-ops">' +
          (isCur
            ? '<span class="slirn-llm-item-cur">✅ 当前使用中</span>'
            : '<button class="slirn-btn slirn-btn-sm slirn-btn-primary" data-action="llm-use" data-id="' +
              escapeHtml(m.id) + '">⭐ 设为当前</button>') +
          '<button class="slirn-btn slirn-btn-sm" data-action="llm-test" data-id="' + escapeHtml(m.id) + '">测试</button>' +
          '<button class="slirn-btn slirn-btn-sm" data-action="llm-edit" data-id="' + escapeHtml(m.id) + '">编辑</button>' +
          '<button class="slirn-btn slirn-btn-sm" data-action="llm-remove" data-id="' + escapeHtml(m.id) + '">删除</button>' +
        '</div>' +
      '</div>';
    }).join('');
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  // 上传文件到我们自己的端点 — 用 XHR 拿进度事件，弹窗一直显示
  // 不用 Gradio 内置 /gradio_api/upload：那个端点用 python_multipart，并发会截断大文件
  function uploadFile(file) {
    openUploadDialog(file);
    return new Promise(function(resolve, reject) {
      var fd = new FormData();
      fd.append('files', file);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', SLIRN_API + '/upload_video');
      xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) setUploadProgress(e.loaded, e.total);
        else setUploadProgress(0, file.size);
      };
      xhr.onload = function() {
        try {
          var j = JSON.parse(xhr.responseText);
          if (j && j.ok && j.path) {
            resolve(j.path);
          } else {
            reject(new Error((j && j.error) || ('Upload failed: ' + xhr.status)));
          }
        } catch (parseErr) {
          reject(new Error('Upload parse error: ' + parseErr));
        }
      };
      xhr.onerror = function() { reject(new Error('网络错误，上传失败')); };
      xhr.onabort = function() { reject(new Error('上传已取消')); };
      xhr.send(fd);
    });
  }

  // 业务回调：把 server 返回的 file_info / cut_done 渲染到 DOM
  function applyFileInfo(info) {
    if (!info) return;
    var el = document.getElementById('slirn-file-info-display');
    if (el) el.innerHTML = '<div class="slirn-status-msg">📁 ' + info.name + '（' + info.size_mb + ' MB · ' + info.duration + '）</div>';
    var card = document.getElementById('slirn-player-card');
    var v = document.getElementById('slirn-player');
    // Gradio 6 文件 API：/gradio_api/file=<urlencoded-path>
    if (card && v) { v.src = '/gradio_api/file=' + encodeURI(info.path); v.load(); card.style.display = ''; bindSpeedControl(v); }  // 倍速控件（REQ-20260916-014）
    // 把 seek 滑块的最大值设为视频时长（默认 HTML 写死 600，需要根据实际视频更新）
    var seek = document.getElementById('slirn-seek');
    if (seek && info.duration_seconds) {
      seek.max = info.duration_seconds;
      seek.value = 0;
    }
    // 更新时间显示 HH:MM:SS.mmm / HH:MM:SS.mmm
    var timeDisp = document.getElementById('slirn-time-display');
    if (timeDisp && info.duration) {
      timeDisp.textContent = '00:00:00.000 / ' + info.duration + '.000';
    }
  }
  function applyCutDone(info) {
    if (!info) return;
    var msg = document.getElementById('slirn-cut-msg');
    if (msg) msg.innerHTML = '<div class="slirn-status-msg">✅ 待剪辑视频已生成 ' + info.size_mb + ' MB</div>';
    var wrap = document.getElementById('slirn-cut-preview-wrap');
    var v = document.getElementById('slirn-cut-preview');
    if (wrap && v) { v.src = '/gradio_api/file=' + encodeURI(info.path); v.load(); wrap.style.display = ''; bindSpeedControl(v); }  // 倍速控件（REQ-20260916-014）
  }

  // ===== 字幕生成：后台轮询 + 详情刷新（REQ-20260915-001）=====
  function fmtElapsed(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? (m + ' 分 ' + s + ' 秒') : (s + ' 秒');
  }

  function refreshDetail(tid) {
    postJSON(SLIRN_API + '/view_task', {task_id: tid}).then(function(r) {
      if (r && r.ok && r.html) {
        var d = document.getElementById('slirn-tab-detail');
        if (d) { d.innerHTML = r.html; d.style.display = ''; }
        bindSubPlayer();
        bindRevPlayer();
      }
    });
  }

  // ===== 编辑任务 / 剪辑工作台（REQ-20260915-003）=====
  function initTaskEdit() {
    var st = document.getElementById('slirn-edit-state');
    if (!st) { window.slirnEditTaskId = null; return; }
    window.slirnEditTaskId = st.getAttribute('data-task-id');
    window.slirnSelectedFile = st.getAttribute('data-video-path') || '';
    // 播放器：完整原视频 + 滑块全长
    var card = document.getElementById('slirn-player-card');
    var v = document.getElementById('slirn-player');
    var url = st.getAttribute('data-video-url');
    if (card && v && url) {
      v.src = url; v.load();
      card.style.display = '';
      bindSpeedControl(v);  // 倍速控件（REQ-20260916-014）
    }
    var durSec = parseFloat(st.getAttribute('data-duration-seconds')) || 0;
    var seek = document.getElementById('slirn-seek');
    if (seek && durSec > 0) { seek.max = durSec; seek.value = 0; }
    var td = document.getElementById('slirn-time-display');
    if (td && durSec > 0) {
      var ds = secondsToHMS(durSec);
      td.textContent = '00:00:00.000 / ' + ds + '.000';
    }
    try { window.slirnRenderHwChips && window.slirnRenderHwChips(); } catch (e) {}
  }

  function openWorkbench(tid) {
    // REQ-20260918-053：进 wb 时把 task_id 写到 URL hash，F5/Cmd+R 刷新仍在同一任务页
    if (tid && history && history.replaceState) {
      try { history.replaceState(null, '', '#wb=' + encodeURIComponent(tid)); } catch (e) {}
    }
    postJSON(SLIRN_API + '/workbench', {task_id: tid}).then(function(r) {
      hideWbLoader();  // REQ-20260918-051：无论成功失败都关掉等待弹窗
      if (r && r.ok && r.html) {
        var w = document.getElementById('slirn-tab-workbench');
        if (!w) return;
        // 刷新前快照（REQ-20260918-046）：哪些阶段已完成 + 用户当前所在面板 —
        // 供刷新后判断「当前阶段刚完成 → 自动进下一阶段」
        var prevDone = {}, prevActive = '';
        var hadWb = !!document.querySelector('#slirn-tab-workbench .slirn-wb-stage');
        if (hadWb) {
          document.querySelectorAll('#slirn-tab-workbench .slirn-wb-stage.done').forEach(function(s) {
            prevDone[s.getAttribute('data-pane') || ''] = 1;
          });
          document.querySelectorAll('#slirn-tab-workbench .slirn-wb-pane').forEach(function(p) {
            if (p.style.display !== 'none') prevActive = (p.id || '').replace('slirn-wb-pane-', '');
          });
        }
        // ===== REQ-20260918-047 v3：跨 innerHTML 替换保留 wb 内全部表单状态 =====
        // 用户反馈：wb 内任何复选框勾上后点其他地方 → 勾选被清空。
        // 根因：openWorkbench 走 w.innerHTML = r.html 整体替换 wb，wb 内
        // 所有 <input> / <select> / <textarea> 的用户当前状态（勾选 / 输入 /
        // 选中项）随旧 DOM 一起销毁。即使 pipe-panel 已 detach 保住，「阶段
        // 自动进入下一阶段」「字幕生成阶段区分说话人」「备注输入」「决策
        // 下拉」这些 wb 其他位置的表单仍然丢失。修复：替换前对 wb + pipe-panel
        // 内所有有 id 的表单做快照，替换 + bind/apply 状态恢复之后逐项
        // 回填用户的当前值，用户操作完整保留。
        function _snapInputs(root) {
          if (!root) return {};
          var snap = {};
          root.querySelectorAll('input, select, textarea').forEach(function(el) {
            if (!el.id) return;
            if (el.type === 'checkbox') snap[el.id] = {kind: 'cb', checked: el.checked};
            else if (el.type === 'radio') { if (el.checked) snap[el.id] = {kind: 'radio'}; }
            else snap[el.id] = {kind: 'val', value: el.value};
          });
          return snap;
        }
        function _restoreInputs(snap) {
          Object.keys(snap).forEach(function(id) {
            var el = document.getElementById(id);
            if (!el) return;  // 新 wb 里没这字段（数据驱动）→ 跳过
            var s = snap[id];
            if (s.kind === 'cb') el.checked = s.checked;
            else if (s.kind === 'radio') el.checked = true;
            else el.value = s.value;
          });
        }
        // 1) 快照 pipe-panel + pipe-status（它们 detach 后会保留，但保险起见也快照）
        var _pipePanel = document.getElementById('slirn-pipe-panel');
        var _pipeStatus = document.getElementById('slirn-pipe-status');
        var _panelSnap = _snapInputs(_pipePanel);
        var _statusSnap = _snapInputs(_pipeStatus);
        // 2) 快照 wb 内除 pipe-panel + pipe-status 之外的所有表单
        var _wbAllInputsSnap = {};
        if (w) {
          w.querySelectorAll('input, select, textarea').forEach(function(el) {
            if (!el.id) return;
            if (el === _pipePanel || el === _pipeStatus) return;
            if (_pipePanel && _pipePanel.contains(el)) return;
            if (_pipeStatus && _pipeStatus.contains(el)) return;
            if (el.type === 'checkbox') _wbAllInputsSnap[el.id] = {kind: 'cb', checked: el.checked};
            else if (el.type === 'radio') { if (el.checked) _wbAllInputsSnap[el.id] = {kind: 'radio'}; }
            else _wbAllInputsSnap[el.id] = {kind: 'val', value: el.value};
          });
        }
        // 3) detach pipe-panel + pipe-status
        if (_pipePanel) _pipePanel.parentNode.removeChild(_pipePanel);
        if (_pipeStatus) _pipeStatus.parentNode.removeChild(_pipeStatus);
        w.innerHTML = r.html;
        ALL_TABS.forEach(function(id) { var el = document.getElementById(id); if (el) el.style.display = 'none'; });
        var d = document.getElementById('slirn-tab-detail');
        if (d) d.style.display = 'none';
        w.style.display = '';
        window.scrollTo({top: 0, behavior: 'smooth'});
        bindSubPlayer();
        bindRevPlayer();
        // 重渲染后旧行引用全部失效：试听状态清零（防跳播序列指向已移除的行）
        cutKeepSeq = null; cutKeepIdx = 0; cutKeepMode = null;
        bindCutPlayer();  // 切分修剪播放器（timeupdate 高亮/自动停/跳播 — REQ-20260916-011）
        var rcPv = document.getElementById('slirn-rc-player');
        if (rcPv) bindSpeedControl(rcPv);  // 粗剪成片预览倍速（REQ-20260916-014 同款）
        var optPv = revVis('slirn-opt-player');
        if (optPv) bindSpeedControl(optPv);  // 优化字幕行播放倍速（REQ-20260917-030）
        bindOptRows(tid);
        setupOptWordsPagination();  // REQ-20260918-050：词频列表分页（每次 wb 重渲后调）
        optInputOverflowInit();  // REQ-20260918-058：替换输入框溢出检测 + 自动换行
        bindFineControls();  // REQ-20260919-061：精剪视频·素材合成器控件绑定（位置/缩放/字体/输出自动保存）
        bindFineSteppers();  // REQ-20260919-061：精剪视频·数值控件（number input + ▲▼）与滑块双向同步
        bindCropAspectLink();  // REQ-20260919-061a：crop_w / crop_h 按 16:9 联动（防变形）
        bindBgWhiteDetector();  // REQ-20260919-062：背景图白色区域检测（算法 + 阈值 + 4 角点 + 填充）
        bindFineMaterialPreviews();  // REQ-20260919-063：每个素材的 👁️ 预览按钮（图片/视频/音频/SRT）
        // 模板列表已搬到「📥 引用参数」modal 里 — wb 渲染时不主动拉，按需由 fineImportShow() 取
        var optSt = revVis('slirn-opt-status');  // 优化 job 还在跑 → 恢复轮询
        if (optSt && optSt.dataset.taskId && optSt.dataset.state === 'running')
          startOptPolling(optSt.dataset.taskId);
        applyWbStagesState();  // 恢复上次收起/展开（跨刷新保持 — REQ-20260916-002）
        applyRevRigorState();  // 上次选过的严谨性级别预填（REQ-20260916-003）
        applyWbAutoNextState();  // 自动进下一阶段开关回填（REQ-20260918-046）
        wbAutoNextMaybe(prevDone, hadWb, prevActive);  // 当前阶段刚完成 → 按设置跳下一阶段
        // 4) 恢复 wb 内非 pipe-panel / 非 pipe-status 的表单（apply* 状态回填之后，
        //    用户手动改的值会覆盖回填，这是预期的：用户最近的操作胜出）
        _restoreInputs(_wbAllInputsSnap);
        // 5) 把流程配置面板插回新的 wb-inner
        var _inner = document.getElementById('slirn-tab-workbench-inner');
        if (_inner) {
          // pipe-panel 紧跟 wb 顶部信息卡之后（在 slirn-wb-main 之前；位置与模板一致）
          if (_pipeStatus && _pipeStatus.parentNode !== _inner) _inner.appendChild(_pipeStatus);
          if (_pipePanel && _pipePanel.parentNode !== _inner) _inner.appendChild(_pipePanel);
          // ===== BUGFIX：拔掉 r.html 模板里的「空壳」重复元素 =====
          // 现象：用户切换任务（或工作台刷新时）出现两个「流程配置」面板 ——
          // 一个是当前任务的（新挂载，由 slirnPipelineMount 渲染），一个是上一个
          // 任务的（OLD 重新 attach，task-id 与内容都还是旧的）。根因：r.html
          // 模板自带空 <section id="slirn-pipe-panel">，又 appendChild 了 OLD
          // pipe-panel，DOM 里就同时有两个同 id 的元素。
          // 修复：保留 OLD（其内容已加载，跨刷新保住表单状态），删掉模板里的
          // 空壳。slirnPipelineMount 会按 OLD 当前的 data-task-id 与新 tid 比对，
          // 不同则 loadPanel(tid) 重新加载（任务切换场景），相同则保留表单（同
          // 任务刷新场景）。
          // 注意：querySelectorAll 必须用 'not-self' 类名筛除 OLD，否则会把自己
          // 也删掉。我们用「不在 _pipePanel/_pipeStatus 集合内」条件筛选。
          // BUGFIX：只有 OLD 存在时才清理空壳。首次进 wb（重启后）_pipePanel=null，
          // 此时「el !== null」恒为真 → 模板里的空 pipe-panel 会被误删，导致
          // 用户看到「流程配置没了」。必须用 _pipePanel || _pipeStatus 守住。
          if (_pipePanel || _pipeStatus) {
            var _toRemove = [];
            _inner.querySelectorAll('section#slirn-pipe-panel, div#slirn-pipe-status').forEach(function(el) {
              if (el !== _pipePanel && el !== _pipeStatus) _toRemove.push(el);
            });
            _toRemove.forEach(function(el) {
              try { el.parentNode.removeChild(el); } catch (err) {}
            });
          }
        }
        // 6) 恢复 pipe-panel / pipe-status 内表单状态（节点引用保住但保险起见也回填）
        _restoreInputs(_panelSnap);
        _restoreInputs(_statusSnap);
        // 流程配置面板挂载（REQ-20260918-047 v2）：工作台内常驻纵向面板
        // 面板若已存在（同 tid + 有 innerHTML）→ 不重新加载，状态保留
        if (window.slirnPipelineMount) window.slirnPipelineMount(tid);
      } else if (r && r.error) {
        toast('❌ ' + r.error, 'error');
      }
    });
  }

  // ===== 阶段列表收起/展开（localStorage 记忆 — REQ-20260916-002）=====
  function applyWbStagesState() {
    var host = document.getElementById('slirn-tab-workbench-inner');
    if (!host) return;
    var collapsed = '';
    try { collapsed = localStorage.getItem('slirnWbStagesCollapsed') || ''; } catch (err) {}
    if (collapsed !== '0' && collapsed !== '1') {  // 脏值自愈：当作未收起（REQ-20260916-011）
      collapsed = '';
      try { localStorage.removeItem('slirnWbStagesCollapsed'); } catch (err) {}
    }
    host.classList.toggle('wb-stages-collapsed', collapsed === '1');
  }

  // ===== 完成后自动进下一阶段（REQ-20260918-046）=====
  // 阶段条上方的开关（localStorage 记忆，默认关）。开启后：工作台刷新时若
  // 「用户当前所在阶段」刚变为完成 → 自动切到下一阶段并提示。关着时行为不变。
  function wbAutoNextOn() {
    try { return localStorage.getItem('slirnWbAutoNext') === '1'; } catch (err) { return false; }
  }
  function applyWbAutoNextState() {
    var el = document.getElementById('slirn-wb-autonext');
    if (el) el.checked = wbAutoNextOn();
  }
  document.addEventListener('change', function(e) {
    if (e.target && e.target.id === 'slirn-wb-autonext') {
      try {
        localStorage.setItem('slirnWbAutoNext', e.target.checked ? '1' : '');
      } catch (err) {}
      toast(e.target.checked ? '✅ 已开启：当前阶段完成后自动进入下一阶段'
                             : '↩️ 已关闭自动进入下一阶段');
    }
  });
  function wbAutoNextMaybe(prevDone, hadWb, prevActive) {
    if (!hadWb || !wbAutoNextOn() || !prevActive) return;
    // REQ-20260918-053：仅迭代真正的流水线阶段（排除「执行日志」等视图阶段）
    var stages = [];
    document.querySelectorAll('#slirn-tab-workbench .slirn-wb-stage:not(.slirn-wb-stage-extra)').forEach(function(s) {
      stages.push(s.getAttribute('data-pane') || '');
    });
    var justDone = '';
    stages.forEach(function(pk) {
      var el = document.querySelector('#slirn-tab-workbench .slirn-wb-stage[data-pane="' + pk + '"]');
      if (el && el.classList.contains('done') && !prevDone[pk] && pk === prevActive) justDone = pk;
    });
    if (!justDone) return;
    var idx = stages.indexOf(justDone);
    if (idx < 0 || idx + 1 >= stages.length) return;  // 最后一个阶段：没有下一阶段
    switchWbPane(stages[idx + 1]);
    toast('✅ 本阶段完成 — 已自动进入下一阶段（可在阶段上方关闭自动跳转）');
  }

  // ===== 严谨性级别：上次选择预填（不发起新分析也可见 — REQ-20260916-003）=====
  function applyRevRigorState() {
    if (!document.querySelector('input[name="slirn-rev-rigor"]:checked')) {
      var saved = '';
      try { saved = localStorage.getItem('slirnRevRigor') || ''; } catch (err) {}
      if (['high', 'medium', 'low', 'custom'].indexOf(saved) >= 0) {
        var el = document.querySelector('input[name="slirn-rev-rigor"][value="' + saved + '"]');
        if (el) el.checked = true;
      }
    }
    syncRigorCustomUI();  // 自定义档编辑区跟随（REQ-20260916-007）
  }

  // ===== 自定义严谨性（REQ-20260916-007/009）：编辑区展开 + 底稿/草稿预填 =====
  // 选中「自定义」才展开；textarea 首次展开预填 localStorage 草稿，无草稿用高档底稿
  // （data-prompt-high 与服务端 default_custom_prompt 回退逻辑同源），此后不再覆盖用户编辑
  function syncRigorCustomUI() {
    var wrap = document.getElementById('slirn-rigor-custom');
    if (!wrap) return;
    var sel = document.querySelector('input[name="slirn-rev-rigor"]:checked');
    var isCustom = !!(sel && sel.value === 'custom');
    wrap.style.display = isCustom ? '' : 'none';
    var ta = wrap.querySelector('.slirn-rigor-custom-text');
    if (isCustom && ta && !ta.dataset.slirnFilled) {
      var draft = '';
      try { draft = localStorage.getItem('slirnRevCustomPrompt') || ''; } catch (err) {}
      ta.value = draft || wrap.getAttribute('data-prompt-high') || '';
      ta.dataset.slirnFilled = '1';
    }
  }
  document.addEventListener('change', function(e) {
    if (e.target && e.target.name === 'slirn-rev-rigor') syncRigorCustomUI();
    // 切分修剪·组级决策下拉（REQ-20260917-027）：鼠标改判与快捷键 K/D/S 同效；
    // 选「维持原状」=取消改判，选回当前值 = 无操作（区别于键盘的「再按同键取消」）
    if (e.target && e.target.classList && e.target.classList.contains('slirn-cut-actsel')) {
      var gAct = e.target.closest('.slirn-cut-group');
      if (gAct) {
        var nvAct = e.target.value || '';
        if (nvAct && (gAct.getAttribute('data-act') || '') === nvAct) return;
        cutApplyAct(gAct, nvAct);
      }
    }
    // 优化字幕·替换值被编辑（REQ-20260917-038）：编辑即视为明确处理 → 计入词进度
    if (e.target && e.target.classList && e.target.classList.contains('slirn-opt-after')) {
      optOccMarkReviewed(e.target.closest('.slirn-opt-occ'));
    }
    // 决策下拉变化 → 行 data-decision/data-final 跟随（实质口径），过滤
    // 计数/可见性实时刷新（REQ-20260916-010；未保存前纯前端，刷新即还原）
    if (e.target && e.target.classList && e.target.classList.contains('slirn-rev-select')) {
      var rowF = e.target.closest('.slirn-rev-row');
      if (rowF) {
        var dv = e.target.value || 'pending';
        var suggF = rowF.getAttribute('data-sugg') || '';
        var finF = '';
        if (dv === 'keep' || dv === 'delete' || dv === 'split' || dv === 'fix') finF = dv;
        else if (dv === 'accept' && (suggF === 'keep' || suggF === 'delete'
            || suggF === 'split' || suggF === 'fix')) finF = suggF;
        rowF.setAttribute('data-decision', dv);
        rowF.setAttribute('data-final', finF);
        // 决策改为切分/内容更正 → 展开详情并把行内输入框备好（REQ-20260917-026）：
        // 输入框语义跟随决策（split=切分修剪后内容 / fix=更正后内容），为空时从
        // 详情块的模型建议文本（✂️ 建议保留 / ✏️ 更正后）预填，直接可微调；
        // 切分修剪阶段自动取输入框值生效（cutlist_service._fix_target/_split_target）
        var noteF = rowF.querySelector('.slirn-rev-note-input');
        if (noteF && (dv === 'split' || dv === 'fix')) {
          noteF.placeholder = dv === 'fix' ? '更正后内容（可微调）' : '切分修剪后内容（可空）';
          if (!noteF.value.trim()) {
            var ktF = rowF.querySelector('.slirn-rev-keeptext');
            var mF = ktF && /「(.+)」/.exec(ktF.textContent || '');
            if (mF) noteF.value = mF[1];
          }
          rowF.classList.add('open');
          var tgF = rowF.querySelector('.slirn-rev-toggle');
          if (tgF) tgF.textContent = '▴';
          try {
            noteF.focus();
            noteF.setSelectionRange(noteF.value.length, noteF.value.length);
          } catch (err) {}
        }
        revFilterSync();
      }
    }
  });
  // 草稿实时保存（仅本机浏览器）— 不依赖点「分析」提交
  document.addEventListener('input', function(e) {
    if (e.target && e.target.classList &&
        e.target.classList.contains('slirn-rigor-custom-text')) {
      try { localStorage.setItem('slirnRevCustomPrompt', e.target.value); } catch (err) {}
    }
  });

  function switchWbPane(paneKey) {
    document.querySelectorAll('.slirn-wb-stage').forEach(function(s) {
      s.classList.toggle('active', s.getAttribute('data-pane') === paneKey);
    });
    document.querySelectorAll('.slirn-wb-pane').forEach(function(p) {
      p.style.display = (p.id === 'slirn-wb-pane-' + paneKey) ? '' : 'none';
    });
    // 面板显隐切换后过滤计数才可见：重算 chips/可见性（工作台初始打开时
    // 修订面板可能隐藏，revRows 取不到行 → 计数 0；REQ-20260916-010）
    revFilterSync();
    // REQ-20260918-053：首次进入执行日志面板时自动加载一次（无需手动点刷新）
    if (paneKey === 'logs' && !logsState._loaded) {
      loadLogs();
      logsState._loaded = true;
    }
  }

  // ===== REQ-20260918-053：执行日志面板（过滤 + 拉取 + 渲染）=====
  // 全局状态（同一时刻只看一个任务）：面板内 chip 切换 + 关键词输入 + 刷新按钮都改它
  // REQ-20260920-084：新增 timeFrom / timeTo / auto 过滤
  // REQ-20260920-086：新增 page / pageSize 分页
  var logsState = {
    kinds: [],        // [] = 不限（默认全选）
    statuses: [],     // [] = 不限
    keyword: '',
    timeFrom: '',     // ISO 8601；空 = 不限
    timeTo: '',       // ISO 8601；空 = 不限
    auto: 'any',      // 'any' | 'manual' | 'auto'
    page: 1,          // REQ-086：当前页（从 1 起）
    pageSize: 20,     // REQ-086：每页条数（默认 20，可改 10/50/100）
    _loaded: false,   // 首次进入是否已加载
  };

  function _logsReadFilters() {
    var box = document.getElementById('slirn-wb-pane-logs');
    if (!box) return;
    logsState.kinds = [];
    box.querySelectorAll('.slirn-chip[data-log-kind].active').forEach(function(b) {
      logsState.kinds.push(b.getAttribute('data-log-kind') || '');
    });
    logsState.statuses = [];
    box.querySelectorAll('.slirn-chip[data-log-status].active').forEach(function(b) {
      logsState.statuses.push(b.getAttribute('data-log-status') || '');
    });
    // REQ-20260920-084：读 auto chip
    var autoActive = box.querySelector('.slirn-chip[data-log-auto].active');
    logsState.auto = autoActive ? (autoActive.getAttribute('data-log-auto') || 'any') : 'any';
    // REQ-20260920-084：读时间段 chip
    var timeActive = box.querySelector('.slirn-chip[data-log-time].active');
    var timeRange = timeActive ? (timeActive.getAttribute('data-log-time') || '') : '';
    if (timeRange === 'today') {
      var d = new Date();
      d.setHours(0, 0, 0, 0);
      logsState.timeFrom = d.toISOString();
      logsState.timeTo = '';
    } else if (timeRange === '7d') {
      var d7 = new Date(Date.now() - 7 * 86400000);
      logsState.timeFrom = d7.toISOString();
      logsState.timeTo = '';
    } else if (timeRange === '30d') {
      var d30 = new Date(Date.now() - 30 * 86400000);
      logsState.timeFrom = d30.toISOString();
      logsState.timeTo = '';
    } else {
      logsState.timeFrom = '';
      logsState.timeTo = '';
    }
    var kw = document.getElementById('slirn-logs-keyword');
    logsState.keyword = kw ? (kw.value || '') : '';
  }

  function loadLogs() {
    var list = document.getElementById('slirn-logs-list');
    if (!list) return;
    var tid = list.getAttribute('data-task-id') || '';
    if (!tid) return;
    _logsReadFilters();
    var payload = {
      task_id: tid,
      kinds: logsState.kinds,
      statuses: logsState.statuses,
      keyword: logsState.keyword,
      // REQ-20260920-084：传 time_from / time_to / auto 给 list_logs
      time_from: logsState.timeFrom || '',
      time_to: logsState.timeTo || '',
      auto: logsState.auto || 'any',
      // REQ-20260920-086：分页 offset / limit（替代旧 limit=200）
      offset: (Math.max(1, logsState.page) - 1) * logsState.pageSize,
      limit: logsState.pageSize,
    };
    var countEl = document.querySelector('.slirn-logs-count');
    if (countEl) countEl.textContent = '加载中…';
    // REQ-20260920-084：改调 /slirn/api/list_logs（REQ-081 新端点；支持 time_from / auto）
    postJSON(SLIRN_API + '/list_logs', payload).then(function(r) {
      if (!r || !r.ok) {
        if (countEl) countEl.textContent = '加载失败';
        list.innerHTML = '<div class="slirn-form-hint slirn-logs-empty">加载失败：' + escapeHtml((r && r.error) || '未知错误') + '</div>';
        return;
      }
      _renderLogsList(list, r.items || []);
      _renderLogsPager(r);
      // REQ-086：countEl 显示「过滤后总条数」（不是当前页）
      if (countEl) countEl.textContent = (r.total || 0) + ' 条';
    }).catch(function(err) {
      if (countEl) countEl.textContent = '加载失败';
      list.innerHTML = '<div class="slirn-form-hint slirn-logs-empty">加载失败：' + escapeHtml(String(err)) + '</div>';
    });
  }

  // REQ-20260920-086：渲染分页导航（首页/上一页/下一页/末页 + 当前页/总页数/总条数）
  function _renderLogsPager(r) {
    var pager = document.getElementById('slirn-logs-pager');
    if (!pager) return;
    var total = r.total || 0;
    var pageSize = r.page_size || logsState.pageSize;
    var totalPages = r.total_pages || Math.max(1, Math.ceil(total / pageSize));
    var page = r.page || 1;
    pager.setAttribute('data-page', String(page));
    pager.setAttribute('data-page-size', String(pageSize));
    pager.setAttribute('data-total', String(total));
    var pageEl = pager.querySelector('[data-bind="page"]');
    var tpEl = pager.querySelector('[data-bind="total-pages"]');
    var totalEl = pager.querySelector('[data-bind="total"]');
    if (pageEl) pageEl.textContent = String(page);
    if (tpEl) tpEl.textContent = String(totalPages);
    if (totalEl) totalEl.textContent = String(total);
    // 同步 logsState.page / pageSize（防止 back/forward 不一致）
    logsState.page = page;
    logsState.pageSize = pageSize;
    // 翻页按钮 disabled 状态
    var btns = pager.querySelectorAll('.slirn-pager-btn');
    btns.forEach(function(b) {
      var act = b.getAttribute('data-pager');
      var disabled = (act === 'first' || act === 'prev')
        ? page <= 1
        : page >= totalPages;
      b.disabled = disabled;
    });
  }

  // REQ-20260918-053：阶段中文标签（与后端 KIND_LABELS 对齐，复制一份避免跨域/加载顺序问题）
  // REQ-20260920-084：补齐 REQ-074 / REQ-081 加的 6 个 kind
  var LOG_KIND_LABELS = {
    subtitle_generation: '字幕生成',
    subtitle_review: '字幕修订',
    rough_cut: '切分修剪',
    rough_cut_link_person: '关联人员ID',
    rough_compose: '粗剪合成',
    rough_compose_delete: '删除粗剪成品',
    optimize: '优化字幕',
    fine_ai_layout: 'AI 智能布局',
    fine_bg_detect: '检测区域',
    fine_preview: '生成预览',
    fine_export: '最终导出视频',
  };
  function _formatLogsDuration(ms) {
    if (!ms || ms < 0) return '-';
    var s = Math.floor(ms / 1000);
    if (s < 60) return s + '秒';
    var m = Math.floor(s / 60);
    var rs = s % 60;
    if (m < 60) return m + '分' + rs + '秒';
    var h = Math.floor(m / 60);
    var rm = m % 60;
    return h + '时' + rm + '分';
  }
  function _renderLogsList(list, items) {
    if (!items.length) {
      list.innerHTML = '<div class="slirn-form-hint slirn-logs-empty">无符合条件的记录。点击「清除」放宽过滤试试。</div>';
      return;
    }
    var html = '';
    items.forEach(function(it) {
      var status = it.status || 'running';
      var badgeCls = status === 'success' ? 'slirn-exec-ok'
                   : status === 'failed' ? 'slirn-exec-fail'
                   : 'slirn-exec-running';
      var badgeText = status === 'success' ? '✅ 成功'
                    : status === 'failed' ? '❌ 失败'
                    : '⏳ 运行中';
      var kindLabel = LOG_KIND_LABELS[it.kind] || it.kind || '操作';
      var startIso = (it.started_at_iso || '').replace('T', ' ').slice(0, 19);
      var endIso = (it.finished_at_iso || '').replace('T', ' ').slice(0, 19) || '—';
      var duration = _formatLogsDuration(it.duration_ms);
      var extra = it.extra || {};
      var extraBits = [];
      if (extra.lines) extraBits.push(extra.lines + ' 行');
      if (extra.occurrences) extraBits.push(extra.occurrences + ' 处');
      if (extra.segments) extraBits.push(extra.segments + ' 段');
      if (extra.intervals) extraBits.push(extra.intervals + ' 区间');
      if (extra.del_speakers_count) extraBits.push('删除 ' + extra.del_speakers_count + ' 说话人');
      if (extra.speakers) extraBits.push(extra.speakers + ' 位说话人');
      if (extra.output_mb) extraBits.push(extra.output_mb + ' MB');
      var extraStr = extraBits.length ? ' · ' + extraBits.join(' / ') : '';
      var errHtml = it.error
        ? '<div class="slirn-exec-err">' + escapeHtml(String(it.error).slice(0, 300)) + '</div>'
        : '';
      html += '<div class="slirn-exec-row" data-status="' + escapeHtml(status) + '">'
        + '<span class="slirn-exec-idx">#' + escapeHtml(String(it.id || '').slice(-8)) + '</span>'
        + '<span class="slirn-exec-kind"><strong>' + escapeHtml(kindLabel) + '</strong>'
        + ' · ' + escapeHtml(startIso) + ' → ' + escapeHtml(endIso) + '</span>'
        + '<span class="slirn-exec-dur">' + escapeHtml(duration) + '</span> '
        + '<span class="' + badgeCls + '">' + badgeText + extraStr + '</span>'
        + errHtml
        + '</div>';
    });
    list.innerHTML = html;
  }

  // chip 点击：toggle .active 类 + 自动重查
  // REQ-20260920-084：data-log-auto / data-log-time chip 走单选（互斥），其他走多选
  // REQ-20260920-086：过滤变化时重置 page = 1；翻页按钮 click handler
  document.addEventListener('click', function(e) {
    var t = e.target;
    if (!t || !t.classList) return;
    if (t.classList.contains('slirn-chip') && (t.hasAttribute('data-log-kind') || t.hasAttribute('data-log-status'))) {
      t.classList.toggle('active');
      logsState.page = 1;  // REQ-086：过滤变化重置 page
      loadLogs();
      return;
    }
    // REQ-20260920-084：auto chip（单选）
    if (t.classList.contains('slirn-chip') && t.hasAttribute('data-log-auto')) {
      var boxA = document.getElementById('slirn-wb-pane-logs');
      if (!boxA) return;
      boxA.querySelectorAll('.slirn-chip[data-log-auto].active').forEach(function(b) {
        if (b !== t) b.classList.remove('active');
      });
      t.classList.toggle('active');
      logsState.page = 1;  // REQ-086
      loadLogs();
      return;
    }
    // REQ-20260920-084：time chip（单选）
    if (t.classList.contains('slirn-chip') && t.hasAttribute('data-log-time')) {
      var boxT = document.getElementById('slirn-wb-pane-logs');
      if (!boxT) return;
      boxT.querySelectorAll('.slirn-chip[data-log-time].active').forEach(function(b) {
        if (b !== t) b.classList.remove('active');
      });
      t.classList.toggle('active');
      logsState.page = 1;  // REQ-086
      loadLogs();
      return;
    }
    if (t.getAttribute && t.getAttribute('data-action') === 'logs-refresh') {
      e.preventDefault();
      loadLogs();
      return;
    }
    if (t.getAttribute && t.getAttribute('data-action') === 'logs-clear-kinds') {
      e.preventDefault();
      var box = document.getElementById('slirn-wb-pane-logs');
      if (!box) return;
      box.querySelectorAll('.slirn-chip[data-log-kind].active').forEach(function(b) { b.classList.remove('active'); });
      logsState.page = 1;  // REQ-086
      loadLogs();
      return;
    }
    if (t.getAttribute && t.getAttribute('data-action') === 'logs-clear-statuses') {
      e.preventDefault();
      var box = document.getElementById('slirn-wb-pane-logs');
      if (!box) return;
      box.querySelectorAll('.slirn-chip[data-log-status].active').forEach(function(b) { b.classList.remove('active'); });
      logsState.page = 1;  // REQ-086
      loadLogs();
      return;
    }
    // REQ-20260920-086：分页按钮 click（首页/上一页/下一页/末页）
    if (t.classList && t.classList.contains('slirn-pager-btn')) {
      var act = t.getAttribute('data-pager');
      var pager = document.getElementById('slirn-logs-pager');
      if (!pager || !act) return;
      if (t.disabled) return;  // disabled 按钮不响应
      var curPage = parseInt(pager.getAttribute('data-page') || '1', 10);
      var tp = parseInt(pager.querySelector('[data-bind="total-pages"]').textContent || '1', 10);
      if (act === 'first') logsState.page = 1;
      else if (act === 'prev') logsState.page = Math.max(1, curPage - 1);
      else if (act === 'next') logsState.page = Math.min(tp, curPage + 1);
      else if (act === 'last') logsState.page = Math.max(1, tp);
      loadLogs();
      return;
    }
  });

  // REQ-20260920-086：每页大小下拉 change 事件
  document.addEventListener('change', function(e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains('slirn-pager-size')) {
      logsState.pageSize = parseInt(t.value || '20', 10);
      logsState.page = 1;  // 改 pageSize 重置 page
      loadLogs();
    }
  });

  // 关键词输入：300ms 防抖自动重查
  var _logsKwTimer = null;
  document.addEventListener('input', function(e) {
    var t = e.target;
    if (!t || t.id !== 'slirn-logs-keyword') return;
    if (_logsKwTimer) clearTimeout(_logsKwTimer);
    _logsKwTimer = setTimeout(function() {
      loadLogs();
    }, 300);
  });

  var subPollTimer = null;
  function startSubPolling(tid) {
    if (subPollTimer) { clearInterval(subPollTimer); subPollTimer = null; }
    var update = function() {
      postJSON(SLIRN_API + '/subtitle_status', {task_id: tid}).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        var el = document.getElementById('slirn-asr-status');
        if (j.state === 'running') {
          if (el) {
            el.style.display = '';
            el.dataset.state = 'running';
            el.innerHTML = '⏳ ' + escapeHtml(j.stage || '处理中') + ' · 已耗时 ' + fmtElapsed(j.elapsed_s || 0);
          }
        } else {
          if (subPollTimer) { clearInterval(subPollTimer); subPollTimer = null; }
          if (j.state === 'done') {
            toast('✅ 字幕生成完成：' + (j.segments_count || 0) + ' 段');
            // 字幕完成时用户可能在工作台或详情页 — 刷新所在视图
            if (el && el.closest && el.closest('#slirn-tab-workbench')) { openWorkbench(tid); }
            else { refreshDetail(tid); }
          } else if (j.state === 'error') {
            if (el) {
              el.style.display = '';
              el.dataset.state = 'error';
              el.innerHTML = '❌ ' + escapeHtml(j.error || '生成失败');
            }
            toast('❌ 字幕生成失败', 'error');
          }
        }
      });
    };
    update();
    subPollTimer = setInterval(update, 2000);
  }

  // ===== 功能区折叠（REQ-20260918-044）=====
  // 各阶段顶部的说明/统计/快捷键/筛选/跳转/批量/搜索条：外包一层可折叠壳，
  // 点胶囊头收起只留一行、再点展开 — 用户按需留出空间、聚焦当前要做的事。
  // 折叠状态记在本机（按区独立记忆）。MutationObserver 兜底：面板（服务端
  // innerHTML）或条内容（JS 重渲染）更新后自动补壳，条内重渲染不破坏壳。
  function colState() {
    try { return JSON.parse(localStorage.getItem('slirnCols') || '{}'); } catch (err) { return {}; }
  }
  function colSet(key, off) {
    if (!key) return;
    var s = colState();
    if (off) s[key] = 1; else delete s[key];
    try { localStorage.setItem('slirnCols', JSON.stringify(s)); } catch (err) {}
  }
  function colWrap(bar, key, title) {
    if (!bar || bar.dataset.slirnCol !== undefined) return;
    bar.dataset.slirnCol = '1';
    var off = !!colState()[key];
    var wrap = document.createElement('div');
    wrap.className = 'slirn-col' + (off ? ' off' : '');
    wrap.setAttribute('data-col-key', key);
    var head = document.createElement('div');
    head.className = 'slirn-col-head';
    head.innerHTML = '<button type="button" class="slirn-col-btn" data-action="col-toggle"><span class="slirn-col-chev">'
      + (off ? '▸' : '▾') + '</span>' + title + '</button>';
    bar.parentNode.insertBefore(wrap, bar);
    wrap.appendChild(head);
    wrap.appendChild(bar);
  }
  function colPaneKey(bar, suffix) {
    var pane = bar.closest ? bar.closest('.slirn-wb-pane') : null;
    return ((pane && pane.id ? pane.id.replace('slirn-wb-pane-', '') : 'x') + ':' + suffix);
  }
  function colEnhance(root) {
    var scope = root || document;
    // REQ-20260918-059：同一 pane 内所有 .slirn-form-hint 合并成单个折叠（不管相邻不相邻）
    // 旧实现只合并相邻 hint；被 opt-words / opt-list 等元素隔开时会各自成组，撑高页面。
    var paneMap = Object.create(null);
    Array.prototype.forEach.call(scope.querySelectorAll('.slirn-wb-pane .slirn-form-hint'), function(h) {
      if (h.dataset.slirnCol !== undefined) return;
      var pane = (h.closest && h.closest('.slirn-wb-pane')) || null;
      var pid = pane && pane.id ? pane.id : 'x';
      (paneMap[pid] = paneMap[pid] || []).push(h);
    });
    Object.keys(paneMap).forEach(function(pid) {
      var group = paneMap[pid];
      if (!group.length) return;
      var first = group[0];
      colWrap(first, colPaneKey(first, 'hint'), 'ℹ️ 说明');
      var wrap = first.parentNode;  // colWrap 已把 first 挪进 .slirn-col
      for (var i = 1; i < group.length; i++) {
        group[i].dataset.slirnCol = '1';
        wrap.appendChild(group[i]);
      }
    });
    Array.prototype.forEach.call(scope.querySelectorAll('.slirn-wb-pane .slirn-sub-meta'), function(m) {
      colWrap(m, colPaneKey(m, 'meta'), '📊 统计');
    });
    Array.prototype.forEach.call(scope.querySelectorAll('.slirn-wb-pane .slirn-rev-kbhint'), function(k) {
      colWrap(k, colPaneKey(k, 'kb'), '⌨ 快捷键');
    });
    var singles = [
      ['#slirn-rev-filter',       'rev:filter',  '🔎 筛选 / 跳转'],
      ['#slirn-rev-batch',        'rev:batch',   '🧮 批量修改'],
      ['#slirn-rev-search',       'rev:search',  '🔎 搜索定位'],
      ['#slirn-cut-spk-bar',      'cut:spk',     '👥 人员统计 / 查找'],
      ['#slirn-cut-batch',        'cut:batch',   '🧮 批量修改'],
      ['#slirn-cut-search',       'cut:search',  '🔎 搜索定位'],
      ['#slirn-opt-words',        'opt:words',   '🔤 词频列表'],
      ['.slirn-opt-word-filters', 'opt:wfilter', '🎚 词频筛选']
    ];
    singles.forEach(function(z) {
      Array.prototype.forEach.call(scope.querySelectorAll(z[0]), function(bar) {
        colWrap(bar, z[1], z[2]);
      });
    });
  }
  var colObs = new MutationObserver(function() {
    clearTimeout(colObs._t);
    colObs._t = setTimeout(function() { colEnhance(document); }, 60);  // 去抖：一次渲染多次 DOM 变更
  });
  colObs.observe(document.body, { childList: true, subtree: true });
  colEnhance(document);

  // ===== 视频浮层（REQ-20260918-043）=====
  // 无论哪个阶段：视频一开始播放就挪进可拖动的悬浮层 — 页面不再因插入
  // 播放器而整体位移，编辑信息不被顶开。✕ 只是收起（暂停），再次播放
  // 自动弹回；拖动位置记在本机。详情页/弹窗里的成片预览不搬（观看场景，
  // 不挤编辑区）。
  var vfLayer = null;
  function vfPlace(layer, x, y) {
    var w = layer.offsetWidth || 440;
    var minX = -(w - 76), maxX = window.innerWidth - 76;  // 标题条至少留 76px 可抓回
    var minY = 0, maxY = window.innerHeight - 44;
    layer.style.left = Math.round(Math.min(maxX, Math.max(minX, x))) + 'px';
    layer.style.top = Math.round(Math.min(maxY, Math.max(minY, y))) + 'px';
  }
  function vfPlaceSaved(layer) {
    var pos = null;
    try { pos = JSON.parse(localStorage.getItem('slirnVfPos') || 'null'); } catch (err) {}
    if (pos && typeof pos.l === 'number' && typeof pos.t === 'number') {
      vfPlace(layer, pos.l, pos.t);
    } else {
      vfPlace(layer, window.innerWidth - 464, 24);  // 默认右上角
    }
  }
  function vfDragBind(layer) {
    var bar = layer.querySelector('.slirn-vf-bar');
    if (!bar) return;
    bar.addEventListener('pointerdown', function(ev) {
      if (ev.target.closest('.slirn-vf-close')) return;  // 关闭按钮不触发拖动
      var r = layer.getBoundingClientRect();
      var ox = ev.clientX - r.left, oy = ev.clientY - r.top;
      var move = function(e2) { vfPlace(layer, e2.clientX - ox, e2.clientY - oy); };
      var up = function() {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        try {
          localStorage.setItem('slirnVfPos',
            JSON.stringify({ l: layer.offsetLeft, t: layer.offsetTop }));
        } catch (err) {}
      };
      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
      ev.preventDefault();
    });
    // REQ-20260919-061a v7 用户反馈：浮动视频窗口可调整大小 — 用 ResizeObserver
    // 监听用户拖右下角 resize handle 改变尺寸，存到 localStorage 下次恢复。
    if (typeof ResizeObserver !== 'undefined') {
      var ro = new ResizeObserver(function(entries) {
        for (var i = 0; i < entries.length; i++) {
          var cr = entries[i].contentRect;
          try {
            var saved = JSON.parse(localStorage.getItem('slirnVfSize') || 'null') || {};
            saved.w = Math.round(cr.width);
            saved.h = Math.round(cr.height);
            localStorage.setItem('slirnVfSize', JSON.stringify(saved));
          } catch (err) {}
        }
      });
      ro.observe(layer);
    }
  }
  function vfRestoreSize(layer) {
    try {
      var s = JSON.parse(localStorage.getItem('slirnVfSize') || 'null');
      if (s && s.w > 0 && s.h > 0) {
        layer.style.width = s.w + 'px';
        layer.style.height = s.h + 'px';
      }
    } catch (err) {}
  }
  function vfEnsureLayer() {
    if (vfLayer && vfLayer.isConnected) return vfLayer;
    vfLayer = document.createElement('div');
    vfLayer.id = 'slirn-video-float';
    vfLayer.className = 'slirn-video-float';
    vfLayer.hidden = true;
    vfLayer.innerHTML = '<div class="slirn-vf-bar">'
      + '<span class="slirn-vf-grip">⠿</span><span class="slirn-vf-title">▶ 视频预览（可拖动）</span>'
      + '<button type="button" class="slirn-vf-close" data-action="vf-close" '
      + 'title="收起浮层（视频暂停；再次播放自动弹回）">✕</button></div>'
      + '<div class="slirn-vf-body"></div>';
    document.body.appendChild(vfLayer);
    vfDragBind(vfLayer);
    return vfLayer;
  }
  function vfShow(wrap) {
    if (!wrap) return;
    var layer = vfEnsureLayer();
    var body = layer.querySelector('.slirn-vf-body');
    if (wrap.parentNode !== body) {
      // 浮层里只留当前播放器：面板重渲染后的旧节点直接清掉
      Array.prototype.forEach.call(body.children, function(el) { if (el !== wrap) el.remove(); });
      body.appendChild(wrap);
    }
    wrap.style.display = '';
    layer.hidden = false;
    vfRestoreSize(layer);  // 恢复上次保存的尺寸（用户拖右下角 resize 调整过的）
    vfPlaceSaved(layer);  // 记忆位置；隐藏期间视口可能变化 → 重新夹取进屏
  }
  // 兜底：不走 playXxx 助手的视频（如粗剪成片预览）一播放也进浮层
  document.addEventListener('play', function(ev) {
    var v = ev.target;
    if (!v || v.tagName !== 'VIDEO' || !v.closest) return;
    if (!v.closest('.slirn-wb-pane')) return;  // 仅工作台阶段；详情页/弹窗预览原地不动
    var wrap = v.closest('.slirn-video-wrap');
    if (!wrap) {  // 无包裹 → 就地包一层再搬，原位不留洞
      wrap = document.createElement('div');
      wrap.className = 'slirn-video-wrap';
      v.parentNode.insertBefore(wrap, v);
      wrap.appendChild(v);
      bindSpeedControl(v);
    }
    vfShow(wrap);
  }, true);

  // ===== 字幕播放器：定位播放 + 播放高亮跟随 =====
  function playSubAt(tid, startMs) {
    var wrap = document.getElementById('slirn-sub-player-wrap');
    var v = document.getElementById('slirn-sub-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) vfShow(wrap);
    if (wrap) wrap.style.display = '';
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var go = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) go();
    else v.addEventListener('loadedmetadata', go, {once: true});
  }

  // ===== 倍速显示与控制（REQ-20260916-014）=====
  // 背景：全部代码无任何 playbackRate 写入（grep 证实），神秘变速来自 Chrome 原生视频
  // 菜单（右键/⋮「播放速度」）等外部途径——页面上原本无任何倍速显示，所以"不知道为
  // 什么变快变慢"。这里把真实速率显示出来并可调大/调小/一键回 1x；ratechange 监听
  // 捕获任何来源的变速——速率一变必可见，不再是黑盒。
  var SLIRN_RATE_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 2.5, 3];
  var slirnRate = 1;  // 会话级倍速：播放器元素随面板重渲染重建（重建后浏览器重置 1x），重绑时重新应用
  function slirnRateLabel(r) { return parseFloat((+r || 1).toFixed(2)) + 'x'; }
  function bindSpeedControl(v) {
    if (!v) return;
    var wrap = (v.closest ? v.closest('.slirn-video-wrap') : null) || v.parentNode;
    if (!wrap || !wrap.querySelector) return;
    var ctl = wrap.querySelector('.slirn-speed');
    if (!ctl) {
      ctl = document.createElement('div');
      ctl.className = 'slirn-speed';
      ctl.innerHTML = '<span class="slirn-speed-cap">倍速</span>'
        + '<button type="button" class="slirn-btn slirn-btn-xs" data-rate-act="down" title="调慢（最低 0.5x）">−</button>'
        + '<button type="button" class="slirn-btn slirn-btn-xs slirn-speed-cur" data-rate-act="reset" title="点击恢复 1x">1x</button>'
        + '<button type="button" class="slirn-btn slirn-btn-xs" data-rate-act="up" title="调快（最高 3x）">＋</button>';
      wrap.appendChild(ctl);
      ctl.addEventListener('click', function(e) {
        var b = e.target.closest ? e.target.closest('[data-rate-act]') : null;
        if (!b) return;
        e.preventDefault();
        var r = +v.playbackRate || 1;
        var act = b.getAttribute('data-rate-act');
        if (act === 'reset') {
          r = 1;  // 调回原来的值 = 正常速度
        } else {
          // 阶梯调大/调小：先找当前速率最近的档位，再上下移动一格
          var near = 0;
          for (var i = 1; i < SLIRN_RATE_STEPS.length; i++) {
            if (Math.abs(SLIRN_RATE_STEPS[i] - r) < Math.abs(SLIRN_RATE_STEPS[near] - r)) near = i;
          }
          var nxt = Math.max(0, Math.min(SLIRN_RATE_STEPS.length - 1, near + (act === 'up' ? 1 : -1)));
          r = SLIRN_RATE_STEPS[nxt];
        }
        v.playbackRate = r;  // 触发 ratechange → 下方监听统一同步显示
      });
    }
    if (!ctl.dataset.ratebound) {
      ctl.dataset.ratebound = '1';
      // 任何来源的变速（本控件 / Chrome 原生菜单 / 扩展）→ 显示同步为真实速率
      v.addEventListener('ratechange', function() {
        slirnRate = +v.playbackRate || 1;
        var cur = ctl.querySelector('[data-rate-act="reset"]');
        if (cur) cur.textContent = slirnRateLabel(slirnRate);
      });
    }
    // 元素重渲染后浏览器重置为 1x → 重绑时把会话倍速应用回去（同值赋值不触发 ratechange，手动刷一次显示）
    try { v.playbackRate = slirnRate; } catch (err) {}
    var cur0 = ctl.querySelector('[data-rate-act="reset"]');
    if (cur0) cur0.textContent = slirnRateLabel(v.playbackRate);
  }

  // ===== SD 开关状态（REQ-20260917-029）：localStorage 记忆，重渲染/换视图后恢复 =====
  // 服务端默认渲染选中；详情页与工作台各一份同 id 复选框，apply 时同步全部实例
  function applySdSwitchState() {
    var saved = null;
    try { saved = localStorage.getItem('slirnSdSwitch'); } catch (e) {}
    if (saved === null) return;  // 用户没选过 → 保持服务端默认（开）
    var boxes = document.querySelectorAll('#slirn-sd-switch');
    for (var i = 0; i < boxes.length; i++) boxes[i].checked = saved === '1';
  }
  if (!window.__slirnSdBound) {
    window.__slirnSdBound = true;
    document.addEventListener('change', function(e) {
      var t = e.target;
      if (t && t.id === 'slirn-sd-switch') {
        try { localStorage.setItem('slirnSdSwitch', t.checked ? '1' : '0'); } catch (err) {}
      }
    });
  }

  function bindSubPlayer() {
    var v = document.getElementById('slirn-sub-player');
    var list = document.getElementById('slirn-sub-list');
    bindSpeedControl(v);  // 倍速显示与控制（REQ-20260916-014）
    if (v && list && !v.dataset.bound) {
      v.dataset.bound = '1';
      var rows = Array.prototype.slice.call(list.querySelectorAll('.slirn-sub-row'));
      var lastHit = -1;
      var setActive = function(idx) {
        for (var i = 0; i < rows.length; i++) rows[i].classList.toggle('active', i === idx);
        if (idx >= 0 && rows[idx] && rows[idx].scrollIntoView) rows[idx].scrollIntoView({block: 'nearest'});
      };
      v.addEventListener('timeupdate', function() {
        var tms = v.currentTime * 1000, hit = -1;
        for (var i = 0; i < rows.length; i++) {
          var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
          var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
          if (tms >= s0 && tms < e0) { hit = i; break; }
          if (s0 > tms) break;  // 行按时间有序，后面不可能命中
        }
        if (hit === -1 && lastHit >= 0) {
          // 两个字幕段之间的静音间隙：保持上一行高亮（校对视角更连续）
          var eh = parseInt(rows[lastHit].getAttribute('data-end-ms'), 10) || 0;
          var nh = (lastHit + 1 < rows.length)
            ? (parseInt(rows[lastHit + 1].getAttribute('data-start-ms'), 10) || 0)
            : Infinity;
          if (tms >= eh && tms < nh) hit = lastHit;
        }
        lastHit = hit;
        setActive(hit);
      });
    }
    // 详情（重新）打开时，若 job 还在跑 → 恢复轮询
    var st = document.getElementById('slirn-asr-status');
    if (st && st.dataset.taskId && st.dataset.state === 'running') startSubPolling(st.dataset.taskId);
    applySdSwitchState();  // SD 开关：重渲染后恢复上次选择（REQ-20260917-029）
  }

  // ===== 字幕修订：轮询 + 播放器（REQ-20260915-005，与字幕区同模式、独立 id）=====
  var revPollTimer = null;
  function startRevPolling(tid) {
    if (revPollTimer) { clearInterval(revPollTimer); revPollTimer = null; }
    var update = function() {
      postJSON(SLIRN_API + '/revise_status', {task_id: tid}).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        var el = document.getElementById('slirn-rev-status');
        if (j.state === 'running') {
          if (el) {
            el.style.display = '';
            el.dataset.state = 'running';
            el.innerHTML = '⏳ ' + escapeHtml(j.stage || '分析中') + ' · 已耗时 ' + fmtElapsed(j.elapsed_s || 0);
          }
        } else {
          if (revPollTimer) { clearInterval(revPollTimer); revPollTimer = null; }
          if (j.state === 'done') {
            toast('✅ 大模型分析完成：' + (j.entries_count || 0) + ' 条建议');
            openWorkbench(tid);  // 刷新面板（建议列表 + 阶段态）
          } else if (j.state === 'error') {
            if (el) {
              el.style.display = '';
              el.dataset.state = 'error';
              el.innerHTML = '❌ ' + escapeHtml(j.error || '分析失败');
            }
            toast('❌ 大模型分析失败', 'error');
          }
        }
      });
    };
    update();
    revPollTimer = setInterval(update, 2000);
  }

  // 详情页与工作台可能同时持有修订区 DOM（隐藏 tab 不清空 innerHTML）→ 一律取
  // 「可见的」那个元素，避免 id 撞车时操作到隐藏播放器/列表（REQ-20260916-004 顺带修复）
  function revVis(id) {
    var els = document.querySelectorAll('#' + id);
    for (var i = 0; i < els.length; i++) { if (els[i].offsetParent) return els[i]; }
    return els[0] || null;
  }

  function playRevAt(tid, startMs) {
    var wrap = revVis('slirn-rev-player-wrap');
    var v = revVis('slirn-rev-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) vfShow(wrap);
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var go = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) go();
    else v.addEventListener('loadedmetadata', go, {once: true});
  }

  // 修订行连续跳播（REQ-20260917-028）：字幕之间常有无声/停顿空白，逐条连播时
  // 空白也一并播出来。点行后从该行起链式播放：本条到尾 → 直接跳到下一条起点
  // （段间空白不播），播完最后一条自动停。链随 timeupdate 维护（见 bindRevPlayer），
  // 用户回拖/前拖按时间自动重定位。
  var revPlaySeq = null, revPlayIdx = 0;
  function revPlayFrom(row) {
    if (!row) return false;
    var seq = [], started = false;
    revRows().forEach(function(r) {  // 全量行（时间序）：链按字幕轨推进，与筛选无关
      if (r === row) started = true;
      if (!started) return;
      var s = parseInt(r.getAttribute('data-start-ms'), 10) || 0;
      var e = parseInt(r.getAttribute('data-end-ms'), 10) || 0;
      if (e > s) seq.push({ s: s, e: e });
    });
    revPlaySeq = seq.length ? seq : null;
    revPlayIdx = 0;
    playRevAt(row.getAttribute('data-task-id') || '',
              revPlaySeq ? revPlaySeq[0].s : (parseInt(row.getAttribute('data-start-ms'), 10) || 0));
    return true;
  }

  // 切分修剪行定位播放（REQ-20260916-008）— 与修订行同模式，独立播放器防 id 撞车
  function playCutAt(tid, startMs) {
    var wrap = revVis('slirn-cut-player-wrap');
    var v = revVis('slirn-cut-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) vfShow(wrap);
    if (!v.src) { v.src = SLIRN_API + '/video/' + encodeURIComponent(tid); v.load(); }
    var goCut = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) goCut();
    else v.addEventListener('loadedmetadata', goCut, {once: true});
  }

  // ===== 切分修剪预览与决策（REQ-20260916-011 → 013 连续播放）=====
  // 行点击/↑↓ = 从该行起点连续播放（REQ-20260916-013：播完一条自动接下一条，
  // 不再段尾自停），播放中 active 高亮跟随（与字幕/修订阶段同款）；
  // 组头 ▶ 试听 = 本组 keep 子段成片口径跳播。
  // 快捷键与字幕修订阶段同键位（可自定义）：↑↓ 切换 · 空格 播/停 · R 重播 ·
  // K/D/S 对选中行所属字幕整条改判（保留/删除/切分，再按同键取消）。
  // 翻转/改判未保存纯前端，「💾 保存切分决策」落盘。
  var cutKeepSeq = null; // 试听跳播序列 [{s,e,row},…]（null = 不跳播）
  var cutKeepIdx = 0;    // 试听当前段下标（索引跟踪：只前进不回扫 — REQ-20260916-012）
  var cutKeepMode = null; // 跳播来源：'group' 组头试听（显示成片试听条）| 'row' 行级连续播放（REQ-20260916-015）
  // 执行口径的行保留判定（与 cutlist_service.effective_keep_units 对齐 — REQ-20260916-015）：
  // 组级改判 delete → 整条剔除；切分组子段按 mark（组改判 keep → 子段划分作废、整段保留）；
  // 整段组默认保留（改判 split 未重切前维持整段）。试听/连续播放只播保留内容 = 成片效果。
  function cutRowKept(row) {
    var g = row.closest('.slirn-cut-group');
    var act = g ? (g.getAttribute('data-act') || '') : '';
    if (act === 'delete') return false;
    if (row.classList.contains('sub')) {
      if (act === 'keep') return true;
      return (row.getAttribute('data-mark') || 'keep') === 'keep';
    }
    return true;
  }
  // 从 startRow 起到列表末尾的全部保留区间（时间序）— 行级连续播放的跳播序列：
  // 删除洞（子段 mark=delete）与改判删除的整条直接跳过不播
  function cutKeepAllFrom(startRow) {
    var seq = [], started = false;
    cutRows().forEach(function(r) {
      if (r === startRow) started = true;
      if (!started || !cutRowKept(r)) return;
      var s = parseInt(r.getAttribute('data-start-ms'), 10) || 0;
      var e = parseInt(r.getAttribute('data-end-ms'), 10) || 0;
      if (e > s) seq.push({ s: s, e: e, row: r });
    });
    return seq;
  }
  function cutRows() {
    var list = revVis('slirn-cut-list');
    if (!list || !list.offsetParent) return [];  // 面板不可见 → 快捷键整体不生效
    return Array.prototype.slice.call(list.querySelectorAll('.slirn-cut-row'));
  }
  function cutSelIndex(rows) {
    for (var i = 0; i < rows.length; i++) { if (rows[i].classList.contains('kbsel')) return i; }
    return -1;
  }
  function cutMarkSel(row) {
    cutRows().forEach(function(r) { r.classList.toggle('kbsel', r === row); });
    if (row && row.scrollIntoView) row.scrollIntoView({block: 'nearest'});
  }
  function cutSelectRow(idx, seek) {
    var rows = cutRows();
    if (!rows.length) return null;
    var cur = cutSelIndex(rows);
    if (cur < 0) idx = (idx < 0) ? rows.length - 1 : 0;  // 无选中：↓ 第一行，↑ 最后一行
    idx = Math.max(0, Math.min(rows.length - 1, idx));
    var row = rows[idx];
    cutMarkSel(row);
    if (seek) cutPreview(row);
    return row;
  }
  // 行级连续播放（REQ-20260916-013 起，REQ-20260916-015 改为保留内容跳播）：从本行起
  // 按执行口径连续播保留区间 — 播完一条 seek 下一条，删除洞/改判删除整条直接跳过不播
  // （= 成片效果），active 高亮随 timeupdate 跟随切换。点击的行若本身是删除内容，
  // 从其后的下一个保留区间播起。
  function cutPreview(row) {
    if (!row) return;
    cutAuditionBar(null);
    var tid = row.getAttribute('data-task-id') || '';
    var seq = cutKeepAllFrom(row);
    if (!seq.length) {
      // 本行起再无保留内容：退化为定位普通播放（至少让用户听到点过的位置）
      cutKeepSeq = null; cutKeepMode = null;
      playCutAt(tid, parseInt(row.getAttribute('data-start-ms'), 10) || 0);
      return;
    }
    cutKeepSeq = seq;
    cutKeepIdx = 0;
    cutKeepMode = 'row';
    cutMarkSel(row);  // 选中停在点到的行（播放起点可能是其后的保留区间）— 键盘改判目标可预期
    playCutAt(tid, seq[0].s);
  }
  function cutReplayRow() {
    var rows = cutRows();
    var row = rows[cutSelIndex(rows)];
    if (!row) {
      var kmR = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmR.prev) + ' / ' + revKeyLabel(kmR.next) + ' 选择一个子段', 'error');
      return;
    }
    cutPreview(row);
  }
  function cutTogglePlay() {
    var v = revVis('slirn-cut-player');
    if (!v) return;
    if (!v.src) {  // 从未播放过：从选中行（或第一行）起点开播
      var rows = cutRows();
      cutPreview(rows[cutSelIndex(rows)] || rows[0]);
      return;
    }
    if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function() {}); }
    else {
      v.pause();
      if (cutKeepMode === 'group') { cutKeepSeq = null; cutAuditionBar(null); }  // 组试听：手动暂停即退出（REQ-20260916-012）
      // 行级连续播放：暂停保留跳播序列 — 恢复播放后继续跳过删除内容（REQ-20260916-015）
    }
  }
  // 组头 ▶ 试听：本组「执行口径」保留内容连续跳播 — 播完一段自动 seek 下一段
  // （跳变即真实剪辑效果：删掉洞后 keep 段首尾紧贴）。REQ-20260916-015 对齐
  // effective_keep_units：改判删除 → 成片没有这段；改判保留 → 整段一段（子段作废）；
  // 其余按子段 mark=keep。
  function cutPlayGroupKeep(g) {
    if (!g) return;
    var tid = g.getAttribute('data-task-id') || '';
    var act = g.getAttribute('data-act') || '';
    if (act === 'delete') {
      toast('本组已改判删除 — 成片中没有这段内容', 'error');
      return;
    }
    var seq = [];
    var subs = Array.prototype.slice.call(g.querySelectorAll('.slirn-cut-row.sub'));
    if (subs.length && act !== 'keep') {
      subs.forEach(function(r) {
        if ((r.getAttribute('data-mark') || 'keep') === 'keep') {
          var s = parseInt(r.getAttribute('data-start-ms'), 10) || 0;
          var e = parseInt(r.getAttribute('data-end-ms'), 10) || 0;
          if (e > s) seq.push({ s: s, e: e, row: r });
        }
      });
    } else {
      // 整段保留：改判 keep 的切分组（子段划分作废）或整段组 — 首行起点到末行终点一段
      var rowsG = Array.prototype.slice.call(g.querySelectorAll('.slirn-cut-row'));
      if (rowsG.length) {
        var s0 = parseInt(rowsG[0].getAttribute('data-start-ms'), 10) || 0;
        var e0 = parseInt(rowsG[rowsG.length - 1].getAttribute('data-end-ms'), 10) || 0;
        if (e0 > s0) seq.push({ s: s0, e: e0, row: rowsG[0] });
      }
    }
    if (!seq.length) { toast('本组没有保留子段（全部为删除洞）', 'error'); return; }
    cutKeepSeq = seq;
    cutKeepIdx = 0;  // 从第一段开播，一次到底（REQ-20260916-012）
    cutKeepMode = 'group';
    playCutAt(tid, seq[0].s);
    cutMarkSel(seq[0].row);
    var v = revVis('slirn-cut-player');
    if (v) {
      var p = v.play(); if (p && p.catch) p.catch(function() {});
      cutAuditionBar(v);  // 试听条：成片口径连续时间戳
    }
  }
  // 试听条（REQ-20260916-012）：成片时间戳 = 当前段之前全部 keep 段累计时长 + 段内偏移。
  // 跳播时视频原始时间戳会跳变；这条时间轴连续不回退，与当前试听进度严格一致。
  function cutFmtMS(ms) {
    var s = Math.floor(ms / 1000);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
    var two = function(n) { return (n < 10 ? '0' : '') + n; };
    return (h ? h + ':' + two(m) : m) + ':' + two(ss);
  }
  function cutAuditionBar(v) {
    var wrap = document.getElementById('slirn-cut-player-wrap');
    if (!wrap) return;
    var bar = wrap.querySelector('.slirn-cut-audition');
    if (!cutKeepSeq || !v) { if (bar) bar.hidden = true; return; }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'slirn-cut-audition';
      wrap.appendChild(bar);
    }
    var total = 0, before = 0, i;
    for (i = 0; i < cutKeepSeq.length; i++) total += Math.max(0, cutKeepSeq[i].e - cutKeepSeq[i].s);
    for (i = 0; i < cutKeepIdx; i++) before += Math.max(0, cutKeepSeq[i].e - cutKeepSeq[i].s);
    var seg = cutKeepSeq[cutKeepIdx];
    var cur = before + Math.max(0, Math.min(v.currentTime * 1000, seg.e) - seg.s);
    bar.hidden = false;
    bar.textContent = '🎧 试听（成片口径）' + cutFmtMS(cur) + ' / ' + cutFmtMS(total)
      + ' · 保留段 ' + (cutKeepIdx + 1) + '/' + cutKeepSeq.length
      + ' · 对应视频 ' + cutFmtMS(v.currentTime * 1000);
  }
  // 翻转子段标记（未保存纯前端；data-mark 与 data-mark-init 供「有手工修改」判断）
  function cutFlipMark(row) {
    var nw = (row.getAttribute('data-mark') || 'keep') === 'keep' ? 'delete' : 'keep';
    row.setAttribute('data-mark', nw);
    row.classList.toggle('mark-delete', nw === 'delete');
    var el = row.querySelector('.slirn-cut-mark');
    if (el) el.textContent = nw === 'keep' ? '✅ 保留' : '❌ 删除';
    toast(nw === 'keep' ? '已翻转为 ✅ 保留（未保存）' : '已翻转为 ❌ 删除（未保存）');
  }
  // ===== 字幕级改判 K/D/S（REQ-20260916-011 M3）：作用于选中行所属的字幕（组）=====
  // 空=维持原状 · delete=整条删 · keep=原切分不切了整段保留 · split=改为切分（展开编辑区）
  // REQ-20260917-027：核心抽 cutApplyAct，组头/整段行的决策下拉（鼠标）与键盘同效
  function cutActBadge(g, val) {
    var b = g.querySelector('[data-actsel]');
    if (b) b.value = val || '';  // ''=维持原状
  }
  function cutApplyAct(g, val) {
    cutCloseResplit();  // 任何改判动作先收起重切编辑区（改判 split 时再重开 — 防取消后残留）
    if (!val) {  // 维持原状 → 取消改判
      g.removeAttribute('data-act');
      cutActBadge(g, '');
      toast('已取消改判（维持原状）— 未保存');
      return;
    }
    g.setAttribute('data-act', val);
    cutActBadge(g, val);
    if (val === 'split') {
      cutOpenResplit(g);  // 切分需要内容：展开编辑区（预填原文/当前切分后文字）
      toast('✂️ 已改判切分 — 填写切分后内容后点「重新切分」（未保存）');
    } else {
      toast((val === 'keep' ? '✅ 已改判整条保留' : '❌ 已改判整条删除')
        + '（子段标记不再参与执行）— 未保存');
    }
  }
  function cutApplyDecision(val) {
    var rows = cutRows();
    var row = rows[cutSelIndex(rows)];
    if (!row) {
      var kmA = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmA.prev) + ' / ' + revKeyLabel(kmA.next) + ' 选择一条字幕，再按 '
        + revKeyLabel(kmA.keep) + ' / ' + revKeyLabel(kmA.del) + ' / ' + revKeyLabel(kmA.split) + ' 改判', 'error');
      return;
    }
    var g = row.closest('.slirn-cut-group');
    if (!g) return;
    if ((g.getAttribute('data-act') || '') === val) {  // 再按同键 → 取消，回到维持原状
      cutApplyAct(g, '');
      return;
    }
    cutApplyAct(g, val);
  }
  // ===== 单段重新切分（REQ-20260916-011 M3）：组头下行内展开编辑区 =====
  function cutOpenResplit(g) {
    if (!g) return;
    cutCloseResplit();
    var target = g.getAttribute('data-target') || g.getAttribute('data-orig-text') || '';
    var si = g.getAttribute('data-source-i') || '?';
    var box = document.createElement('div');
    box.className = 'slirn-cut-resplit';
    box.innerHTML =
      '<div class="slirn-cut-resplit-label">✂️ 重新切分第 ' + si + ' 条 — 修改「切分后内容」'
      + '（多写少写都行，对不上的字自动落进删除洞）：</div>'
      + '<input class="slirn-cut-resplit-input" type="text">'
      + '<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit-go">✂️ 按新内容重新切分</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-cut-act="resplit-cancel">取消</button>'
      + '<span class="slirn-cut-resplit-hint">重切会重置本段的手工翻转标记；切分后内容将回写修订决策（本条 → 切分）</span>';
    var head = g.querySelector('.slirn-cut-ghead') || g.querySelector('.slirn-cut-row');
    if (!head) return;
    head.parentNode.insertBefore(box, head.nextSibling);
    var inp = box.querySelector('.slirn-cut-resplit-input');
    inp.value = target.trim();
    inp.focus();
    try { inp.setSelectionRange(inp.value.length, inp.value.length); } catch (err) {}
  }
  function cutCloseResplit() {
    var b = document.querySelector('.slirn-cut-resplit');
    if (b && b.parentNode) b.parentNode.removeChild(b);
  }
  function cutDoResplit(g) {
    var inp = document.querySelector('.slirn-cut-resplit-input');
    if (!g || !inp) return;
    var val = inp.value.trim();
    if (!val) { toast('❌ 请填写切分后内容', 'error'); inp.focus(); return; }
    var tid = g.getAttribute('data-task-id') || '';
    postJSON(SLIRN_API + '/resplit_segment', {
      task_id: tid,
      source_i: parseInt(g.getAttribute('data-source-i'), 10),
      target_text: val,
    }).then(function(r) {
      if (r && r.ok) { toast(r.toast || '已按新内容重新切分'); openWorkbench(tid); }
      else if (r && r.error) toast('❌ ' + r.error, 'error');
    });
  }
  function cutHasManual() {  // 有手工修改？（翻转过 / 有字幕级改判）→ 重新执行前 confirm
    var flipped = cutRows().some(function(r) {
      return (r.getAttribute('data-mark') || '') !== (r.getAttribute('data-mark-init') || '');
    });
    var acted = !!document.querySelector('#slirn-cut-list .slirn-cut-group[data-act]');
    return flipped || acted;
  }
  // ===== 关联人员ID（REQ-20260917-031 / 033）：subtitle 段级 spk 按时间重叠对齐到切分行 =====
  // REQ-033：关联状态落盘 speaker_link.json（端点写）；面板重进时服务端直接渲染徽章 +
  // 统计条（容器 data-linked=1）。统计口径 = 仅计未删除记录（删除状态完全不计入，
  // 用于确认非主讲人员已清除）；删除改判走既有组 act / 子段 mark 口径，保存才落盘。
  function cutSpkRows() {
    return cutRows().filter(function(r) { return !!r.getAttribute('data-spk'); });
  }
  function cutSpkSetMark(row, mk) {  // 设置子段去留（幂等、不 toast — 批量改判用）
    row.setAttribute('data-mark', mk);
    row.classList.toggle('mark-delete', mk === 'delete');
    var el = row.querySelector('.slirn-cut-mark');
    if (el) el.textContent = (mk === 'keep' ? '✅ 保留' : '❌ 删除')
      + ((el.textContent || '').indexOf('✏️') >= 0 ? ' ✏️' : '');
  }
  function cutSpkStats() {  // 按 DOM 现算 → [{spk, count}]（spk 升序；count=未删除行数）
    var m = {};
    cutSpkRows().forEach(function(r) {
      var s = r.getAttribute('data-spk');
      if (!s) return;
      if (!(s in m)) m[s] = 0;  // 有行即入表 — 全删光也显示 0 条（清零反馈）
      if (cutRowKept(r)) m[s] += 1;  // 删除状态不计入统计（REQ-033）
    });
    return Object.keys(m).map(function(k) {
      return {spk: k, count: m[k]};
    }).sort(function(a, b) { return parseInt(a.spk, 10) - parseInt(b.spk, 10); });
  }
  function cutSpkBarRender() {  // 统计条（chips + 查找/导航/删除/重算），保留输入值
    var bar = document.getElementById('slirn-cut-spk-bar');
    if (!bar) return;
    var keepQ = '';
    var prevQ = bar.querySelector('#slirn-cut-spk-q');
    if (prevQ) keepQ = prevQ.value;
    var keepSkip = true;  // REQ-20260918-041：默认勾选「跳过已删除」
    var prevSkip = bar.querySelector('#slirn-cut-spk-skipdel');
    if (prevSkip) keepSkip = prevSkip.checked;
    var chips = cutSpkStats().map(function(c) {
      return '<span class="slirn-cut-spk-chip" data-action="cut-spk-chip" data-spk="' + c.spk + '"'
        + ' title="点击填入查找框（统计仅计未删除记录）">👤' + c.spk + ' · <b>' + c.count + '</b> 条</span>';
    }).join('');
    bar.innerHTML =
      '<div class="slirn-cut-spk-title">👥 人员统计（仅计未删除记录 · 关联已保存，重进任务自动显示）</div>'
      + '<div class="slirn-cut-spk-chips">' + chips + '</div>'
      + '<div class="slirn-cut-spk-find">按人员ID查找：'
      + '<input id="slirn-cut-spk-q" type="number" min="1" step="1" placeholder="如 2">'
      + '<label class="slirn-cut-spk-skiplbl" title="勾选后「上一条/下一条」只在未删除的记录间跳转">'
      + '<input id="slirn-cut-spk-skipdel" type="checkbox" checked>跳过已删除</label>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-prev">⬆️ 上一条</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-next">⬇️ 下一条</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-delete">❌ 删除该人员全部记录</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="cut-spk-recount">🧮 重新统计</button>'
      + '<span class="slirn-cut-spk-hint">统计只计未删除记录 — 删除非主讲人员后重算即可确认清零；删除改判需「💾 保存切分决策」落盘</span></div>';
    bar.style.display = '';
    bar.dataset.linked = '1';
    var q = bar.querySelector('#slirn-cut-spk-q');
    if (q) q.value = keepQ;
    var skipEl = bar.querySelector('#slirn-cut-spk-skipdel');
    if (skipEl) skipEl.checked = keepSkip;
  }
  // ===== 搜索定位（REQ-20260918-040）：内容片段输入 → 匹配行间循环定位 =====
  // 行上服务端渲染 data-text（显示文本）+ data-orig（切分整段行附原文，更正行搜原文可命中）；
  // 修订只在过滤后可见行中搜，切分全量行（无过滤）。
  function searchTextOf(row) {
    return ((row.getAttribute('data-text') || '') + '\n'
          + (row.getAttribute('data-orig') || '')).toLowerCase();
  }
  function searchQuery(boxId) {
    var q = ((document.getElementById(boxId) || {}).value || '').trim().toLowerCase();
    if (!q) { toast('❌ 请先输入要搜索的字幕内容', 'error'); return null; }
    return q;
  }
  function searchCountSync(boxId, rowsFn) {
    var cnt = document.getElementById(boxId.replace('-q', '-count'));
    if (!cnt) return;
    var q = ((document.getElementById(boxId) || {}).value || '').trim().toLowerCase();
    if (!q) { cnt.textContent = ''; return; }
    var n = rowsFn().filter(function(r) { return searchTextOf(r).indexOf(q) >= 0; }).length;
    cnt.textContent = n ? (n + ' 处') : '无匹配';
  }
  function searchNavTo(boxId, rowsFn, dir2, markSel) {
    var q = searchQuery(boxId);
    if (!q) return;
    var rows = rowsFn().filter(function(r) { return searchTextOf(r).indexOf(q) >= 0; });
    if (!rows.length) { toast('❌ 没有匹配「' + q + '」的字幕行'); return; }
    var cur = rows.filter(function(r) { return r.classList.contains('kbsel'); })[0];
    var idx = cur ? rows.indexOf(cur) : -1;
    var next = idx < 0 ? (dir2 > 0 ? 0 : rows.length - 1) : (idx + dir2 + rows.length) % rows.length;
    var row = rows[next];
    markSel(row);
    var idxTxt = (row.querySelector('.slirn-sub-idx') || {textContent: row.getAttribute('data-id') || ''}).textContent.trim();
    toast('🔎「' + q + '」第 ' + (next + 1) + '/' + rows.length + ' 条（序号 ' + idxTxt + '）');
    searchCountSync(boxId, rowsFn);
  }
  document.addEventListener('input', function(e) {
    if (!e.target) return;
    if (e.target.id === 'slirn-rev-search-q') searchCountSync('slirn-rev-search-q', revNavRows);
    else if (e.target.id === 'slirn-cut-search-q') searchCountSync('slirn-cut-search-q', cutRows);
  });
  document.addEventListener('keydown', function(e) {
    if (!e.target) return;
    if (e.target.id !== 'slirn-rev-search-q' && e.target.id !== 'slirn-cut-search-q') return;
    if (e.key === 'Enter') {
      if (e.target.id === 'slirn-rev-search-q')
        searchNavTo('slirn-rev-search-q', revNavRows, e.shiftKey ? -1 : 1, revMarkSel);
      else
        searchNavTo('slirn-cut-search-q', cutRows, e.shiftKey ? -1 : 1, cutMarkSel);
      e.preventDefault();
    }
  });
  function cutSpkLink(btn) {  // 👤 关联人员ID → POST cut_speaker_link → 注入徽章 + 统计条
    var tid = btn.getAttribute('data-task-id') || '';
    btn.disabled = true;
    btn.textContent = '⏳ 关联中…';
    postJSON(SLIRN_API + '/cut_speaker_link', {task_id: tid}).then(function(r) {
      btn.disabled = false;
      btn.textContent = '👤 关联人员ID';
      if (!(r && r.ok && r.link && r.link.available)) {
        toast('❌ ' + ((r && r.error) || '关联失败'), 'error');
        return;
      }
      var rowsMap = r.link.rows || {}, n = 0;
      cutRows().forEach(function(row) {
        var spk = rowsMap[row.getAttribute('data-id') || ''];
        if (spk) {
          row.setAttribute('data-spk', String(spk));
          var old = row.querySelector('.slirn-cut-spk');
          if (old) old.remove();
          var b = document.createElement('span');
          b.className = 'slirn-cut-spk';
          b.textContent = '👤' + spk;
          b.title = '人员 ' + spk + '（时间段重叠最大的字幕段说话人）';
          // REQ-20260917-036：徽章放序号之后、时间戳之前（同行不折行 — CSS 显式第2轨）
          var anchor = row.querySelector('.slirn-sub-idx');
          if (anchor && anchor.nextSibling) row.insertBefore(b, anchor.nextSibling);
          else row.appendChild(b);
          n += 1;
        } else {
          row.removeAttribute('data-spk');
          var ob = row.querySelector('.slirn-cut-spk');
          if (ob) ob.remove();
        }
      });
      cutSpkBarRender();
      btn.textContent = '🔄 重新关联人员ID';
      var barEl = document.getElementById('slirn-cut-spk-bar');
      if (barEl) barEl.dataset.linked = '1';  // 已关联标记（重新统计守卫用）
      toast('👥 已标注 ' + n + ' 行（' + (r.link.stats || []).length + ' 位人员）· 关联已保存，重进任务自动显示'
        + ' — 删除后记得「💾 保存切分决策」');
    }, function() {
      btn.disabled = false;
      btn.textContent = '👤 关联人员ID';
      toast('❌ 网络错误，请重试', 'error');
    });
  }
  function cutSpkQuery() {  // 查找框值（无统计条 → null 并提示）
    var q = document.getElementById('slirn-cut-spk-q');
    if (!q) { toast('先点「👤 关联人员ID」建立人员关联', 'error'); return null; }
    var v = (q.value || '').trim();
    if (!v) { toast('先输入人员ID（如 2，可点统计条快速填入）', 'error'); q.focus(); return null; }
    return v;
  }
  function cutSpkNav(dir) {  // 上一条/下一条：该人员行间循环跳转，kbsel 高亮 + 滚动定位
    var spk = cutSpkQuery();          // REQ-20260917-035：勾选「跳过已删除」→ 只在未删除记录间跳
    if (spk === null) return;
    var skipEl = document.getElementById('slirn-cut-spk-skipdel');
    var skipDel = !!(skipEl && skipEl.checked);
    var rows = cutSpkRows().filter(function(r) {
      return r.getAttribute('data-spk') === spk && (!skipDel || cutRowKept(r));
    });
    if (!rows.length) {
      var any = cutSpkRows().some(function(r) { return r.getAttribute('data-spk') === spk; });
      toast(any ? '👤' + spk + ' 的记录已全部删除 — 取消勾选「跳过已删除」可继续翻看'
                : '👤' + spk + ' 无匹配字幕记录', 'error');
      return;
    }
    var curRow = document.querySelector('#slirn-cut-list .slirn-cut-row.kbsel');
    var idx = rows.indexOf(curRow);  // 当前选中不在该人员行内 → 从头/尾起
    var next = idx < 0 ? (dir > 0 ? 0 : rows.length - 1)
                       : (idx + dir + rows.length) % rows.length;
    cutMarkSel(rows[next]);
    toast('👤' + spk + ' 第 ' + (next + 1) + '/' + rows.length + ' 条（编号 '
      + rows[next].getAttribute('data-id') + '）' + (skipDel ? ' · 已跳过删除' : ''));
  }
  function cutSpkDelete() {  // 删除该人员全部记录：整段 → 组 act=delete；子段 → mark=delete
    var spk = cutSpkQuery();
    if (spk === null) return;
    var rows = cutSpkRows().filter(function(r) { return r.getAttribute('data-spk') === spk; });
    if (!rows.length) { toast('👤' + spk + ' 无匹配字幕记录', 'error'); return; }
    if (!window.confirm('把人员 👤' + spk + ' 的 ' + rows.length + ' 条字幕记录全部改判删除？\n'
      + '（未保存 — 可逐条/逐组翻回；点「💾 保存切分决策」后落盘并影响成片）')) return;
    cutCloseResplit();
    var nWhole = 0, nSub = 0;
    rows.forEach(function(r) {
      var g = r.closest('.slirn-cut-group');
      if (!g) return;
      if (r.classList.contains('sub')) {
        if (g.getAttribute('data-act') === 'keep') {  // 整组保留改判会压住子段 mark → 先撤销
          g.removeAttribute('data-act');
          cutActBadge(g, '');
        }
        cutSpkSetMark(r, 'delete');
        nSub += 1;
      } else {
        g.setAttribute('data-act', 'delete');
        cutActBadge(g, 'delete');
        nWhole += 1;
      }
    });
    cutSpkBarRender();
    toast('❌ 👤' + spk + ' 已改判删除：整段 ' + nWhole + ' + 子段 ' + nSub
      + '（未保存 — 可翻回；其记录已不计入统计，点「🧮 重新统计」可确认清零）');
  }

  // ===== REQ-20260919-068：字幕修订阶段关联人员 ID =====
  // 模式与 cutSpk* 一致：行集合换为 rev rows；删除口径简化为 decision=delete。
  function revRowKept(row) {
    return (row.getAttribute('data-decision') || '') !== 'delete';
  }
  function revSpkRows() {  // 已关联的修订行（data-spk 不为空）
    return revRows().filter(function(r) { return !!r.getAttribute('data-spk'); });
  }
  function revSpkStats() {  // 按 DOM 现算 → [{spk, count}]（spk 升序；count=未删除行数）
    var m = {};
    revSpkRows().forEach(function(r) {
      var s = r.getAttribute('data-spk');
      if (!s) return;
      if (!(s in m)) m[s] = 0;
      if (revRowKept(r)) m[s] += 1;  // 删除决策不计入统计
    });
    return Object.keys(m).sort(function(a, b) { return parseInt(a, 10) - parseInt(b, 10); })
      .map(function(s) { return { spk: parseInt(s, 10), count: m[s] }; });
  }
  function revSpkBarRender() {  // 统计条（chips + 查找/导航/删除/重算），保留输入值
    var bar = document.getElementById('slirn-rev-spk-bar');
    if (!bar) return;
    var keepQ = '';
    var prevQ = bar.querySelector('#slirn-rev-spk-q');
    if (prevQ) keepQ = prevQ.value;
    var keepSkip = true;
    var prevSkip = bar.querySelector('#slirn-rev-spk-skipdel');
    if (prevSkip) keepSkip = prevSkip.checked;
    var chips = revSpkStats().map(function(c) {
      return '<span class="slirn-rev-spk-chip" data-action="rev-spk-chip" data-spk="' + c.spk + '"'
        + ' title="点击填入查找框（统计仅计未删除决策行）">👤' + c.spk + ' · <b>' + c.count + '</b> 条</span>';
    }).join('');
    bar.innerHTML =
      '<div class="slirn-rev-spk-title">👥 人员统计（仅计未删除决策行 · 关联已保存，重进任务自动显示）</div>'
      + '<div class="slirn-rev-spk-chips">' + chips + '</div>'
      + '<div class="slirn-rev-spk-find">按人员ID查找：'
      + '<input id="slirn-rev-spk-q" type="number" min="1" step="1" placeholder="如 2">'
      + '<label class="slirn-rev-spk-skiplbl" title="勾选后「上一条/下一条」只在未删除决策行间跳转">'
      + '<input id="slirn-rev-spk-skipdel" type="checkbox" checked>跳过已删除</label>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-prev">⬆️ 上一条</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-next">⬇️ 下一条</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-delete">❌ 删除该人员全部记录</button>'
      + '<button class="slirn-btn slirn-btn-xs" data-action="rev-spk-recount">🧮 重新统计</button>'
      + '<span class="slirn-rev-spk-hint">统计只计未删除决策行 — 删除非主讲人员后重算即可确认清零；删除改判需「💾 保存修订决策」落盘</span></div>';
    bar.style.display = '';
    bar.dataset.linked = '1';
    var q = bar.querySelector('#slirn-rev-spk-q');
    if (q) q.value = keepQ;
    var skipEl = bar.querySelector('#slirn-rev-spk-skipdel');
    if (skipEl) skipEl.checked = keepSkip;
  }
  function revSpkLink(btn) {  // 👤 关联人员ID → POST rev_speaker_link → 注入徽章 + 统计条
    var tid = btn.getAttribute('data-task-id') || '';
    btn.disabled = true;
    btn.textContent = '⏳ 关联中…';
    postJSON(SLIRN_API + '/rev_speaker_link', {task_id: tid}).then(function(r) {
      btn.disabled = false;
      btn.textContent = '👤 关联人员ID';
      if (!(r && r.ok && r.link && r.link.available)) {
        toast('❌ ' + ((r && r.error) || '关联失败'), 'error');
        return;
      }
      var rowsMap = r.link.rows || {}, n = 0;
      revRows().forEach(function(row) {
        var spk = rowsMap[row.getAttribute('data-i') || ''];
        if (spk) {
          row.setAttribute('data-spk', String(spk));
          var badge = row.querySelector('.slirn-rev-spk');
          if (badge) badge.textContent = '👤' + spk;
          n += 1;
        } else {
          row.removeAttribute('data-spk');
          var ob = row.querySelector('.slirn-rev-spk');
          if (ob) ob.textContent = '';
        }
      });
      revSpkBarRender();
      btn.textContent = '🔄 重新关联人员ID';
      var barEl = document.getElementById('slirn-rev-spk-bar');
      if (barEl) barEl.dataset.linked = '1';
      toast('👥 已标注 ' + n + ' 行（' + (r.link.stats || []).length + ' 位人员）· 关联已保存，重进任务自动显示'
        + ' — 删除后记得「💾 保存修订决策」');
    }, function() {
      btn.disabled = false;
      btn.textContent = '👤 关联人员ID';
      toast('❌ 网络错误，请重试', 'error');
    });
  }
  function revSpkQuery() {  // 查找框值
    var q = document.getElementById('slirn-rev-spk-q');
    if (!q) { toast('先点「👤 关联人员ID」建立人员关联', 'error'); return null; }
    var v = (q.value || '').trim();
    if (!v) { toast('先输入人员ID（如 2，可点统计条快速填入）', 'error'); q.focus(); return null; }
    return v;
  }
  function revSpkNav(dir) {  // 上一条/下一条：修订行间循环跳转，kbsel 高亮 + 滚动定位
    var spk = revSpkQuery();
    if (spk === null) return;
    var skipEl = document.getElementById('slirn-rev-spk-skipdel');
    var skipDel = !!(skipEl && skipEl.checked);
    var rows = revSpkRows().filter(function(r) {
      return r.getAttribute('data-spk') === spk && (!skipDel || revRowKept(r));
    });
    if (!rows.length) {
      var any = revSpkRows().some(function(r) { return r.getAttribute('data-spk') === spk; });
      toast(any ? '👤' + spk + ' 的记录已全部删除 — 取消勾选「跳过已删除」可继续翻看'
                : '👤' + spk + ' 无匹配字幕记录', 'error');
      return;
    }
    var curRow = revRows().filter(function(r) { return r.classList.contains('kbsel'); })[0];
    var idx = rows.indexOf(curRow);
    var next = idx < 0 ? (dir > 0 ? 0 : rows.length - 1)
                       : (idx + dir + rows.length) % rows.length;
    revMarkSel(rows[next]);
    toast('👤' + spk + ' 第 ' + (next + 1) + '/' + rows.length + ' 条（序号 '
      + (rows[next].getAttribute('data-i') || '?') + '）' + (skipDel ? ' · 已跳过删除' : ''));
  }
  function revSpkDelete() {  // 删除该人员全部记录：行 decision=delete
    var spk = revSpkQuery();
    if (spk === null) return;
    var rows = revSpkRows().filter(function(r) { return r.getAttribute('data-spk') === spk; });
    if (!rows.length) { toast('👤' + spk + ' 无匹配字幕记录', 'error'); return; }
    if (!window.confirm('把人员 👤' + spk + ' 的 ' + rows.length + ' 条字幕记录全部决策改为「删除」？\n'
      + '（未保存 — 可逐条翻回；点「💾 保存修订决策」后落盘并影响成片）')) return;
    var n = 0;
    rows.forEach(function(r) {
      var sel = r.querySelector('.slirn-rev-select');
      if (sel) { sel.value = 'delete'; n += 1; }
      // 触发 select 的 change 事件让 JS 同步 UI 状态（badge 等）
      if (sel) sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    revSpkBarRender();
    toast('❌ 👤' + spk + ' 已改判删除 ' + n + ' 条（未保存 — 可翻回；其记录已不计入统计，点「🧮 重新统计」可确认清零）');
  }
  // 粗剪合成（REQ-20260916-016，可选步骤）：启动后台拼接 → 轮询进度 → 完成注入预览
  function rcFmtDur(sec) {
    var s = Math.floor(sec || 0), m = Math.floor(s / 60), h = Math.floor(m / 60);
    var two = function(n) { return (n < 10 ? '0' : '') + n; };
    return h ? h + ':' + two(m % 60) + ':' + two(s % 60) : m + ':' + two(s % 60);
  }
  function rcInjectPreview(tid, result) {
    var pane = document.getElementById('slirn-wb-pane-rough_compose');
    if (!pane) return;
    var old = document.getElementById('slirn-rc-player');  // 可能已浮层化（REQ-20260918-043），全文档找
    if (old && old.closest('.slirn-video-wrap')) {  // 已有预览 → 换源刷新即可
      old.src = '/slirn/api/video/' + encodeURIComponent(tid) + '?src=rough_compose&t=' + Date.now();
      old.load();
      var meta = pane.querySelector('.slirn-sub-meta:last-of-type');
      if (meta && result) meta.textContent = '🎞️ 粗剪成片 · ' + result.size_mb + ' MB · ' + rcFmtDur(result.duration);
      return;
    }
    var card = pane.querySelector('.slirn-card');
    if (!card) return;
    var wrap = document.createElement('div');
    wrap.className = 'slirn-video-wrap';
    wrap.style.marginTop = '12px';
    wrap.innerHTML = '<video id="slirn-rc-player" controls preload="metadata" src="'
      + '/slirn/api/video/' + encodeURIComponent(tid) + '?src=rough_compose"></video>';
    card.appendChild(wrap);
    var meta = document.createElement('div');
    meta.className = 'slirn-sub-meta';
    meta.style.marginTop = '8px';
    meta.textContent = result ? ('🎞️ 粗剪成片 · ' + result.size_mb + ' MB · ' + rcFmtDur(result.duration)) : '🎞️ 粗剪成片已生成';
    card.appendChild(meta);
    bindSpeedControl(wrap.querySelector('video'));  // 倍速控件（REQ-20260916-014）
  }
  function rcCompose(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var status = document.getElementById('slirn-rc-status');
    var setBtn = function(txt, dis) { btn.textContent = txt; btn.disabled = !!dis; };
    var show = function(msg) { if (status) { status.style.display = ''; status.textContent = msg; } };
    setBtn('🎬 合成中…', true);
    show('⏳ 正在启动合成…');
    postJSON(SLIRN_API + '/compose_rough', {task_id: tid}).then(function(r) {
      if (!r || !r.ok) {
        setBtn('🎬 合成粗剪视频', false);
        show('');
        if (status) status.style.display = 'none';
        toast('❌ ' + (r && r.error ? r.error : '启动失败'), 'error');
        return;
      }
      toast(r.toast || '🎬 合成已启动');
      var timer = setInterval(function() {
        postJSON(SLIRN_API + '/compose_rough_status', {task_id: tid}).then(function(s) {
          if (!s || !s.ok || !s.job) return;
          var j = s.job;
          if (j.state === 'running') {
            var pct = Math.max(1, Math.min(99, Math.round(j.progress || 0)));
            setBtn('🎬 合成中… ' + pct + '%', true);
            show('⏳ 合成进行中 ' + pct + '% — 可继续其它操作，完成后此处自动显示预览');
            return;
          }
          clearInterval(timer);
          if (j.state === 'done') {
            setBtn('🎬 重新合成粗剪视频', false);
            show('✅ 粗剪成片已生成 — 下方预览整体效果');
            toast('✅ 粗剪成片已生成');
            rcInjectPreview(tid, j.result);
          } else {
            setBtn('🎬 重新合成粗剪视频', false);
            show('❌ ' + (j.error || '合成失败'));
            toast('❌ ' + (j.error || '合成失败'), 'error');
          }
        });
      }, 2000);
    });
  }
  // ===== 粗剪合成 · 删除 / 字幕 SRT 操作（REQ-20260916-019） =====
  function rcDelete(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    if (!tid) { toast('❌ 缺少 task_id', 'error'); return; }
    if (!window.confirm('删除「粗剪成片」（mp4 + 随片 srt 副产物）？\n'
        + '删除后需要重新合成才能预览效果（约 11 分钟）。')) return;
    btn.disabled = true;
    postJSON(SLIRN_API + '/compose_rough_delete', {task_id: tid}).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok) {
        toast('❌ ' + (r && r.error ? r.error : '删除失败'), 'error');
        return;
      }
      toast(r.toast || '🗑️ 已删除');
      openWorkbench(tid);  // 刷新工作台：删按钮消失、字幕预览保留（产物没了，按钮消失即可）
    });
  }
  function rcSubsCopy(btn) {
    var pane = btn.closest('.slirn-card');
    if (!pane) { toast('❌ 找不到字幕面板', 'error'); return; }
    var pre = pane.querySelector('pre.slirn-rc-subs-body');
    var srt = pre ? pre.textContent : '';
    if (!srt) { toast('❌ 没有可复制的字幕', 'error'); return; }
    var done = function() { toast('📋 已复制 ' + srt.split('\n\n').length + ' 段 SRT'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(srt).then(done, function() { fallbackCopy(srt, done); });
    } else {
      fallbackCopy(srt, done);
    }
  }
  function fallbackCopy(text, cb) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); cb && cb(); } catch (e) { toast('❌ 复制失败', 'error'); }
    document.body.removeChild(ta);
  }
  function rcSubsDownload(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    if (!tid) { toast('❌ 缺少 task_id', 'error'); return; }
    btn.disabled = true;
    postJSON(SLIRN_API + '/compose_rough_preview_subs', {task_id: tid}).then(function(r) {
      btn.disabled = false;
      if (!r || !r.ok || !r.srt) {
        toast('❌ ' + (r && r.error ? r.error : '获取 SRT 失败'), 'error');
        return;
      }
      var blob = new Blob([r.srt], {type: 'text/plain;charset=utf-8'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'rough_compose_subs_' + tid + '.srt';
      document.body.appendChild(a); a.click();
      setTimeout(function() { URL.revokeObjectURL(url); document.body.removeChild(a); }, 100);
      toast('⬇️ 已下载 ' + (r.lines || 0) + ' 行 SRT');
    });
  }
  // ===== 优化字幕（REQ-20260917-030）：轮询 + 词过滤 + 出现项采纳/编辑 + 保存 + SRT 下载 =====
  var optPollTimer = null;
  function startOptPolling(tid) {
    if (optPollTimer) { clearInterval(optPollTimer); optPollTimer = null; }
    var update = function() {
      postJSON(SLIRN_API + '/optimize_subtitle_status', {task_id: tid}).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        var el = revVis('slirn-opt-status');
        if (j.state === 'running') {
          if (el) {
            el.style.display = '';
            el.dataset.state = 'running';
            el.innerHTML = '⏳ ' + escapeHtml(j.stage || '处理中')
              + (j.progress ? ' · ' + Math.round(j.progress) + '%' : '')
              + ' · 已耗时 ' + fmtElapsed(j.elapsed_s || 0);
          }
        } else {
          if (optPollTimer) { clearInterval(optPollTimer); optPollTimer = null; }
          if (j.state === 'done') {
            toast('✅ 优化字幕完成：识别 ' + (j.lines || 0) + ' 行 · 不明确 '
              + (j.occurrences || 0) + ' 处');
            openWorkbench(tid);  // 刷新面板（词频 + 出现项列表 + 阶段态）
          } else if (j.state === 'error') {
            if (el) {
              el.style.display = '';
              el.dataset.state = 'error';
              el.innerHTML = '❌ ' + escapeHtml(j.error || '优化失败');
            }
            toast('❌ 优化字幕失败', 'error');
          }
        }
      });
    };
    update();
    optPollTimer = setInterval(update, 2000);
  }
  function optStart(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var payload = {task_id: tid};
    if (btn.getAttribute('data-has') === '1') {
      if (!window.confirm('重新优化将覆盖现有结果，并重置全部采纳决定。确定继续？')) return;
      payload.force = true;
    }
    postJSON(SLIRN_API + '/optimize_subtitle', payload).then(function(r) {
      if (r && r.ok) {
        toast(r.toast || '已开始优化');
        var el = revVis('slirn-opt-status');
        if (el) { el.dataset.state = 'running'; el.style.display = ''; el.innerHTML = '⏳ 已提交…'; }
        startOptPolling(tid);
      } else if (r && r.error) {
        toast('❌ ' + r.error, 'error');
      }
    });
  }
  // REQ-20260918-055：替换值输入框按 Enter → 自动标记为「已修正/已完成」
  // （采纳 + reviewed 标记 + 词行进度 +1 + 瞬时视觉反馈）
  function optOccEnterConfirm(inp, ev) {
    var occ = inp.closest('.slirn-opt-occ');
    if (!occ) return;
    var v = (inp.value || '').trim();
    if (!v) {
      // 替换值为空 → 阻止默认（避免吞掉换行/提交）+ toast 警告
      if (ev && ev.preventDefault) ev.preventDefault();
      toast('⚠️ 替换值为空，无法标记完成', 'warning');
      return;
    }
    // 阻止默认 Enter（避免在文本框里插入换行/触发表单提交）
    if (ev && ev.preventDefault) ev.preventDefault();
    // 1) 自动采纳（如果原本是 0）— 同点 ✓ 按钮效果
    if (occ.getAttribute('data-applied') !== '1') {
      occ.setAttribute('data-applied', '1');
      var tog = occ.querySelector('button.slirn-opt-toggle');
      if (tog) {
        tog.textContent = '✓';
        tog.title = '已采纳（保存时替换）';
      }
    }
    // 2) 标记 reviewed（幂等：optOccMarkReviewed 内部判 reviewed=1 直接 return）
    optOccMarkReviewed(occ);
    // 3) 瞬时视觉反馈（600ms 后移除）
    occ.classList.add('slirn-opt-occ-just-done');
    setTimeout(function() {
      if (occ && occ.classList) occ.classList.remove('slirn-opt-occ-just-done');
    }, 600);
    // 4) REQ-20260918-056：自动持久化（不刷面板，保留焦点）
    var inner = document.getElementById('slirn-tab-workbench-inner');
    var tid = inner ? (inner.getAttribute('data-task-id') || '') : '';
    if (tid) optAutoSave(tid);
  }
  function optOccToggle(btn) {  // 出现项采纳/不采纳（纯前端，保存时统一提交）
    var w = btn.closest('.slirn-opt-occ');
    if (!w) return;
    var now = w.getAttribute('data-applied') === '1' ? 0 : 1;
    w.setAttribute('data-applied', String(now));
    btn.textContent = now === 1 ? '✓' : '✕';
    btn.title = now === 1 ? '已采纳（保存时替换）' : '已不采纳（保留原文）';
    optOccMarkReviewed(w);  // REQ-038：明确处理过（无论采纳与否）→ 计入词进度
    // REQ-20260918-057A：与回车一致 — 切换后自动保存（含并发锁）
    var inner = document.getElementById('slirn-tab-workbench-inner');
    var tid = inner ? (inner.getAttribute('data-task-id') || '') : '';
    if (tid) optAutoSave(tid);
  }
  function optOccMarkReviewed(occEl) {  // REQ-038：occ 标记已处理 + 所属词行进度刷新
    if (!occEl || occEl.getAttribute('data-reviewed') === '1') return;
    occEl.setAttribute('data-reviewed', '1');
    optWordRowRefresh(occEl.getAttribute('data-word') || '');
  }
  function optWordRowRefresh(word) {  // 重算词行 done 状态（x/y + 徽章 + data-done）
    if (!word) return;
    var row = document.querySelector('#slirn-opt-words .slirn-opt-word[data-word="' + cssEscape(word) + '"]');
    if (!row) return;
    var occs = document.querySelectorAll('.slirn-opt-occ[data-word="' + cssEscape(word) + '"]');
    var total = occs.length, done = 0;
    occs.forEach(function(o) { if (o.getAttribute('data-reviewed') === '1') done += 1; });
    row.setAttribute('data-done', done >= total && total > 0 ? '1' : '0');
    var prog = row.querySelector('.slirn-opt-word-prog');
    if (prog) prog.textContent = done + '/' + total;
    var badge = row.querySelector('.slirn-opt-word-badge');
    if (badge) {
      var ok = done >= total && total > 0;
      badge.textContent = ok ? '✅ 已完成' : '⬜ 未完成';
      badge.title = ok ? '该词全部出现处都已明确处理（采纳或不采纳）'
                       : '还有 ' + (total - done) + ' 处未处理 — 逐处切换 ✓/✕ 或编辑替换值即计为已处理';
    }
  }
  function optWordFilter(chip) {  // 点词 → 列出含该词的所有出现行 + 各上下 5 行；再点取消
    var word = chip.getAttribute('data-word') || '';
    var list = revVis('slirn-opt-list');
    if (!list || !word) return;
    var CONTEXT_RADIUS = 5;  // 上下文半径：目标行前后各 5 行 = 共 11 行上下文
    document.querySelectorAll('#slirn-opt-words .slirn-opt-chip').forEach(function(c) {
      c.classList.remove('active');
    });
    // 复用：同一词再点 → 清空上下文
    if (list.getAttribute('data-filter-word') === word) {
      list.removeAttribute('data-filter-word');
      list.removeAttribute('data-filter-mode');
      list.querySelectorAll('.slirn-opt-row').forEach(function(row) {
        row.style.display = '';
        row.classList.remove('slirn-opt-row-target');
      });
      // 同步移除上下文模式 hint
      var hintOld = document.getElementById('slirn-opt-filter-hint');
      if (hintOld) hintOld.remove();
      return;
    }
    list.setAttribute('data-filter-word', word);
    list.setAttribute('data-filter-mode', 'context');
    chip.classList.add('active');

    // 一次遍历同时做两件事：
    // 1) 找出所有含目标词的行（target）及其 segment id
    // 2) 算出 target id ± CONTEXT_RADIUS 内的所有 id（去重 set）
    var targetIds = [];
    var contextIdSet = {};
    var firstHit = null;
    list.querySelectorAll('.slirn-opt-row').forEach(function(row) {
      var ws = (row.getAttribute('data-words') || '').split('\n');
      var hit = ws.indexOf(word) >= 0;
      if (hit) {
        var rid = parseInt(row.getAttribute('data-id') || '0', 10) || 0;
        targetIds.push(rid);
        for (var d = -CONTEXT_RADIUS; d <= CONTEXT_RADIUS; d++) {
          contextIdSet[rid + d] = 1;
        }
        if (!firstHit) firstHit = row;
      }
    });

    // 应用显示/隐藏 + 标记 target 行（高亮 + 视觉上看得见）
    list.querySelectorAll('.slirn-opt-row').forEach(function(row) {
      var rid = parseInt(row.getAttribute('data-id') || '0', 10) || 0;
      var isTarget = targetIds.indexOf(rid) >= 0;
      row.classList.toggle('slirn-opt-row-target', isTarget);
      row.style.display = contextIdSet[rid] ? '' : 'none';
    });

    // 顶部插一条 hint 让用户知道这是「上下文模式」+ 隐藏了多少行
    var hintId = 'slirn-opt-filter-hint';
    var oldHint = document.getElementById(hintId);
    if (oldHint) oldHint.remove();
    if (targetIds.length > 0) {
      var hint = document.createElement('div');
      hint.id = hintId;
      hint.className = 'slirn-form-hint slirn-opt-filter-hint';
      hint.innerHTML = '🔍 <b>' + escapeHtml(word) + '</b> 出现 <b>' + targetIds.length
        + '</b> 处，每处显示上下文 ±' + CONTEXT_RADIUS + ' 行（再点同一词可清除）';
      list.parentNode.insertBefore(hint, list);
    }

    // 滚到第一个出现处（保留原行为）
    if (firstHit) try { firstHit.scrollIntoView({block: 'center', behavior: 'smooth'}); } catch (err) {}
  }
  function optWordFilterBtn(btn) {  // REQ-038：词列表按处理状态过滤（全部/未完成/已完成）
    var mode = btn.getAttribute('data-mode') || 'all';
    // REQ-20260918-060：colEnhance 把 .slirn-opt-word-filters 和 #slirn-opt-words
    // 包成各自独立的 .slirn-col wrap（两个 wrap 是兄弟），导致旧实现
    // box = document.getElementById('slirn-opt-words') 找不到 filter 按钮、
    // 而 filter 按钮所在的 wrap 又找不到 .slirn-opt-word 行。
    // 修复：用最近的 opt-zone 容器（同时包含 filter 按钮 + 词行）。
    var scope = btn.closest && btn.closest('.slirn-card');
    if (!scope) scope = document.body;
    scope.querySelectorAll('button[data-action="opt-word-filter"]').forEach(function(b) {
      b.classList.toggle('active', b === btn);
    });
    scope.querySelectorAll('.slirn-opt-word').forEach(function(r) {
      var done = r.getAttribute('data-done') === '1';
      r.style.display = (mode === 'all' || (mode === 'done') === done) ? '' : 'none';
    });
    // REQ-20260918-050：过滤后重置到第 1 页 + 按可见项重新分页
    optWordsCurrentPage = 1;
    paginateOptWords();
    // REQ-20260918-057B：状态切换后联动文字过滤（AND 组合）
    optWordTextFilter();
  }
  // REQ-20260918-057B：词频文字过滤（与状态过滤 AND 组合 + 250ms 防抖）
  function optWordTextFilter() {
    // REQ-20260918-060：同 optWordFilterBtn — colEnhance 把 filters 和 words
    // 包成兄弟 wrap；用 .slirn-card 作为共同 scope 才能同时找到 input/clear/buttons/words。
    var inp = document.getElementById('slirn-opt-word-text');
    var clr = document.querySelector('.slirn-opt-word-text-clear');
    if (!inp) return;
    var scope = inp.closest && inp.closest('.slirn-card');
    if (!scope) scope = document.body;
    var q = (inp.value || '').trim().toLowerCase();
    if (clr) clr.style.display = q ? '' : 'none';
    // 当前激活的状态过滤 mode（同时尊重状态过滤）
    var modeBtn = scope.querySelector('button[data-action="opt-word-filter"].active');
    var mode = modeBtn ? (modeBtn.getAttribute('data-mode') || 'all') : 'all';
    scope.querySelectorAll('.slirn-opt-word').forEach(function(r) {
      var w = (r.getAttribute('data-word') || '').toLowerCase();
      var matchText = !q || w.indexOf(q) >= 0;
      var done = r.getAttribute('data-done') === '1';
      var matchMode = (mode === 'all' || (mode === 'done') === done);
      r.style.display = (matchText && matchMode) ? '' : 'none';
    });
    // REQ-20260918-050：过滤变化后重置页码 + 重新分页
    optWordsCurrentPage = 1;
    paginateOptWords();
  }
  function bindOptWordTextFilter() {  // 幂等 — wb 重渲后重绑 input 事件
    var inp = document.getElementById('slirn-opt-word-text');
    if (!inp || inp.dataset.bound) return;
    inp.dataset.bound = '1';
    var t = null;
    inp.addEventListener('input', function() {
      if (t) clearTimeout(t);
      t = setTimeout(optWordTextFilter, 250);
    });
  }
  // ========== REQ-20260919-061：精剪视频·四素材合成器 前端函数 ==========
  function fineUpload(btn, kind) {
    var fileInput = document.getElementById('slirn-fine-file-' + kind);
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
      toast('❌ 请先选择文件');
      return;
    }
    var file = fileInput.files[0];
    var inner = document.getElementById('slirn-tab-workbench-inner');
    var tid = inner ? (inner.getAttribute('data-task-id') || '') : '';
    if (!tid) { toast('❌ 缺少 task_id'); return; }
    var fd = new FormData();
    fd.append('task_id', tid);
    fd.append('kind', kind);
    fd.append('file', file);
    btn.disabled = true;
    var oldText = btn.textContent;
    btn.textContent = '📤 上传中...';
    fetch('/slirn/api/upload_fine_material_form', { method: 'POST', body: fd })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        btn.disabled = false;
        btn.textContent = oldText;
        if (j.ok) {
          // 更新 status + has-file 类
          var status = document.querySelector('[data-status-kind="' + kind + '"]');
          if (status) status.innerHTML = '✅ ' + (j.path || file.name).split(/[\\/]/).pop();
          var card = document.querySelector('.slirn-fine-upload-card[data-kind="' + kind + '"]');
          if (card) card.classList.add('has-file');
          toast('✅ ' + (j.toast || '已上传'));
          // REQ-20260920-079：超大图片上传时提示用户「合成时自动缩放」
          if (j.warning) {
            setTimeout(function() { toast(j.warning); }, 600);
          }
        } else {
          toast('❌ ' + (j.error || '上传失败'));
        }
      }).catch(function(e) {
        btn.disabled = false; btn.textContent = oldText;
        toast('❌ 网络错误: ' + e.message);
      });
  }
  function fineSetSaveStatus(text, state) {
    // REQ-20260919-061 用户反馈：保存成功后更新状态指示器。
    var el = document.getElementById('slirn-fine-save-status');
    if (!el) return;
    el.textContent = text;
    el.setAttribute('data-state', state || 'idle');
    if (state === 'saving') {
      el.classList.remove('err');
    } else if (state === 'saved') {
      el.classList.remove('err');
    } else if (state === 'error') {
      el.classList.add('err');
    }
  }
  function _fineFormatNow() {
    var d = new Date();
    var pad = function(n) { return n < 10 ? '0' + n : '' + n; };
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  function fineSaveAll(showToast, asTemplate) {
    // showToast=true 仅在用户主动点 💾 按钮时显示；自动保存静默更新状态指示器。
    // asTemplate=true 时（用户主动点保存按钮）：如果「模板名」为空 → window.prompt 弹窗要求填，
    //   拿到名后调 save_fine_global_profile 另存为全局模板。
    // asTemplate=false（自动保存）→ 不写模板，避免一堆「未命名」垃圾数据。
    // 收集所有滑块 + 复选框 + 字体 + 输出，一次保存（避免多次请求）
    var inner = document.getElementById('slirn-tab-workbench-inner');
    var tid = inner ? (inner.getAttribute('data-task-id') || '') : '';
    if (!tid) return;
    // 若要求存模板：先解析模板名（用户主动点保存时；asTemplate 包含默认 false）
    if (asTemplate) {
      var _nameEl = document.getElementById('slirn-fine-profile-name');
      var _name = _nameEl ? _nameEl.value.trim() : '';
      if (!_name) {
        // 弹窗要求填名（maxlength=30 与后端规则对齐）
        _name = window.prompt(
          '请填写模板名（≤30 字，会作为全局参数模板保存）',
          ''
        );
        if (_name === null) {
          // 用户取消 → 不存模板，但 fine_compose 还是要存
          toast('已跳过保存模板，只保存当前参数');
        } else {
          _name = String(_name).trim();
          if (!_name) { toast('❌ 模板名不能为空'); return; }
          if (_name.length > 30) { _name = _name.slice(0, 30); }
          if (_nameEl) _nameEl.value = _name;
        }
      }
      var _profileName = _name || '';
    }
    fineSetSaveStatus('保存中…', 'saving');
    var layout = {};
    document.querySelectorAll('.slirn-fine-slider').forEach(function(s) {
      var key = s.getAttribute('data-key') || '';
      var parts = key.split('.');
      if (parts.length !== 2) return;
      var k1 = parts[0], k2 = parts[1];
      layout[k1] = layout[k1] || {};
      layout[k1][k2] = parseFloat(s.value);
    });
    document.querySelectorAll('.slirn-fine-enabled, [data-key][type="checkbox"]').forEach(function(c) {
      var k = c.getAttribute('data-key') || '';
      if (!k || k.indexOf('.') < 0) return;
      var parts = k.split('.');
      var k1 = parts[0], k2 = parts[1];
      layout[k1] = layout[k1] || {};
      // REQ-20260919-062 v18：crop_aspect_lock 字段名不是 "enabled"，直接存原字段。
      // 其它 .slirn-fine-enabled 仍然写 .enabled。
      if (c.classList.contains('slirn-fine-enabled')) {
        layout[k1].enabled = c.checked;
      } else {
        layout[k1][k2] = c.checked;
      }
    });
    // 收集所有 3 个端点 — Promise.all 一起完成再更新状态
    var promises = [];
    promises.push(
      fetch('/slirn/api/save_fine_layout?task_id=' + encodeURIComponent(tid), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: tid, layout: layout })
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          // REQ-20260919-062 v5：后端在 viewport 限定下夹紧了 video 的 x/y/scale，
          // 把夹紧后的值同步回滑块显示（用户拖到边界外时滑块自动回退）
          if (j && j.layout && j.layout.video) {
            var v = j.layout.video;
            if (typeof v.x === 'number') _fineSyncSlider('slirn-fine-video-x', v.x);
            if (typeof v.y === 'number') _fineSyncSlider('slirn-fine-video-y', v.y);
            if (typeof v.scale === 'number') _fineSyncSlider('slirn-fine-video-scale', v.scale);
          }
          return j;
        })
    );
    // 字体
    var font = {};
    document.querySelectorAll('[data-font-key]').forEach(function(el) {
      var k = el.getAttribute('data-font-key');
      if (el.type === 'checkbox') font[k] = el.checked;
      else if (el.type === 'number') font[k] = parseInt(el.value, 10);
      else font[k] = el.value;
    });
    if (Object.keys(font).length > 0) {
      promises.push(
        fetch('/slirn/api/save_fine_font?task_id=' + encodeURIComponent(tid), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: tid, font: font })
        }).then(function(r) { return r.json(); })
      );
    }
    // 输出
    var output = {};
    document.querySelectorAll('[data-output-key]').forEach(function(el) {
      output[el.getAttribute('data-output-key')] = el.value;
    });
    if (Object.keys(output).length > 0) {
      promises.push(
        fetch('/slirn/api/save_fine_output?task_id=' + encodeURIComponent(tid), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: tid, output: output })
        }).then(function(r) { return r.json(); })
      );
    }
    // REQ-20260919-061 扩展：背景音乐（独立于 layout/font/output）
    var audio = {};
    document.querySelectorAll('[data-audio-key]').forEach(function(el) {
      var k = el.getAttribute('data-audio-key');
      audio[k] = parseFloat(el.value);
    });
    if (Object.keys(audio).length > 0) {
      promises.push(
        fetch('/slirn/api/save_fine_audio?task_id=' + encodeURIComponent(tid), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: tid, audio: audio })
        }).then(function(r) { return r.json(); })
      );
    }
    Promise.all(promises).then(function(results) {
      // REQ-20260919-061 用户反馈：「保存全部」一直报错的根因 — 后端用 `{"ok":true}` 而不是
      // `{"code":0}`，原版写 `r.code !== 0` 会让每个成功响应都被判失败。改成按 `ok` 字段判定，
      // 错误信息也从 `error` 字段取（与 `_err` 返回结构对齐）。
      var failed = results.find(function(r) { return !r || r.ok !== true; });
      if (failed) {
        var msg = failed.error || '未知错误';
        fineSetSaveStatus('保存失败：' + msg, 'error');
        if (showToast) toast('❌ 保存失败: ' + msg, 'error');
        return;
      }
      // 模板保存：用户主动保存且模板名非空时
      if (asTemplate && _profileName) {
        fetch('/slirn/api/save_fine_global_profile', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: tid, name: _profileName })
        })
          .then(function(r) { return r.json(); })
          .then(function(j) {
            if (j && j.ok) {
              fineSetSaveStatus('上次保存：' + _fineFormatNow() + '（模板「' + _profileName + '」）', 'saved');
              if (showToast) toast('✅ 已保存参数 + 模板「' + _profileName + '」');
            } else {
              // fine_compose 已存好；模板保存失败 → 不算整个失败，提示用户
              fineSetSaveStatus('上次保存：' + _fineFormatNow() + '（模板保存失败）', 'saved');
              if (showToast) toast('⚠️ 参数已保存，但模板保存失败: ' + ((j && j.error) || '未知错误'));
            }
          })
          .catch(function(e) {
            if (showToast) toast('⚠️ 参数已保存，但模板保存网络错误: ' + e.message);
          });
      } else {
        fineSetSaveStatus('上次保存：' + _fineFormatNow(), 'saved');
        if (showToast) toast('✅ 已保存全部参数');
      }
    }).catch(function(e) {
      fineSetSaveStatus('保存失败：网络错误', 'error');
      if (showToast) toast('❌ 保存失败: 网络错误', 'error');
    });
  }
  function bindFineControls() {  // wb 重渲后调用 — 绑所有 fine_cut 控件
    var t = null;
    function debounceSave() {
      if (t) clearTimeout(t);
      t = setTimeout(fineSaveAll, 300);
    }
    function updateCropAspect() {
      var wx = document.getElementById('slirn-fine-video-crop_w');
      var hx = document.getElementById('slirn-fine-video-crop_h');
      var aspectEl = document.getElementById('slirn-fine-crop-aspect-val');
      if (!aspectEl || !wx || !hx) return;
      var w = parseFloat(wx.value) || 0;
      var h = parseFloat(hx.value) || 0;
      aspectEl.textContent = w > 0 ? (h / w).toFixed(3) : '0.000';
    }
    // REQ-20260919-062 v7：实时计算视频显示尺寸（crop_w × scale, crop_h × scale）
    function updateVideoDisp() {
      var cw = document.getElementById('slirn-fine-video-crop_w');
      var ch = document.getElementById('slirn-fine-video-crop_h');
      var sc = document.getElementById('slirn-fine-video-scale');
      var dispW = document.getElementById('slirn-fine-video-disp-w');
      var dispH = document.getElementById('slirn-fine-video-disp-h');
      var scalePct = document.getElementById('slirn-fine-video-scale-pct');
      var aspect = document.getElementById('slirn-fine-video-aspect');
      var cropWPct = document.getElementById('slirn-fine-video-crop-w-pct');
      var cropHPct = document.getElementById('slirn-fine-video-crop-h-pct');
      if (!cw || !ch || !sc) return;
      var w = parseFloat(cw.value) || 0;
      var h = parseFloat(ch.value) || 0;
      var s = parseFloat(sc.value) || 0;
      var dw = Math.round(w * s);
      var dh = Math.round(h * s);
      if (dispW) dispW.textContent = String(dw);
      if (dispH) dispH.textContent = String(dh);
      if (scalePct) scalePct.textContent = (s * 100).toFixed(4);
      if (aspect) aspect.textContent = dw > 0 ? (dh / dw).toFixed(3) : '0.000';
      // REQ-20260919-062 v10：占背景图宽高百分比 = crop_w / 1920 × 100%,
      // crop_h / 1080 × 100%（背景图整个区域 = 设计空间 1920×1080）。
      if (cropWPct) cropWPct.textContent = (w / 1920 * 100).toFixed(2);
      if (cropHPct) cropHPct.textContent = (h / 1080 * 100).toFixed(2);
    }
    // REQ-20260919-062 v10：去掉了页面内的预览框（设计空间画布）— 不再需要
    // _fineApplyVideoLayout / _fineApplyVideoLayout ResizeObserver。预览只通过
    // 弹窗 .slirn-mat-preview-float（每个素材独立预览）进行。
    // REQ-20260919-062 v15：crop_w 变化时自动重算 scale = crop_w / 1920（保留 4 位小数）。
    // 共用公式：scale = crop_w / 1920；clamp 到 [0.1, 2.0]；toFixed(4) 取 4 位小数。
    // 不在这里 toast（避免每次拖滑块都弹提示）；保存走 bindFineControls 的 debounceSave。
    function _recomputeScaleFromCropW() {
      var cropWEl = document.getElementById('slirn-fine-video-crop_w');
      var scaleEl = document.getElementById('slirn-fine-video-scale');
      if (!cropWEl || !scaleEl) return;
      var cropW = parseInt(cropWEl.value, 10);
      if (!cropW || cropW <= 0) return;
      // scale = crop_w / 1920，保留 4 位小数
      var scale = Math.round((cropW / 1920) * 10000) / 10000;
      // clamp 到滑块范围
      var minS = parseFloat(scaleEl.min || '0.1');
      var maxS = parseFloat(scaleEl.max || '2.0');
      scale = Math.max(minS, Math.min(maxS, scale));
      // 同步 slider + number input（保留 4 位小数显示）
      var s = scale.toFixed(4);
      if (scaleEl.value !== s) {
        scaleEl.value = s;
        // 主动派发 input 事件，让 updateVideoDisp 立即刷新视频信息行
        scaleEl.dispatchEvent(new Event('input', { bubbles: true }));
      }
      var scaleNum = document.getElementById('slirn-fine-video-scale_num');
      if (scaleNum && scaleNum.value !== s) scaleNum.value = s;
    }
    document.querySelectorAll('.slirn-fine-slider, .slirn-fine-enabled, [data-font-key], [data-output-key]').forEach(function(el) {
      if (el.dataset.fineBound) return;
      el.dataset.fineBound = '1';
      var ev = (el.type === 'checkbox') ? 'change' : 'input';
      el.addEventListener(ev, function() {
        // REQ-20260919-061a：val 显示区已移除，输入框本身承担数值显示。
        if (el.classList.contains('slirn-fine-slider') || el.classList.contains('slirn-fine-font-slider')) {
          // REQ-20260919-061a v3 用户反馈：拖动滑块时对应 num 框数值要同步更新（之前
          // 只有 ▲▼ 路径同步了两边，slider input 路径漏写）。直接同步 value，不重派发
          // input 事件，避免无限递归；debounceSave 仍由本次事件触发。
          var num = document.getElementById(el.id + '_num');
          if (num && num.value !== el.value) num.value = el.value;
          // crop 矩形 w/h 变化时实时更新比例（仅 layout slider 才有 data-key）
          var k = el.getAttribute('data-key') || '';
          if (k === 'video.crop_w' || k === 'video.crop_h') updateCropAspect();
          // REQ-20260919-062 v7：video.crop_w/crop_h/scale 变化时刷新显示尺寸
          if (k === 'video.crop_w' || k === 'video.crop_h' || k === 'video.scale') {
            updateVideoDisp();
          }
          // REQ-20260919-062 v15：crop_w 变化时自动重算 scale（保留 4 位小数）。
          // scale 的 input 事件会被同 handler 处理 → 触发 updateVideoDisp + debounceSave，
          // 因此无需在这里单独调 fineSaveAll。
          if (k === 'video.crop_w') {
            _recomputeScaleFromCropW();
          }
        }
        debounceSave();
      });
    });
  }

  // ---------- REQ-20260919-061 扩展：全局参数模板 ----------
  function _fineTid() {
    var inner = document.getElementById('slirn-tab-workbench-inner');
    return inner ? (inner.getAttribute('data-task-id') || '') : '';
  }
  function bindFineSteppers() {
    // REQ-20260919-061a v7 用户反馈：num input 旁边的自定义 ▲▼ 按钮已移除（与浏览器
    // 原生 stepper 重复），所以这一步只剩 num 自身的双向同步逻辑：
    //   - number input（用浏览器原生 stepper 微调）→ 按 Enter 或失焦时 clamp + 写回
    //     slider，触发 bindFineControls 的 input 事件走 300ms 防抖自动保存
    function _readMinMaxStep(target) {
      var min = parseFloat(target.min);
      var max = parseFloat(target.max);
      var step = parseFloat(target.step) || 1;
      if (isNaN(min) || isNaN(max)) {
        // 兜底：若 min/max 没写（不应该发生），用 num 的 value 推断
        var cur = parseFloat(target.value) || 0;
        return { min: cur, max: cur, step: 1 };
      }
      return { min: min, max: max, step: step };
    }
    document.querySelectorAll('.slirn-fine-num[data-for]').forEach(function(num) {
      if (num.dataset.numBound) return;
      num.dataset.numBound = '1';
      // REQ-20260919-061a 用户反馈：直接修改输入框，按回车时才调整滑动条位置。
      // 键入过程中不修改 slider，避免 "1." 这种中间态被打断。
      function _commitNum() {
        var slider = document.getElementById(num.dataset.for);
        var src = slider || num;
        var b = _readMinMaxStep(src);
        var v = parseFloat(num.value);
        if (isNaN(v)) { num.value = src.value; return; }
        var clamped = Math.max(b.min, Math.min(b.max, v));
        num.value = String(clamped);
        if (slider && slider.value !== String(clamped)) {
          slider.value = String(clamped);
          // 触发 input 事件 → bindFineControls 走 300ms 防抖自动保存
          slider.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }
      num.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          _commitNum();
          num.blur();  // 收起光标，让用户看到 slider 已就位
        }
      });
      num.addEventListener('blur', _commitNum);
    });
  }
  function bindCropAspectLink() {
    // REQ-20260919-061a 用户反馈：crop_w 和 crop_h 按 16:9 联动（防变形）。
    // 默认开启；用户可关闭（关后 1:1 等预设才能任意设 w=h）。
    var toggle = document.getElementById('slirn-fine-crop-aspect-link');
    var wEl = document.getElementById('slirn-fine-video-crop_w');
    var hEl = document.getElementById('slirn-fine-video-crop_h');
    var wNum = document.getElementById('slirn-fine-video-crop_w_num');
    var hNum = document.getElementById('slirn-fine-video-crop_h_num');
    if (!toggle || !wEl || !hEl) return;
    if (toggle.dataset.linkBound) return;
    toggle.dataset.linkBound = '1';
    var _syncing = false;
    function _setLinked(otherEl, otherNum, val) {
      // val 由调用方算好；同步写 slider + num（让两边都更新）
      var s = String(val);
      if (otherEl.value !== s) otherEl.value = s;
      if (otherNum && otherNum.value !== s) otherNum.value = s;
    }
    function _syncHfromW() {
      var w = parseFloat(wEl.value) || 0;
      var newH = Math.round(w * 9 / 16);
      _setLinked(hEl, hNum, newH);
    }
    function _syncWfromH() {
      var h = parseFloat(hEl.value) || 0;
      var newW = Math.round(h * 16 / 9);
      _setLinked(wEl, wNum, newW);
    }
    function _propagate(srcEl) {
      // 让被联动的一方也触发 input 事件 → bindFineControls 走防抖自动保存
      // + updateCropAspect 刷新比例显示
      srcEl.dispatchEvent(new Event('input', { bubbles: true }));
    }
    function _wrap(handler) {
      return function() {
        if (_syncing || !toggle.checked) return;
        _syncing = true;
        try { handler(); } finally { _syncing = false; }
      };
    }
    // slider input：拖动 w → 同步 h；拖动 h → 同步 w
    wEl.addEventListener('input', _wrap(function() {
      _syncHfromW(); _propagate(hEl);
    }));
    hEl.addEventListener('input', _wrap(function() {
      _syncWfromH(); _propagate(wEl);
    }));
    // toggle 切换：开 → 立刻按当前 w 重算 h；关 → 不动
    toggle.addEventListener('change', function() {
      if (toggle.checked) {
        _syncing = true;
        try { _syncHfromW(); _propagate(hEl); }
        finally { _syncing = false; }
      }
      // REQ-20260919-062 v18：把勾选状态持久化到 layout.video.crop_aspect_lock
      // （fineSaveAll 会从 [data-key][type=checkbox] 自动收集所有复选框）
      // 用 setTimeout 0 跳出当前 input 事件栈，避免与其他 input 事件循环
      setTimeout(function() { if (typeof fineSaveAll === 'function') fineSaveAll(); }, 0);
    });
  }

  // REQ-20260919-062：背景图白色区域检测
  // - 算法下拉（pixel / ai）+ 阈值下拉 + 阈值手动输入（双向同步）
  // - 检测按钮 → 调 /slirn/api/detect_bg_white_area → 填充 4 角点 + 宽高 + 中心 + 像素数
  // - 「填充到视频位置和裁剪」按钮 → 把结果写回 video.X/Y + video.crop_x/y/w/h
  var _bgDetectLastResult = null;  // 上次检测结果，供 apply 复用
  function bindBgWhiteDetector() {
    var detectBtn = document.querySelector('[data-action="fine-bg-detect"]');
    var applyBtn = document.querySelector('[data-action="fine-bg-detect-apply"]');
    var algoSel = document.getElementById('slirn-fine-bg-detect-algo');
    var thSel = document.getElementById('slirn-fine-bg-detect-threshold-sel');
    var thNum = document.getElementById('slirn-fine-bg-detect-threshold-num');
    var thRow = document.getElementById('slirn-fine-bg-detect-threshold-row');
    if (!detectBtn) return;

    // 算法切换 → 阈值控件仅 pixel 显示；ai/ai_color 隐藏
    function _syncAlgoUi() {
      var algo = (algoSel && algoSel.value) || 'pixel';
      if (thRow) thRow.style.display = (algo === 'pixel') ? '' : 'none';
    }
    if (algoSel) {
      algoSel.addEventListener('change', _syncAlgoUi);
      _syncAlgoUi();
    }

    // 阈值下拉 ↔ 手动输入 双向同步
    if (thSel && thNum) {
      thSel.addEventListener('change', function() {
        thNum.value = thSel.value;
      });
      thNum.addEventListener('input', function() {
        var v = parseInt(thNum.value, 10);
        if (!isNaN(v) && v >= 200 && v <= 255) {
          // 找匹配选项，没有就不动下拉
          var opt = thSel.querySelector('option[value="' + v + '"]');
          if (opt) thSel.value = String(v);
        }
      });
    }

    detectBtn.addEventListener('click', function() {
      var tid = detectBtn.dataset.taskId || _fineTid();
      var algorithm = (algoSel && algoSel.value) || 'ai_color';
      var threshold = (thNum && parseInt(thNum.value, 10)) || 250;
      detectBtn.disabled = true;
      var origText = detectBtn.textContent;
      detectBtn.textContent = '⏳ 检测中…';
      fetch(SLIRN_API + '/detect_bg_white_area', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: tid, algorithm: algorithm, threshold: threshold }),
      })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        detectBtn.disabled = false;
        detectBtn.textContent = origText;
        if (!j || !j.ok) {
          toast('❌ 检测失败: ' + ((j && j.err) || '未知错误'), 'error');
          return;
        }
        _bgDetectLastResult = j;
        // 填充只读区
        var c = j.corners || {};
        var set = function(id, val) {
          var el = document.getElementById(id);
          if (el) el.textContent = val;
        };
        set('slirn-fine-bg-detect-tl',     '(' + (c.topleft     || [j.x, j.y]).join(', ') + ')');
        set('slirn-fine-bg-detect-tr',     '(' + (c.topright    || [j.x + j.width - 1, j.y]).join(', ') + ')');
        set('slirn-fine-bg-detect-bl',     '(' + (c.bottomleft  || [j.x, j.y + j.height - 1]).join(', ') + ')');
        set('slirn-fine-bg-detect-br',     '(' + (c.bottomright || [j.x + j.width - 1, j.y + j.height - 1]).join(', ') + ')');
        set('slirn-fine-bg-detect-wh',     j.width + ' × ' + j.height);
        set('slirn-fine-bg-detect-center', '(' + j.center_x + ', ' + j.center_y + ')');
        set('slirn-fine-bg-detect-pixels', (j.pixel_count || 0).toLocaleString());
        set('slirn-fine-bg-detect-native', j.image_native_w + ' × ' + j.image_native_h);
        // AI 颜色模式额外显示主色（带色块）
        var colorTextEl = document.getElementById('slirn-fine-bg-detect-color-text');
        var colorSwatchEl = document.getElementById('slirn-fine-bg-detect-color-swatch');
        if (j.detected_color && Array.isArray(j.detected_color) && j.detected_color.length === 3) {
          var rgb = j.detected_color;
          var hex = '#' + rgb.map(function(v) {
            var h = parseInt(v, 10).toString(16);
            return h.length === 1 ? '0' + h : h;
          }).join('');
          if (colorTextEl) colorTextEl.textContent = 'RGB(' + rgb.join(', ') + ') · ' + hex + ' (±' + (j.color_tolerance || 10) + ')';
          if (colorSwatchEl) {
            colorSwatchEl.style.backgroundColor = hex;
            colorSwatchEl.style.display = 'inline-block';
          }
        } else {
          if (colorTextEl) colorTextEl.textContent = '—（仅 pixel / ai_color 算法适用）';
          if (colorSwatchEl) colorSwatchEl.style.backgroundColor = 'transparent';
        }
        var resultEl = document.getElementById('slirn-fine-bg-detect-result');
        if (resultEl) resultEl.hidden = false;
        if (applyBtn) applyBtn.disabled = false;
        var algoLabel = algorithm === 'ai_color' ? '（AI 主色识别）' :
                        algorithm === 'ai'       ? '（AI bbox 识别）' : '（像素扫描）';
        toast('✅ 区域已检测 ' + algoLabel + ' — 左上 (' + j.x + ', ' + j.y + ')，宽 ' + j.width + '，高 ' + j.height, 'success');
      })
      .catch(function(err) {
        detectBtn.disabled = false;
        detectBtn.textContent = origText;
        toast('❌ 网络错误: ' + err, 'error');
      });
    });

    if (applyBtn) {
      applyBtn.addEventListener('click', function() {
        var r = _bgDetectLastResult;
        if (!r) {
          toast('⚠️ 请先点「🔍 检测区域」', 'warning');
          return;
        }
        // 逻辑：检测到的区域是用来展示视频的画布矩形。
        //   - 视频左上角 = 区域左上角 → video.X = r.x, video.Y = r.y
        //   - 区域宽高 = 从原视频截取的宽高 → video.crop_w = r.width, crop_h = r.height
        //   - 从原视频 (0,0) 起取这块矩形 → video.crop_x = 0, crop_y = 0
        //   - 缩放归 1.0，让裁剪后的视频刚好填满区域
        function _setSlider(sliderId, val) {
          var s = document.getElementById(sliderId);
          var n = document.getElementById(sliderId + '_num');
          if (s) {
            s.value = String(val);
            s.dispatchEvent(new Event('input', { bubbles: true }));
          }
          if (n) n.value = String(val);
        }
        _setSlider('slirn-fine-video-x', r.x);
        _setSlider('slirn-fine-video-y', r.y);
        _setSlider('slirn-fine-video-crop_x', 0);
        _setSlider('slirn-fine-video-crop_y', 0);
        _setSlider('slirn-fine-video-crop_w', r.width);
        _setSlider('slirn-fine-video-crop_h', r.height);
        _setSlider('slirn-fine-video-scale', 1.0);

        // REQ-20260919-062 v5：把 viewport 限定也保存到后端，
        // 这样用户后续拖滑块时 video 不会跑出检测区域。
        var applyTid = applyBtn.getAttribute('data-task-id') || '';
        if (applyTid) {
          fetch('/slirn/api/save_fine_layout?task_id=' + encodeURIComponent(applyTid), {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              task_id: applyTid,
              layout: { video: { viewport: { x: r.x, y: r.y, width: r.width, height: r.height } } },
            }),
          })
            .then(function(resp) { return resp.json(); })
            .then(function(j) {
              // 后端夹紧 x/y/scale 到 viewport（这里通常无变化；保险起见同步一次）
              if (j && j.layout && j.layout.video) {
                var v = j.layout.video;
                _fineSyncSlider('slirn-fine-video-x', v.x);
                _fineSyncSlider('slirn-fine-video-y', v.y);
                _fineSyncSlider('slirn-fine-video-scale', v.scale);
              }
            })
            .catch(function(err) { toast('⚠️ viewport 保存失败：' + err, 'warning'); });
        }
        toast('✅ 已填充：video 位置=(' + r.x + ', ' + r.y + ')，从原视频 (0,0) 截取 ' + r.width + '×' + r.height + '，缩放 1.00 填满区域，并已限定视频在该区域内', 'success');
      });
    }
  }
  function _fineEscapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // REQ-20260919-062 v5：把值同步到滑块上（不触发 input 事件，避免再触发自动保存死循环）
  function _fineSyncSlider(sliderId, val) {
    var s = document.getElementById(sliderId);
    var n = document.getElementById(sliderId + '_num');
    var cur = s ? parseFloat(s.value) : NaN;
    var target = parseFloat(val);
    if (s && !isNaN(target) && (isNaN(cur) || Math.abs(cur - target) >= 0.001)) {
      s.value = String(target);
    }
    if (n && !isNaN(target)) n.value = String(target);
  }

  // REQ-20260919-063 用户反馈：每个素材都要提供预览功能，并且可以缩放展示素材窗口的尺寸。
  // 实现：
  //   - 委托：[data-action="fine-mat-preview"] → fineMaterialPreview(tid, kind)
  //   - 弹出 .slirn-mat-preview-float 浮层（CSS resize: both；ResizeObserver 持久化尺寸）
  //   - 按 kind 渲染合适的内容：image → <img>；video → <video controls>；
  //     audio → <audio controls>；subtitle (.srt) → fetch 文本按行展示
  function bindFineMaterialPreviews() {
    // 绑定已委托给全局 action handler（见 router.js 主事件循环），
    // 此函数保留为 wb-setup 阶段的占位调用，便于将来扩展按需刷新。
  }

  var _matFloatDragOffset = null;  // 标题栏拖动用
  function fineMaterialPreview(tid, kind) {
    if (!tid || !kind) {
      toast('⚠️ 缺少任务 ID 或素材类型', 'warning');
      return;
    }
    // 已存在的浮层先关掉（同一 kind 再点 = 切内容；不同 kind = 关掉再开新的）
    var existing = document.getElementById('slirn-mat-preview-float');
    if (existing) existing.remove();

    var labels = {
      video: '🎬 粗剪视频',
      subtitle: '📝 字幕文件',
      cover: '🖼 封面图片',
      bg: '🎨 背景图片',
      reference: '🤖 参考位置关系图',
      audio: '🎵 背景音乐',
    };
    var label = labels[kind] || kind;
    var fileUrl = SLIRN_API + '/fine_material_file?task_id=' + encodeURIComponent(tid)
      + '&kind=' + encodeURIComponent(kind);

    // 容器（带 data-* 属性便于将来扩展）
    var flt = document.createElement('div');
    flt.id = 'slirn-mat-preview-float';
    flt.className = 'slirn-mat-preview-float';
    flt.setAttribute('data-kind', kind);
    flt.setAttribute('data-task-id', tid);
    // 默认位置：屏幕右侧偏下（不挡顶部状态栏）
    flt.style.top = '120px';
    flt.style.right = '40px';
    flt.style.left = 'auto';
    // 恢复之前保存的尺寸
    try {
      var saved = JSON.parse(localStorage.getItem('slirnMatPreviewSize') || 'null');
      if (saved && typeof saved.w === 'number' && typeof saved.h === 'number') {
        flt.style.width = saved.w + 'px';
        flt.style.height = saved.h + 'px';
      }
    } catch (err) { /* 静默忽略 */ }

    // 标题栏（可拖动 + 关闭）
    var header = document.createElement('div');
    header.className = 'slirn-mat-preview-header';
    var title = document.createElement('span');
    title.className = 'slirn-mat-preview-title';
    title.textContent = '预览 · ' + label;
    var closeBtn = document.createElement('button');
    closeBtn.className = 'slirn-mat-preview-close';
    closeBtn.setAttribute('aria-label', '关闭预览');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', function() {
      var f = document.getElementById('slirn-mat-preview-float');
      if (f) f.remove();
    });
    header.appendChild(title);
    header.appendChild(closeBtn);

    // 内容区
    var body = document.createElement('div');
    body.className = 'slirn-mat-preview-body';

    // 按 kind 渲染
    if (kind === 'subtitle') {
      // SRT 文本：fetch 拿原文，按行展示
      body.innerHTML = '<div class="slirn-mat-preview-error">加载中…</div>';
      fetch(fileUrl)
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.text();
        })
        .then(function(text) {
          body.innerHTML = '';
          var pre = document.createElement('pre');
          // 保留原始换行；前 500 行足够预览
          var lines = text.split(/\r?\n/);
          if (lines.length > 500) {
            lines = lines.slice(0, 500).concat(['...（共 ' + text.split(/\r?\n/).length + ' 行，已截断）']);
          }
          pre.textContent = lines.join('\n');
          body.appendChild(pre);
        })
        .catch(function(err) {
          body.innerHTML = '<div class="slirn-mat-preview-error">❌ 字幕加载失败: ' + _fineEscapeHtml(String(err)) + '</div>';
        });
    } else if (kind === 'video') {
      var v = document.createElement('video');
      v.src = fileUrl;
      v.controls = true;
      v.preload = 'metadata';
      body.appendChild(v);
    } else if (kind === 'audio') {
      var a = document.createElement('audio');
      a.src = fileUrl;
      a.controls = true;
      a.preload = 'metadata';
      body.appendChild(a);
    } else {
      // image 类型（cover/bg/reference）— 用 <img>，自带错误回退
      var img = document.createElement('img');
      img.src = fileUrl;
      img.alt = label;
      img.addEventListener('error', function() {
        body.innerHTML = '<div class="slirn-mat-preview-error">❌ 图片加载失败（文件可能已损坏或被占用）</div>';
      });
      body.appendChild(img);
    }

    flt.appendChild(header);
    flt.appendChild(body);
    document.body.appendChild(flt);

    // 拖动（标题栏 mousedown → mousemove 改 left/top）
    header.addEventListener('mousedown', function(ev) {
      // 仅主键；点击关闭按钮时不要触发拖动
      if (ev.button !== 0) return;
      if (ev.target === closeBtn) return;
      var rect = flt.getBoundingClientRect();
      _matFloatDragOffset = {
        dx: ev.clientX - rect.left,
        dy: ev.clientY - rect.top,
        // 解除 right: auto，改用 left/top 定位
        left: rect.left,
        top: rect.top,
      };
      flt.style.left = rect.left + 'px';
      flt.style.top = rect.top + 'px';
      flt.style.right = 'auto';
      ev.preventDefault();
    });
    document.addEventListener('mousemove', function(ev) {
      if (!_matFloatDragOffset) return;
      var nx = Math.max(0, ev.clientX - _matFloatDragOffset.dx);
      var ny = Math.max(0, ev.clientY - _matFloatDragOffset.dy);
      flt.style.left = nx + 'px';
      flt.style.top = ny + 'px';
    });
    document.addEventListener('mouseup', function() {
      _matFloatDragOffset = null;
    });

    // ResizeObserver：用户拖右下角调尺寸 → 持久化到 localStorage
    if (typeof ResizeObserver !== 'undefined') {
      var ro = new ResizeObserver(function(entries) {
        for (var i = 0; i < entries.length; i++) {
          var cr = entries[i].contentRect;
          try {
            localStorage.setItem('slirnMatPreviewSize', JSON.stringify({
              w: Math.round(cr.width),
              h: Math.round(cr.height),
            }));
          } catch (err) { /* 静默 */ }
        }
      });
      ro.observe(flt);
    }
  }

  // REQ-20260919-062 v11：生成预览/导出完成后，自动弹出合成预览窗口。
  // 复用 .slirn-mat-preview-float 样式（位置 + resize 行为一致），但 body
  // 直接放 <video src={url}>（不需要再 fetch /slirn/api/fine_material_file）。
  // v19 用户反馈：合成预览窗口无法拖动 + 拖动后位置不持久化。
  //   根因：openFinePreviewFloat 只绑了 mousedown，没绑 mousemove/mouseup，导致
  //   鼠标移动时浮窗纹丝不动。同时复用单例 _matFloatDragOffset 还会被
  //   fineMaterialPreview 的 mousemove handler 错位更新（闭包里的 flt 已 remove）。
  //   修复：把 _dragOffset 提到本函数闭包里（每个浮窗独立），完整 mousedown/move/up
  //   三件套，并在 mouseup 时把 left/top 持久化到 localStorage 的 slirnMatPreviewPos。
  function openFinePreviewFloat(url, title) {
    if (!url) return;
    var labels_title = title || '🎬 合成预览';
    // 已存在的浮层先关掉（避免叠加）
    var existing = document.getElementById('slirn-fine-preview-float');
    if (existing) existing.remove();
    var flt = document.createElement('div');
    flt.id = 'slirn-fine-preview-float';
    flt.className = 'slirn-mat-preview-float slirn-fine-preview-float';
    // 默认位置 + 尺寸（与单素材预览一致）
    flt.style.top = '120px';
    flt.style.right = '40px';
    flt.style.left = 'auto';
    try {
      var savedSize = JSON.parse(localStorage.getItem('slirnMatPreviewSize') || 'null');
      if (savedSize && savedSize.w && savedSize.h) {
        flt.style.width = savedSize.w + 'px';
        flt.style.height = savedSize.h + 'px';
      }
      // v19：恢复上次拖到的位置（left/top）
      var savedPos = JSON.parse(localStorage.getItem('slirnMatPreviewPos') || 'null');
      if (savedPos && typeof savedPos.left === 'number' && typeof savedPos.top === 'number') {
        flt.style.left = savedPos.left + 'px';
        flt.style.top = savedPos.top + 'px';
        flt.style.right = 'auto';
      }
    } catch (err) { /* 静默 */ }
    // 标题栏
    var header = document.createElement('div');
    header.className = 'slirn-mat-preview-header';
    var titleEl = document.createElement('span');
    titleEl.className = 'slirn-mat-preview-title';
    titleEl.textContent = labels_title;
    var closeBtn = document.createElement('button');
    closeBtn.className = 'slirn-mat-preview-close';
    closeBtn.setAttribute('aria-label', '关闭预览');
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', function() {
      var f = document.getElementById('slirn-fine-preview-float');
      if (f) f.remove();
    });
    header.appendChild(titleEl);
    header.appendChild(closeBtn);
    // body：放一个 <video controls autoplay src={url}>；video src 需要绝对 URL
    var body = document.createElement('div');
    body.className = 'slirn-mat-preview-body';
    var v = document.createElement('video');
    v.src = url;
    v.controls = true;
    v.autoplay = true;
    v.preload = 'metadata';
    v.style.width = '100%';
    v.style.height = '100%';
    body.appendChild(v);
    flt.appendChild(header);
    flt.appendChild(body);
    document.body.appendChild(flt);
    // v19：完整拖动三件套（mousedown / mousemove / mouseup）。
    // _dragOffset 放在闭包里，避免复用模块级 _matFloatDragOffset 被其它浮窗覆盖。
    var _dragOffset = null;
    header.addEventListener('mousedown', function(ev) {
      if (ev.button !== 0) return;
      if (ev.target === closeBtn) return;
      var rect = flt.getBoundingClientRect();
      _dragOffset = {
        dx: ev.clientX - rect.left,
        dy: ev.clientY - rect.top,
      };
      flt.style.left = rect.left + 'px';
      flt.style.top = rect.top + 'px';
      flt.style.right = 'auto';
      ev.preventDefault();
    });
    document.addEventListener('mousemove', function(ev) {
      if (!_dragOffset) return;
      var nx = Math.max(0, ev.clientX - _dragOffset.dx);
      var ny = Math.max(0, ev.clientY - _dragOffset.dy);
      flt.style.left = nx + 'px';
      flt.style.top = ny + 'px';
    });
    document.addEventListener('mouseup', function() {
      if (!_dragOffset) return;
      _dragOffset = null;
      // 持久化位置（mouseup 时记录当前 flt 的 left/top）
      try {
        var rect = flt.getBoundingClientRect();
        localStorage.setItem('slirnMatPreviewPos', JSON.stringify({
          left: Math.round(rect.left),
          top: Math.round(rect.top),
        }));
      } catch (err) { /* 静默 */ }
    });
    // 尺寸持久化（复用 slirnMatPreviewSize key）
    if (typeof ResizeObserver !== 'undefined') {
      var ro = new ResizeObserver(function(entries) {
        for (var i = 0; i < entries.length; i++) {
          var cr = entries[i].contentRect;
          try {
            localStorage.setItem('slirnMatPreviewSize', JSON.stringify({
              w: Math.round(cr.width),
              h: Math.round(cr.height),
            }));
          } catch (err) { /* 静默 */ }
        }
      });
      ro.observe(flt);
    }
  }

  // REQ-20260920-090：合成元素组合测试面板（debug）
  // 5 checkbox + 一键测试 + 诊断 BGM 路径
  function comboTestAction(action, target) {
    var tid = target.getAttribute('data-task-id');
    if (!tid) { toast('❌ 缺少 task_id'); return; }
    var outputEl = document.getElementById('slirn-combo-test-output-' + tid);
    var detailsEl = document.getElementById('slirn-combo-test-details');
    if (!detailsEl) return;
    var checkboxes = detailsEl.querySelectorAll('input[type="checkbox"][data-combo-kind]');
    function _readComboState() {
      var state = {};
      checkboxes.forEach(function(cb) {
        state[cb.getAttribute('data-combo-kind')] = cb.checked;
      });
      return state;
    }
    // REQ-20260920-091：读开始时间（时:分:秒）+ 时长（秒）；返回 preview_start / duration
    function _readTimeParams() {
      var h = parseInt(document.getElementById('slirn-combo-test-start-h').value, 10) || 0;
      var m = parseInt(document.getElementById('slirn-combo-test-start-m').value, 10) || 0;
      var s = parseInt(document.getElementById('slirn-combo-test-start-s').value, 10) || 0;
      var preview_start = Math.max(0, h * 3600 + m * 60 + s);
      var durRaw = parseFloat(document.getElementById('slirn-combo-test-duration').value);
      // 时长钳到 [2, 86400]；缺省或非法值 → null（= 完整视频）
      var duration = (isFinite(durRaw) && durRaw >= 2) ? Math.min(durRaw, 86400) : null;
      return { preview_start: preview_start, duration: duration };
    }
    // REQ-20260920-091：根据 time 参数算 output_path（与后端命名规则一致）
    function _computeOutputPath(tp) {
      if (tp.preview_start === 0 && tp.duration === null) {
        return 'outputs/fine_export.mp4';
      }
      var dTag = tp.duration != null ? tp.duration.toFixed(1) : 'full';
      return 'outputs/fine_export_t' + tp.preview_start.toFixed(1) + '_d' + dTag + '.mp4';
    }
    function _appendOutput(html) {
      if (outputEl) {
        outputEl.style.display = 'block';
        outputEl.innerHTML = html;
      }
    }
    function _snapshot() {
      // 保存到全局 state，刷新后失效
      window._comboSnapshot = window._comboSnapshot || {};
      window._comboSnapshot[tid] = _readComboState();
      var restoreBtn = detailsEl.querySelector('[data-action="combo-restore"]');
      if (restoreBtn) restoreBtn.hidden = false;
    }
    function _setBusy(busy) {
      detailsEl.querySelectorAll('button[data-action^="combo-"]').forEach(function(b) {
        if (b.getAttribute('data-action') !== 'combo-restore') b.disabled = busy;
      });
    }

    if (action === 'combo-diagnose') {
      _appendOutput('🔍 正在诊断...');
      fetch('/slirn/api/diagnose_bgm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: tid })
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          if (!j.ok) { _appendOutput('<span class="err">❌ ' + (j.error || '诊断失败') + '</span>'); return; }
          var html = '[预测诊断]\n';
          html += '  inputs_count = ' + j.inputs_count + '\n';
          html += '  audio_idx = ' + j.audio_idx + '\n';
          html += '  predicted_has_bgm = ' + (j.predicted_has_bgm ? '<span class="ok">✅ true</span>' : '<span class="warn">⚠ false</span>') + '\n';
          if (j.why_no_bgm) html += '  why_no_bgm: ' + j.why_no_bgm + '\n';
          html += '\n[元素状态]\n';
          var kinds = ['video', 'subtitle', 'cover', 'bg', 'audio'];
          kinds.forEach(function(k) {
            var e = j.elements[k];
            var mark = e.will_render ? '<span class="ok">✅</span>' : '<span class="warn">⚠</span>';
            html += '  ' + mark + ' ' + k + ': layout=' + e.layout_enabled + ', material=' + (e.material_path ? '✓' : '✗') + ', will_render=' + e.will_render + '\n';
          });
          if (j.predicted_audio_filters) {
            html += '\n[预测 audio filter]\n  ' + j.predicted_audio_filters.replace(/;/g, ';\n  ') + '\n';
          } else {
            html += '\n[预测 audio filter]\n  <span class="warn">⚠ 无（未启用 BGM 或素材缺失）</span>\n';
          }
          _appendOutput(html);
        })
        .catch(function(e) { _appendOutput('<span class="err">❌ 网络错误: ' + e.message + '</span>'); });
      return;
    }

    if (action === 'combo-restore') {
      var snap = (window._comboSnapshot || {})[tid];
      if (!snap) { toast('⚠ 没有可还原的快照'); return; }
      // 把 snapshot 写回 checkboxes → 调 combo-apply 写 fc
      checkboxes.forEach(function(cb) {
        var k = cb.getAttribute('data-combo-kind');
        if (k in snap) cb.checked = snap[k];
      });
      _applyComboToFC(tid, _readComboState()).then(function() {
        toast('↩️ 已还原 snapshot');
        var restoreBtn = detailsEl.querySelector('[data-action="combo-restore"]');
        if (restoreBtn) restoreBtn.hidden = true;
        window._comboSnapshot[tid] = null;
      }).catch(function(e) { toast('❌ 还原失败: ' + e.message); });
      return;
    }

    // combo-apply / combo-test 共用：先 snapshot 原状态，再 apply 当前勾选
    _snapshot();
    var state = _readComboState();
    if (action === 'combo-test') {
      _setBusy(true);
      var tp = _readTimeParams();
      var outPath = _computeOutputPath(tp);
      _appendOutput('🚀 正在应用勾选并启动合成（start=' + tp.preview_start + 's, dur=' + (tp.duration || 'full') + 's）...');
      _applyComboToFC(tid, state).then(function() {
        // REQ-20260920-091：combo-test 传 time 参数给 export_fine_video（不导完整视频）
        var exportBody = { task_id: tid, preview_start: tp.preview_start };
        if (tp.duration !== null) exportBody.duration = tp.duration;
        return fetch('/slirn/api/export_fine_video', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(exportBody)
        });
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          if (!j.ok || !j.job_id) { _appendOutput('<span class="err">❌ 启动失败: ' + (j.error || '未知错误') + '</span>'); _setBusy(false); return; }
          var jobId = j.job_id;
          _appendOutput('🚀 已启动 job_id=' + jobId + '\n⏳ 轮询渲染状态...');
          var pollCount = 0;
          function poll() {
            pollCount++;
            return fetch('/slirn/api/render_status?job_id=' + jobId).then(function(r) { return r.json(); })
              .then(function(s) {
                if (s.state === 'running') {
                  _appendOutput('🚀 job_id=' + jobId + ' | progress=' + (s.progress_pct || 0) + '% | 已轮询 ' + pollCount + ' 次');
                  if (pollCount < 600) setTimeout(poll, 1500);
                  else { _appendOutput('<span class="warn">⚠ 轮询超时（15 分钟）</span>'); _setBusy(false); }
                  return null;
                }
                return s;
              });
          }
          return poll();
        })
        .then(function(finalState) {
          if (!finalState) return;
          if (finalState.state !== 'done') {
            _appendOutput('<span class="err">❌ 渲染 ' + finalState.state + ' | ' + (finalState.error || '') + '</span>');
            _setBusy(false); return;
          }
          // REQ-20260920-091：probe 探测**对应**的 output 文件（不是默认 final.mp4）
          _appendOutput('✅ 渲染完成（' + (finalState.elapsed_sec || '?') + ' 秒）\n🔍 探测 output 音频（' + outPath + '）...');
          return fetch('/slirn/api/probe_output_audio', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: tid, output_path: outPath })
          }).then(function(r) { return r.json(); }).then(function(p) {
            var html = '✅ 渲染完成（' + (finalState.elapsed_sec || '?') + ' 秒）\n\n[BGM 检测报告]\n';
            if (!p.ok) { html += '<span class="err">❌ probe 失败: ' + p.error + '</span>'; _appendOutput(html); _setBusy(false); return; }
            html += '  audio stream 数: ' + p.audio_stream_count + '\n';
            html += '  duration: ' + (p.duration ? p.duration.toFixed(1) + 's' : '?') + '\n';
            html += '  mean_volume: ' + (p.mean_volume_db != null ? p.mean_volume_db.toFixed(1) + ' dB' : '?') + '\n';
            html += '  max_volume: ' + (p.max_volume_db != null ? p.max_volume_db.toFixed(1) + ' dB' : '?') + '\n';
            if (p.bgm_mean_volume_db != null) {
              html += '  BGM 源 mean_volume: ' + p.bgm_mean_volume_db.toFixed(1) + ' dB\n';
              var diff = Math.abs((p.mean_volume_db || 0) - p.bgm_mean_volume_db);
              html += '  差距: ' + diff.toFixed(1) + ' dB\n';
              if (diff < 3) html += '  <span class="ok">✅ 高度一致（BGM 正常合成）</span>\n';
              else if (diff < 10) html += '  <span class="warn">⚠ 部分匹配（差距较大）</span>\n';
              else html += '  <span class="err">❌ 差距过大（BGM 可能缺失或异常）</span>\n';
            } else {
              if (p.audio_stream_count === 0) html += '  <span class="err">❌ output 无 audio stream（BGM 缺失）</span>\n';
              else html += '  <span class="warn">⚠ 未对比 BGM 源（未提供 bgm_path）</span>\n';
            }
            _appendOutput(html);
            _setBusy(false);
          });
        })
        .catch(function(e) { _appendOutput('<span class="err">❌ 异常: ' + e.message + '</span>'); _setBusy(false); });
      return;
    }

    if (action === 'combo-apply') {
      _appendOutput('📝 正在应用勾选...');
      _applyComboToFC(tid, state).then(function() {
        _appendOutput('✅ 勾选已写入 fc（layout.video/subtitle/cover/bg + audio）\n💡 点「🚀 一键测试合成」跑 ffmpeg');
      }).catch(function(e) { _appendOutput('<span class="err">❌ 写 fc 失败: ' + e.message + '</span>'); });
      return;
    }
  }

  // REQ-20260920-090：把 5 个勾选状态写入 fc（save_fine_layout + save_fine_audio）
  function _applyComboToFC(tid, state) {
    // /save_fine_layout 接受 body.layout = {element: {enabled, ...}}（一次写 4 个 elements）
    var layoutUpdate = {
      video: { enabled: !!state.video },
      subtitle: { enabled: !!state.subtitle },
      cover: { enabled: !!state.cover },
      bg: { enabled: !!state.bg },
    };
    return fetch('/slirn/api/save_fine_layout', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: tid, layout: layoutUpdate })
    })
      .then(function(r) { return r.json(); })
      .then(function() {
        // /save_fine_audio 接受 body.audio = {enabled: true}
        return fetch('/slirn/api/save_fine_audio', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: tid, audio: { enabled: !!state.audio } })
        });
      })
      .then(function(r) { return r.json(); });
  }

  // 由 export_fine_video 端点启动后台线程后，前端调 startFineExportInline：
  //   1. 把按钮变为「⏳ 导出中…」disabled
  //   2. 按钮右侧 #slirn-fine-export-status 显示状态文字 + 迷你进度条
  //   3. 1.5s 轮询 /slirn/api/render_status 更新进度
  //   4. 点击 status 元素本身 → 取消/下载/查看错误（三态对称交互）
  // 不弹任何模态框，不影响用户操作其他工作。
  function startFineExportInline(tid, jobId, btnEl) {
    var statusEl = document.getElementById('slirn-fine-export-status');
    if (!statusEl) return;
    if (!btnEl) btnEl = document.getElementById('slirn-fine-export-btn');

    setExportBtnState(btnEl, 'running');
    setExportInlineState(statusEl, 'running', 0, 0, 0, 0);

    // REQ-20260920-089：导出进行中显示独立取消按钮
    var cancelBtn = document.getElementById('slirn-fine-export-cancel-btn');
    if (cancelBtn) {
      cancelBtn.style.display = '';
      cancelBtn.setAttribute('data-state', 'running');
      cancelBtn.setAttribute('data-job-id', jobId);
      cancelBtn.disabled = false;
    }

    var _stateLabel = { queued: '排队中', running: '渲染中', done: '已完成', failed: '失败', cancelled: '已取消' };
    var _timer = null;

    // REQ-20260920-089：终态隐藏取消按钮
    var _hideCancelBtn = function() {
      var cb = document.getElementById('slirn-fine-export-cancel-btn');
      if (cb) {
        cb.style.display = 'none';
        cb.setAttribute('data-state', 'idle');
        cb.disabled = false;
      }
    };

    var _poll = function() {
      fetch('/slirn/api/render_status?job_id=' + encodeURIComponent(jobId))
        .then(function(r) { return r.json(); })
        .then(function(s) {
          if (!s.ok) {
            setExportInlineState(statusEl, 'failed', 0, 0, 0, 0);
            clearInterval(_timer); _timer = null;
            setExportBtnState(btnEl, 'failed');
            toast('❌ ' + (s.error || '查询失败'));
            return;
          }
          var st = s.state;
          // 映射后端 state 到 inline 状态元素
          var _map = { running: 'running', done: 'done', failed: 'failed', cancelled: 'idle', queued: 'running' };
          setExportInlineState(
            statusEl,
            _map[st] || 'running',
            s.progress_pct, s.elapsed_sec, s.eta_sec, s.speed_x,
            _stateLabel[st] || st
          );

          if (st === 'done') {
            clearInterval(_timer); _timer = null;
            setExportBtnState(btnEl, 'done', s.output_url);
            statusEl.setAttribute('data-output-url', s.output_url || '');
            _hideCancelBtn();
            toast('✅ 导出完成');
          } else if (st === 'failed') {
            clearInterval(_timer); _timer = null;
            setExportBtnState(btnEl, 'failed', null, s.error);
            statusEl.setAttribute('data-error', s.error || '未知错误');
            _hideCancelBtn();
          } else if (st === 'cancelled') {
            clearInterval(_timer); _timer = null;
            setExportBtnState(btnEl, 'idle');
            _hideCancelBtn();
          }
        })
        .catch(function(e) {
          // 网络抖动不立即报错，下一轮再试
          console.warn('[render_status]', e);
        });
    };
    _poll();
    _timer = setInterval(_poll, 1500);

    // 点击 status 元素本身 → 取消（running）或下载（done）或查看错误（failed）
    statusEl.onclick = function() {
      var st = statusEl.getAttribute('data-state');
      if (st === 'running' || st === 'cancelling') {
        if (!confirm('确认取消当前渲染？已生成的片段会被丢弃。')) return;
        setExportInlineState(statusEl, 'cancelling', null);
        fetch('/slirn/api/cancel_render', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: jobId })
        })
          .then(function(r) { return r.json(); })
          .then(function(j) {
            if (!j.ok) toast('❌ ' + (j.error || '取消失败'));
            else toast('⏹ 已发送取消信号');
          })
          .catch(function(e) { toast('❌ 网络错误: ' + e.message); });
      } else if (st === 'done') {
        var url = statusEl.getAttribute('data-output-url');
        if (url) window.open(url, '_blank');
      } else if (st === 'failed') {
        toast('❌ 渲染失败: ' + (statusEl.getAttribute('data-error') || '未知错误'));
      }
    };

    // 页面卸载时清理 interval（避免泄漏；job 后台继续跑，5min TTL 过期自动清理）
    var _unload = function() { if (_timer) { clearInterval(_timer); _timer = null; } };
    window.addEventListener('beforeunload', _unload);
    window.addEventListener('pagehide', _unload);
  }

  // REQ-20260920-077：更新 inline 状态元素的内容 + 进度条宽度。
  // state: 'idle' | 'running' | 'done' | 'failed' | 'cancelling'
  function setExportInlineState(el, state, pct, elapsed, eta, speed, label) {
    el.setAttribute('data-state', state);
    if (state === 'idle') {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    el.hidden = false;
    var pctStr = (pct != null && !isNaN(pct)) ? pct.toFixed(1) + '%' : '';
    var elapsedStr = (elapsed != null && !isNaN(elapsed)) ? _fmtSec(elapsed) : '';
    var etaStr = (eta != null && eta >= 0) ? '剩 ≈ ' + _fmtSec(eta) : '';
    var speedStr = (speed != null && speed > 0) ? '×' + speed.toFixed(2) : '';
    if (state === 'done') {
      el.innerHTML = '✅ 已完成 · 下载'
        + (elapsedStr ? ' <span style="opacity:.65">· ' + elapsedStr + '</span>' : '');
    } else if (state === 'failed') {
      el.innerHTML = '❌ 失败 · 查看';
    } else if (state === 'cancelling') {
      el.innerHTML = '⏹ 取消中…';
    } else {
      // running
      el.innerHTML = '⏳ ' + (label || '渲染中')
        + ' <span class="slirn-fine-export-track">'
        +   '<span class="slirn-fine-export-bar" style="width:' + pctStr + '"></span>'
        + '</span> '
        + pctStr
        + (elapsedStr ? ' · 已用 ' + elapsedStr : '')
        + (etaStr ? ' · ' + etaStr : '')
        + (speedStr ? ' · ' + speedStr : '');
    }
  }

  // REQ-20260920-077：更新按钮的文本 + disabled + 点击行为。
  // state: 'idle' | 'running' | 'done' | 'failed' | 'cancelling'
  function setExportBtnState(btn, state, outputUrl, error) {
    if (!btn) return;
    btn.removeAttribute('data-state');
    if (state === 'idle') {
      btn.disabled = false;
      btn.textContent = '💾 导出最终视频';
      btn.onclick = null;  // 复用原 action handler 委托
      btn.removeAttribute('data-output-url');
      btn.removeAttribute('data-error');
    } else if (state === 'running' || state === 'cancelling') {
      btn.disabled = true;
      btn.textContent = state === 'cancelling' ? '⏹ 取消中…' : '⏳ 导出中…';
      btn.onclick = null;
    } else if (state === 'done') {
      btn.disabled = false;
      btn.textContent = '✅ 已导出 · 下载';
      btn.setAttribute('data-output-url', outputUrl || '');
      btn.onclick = function(e) {
        e.stopPropagation();
        e.preventDefault();
        if (outputUrl) window.open(outputUrl, '_blank');
        return false;
      };
    } else if (state === 'failed') {
      btn.disabled = false;
      btn.textContent = '❌ 失败 · 重试';
      btn.setAttribute('data-error', error || '未知');
      btn.onclick = null;  // 复用原 action handler 自动重试
    }
  }

  function _fmtMs(ms) {
    if (!ms || ms <= 0) return '00:00:00';
    var s = Math.floor(ms / 1000);
    return _fmtSec(s);
  }
  function _fmtSec(sec) {
    if (sec == null || isNaN(sec) || sec < 0) return '00:00:00';
    var s = Math.floor(sec);
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var ss = s % 60;
    function pad(n) { return n < 10 ? '0' + n : '' + n; }
    return pad(h) + ':' + pad(m) + ':' + pad(ss);
  }

  function fineImportShow() {
    // REQ-20260919-061 用户反馈：点「📥 引用参数」→ 弹出 modal 列出已保存模板。
    // 每次打开都重新拉一次（模板可能已被其他任务/用户改动过）。
    var overlay = document.getElementById('slirn-fine-import-overlay');
    var list = document.getElementById('slirn-fine-import-list');
    if (!overlay || !list) return;
    overlay.hidden = false;
    list.innerHTML = '<div class="slirn-fine-profile-empty">加载中…</div>';
    fetch('/slirn/api/list_fine_global_profiles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        var profiles = (j && j.ok && j.profiles) ? j.profiles : [];
        if (!profiles.length) {
          list.innerHTML =
            '<div class="slirn-fine-profile-empty">暂无模板 — 在顶部「模板名」输入框填名字，点「💾 保存设置参数」即可创建</div>';
          return;
        }
        list.innerHTML = profiles.map(function(p) {
          var ts = (p.saved_at || '').replace('T', ' ').slice(0, 16);
          return (
            '<div class="slirn-fine-import-row" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
              '<div class="slirn-fine-import-info">' +
                '<span class="slirn-fine-import-name">' + _fineEscapeHtml(p.name) + '</span>' +
                '<span class="slirn-fine-import-time">' + _fineEscapeHtml(ts) + '</span>' +
              '</div>' +
              '<div class="slirn-fine-import-ops">' +
                '<button class="slirn-btn slirn-btn-xs slirn-btn-primary" ' +
                  'data-action="fine-import-apply" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
                  '📥 应用</button>' +
                '<button class="slirn-btn slirn-btn-xs" ' +
                  'data-action="fine-import-export" data-profile-id="' + _fineEscapeHtml(p.id) + '" ' +
                  'title="下载该模板的参数为 JSON 文件（与精剪阶段「📤 导出参数」同口径）">' +
                  '📤 导出</button>' +
                '<button class="slirn-btn slirn-btn-xs" ' +
                  'data-action="fine-import-rename" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
                  '✏️ 改名</button>' +
                '<button class="slirn-btn slirn-btn-xs slirn-btn-danger" ' +
                  'data-action="fine-import-delete" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
                  '🗑 删除</button>' +
              '</div>' +
            '</div>'
          );
        }).join('');
      })
      .catch(function(e) {
        list.innerHTML =
          '<div class="slirn-fine-profile-empty">❌ 加载失败: ' + _fineEscapeHtml(e.message) + '</div>';
      });
  }
  function fineImportClose() {
    var overlay = document.getElementById('slirn-fine-import-overlay');
    if (overlay) overlay.hidden = true;
  }
  function fineProfileList() {
    // 拉全局模板列表 → 渲染到 #slirn-fine-profile-list
    fetch('/slirn/api/list_fine_global_profiles', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        var box = document.getElementById('slirn-fine-profile-list');
        if (!box) return;
        var profiles = (j && j.ok && j.profiles) ? j.profiles : [];
        if (!profiles.length) {
          box.innerHTML = '<div class="slirn-fine-profile-empty">暂无模板 — 调好参数后在上面输入名保存</div>';
          return;
        }
        box.innerHTML = profiles.map(function(p) {
          var ts = (p.saved_at || '').replace('T', ' ').slice(0, 16);
          return (
            '<div class="slirn-fine-profile-row" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
              '<div class="slirn-fine-profile-info">' +
                '<span class="slirn-fine-profile-name">' + _fineEscapeHtml(p.name) + '</span>' +
                '<span class="slirn-fine-profile-time">' + _fineEscapeHtml(ts) + '</span>' +
              '</div>' +
              '<div class="slirn-fine-profile-ops">' +
                '<button class="slirn-btn slirn-btn-xs slirn-btn-primary" ' +
                  'data-action="fine-profile-apply" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
                  '📥 应用</button>' +
                '<button class="slirn-btn slirn-btn-xs" ' +
                  'data-action="fine-profile-rename" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
                  '✏️ 改名</button>' +
                '<button class="slirn-btn slirn-btn-xs" ' +
                  'data-action="fine-profile-delete" data-profile-id="' + _fineEscapeHtml(p.id) + '">' +
                  '🗑 删除</button>' +
              '</div>' +
            '</div>'
          );
        }).join('');
      })
      .catch(function(e) {
        toast('❌ 加载模板列表失败: ' + e.message, 'error');
      });
  }
  function fineProfileSave() {
    var tid = _fineTid();
    if (!tid) { toast('❌ 缺少 task_id', 'error'); return; }
    var inp = document.getElementById('slirn-fine-profile-name');
    var name = inp ? inp.value.trim() : '';
    if (!name) { toast('❌ 请先填写模板名', 'error'); return; }
    fetch('/slirn/api/save_fine_global_profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: tid, name: name })
    })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j && j.ok) {
          toast(j.toast || '✅ 已保存');
          if (inp) inp.value = '';
          fineProfileList();
        } else {
          toast('❌ ' + ((j && j.error) || '保存失败'), 'error');
        }
      })
      .catch(function(e) {
        toast('❌ 网络错误: ' + e.message, 'error');
      });
  }
  function fineProfileApply(profileId, profileName) {
    var tid = _fineTid();
    if (!tid) { toast('❌ 缺少 task_id', 'error'); return; }
    // 弹窗确认覆盖
    slirnConfirm(
      '📥 应用模板「' + profileName + '」',
      '将覆盖当前任务的「位置/裁剪/字体/输出/音频」参数。\n素材文件（视频/封面/字幕/BGM）不受影响。\n\n确定继续？',
      function() {
        fetch('/slirn/api/apply_fine_global_profile', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: tid, profile_id: profileId })
        })
          .then(function(r) { return r.json(); })
          .then(function(j) {
            if (j && j.ok) {
              toast(j.toast || '✅ 已应用模板');
              // 替换 wb HTML → 所有控件（滑块/勾选/值显示）刷新
              var inner = document.getElementById('slirn-tab-workbench-inner');
              if (inner && j.html) {
                // 用临时容器解析新 HTML
                var tmp = document.createElement('div');
                tmp.innerHTML = j.html;
                var fresh = tmp.querySelector('#slirn-tab-workbench-inner');
                if (fresh) inner.innerHTML = fresh.innerHTML;
                // 重新绑 fine_cut 控件
                if (typeof bindFineControls === 'function') bindFineControls();
                if (typeof bindFineSteppers === 'function') bindFineSteppers();
                // 引用参数 modal 应用后自动关闭（避免残留旧状态）
                if (typeof fineImportClose === 'function') fineImportClose();
              }
            } else {
              toast('❌ ' + ((j && j.error) || '应用失败'), 'error');
            }
          })
          .catch(function(e) {
            toast('❌ 网络错误: ' + e.message, 'error');
          });
      }
    );
  }
  function fineProfileDelete(profileId) {
    if (!window.confirm('确定删除该模板？\n（不影响已应用此模板的任务）')) return;
    fetch('/slirn/api/delete_fine_global_profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId })
    })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j && j.ok) {
          toast(j.toast || '🗑 已删除');
          fineProfileList();
        } else {
          toast('❌ ' + ((j && j.error) || '删除失败'), 'error');
        }
      })
      .catch(function(e) {
        toast('❌ 网络错误: ' + e.message, 'error');
      });
  }
  function fineProfileRename(profileId, currentName) {
    var newName = window.prompt('新模板名（当前：「' + currentName + '」）', currentName);
    if (!newName || !newName.trim()) return;
    newName = newName.trim();
    if (newName === currentName) return;
    fetch('/slirn/api/rename_fine_global_profile', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId, name: newName })
    })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        if (j && j.ok) {
          toast(j.toast || '✏️ 已重命名');
          fineProfileList();
        } else {
          toast('❌ ' + ((j && j.error) || '改名失败'), 'error');
        }
      })
      .catch(function(e) {
        toast('❌ 网络错误: ' + e.message, 'error');
      });
  }
  // 最小化确认弹窗：复用 .slirn-modal-overlay 样式
  function slirnConfirm(title, message, onOk) {
    var existing = document.getElementById('slirn-confirm-overlay');
    if (existing) existing.remove();
    var overlay = document.createElement('div');
    overlay.className = 'slirn-modal-overlay';
    overlay.id = 'slirn-confirm-overlay';
    overlay.innerHTML =
      '<div class="slirn-modal-card">' +
        '<div class="slirn-modal-title">' + _fineEscapeHtml(title) + '</div>' +
        '<div class="slirn-modal-filename">' + _fineEscapeHtml(message).replace(/\n/g, '<br>') + '</div>' +
        '<div style="display:flex; gap:10px; justify-content:center;">' +
          '<button class="slirn-btn" id="slirn-confirm-cancel">❌ 取消</button>' +
          '<button class="slirn-btn slirn-btn-primary" id="slirn-confirm-ok">✅ 确认覆盖</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);
    function close() { overlay.remove(); }
    document.getElementById('slirn-confirm-cancel').onclick = close;
    document.getElementById('slirn-confirm-ok').onclick = function() {
      close();
      if (typeof onOk === 'function') onOk();
    };
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay) close();  // 点遮罩关闭
    });
  }

  // REQ-20260920-088：素材路径详情弹窗（完整路径 + 来源色块 + 文件元数据）
  function fineMatDetail(tid, kind) {
    if (!tid || !kind) {
      toast('⚠️ 缺少任务 ID 或素材类型', 'warning');
      return;
    }
    postJSON(SLIRN_API + '/material_info', {task_id: tid, kind: kind})
      .then(function(r) {
        if (!r || !r.ok) {
          toast('❌ 查询失败: ' + ((r && r.error) || '未知错误'), 'error');
          return;
        }
        _renderMatDetailModal(r);
        var modal = document.getElementById('slirn-mat-detail-modal');
        if (modal) modal.hidden = false;
      })
      .catch(function(e) {
        toast('❌ 网络错误: ' + (e && e.message ? e.message : e), 'error');
      });
  }

  function _renderMatDetailModal(info) {
    var body = document.getElementById('slirn-mat-detail-body');
    if (!body) return;
    var kindLabel = ({
      video: '🎬 视频',
      subtitle: '📝 字幕',
      cover: '🖼 封面',
      bg: '🎨 背景',
      reference: '🤖 参考',
      audio: '🎵 音频'
    })[info.kind] || info.kind;
    var sourceClass = info.source === 'auto' ? 'mat-source-auto'
      : info.source === 'upload' ? 'mat-source-upload'
      : info.source === 'default_bgm' ? 'mat-source-default'
      : 'mat-source-none';
    var sourceLabel = info.source_label || '未配置';
    var sizeStr;
    if (info.exists) {
      var mb = info.size_bytes / 1024 / 1024;
      if (mb >= 1) {
        sizeStr = mb.toFixed(2) + ' MB';
      } else {
        sizeStr = (info.size_bytes / 1024).toFixed(1) + ' KB';
      }
    } else {
      sizeStr = '—';
    }
    var html = '';
    // 顶部：类型 + 来源色块
    html += '<div class="slirn-mat-detail-header">';
    html += '<span class="slirn-mat-detail-kind">' + escapeHtml(kindLabel) + '</span>';
    html += '<span class="slirn-mat-detail-source ' + sourceClass + '">' + escapeHtml(sourceLabel) + '</span>';
    html += '</div>';
    // 表格
    html += '<table class="slirn-mat-detail-table">';
    html += '<tr><th>📁 物理路径</th><td>' +
      (info.exists
        ? escapeHtml(info.abs_path || '')
        : '<span class="slirn-warn">⚠️ 文件不存在</span>') +
      '</td></tr>';
    html += '<tr><th>🔗 fc.materials.path</th><td>' + escapeHtml(info.fc_path || '—') + '</td></tr>';
    html += '<tr><th>📊 文件大小</th><td>' + escapeHtml(sizeStr) + '</td></tr>';
    html += '<tr><th>🕒 最后修改</th><td>' + escapeHtml(info.mtime || '—') + '</td></tr>';
    html += '<tr><th>🔖 source 字段</th><td>' + escapeHtml(info.source || 'none') + '</td></tr>';
    html += '<tr><th>🎬 类型</th><td>' + escapeHtml(info.type || '—') + '</td></tr>';
    if (info.source === 'auto') {
      var upLabel = info.upstream_name || '—';
      var upExistsHtml = info.upstream_exists
        ? '<span class="slirn-ok">✅ 存在</span>'
        : '<span class="slirn-warn">⚠️ 已不存在</span>';
      html += '<tr><th>📥 上游产物</th><td>' + escapeHtml(upLabel) + ' ' + upExistsHtml + '</td></tr>';
    }
    html += '</table>';
    // 上游产物已删除时给个「重新自动获取」按钮
    if (info.source === 'auto' && !info.upstream_exists) {
      html += '<div class="slirn-mat-detail-actions">';
      html += '<button class="slirn-btn slirn-btn-primary" data-action="fine-source-auto" data-kind="' +
        escapeAttr(info.kind) + '">📥 重新自动获取</button>';
      html += '</div>';
    }
    body.innerHTML = html;
  }

  // REQ-20260920-088：模态框点击遮罩关闭 + ESC 关闭（独立绑定，避免与其它 modal 冲突）
  document.addEventListener('click', function(e) {
    var overlay = e.target;
    if (overlay && overlay.id === 'slirn-mat-detail-modal') {
      overlay.hidden = true;
    }
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      var m = document.getElementById('slirn-mat-detail-modal');
      if (m && !m.hidden) m.hidden = true;
    }
  });

  function fineSourceAuto(btn, kind) {
    // REQ-20260919-061：把素材来源切到「自动获取上游产物」。
    // 设计：两个按钮常驻（手动上传 / 自动获取），点哪个就用哪个，无需切换按钮组。
    var inner = document.getElementById('slirn-tab-workbench-inner');
    var tid = inner ? (inner.getAttribute('data-task-id') || '') : '';
    if (!tid) { toast('❌ 缺少 task_id'); return; }
    btn.disabled = true;
    var oldText = btn.textContent;
    btn.textContent = '📥 获取中...';
    fetch('/slirn/api/auto_pick_upstream_material', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: tid, kind: kind })
    })
      .then(function(r) { return r.json(); })
      .then(function(j) {
        btn.disabled = false; btn.textContent = oldText;
        if (j.ok) {
          if (typeof refreshWb === 'function') refreshWb();
          else location.reload();
          toast('✅ ' + (j.toast || '已自动从上游获取'));
        } else {
          toast('❌ ' + (j.error || '自动获取失败'));
        }
      }).catch(function(e) {
        btn.disabled = false; btn.textContent = oldText;
        toast('❌ 网络错误: ' + e.message);
      });
  }
  // （移除 fineSourceManual：上传按钮始终可用，无需切换）
  function fineCropPreset(btn, preset) {
    // REQ-20260919-061 用户补充：crop_* 用设计空间 1920×1080 像素。
    // 16:9 居中：在 1920×1080 设计空间裁出 1920×1080 矩形 = 全幅；这里改为裁
    // 一个 1920×1080 矩形居中（即全幅），但保留 UI 让用户直观看到比例。
    // 1:1 居中：从源视频居中裁一个 1080×1080 正方形（按设计空间最短边）。
    var defaults = {
      // 全幅：crop 矩形 = 整个 1920×1080（实际源视频全幅）
      'full': { crop_x: 0,    crop_y: 0,    crop_w: 1920, crop_h: 1080 },
      // 16:9 居中：矩形 = 整个 1920×1080（全幅本身就是 16:9，无需裁）
      '16x9': { crop_x: 0,    crop_y: 0,    crop_w: 1920, crop_h: 1080 },
      // 1:1 居中：边长 = 设计空间短边 1080，居中 → (540, 0, 1080, 1080)
      '1x1':  { crop_x: 420,  crop_y: 0,    crop_w: 1080, crop_h: 1080 },
    }[preset] || null;
    if (!defaults) return;
    ['crop_x', 'crop_y', 'crop_w', 'crop_h'].forEach(function(k) {
      var slider = document.getElementById('slirn-fine-video-' + k);
      if (slider) {
        slider.value = defaults[k];
      }
    });
    // 更新比例显示
    var aspectEl = document.getElementById('slirn-fine-crop-aspect-val');
    if (aspectEl && defaults.crop_w > 0) {
      aspectEl.textContent = (defaults.crop_h / defaults.crop_w).toFixed(3);
    }
    // 触发保存
    fineSaveAll();
    toast('✅ 已应用「' + (preset === 'full' ? '全幅' : preset === '16x9' ? '16:9 居中' : '1:1 居中') + '」');
  }

  // REQ-20260920-078：系统默认 BGM 列表 + 选择（一键选 5 个 lo-fi mp3 之一）。
  // 复用现有 fineSaveAll 自动保存机制（不弹 toast）。
  // REQ-20260920-082：暴露到 window 让 pipeline.js loadPanel 完成后调用。
  var _defaultBgmsCache = null;
  window.fineDefaultBgmLoad = async function fineDefaultBgmLoad() {
    var sel = document.getElementById('slirn-fine-default-bgm');
    if (!sel) return;
    if (sel.dataset.loaded === '1') return;
    try {
      var r = await fetch('/slirn/api/list_default_bgms', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: '{}',
      });
      var j = await r.json();
      if (!j.ok) return;
      _defaultBgmsCache = j.bgms || [];
      // 清空已有 options（保留「— 不选 —」）
      while (sel.options.length > 1) sel.remove(1);
      _defaultBgmsCache.forEach(function(b) {
        var opt = document.createElement('option');
        opt.value = b.id;
        if (b.available) {
          var mb = (b.size_bytes / 1048576).toFixed(1);
          opt.textContent = '🎵 ' + b.name + '（' + mb + ' MB）';
        } else {
          opt.textContent = '⚠️ ' + b.name + '（文件缺失）';
          opt.disabled = true;
        }
        sel.appendChild(opt);
      });
      sel.dataset.loaded = '1';
      // 同步当前 fc.materials.audio.path → 对应 ID
      fineDefaultBgmSyncFromFc();
    } catch (e) {
      console.warn('[list_default_bgms]', e);
    }
  }

  function fineDefaultBgmSyncFromFc() {
    var sel = document.getElementById('slirn-fine-default-bgm');
    if (!sel) return;
    // 从页面状态推断：当前 audio 路径里若含 "<bgm_id>.mp3" 则选中
    var matPath = '';
    // 通过现有 audio 控件的 data-* 推断（materials.audio.path 由 save 时收集）
    // 简化：直接对比 _lastSavedAudioPath（保存回调里写入）
    if (window._slirnFineAudioPath) {
      var fname = String(window._slirnFineAudioPath).split('/').pop() || '';
      var match = _defaultBgmsCache && _defaultBgmsCache.find(function(b) {
        return fname === b.id + '.mp3';
      });
      sel.value = match ? match.id : '';
    }
  }

  async function fineDefaultBgmSelect(bgmId, tid) {
    if (!bgmId) {
      // 选「— 不选（清空）—」：不动 fc.materials.audio（保留已上传 BGM）；
      // 只清掉自动启用标记，不强制取消
      return;
    }
    try {
      var r = await fetch('/slirn/api/select_default_bgm', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_id: tid, bgm_id: bgmId}),
      });
      var j = await r.json();
      if (!j.ok) { toast('❌ ' + (j.error || '选择失败')); return; }
      toast(j.toast || '✅ 已选 BGM');
      // 自动勾选「启用背景音乐」checkbox
      var cb = document.querySelector('.slirn-fine-enabled[data-key="audio"]');
      if (cb && !cb.checked) {
        cb.checked = true;
        cb.dispatchEvent(new Event('change', {bubbles: true}));
      }
      // 触发 fineSaveAll 自动保存（后台静默，showToast=false, asTemplate=false）
      if (typeof fineSaveAll === 'function') {
        try { await fineSaveAll(false, false); } catch (e) { /* 静默 */ }
      }
      // 缓存当前路径，供 sync 用
      window._slirnFineAudioPath = 'materials/audio/' + bgmId + '.mp3';
    } catch (e) {
      toast('❌ 网络错误: ' + (e.message || e));
    }
  }

  // 委托 change 事件：用户在「📦 系统默认 BGM」下拉选某项
  document.addEventListener('change', function(e) {
    if (e.target && e.target.id === 'slirn-fine-default-bgm') {
      var _tid = e.target.getAttribute('data-task-id') || _fineTid();
      var val = e.target.value || '';
      fineDefaultBgmSelect(val, _tid);
    }
  });

  // REQ-20260919-062 v14 用户反馈：视频缩放 = 视频原裁剪宽度 / 背景图片宽度。
  // 公式 scale = crop_w / 1920。点「🎯 按裁剪宽度」按钮应用此公式。
  // 设计空间 bg_w = 1920；视频裁剪宽度 = crop_w（来自滑块）。
  // 限制：scale ∈ [0.1, 2.0]（与滑块 max/min 对齐）。
  function fineScaleAutoFromCrop() {
    var cropWEl = document.getElementById('slirn-fine-video-crop_w');
    var scaleEl = document.getElementById('slirn-fine-video-scale');
    if (!cropWEl || !scaleEl) {
      toast('❌ 找不到裁剪宽度 / 缩放滑块', 'error');
      return;
    }
    var cropW = parseInt(cropWEl.value, 10);
    if (!cropW || cropW <= 0) {
      toast('❌ 视频裁剪宽度无效', 'error');
      return;
    }
    // REQ-20260919-062 v15：保留 4 位小数（与自动重算路径一致）
    var scale = Math.round((cropW / 1920) * 10000) / 10000;
    // 限制到滑块范围 [0.1, 2.0]
    var minS = parseFloat(scaleEl.min || '0.1');
    var maxS = parseFloat(scaleEl.max || '2.0');
    scale = Math.max(minS, Math.min(maxS, scale));
    // 同步 slider + number input（用 _fineSyncSlider 一致化处理，保留 4 位）
    _fineSyncSlider('slirn-fine-video-scale', scale);
    // 触发保存 + 刷新视频信息行（updateVideoDisp 由滑块 change 事件触发）
    fineSaveAll();
    toast('✅ 已应用「按裁剪宽度」：scale = ' + scale.toFixed(4));
  }
  // ========== END REQ-20260919-061 前端函数 ==========
  function optFilterBtn(btn) {  // 只看有不明确字词的行 / 全部行
    var list = revVis('slirn-opt-list');
    if (!list) return;
    var only = list.classList.toggle('slirn-opt-only-occ');
    btn.setAttribute('data-shown', only ? '0' : '1');
    btn.textContent = only ? '📋 显示全部识别行'
      : (btn.getAttribute('data-all-text') || '🔍 只看有不明确字词的行');
  }
  function optCollectDecisions() {  // REQ-20260918-056：共用 decisions 收集（前端 DOM = 磁盘状态镜像）
    var list = revVis('slirn-opt-list');
    if (!list) return [];
    var decisions = [];
    list.querySelectorAll('.slirn-opt-occ').forEach(function(w) {
      var inp = w.querySelector('input.slirn-opt-after');
      decisions.push({
        occ_id: parseInt(w.getAttribute('data-occ'), 10),
        applied: w.getAttribute('data-applied') === '1',
        after: inp ? inp.value.trim() : '',
        reviewed: w.getAttribute('data-reviewed') === '1'  // REQ-038：处理进度随保存落盘
      });
    });
    return decisions;
  }
  // REQ-20260918-056：回车后自动保存 — 含并发锁（连续 Enter 合并到 pending）
  var _optSaveInFlight = null;
  var _optSavePending = null;
  function optAutoSave(tid) {
    if (!tid) return;
    if (_optSaveInFlight) {
      // 已有请求在跑 → 把当前快照记为"完成后立即再发一次"
      _optSavePending = {tid: tid, decisions: optCollectDecisions()};
      return;
    }
    var decisions = optCollectDecisions();
    _optSaveInFlight = postJSON(SLIRN_API + '/save_optimize_subtitle',
      {task_id: tid, decisions: decisions})
      .then(function(r) {
        if (r && r.ok) {
          toast('✅ 已自动保存', 'success');
        } else if (r && r.error) {
          toast('❌ 自动保存失败：' + r.error, 'error');
        }
      })
      .catch(function() {
        toast('❌ 自动保存失败（网络错误）', 'error');
      })
      .then(function() {
        _optSaveInFlight = null;
        // 若期间有 pending → 立即再发一次（捕获最后一次的状态）
        if (_optSavePending) {
          var p = _optSavePending; _optSavePending = null;
          optAutoSave(p.tid);
        }
      });
  }
  function optSave(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var decisions = optCollectDecisions();
    if (decisions.length === 0) { toast('❌ 无优化结果列表', 'error'); return; }
    postJSON(SLIRN_API + '/save_optimize_subtitle', {task_id: tid, decisions: decisions})
      .then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已确认保存');
          openWorkbench(tid);  // 刷新阶段态（done）+ 统计 + 对应关系
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
  }
  function optSrtDownload(btn) {  // 下载优化后成片字幕 SRT（粗剪成片时间基）
    var tid = btn.getAttribute('data-task-id') || '';
    postJSON(SLIRN_API + '/optimized_srt', {task_id: tid}).then(function(r) {
      if (!r || !r.ok || !r.srt) {
        toast('❌ ' + (r && r.error ? r.error : '获取 SRT 失败'), 'error');
        return;
      }
      var blob = new Blob([r.srt], {type: 'text/plain;charset=utf-8'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'optimized_subs_' + tid + '.srt';
      document.body.appendChild(a); a.click();
      setTimeout(function() { URL.revokeObjectURL(url); document.body.removeChild(a); }, 100);
      toast('⬇️ 已下载 ' + (r.lines || 0) + ' 行 SRT');
    });
  }
  function playOptAt(tid, startMs) {  // 行定位播放（成片时间基 — ?src=rough_compose 源）
    var wrap = revVis('slirn-opt-player-wrap');
    var v = revVis('slirn-opt-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
    if (wrap) vfShow(wrap);
    if (!v.src) {
      v.src = SLIRN_API + '/video/' + encodeURIComponent(tid) + '?src=rough_compose';
      v.load();
    }
    // REQ-20260918-054：首次绑定 timeupdate + seeked 跟高亮（幂等）
    bindOptPlayerHighlight();
    var goO = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      // REQ-20260918-054：跳转后立即按 currentTime 重算（不等首个 timeupdate）
      optPlayerHighlight(v);
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) goO();
    else v.addEventListener('loadedmetadata', goO, {once: true});
  }
  // REQ-20260918-054：按 currentTime 重算当前播放行 + 应用 .active + 自动滚到视口
  // 单行点击语义，没有 cut/rev 那样的连续跳播链；只做"高亮跟随"。
  function optPlayerHighlight(v) {
    var list = revVis('slirn-opt-list');
    if (!v || !list) return;
    var rows = Array.prototype.slice.call(
      list.querySelectorAll('.slirn-opt-row[data-start-ms]'));
    if (rows.length === 0) return;
    var tms = (v.currentTime || 0) * 1000, hit = -1;
    for (var i = 0; i < rows.length; i++) {
      var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
      var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
      // 兜底：end_ms 缺失/≤start 时用下一行 start_ms 作为上界；
      // 最后一行（无下一行）兜底为 Infinity — 让此行包到结尾，避免
      // currentTime 越过末行 start_ms 时高亮丢失
      var eEff = (e0 > s0) ? e0 :
        (i + 1 < rows.length) ?
          (parseInt(rows[i + 1].getAttribute('data-start-ms'), 10) || (s0 + 1)) :
          Infinity;
      if (tms >= s0 && tms < eEff) { hit = i; break; }
      if (s0 > tms) break;
    }
    for (var j = 0; j < rows.length; j++) {
      rows[j].classList.toggle('active', j === hit);
    }
    if (hit >= 0 && rows[hit] && rows[hit].scrollIntoView) {
      try { rows[hit].scrollIntoView({block: 'nearest'}); } catch (err) {}
    }
  }
  function bindOptPlayerHighlight() {  // 幂等：video 替换后由 v.dataset 标记防重复
    var v = revVis('slirn-opt-player');
    if (!v || v.dataset.optHLBound) return;
    v.dataset.optHLBound = '1';
    v.addEventListener('timeupdate', function() { optPlayerHighlight(v); });
    v.addEventListener('seeked',    function() { optPlayerHighlight(v); });
  }
  function bindOptRows(tid) {  // 行点击定位播放（输入框/按钮自身不触发）
    var list = revVis('slirn-opt-list');
    if (!list) return;
    list.querySelectorAll('.slirn-opt-row[data-start-ms]').forEach(function(row) {
      row.addEventListener('click', function(ev) {
        if (ev.target && ev.target.closest('button,input')) return;
        playOptAt(tid, parseInt(row.getAttribute('data-start-ms'), 10) || 0);
      });
    });
  }
  // REQ-20260918-050：词频列表分页（每页 20 条 = 每行 2 条 × 10 行）
  var OPT_WORDS_PAGE_SIZE = 20;
  var optWordsCurrentPage = 1;
  function setupOptWordsPagination() {  // 初始化分页（每次 wb 重渲染后调）
    var box = document.getElementById('slirn-opt-words');
    if (!box) return;
    // 用 class 找 pager（不依赖位置：colEnhance 会把 box 包进 .slirn-col，
    // 那时 box.nextElementSibling 就不是 pager；用 querySelector 永远拿得到）
    var pager = document.querySelector('.slirn-opt-words-pager');
    if (!pager) {
      pager = document.createElement('div');
      pager.className = 'slirn-opt-words-pager';
      // 紧贴 box 后面插入（"行内 …删除线…" 提示之前）。即使 colEnhance 后续
      // 把 box 包进 .slirn-col，pager 仍在 slirn-card 层级、紧跟在 col 之后，
      // 视觉上仍是「词频列表正下方」，不会跑到字幕列表下方。
      box.parentNode.insertBefore(pager, box.nextSibling);
    }
    optWordsCurrentPage = 1;  // 重置页码（wb 重渲后用户期望从头看）
    paginateOptWords();
    // REQ-20260918-057B：wb 重渲后 input 是新元素，重绑 input 事件
    bindOptWordTextFilter();
  }
  // REQ-20260918-058：替换输入框溢出检测 — scrollWidth>clientWidth 时加 .wrapped
  function optInputOverflowCheck(input) {
    if (!input) return;
    var occ = input.closest('.slirn-opt-occ');
    if (!occ) return;
    // 短文字 → 移除 wrapped（确保 wrap 后再变短能恢复横排）
    // 长文字 → 添加 wrapped
    // 容差 +2px（防浏览器子像素 rounding）
    var overflow = (input.scrollWidth || 0) > (input.clientWidth || 0) + 2;
    occ.classList.toggle('wrapped', overflow);
  }
  function optInputOverflowInit() {  // wb 重渲后调一次：检测 + 绑 input 事件
    var list = document.getElementById('slirn-opt-list');
    if (!list) return;
    var inputs = list.querySelectorAll('.slirn-opt-occ input.slirn-opt-after');
    inputs.forEach(function(inp) {
      // 多重试：font 加载 + 容器布局完成前 scrollWidth/clientWidth 可能不准
      requestAnimationFrame(function() { optInputOverflowCheck(inp); });
      setTimeout(function() { optInputOverflowCheck(inp); }, 200);
      setTimeout(function() { optInputOverflowCheck(inp); }, 800);
      if (!inp.dataset.wrapBound) {
        inp.dataset.wrapBound = '1';
        var t = null;
        inp.addEventListener('input', function() {
          if (t) clearTimeout(t);
          t = setTimeout(function() { optInputOverflowCheck(inp); }, 100);
        });
        // ResizeObserver：父容器/window resize 后重测（fallback）
        if (window.ResizeObserver && !inp.dataset.roBound) {
          inp.dataset.roBound = '1';
          try {
            var ro = new ResizeObserver(function() {
              setTimeout(function() { optInputOverflowCheck(inp); }, 50);
            });
            ro.observe(inp);
          } catch (e) { /* ignore */ }
        }
      }
    });
  }
  function paginateOptWords() {
    var box = document.getElementById('slirn-opt-words');
    if (!box) return;
    var pager = document.querySelector('.slirn-opt-words-pager');
    var all = Array.prototype.slice.call(box.querySelectorAll('.slirn-opt-word'));
    // 过滤当前「应该被分页的」词：optWordFilterBtn 用 display:none 隐藏未选中词。
    // 注意不能用 slirn-opt-word-hidden 来过滤——分页自己也用它，否则翻页后会
    // 把「分页隐藏的词」从计数里去掉，total 会越翻越少，最终 total <= 20 触
    // 发「隐藏分页控件」分支、把分页器清空，再也翻不回去。
    var visible = all.filter(function(el) {
      return el.style.display !== 'none';
    });
    var total = visible.length;
    var pages = Math.max(1, Math.ceil(total / OPT_WORDS_PAGE_SIZE));
    if (optWordsCurrentPage > pages) optWordsCurrentPage = pages;

    // 隐藏非当前页
    visible.forEach(function(el, i) {
      var pageIdx = Math.floor(i / OPT_WORDS_PAGE_SIZE);
      el.classList.toggle('slirn-opt-word-hidden', pageIdx !== optWordsCurrentPage - 1);
    });

    // 更新分页控件（pager 用 class 找，colEnhance wrap 不影响）
    if (!pager) return;
    if (total <= OPT_WORDS_PAGE_SIZE) {
      pager.style.display = 'none';
      pager.innerHTML = '';
      return;
    }
    pager.style.display = '';
    pager.innerHTML =
      '<button type="button" data-action="opt-page-prev"' + (optWordsCurrentPage <= 1 ? ' disabled' : '') + '>‹ 上一页</button>'
      + '<span class="slirn-opt-page-info">第 ' + optWordsCurrentPage + ' / ' + pages + ' 页 · 共 ' + total + ' 个词</span>'
      + '<button type="button" data-action="opt-page-next"' + (optWordsCurrentPage >= pages ? ' disabled' : '') + '>下一页 ›</button>';
  }
  // 保存切分决策：全量收集子段 mark + 组级 action（改判 split 附切分后内容）→ 落盘
  function cutSave(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var marks = {};
    cutRows().forEach(function(r) {
      if (r.classList.contains('sub') && r.getAttribute('data-id'))
        marks[r.getAttribute('data-id')] = r.getAttribute('data-mark') || 'keep';
    });
    var acts = {}, targets = {}, badSplit = null;
    document.querySelectorAll('#slirn-cut-list .slirn-cut-group[data-act]').forEach(function(g) {
      acts[g.getAttribute('data-source-i') || ''] = g.getAttribute('data-act');
    });
    // 改判切分必须有切分后内容：组 data-target（重切/恢复）或当前编辑区输入值
    document.querySelectorAll('#slirn-cut-list .slirn-cut-group[data-act="split"]').forEach(function(g) {
      var tv = (g.getAttribute('data-target') || '').trim();
      if (!tv) {
        var inp = document.querySelector('.slirn-cut-resplit-input');
        if (inp && inp.closest('.slirn-cut-group') === g) tv = inp.value.trim();
      }
      if (!tv) badSplit = g.getAttribute('data-source-i');
      targets[g.getAttribute('data-source-i') || ''] = tv;
    });
    if (badSplit !== null) {
      toast('❌ 第 ' + badSplit + ' 条已改判切分，但未填写切分后内容 — 按 S 或点 ✂️ 填写后重切', 'error');
      return;
    }
    postJSON(SLIRN_API + '/save_cut_decisions',
             {task_id: tid, manual_marks: marks, actions: acts, split_targets: targets})
      .then(function(r) {
        if (r && r.ok) { toast(r.toast || '切分决策已保存'); openWorkbench(tid); }
        else if (r && r.error) toast('❌ ' + r.error, 'error');
      });
  }
  // 播放器绑定：timeupdate 三合一 — 试听跳播 / 预播段尾自动停 / 高亮跟随
  function bindCutPlayer() {
    var v = revVis('slirn-cut-player');
    var list = revVis('slirn-cut-list');
    bindSpeedControl(v);  // 倍速显示与控制（REQ-20260916-014）
    if (v && list && !v.dataset.bound) {
      v.dataset.bound = '1';
      var rows = Array.prototype.slice.call(list.querySelectorAll('.slirn-cut-row'));
      var lastHit = -1;
      v.addEventListener('timeupdate', function() {
        var tms = v.currentTime * 1000;
        // ① 试听跳播（REQ-20260916-012 索引跟踪）：只看当前段 — 播过当前 keep
        // 段尾 → seek 下一段起点；播完最后一段 → 停。绝不从 0 重扫：旧行为里
        // 跳到下一段后 tms ≥ 前段尾恒成立，会立刻再跳、末组折返，段间无限
        // 乒乓反复播放且永不结束。
        // 序列来源（REQ-20260916-015）：组头试听（cutKeepMode='group'，显示成片
        // 试听条）或行级连续播放（'row'，只播保留内容、不显示试听条）。
        if (cutKeepSeq) {
          var seg = cutKeepSeq[cutKeepIdx];
          if (tms < seg.s - 500) {  // 用户回拖：重定位到 tms 所在段
            while (cutKeepIdx > 0 && tms < cutKeepSeq[cutKeepIdx].s) cutKeepIdx--;
            seg = cutKeepSeq[cutKeepIdx];
          }
          if (tms >= seg.e - 30) {
            if (cutKeepIdx + 1 < cutKeepSeq.length) {
              cutKeepIdx++;
              var nxt = cutKeepSeq[cutKeepIdx];
              try { v.currentTime = nxt.s / 1000; } catch (err) {}
              // 不移动 kbsel（REQ-20260917-027）：跳播/播放位置的高亮由 ② 的
              // .active 跟随；kbsel 是键盘/鼠标的决策目标，保持在用户选中的行 —
              // 快捷键删除过的行点击后选中不再被跳播抢走，可立即用下拉改回
              if (cutKeepMode === 'group') cutAuditionBar(v);
              return;  // 跳转后的首个 timeupdate 再走高亮，防旧位置误亮
            }
            v.pause(); cutKeepSeq = null; cutKeepMode = null;
            cutAuditionBar(null);  // 播放完毕：试听结束收起
          } else {
            if (cutKeepMode === 'group') cutAuditionBar(v);  // 试听时间戳与当前段保持一致
          }
        }
        // ② 高亮跟随（与字幕/修订阶段同款 active：段间缝隙保持前一段亮）
        var hit = -1;
        for (var i = 0; i < rows.length; i++) {
          var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
          var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
          if (tms >= s0 && tms < e0) { hit = i; break; }
          if (s0 > tms) break;
        }
        if (hit === -1 && lastHit >= 0) {
          var eh = parseInt(rows[lastHit].getAttribute('data-end-ms'), 10) || 0;
          var nh = (lastHit + 1 < rows.length)
            ? (parseInt(rows[lastHit + 1].getAttribute('data-start-ms'), 10) || 0)
            : Infinity;
          if (tms >= eh && tms < nh) hit = lastHit;
        }
        lastHit = hit;
        for (var j = 0; j < rows.length; j++) rows[j].classList.toggle('active', j === hit);
        if (hit >= 0 && rows[hit] && rows[hit].scrollIntoView) rows[hit].scrollIntoView({block: 'nearest'});
      });
    }
  }

  function bindRevPlayer() {
    var v = revVis('slirn-rev-player');
    var list = revVis('slirn-rev-list');
    bindSpeedControl(v);  // 倍速显示与控制（REQ-20260916-014）
    if (v && list && !v.dataset.bound) {
      v.dataset.bound = '1';
      revPlaySeq = null;  // 面板重建（新视频元素）→ 旧链作废，防 stale 行时间戳（REQ-20260917-028）
      var rows = Array.prototype.slice.call(list.querySelectorAll('.slirn-rev-row'));
      var lastHit = -1;
      var setActive = function(idx) {
        for (var i = 0; i < rows.length; i++) rows[i].classList.toggle('active', i === idx);
        if (idx >= 0 && rows[idx] && rows[idx].scrollIntoView) rows[idx].scrollIntoView({block: 'nearest'});
      };
      v.addEventListener('timeupdate', function() {
        var tms = v.currentTime * 1000, hit = -1;
        // ① 连续跳播（REQ-20260917-028）：本条到尾 → seek 下一条起点（段间空白
        // 不播）；回拖/前拖按时间重定位链位置；播完最后一条停
        if (revPlaySeq && revPlaySeq.length) {
          if (tms < revPlaySeq[revPlayIdx].s - 500) {
            while (revPlayIdx > 0 && tms < revPlaySeq[revPlayIdx].s) revPlayIdx--;
          } else {
            while (revPlayIdx + 1 < revPlaySeq.length && tms >= revPlaySeq[revPlayIdx + 1].s) revPlayIdx++;
          }
          var seg = revPlaySeq[revPlayIdx];
          if (tms >= seg.e - 30) {
            if (revPlayIdx + 1 < revPlaySeq.length) {
              revPlayIdx++;
              try { v.currentTime = revPlaySeq[revPlayIdx].s / 1000; } catch (err) {}
              return;  // 跳转后的首个 timeupdate 再走高亮，防旧位置误亮
            }
            v.pause(); revPlaySeq = null;  // 最后一条播完：自动停
          }
        }
        // ② 高亮跟随（与字幕/切分阶段同款 active：段间缝隙保持前一段亮）
        for (var i = 0; i < rows.length; i++) {
          var s0 = parseInt(rows[i].getAttribute('data-start-ms'), 10) || 0;
          var e0 = parseInt(rows[i].getAttribute('data-end-ms'), 10) || 0;
          if (tms >= s0 && tms < e0) { hit = i; break; }
          if (s0 > tms) break;
        }
        if (hit === -1 && lastHit >= 0) {
          var eh = parseInt(rows[lastHit].getAttribute('data-end-ms'), 10) || 0;
          var nh = (lastHit + 1 < rows.length)
            ? (parseInt(rows[lastHit + 1].getAttribute('data-start-ms'), 10) || 0)
            : Infinity;
          if (tms >= eh && tms < nh) hit = lastHit;
        }
        lastHit = hit;
        setActive(hit);
      });
    }
    // 工作台（重新）打开时，若 job 还在跑 → 恢复轮询
    var st = document.getElementById('slirn-rev-status');
    if (st && st.dataset.taskId && st.dataset.state === 'running') startRevPolling(st.dataset.taskId);
    applyRevKeysState();  // 提示条跟随自定义键位（localStorage — REQ-20260916-005）
    revFilterSync();  // 过滤条：渲染 chips 计数 + 应用上次筛选（localStorage — REQ-20260916-010）
  }

  // ===== 字幕修订快捷键（REQ-20260916-004）：听 → 判 → 标记 → 下一条 =====
  // ↑↓ 选行（跳到行起点，播放态跟随）· 空格 播放/暂停 · R 重播本行
  // K 保留 / D 删除（标记后自动下一条）· S 切分（展开详情+聚焦内容输入，不跳行）
  // Esc 从切分修剪后内容输入框退回列表；输入框/下拉聚焦时不劫持按键
  function revRows() {
    var list = revVis('slirn-rev-list');
    if (!list || !list.offsetParent) return [];  // 列表不可见 → 快捷键整体不生效
    return Array.prototype.slice.call(list.querySelectorAll('.slirn-rev-row'));
  }
  function revSelIndex(rows) {
    for (var i = 0; i < rows.length; i++) { if (rows[i].classList.contains('kbsel')) return i; }
    return -1;
  }
  function revMarkSel(row) {
    revRows().forEach(function(r) { r.classList.toggle('kbsel', r === row); });
    if (row && row.scrollIntoView) row.scrollIntoView({block: 'nearest'});
  }
  function revSelectRow(idx, seek) {
    var rows = revNavRows();  // 过滤后可见行（REQ-20260916-010）：↑↓ 跳过被筛掉的行
    if (!rows.length) return null;
    var cur = revSelIndex(rows);
    if (cur < 0) idx = (idx < 0) ? rows.length - 1 : 0;  // 无选中：↓ 取第一行，↑ 取最后一行
    idx = Math.max(0, Math.min(rows.length - 1, idx));
    var row = rows[idx];
    revMarkSel(row);
    if (seek) {
      var v = revVis('slirn-rev-player');
      if (v && v.src) {  // 播放器加载过才跳（暂停时不强制播放，按空格续听）
        try { v.currentTime = (parseInt(row.getAttribute('data-start-ms'), 10) || 0) / 1000; } catch (err) {}
      }
    }
    return row;
  }
  function revTogglePlay() {
    var v = revVis('slirn-rev-player');
    if (!v) return;
    if (!v.src) {  // 从未播放过：从选中行（或第一行）起点开播（连续跳播 — REQ-20260917-028）
      var rows = revNavRows();
      var row = rows[revSelIndex(rows)] || rows[0];
      if (row) revPlayFrom(row);
      return;
    }
    if (v.paused) { var p = v.play(); if (p && p.catch) p.catch(function() {}); }
    else { v.pause(); }
  }
  function revReplayRow() {
    var rows = revNavRows();
    var row = rows[revSelIndex(rows)];
    if (!row) {
      var kmR = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmR.prev) + ' / ' + revKeyLabel(kmR.next) + ' 选择一条字幕', 'error');
      return;
    }
    revPlayFrom(row);  // 重播本条并续链（REQ-20260917-028：播完跳下一条，空白不播）
  }
  function revApplyDecision(val) {
    var rows = revNavRows();
    var idx = revSelIndex(rows);
    if (idx < 0) {
      var kmA = revKeysLoad();
      toast('⌨ 先用 ' + revKeyLabel(kmA.prev) + ' / ' + revKeyLabel(kmA.next) + ' 选择一条字幕，再按 '
        + revKeyLabel(kmA.keep) + ' / ' + revKeyLabel(kmA.del) + ' / ' + revKeyLabel(kmA.split) + ' 标记', 'error');
      return;
    }
    var row = rows[idx];
    var sel = row.querySelector('.slirn-rev-select');
    if (!sel) return;
    sel.value = val;
    sel.dispatchEvent(new Event('change', {bubbles: true}));
    if (val === 'split') {
      // 切分需要人工给出修剪后文本：展开详情块、光标移到输入框，不自动跳行（写完按 Esc 返回）
      row.classList.add('open');
      var tg = row.querySelector('.slirn-rev-toggle');
      if (tg) tg.textContent = '▴';
      var note = row.querySelector('.slirn-rev-note-input');
      if (note) {
        note.focus();
        try { note.setSelectionRange(note.value.length, note.value.length); } catch (err) {}
      }
      toast('✂️ 已标记切分 — 填写切分修剪后内容后按 ' + revKeyLabel(revKeysLoad().esc) + ' 返回列表');
    } else {
      revSelectRow(idx + 1, true);  // 保留/删除：标记即过，自动下一条
    }
  }

  // ===== 修订列表状态过滤（REQ-20260916-010）=====
  // 两个维度：建议状态（模型判定）/ 决策状态（用户选择）。决策维度按「实质
  // 类别」匹配 — accept 的实质 = 模型建议类别（与切分清单 _final_kind 同源）：
  // 筛「切分」时「采纳+建议切分」的行也命中。筛选只藏行不删行，
  // 「保存修订决策」仍收集全部行（revRows 不受过滤影响）。
  var REV_FILTER_SUGG = [
    ['keep', '保留'], ['delete', '删除'], ['split', '切分'],
    ['fix', '更正'], ['review', '复核'],
  ];
  var REV_FILTER_DEC = [
    ['pending', '未决策'], ['accept', '采纳建议'],
    ['keep', '保留'], ['delete', '删除'], ['split', '切分'], ['fix', '内容更正'],
  ];
  function revFilterLoad() {
    try { return JSON.parse(localStorage.getItem('slirnRevFilter') || 'null'); }
    catch (err) { return null; }
  }
  var revFilter = revFilterLoad() || { dim: 'sugg', val: 'all' };
  if (revFilter.dim !== 'sugg' && revFilter.dim !== 'dec') revFilter = { dim: 'sugg', val: 'all' };
  function revFilterSave() {
    try { localStorage.setItem('slirnRevFilter', JSON.stringify(revFilter)); } catch (err) {}
  }
  function revNavRows() {  // 过滤后仍可见的行（导航/标记走这里；保存收集仍用 revRows 全量）
    return revRows().filter(function(r) { return r.style.display !== 'none'; });
  }
  function revMatchKind(row, dim, val) {  // 统一匹配口径（筛选与跳转共用）
    if (val === 'all') return true;
    if (dim === 'sugg') return (row.getAttribute('data-sugg') || '') === val;
    if (val === 'pending' || val === 'accept')
      return (row.getAttribute('data-decision') || '') === val;
    return (row.getAttribute('data-final') || '') === val;  // 实质口径
  }
  function revFilterMatch(row) { return revMatchKind(row, revFilter.dim, revFilter.val); }
  function revFilterCountOf(rows, dim, val) {
    return rows.filter(function(r) { return revMatchKind(r, dim, val); }).length;
  }
  function revFilterRender() {
    var chips = revVis('slirn-rev-filter-chips');
    if (!chips) return;
    var rows = revRows();
    var defs = revFilter.dim === 'sugg' ? REV_FILTER_SUGG : REV_FILTER_DEC;
    var html = '<button type="button" class="slirn-rev-chip' + (revFilter.val === 'all' ? ' active' : '')
      + '" data-rev-filter-val="all">全部 ' + rows.length + '</button>';
    defs.forEach(function(d) {
      var n = revFilterCountOf(rows, revFilter.dim, d[0]);
      var solid = revFilter.dim === 'dec' && d[0] !== 'pending' && d[0] !== 'accept';
      html += '<button type="button" class="slirn-rev-chip' + (revFilter.val === d[0] ? ' active' : '')
        + '" data-rev-filter-val="' + d[0] + '"'
        + (solid ? ' title="实质口径：含「采纳建议」且建议为此类的行"' : '')
        + '>' + d[1] + ' ' + n + '</button>';
    });
    chips.innerHTML = html;
    document.querySelectorAll('[data-rev-filter-dim]').forEach(function(b) {
      b.classList.toggle('active', b.getAttribute('data-rev-filter-dim') === revFilter.dim);
    });
  }
  function revFilterApply() {
    if (!revVis('slirn-rev-filter')) return;
    var rows = revRows(), shown = 0;
    rows.forEach(function(r) {
      var ok = revFilterMatch(r);
      r.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    var cnt = revVis('slirn-rev-filter-count');
    if (cnt) cnt.textContent = '显示 ' + shown + ' / ' + rows.length + ' 条';
  }
  function revFilterSync() { revFilterRender(); revFilterApply(); revJumpRenderSel(); }

  // ===== 批量改判（REQ-20260918-039）：起止序号 + 目标状态，确认后一次改一批 =====
  // 与单条修改同口径：纯前端状态（保存前可继续调整），落盘走各阶段既有保存按钮
  function batchRange(box, sId, eId) {  // 读起止输入 → {s,e} | null（非法先 toast 报错）
    var sEl = box.querySelector('#' + sId), eEl = box.querySelector('#' + eId);
    var s = parseFloat(sEl && sEl.value), e = parseFloat(eEl && eEl.value);
    if (!isFinite(s) || !isFinite(e) || s > e || s < 0) {
      toast('❌ 批量区间不正确：起止都要是数字，且 起 ≤ 止', 'error');
      return null;
    }
    return { s: s, e: e };
  }
  function revBatchApply() {  // 字幕修改：区间内行决策 → 目标（data-final 实质口径同步）
    var box = document.getElementById('slirn-rev-batch');
    if (!box) return;
    var rg = batchRange(box, 'slirn-rev-batch-start', 'slirn-rev-batch-end');
    if (!rg) return;
    var selT = box.querySelector('#slirn-rev-batch-sel');
    var tv = selT ? selT.value : '';
    if (!tv) { toast('❌ 请选择目标状态', 'error'); return; }
    var label = (selT && selT.options[selT.selectedIndex])
      ? selT.options[selT.selectedIndex].textContent : tv;
    var rows = [];
    var all = document.querySelectorAll('#slirn-rev-list .slirn-rev-row');
    for (var i = 0; i < all.length; i++) {
      var sel = all[i].querySelector('.slirn-rev-select');
      var si = parseInt(sel && sel.getAttribute('data-i'), 10);
      if (isFinite(si) && si >= rg.s && si <= rg.e) rows.push(all[i]);
    }
    if (!rows.length) {
      toast('❌ 区间 ' + rg.s + ' ～ ' + rg.e + ' 内没有字幕行', 'error');
      return;
    }
    if (!window.confirm('把第 ' + rg.s + ' ～ ' + rg.e + ' 条（共 ' + rows.length
        + ' 条）的决策改为「' + label + '」？\n（未保存 — 保存前可继续调整）')) return;
    rows.forEach(function(row) {
      var selR = row.querySelector('.slirn-rev-select');
      if (selR) selR.value = tv;
      var sugg = row.getAttribute('data-sugg') || '';
      var fin = '';  // 实质口径 — 与决策 change 委托同算法
      if (tv === 'keep' || tv === 'delete' || tv === 'split' || tv === 'fix') fin = tv;
      else if (tv === 'accept' && (sugg === 'keep' || sugg === 'delete'
          || sugg === 'split' || sugg === 'fix')) fin = sugg;
      row.setAttribute('data-decision', tv);
      row.setAttribute('data-final', fin);
      // split/fix：空备注从模型建议预填（与单条 change 同源；不展开不聚焦 — 批量不抢焦点）
      var note = row.querySelector('.slirn-rev-note-input');
      if (note && (tv === 'split' || tv === 'fix') && !note.value.trim()) {
        var kt = row.querySelector('.slirn-rev-keeptext');
        var m = kt && /「(.+)」/.exec(kt.textContent || '');
        if (m) note.value = m[1];
      }
    });
    revFilterSync();
    toast('🧮 已批量设置 ' + rows.length + ' 条 →「' + label + '」（未保存 — 记得保存修订决策）');
  }
  function cutBatchApply() {  // 切分修剪：整段行=组级决策 / 子段行=去留标记
    var box = document.getElementById('slirn-cut-batch');
    if (!box) return;
    var rg = batchRange(box, 'slirn-cut-batch-start', 'slirn-cut-batch-end');
    if (!rg) return;
    var selT = box.querySelector('#slirn-cut-batch-sel');
    var tv = selT ? selT.value : 'keep';
    var label = (selT && selT.options[selT.selectedIndex])
      ? selT.options[selT.selectedIndex].textContent : tv;
    // 行命中：行号数值（10 / 10.1）∈ 区间，或所属父段号 ∈ 区间
    //（"第 10 到 10 条"覆盖该段全部子段 — 与界面所见编号对应）
    var wholes = [], subs = [];
    var all = document.querySelectorAll('#slirn-cut-list .slirn-cut-row');
    for (var i = 0; i < all.length; i++) {
      var r = all[i];
      var id = parseFloat(r.getAttribute('data-id'));
      var src = parseInt(r.getAttribute('data-source-i'), 10);
      var hit = (isFinite(id) && id >= rg.s && id <= rg.e)
             || (isFinite(src) && src >= rg.s && src <= rg.e);
      if (!hit) continue;
      if (r.classList.contains('sub')) subs.push(r);
      else wholes.push(r);
    }
    var n = wholes.length + subs.length;
    if (!n) {
      toast('❌ 区间 ' + rg.s + ' ～ ' + rg.e + ' 内没有切分行', 'error');
      return;
    }
    var parts = [];
    if (wholes.length) parts.push('整段行 ' + wholes.length + ' 条（改组级决策）');
    if (subs.length) parts.push('子段行 ' + subs.length + ' 条（改去留标记）');
    if (!window.confirm('把第 ' + rg.s + ' ～ ' + rg.e + ' 条改为「' + label + '」？\n'
        + parts.join(' · ') + '\n（未保存 — 保存前可继续调整）')) return;
    wholes.forEach(function(r) {  // 整段行 → 组级决策（与 actsel 同效；静默不逐条 toast）
      var g = r.closest('.slirn-cut-group');
      if (!g) return;
      if (tv) g.setAttribute('data-act', tv);
      else g.removeAttribute('data-act');  // 维持原状 = 取消改判
      var b = g.querySelector('[data-actsel]');
      if (b) b.value = tv;
    });
    subs.forEach(function(r) {  // 子段行 → 去留（维持原状 = 回初始建议标记）
      cutSpkSetMark(r, tv || r.getAttribute('data-mark-init') || 'keep');
    });
    var spkBar = document.getElementById('slirn-cut-spk-bar');  // 仅计未删除口径跟随刷新
    if (spkBar && spkBar.getAttribute('data-linked') === '1') cutSpkBarRender();
    toast('🧮 已批量设置 ' + n + ' 条 →「' + label + '」（未保存 — 记得保存切分决策）');
  }

  // ===== 状态跳转（REQ-20260917-025）：独立状态选择列表 + 完整列表内跳上/下一条 =====
  // 跳转目标由「⬆/⬇ 按钮前」的状态下拉显式指定（与筛选 chips 完全解耦）：
  // 跳转时显示全部行——目标行的上下文语句可见，便于结合前后文决定处理；
  // 筛选 chips 仍是纯视图收窄（会藏行）。kbsel 高亮目标行并居中滚动；
  // 播放器已加载则定位到行起点（与 ↑↓ 键同源）。匹配口径与筛选项完全
  // 同源（revMatchKind）；选「全部」时退化为整表上一/下一条。
  var revJumpSel = null;  // {dim, val}：跳转目标状态（localStorage 持久化，与筛选独立）
  var revJumpIdx = -1;    // 上次跳转命中的行（revRows 全量下标）；-1 = 未跳过
  function revJumpSelLoad() {
    try {
      var s = JSON.parse(localStorage.getItem('slirnRevJump') || 'null');
      if (s && (s.dim === 'sugg' || s.dim === 'dec') && typeof s.val === 'string') return s;
    } catch (err) {}
    return null;
  }
  function revJumpSelSave() {
    try { localStorage.setItem('slirnRevJump', JSON.stringify(revJumpSel)); } catch (err) {}
  }
  function revJumpLabel() {
    if (!revJumpSel || revJumpSel.val === 'all')
      return (revJumpSel && revJumpSel.dim === 'sugg' ? '建议' : '决策') + ' · 全部';
    var defs = revJumpSel.dim === 'sugg' ? REV_FILTER_SUGG : REV_FILTER_DEC;
    for (var i = 0; i < defs.length; i++) if (defs[i][0] === revJumpSel.val)
      return (revJumpSel.dim === 'sugg' ? '建议 · ' : '决策 · ') + defs[i][1];
    return revJumpSel.val;
  }
  function revJumpRenderSel() {  // 渲染/恢复跳转状态下拉（面板重建后幂等）
    var sel = revVis('slirn-rev-jump-sel');
    if (!sel) return;
    if (!revJumpSel) revJumpSel = revJumpSelLoad() || { dim: 'dec', val: 'pending' };
    if (!sel.options.length) {
      var html = '<optgroup label="建议状态"><option value="sugg:all">建议 · 全部</option>';
      REV_FILTER_SUGG.forEach(function(d) {
        html += '<option value="sugg:' + d[0] + '">建议 · ' + d[1] + '</option>';
      });
      html += '</optgroup><optgroup label="决策状态"><option value="dec:all">决策 · 全部</option>';
      REV_FILTER_DEC.forEach(function(d) {
        html += '<option value="dec:' + d[0] + '">决策 · ' + d[1] + '</option>';
      });
      html += '</optgroup>';
      sel.innerHTML = html;
    }
    sel.value = revJumpSel.dim + ':' + revJumpSel.val;
    if (!sel.getAttribute('data-bound')) {  // change 监听只绑一次（面板重建后新元素重绑）
      sel.setAttribute('data-bound', '1');
      sel.addEventListener('change', function() {
        var p = (sel.value || 'dec:pending').split(':');
        revJumpSel = { dim: p[0], val: p[1] };
        revJumpSelSave();
        revJumpIdx = -1;  // 换跳转状态：锚点重置
      });
    }
  }
  function revJump(dir) {
    revJumpRenderSel();
    if (!revJumpSel) return;
    var rows = revRows();
    if (!rows.length) return;
    rows.forEach(function(r) { r.style.display = ''; });  // 显示全部：上下文可见
    var cnt = revVis('slirn-rev-filter-count');
    var matches = [];
    for (var i = 0; i < rows.length; i++)
      if (revMatchKind(rows[i], revJumpSel.dim, revJumpSel.val)) matches.push(i);
    if (!matches.length) {
      if (cnt) cnt.textContent = '「' + revJumpLabel() + '」无匹配 · 显示全部 ' + rows.length + ' 条';
      toast('没有「' + revJumpLabel() + '」状态的行', 'error');
      return;
    }
    // 起点：上次跳转行 → 键盘选中行 → 首条（下一条）/ 末条（上一条）
    var start = (revJumpIdx >= 0 && revJumpIdx < rows.length) ? revJumpIdx : revSelIndex(rows);
    var cur = matches.indexOf(start);
    var nextPos = cur >= 0 ? (cur + dir + matches.length) % matches.length
                           : (dir > 0 ? 0 : matches.length - 1);
    if (cur >= 0 && matches.length > 1 &&
        ((dir > 0 && nextPos === 0) || (dir < 0 && nextPos === matches.length - 1)))
      toast('已到「' + revJumpLabel() + '」的' + (dir > 0 ? '最后' : '第一') + '一条，继续将循环跳转');
    var idx = matches[nextPos];
    revJumpIdx = idx;
    revMarkSel(rows[idx]);
    if (rows[idx].scrollIntoView)
      rows[idx].scrollIntoView({block: 'center', behavior: 'smooth'});
    var v = revVis('slirn-rev-player');
    if (v && v.src) {  // 播放器加载过才定位（暂停时不强制播放，按空格续听）
      try { v.currentTime = (parseInt(rows[idx].getAttribute('data-start-ms'), 10) || 0) / 1000; } catch (err) {}
    }
    if (cnt) cnt.textContent = '跳转「' + revJumpLabel() + '」' + (nextPos + 1) + '/' + matches.length
      + ' · 显示全部 ' + rows.length + ' 条（上下文可见）';
  }

  // ===== 快捷键自定义（REQ-20260916-005）：localStorage 键 slirnRevKeys =====
  // 每个人习惯不同：点击提示条「⚙ 自定义」→ 点键帽 → 按新键。键位图持久化，
  // 提示条/引导 toast 跟随当前键位渲染；冲突拒绝、可恢复默认。
  var REV_KEY_ACTIONS = [
    { id: 'prev',   name: '上一条',                   def: 'arrowup' },
    { id: 'next',   name: '下一条',                   def: 'arrowdown' },
    { id: 'play',   name: '播放 / 暂停',               def: ' ' },
    { id: 'replay', name: '重播本行',                  def: 'r' },
    { id: 'keep',   name: '保留（标记后自动下一条）',   def: 'k' },
    { id: 'del',    name: '删除（标记后自动下一条）',   def: 'd' },
    { id: 'split',  name: '切分（展开详情并聚焦内容）', def: 's' },
    { id: 'esc',    name: '退出说明输入框',             def: 'escape' }
  ];
  var REV_KEY_ALLOWED = ['arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'escape',
    'enter', 'backspace', 'delete', 'home', 'end', 'pageup', 'pagedown'];  // 单字符键另判
  // REQ-20260918-042：视频播放器原生快捷键（Chrome/Edge，视频获得焦点时生效）。
  // 应用键与之重叠 → 弹窗内亮 ⚠ 徽章 + 绑定时 toast 提醒（允许绑定，仅提醒）：
  // 列表可见时应用键优先，该键在视频聚焦时不再触发原生播放控制。
  var REV_VIDEO_KEYS = {
    ' ': '播放 / 暂停', 'k': '播放 / 暂停',
    'arrowleft': '后退 5 秒', 'arrowright': '前进 5 秒',
    'j': '后退 10 秒', 'l': '前进 10 秒',
    'arrowup': '音量 +', 'arrowdown': '音量 -',
    'm': '静音切换', 'f': '全屏切换', 'c': '字幕开关',
    '0': '跳到 0%', '1': '跳到 10%', '2': '跳到 20%', '3': '跳到 30%',
    '4': '跳到 40%', '5': '跳到 50%', '6': '跳到 60%', '7': '跳到 70%',
    '8': '跳到 80%', '9': '跳到 90%',
    'home': '回到开头', 'end': '跳到片尾'
  };
  function revKeysLoad() {
    var km = {};
    REV_KEY_ACTIONS.forEach(function(a) { km[a.id] = a.def; });
    try {
      var raw = JSON.parse(localStorage.getItem('slirnRevKeys') || 'null');
      if (raw && typeof raw === 'object') {
        REV_KEY_ACTIONS.forEach(function(a) {
          var v = raw[a.id];
          // 脏数据（非字符串/重复键）→ 该动作回退默认，先到先得
          if (typeof v === 'string' && v) {
            var used = REV_KEY_ACTIONS.some(function(b) { return km[b.id] === v; });
            if (!used) km[a.id] = v;
          }
        });
      }
    } catch (err) {}
    return km;
  }
  function revKeyLabel(k) {
    var map = { ' ': '空格', 'arrowup': '↑', 'arrowdown': '↓', 'arrowleft': '←',
      'arrowright': '→', 'escape': 'Esc', 'enter': 'Enter', 'backspace': '⌫',
      'delete': 'Del', 'home': 'Home', 'end': 'End', 'pageup': 'PgUp', 'pagedown': 'PgDn' };
    var s = map[k] || ((k && k.length === 1) ? k.toUpperCase() : k);
    return String(s).replace(/[&<>"']/g, function(c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  // 提示条跟随当前键位重渲染（详情页/工作台两份一并更新）
  function applyRevKeysState() {
    var km = revKeysLoad();
    document.querySelectorAll('.slirn-rev-kbhint').forEach(function(el) {
      el.innerHTML = '⌨ 快捷键：<kbd>' + revKeyLabel(km.prev) + '</kbd><kbd>'
        + revKeyLabel(km.next) + '</kbd> 上一条 / 下一条 · <kbd>' + revKeyLabel(km.play)
        + '</kbd> 播放 / 暂停 · <kbd>' + revKeyLabel(km.replay) + '</kbd> 重播本行 · <kbd>'
        + revKeyLabel(km.keep) + '</kbd> 保留 · <kbd>' + revKeyLabel(km.del)
        + '</kbd> 删除（标记后自动下一条）· <kbd>' + revKeyLabel(km.split)
        + '</kbd> 切分（展开详情聚焦内容） · <kbd>' + revKeyLabel(km.esc)
        + '</kbd> 退出输入框'
        + ' · <span class="slirn-revkeys-open" data-action="revkeys-open" role="button" tabindex="0">⚙ 自定义</span>';
    });
  }
  var revKeysRec = null;  // 正在录制换绑的动作 id（null = 未在录制）
  function revKeysModalOpen() { return !!document.getElementById('slirn-revkeys-modal'); }
  function revKeysOpenModal() {
    if (revKeysModalOpen()) return;
    var bd = document.createElement('div');
    bd.id = 'slirn-revkeys-modal';
    bd.className = 'slirn-revkeys-backdrop';
    bd.innerHTML = '<div class="slirn-revkeys-modal" role="dialog" aria-label="快捷键自定义">'
      + '<div class="slirn-revkeys-title">⌨ 快捷键自定义'
      + '<span class="slirn-revkeys-sub">点击键帽 → 按新键更换</span></div>'
      + '<div class="slirn-revkeys-rows"></div>'
      + '<div class="slirn-revkeys-vlist"></div>'
      + '<div class="slirn-revkeys-foot">'
      + '<span class="slirn-revkeys-tip">Esc 取消录制 · 不支持组合键</span>'
      + '<button class="slirn-btn" data-action="revkeys-reset">↩️ 恢复默认</button>'
      + '<button class="slirn-btn slirn-btn-primary" data-action="revkeys-close">✅ 完成</button>'
      + '</div></div>';
    bd.addEventListener('click', function(ev) { if (ev.target === bd) revKeysCloseModal(); });
    document.body.appendChild(bd);
    revKeysRec = null;
    revKeysRenderRows();
    revKeysRenderVideo();
  }
  function revKeysCloseModal() {
    var m = document.getElementById('slirn-revkeys-modal');
    if (m) m.remove();
    revKeysRec = null;
  }
  function revKeysRenderRows(flashConflict) {
    var box = document.querySelector('#slirn-revkeys-modal .slirn-revkeys-rows');
    if (!box) return;
    var km = revKeysLoad();
    var html = '';
    REV_KEY_ACTIONS.forEach(function(a) {
      var cls = 'slirn-revkeys-key';
      if (revKeysRec === a.id) cls += ' rec';
      if (flashConflict === a.id) cls += ' conflict';
      // REQ-20260918-042：当前键与视频播放键重叠 → 行内 ⚠ 徽章
      var vhit = REV_VIDEO_KEYS[km[a.id]];
      html += '<div class="slirn-revkeys-row"><span class="slirn-revkeys-name">' + a.name
        + (vhit ? '<i class="slirn-revkeys-vwarn">⚠ 视频键 · ' + vhit + '</i>' : '')
        + '</span><span class="' + cls + '" data-revkey="' + a.id + '">'
        + (revKeysRec === a.id ? '按新键…' : revKeyLabel(km[a.id])) + '</span></div>';
    });
    box.innerHTML = html;
  }
  // REQ-20260918-042：视频播放快捷键对照表（每个键什么意思 + 是否已被应用键占用）
  function revKeysRenderVideo(hlKey) {
    var box = document.querySelector('#slirn-revkeys-modal .slirn-revkeys-vlist');
    if (!box) return;
    var km = revKeysLoad();
    var html = '<div class="slirn-revkeys-vtitle">▶ 视频播放快捷键'
      + '<span class="slirn-revkeys-vsub">视频画面获得焦点时生效（Chrome/Edge 原生播放器）</span></div>'
      + '<div class="slirn-revkeys-vgrid">';
    Object.keys(REV_VIDEO_KEYS).forEach(function(k) {
      var usedBy = '';
      REV_KEY_ACTIONS.forEach(function(a) {
        if (!usedBy && km[a.id] === k) usedBy = a.name;
      });
      html += '<span class="slirn-revkeys-vitem' + (hlKey === k ? ' hl' : '')
        + (usedBy ? ' used' : '') + '"><kbd>' + revKeyLabel(k) + '</kbd>'
        + REV_VIDEO_KEYS[k]
        + (usedBy ? '<em>已绑：' + usedBy + '</em>' : '') + '</span>';
    });
    box.innerHTML = html + '</div>'
      + '<div class="slirn-revkeys-vnote">⚠ 把上表中的键绑成应用快捷键时：列表可见则应用动作优先，'
      + '该键在视频聚焦时不再触发原生播放控制 — 绑定会弹提醒，可自行权衡。</div>';
  }
  // 录制监听（capture）：弹窗打开时接管全部按键，列表快捷键让位
  document.addEventListener('keydown', function(e) {
    if (!revKeysModalOpen()) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.ctrlKey || e.metaKey || e.altKey) {
      toast('❌ 不支持组合键（避免与浏览器/系统冲突）', 'error');
      return;
    }
    if (e.key === 'Shift' || e.key === 'Control' || e.key === 'Alt'
      || e.key === 'Meta' || e.key === 'Dead') return;  // 等待实际按键
    if (e.repeat) return;
    if (!revKeysRec) { if (e.key === 'Escape') revKeysCloseModal(); return; }
    // Esc 取消录制；但 esc 动作本身允许绑 Esc（否则永远绑不回去）
    if (e.key === 'Escape' && revKeysRec !== 'esc') {
      revKeysRec = null;
      revKeysRenderRows();
      return;
    }
    var k = (e.key || '').toLowerCase();
    var okChar = (k.length === 1 && /[\x20-\x7e]/.test(k));  // 可打印 ASCII 单键
    if (!okChar && REV_KEY_ALLOWED.indexOf(k) < 0) {
      toast('❌ 该键不可用作快捷键', 'error');
      return;
    }
    var km = revKeysLoad();
    for (var i = 0; i < REV_KEY_ACTIONS.length; i++) {
      var a = REV_KEY_ACTIONS[i];
      if (a.id !== revKeysRec && km[a.id] === k) {
        revKeysRenderRows(a.id);  // 冲突行闪红
        toast('❌ 「' + revKeyLabel(k) + '」已用于「' + a.name + '」，请换一个键', 'error');
        return;
      }
    }
    km[revKeysRec] = k;
    try { localStorage.setItem('slirnRevKeys', JSON.stringify(km)); } catch (err) {}
    var boundName = '';
    REV_KEY_ACTIONS.forEach(function(a) { if (a.id === revKeysRec) boundName = a.name; });
    revKeysRec = null;
    revKeysRenderRows();
    revKeysRenderVideo(k);  // 对照表同步「已绑」标记
    applyRevKeysState();
    // REQ-20260918-042：与视频播放键重叠 → 提醒（允许绑定；列表可见时应用键优先）
    if (REV_VIDEO_KEYS[k]) {
      toast('⚠️ 「' + revKeyLabel(k) + '」是视频播放快捷键（' + REV_VIDEO_KEYS[k]
        + '）—「' + boundName + '」绑定后，视频聚焦时此键将优先执行应用动作', 'warning');
    }
  }, true);

  document.addEventListener('keydown', function(e) {
    if (revKeysModalOpen()) return;  // 自定义弹窗打开 → 录制监听器接管
    // 带修饰键的组合留给浏览器/系统
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = (e.key || '').toLowerCase();
    var km = revKeysLoad();
    // 退回键：手动说明输入框 → 退回列表快捷键状态（焦点在输入框内也生效，键可改绑）
    if (k === km.esc) {
      var ae = document.activeElement;
      if (ae && ae.classList && ae.classList.contains('slirn-rev-note-input')) {
        ae.blur();
        e.preventDefault();
      }
      return;
    }
    // 文本输入中不劫持（下拉的方向键保留原生行为）
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    var rows = revNavRows();  // 快捷键只作用于过滤后可见的行（REQ-20260916-010）
    if (!rows.length) return;  // 修订列表不可见/无行 → 快捷键不生效
    // 反查当前键位对应的动作（按动作表顺序，先定义者优先）
    var act = null;
    for (var i = 0; i < REV_KEY_ACTIONS.length; i++) {
      if (km[REV_KEY_ACTIONS[i].id] === k) { act = REV_KEY_ACTIONS[i].id; break; }
    }
    if (!act) return;
    // 长按只放行导航（快速滚动）；其余动作防连环触发
    if (e.repeat && act !== 'prev' && act !== 'next') return;
    e.preventDefault();
    if (act === 'prev') { revSelectRow(revSelIndex(rows) - 1, true); }
    else if (act === 'next') { revSelectRow(revSelIndex(rows) + 1, true); }
    else if (act === 'play') { revTogglePlay(); }
    else if (act === 'replay') { revReplayRow(); }
    else if (act === 'keep') { revApplyDecision('keep'); }
    else if (act === 'del') { revApplyDecision('delete'); }
    else if (act === 'split') { revApplyDecision('split'); }
  });

  // ===== 切分修剪快捷键（REQ-20260916-011）：与修订区共用键位/自定义，按可见面板分发 =====
  // ↑↓ 选行（跳段起点播放）· 空格 播放/暂停 · R 重播本段（播到段尾自动停）
  // K/D/S 字幕级改判（作用于选中行所属字幕：保留/删除/切分，再按同键取消）
  // 修订列表可见时本 handler 自然让位（cutRows 为空），两区互不抢键。
  document.addEventListener('keydown', function(e) {
    if (revKeysModalOpen()) return;  // 键位自定义弹窗打开 → 录制监听器接管
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var k = (e.key || '').toLowerCase();
    var km = revKeysLoad();
    // Esc：退出重切编辑区（焦点在输入框内也生效，键可改绑）
    if (k === km.esc) {
      var aeC = document.activeElement;
      if (aeC && aeC.classList && aeC.classList.contains('slirn-cut-resplit-input')) {
        cutCloseResplit();
        e.preventDefault();
      }
      return;
    }
    var t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    var rows = cutRows();
    if (!rows.length) return;  // 切分面板不可见 → 快捷键不生效
    var act = null;
    for (var i = 0; i < REV_KEY_ACTIONS.length; i++) {
      if (km[REV_KEY_ACTIONS[i].id] === k) { act = REV_KEY_ACTIONS[i].id; break; }
    }
    if (!act || act === 'esc') return;
    if (e.repeat && act !== 'prev' && act !== 'next') return;  // 长按只放行导航
    e.preventDefault();
    if (act === 'prev') { cutSelectRow(cutSelIndex(rows) - 1, true); }
    else if (act === 'next') { cutSelectRow(cutSelIndex(rows) + 1, true); }
    else if (act === 'play') { cutTogglePlay(); }
    else if (act === 'replay') { cutReplayRow(); }
    else if (act === 'keep') { cutApplyDecision('keep'); }
    else if (act === 'del') { cutApplyDecision('delete'); }
    else if (act === 'split') { cutApplyDecision('split'); }
  });

  // REQ-20260918-055：优化字幕·替换值输入框回车 → 标记完成（采纳 + reviewed）
  // document 级委托 — wb 重渲染后元素替换不影响；严格过滤避免误吞其他 Enter
  document.addEventListener('keydown', function(e) {
    if (revKeysModalOpen()) return;  // 录制弹窗打开时让位
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (e.key !== 'Enter') return;
    var ae = e.target;
    if (!ae || !ae.classList || !ae.classList.contains('slirn-opt-after')) return;
    optOccEnterConfirm(ae, e);
  });

  function handleResp(resp, refreshCellId) {
    if (!resp) { toast('❌ 无响应', 'error'); return; }
    if (!resp.ok) { toast('❌ ' + (resp.error || '操作失败'), 'error'); return; }
    if (resp.html) refreshCell(refreshCellId, resp.html);
    if (resp.file_info) applyFileInfo(resp.file_info);
    if (resp.cut_done) applyCutDone(resp.cut_done);
    if (resp.toast) toast(resp.toast);
  }

  // ===== 全局事件委托 =====
  document.addEventListener('click', function(e) {
    // 字幕行点击（行自身无 data-action，先于 data-action 委托处理）
    var subRow = e.target.closest('.slirn-sub-row');
    if (subRow) {
      e.preventDefault();
      var tidS = subRow.getAttribute('data-task-id') || '';
      var startMs = parseInt(subRow.getAttribute('data-start-ms'), 10) || 0;
      playSubAt(tidS, startMs);
      return;
    }

    // 修订行点击连续跳播（select/input/button 与详情块上的点击不触发）：
    // 本条播完自动跳下一条起点，段间空白不播（REQ-20260917-028）
    var revRow = e.target.closest('.slirn-rev-row');
    if (revRow && !e.target.closest('select, input, button, a, .slirn-rev-detail')) {
      e.preventDefault();
      revMarkSel(revRow);  // 鼠标与键盘共享「当前行」（REQ-20260916-004）
      revPlayFrom(revRow);
      return;
    }

    // 切分修剪（REQ-20260916-011）：mark 徽章点击=翻转 · 组头试听钮=keep 连续跳播 ·
    // 行点击=段级预播（播到段尾自动停）；旧「点行续播」由预播替代
    var cutMark = e.target.closest('.slirn-cut-mark');
    if (cutMark) {
      e.preventDefault();
      var rowM = cutMark.closest('.slirn-cut-row');
      if (rowM) { cutMarkSel(rowM); cutFlipMark(rowM); }
      return;
    }
    var cutListen = e.target.closest('[data-cut-act="play-keep"]');
    if (cutListen) {
      e.preventDefault();
      cutPlayGroupKeep(cutListen.closest('.slirn-cut-group'));
      return;
    }
    // 单段重新切分（REQ-20260916-011）：入口展开 / 执行 / 取消
    var cutRsg = e.target.closest('[data-cut-act="resplit"]');
    if (cutRsg) {
      e.preventDefault();
      cutOpenResplit(cutRsg.closest('.slirn-cut-group'));
      return;
    }
    var cutRsgGo = e.target.closest('[data-cut-act="resplit-go"]');
    if (cutRsgGo) {
      e.preventDefault();
      cutDoResplit(cutRsgGo.closest('.slirn-cut-group'));
      return;
    }
    var cutRsgNo = e.target.closest('[data-cut-act="resplit-cancel"]');
    if (cutRsgNo) {
      e.preventDefault();
      cutCloseResplit();
      return;
    }
    var cutRow = e.target.closest('.slirn-cut-row');
    if (cutRow && !e.target.closest('button, a, select')) {  // select=决策下拉（REQ-20260917-027），点它只开下拉不预播
      e.preventDefault();
      cutMarkSel(cutRow);  // 鼠标与键盘共享「当前行」
      cutPreview(cutRow);
      return;
    }

    // 快捷键自定义：键帽点击进入录制（REQ-20260916-005；键帽无 data-action，先于其判断）
    var rk = e.target.closest('[data-revkey]');
    if (rk) {
      e.preventDefault();
      revKeysRec = rk.getAttribute('data-revkey');
      revKeysRenderRows();
      return;
    }

    // 状态过滤：维度切换 / 筛选项点选（REQ-20260916-010；chips 无 data-action，先于其判断）
    // 跳转状态（REQ-20260917-025）独立于筛选：这里不再触碰跳转锚点
    var fdim = e.target.closest('[data-rev-filter-dim]');
    if (fdim) {
      e.preventDefault();
      revFilter.dim = fdim.getAttribute('data-rev-filter-dim');
      revFilter.val = 'all';
      revFilterSave();
      revFilterSync();
      return;
    }
    var fval = e.target.closest('[data-rev-filter-val]');
    if (fval) {
      e.preventDefault();
      revFilter.val = fval.getAttribute('data-rev-filter-val');
      revFilterSave();
      revFilterSync();
      return;
    }
    // 状态跳转（REQ-20260917-025）：按「状态下拉」所选状态在完整列表里跳上/下一条（上下文可见）
    var fjmp = e.target.closest('[data-rev-jump]');
    if (fjmp) {
      e.preventDefault();
      revJump(fjmp.getAttribute('data-rev-jump') === 'prev' ? -1 : 1);
      return;
    }

    var target = e.target.closest('[data-action]');
    if (!target) return;
    var action = target.getAttribute('data-action');
    e.preventDefault();

    if (action === 'rigor-prompt-preset') {
      // 自定义严谨性：载入/恢复 高/中/低 任一档底稿（REQ-20260916-009）；
      // 已有编辑内容且不同 → 确认后再覆盖；草稿同步覆盖
      var wrapR = document.getElementById('slirn-rigor-custom');
      var taR = wrapR ? wrapR.querySelector('.slirn-rigor-custom-text') : null;
      if (taR) {
        var keyR = target.getAttribute('data-preset') || 'high';
        var textR = wrapR.getAttribute('data-prompt-' + keyR) || '';
        var nameR = target.textContent.trim();
        if (taR.value && taR.value !== textR &&
            !window.confirm('载入底稿「' + nameR + '」会覆盖当前编辑内容。确定继续？')) return;
        taR.value = textR;
        try { localStorage.setItem('slirnRevCustomPrompt', textR); } catch (err) {}
        taR.focus();
        toast('已载入底稿「' + nameR + '」，可在此基础上修改');
      }
      return;
    }
    if (action === 'revkeys-open') {
      revKeysOpenModal();
      return;
    }
    if (action === 'revkeys-close') {
      revKeysCloseModal();
      return;
    }
    if (action === 'vf-close') {  // 视频浮层收起（REQ-20260918-043）：暂停 + 隐藏，再播放自动弹回
      var vfL = document.getElementById('slirn-video-float');
      if (vfL) {
        vfL.querySelectorAll('video').forEach(function(vv) { try { vv.pause(); } catch (err) {} });
        vfL.hidden = true;
      }
      return;
    }
    if (action === 'col-toggle') {  // 功能区折叠（REQ-20260918-044）：收起只留胶囊头，再点展开
      var colBox = target.closest('.slirn-col');
      if (colBox) {
        var colOff = !colBox.classList.contains('off');
        colBox.classList.toggle('off', colOff);
        colSet(colBox.getAttribute('data-col-key') || '', colOff);
        var chevEl = colBox.querySelector('.slirn-col-chev');
        if (chevEl) chevEl.textContent = colOff ? '▸' : '▾';
      }
      return;
    }
    if (action === 'rev-search-prev') { searchNavTo('slirn-rev-search-q', revNavRows, -1, revMarkSel); return; }
    if (action === 'rev-search-next') { searchNavTo('slirn-rev-search-q', revNavRows, 1, revMarkSel); return; }
    if (action === 'cut-search-prev') { searchNavTo('slirn-cut-search-q', cutRows, -1, cutMarkSel); return; }
    if (action === 'cut-search-next') { searchNavTo('slirn-cut-search-q', cutRows, 1, cutMarkSel); return; }
    if (action === 'revkeys-reset') {
      try { localStorage.removeItem('slirnRevKeys'); } catch (err) {}
      revKeysRec = null;
      revKeysRenderRows();
      revKeysRenderVideo();
      applyRevKeysState();
      toast('↩️ 快捷键已恢复默认键位');
      return;
    }

    // Tab 切换
    if (TAB_BUTTONS[action]) {
      showTab(TAB_BUTTONS[action]);
      return;
    }

    if (action === 'refresh-tasks') {
      postJSON(SLIRN_API + '/refresh_tasks', {}).then(function(r) { handleResp(r, 'slirn-tab-tasks'); });
    }
    else if (action === 'refresh-wb') {
      // REQ-20260918-053 延伸：wb 内「🔄 刷新」按钮 — 不刷整页，仅重渲 wb 面板
      // 用户原话：「一刷新当前页面就回到首页，还得点列表，然后还得重新进来」
      // 配套：URL #wb= 已写入，F5 / Cmd+R 也能留在同一任务页
      var _tidR = target.getAttribute('data-task-id') || '';
      if (_tidR) openWorkbench(_tidR);
    }
    else if (action === 'view-task') {
      var tid = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/view_task', {task_id: tid}).then(function(r) {
        if (r && r.html) {
          var d = document.getElementById('slirn-tab-detail');
          if (d) { d.innerHTML = r.html; d.style.display = ''; }
          bindSubPlayer();
          bindRevPlayer();
        }
      });
    }
    else if (action === 'gen-subtitle') {
      var tidG = target.getAttribute('data-task-id') || '';
      // 同 id 复选框详情页/工作台各一份（两 tab 同在 DOM）→ 从点击按钮所在卡片内
      // 取开关，避免 getElementById 命中隐藏副本读到旧状态（REQ-20260917-029）
      var sdCard = target.closest ? target.closest('.slirn-card') : null;
      var sdEl = sdCard ? sdCard.querySelector('#slirn-sd-switch')
                        : document.getElementById('slirn-sd-switch');
      postJSON(SLIRN_API + '/gen_subtitle', {task_id: tidG, sd: sdEl ? sdEl.checked : true}).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已开始生成');
          var el = document.getElementById('slirn-asr-status');
          if (el) { el.dataset.state = 'running'; el.style.display = ''; el.innerHTML = '⏳ 已提交…'; }
          startSubPolling(tidG);
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'play-segment') {
      var tidP = target.getAttribute('data-task-id') || '';
      playSubAt(tidP, 0);
    }
    else if (action === 'play-rev-video') {
      // 连续跳播从第一条起（REQ-20260917-028）；无行兜底普通播放
      if (!revPlayFrom(revRows()[0] || null)) playRevAt(target.getAttribute('data-task-id') || '', 0);
    }
    else if (action === 'play-cut-video') {
      playCutAt(target.getAttribute('data-task-id') || '', 0);
    }
    else if (action === 'build-cutlist') {
      // 生成切分清单并完成本阶段（REQ-20260916-008）：落盘 + 推进 ROUGH_CUT_DONE，
      // openWorkbench 全刷新（阶段条 rough_cut → done、精剪字幕 → current）
      var tidC = target.getAttribute('data-task-id') || '';
      postJSON(SLIRN_API + '/build_cutlist', {task_id: tidC})
        .then(function(r) {
          if (r && r.ok) {
            toast(r.toast || '切分清单已生成');
            openWorkbench(tidC);
          } else if (r && r.error) {
            toast('❌ ' + r.error, 'error');
          }
        });
    }
    else if (action === 'save-cut-decisions') {
      // 保存切分决策（REQ-20260916-011）：翻转 manual_marks + 字幕级改判 actions
      cutSave(target);
    }
    else if (action === 'cut-spk-link') {
      // 关联人员ID（REQ-20260917-031）：时间段重叠对齐 spk → 徽章 + 统计条
      cutSpkLink(target);
    }
    else if (action === 'cut-spk-prev') {
      cutSpkNav(-1);
    }
    else if (action === 'cut-spk-next') {
      cutSpkNav(1);
    }
    else if (action === 'cut-spk-delete') {
      cutSpkDelete();
    }
    else if (action === 'cut-spk-chip') {
      // 统计条 chip 点击 → 填入查找框（服务端渲染 / JS 重渲染的 chips 都走此委托）
      var qEl = document.getElementById('slirn-cut-spk-q');
      if (qEl) { qEl.value = target.getAttribute('data-spk') || ''; qEl.focus(); }
    }
    else if (action === 'cut-spk-recount') {
      // 重新统计：按当前 DOM 去留现算（未保存改判也计入）；未关联过 → 引导
      var spkBarEl = document.getElementById('slirn-cut-spk-bar');
      if (!spkBarEl || spkBarEl.dataset.linked !== '1') {
        toast('先点「👤 关联人员ID」建立人员关联', 'error');
      } else {
        cutSpkBarRender();
        toast('🧮 已按当前去留状态重新统计');
      }
    }
    else if (action === 'rev-spk-link') {
      // 字幕修订阶段关联人员ID（REQ-20260919-068）：时间重叠 → 行徽章 + 统计条
      revSpkLink(target);
    }
    else if (action === 'rev-spk-prev') {
      revSpkNav(-1);
    }
    else if (action === 'rev-spk-next') {
      revSpkNav(1);
    }
    else if (action === 'rev-spk-delete') {
      revSpkDelete();
    }
    else if (action === 'rev-spk-chip') {
      // 统计条 chip 点击 → 填入查找框
      var revQEl = document.getElementById('slirn-rev-spk-q');
      if (revQEl) { revQEl.value = target.getAttribute('data-spk') || ''; revQEl.focus(); }
    }
    else if (action === 'rev-spk-recount') {
      // 重新统计：按当前 DOM 决策现算（未保存改判也计入）；未关联过 → 引导
      var revSpkBarEl = document.getElementById('slirn-rev-spk-bar');
      if (!revSpkBarEl || revSpkBarEl.dataset.linked !== '1') {
        toast('先点「👤 关联人员ID」建立人员关联', 'error');
      } else {
        revSpkBarRender();
        toast('🧮 已按当前决策状态重新统计');
      }
    }
    else if (action === 'rev-batch-apply') {
      revBatchApply();  // REQ-20260918-039：字幕修改批量改判
    }
    else if (action === 'cut-batch-apply') {
      cutBatchApply();  // REQ-20260918-039：切分修剪批量改判
    }
    else if (action === 'compose-rough') {
      // 粗剪合成（REQ-20260916-016，可选）：后台拼接保留区间成片
      rcCompose(target);
    }
    else if (action === 'compose-rough-delete') {
      // 删除粗剪成片（REQ-20260916-019）：confirm → POST → 刷新工作台
      rcDelete(target);
    }
    else if (action === 'compose-rough-subs-copy') {
      // 复制有效字幕 SRT（REQ-20260916-019）：从 <pre> 读 textContent
      rcSubsCopy(target);
    }
    else if (action === 'compose-rough-subs-download') {
      // 下载有效字幕 SRT（REQ-20260916-019）：拉端点拿 SRT → Blob 下载
      rcSubsDownload(target);
    }
    else if (action === 'optimize-start') {
      // 优化字幕（REQ-20260917-030）：成片重识别 + 大模型分析 → 轮询 → 刷新
      optStart(target);
    }
    else if (action === 'opt-occ-toggle') {
      optOccToggle(target);
    }
    else if (action === 'opt-word') {
      optWordFilter(target);
    }
    else if (action === 'opt-word-filter') {
      optWordFilterBtn(target);  // REQ-038：词列表按 处理完成 状态过滤
    }
    else if (action === 'opt-word-text-clear') {
      // REQ-20260918-057B：清空文字过滤
      var _inp = document.getElementById('slirn-opt-word-text');
      if (_inp) {
        _inp.value = '';
        optWordTextFilter();
        _inp.focus();
      }
    }
    else if (action === 'fine-upload') {
      // REQ-20260919-061：上传精剪视频素材
      fineUpload(target, target.getAttribute('data-kind') || '');
    }
    else if (action === 'fine-source-auto') {
      // REQ-20260919-061：从上游自动获取（与手动上传按钮并行可用）
      fineSourceAuto(target, target.getAttribute('data-kind') || '');
    }
    else if (action === 'fine-mat-preview') {
      // REQ-20260919-063：每个素材的预览按钮（图片/视频/音频/SRT）
      var _kind = target.getAttribute('data-kind') || '';
      var _tid = target.getAttribute('data-task-id') || _fineTid();
      if (typeof fineMaterialPreview === 'function') fineMaterialPreview(_tid, _kind);
    }
    else if (action === 'fine-mat-detail') {
      // REQ-20260920-088：素材路径详情按钮（弹窗显示完整路径 + 来源色块 + 文件元数据）
      var _mdKind = target.getAttribute('data-kind') || '';
      var _mdTid = target.getAttribute('data-task-id') || _fineTid();
      if (_mdKind && _mdTid && typeof fineMatDetail === 'function') {
        fineMatDetail(_mdTid, _mdKind);
      }
    }
    else if (action === 'mat-detail-close') {
      // REQ-20260920-088：关闭素材路径详情模态框
      var _mdModal = document.getElementById('slirn-mat-detail-modal');
      if (_mdModal) _mdModal.hidden = true;
    }
    else if (action === 'fine-crop-preset') {
      // REQ-20260919-061：视频源裁剪预设（全幅/16:9/1:1）
      fineCropPreset(target, target.getAttribute('data-preset') || '');
    }
    else if (action === 'fine-scale-auto') {
      // REQ-20260919-062 v14：缩放 = crop_w / 1920（视频原裁剪宽度 / 背景图片宽度）
      fineScaleAutoFromCrop();
    }
    else if (action === 'fine-save-all') {
      // REQ-20260919-061 用户反馈：手动保存参数 + 模板（顶部操作栏）
      // showToast=true（用户主动点） + asTemplate=true（若没填名会弹窗要求填）
      if (typeof fineSaveAll === 'function') fineSaveAll(true, true);
    }
    else if (action === 'fine-import-show') {
      // REQ-20260919-061 用户反馈：弹出引用参数 modal（从全局模板列表里选）
      if (typeof fineImportShow === 'function') fineImportShow();
    }
    else if (action === 'fine-import-close') {
      if (typeof fineImportClose === 'function') fineImportClose();
    }
    else if (action === 'fine-import-apply') {
      var _impPid = target.getAttribute('data-profile-id') || '';
      var _impRow = target.closest('.slirn-fine-import-row');
      var _impName = _impRow ? (_impRow.querySelector('.slirn-fine-import-name') || {}).textContent || '' : '';
      if (typeof fineProfileApply === 'function') fineProfileApply(_impPid, _impName);
    }
    else if (action === 'fine-import-delete') {
      var _impDelPid = target.getAttribute('data-profile-id') || '';
      if (typeof fineProfileDelete === 'function') fineProfileDelete(_impDelPid);
      // 删除后刷新 modal 列表
      if (typeof fineImportShow === 'function') fineImportShow();
    }
    else if (action === 'fine-import-rename') {
      var _impRenPid = target.getAttribute('data-profile-id') || '';
      var _impRenRow = target.closest('.slirn-fine-import-row');
      var _impRenName = _impRenRow ? (_impRenRow.querySelector('.slirn-fine-import-name') || {}).textContent || '' : '';
      if (typeof fineProfileRename === 'function') fineProfileRename(_impRenPid, _impRenName);
      if (typeof fineImportShow === 'function') fineImportShow();
    }
    else if (action === 'fine-import-export') {
      // REQ-20260919-070：导出单个全局模板参数为 JSON 文件。
      // 与「📤 导出参数」（任务级）同口径：后端返 {filename, content, mime} →
      // 前端 Blob + <a download> 触发下载。
      var _expBtn = target;
      var _expPid = _expBtn.getAttribute('data-profile-id') || '';
      var _expOldText = _expBtn.textContent;
      _expBtn.disabled = true;
      _expBtn.textContent = '⏳ 导出中...';
      fetch('/slirn/api/export_fine_global_profile', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({profile_id: _expPid})
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          _expBtn.disabled = false;
          _expBtn.textContent = _expOldText;
          if (!j.ok) { toast('❌ ' + (j.error || '导出失败')); return; }
          try {
            var blob = new Blob([j.content], {type: j.mime || 'application/json'});
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = j.filename || ('fine_params_profile_' + _expPid + '.json');
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            toast('✅ 已导出模板参数 → ' + a.download);
          } catch (e) {
            toast('❌ 触发下载失败: ' + e.message);
          }
        })
        .catch(function(e) {
          _expBtn.disabled = false;
          _expBtn.textContent = _expOldText;
          toast('❌ 网络错误: ' + e.message);
        });
    }
    else if (action === 'fine-export-params') {
      // REQ-20260919-065：导出当前任务的精剪参数为 JSON 文件（不含素材二进制）
      var _b = target;
      var _tid = _b.getAttribute('data-task-id') || '';
      var _oldText = _b.textContent;
      _b.disabled = true;
      _b.textContent = '⏳ 导出中...';
      fetch('/slirn/api/export_fine_params', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_id: _tid})
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          _b.disabled = false;
          _b.textContent = _oldText;
          if (!j.ok) {
            toast('❌ ' + (j.error || '导出失败'));
            return;
          }
          // 触发浏览器下载（Blob + <a download>）
          try {
            var blob = new Blob([j.content], {type: j.mime || 'application/json'});
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = j.filename || ('fine_params_' + _tid + '.json');
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            toast('✅ 参数已导出到 ' + a.download);
          } catch (e) {
            toast('❌ 触发下载失败: ' + e.message);
          }
        })
        .catch(function(e) {
          _b.disabled = false;
          _b.textContent = _oldText;
          toast('❌ 网络错误: ' + e.message);
        });
    }
    else if (action === 'fine-import-params') {
      // REQ-20260919-065：从 JSON 文件导入参数（覆盖当前参数；不动素材）
      var _b = target;
      var _tid = _b.getAttribute('data-task-id') || '';
      var _oldText = _b.textContent;
      _b.disabled = true;
      _b.textContent = '⏳ 选择文件中...';
      // 动态创建 input[type=file] 触发选择器
      var input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json,application/json';
      input.style.display = 'none';
      input.addEventListener('change', function(e) {
        var file = e.target.files && e.target.files[0];
        if (!file) {
          _b.disabled = false;
          _b.textContent = _oldText;
          return;
        }
        // 64KB 上限（实际参数远低于此）
        if (file.size > 64 * 1024) {
          toast('❌ 文件过大（>' + (file.size / 1024).toFixed(1) + 'KB），拒绝导入');
          _b.disabled = false;
          _b.textContent = _oldText;
          return;
        }
        var reader = new FileReader();
        reader.onload = function(ev) {
          _b.textContent = '⏳ 导入中...';
          fetch('/slirn/api/import_fine_params', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({task_id: _tid, content: ev.target.result})
          })
            .then(function(r) { return r.json(); })
            .then(function(j) {
              _b.disabled = false;
              _b.textContent = _oldText;
              if (!j.ok) {
                toast('❌ ' + (j.error || '导入失败'));
                return;
              }
              var cnt = (j.applied_fields || []).length;
              toast('✅ 已从导入文件应用参数（' + cnt + ' 个字段）');
              // 刷新整个精剪面板，让滑块/输入框反映新参数
              if (typeof renderWorkbench === 'function') {
                renderWorkbench();
              } else if (typeof wbRefresh === 'function') {
                wbRefresh();
              }
            })
            .catch(function(err) {
              _b.disabled = false;
              _b.textContent = _oldText;
              toast('❌ 网络错误: ' + err.message);
            });
        };
        reader.onerror = function() {
          _b.disabled = false;
          _b.textContent = _oldText;
          toast('❌ 读取文件失败');
        };
        reader.readAsText(file, 'utf-8');
      });
      document.body.appendChild(input);
      input.click();
      document.body.removeChild(input);
    }
    else if (action === 'fine-ai-parse') {
      // REQ-20260919-061：调多模态模型解析参考位置关系图（Phase C）
      var _ai = target;
      _ai.disabled = true;
      var _aiText = _ai.textContent;
      _ai.textContent = '🤖 解析中...';
      var _tid = _ai.getAttribute('data-task-id') || '';
      fetch('/slirn/api/parse_reference_layout', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({task_id: _tid})
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          _ai.disabled = false;
          _ai.textContent = _aiText;
          if (j.ok && j.layout) {
            // 同步填到滑块 + 复选框（x/y 是整数像素 → 不带小数；scale 浮点 → 2 位小数）
            ['video', 'subtitle', 'cover', 'bg'].forEach(function(k) {
              var v = j.layout[k];
              if (!v) return;
              ['x', 'y', 'scale'].forEach(function(axis) {
                var slider = document.getElementById('slirn-fine-' + k + '-' + axis);
                if (slider) {
                  slider.value = String(v[axis]);
                }
              });
              var ena = document.querySelector('.slirn-fine-enabled[data-key="' + k + '"]');
              if (ena) ena.checked = !!v.enabled;
            });
            toast('✅ AI 已生成布局');
          } else {
            toast('❌ ' + (j.error || '解析失败'));
          }
        }).catch(function(e) {
          _ai.disabled = false; _ai.textContent = _aiText;
          toast('❌ 网络错误: ' + e.message);
        });
    }
    else if (action === 'fine-preview' || action === 'fine-export') {
      // REQ-20260919-061 Phase B：调 ffmpeg 渲染
      var _b = target;
      if (_b.disabled) return;
      var _isPreview = (action === 'fine-preview');
      var _tid = _b.getAttribute('data-task-id') || '';

      // REQ-20260919-074：导出最终视频走异步后台任务（1-3 小时不再超时）
      if (!_isPreview) {
        var _oldText2 = _b.textContent;
        _b.disabled = true;
        _b.textContent = '💾 启动导出...';
        fetch('/slirn/api/export_fine_video', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task_id: _tid })
        })
          .then(function(r) { return r.json(); })
          .then(function(j) {
            _b.disabled = false; _b.textContent = _oldText2;
            if (!j.ok) { toast('❌ ' + (j.error || '启动失败')); return; }
            // REQ-20260920-077：改用 inline 状态元素（按钮旁），不弹模态框
            if (j.job_id && typeof startFineExportInline === 'function') {
              startFineExportInline(_tid, j.job_id, _b);
            } else {
              toast('✅ ' + (j.toast || '已启动'));
            }
          })
          .catch(function(e) {
            _b.disabled = false; _b.textContent = _oldText2;
            toast('❌ 网络错误: ' + e.message);
          });
        return;
      }

      // 预览：同步路径（≤30 秒，原逻辑不变）
      var _api = '/slirn/api/render_fine_preview';
      var _oldText = _b.textContent;
      _b.disabled = true;
      var _payload = { task_id: _tid };
      var _durEl = document.getElementById('slirn-fine-preview-duration');
      var _dur = parseFloat(_durEl && _durEl.value);
      if (!isNaN(_dur)) _payload.duration = _dur;
      // REQ-20260919-066：预览开始时间改为 时:分:秒 三段输入（默认 00:00:00）。
      var _hEl = document.getElementById('slirn-fine-preview-start-h');
      var _mEl = document.getElementById('slirn-fine-preview-start-m');
      var _sEl = document.getElementById('slirn-fine-preview-start-s');
      var _h = parseInt(_hEl && _hEl.value, 10);
      var _m = parseInt(_mEl && _mEl.value, 10);
      var _s = parseInt(_sEl && _sEl.value, 10);
      if (isNaN(_h) || _h < 0) _h = 0;
      if (isNaN(_m) || _m < 0) _m = 0;
      if (isNaN(_s) || _s < 0) _s = 0;
      if (_m > 59) _m = 59;
      if (_s > 59) _s = 59;
      var _totalSec = _h * 3600 + _m * 60 + _s;
      _payload.preview_start = _totalSec;
      var _timeStr = (_h < 10 ? '0' + _h : _h) + ':' +
                     (_m < 10 ? '0' + _m : _m) + ':' +
                     (_s < 10 ? '0' + _s : _s);
      var _durStr = isNaN(_dur) ? '10' : _dur;
      _b.textContent = '🎬 渲染中（' + _timeStr + ' 起 ' + _durStr + ' 秒）...';
      fetch(_api, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_payload)
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          _b.disabled = false; _b.textContent = _oldText;
          if (j.ok) {
            if (j.url && typeof openFinePreviewFloat === 'function') {
              openFinePreviewFloat(j.url, '🎬 合成预览（前 ' + (j.preview_dur || '?') + ' 秒）');
            }
            var _empty = document.getElementById('slirn-fine-preview-empty');
            if (_empty) _empty.style.display = 'none';
            toast('✅ ' + (j.toast || '完成'));
          } else {
            toast('❌ ' + (j.error || '渲染失败'));
          }
        }).catch(function(e) {
          _b.disabled = false; _b.textContent = _oldText;
          toast('❌ 网络错误: ' + e.message);
        });
    }
    else if (action === 'fine-export-cancel') {
      // REQ-20260920-089：独立可见的取消按钮（不依赖 status 元素 click）
      if (!confirm('确认取消当前渲染？已生成的片段会被丢弃。')) return;
      var _ceBtn = document.getElementById('slirn-fine-export-cancel-btn');
      if (_ceBtn) { _ceBtn.disabled = true; _ceBtn.setAttribute('data-state', 'cancelling'); }
      // 从 status 元素读 job_id（兜底：也可能从取消按钮的 data-job-id 读）
      var _statusEl = document.getElementById('slirn-fine-export-status');
      var _jobId = (_ceBtn && _ceBtn.getAttribute('data-job-id')) ||
                   (_statusEl && _statusEl.getAttribute('data-job-id')) || '';
      if (!_jobId) {
        toast('❌ 找不到当前 job_id（可能已完成或已取消）');
        if (_ceBtn) { _ceBtn.disabled = false; _ceBtn.setAttribute('data-state', 'idle'); }
        return;
      }
      fetch('/slirn/api/cancel_render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: _jobId })
      })
        .then(function(r) { return r.json(); })
        .then(function(j) {
          if (!j.ok) {
            toast('❌ ' + (j.error || '取消失败'));
            if (_ceBtn) { _ceBtn.disabled = false; _ceBtn.setAttribute('data-state', 'running'); }
          } else {
            toast('⏹ 已发送取消信号');
          }
        })
        .catch(function(e) {
          toast('❌ 网络错误: ' + e.message);
          if (_ceBtn) { _ceBtn.disabled = false; _ceBtn.setAttribute('data-state', 'running'); }
        });
    }
    else if (action === 'combo-apply' || action === 'combo-test' || action === 'combo-diagnose' || action === 'combo-restore') {
      // REQ-20260920-090：合成元素组合测试面板（debug）—— 5 checkbox 调试入口
      comboTestAction(action, target);
      return;
    }
    else if (action === 'opt-page-prev' || action === 'opt-page-next') {
      // REQ-20260918-050：词频列表翻页
      if (action === 'opt-page-prev' && optWordsCurrentPage > 1) optWordsCurrentPage--;
      else if (action === 'opt-page-next') {
        var _box = document.getElementById('slirn-opt-words');
        if (_box) {
          // 计数不能用 hidden class（见 paginateOptWords 注释）— 仅算 filter 后的
          var _vis = Array.prototype.slice.call(_box.querySelectorAll('.slirn-opt-word'))
            .filter(function(el){ return el.style.display !== 'none'; });
          var _pages = Math.max(1, Math.ceil(_vis.length / OPT_WORDS_PAGE_SIZE));
          if (optWordsCurrentPage < _pages) optWordsCurrentPage++;
        }
      }
      paginateOptWords();
    }
    else if (action === 'opt-filter') {
      optFilterBtn(target);
    }
    else if (action === 'save-optimize') {
      optSave(target);
    }
    else if (action === 'opt-srt-download') {
      optSrtDownload(target);
    }
    else if (action === 'rebuild-cutlist') {
      // 重新执行切分修剪（REQ-20260916-011）：按最新修订决策整单重算落盘，
      // 清除全部手工决策（与「重新分析=重置」同一哲学）；有手工修改先 confirm
      var tidRb = target.getAttribute('data-task-id') || '';
      if (cutHasManual() &&
          !window.confirm('重新执行将按最新修订决策整单重算，并清除全部手工决策（子段翻转与字幕级改判）。确定继续？')) return;
      postJSON(SLIRN_API + '/build_cutlist', {task_id: tidRb})
        .then(function(r) {
          if (r && r.ok) {
            toast(r.toast || '已按最新决策重新执行切分修剪');
            openWorkbench(tidRb);
          } else if (r && r.error) {
            toast('❌ ' + r.error, 'error');
          }
        });
    }
    else if (action === 'revise-subtitle') {
      var tidV = target.getAttribute('data-task-id') || '';
      // 严谨性级别必选（REQ-20260916-003）：先于覆盖确认 — 没选等级就不发请求
      var rigorEl = document.querySelector('input[name="slirn-rev-rigor"]:checked');
      if (!rigorEl) {
        toast('❌ 请先选择分析严谨性级别（高 / 中 / 低）', 'error');
        var det = document.getElementById('slirn-rev-rigor-box');
        if (det) det.open = true;
        var cards = document.querySelector('.slirn-rigor-cards');
        if (cards) cards.scrollIntoView({behavior: 'smooth', block: 'center'});
        return;
      }
      var payloadV = {task_id: tidV, rigor: rigorEl.value};
      if (rigorEl.value === 'custom') {  // 自定义档带上用户提示词（空白 → 服务端回退底稿）
        var wrapC = document.getElementById('slirn-rigor-custom');
        var taC = wrapC ? wrapC.querySelector('.slirn-rigor-custom-text') : null;
        payloadV.custom_prompt = taC ? taC.value : '';
      }
      try { localStorage.setItem('slirnRevRigor', rigorEl.value); } catch (err) {}
      if (target.getAttribute('data-has-revision') === '1') {
        if (!window.confirm('重新分析将覆盖现有建议，并重置全部手动决策。确定继续？')) return;
        payloadV.force = true;
      }
      postJSON(SLIRN_API + '/revise_subtitle', payloadV).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已开始分析');
          var el = document.getElementById('slirn-rev-status');
          if (el) { el.dataset.state = 'running'; el.style.display = ''; el.innerHTML = '⏳ 已提交…'; }
          startRevPolling(tidV);
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'save-revision') {
      var tidW = target.getAttribute('data-task-id') || '';
      var decisions = [];
      revRows().forEach(function(row) {  // 只收集可见列表（详情页+工作台同屏时防串数据）
        var sel = row.querySelector('.slirn-rev-select');
        var note = row.querySelector('.slirn-rev-note-input');
        if (sel) {
          decisions.push({
            i: parseInt(sel.getAttribute('data-i'), 10),
            decision: sel.value,
            user_note: note ? note.value : '',
          });
        }
      });
      if (!decisions.length) { toast('❌ 无可保存的决策行', 'error'); return; }
      postJSON(SLIRN_API + '/save_revision', {task_id: tidW, decisions: decisions})
        .then(function(r) {
          if (r && r.ok) {
            toast(r.toast || '已保存');
            openWorkbench(tidW);  // 刷新统计/阶段态
          } else if (r && r.error) {
            toast('❌ ' + r.error, 'error');
          }
        });
    }
    else if (action === 'wb-toggle-stages') {
      var hostW = document.getElementById('slirn-tab-workbench-inner');
      if (hostW) {
        var nowCollapsed = !hostW.classList.contains('wb-stages-collapsed');
        hostW.classList.toggle('wb-stages-collapsed', nowCollapsed);
        try { localStorage.setItem('slirnWbStagesCollapsed', nowCollapsed ? '1' : '0'); } catch (err) {}
      }
    }
    else if (action === 'rev-detail') {
      var rowD = target.closest('.slirn-rev-row');
      if (rowD) {
        var opened = rowD.classList.toggle('open');
        target.textContent = opened ? '▴' : '▾';
      }
    }
    else if (action === 'open-llm-settings') {
      openLLMSettings();
    }
    else if (action === 'llm-close') {
      var llmModal = document.getElementById('slirn-llm-modal');
      if (llmModal) llmModal.remove();
    }
    else if (action === 'llm-add') {
      var gv = function(elId) { return ((document.getElementById(elId) || {}).value || '').trim(); };
      var protoSel = document.getElementById('slirn-llm-in-proto');
      var visionChk = document.getElementById('slirn-llm-in-vision');
      var editing = LLM_EDIT_ID;  // 非空 = 当前是编辑模式 → 提交修改
      var payload = {
        id: gv('slirn-llm-in-id'), provider: gv('slirn-llm-in-provider'),
        base_url: gv('slirn-llm-in-url'), api_key_env: gv('slirn-llm-in-env'),
        protocol: protoSel ? protoSel.value : 'openai',
        vision: !!(visionChk && visionChk.checked),
      };
      postJSON(SLIRN_API + '/llm_config/' + (editing ? 'update' : 'add'), editing
        ? {id: editing, new_id: payload.id, provider: payload.provider,
           base_url: payload.base_url, api_key_env: payload.api_key_env,
           protocol: payload.protocol, vision: payload.vision}
        : payload).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || (editing ? '已更新' : '已添加'));
          renderLLMList(r.models || [], r.current || '');
          llmFormReset();
        } else {
          toast('❌ ' + ((r && r.error) || '操作失败'), 'error');
        }
      });
    }
    else if (action === 'llm-edit') {
      var editId = target.getAttribute('data-id') || '';
      var m = LLM_MODELS.filter(function(x) { return x.id === editId; })[0];
      if (!m) return;
      LLM_EDIT_ID = editId;
      document.getElementById('slirn-llm-in-id').value = m.id;
      document.getElementById('slirn-llm-in-provider').value = m.provider || '';
      document.getElementById('slirn-llm-in-url').value = m.base_url || '';
      document.getElementById('slirn-llm-in-env').value = m.api_key_env || '';
      var protoSel2 = document.getElementById('slirn-llm-in-proto');
      if (protoSel2) protoSel2.value = (m.protocol === 'anthropic') ? 'anthropic' : 'openai';
      var visionEditChk = document.getElementById('slirn-llm-in-vision');
      if (visionEditChk) visionEditChk.checked = !!m.vision;
      var ft = document.getElementById('slirn-llm-form-title');
      if (ft) ft.textContent = '修改模型（' + editId + '）';
      var ab = document.getElementById('slirn-llm-add-btn');
      if (ab) ab.textContent = '💾 保存修改';
      var cb = document.getElementById('slirn-llm-cancel-btn');
      if (cb) cb.style.display = '';
      var formEl = document.getElementById('slirn-llm-form');
      if (formEl && formEl.scrollIntoView) formEl.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    }
    else if (action === 'llm-cancel-edit') {
      llmFormReset();
    }
    else if (action === 'llm-use' || action === 'llm-remove') {
      var llmId = target.getAttribute('data-id') || '';
      postJSON(SLIRN_API + '/llm_config/' + action.slice(4), {id: llmId}).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '已更新');
          renderLLMList(r.models || [], r.current || '');
        } else {
          toast('❌ ' + ((r && r.error) || '操作失败'), 'error');
        }
      });
    }
    else if (action === 'llm-test') {
      var testId = target.getAttribute('data-id') || '';
      var testBox = document.getElementById('slirn-llm-test-result');
      if (testBox) { testBox.className = 'slirn-llm-test-result'; testBox.textContent = '⏳ 测试中（' + testId + ' 真实调用一次）…'; }
      postJSON(SLIRN_API + '/llm_test', {id: testId}).then(function(r) {
        if (!testBox) return;
        if (r && r.ok) {
          testBox.className = 'slirn-llm-test-result ok';
          testBox.textContent = '✅ 模型可用（' + r.model + ' · 耗时 '
            + (r.elapsed_s || 0).toFixed(1) + 's · 回复「' + (r.reply || '') + '」）';
        } else {
          testBox.className = 'slirn-llm-test-result err';
          testBox.textContent = '❌ ' + ((r && r.error) || '测试失败');
        }
      });
    }
    else if (action === 'close-detail') {
      var d = document.getElementById('slirn-tab-detail');
      if (d) d.style.display = 'none';
    }
    else if (action === 'delete-task') {
      // REQ-20260917-034：删除不可恢复 + 整目录真删 — 必须先确认（含任务名）
      var tid2 = target.getAttribute('data-task-id') || '';
      var name2 = target.getAttribute('data-task-name') || tid2;
      if (!window.confirm('🗑️ 确认删除任务「' + name2 + '」？\n\n'
          + '任务目录的全部文件（原视频链接、字幕、切分决策、合成产物等）'
          + '将从磁盘永久删除，不可恢复。')) return;
      // 释放正在预览的任务视频（Windows 下句柄未松开会 rmtree 失败）
      document.querySelectorAll('video').forEach(function(v) {
        try { v.pause(); v.removeAttribute('src'); v.load(); } catch (err) {}
      });
      postJSON(SLIRN_API + '/delete_task', {task_id: tid2}).then(function(r) {
        handleResp(r, 'slirn-tab-tasks');
        var d = document.getElementById('slirn-tab-detail');
        if (d) d.style.display = 'none';
      });
    }
    else if (action === 'edit-task') {
      var tidE = target.getAttribute('data-task-id') || '';
      // REQ-20260917-037：大任务编辑要读视频信息/热词，加载慢 — 点击即给加载态
      var origTextE = target.textContent;
      target.disabled = true;
      target.textContent = '⏳ 加载中…';
      var cEl = document.getElementById('slirn-tab-create');
      if (cEl) {
        cEl.innerHTML = '<div class="slirn-card" style="margin-top:16px;">'
          + '<div class="slirn-loading-box"><div class="slirn-spinner"></div>'
          + '<div>⏳ 正在加载任务（读取视频信息与热词，大视频稍慢）…</div></div></div>';
        cEl.style.display = '';
        ALL_TABS.forEach(function(id) {
          if (id !== 'slirn-tab-create') { var el = document.getElementById(id); if (el) el.style.display = 'none'; }
        });
      }
      var restoreTabsE = function() {
        target.disabled = false;
        target.textContent = origTextE;
        var tEl = document.getElementById('slirn-tab-tasks');
        var c2 = document.getElementById('slirn-tab-create');
        if (c2) c2.style.display = 'none';
        if (tEl) tEl.style.display = '';
      };
      postJSON(SLIRN_API + '/edit_task', {task_id: tidE}).then(function(r) {
        target.disabled = false;
        target.textContent = origTextE;
        if (r && r.ok && r.html) {
          var c = document.getElementById('slirn-tab-create');
          if (c) { c.innerHTML = r.html; c.style.display = ''; }
          ALL_TABS.forEach(function(id) {
            if (id !== 'slirn-tab-create') { var el = document.getElementById(id); if (el) el.style.display = 'none'; }
          });
          var dE = document.getElementById('slirn-tab-detail');
          if (dE) dE.style.display = 'none';
          initTaskEdit();
        } else if (r && r.error) {
          restoreTabsE();
          toast('❌ ' + r.error, 'error');
        }
      }, function() {
        restoreTabsE();
        toast('❌ 网络错误，请重试', 'error');
      });
    }
    else if (action === 'open-workbench') {
      var tidW = target.getAttribute('data-task-id') || '';
      // REQ-20260918-051：内容多的任务接口耗时长，先弹等待动画再去拉接口
      var labelW = target.getAttribute('data-task-label') || tidW;
      showWbLoader(tidW, labelW);
      openWorkbench(tidW);
    }
    else if (action === 'wb-stage') {
      switchWbPane(target.getAttribute('data-pane') || '');
    }
    else if (action === 'update-task') {
      var tidU = target.getAttribute('data-task-id') || window.slirnEditTaskId || '';
      var sU = getInput('slirn-start-box'), eU = getInput('slirn-end-box');
      var nU = getInput('slirn-task-name');
      var manualU = getInput('slirn-hotwords-manual');
      var inheritElU = document.getElementById('slirn-hw-inherit-all');
      var inheritU = inheritElU ? !!inheritElU.checked : false;
      var pickedU = collectPickedHotwords();
      postJSON(SLIRN_API + '/update_task', {
        task_id: tidU, start: sU, end: eU, name: nU,
        inherit_public: inheritU, picked_words: pickedU, manual_words: manualU,
      }).then(function(r) {
        if (r && r.ok) {
          toast(r.toast || '✅ 已保存');
          handleResp(r, 'slirn-tab-tasks');
          showTab('slirn-tab-tasks');
        } else if (r && r.error) {
          toast('❌ ' + r.error, 'error');
        }
      });
    }
    else if (action === 'set-start') {
      var seek = document.getElementById('slirn-seek');
      if (seek) document.getElementById('slirn-start-box').value = secondsToHMS(parseFloat(seek.value));
    }
    else if (action === 'set-end') {
      var seek2 = document.getElementById('slirn-seek');
      if (seek2) document.getElementById('slirn-end-box').value = secondsToHMS(parseFloat(seek2.value));
    }
    else if (action === 'cut-preview') {
      var s = getInput('slirn-start-box'), e2 = getInput('slirn-end-box');
      // 客户端校验：先看时间格式 + 开始<结束 再发请求
      var sSec = hmsToSeconds(s);
      var eSec = hmsToSeconds(e2);
      if (sSec == null) { toast('❌ 开始时间格式错误（应为 HH:MM:SS.mmm）', 'error'); return; }
      if (eSec == null) { toast('❌ 结束时间格式错误（应为 HH:MM:SS.mmm）', 'error'); return; }
      if (sSec >= eSec) { toast('❌ 开始时间必须小于结束时间（' + s + ' ≥ ' + e2 + '）', 'error'); return; }
      postJSON(SLIRN_API + '/cut_preview', {path: window.slirnSelectedFile, start: s, end: e2})
        .then(function(r) { handleResp(r, 'slirn-tab-create'); });
    }
    else if (action === 'create-task') {
      var s1 = getInput('slirn-start-box'), e1 = getInput('slirn-end-box');
      var n = getInput('slirn-task-name');
      var manual = getInput('slirn-hotwords-manual');
      var inheritEl = document.getElementById('slirn-hw-inherit-all');
      var inherit = inheritEl ? !!inheritEl.checked : false;
      var picked = collectPickedHotwords();
      postJSON(SLIRN_API + '/create_task', {
        path: window.slirnSelectedFile, start: s1, end: e1, name: n,
        inherit_public: inherit,
        picked_words: picked,
        manual_words: manual,
      }).then(function(r) {
        if (r && r.ok) {
          handleResp(r, 'slirn-tab-tasks');
          showTab('slirn-tab-tasks');
        }
      });
    }
    else if (action === 'cancel-create') {
      postJSON(SLIRN_API + '/cancel_create', {}).then(function(r) {
        handleResp(r, 'slirn-tab-tasks');
        showTab('slirn-tab-tasks');
      });
    }
    else if (action === 'hw-add') {
      var w = getInput('slirn-hw-word'), cat = getInput('slirn-hw-category');
      postJSON(SLIRN_API + '/hw_add', {word: w, category: cat})
        .then(function(r) {
          if (r && r.ok) {
            // 清空词输入框（保留分类方便连续添加同一分类的词）
            var ta = document.getElementById('slirn-hw-word');
            if (ta) ta.value = '';
          }
          handleResp(r, 'slirn-tab-hotwords');
        });
    }
    else if (action === 'hw-delete-one') {
      // 阻止冒泡到 cell 上的潜在 click 监听
      if (e) { e.stopPropagation(); }
      var wd = target.getAttribute('data-word') || '';
      if (!wd) return;
      if (!confirm('删除热词「' + wd + '」？')) return;
      postJSON(SLIRN_API + '/hw_remove', {word: wd})
        .then(function(r) { handleResp(r, 'slirn-tab-hotwords'); });
    }
    else if (action === 'hw-cat-select-all' || action === 'hw-cat-invert' || action === 'hw-cat-delete') {
      // 找当前按钮所在的分类 section
      var section = target.closest('.slirn-category-section');
      if (!section) return;
      var cells = section.querySelectorAll('.slirn-hotword-cell:not(.empty)');
      if (action === 'hw-cat-select-all') {
        cells.forEach(function(c) { c.classList.add('selected'); });
        updateDeleteCount(section);
      } else if (action === 'hw-cat-invert') {
        cells.forEach(function(c) { c.classList.toggle('selected'); });
        updateDeleteCount(section);
      } else if (action === 'hw-cat-delete') {
        var selected = section.querySelectorAll('.slirn-hotword-cell.selected');
        var words = [];
        selected.forEach(function(c) {
          var ww = c.getAttribute('data-word');
          if (ww) words.push(ww);
        });
        if (!words.length) {
          toast('请先用 全选 / 反选 勾选要删除的词', 'error');
          return;
        }
        if (!confirm('确认删除 ' + words.length + ' 个词？')) return;
        postJSON(SLIRN_API + '/hw_remove', {words: words})
          .then(function(r) { handleResp(r, 'slirn-tab-hotwords'); });
      }
    }
    else if (action === 'trigger-file') {
      // 动态创建一个临时 file input — 每次新创建才能避开 value 缓存（重选同一文件也能触发 change）
      var tmp = document.createElement('input');
      tmp.type = 'file';
      tmp.accept = '.mp4,.avi,.mkv,.mov,.webm,.ts,.mpeg,.m4v,.flv,.wmv,video/*';
      tmp.style.display = 'none';
      tmp.addEventListener('change', function() {
        var files = tmp.files;
        document.body.removeChild(tmp);
        if (!files || !files.length) return;
        var file = files[0];
        uploadFile(file).then(function(path) {
          closeUploadDialog();
          window.slirnSelectedFile = path;
          toast('✅ 上传完成，正在解析视频…');
          return postJSON(SLIRN_API + '/file_selected', {path: path});
        }).then(function(r) {
          closeUploadDialog();
          handleResp(r, 'slirn-tab-create');
        }).catch(function(err) {
          closeUploadDialog();
          toast('❌ 上传失败: ' + err, 'error');
        });
      });
      document.body.appendChild(tmp);
      tmp.click();
    }
  });

  // ===== 拖拽上传 — dropzone 区域支持把视频文件拖进来 =====
  function slirnDragUpload(file) {
    uploadFile(file).then(function(path) {
      closeUploadDialog();
      window.slirnSelectedFile = path;
      toast('✅ 上传完成，正在解析视频…');
      return postJSON(SLIRN_API + '/file_selected', {path: path});
    }).then(function(r) {
      closeUploadDialog();
      handleResp(r, 'slirn-tab-create');
    }).catch(function(err) {
      closeUploadDialog();
      toast('❌ 上传失败: ' + err, 'error');
    });
  }
  document.addEventListener('dragover', function(e) {
    var dz = e.target && e.target.closest && e.target.closest('.slirn-dropzone');
    if (!dz) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    dz.classList.add('drag-over');
  });
  document.addEventListener('dragleave', function(e) {
    var dz = e.target && e.target.closest && e.target.closest('.slirn-dropzone');
    if (dz) {
      dz.classList.remove('drag-over');
    }
  });
  document.addEventListener('drop', function(e) {
    var dz = e.target && e.target.closest && e.target.closest('.slirn-dropzone');
    if (!dz) return;
    e.preventDefault();
    dz.classList.remove('drag-over');
    var files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) {
      toast('⚠️ 未识别到文件，请拖入视频文件', 'warning');
      return;
    }
    var file = files[0];
    if (!file.type.startsWith('video/') && !/\.(mp4|avi|mkv|mov|webm|ts|mpeg|m4v|flv|wmv)$/i.test(file.name)) {
      toast('⚠️ 请拖入视频文件（mp4/avi/mkv/...）', 'warning');
      return;
    }
    slirnDragUpload(file);
  });

  // ===== 文件选择 — 改由 trigger-file 处理器动态创建 input 并就地监听 change =====
  // 不再用全局 change 监听，避免拖拽上传时双触发

  // ===== Slider / Video 同步 =====
  document.addEventListener('input', function(e) {
    if (e.target && e.target.id === 'slirn-seek') {
      var v = document.getElementById('slirn-player');
      if (v) v.currentTime = parseFloat(e.target.value);
    }
  });
  document.addEventListener('timeupdate', function(e) {
    if (e.target && e.target.id === 'slirn-player') {
      var v = e.target;
      var s = document.getElementById('slirn-seek');
      var d = document.getElementById('slirn-time-display');
      if (s) s.value = v.currentTime;
      if (d) d.textContent = secondsToHMS(v.currentTime) + ' / ' + secondsToHMS(v.duration);
      if (s && v.duration) s.max = v.duration;
    }
  });

  console.log('[slirn] router initialized (custom /slirn/api mode)');

  // ===== REQ-20260918-053：URL #wb=<tid> 持久化 — F5 / Cmd+R 自动回到 wb =====
  // 写入端：openWorkbench(tid) → history.replaceState('#wb=<tid>')
  // 读取端：DOMContentLoaded → 解析 hash → 切到任务页 + openWorkbench(tid)
  function _restoreWbFromHash() {
    var hash = (location && location.hash) ? location.hash : '';
    var m = hash.match(/^#wb=(.+)$/);
    if (!m) return;
    var tid = decodeURIComponent(m[1] || '').trim();
    if (!tid) return;
    // 切到 wb tab，再异步加载 wb 内容（顺序：tab 切换 → openWorkbench）
    try { showTab('slirn-tab-workbench'); } catch (e) {}
    // 等待 wb tab 显示后再渲染（避免 race）
    setTimeout(function() { try { openWorkbench(tid); } catch (e) {} }, 0);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _restoreWbFromHash);
  } else {
    // DOM 已就绪，立即执行（router.js 通常在 </body> 之前同步加载）
    try { _restoreWbFromHash(); } catch (e) {}
  }
})();
