// 短视频多素材混剪工作台。所有写操作都经 /slirn/api/*，继续受服务端认证与任务权限约束。
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

  function root() {
    return document.getElementById('slirn-tab-short-video');
  }

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

  function setStatus(msg, isError) {
    var el = document.getElementById('slirn-sv-render-status');
    if (!el) return;
    el.hidden = !msg;
    el.textContent = msg || '';
    el.classList.toggle('error', !!isError);
  }

  function loadList() {
    postJSON(API + '/short_video_list', {}).then(function(r) {
      if (r && r.ok) setHTML(r.html);
      else setHTML('<div class="slirn-empty">' + esc((r && r.error) || '加载失败') + '</div>');
    });
  }
  window.slirnShortVideoLoad = loadList;

  function openProject(taskId, projectId) {
    postJSON(API + '/short_video_get', {task_id: taskId, project_id: projectId}).then(function(r) {
      if (r && r.ok) setHTML(r.html);
      else toast((r && r.error) || '打开失败', 'error');
    });
  }

  function createProject() {
    var taskId = (document.getElementById('slirn-sv-create-task') || {}).value || '';
    var name = (document.getElementById('slirn-sv-create-name') || {}).value || '';
    var brief = (document.getElementById('slirn-sv-create-brief') || {}).value || '';
    var allow = !!(document.getElementById('slirn-sv-create-llm') || {}).checked;
    if (!taskId) return toast('请选择来源任务', 'error');
    postJSON(API + '/short_video_create', {
      task_id: taskId, name: name, brief: brief,
      config: {allow_external_llm: allow}
    }).then(function(r) {
      if (r && r.ok) {
        toast('项目已创建');
        openProject(taskId, r.project.id);
      } else toast((r && r.error) || '创建失败', 'error');
    });
  }

  function uploadMaterial() {
    var inner = document.getElementById('slirn-short-video-inner');
    if (!inner) return;
    var taskId = inner.getAttribute('data-task-id') || '';
    var projectId = inner.getAttribute('data-project-id') || '';
    var fileEl = document.getElementById('slirn-sv-upload-file');
    var kindEl = document.getElementById('slirn-sv-upload-kind');
    var file = fileEl && fileEl.files && fileEl.files[0];
    if (!file) return toast('请选择素材文件', 'error');
    var fd = new FormData();
    fd.append('task_id', taskId);
    fd.append('project_id', projectId);
    fd.append('kind', kindEl ? kindEl.value : 'auto');
    fd.append('file', file);
    setStatus('上传素材中…');
    postForm(API + '/short_video_upload_material', fd).then(function(r) {
      if (r && r.ok) {
        toast('素材已上传');
        openProject(taskId, projectId);
      } else {
        setStatus((r && r.error) || '上传失败', true);
        toast((r && r.error) || '上传失败', 'error');
      }
    });
  }

  function readConfig() {
    var out = {};
    document.querySelectorAll('[data-sv-config]').forEach(function(el) {
      var key = el.getAttribute('data-sv-config');
      if (el.type === 'checkbox') out[key] = el.checked;
      else if (el.type === 'number') out[key] = Number(el.value);
      else out[key] = el.value;
    });
    return out;
  }

  function readVariant(box) {
    function field(name) {
      var el = box.querySelector('.slirn-sv-form-grid [data-sv-field="' + name + '"]');
      return el ? el.value : '';
    }
    function number(name) {
      var v = Number(field(name));
      return isFinite(v) ? v : 0;
    }
    var segments = [];
    box.querySelectorAll('.slirn-sv-segment').forEach(function(row) {
      var material = row.querySelector('[data-sv-field="material_id"]');
      if (!material || !material.value) return;
      segments.push({
        id: row.getAttribute('data-seg-id') || '',
        material_id: material.value,
        start: Number((row.querySelector('[data-sv-field="start"]') || {}).value || 0),
        duration: Number((row.querySelector('[data-sv-field="duration"]') || {}).value || 5),
        transition: (row.querySelector('[data-sv-field="transition"]') || {}).value || 'fade'
      });
    });
    var overlays = [];
    box.querySelectorAll('.slirn-sv-overlay').forEach(function(row) {
      var material = row.querySelector('[data-sv-field="material_id"]');
      if (!material || !material.value) return;
      overlays.push({
        id: row.getAttribute('data-overlay-id') || '',
        material_id: material.value,
        start: Number((row.querySelector('[data-sv-field="start"]') || {}).value || 0),
        duration: Number((row.querySelector('[data-sv-field="duration"]') || {}).value || 3),
        x: Number((row.querySelector('[data-sv-field="x"]') || {}).value || 0),
        y: Number((row.querySelector('[data-sv-field="y"]') || {}).value || 0),
        scale: Number((row.querySelector('[data-sv-field="scale"]') || {}).value || 0.38)
      });
    });
    var subtitles = [];
    box.querySelectorAll('.slirn-sv-subtitle').forEach(function(row) {
      var text = (row.querySelector('[data-sv-field="text"]') || {}).value || '';
      if (!text.trim()) return;
      subtitles.push({
        start: Number((row.querySelector('[data-sv-field="start"]') || {}).value || 0),
        end: Number((row.querySelector('[data-sv-field="end"]') || {}).value || 2),
        text: text
      });
    });
    return {
      id: box.getAttribute('data-variant-id') || '',
      name: (box.querySelector('.slirn-sv-variant-head strong') || {}).textContent || '版本',
      title: field('title'),
      hook: field('hook'),
      cta: field('cta'),
      bgm_material_id: field('bgm_material_id'),
      bgm_volume_db: number('bgm_volume_db'),
      segments: segments,
      overlays: overlays,
      subtitles: subtitles
    };
  }

  function collectProject() {
    var inner = document.getElementById('slirn-short-video-inner');
    if (!inner) return null;
    var variants = [];
    inner.querySelectorAll('.slirn-sv-variant').forEach(function(box) {
      variants.push(readVariant(box));
    });
    var briefEl = document.getElementById('slirn-sv-brief');
    return {
      task_id: inner.getAttribute('data-task-id') || '',
      project_id: inner.getAttribute('data-project-id') || '',
      brief: briefEl ? briefEl.value : '',
      config: readConfig(),
      storyboard: {variants: variants}
    };
  }

  function saveProject(reload) {
    var payload = collectProject();
    if (!payload) return Promise.resolve({ok: false, error: '页面未打开'});
    return postJSON(API + '/short_video_save', payload).then(function(r) {
      if (!r || !r.ok) {
        toast((r && r.error) || '保存失败', 'error');
        return r;
      }
      if (reload !== false) {
        if (r.html) setHTML(r.html);
        else openProject(payload.task_id, payload.project_id);
      }
      return r;
    });
  }

  function aiStoryboard() {
    saveProject(false).then(function(r) {
      if (!r || !r.ok) return;
      var p = collectProject();
      setStatus('AI 分镜生成中…');
      postJSON(API + '/short_video_ai_storyboard', {
        task_id: p.task_id, project_id: p.project_id,
        brief: p.brief, config: p.config
      }).then(function(x) {
        if (x && x.ok) {
          toast('分镜已生成');
          setHTML(x.html);
        } else {
          setStatus((x && x.error) || '生成失败', true);
          toast((x && x.error) || '生成失败', 'error');
        }
      });
    });
  }

  var pollTimer = null;
  function pollJob(taskId, projectId, jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(function() {
      postJSON(API + '/short_video_render_status', {
        task_id: taskId, project_id: projectId, job_id: jobId
      }).then(function(r) {
        if (!r || !r.ok) return;
        var j = r.job || {};
        setStatus('渲染状态：' + (j.stage || j.state || '') + ' · ' + (j.progress || 0) + '%');
        if (j.state === 'done' || j.state === 'failed' || j.state === 'cancelled') {
          clearInterval(pollTimer);
          pollTimer = null;
          if (j.state === 'done') {
            toast('渲染完成');
            openProject(taskId, projectId);
          } else {
            setStatus(j.error || ('渲染' + j.state), true);
          }
        }
      });
    }, 1500);
  }

  function renderVariant(btn) {
    var variantId = btn.getAttribute('data-variant-id') || '';
    saveProject(false).then(function(r) {
      if (!r || !r.ok) return;
      var p = collectProject();
      postJSON(API + '/short_video_render', {
        task_id: p.task_id,
        project_id: p.project_id,
        variant_ids: variantId ? [variantId] : []
      }).then(function(x) {
        if (x && x.ok) {
          toast('渲染已启动');
          pollJob(p.task_id, p.project_id, x.job.job_id);
        } else toast((x && x.error) || '启动渲染失败', 'error');
      });
    });
  }

  function deleteProject(btn) {
    var taskId = btn.getAttribute('data-task-id') || '';
    var projectId = btn.getAttribute('data-project-id') || '';
    if (!window.confirm('确认删除该项目及其已生成短视频？')) return;
    postJSON(API + '/short_video_delete', {
      task_id: taskId, project_id: projectId
    }).then(function(r) {
      if (r && r.ok) loadList();
      else toast((r && r.error) || '删除失败', 'error');
    });
  }

  function addSegment(btn) {
    var box = btn.closest('.slirn-sv-variant');
    var rows = box && box.querySelector('.slirn-sv-rows');
    var existing = box && box.querySelector('.slirn-sv-segment');
    if (!rows || !existing) return toast('项目还没有可用视频素材', 'error');
    var clone = existing.cloneNode(true);
    clone.removeAttribute('data-seg-id');
    clone.querySelector('[data-sv-field="start"]').value = '0';
    clone.querySelector('[data-sv-field="duration"]').value = '5';
    rows.appendChild(clone);
  }

  function addOverlay(btn) {
    var box = btn.closest('.slirn-sv-variant');
    var sections = box ? box.querySelectorAll('.slirn-sv-subsection') : [];
    var rows = sections[1] ? sections[1].querySelector('.slirn-sv-rows') : null;
    var existing = box && box.querySelector('.slirn-sv-overlay');
    if (!rows || !existing) return toast('请先上传图片或视频 B-roll 素材', 'error');
    var clone = existing.cloneNode(true);
    clone.removeAttribute('data-overlay-id');
    rows.appendChild(clone);
  }

  function addSubtitle(btn) {
    var box = btn.closest('.slirn-sv-variant');
    var sections = box ? box.querySelectorAll('.slirn-sv-subsection') : [];
    var rows = sections[2] ? sections[2].querySelector('.slirn-sv-rows') : null;
    if (!rows) return;
    var row = document.createElement('div');
    row.className = 'slirn-sv-row slirn-sv-subtitle';
    row.innerHTML =
      '<span class="slirn-sv-row-no">字</span>' +
      '<input type="number" min="0" step="0.1" data-sv-field="start" value="0">' +
      '<input type="number" min="0.1" step="0.1" data-sv-field="end" value="2">' +
      '<input type="text" data-sv-field="text" placeholder="字幕文本">' +
      '<button class="slirn-btn-mini slirn-btn-mini-danger" data-action="sv-remove-subtitle">删</button>';
    rows.appendChild(row);
  }

  document.addEventListener('click', function(ev) {
    var btn = ev.target.closest ? ev.target.closest('[data-action]') : null;
    if (!btn) return;
    var action = btn.getAttribute('data-action');
    if (!action || action.indexOf('sv-') !== 0) return;
    ev.preventDefault();
    if (action === 'sv-refresh') return loadList();
    if (action === 'sv-create') return createProject();
    if (action === 'sv-open') {
      return openProject(btn.getAttribute('data-task-id'), btn.getAttribute('data-project-id'));
    }
    if (action === 'sv-back') return loadList();
    if (action === 'sv-upload') return uploadMaterial();
    if (action === 'sv-ai') return aiStoryboard();
    if (action === 'sv-save') return saveProject(true);
    if (action === 'sv-render') return renderVariant(btn);
    if (action === 'sv-delete') return deleteProject(btn);
    if (action === 'sv-add-segment') return addSegment(btn);
    if (action === 'sv-add-overlay') return addOverlay(btn);
    if (action === 'sv-add-subtitle') return addSubtitle(btn);
    if (action === 'sv-remove-segment' || action === 'sv-remove-overlay' || action === 'sv-remove-subtitle') {
      var row = btn.closest('.slirn-sv-row');
      if (row) row.remove();
    }
  });
})();
