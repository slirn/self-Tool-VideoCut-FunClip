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
      return '<div class="slirn-llm-item' + (isCur ? ' current' : '') + '">' +
        '<div class="slirn-llm-item-head">' +
          '<span class="slirn-llm-item-name">' + (isCur ? '⭐ ' : '') + escapeHtml(m.id) +
            '<span class="slirn-llm-item-prov">' + escapeHtml(m.provider || '') + '</span>' +
            '<span class="slirn-llm-item-proto">' + proto + '</span></span>' +
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
    postJSON(SLIRN_API + '/workbench', {task_id: tid}).then(function(r) {
      if (r && r.ok && r.html) {
        var w = document.getElementById('slirn-tab-workbench');
        if (!w) return;
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
        var optSt = revVis('slirn-opt-status');  // 优化 job 还在跑 → 恢复轮询
        if (optSt && optSt.dataset.taskId && optSt.dataset.state === 'running')
          startOptPolling(optSt.dataset.taskId);
        applyWbStagesState();  // 恢复上次收起/展开（跨刷新保持 — REQ-20260916-002）
        applyRevRigorState();  // 上次选过的严谨性级别预填（REQ-20260916-003）
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
  }

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

  // ===== 字幕播放器：定位播放 + 播放高亮跟随 =====
  function playSubAt(tid, startMs) {
    var wrap = document.getElementById('slirn-sub-player-wrap');
    var v = document.getElementById('slirn-sub-player');
    if (!v) { toast('❌ 播放器未就绪', 'error'); return; }
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
    if (wrap) wrap.style.display = '';
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
    var keepSkip = false;
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
      + '<input id="slirn-cut-spk-skipdel" type="checkbox">跳过已删除</label>'
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
  // 粗剪合成（REQ-20260916-016，可选步骤）：启动后台拼接 → 轮询进度 → 完成注入预览
  function rcFmtDur(sec) {
    var s = Math.floor(sec || 0), m = Math.floor(s / 60), h = Math.floor(m / 60);
    var two = function(n) { return (n < 10 ? '0' : '') + n; };
    return h ? h + ':' + two(m % 60) + ':' + two(s % 60) : m + ':' + two(s % 60);
  }
  function rcInjectPreview(tid, result) {
    var pane = document.getElementById('slirn-wb-pane-rough_compose');
    if (!pane) return;
    var old = pane.querySelector('#slirn-rc-player');
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
  function optOccToggle(btn) {  // 出现项采纳/不采纳（纯前端，保存时统一提交）
    var w = btn.closest('.slirn-opt-occ');
    if (!w) return;
    var now = w.getAttribute('data-applied') === '1' ? 0 : 1;
    w.setAttribute('data-applied', String(now));
    btn.textContent = now === 1 ? '✓' : '✕';
    btn.title = now === 1 ? '已采纳（保存时替换）' : '已不采纳（保留原文）';
    optOccMarkReviewed(w);  // REQ-038：明确处理过（无论采纳与否）→ 计入词进度
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
  function optWordFilter(chip) {  // 点词 → 筛选出现行并滚动到首行；再点取消
    var word = chip.getAttribute('data-word') || '';
    var list = revVis('slirn-opt-list');
    if (!list || !word) return;
    document.querySelectorAll('#slirn-opt-words .slirn-opt-chip').forEach(function(c) {
      c.classList.remove('active');
    });
    if (list.getAttribute('data-filter-word') === word) {
      list.removeAttribute('data-filter-word');
      list.querySelectorAll('.slirn-opt-row').forEach(function(row) { row.style.display = ''; });
      return;
    }
    list.setAttribute('data-filter-word', word);
    chip.classList.add('active');
    var firstHit = null;
    list.querySelectorAll('.slirn-opt-row').forEach(function(row) {
      var ws = (row.getAttribute('data-words') || '').split('\n');
      var hit = ws.indexOf(word) >= 0;
      row.style.display = hit ? '' : 'none';
      if (hit && !firstHit) firstHit = row;
    });
    if (firstHit) try { firstHit.scrollIntoView({block: 'center', behavior: 'smooth'}); } catch (err) {}
  }
  function optWordFilterBtn(btn) {  // REQ-038：词列表按处理状态过滤（全部/未完成/已完成）
    var mode = btn.getAttribute('data-mode') || 'all';
    var box = document.getElementById('slirn-opt-words');
    if (!box) return;
    box.querySelectorAll('button[data-action="opt-word-filter"]').forEach(function(b) {
      b.classList.toggle('active', b === btn);
    });
    box.querySelectorAll('.slirn-opt-word').forEach(function(r) {
      var done = r.getAttribute('data-done') === '1';
      r.style.display = (mode === 'all' || (mode === 'done') === done) ? '' : 'none';
    });
  }
  function optFilterBtn(btn) {  // 只看有不明确字词的行 / 全部行
    var list = revVis('slirn-opt-list');
    if (!list) return;
    var only = list.classList.toggle('slirn-opt-only-occ');
    btn.setAttribute('data-shown', only ? '0' : '1');
    btn.textContent = only ? '📋 显示全部识别行'
      : (btn.getAttribute('data-all-text') || '🔍 只看有不明确字词的行');
  }
  function optSave(btn) {
    var tid = btn.getAttribute('data-task-id') || '';
    var list = revVis('slirn-opt-list');
    if (!list) { toast('❌ 无优化结果列表', 'error'); return; }
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
    if (wrap) wrap.style.display = '';
    if (!v.src) {
      v.src = SLIRN_API + '/video/' + encodeURIComponent(tid) + '?src=rough_compose';
      v.load();
    }
    var goO = function() {
      try { v.currentTime = (startMs || 0) / 1000; } catch (err) {}
      var p = v.play();
      if (p && p.catch) p.catch(function() {});
    };
    if (v.readyState >= 1) goO();
    else v.addEventListener('loadedmetadata', goO, {once: true});
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
      + '<div class="slirn-revkeys-foot">'
      + '<span class="slirn-revkeys-tip">Esc 取消录制 · 不支持组合键</span>'
      + '<button class="slirn-btn" data-action="revkeys-reset">↩️ 恢复默认</button>'
      + '<button class="slirn-btn slirn-btn-primary" data-action="revkeys-close">✅ 完成</button>'
      + '</div></div>';
    bd.addEventListener('click', function(ev) { if (ev.target === bd) revKeysCloseModal(); });
    document.body.appendChild(bd);
    revKeysRec = null;
    revKeysRenderRows();
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
      html += '<div class="slirn-revkeys-row"><span class="slirn-revkeys-name">' + a.name
        + '</span><span class="' + cls + '" data-revkey="' + a.id + '">'
        + (revKeysRec === a.id ? '按新键…' : revKeyLabel(km[a.id])) + '</span></div>';
    });
    box.innerHTML = html;
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
    revKeysRec = null;
    revKeysRenderRows();
    applyRevKeysState();  // 提示条立即跟随新键位
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
    if (action === 'revkeys-reset') {
      try { localStorage.removeItem('slirnRevKeys'); } catch (err) {}
      revKeysRec = null;
      revKeysRenderRows();
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
      var editing = LLM_EDIT_ID;  // 非空 = 当前是编辑模式 → 提交修改
      var payload = {
        id: gv('slirn-llm-in-id'), provider: gv('slirn-llm-in-provider'),
        base_url: gv('slirn-llm-in-url'), api_key_env: gv('slirn-llm-in-env'),
        protocol: protoSel ? protoSel.value : 'openai',
      };
      postJSON(SLIRN_API + '/llm_config/' + (editing ? 'update' : 'add'), editing
        ? {id: editing, new_id: payload.id, provider: payload.provider,
           base_url: payload.base_url, api_key_env: payload.api_key_env,
           protocol: payload.protocol}
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
      openWorkbench(target.getAttribute('data-task-id') || '');
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
})();
