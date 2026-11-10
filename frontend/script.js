/**
 * script.js — Main application logic for Model-to-Gradio frontend.
 * Handles navigation, page rendering, component interactions, and polling.
 */

import * as API from './api.js';
import * as State from './state.js';

// ── Page titles ───────────────────────────────────────────────────────────
const PAGE_TITLES = {
  dashboard:  'Dashboard',
  analyzer:   'Model Analyzer',
  insights:   'Model Insights',
  generator:  'App Generator',
  apps:       'Generated Apps',
  deployment: 'Deployment',
  activity:   'Activity & Logs',
};

// ── Polling intervals (ms) ────────────────────────────────────────────────
const POLL_ANALYSIS   = 1500;
const POLL_LOCAL      = 2000;
const POLL_COLAB      = 3000;
const POLL_APPS       = 10000;

// ── Active poll timers ────────────────────────────────────────────────────
const _polls = {};

function startPoll(key, fn, interval) {
  stopPoll(key);
  // Don't call fn() immediately here — callers that need an immediate
  // call do it themselves. This prevents double-calling on init.
  _polls[key] = setInterval(fn, interval);
}

function stopPoll(key) {
  if (_polls[key]) { clearInterval(_polls[key]); delete _polls[key]; }
}

// ── Navigation ────────────────────────────────────────────────────────────
function navigateTo(page, opts = {}) {
  if (!PAGE_TITLES[page]) return;

  const prev = State.getCurrentPage();
  State.setCurrentPage(page);

  // Update nav items
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
    el.setAttribute('aria-current', el.dataset.page === page ? 'page' : 'false');
  });

  // Show/hide pages
  document.querySelectorAll('.page').forEach(el => {
    el.style.display = el.dataset.page === page ? '' : 'none';
  });

  // Update title
  document.getElementById('pageTitle').textContent = PAGE_TITLES[page];

  // Page-specific initialization
  if (page === 'dashboard')  initDashboard();
  if (page === 'analyzer')   initAnalyzerPage();
  if (page === 'insights')   initInsights();
  if (page === 'generator')  initGenerator();
  if (page === 'apps')       initAppsPage();
  if (page === 'deployment') initDeployment();
  if (page === 'activity')   initActivity();

  // Close mobile sidebar
  document.getElementById('sidebar')?.classList.remove('mobile-open');
}

// ── Wire navigation clicks (static nav items only) ───────────────────────
document.querySelectorAll('.nav-item[data-page]').forEach(el => {
  el.addEventListener('click', e => {
    e.preventDefault();
    navigateTo(el.dataset.page);
  });
});

// Sidebar toggle
document.getElementById('sidebarToggle')?.addEventListener('click', () => {
  const sidebar = document.getElementById('sidebar');
  const collapsed = sidebar.classList.toggle('collapsed');
  State.setSidebarCollapsed(collapsed);
});

// Mobile menu
document.getElementById('mobileMenuBtn')?.addEventListener('click', () => {
  document.getElementById('sidebar')?.classList.toggle('mobile-open');
});

// Topbar Analyze button
document.getElementById('analyzeQuickBtn')?.addEventListener('click', () => navigateTo('analyzer'));
document.getElementById('dashAnalyzeBtn')?.addEventListener('click', () => navigateTo('analyzer'));

// Notification bell
document.getElementById('notifBell')?.addEventListener('click', () => {
  const panel = document.getElementById('notifPanel');
  const isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'flex';
});
document.getElementById('notifPanelClose')?.addEventListener('click', () => {
  document.getElementById('notifPanel').style.display = 'none';
});

// ── Notifications ─────────────────────────────────────────────────────────
const NOTIF_ICONS = {
  success: `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>`,
  error:   `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>`,
  warning: `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>`,
  info:    `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/></svg>`,
};

State.subscribe('notifications', notifs => {
  const dot = document.getElementById('notifDot');
  if (dot) dot.style.display = notifs.length ? '' : 'none';

  const panel = document.getElementById('notifPanelList');
  if (panel) {
    panel.innerHTML = notifs.length
      ? notifs.map(n => `
        <div class="notif-panel-item notif-toast--${n.type}">
          <div class="notif-panel-title">${esc(n.title)}</div>
          <div class="notif-panel-msg">${esc(n.message)}</div>
          <div class="notif-panel-time">${timeAgo(n.at)}</div>
        </div>`).join('')
      : '<div style="padding:2rem;text-align:center;color:var(--text-dim);font-size:0.85rem">No notifications</div>';
  }
});

function showToast(type, title, message = '', duration = 5000) {
  const container = document.getElementById('notifContainer');
  if (!container) return;

  const el = document.createElement('div');
  el.className = `notif-toast notif-toast--${type}`;
  el.innerHTML = `
    <span class="notif-toast-icon">${NOTIF_ICONS[type] || NOTIF_ICONS.info}</span>
    <div class="notif-toast-body">
      <div class="notif-toast-title">${esc(title)}</div>
      ${message ? `<div class="notif-toast-msg">${esc(message)}</div>` : ''}
    </div>
    <button class="notif-toast-close" aria-label="Close">
      <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
    </button>`;

  const close = () => {
    el.classList.add('hiding');
    setTimeout(() => el.remove(), 200);
  };
  el.querySelector('.notif-toast-close').addEventListener('click', close);
  container.appendChild(el);
  if (duration > 0) setTimeout(close, duration);
  State.addNotification(type, title, message, 0); // record, no auto-dismiss
}

// ── Utilities ─────────────────────────────────────────────────────────────
function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function timeAgo(ts) {
  const diff = (Date.now() - ts) / 1000;
  if (diff < 60)   return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400)return `${Math.floor(diff / 3600)}h ago`;
  return new Date(ts).toLocaleDateString();
}

function formatDate(str) {
  if (!str) return '—';
  try { return new Date(str).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }); }
  catch { return str; }
}

function formatParams(n) {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(0)}M`;
  return n.toLocaleString();
}

function slugToDisplay(slug) {
  return slug.replace('github__', '').replace(/__/g, '/');
}

function statusBadgeHtml(status) {
  const map = {
    supported:              ['success', 'Supported'],
    supported_with_warnings:['warning', 'Supported with warnings'],
    unsupported:            ['error',   'Unsupported'],
    partial:                ['neutral', 'Partial'],
    generated:              ['success', 'Generated'],
    failed:                 ['error',   'Failed'],
    running:                ['success', 'Running'],
    stopped:                ['neutral', 'Stopped'],
    idle:                   ['neutral', 'Idle'],
    error:                  ['error',   'Error'],
  };
  const [cls, label] = map[status] || ['neutral', status || '?'];
  return `<span class="badge badge--${cls}">${esc(label)}</span>`;
}

function taskEmoji(task) {
  const map = {
    'text-generation': '💬', 'text2text-generation': '🔄', 'summarization': '📝',
    'translation': '🌐', 'text-classification': '🏷', 'token-classification': '🔍',
    'question-answering': '❓', 'image-classification': '🖼', 'image-to-text': '📸',
    'automatic-speech-recognition': '🎤', 'text-to-speech': '🔊', 'text-to-image': '🎨',
    'object-detection': '🎯', 'image-segmentation': '✂️', 'feature-extraction': '📊',
    'sentence-similarity': '🔗', 'fill-mask': '🎭', 'audio-classification': '🎵',
  };
  return map[task] || '⚡';
}

// ── Collapsible cards ─────────────────────────────────────────────────────
document.addEventListener('click', e => {
  const header = e.target.closest('.card-header.collapsible');
  if (!header) return;
  const target = document.getElementById(header.dataset.target);
  if (!target) return;
  const isOpen = target.style.display !== 'none';
  target.style.display = isOpen ? 'none' : '';
  header.classList.toggle('open', !isOpen);
});

// ── Global delegated click handler for data-page buttons in dynamic HTML ──
// Static nav items are wired at load time. But buttons injected into dynamic
// content (dashboard cards, app detail, etc.) also use data-page and need
// this delegated listener to work.
document.addEventListener('click', e => {
  const btn = e.target.closest('[data-page]');
  // Only act if it's NOT a nav-item (those are handled at startup).
  // nav-items have class 'nav-item'; skip them here to avoid double firing.
  if (!btn || btn.classList.contains('nav-item')) return;
  const page = btn.dataset.page;
  if (page && PAGE_TITLES[page]) {
    e.preventDefault();
    navigateTo(page);
  }
});

// ── DASHBOARD ─────────────────────────────────────────────────────────────
async function initDashboard() {
  await loadAppsData();
  renderDashboard();
}

function renderDashboard() {
  const apps = State.getApps() || [];
  const activity = State.getActivity();

  // Stats
  const analyzed = apps.length;
  const generated = apps.filter(a => a.has_app).length;
  const deployed = activity.filter(a => a.type === 'deployment' && a.status === 'success').length;

  const statEl = id => document.getElementById(id);
  if (statEl('statAnalyzed')) statEl('statAnalyzed').textContent = analyzed;
  if (statEl('statGenerated')) statEl('statGenerated').textContent = generated;
  if (statEl('statDeployed')) statEl('statDeployed').textContent = deployed;

  // Update apps count badge
  const badge = document.getElementById('appsCount');
  if (badge && generated > 0) {
    badge.textContent = generated;
    badge.style.display = '';
  }

  // Recent apps
  const recentApps = document.getElementById('dashRecentApps');
  if (recentApps) {
    if (apps.length === 0) {
      recentApps.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-dim);font-size:0.83rem">No apps yet. <button class="btn btn-ghost btn-sm" onclick="window.navigateTo('analyzer')">Analyze a model</button></div>`;
    } else {
      recentApps.innerHTML = apps.slice(0, 5).map(app => `
        <div class="app-item-compact" onclick="window.navigateTo('apps');window.selectApp('${esc(app.slug)}')">
          <div class="app-icon-compact">
            <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M12.316 3.051a1 1 0 01.633 1.265l-4 12a1 1 0 11-1.898-.632l4-12a1 1 0 011.265-.633zM5.707 6.293a1 1 0 010 1.414L3.414 10l2.293 2.293a1 1 0 11-1.414 1.414l-3-3a1 1 0 010-1.414l3-3a1 1 0 011.414 0zm8.586 0a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 11-1.414-1.414L16.586 10l-2.293-2.293a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
          </div>
          <div>
            <div class="app-name-compact">${esc(slugToDisplay(app.slug))}</div>
            <div class="app-task-compact">${esc(app.task || '—')}</div>
          </div>
          ${statusBadgeHtml(app.has_app ? 'generated' : 'partial')}
          <div class="app-date-compact">${formatDate(app.created_at)}</div>
        </div>`).join('');
    }
  }

  // Recent activity
  const actEl = document.getElementById('dashActivity');
  if (actEl) {
    if (activity.length === 0) {
      actEl.innerHTML = `<div style="padding:2rem;text-align:center;color:var(--text-dim);font-size:0.83rem">No activity yet.</div>`;
    } else {
      actEl.innerHTML = activity.slice(0, 8).map(ev => `
        <div class="activity-item">
          <div class="activity-dot activity-dot--${ev.type || 'analysis'}"></div>
          <div class="activity-body">
            <div class="activity-msg">${esc(ev.message || ev.title || '—')}</div>
            <div class="activity-time">${timeAgo(ev.timestamp ? new Date(ev.timestamp).getTime() : Date.now())}</div>
          </div>
          ${statusBadgeHtml(ev.status)}
        </div>`).join('');
    }
  }
}

// ── MODEL ANALYZER ────────────────────────────────────────────────────────
let _analysisJobInterval = null;

function initAnalyzerPage() {
  const urlInput = document.getElementById('analyzerUrl');
  const analyzeBtn = document.getElementById('analyzerBtn');

  if (urlInput && !urlInput._wired) {
    urlInput._wired = true;
    urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') startAnalysis(); });
    analyzeBtn.addEventListener('click', startAnalysis);

    // Example chips
    document.querySelectorAll('.example-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        urlInput.value = chip.dataset.url;
        startAnalysis();
      });
    });

    // Go to insights / generator buttons
    document.getElementById('goToInsightsBtn')?.addEventListener('click', () => navigateTo('insights'));
    document.getElementById('goToGeneratorBtn')?.addEventListener('click', () => navigateTo('generator'));
  }

  // Restore previous analysis if any
  const analysis = State.getSelectedAnalysis();
  if (analysis && State.getSelectedSlug()) {
    showAnalysisResult(State.getSelectedSlug(), analysis);
  }
}

function startAnalysis() {
  const url = document.getElementById('analyzerUrl')?.value?.trim();
  if (!url) {
    showAnalyzerError('Please enter a model URL.');
    return;
  }

  clearAnalyzerUI();
  showAnalysisProgress('Starting…', 'Connecting to backend', 5);

  API.startAnalysis(url)
    .then(res => {
      if (res.job_id) {
        pollAnalysisJob(res.job_id);
      } else if (res.result) {
        // synchronous result
        handleAnalysisResult(res.result);
      } else {
        showAnalyzerError(res.error || 'Unknown error starting analysis');
      }
    })
    .catch(err => showAnalyzerError(err.message));
}

function pollAnalysisJob(jobId) {
  let pct = 10;
  const stages = ['parse', 'inspect', 'rea', 'analyze', 'generate', 'write', 'done'];
  const stageLabels = {
    parse: 'Parsing URL', inspect: 'Inspecting repository',
    rea: 'Inspecting model weights (REA)', analyze: 'Analyzing capabilities',
    generate: 'Generating Gradio app', write: 'Writing output files', done: 'Done',
  };

  if (_analysisJobInterval) clearInterval(_analysisJobInterval);

  _analysisJobInterval = setInterval(async () => {
    try {
      const status = await API.getAnalysisStatus(jobId);
      const stage = status.stage || 'analyzing';
      const label = stageLabels[stage] || stage;
      const stageIdx = stages.indexOf(stage);
      pct = stageIdx >= 0 ? Math.min(95, 10 + (stageIdx / stages.length) * 85) : Math.min(pct + 5, 90);

      showAnalysisProgress('Analyzing…', label, pct);
      renderProgressPills(stages, stageLabels, stage);

      if (status.status === 'done' || status.status === 'complete') {
        clearInterval(_analysisJobInterval);
        _analysisJobInterval = null;
        if (status.result) {
          handleAnalysisResult(status.result);
        } else {
          showAnalyzerError('Analysis completed but no result was returned.');
        }
      } else if (status.status === 'error' || status.status === 'failed') {
        clearInterval(_analysisJobInterval);
        _analysisJobInterval = null;
        showAnalyzerError(status.error || 'Analysis failed');
      }
    } catch (err) {
      clearInterval(_analysisJobInterval);
      _analysisJobInterval = null;
      showAnalyzerError(err.message);
    }
  }, POLL_ANALYSIS);
}

function handleAnalysisResult(result) {
  hideAnalysisProgress();
  const slug = result.slug || result.source?.repo_id?.replace('/', '__');
  if (!slug) { showAnalyzerError('Result has no slug.'); return; }
  State.setSelectedAnalysis(slug, result);
  State.appendActivity({ type: 'analysis', status: result.status, message: `Analyzed ${slug}`, timestamp: new Date().toISOString() });
  showAnalysisResult(slug, result);
  if (result.artifact_status === 'saved' && result.generation_status === 'completed') {
    showToast('success', 'Files successfully saved', slugToDisplay(slug));
  } else if (result.generation_status === 'blocked' || result.artifact_status === 'analysis_only') {
    showToast('info', 'Analysis completed', 'Code generation is intentionally blocked for this model.');
  } else if (result.generation_status === 'failed' || result.artifact_status === 'missing') {
    showToast('error', 'Generation failed', 'Analysis completed, but application files were not saved.', 8000);
  } else {
    showToast('info', 'Analysis completed', slugToDisplay(slug));
  }

  // Reload apps list
  loadAppsData();
}

function clearAnalyzerUI() {
  document.getElementById('analyzerResult').style.display = 'none';
  document.getElementById('analyzerError').style.display = 'none';
  document.getElementById('analyzerProgress').style.display = 'none';
  document.getElementById('analyzerUrlErr').style.display = 'none';
}

function showAnalyzerError(msg) {
  hideAnalysisProgress();
  document.getElementById('analyzerError').style.display = '';
  document.getElementById('analyzerErrorMsg').textContent = msg;
  document.getElementById('analyzerProgress').style.display = 'none';
  showToast('error', 'Analysis failed', msg, 8000);
}

function showAnalysisProgress(title, sub, pct) {
  document.getElementById('analyzerProgress').style.display = '';
  document.getElementById('analyzerProgressTitle').textContent = title;
  document.getElementById('analyzerProgressSub').textContent = sub;
  document.getElementById('analyzerProgressBar').style.width = pct + '%';
}

function hideAnalysisProgress() {
  document.getElementById('analyzerProgress').style.display = 'none';
}

function renderProgressPills(stages, labels, current) {
  const container = document.getElementById('analyzerStages');
  if (!container) return;
  const currentIdx = stages.indexOf(current);
  container.innerHTML = stages.map((s, i) => {
    const cls = i < currentIdx ? 'stage-pill done' : i === currentIdx ? 'stage-pill current' : 'stage-pill';
    const icon = i < currentIdx ? '✓' : '';
    return `<span class="${cls}">${icon ? icon + ' ' : ''}${esc(labels[s] || s)}</span>`;
  }).join('');
}

function showAnalysisResult(slug, analysis) {
  document.getElementById('analyzerResult').style.display = '';
  document.getElementById('analyzerProgress').style.display = 'none';
  document.getElementById('analyzerError').style.display = 'none';

  const displayName = slugToDisplay(slug);
  const a = analysis;

  // Status badge + name
  document.getElementById('analysisStatusBadge').innerHTML = statusBadgeHtml(a.status);
  document.getElementById('analysisModelName').textContent = a.source?.repo_id || displayName;

  const urlEl = document.getElementById('analysisModelUrl');
  if (urlEl) {
    urlEl.href = a.source?.canonical_url || `https://huggingface.co/${a.source?.repo_id}`;
    urlEl.textContent = a.source?.canonical_url || '';
  }

  // Meta grid
  const metaGrid = document.getElementById('analysisMetaGrid');
  const hw = a.hardware || {};
  metaGrid.innerHTML = [
    ['Task', a.task ? `<code>${esc(a.task)}</code>` : '—'],
    ['Confidence', a.task_confidence != null ? `${(a.task_confidence * 100).toFixed(0)}%` : '—'],
    ['Framework', esc(a.library || '—')],
    ['Parameters', hw.params ? formatParams(hw.params) + ` <small style="color:var(--text-dim)">(${esc(hw.params_source)})</small>` : '—'],
    ['License', esc(a.license || '—')],
    ['Host', esc(a.host || 'huggingface')],
    ['Chat model', a.chat ? '<span class="badge badge--info">Yes</span>' : 'No'],
    ['Revision', a.revision ? `<code>${esc(a.revision.slice(0, 12))}</code>` : '—'],
  ].map(([k, v]) => `
    <div class="meta-item">
      <div class="meta-key">${esc(k)}</div>
      <div class="meta-val">${v}</div>
    </div>`).join('');

  const pipelineStatus = document.getElementById('analysisPipelineStatus');
  if (pipelineStatus) {
    const generation = a.generation_status;
    const artifacts = a.artifact_status;
    const rows = [
      ['Analysis completed', a.analysis_status === 'completed'],
      ['Code generation started', generation === 'completed' || generation === 'blocked' || generation === 'failed'],
      ['Validation completed', generation === 'completed' && artifacts === 'saved'],
      ['Files successfully saved', artifacts === 'saved'],
    ];
    pipelineStatus.innerHTML = rows.map(([label, complete]) =>
      `<span class="pipeline-stage ${complete ? 'complete' : ''}">${complete ? '✓' : '○'} ${label}</span>`
    ).join('');
    if (generation === 'blocked') {
      pipelineStatus.innerHTML += '<span class="pipeline-stage blocked">! Code generation intentionally blocked</span>';
    } else if (generation === 'failed' || artifacts === 'missing') {
      pipelineStatus.innerHTML += '<span class="pipeline-stage failed">! Generation failed; files were not saved</span>';
    }
  }

  // Technical details
  const techContent = document.getElementById('analysisTechContent');
  const me = a.model_evidence || {};
  const cfg = me.config || {};
  const tok = me.tokenizer || {};
  techContent.innerHTML = `
    <div class="tech-grid">
      ${cfg.model_type ? `<div class="tech-item"><div class="tech-label">Model type</div><div class="tech-val">${esc(cfg.model_type)}</div></div>` : ''}
      ${(cfg.architectures || []).length ? `<div class="tech-item"><div class="tech-label">Architecture</div><div class="tech-val">${esc(cfg.architectures[0])}</div></div>` : ''}
      ${cfg.vocab_size ? `<div class="tech-item"><div class="tech-label">Vocab size</div><div class="tech-val">${cfg.vocab_size.toLocaleString()}</div></div>` : ''}
      ${cfg.hidden_size || cfg.d_model ? `<div class="tech-item"><div class="tech-label">Hidden size</div><div class="tech-val">${(cfg.hidden_size || cfg.d_model).toLocaleString()}</div></div>` : ''}
      ${tok.tokenizer_class ? `<div class="tech-item"><div class="tech-label">Tokenizer</div><div class="tech-val">${esc(tok.tokenizer_class)}</div></div>` : ''}
      ${tok.model_max_length ? `<div class="tech-item"><div class="tech-label">Max length</div><div class="tech-val">${tok.model_max_length.toLocaleString()}</div></div>` : ''}
      ${a.use_safetensors !== undefined ? `<div class="tech-item"><div class="tech-label">Weight format</div><div class="tech-val">${a.use_safetensors ? '<span class="badge badge--success">SafeTensors</span>' : '<span class="badge badge--warning">Pickle</span>'}</div></div>` : ''}
      ${a.trust_remote_code !== undefined ? `<div class="tech-item"><div class="tech-label">Remote code</div><div class="tech-val">${a.trust_remote_code ? '<span class="badge badge--error">Required</span>' : '<span class="badge badge--success">Not required</span>'}</div></div>` : ''}
    </div>
    ${a.task_evidence?.length ? `
      <h4 style="font-size:0.8rem;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin:1rem 0 0.5rem">Task Evidence</h4>
      <ul class="evidence-list">${a.task_evidence.slice(0, 5).map(ev => `
        <li class="evidence-item">
          <span class="evidence-tier tier--${ev.tier || 'structural'}">${esc(ev.tier || 'structural')}</span>
          <span>${esc(ev.signal)}: ${esc(ev.detail)} <span style="color:var(--text-dim)">(${esc(ev.source)}, w=${ev.weight})</span></span>
        </li>`).join('')}</ul>` : ''}`;

  // Capabilities
  const capsBody = document.getElementById('analysisCapsBody');
  const caps = a.capabilities || [];
  if (caps.length > 0) {
    capsBody.innerHTML = caps.map(c => `
      <span class="capability-chip cap--${c.status === 'supported' ? (c.role === 'primary' ? 'primary' : 'supported') : 'uncertain'}">
        ${taskEmoji(c.name)} ${esc(c.name)}
        <span class="cap-role">${esc(c.role)}</span>
        ${c.status === 'supported' ? '<span class="badge badge--success" style="font-size:.6rem">✓</span>' : ''}
      </span>`).join('');
  } else {
    capsBody.innerHTML = '<span style="color:var(--text-dim);font-size:0.83rem">No capabilities detected.</span>';
  }

  // Hardware
  const hwBody = document.getElementById('analysisHwBody');
  hwBody.innerHTML = `
    <div class="hw-grid">
      <div class="hw-item">
        <div class="hw-val">${esc(hw.recommended_runtime || '—')}</div>
        <div class="hw-label">Recommended Runtime</div>
      </div>
      <div class="hw-item">
        <div class="hw-val">${hw.est_vram_gb_fp16 != null ? hw.est_vram_gb_fp16 + ' GiB' : '—'}</div>
        <div class="hw-label">Est. VRAM (fp16)</div>
      </div>
      <div class="hw-item">
        <div class="hw-val">${hw.weights_gb_fp16 != null ? hw.weights_gb_fp16 + ' GB' : '—'}</div>
        <div class="hw-label">Weights (fp16)</div>
      </div>
    </div>
    <div style="margin-top:0.75rem;display:flex;gap:0.5rem;flex-wrap:wrap">
      ${hw.runs_locally_cpu ? '<span class="badge badge--success">Runs on CPU</span>' : ''}
      ${hw.fits_free_colab_t4 ? '<span class="badge badge--info">Fits T4 GPU</span>' : ''}
      ${hw.needs_gpu ? '<span class="badge badge--warning">GPU recommended</span>' : ''}
      ${hw.exceeds_colab ? '<span class="badge badge--error">Exceeds Colab</span>' : ''}
      ${a.gated ? '<span class="badge badge--warning">Gated model</span>' : ''}
    </div>
    ${hw.notes?.length ? `<p style="font-size:0.75rem;color:var(--text-dim);margin-top:0.5rem">${esc(hw.notes[0])}</p>` : ''}`;

  // Warnings
  const warnCard = document.getElementById('analysisWarnCard');
  const warnBody = document.getElementById('analysisWarnBody');
  const warnings = [...(a.warnings || []), ...(a.uncertainties || [])];
  if (warnings.length > 0) {
    warnCard.style.display = '';
    warnBody.innerHTML = `
      ${a.warnings?.length ? `<div style="margin-bottom:0.75rem"><strong style="font-size:0.8rem;color:var(--warning)">Warnings:</strong><ul style="margin-top:0.4rem">${a.warnings.map(w => `<li style="font-size:0.8rem;color:var(--text-muted);padding:0.2rem 0">${esc(w)}</li>`).join('')}</ul></div>` : ''}
      ${a.reasons?.length ? `<div><strong style="font-size:0.8rem;color:var(--error)">Not generatable because:</strong><ul style="margin-top:0.4rem">${a.reasons.map(r => `<li style="font-size:0.8rem;color:var(--text-muted);padding:0.2rem 0">${esc(r)}</li>`).join('')}</ul></div>` : ''}
      ${a.uncertainties?.length ? `<div style="margin-top:0.75rem"><strong style="font-size:0.8rem;color:var(--text-dim)">Uncertainties:</strong><ul style="margin-top:0.4rem">${a.uncertainties.map(u => `<li style="font-size:0.8rem;color:var(--text-dim);padding:0.2rem 0">${esc(u)}</li>`).join('')}</ul></div>` : ''}`;
  } else {
    warnCard.style.display = 'none';
  }
}

// ── MODEL INSIGHTS ────────────────────────────────────────────────────────
function initInsights() {
  const analysis = State.getSelectedAnalysis();
  const slug = State.getSelectedSlug();

  if (!analysis || !slug) {
    document.getElementById('insightsEmpty').style.display = '';
    document.getElementById('insightsContent').style.display = 'none';
    return;
  }

  document.getElementById('insightsEmpty').style.display = 'none';
  document.getElementById('insightsContent').style.display = '';
  document.getElementById('insightsModelName').textContent = slugToDisplay(slug);
  document.getElementById('insightsModelTask').textContent = analysis.task || 'Task unknown';

  const continueBtn = document.getElementById('insightsContinueBtn');
  if (continueBtn && !continueBtn._wired) {
    continueBtn._wired = true;
    continueBtn.addEventListener('click', () => navigateTo('generator'));
  }
  renderInsights(analysis);
}

function renderInsights(a) {
  const grid = document.getElementById('insightsGrid');
  if (!grid) return;

  const hw = a.hardware || {};
  const caps = a.capabilities || [];
  const supportedCaps = caps.filter(c => c.status === 'supported');
  const combined = a.combined_analysis || {};
  const recs = combined.recommendations || [];

  const cards = [];

  // Strengths
  const strengths = [];
  if (hw.runs_locally_cpu) strengths.push('Lightweight — runs on CPU without GPU');
  if (hw.fits_free_colab_t4) strengths.push('Compatible with free Colab T4 GPU');
  if (a.use_safetensors) strengths.push('Uses safe SafeTensors weight format');
  if (!a.trust_remote_code) strengths.push('No remote code execution required');
  if (a.license && a.license !== 'Unknown') strengths.push(`License: ${a.license}`);
  if (hw.params && hw.params < 500_000_000) strengths.push(`Relatively small model (${formatParams(hw.params)} params)`);
  if (strengths.length === 0) strengths.push('Model analysis completed successfully');

  cards.push(insightCardHtml('strengths', 'strengths', 'Model Strengths',
    `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M6.267 3.455a3.066 3.066 0 001.745-.723 3.066 3.066 0 013.976 0 3.066 3.066 0 001.745.723 3.066 3.066 0 012.812 2.812c.051.643.304 1.254.723 1.745a3.066 3.066 0 010 3.976 3.066 3.066 0 00-.723 1.745 3.066 3.066 0 01-2.812 2.812 3.066 3.066 0 00-1.745.723 3.066 3.066 0 01-3.976 0 3.066 3.066 0 00-1.745-.723 3.066 3.066 0 01-2.812-2.812 3.066 3.066 0 00-.723-1.745 3.066 3.066 0 010-3.976 3.066 3.066 0 00.723-1.745 3.066 3.066 0 012.812-2.812zm7.44 5.252a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>`,
    strengths
  ));

  // Limitations
  const limits = [];
  if (a.warnings?.length > 0) limits.push(...a.warnings.slice(0, 3));
  if (a.trust_remote_code) limits.push('Requires trust_remote_code=True (security risk)');
  if (hw.needs_gpu && !hw.fits_free_colab_t4) limits.push('Requires GPU — may not fit free Colab tier');
  if (hw.exceeds_colab) limits.push('Model is too large for any single Colab GPU');
  if (a.gated) limits.push('Gated model — requires HuggingFace token and access approval');
  if (limits.length === 0) limits.push('No significant limitations found');

  cards.push(insightCardHtml('limits', 'limits', 'Limitations',
    `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>`,
    limits
  ));

  // Supported capabilities
  cards.push(insightCardHtml('optim', 'optim', 'Detected Capabilities',
    `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M11.3 1.046A1 1 0 0112 2v5h4a1 1 0 01.82 1.573l-7 10A1 1 0 018 18v-5H4a1 1 0 01-.82-1.573l7-10a1 1 0 011.12-.38z" clip-rule="evenodd"/></svg>`,
    supportedCaps.length > 0
      ? supportedCaps.map(c => `${taskEmoji(c.name)} ${c.name} (${(c.confidence * 100).toFixed(0)}% confidence)`)
      : ['No verified capabilities detected — uncertain analysis']
  ));

  // Hardware
  cards.push(insightCardHtml('hw', 'hw', 'Hardware Compatibility',
    `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M2 5a2 2 0 012-2h12a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V5zm3.293 1.293a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 01-1.414-1.414L7.586 10 5.293 7.707a1 1 0 010-1.414zM11 12a1 1 0 100 2h3a1 1 0 100-2h-3z" clip-rule="evenodd"/></svg>`,
    [
      `Recommended: ${hw.recommended_runtime || '—'}`,
      hw.est_vram_gb_fp16 ? `Estimated VRAM: ~${hw.est_vram_gb_fp16} GiB (fp16)` : null,
      hw.runs_locally_cpu ? '✓ Runs on CPU (no GPU needed for small inputs)' : 'Requires GPU for inference',
      hw.fits_free_colab_t4 ? '✓ Fits free Colab T4' : (hw.suggest_4bit ? 'Use 4-bit quantization on T4' : ''),
    ].filter(Boolean)
  ));

  // Fine-tuning
  const ftItems = [];
  const lib = a.library;
  if (lib === 'transformers') {
    ftItems.push('Fine-tuning supported via Hugging Face Trainer API');
    ftItems.push('LoRA/PEFT fine-tuning available for most architectures');
    if (hw.params && hw.params < 1_000_000_000) ftItems.push('Parameter count allows full fine-tuning on a single GPU');
    else ftItems.push('Large model — use LoRA/QLoRA for efficient fine-tuning');
  } else if (lib === 'diffusers') {
    ftItems.push('DreamBooth fine-tuning supported');
    ftItems.push('LoRA for diffusion models is widely supported');
  } else {
    ftItems.push('Fine-tuning depends on the framework used');
  }

  cards.push(insightCardHtml('finetune', 'finetune', 'Fine-tuning Feasibility',
    `<svg viewBox="0 0 20 20" fill="currentColor"><path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z"/></svg>`,
    ftItems
  ));

  // Dependencies
  const deps = a.dependencies || [];
  cards.push(insightCardHtml('deps', 'deps', 'Dependencies',
    `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3 4a1 1 0 011-1h4a1 1 0 010 2H6.414l2.293 2.293a1 1 0 01-1.414 1.414L5 6.414V8a1 1 0 01-2 0V4zm9 1a1 1 0 010-2h4a1 1 0 011 1v4a1 1 0 01-2 0V6.414l-2.293 2.293a1 1 0 11-1.414-1.414L13.586 5H12zm-9 7a1 1 0 012 0v1.586l2.293-2.293a1 1 0 111.414 1.414L6.414 15H8a1 1 0 010 2H4a1 1 0 01-1-1v-4zm13-1a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 010-2h1.586l-2.293-2.293a1 1 0 111.414-1.414L15 13.586V12a1 1 0 011-1z" clip-rule="evenodd"/></svg>`,
    deps.length > 0 ? deps.slice(0, 6).map(d => d) : ['No dependencies detected']
  ));

  grid.innerHTML = cards.join('');

  // Recommendations
  if (recs.length > 0) {
    grid.innerHTML += `
      <div class="insight-card" style="grid-column:1/-1">
        <div class="insight-card-header">
          <div class="insight-icon insight-icon--strengths"><svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/></svg></div>
          <span class="insight-title">Analysis Recommendations</span>
        </div>
        <div class="insight-body">
          ${recs.map(r => `<div class="insight-recommend">${esc(r)}</div>`).join('')}
        </div>
      </div>`;
  }
}

function insightCardHtml(key, iconClass, title, iconSvg, items) {
  return `
    <div class="insight-card">
      <div class="insight-card-header">
        <div class="insight-icon insight-icon--${iconClass}">${iconSvg}</div>
        <span class="insight-title">${esc(title)}</span>
      </div>
      <div class="insight-body">
        <ul class="insight-list">
          ${items.map(i => `<li>${esc(i)}</li>`).join('')}
        </ul>
      </div>
    </div>`;
}

// ── APP GENERATOR ─────────────────────────────────────────────────────────
let _generationPollInterval = null;

function initGenerator() {
  const analysis = State.getSelectedAnalysis();
  const slug = State.getSelectedSlug();

  if (!analysis || !slug) {
    document.getElementById('generatorEmpty').style.display = '';
    document.getElementById('generatorContent').style.display = 'none';
    return;
  }

  document.getElementById('generatorEmpty').style.display = 'none';
  document.getElementById('generatorContent').style.display = '';

  // Model info
  const info = document.getElementById('generatorModelInfo');
  if (info) {
    info.innerHTML = `
      <div class="sel-name">${esc(slugToDisplay(slug))}</div>
      <div class="sel-task">${taskEmoji(analysis.task || '')} ${esc(analysis.task || 'Unknown task')}</div>
      <div class="sel-id">${esc(analysis.source?.repo_id || slug)}</div>
      <div style="margin-top:0.75rem">${statusBadgeHtml(analysis.status)}</div>`;
  }

  // Capabilities list
  const capsList = document.getElementById('generatorCapsList');
  const caps = (analysis.capabilities || []).filter(c => c.status === 'supported' && c.builds_interface !== false);
  if (capsList) {
    if (caps.length > 0) {
      capsList.innerHTML = caps.map(c => `
        <div style="display:flex;align-items:center;gap:0.6rem;padding:0.6rem 0;border-bottom:1px solid var(--border-light)">
          <span style="font-size:1.1rem">${taskEmoji(c.name)}</span>
          <div>
            <div style="font-size:0.85rem;font-weight:600">${esc(c.name)}</div>
            <div style="font-size:0.75rem;color:var(--text-muted)">${esc(c.role)} · ${(c.confidence * 100).toFixed(0)}% confidence · ${esc(c.tier)} evidence</div>
          </div>
          ${statusBadgeHtml('supported')}
        </div>`).join('');
    } else {
      capsList.innerHTML = `<p style="color:var(--text-muted);font-size:0.83rem">No verified capabilities. Generation may fail.</p>`;
    }
  }

  // Wire generate button
  const btn = document.getElementById('generatorBtn');
  if (btn && !btn._wired) {
    btn._wired = true;
    btn.addEventListener('click', () => startGeneration(slug));
  }

  // Wire nav buttons (only once — use _wired flag)
  const viewBtn = document.getElementById('viewGeneratedAppBtn');
  if (viewBtn && !viewBtn._wired) {
    viewBtn._wired = true;
    viewBtn.addEventListener('click', () => navigateTo('apps'));
  }
  const deployBtn = document.getElementById('goToDeployBtn');
  if (deployBtn && !deployBtn._wired) {
    deployBtn._wired = true;
    deployBtn.addEventListener('click', () => navigateTo('deployment'));
  }

  // Restore previous generation if slug matches
  const app = State.getSelectedApp();
  if (app && app.slug === slug && app.has_app) {
    showGenerationSuccess(slug, app.files || []);
  }
}

function startGeneration(slug) {
  document.getElementById('generationSuccess').style.display = 'none';
  document.getElementById('generationError').style.display = 'none';
  document.getElementById('generationProgress').style.display = '';

  const btn = document.getElementById('generatorBtn');
  if (btn) btn.disabled = true;

  API.generateApp(slug)
    .then(res => {
      if (res.job_id) {
        pollGenerationJob(res.job_id, slug);
      } else if (res.result) {
        handleGenerationResult(slug, res.result);
      } else {
        showGenerationError(res.error || 'Unknown error');
      }
    })
    .catch(err => showGenerationError(err.message));
}

function pollGenerationJob(jobId, slug) {
  const stages = ['Validating analysis', 'Building generation request', 'Generating with AI model', 'Validating output', 'Writing files'];
  let stageIdx = 0;

  if (_generationPollInterval) clearInterval(_generationPollInterval);

  _generationPollInterval = setInterval(async () => {
    try {
      const status = await API.getGenerationStatus(jobId);
      stageIdx = Math.min(stageIdx + 1, stages.length - 1);
      document.getElementById('generationProgressSub').textContent = stages[stageIdx] || 'Working…';
      renderGenStages(stages, stageIdx);

      if (status.status === 'done' || status.status === 'complete') {
        clearInterval(_generationPollInterval);
        _generationPollInterval = null;
        handleGenerationResult(slug, status.result || { slug, files: status.files });
      } else if (status.status === 'error' || status.status === 'failed') {
        clearInterval(_generationPollInterval);
        _generationPollInterval = null;
        showGenerationError(status.error || 'Generation failed');
      }
    } catch (err) {
      clearInterval(_generationPollInterval);
      _generationPollInterval = null;
      showGenerationError(err.message);
    }
  }, 2000);
}

function handleGenerationResult(slug, result) {
  document.getElementById('generationProgress').style.display = 'none';
  const btn = document.getElementById('generatorBtn');
  if (btn) btn.disabled = false;

  if (result && (result.files || result.slug)) {
    showGenerationSuccess(slug, result.files || []);
    State.appendActivity({ type: 'generation', status: 'success', message: `Generated app for ${slug}`, timestamp: new Date().toISOString() });
    showToast('success', 'App generated!', slug);
    loadAppsData();
  } else {
    showGenerationError(result?.error || 'Generation did not produce files');
  }
}

function showGenerationSuccess(slug, files) {
  document.getElementById('generationProgress').style.display = 'none';
  document.getElementById('generationError').style.display = 'none';
  document.getElementById('generationSuccess').style.display = '';

  document.getElementById('generationSuccessMsg').textContent = `Files written to output/${slug}/`;
  const fileList = document.getElementById('generatedFilesList');
  if (fileList && files.length > 0) {
    fileList.innerHTML = files.map(f => `
      <span class="file-chip">
        <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/></svg>
        ${esc(f)}
      </span>`).join('');
  } else {
    fileList.innerHTML = `<span class="file-chip"><svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/></svg>app.py</span><span class="file-chip">requirements.txt</span><span class="file-chip">analysis.json</span><span class="file-chip">colab_notebook.ipynb</span><span class="file-chip">run_local.bat</span>`;
  }
}

function showGenerationError(msg) {
  document.getElementById('generationProgress').style.display = 'none';
  document.getElementById('generationSuccess').style.display = 'none';
  document.getElementById('generationError').style.display = '';
  document.getElementById('generationErrorMsg').textContent = msg;
  const btn = document.getElementById('generatorBtn');
  if (btn) btn.disabled = false;
  showToast('error', 'Generation failed', msg, 8000);
}

function renderGenStages(stages, currentIdx) {
  const container = document.getElementById('generationStages');
  if (!container) return;
  container.innerHTML = stages.map((s, i) => {
    const cls = i < currentIdx ? 'done' : i === currentIdx ? 'current' : '';
    return `<div class="gen-stage-item ${cls}">
      <div class="gen-stage-indicator">${i < currentIdx ? '✓' : ''}</div>
      ${esc(s)}
    </div>`;
  }).join('');
}

// ── GENERATED APPS ────────────────────────────────────────────────────────
async function loadAppsData() {
  State.setAppsLoading(true);
  try {
    const apps = await API.listApps();
    State.setApps(apps || []);
  } catch (err) {
    State.setApps([]);
    console.warn('Could not load apps:', err.message);
  }
}

function initAppsPage() {
  renderAppsList();
  loadAppsData().then(renderAppsList);

  const refreshBtn = document.getElementById('appsRefreshBtn');
  if (refreshBtn && !refreshBtn._wired) {
    refreshBtn._wired = true;
    refreshBtn.addEventListener('click', () => loadAppsData().then(renderAppsList));
  }

  const search = document.getElementById('appsSearch');
  if (search && !search._wired) {
    search._wired = true;
    search.addEventListener('input', renderAppsList);
  }

  State.subscribe('apps', renderAppsList);
}

function renderAppsList() {
  const apps = State.getApps();
  const container = document.getElementById('appsList');
  if (!container) return;

  if (apps === null || State.isAppsLoading()) {
    container.innerHTML = `<div class="loading-state"><div class="spinner"></div><span>Loading…</span></div>`;
    return;
  }

  const q = (document.getElementById('appsSearch')?.value || '').toLowerCase();
  const filtered = apps.filter(a => !q || a.slug.toLowerCase().includes(q) || (a.task || '').toLowerCase().includes(q));

  if (filtered.length === 0) {
    container.innerHTML = `<div class="empty-state" style="padding:3rem"><p style="color:var(--text-dim);font-size:0.85rem">${q ? 'No apps match your search.' : 'No apps found in output directory.'}</p></div>`;
    return;
  }

  const selectedSlug = State.getSelectedApp()?.slug;
  container.innerHTML = filtered.map(app => `
    <div class="app-list-item ${app.slug === selectedSlug ? 'selected' : ''}" data-slug="${esc(app.slug)}">
      <div class="app-list-icon">${taskEmoji(app.task || '')}</div>
      <div style="flex:1;min-width:0">
        <div class="app-list-name">${esc(slugToDisplay(app.slug))}</div>
        <div class="app-list-task">${esc(app.task || '—')}</div>
        <div class="app-list-date">${formatDate(app.created_at)}</div>
      </div>
      ${statusBadgeHtml(app.has_app ? 'generated' : 'partial')}
    </div>`).join('');

  container.querySelectorAll('.app-list-item').forEach(el => {
    el.addEventListener('click', () => selectApp(el.dataset.slug));
  });
}

window.navigateTo = navigateTo;
window.selectApp = selectApp;

async function selectApp(slug) {
  // Mark selected in list
  document.querySelectorAll('.app-list-item').forEach(el => {
    el.classList.toggle('selected', el.dataset.slug === slug);
  });

  const detail = document.getElementById('appsDetailContent');
  const empty  = document.getElementById('appsDetailEmpty');
  if (!detail || !empty) return;

  empty.style.display = 'none';
  detail.style.display = '';
  detail.innerHTML = `<div class="loading-state"><div class="spinner"></div><span>Loading app details…</span></div>`;

  try {
    const app = await API.getApp(slug);
    State.setSelectedApp(app);
    renderAppDetail(app);
  } catch (err) {
    detail.innerHTML = `<div style="padding:2rem;color:var(--error);font-size:0.85rem">Error loading app: ${esc(err.message)}</div>`;
  }
}

function renderAppDetail(app) {
  const detail = document.getElementById('appsDetailContent');
  if (!detail) return;

  const a = app.analysis || {};
  const files = app.files || [];
  const hw = a.hardware || {};

  detail.innerHTML = `
    <div class="app-detail-header">
      <div>
        <div class="app-detail-name">${taskEmoji(app.task || '')} ${esc(slugToDisplay(app.slug))}</div>
        <div class="app-detail-id">${esc(app.slug)}</div>
        <div style="margin-top:0.4rem;display:flex;gap:0.4rem;flex-wrap:wrap">
          ${statusBadgeHtml(app.has_app ? 'generated' : 'partial')}
          ${app.task ? `<span class="badge badge--info">${esc(app.task)}</span>` : ''}
        </div>
      </div>
      <div class="app-detail-actions">
        <button class="btn btn-secondary btn-sm" id="goToInsightsBtnDetail">Insights</button>
        <button class="btn btn-primary btn-sm" id="deployFromAppsBtn" data-slug="${esc(app.slug)}">Deploy →</button>
      </div>
    </div>

    <div style="padding:0 1.25rem 1.25rem">
      <!-- Meta -->
      <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:0.5rem;margin-bottom:1rem">
        ${a.task ? `<div class="tech-item"><div class="tech-label">Task</div><div class="tech-val">${esc(a.task)}</div></div>` : ''}
        ${a.library ? `<div class="tech-item"><div class="tech-label">Framework</div><div class="tech-val">${esc(a.library)}</div></div>` : ''}
        ${hw.params ? `<div class="tech-item"><div class="tech-label">Parameters</div><div class="tech-val">${formatParams(hw.params)}</div></div>` : ''}
        ${hw.recommended_runtime ? `<div class="tech-item"><div class="tech-label">Recommended</div><div class="tech-val" style="font-size:0.75rem">${esc(hw.recommended_runtime)}</div></div>` : ''}
      </div>

      <!-- File tabs -->
      ${files.length > 0 ? `
        <div class="file-tabs" id="appFileTabs">
          ${files.map((f, i) => `<button class="file-tab ${i === 0 ? 'active' : ''}" data-file="${esc(f)}">${esc(f)}</button>`).join('')}
        </div>
        <div id="appFileViewer"></div>
      ` : '<p style="color:var(--text-muted);font-size:0.83rem">No files found in this app directory.</p>'}

      <!-- Actions -->
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:1rem">
        <button class="btn btn-secondary btn-sm" id="validateAppBtn" data-slug="${esc(app.slug)}">Validate</button>
        ${app.has_app && files.includes('run_local.bat') ? `
          <button class="btn btn-secondary btn-sm" id="downloadLocalBatBtn">Download BAT</button>
          <button class="btn btn-ghost btn-sm" id="localInstructionsBtn">Local instructions</button>
        ` : ''}
        ${app.has_app ? `<button class="btn btn-ghost btn-sm" id="regenBtn" data-slug="${esc(app.slug)}">Regenerate</button>` : `<button class="btn btn-primary btn-sm" id="genFromDetailBtn">Generate App</button>`}
      </div>
    </div>`;

  // Wire insights button (in dynamic HTML - can't use onclick in ES module scope)
  detail.querySelector('#goToInsightsBtnDetail')?.addEventListener('click', () => {
    if (app.analysis) State.setSelectedAnalysis(app.slug, app.analysis);
    navigateTo('insights');
  });

  // Wire deploy button
  detail.querySelector('#deployFromAppsBtn')?.addEventListener('click', () => {
    State.setSelectedApp(app);
    // Also set analysis for deployment page
    if (app.analysis) State.setSelectedAnalysis(app.slug, app.analysis);
    navigateTo('deployment');
  });

  // Wire validate
  detail.querySelector('#validateAppBtn')?.addEventListener('click', async (e) => {
    const slug = e.target.dataset.slug;
    e.target.disabled = true;
    e.target.textContent = 'Validating…';
    try {
      const res = await API.validateApp(slug);
      showToast(res.ok ? 'success' : 'warning', res.ok ? 'Validation passed' : 'Validation warnings', res.message || '');
    } catch (err) {
      showToast('error', 'Validation failed', err.message);
    } finally {
      e.target.disabled = false;
      e.target.textContent = 'Validate';
    }
  });

  // Wire regenerate button
  detail.querySelector('#regenBtn')?.addEventListener('click', () => {
    if (app.analysis) State.setSelectedAnalysis(app.slug, app.analysis);
    navigateTo('generator');
  });

  // Wire generate app button (shown when app.py doesn't exist yet)
  detail.querySelector('#genFromDetailBtn')?.addEventListener('click', () => {
    if (app.analysis) State.setSelectedAnalysis(app.slug, app.analysis);
    navigateTo('generator');
  });

  detail.querySelector('#downloadLocalBatBtn')?.addEventListener('click', () => {
    downloadLocalLauncher(app.slug);
  });
  detail.querySelector('#localInstructionsBtn')?.addEventListener('click', () => {
    showToast('info', 'Local deployment', 'Download run_local.bat, place it beside app.py and requirements.txt, then double-click it. Keep the terminal open while Gradio is running.', 9000);
  });

  // File tabs
  const tabContainer = detail.querySelector('#appFileTabs');
  if (tabContainer) {
    loadAppFile(app.slug, files[0]);
    tabContainer.querySelectorAll('.file-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        tabContainer.querySelectorAll('.file-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        loadAppFile(app.slug, tab.dataset.file);
      });
    });
  }

  async function downloadLocalLauncher(slug) {
    try {
      const result = await API.getAppFile(slug, 'run_local.bat');
      const blob = new Blob([result.content], { type: 'application/bat' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'run_local.bat';
      link.click();
      URL.revokeObjectURL(link.href);
      showToast('success', 'BAT file downloaded', 'run_local.bat is ready to launch locally.');
    } catch (err) {
      showToast('error', 'Download failed', err.message);
    }
  }
}

async function loadAppFile(slug, filename) {
  const viewer = document.getElementById('appFileViewer');
  if (!viewer) return;
  viewer.innerHTML = `<div class="loading-state" style="padding:1.5rem"><div class="spinner"></div></div>`;
  try {
    const res = await API.getAppFile(slug, filename);
    const content = res.content || '';
    viewer.innerHTML = `
      <div class="code-viewer" style="max-height:380px">
        <pre>${esc(content)}</pre>
      </div>
      <div style="margin-top:0.5rem;font-size:0.72rem;color:var(--text-dim)">${esc(filename)} · ${content.split('\n').length} lines</div>`;
  } catch (err) {
    viewer.innerHTML = `<p style="color:var(--error);font-size:0.82rem;padding:0.75rem">Could not load file: ${esc(err.message)}</p>`;
  }
}

// ── DEPLOYMENT ────────────────────────────────────────────────────────────
function initDeployment() {
  const app = State.getSelectedApp();
  const analysis = State.getSelectedAnalysis();

  if (!app && !analysis) {
    document.getElementById('deploymentEmpty').style.display = '';
    document.getElementById('deploymentContent').style.display = 'none';
    return;
  }

  const slug = app?.slug || State.getSelectedSlug();
  if (!slug) {
    document.getElementById('deploymentEmpty').style.display = '';
    document.getElementById('deploymentContent').style.display = 'none';
    return;
  }

  document.getElementById('deploymentEmpty').style.display = 'none';
  document.getElementById('deploymentContent').style.display = '';

  document.getElementById('deployModelName').textContent = slugToDisplay(slug);
  document.getElementById('deployModelSlug').textContent = slug;

  initLocalDeployment(slug);
  initColabDeployment(slug);
}

// ── Local Deployment ──────────────────────────────────────────────────────
function initLocalDeployment(slug) {
  const startBtn = document.getElementById('localStartBtn');
  const stopBtn  = document.getElementById('localStopBtn');

  if (startBtn && !startBtn._wired) {
    startBtn._wired = true;
    startBtn.addEventListener('click', () => startLocalDeploy(slug));
  }
  if (stopBtn && !stopBtn._wired) {
    stopBtn._wired = true;
    stopBtn.addEventListener('click', () => stopLocalDeploy(slug));
  }

  const copyUrlBtn = document.getElementById('localCopyUrl');
  if (copyUrlBtn && !copyUrlBtn._wired) {
    copyUrlBtn._wired = true;
    copyUrlBtn.addEventListener('click', () => {
      const url = document.getElementById('localUrl')?.href;
      if (url && url !== '#') { navigator.clipboard?.writeText(url); showToast('info', 'URL copied!', url, 2000); }
    });
  }

  const clearLogBtn = document.getElementById('localLogClear');
  if (clearLogBtn && !clearLogBtn._wired) {
    clearLogBtn._wired = true;
    clearLogBtn.addEventListener('click', () => {
      const logBody = document.getElementById('localLogBody');
      if (logBody) logBody.textContent = '';
      const log = document.getElementById('localLog');
      if (log) log.style.display = 'none';
    });
  }

  // Restore local state
  renderLocalState(slug, State.getLocalState(slug));
}

async function startLocalDeploy(slug) {
  setLocalUI(slug, 'loading');
  try {
    const res = await API.startLocal(slug);
    State.setLocalState(slug, res);
    renderLocalState(slug, res);
    // Start polling — call once immediately then on interval
    const pollFn = () => pollLocalState(slug);
    pollFn();
    startPoll(`local-${slug}`, pollFn, POLL_LOCAL);
    showToast('info', 'Local deployment started', slug, 3000);
  } catch (err) {
    showToast('error', 'Failed to start', err.message);
    setLocalUI(slug, 'idle');
  }
}

async function stopLocalDeploy(slug) {
  stopPoll(`local-${slug}`);
  try {
    const res = await API.stopLocal(slug);
    State.setLocalState(slug, res);
    renderLocalState(slug, res);
    showToast('info', 'Local app stopped', slug, 3000);
  } catch (err) {
    showToast('error', 'Stop failed', err.message);
  }
}

async function pollLocalState(slug) {
  try {
    const state = await API.getLocalStatus(slug);
    State.setLocalState(slug, state);
    renderLocalState(slug, state);
    if (['idle', 'stopped', 'failed'].includes(state.state)) {
      stopPoll(`local-${slug}`);
    }
  } catch (err) {
    console.warn('Local poll error:', err.message);
  }
}

function setLocalUI(slug, status) {
  renderLocalState(slug, { state: status });
}

const LOCAL_STAGES = ['preparing', 'hardware', 'creating_environment', 'installing_dependencies', 'preflight', 'starting', 'running'];
const LOCAL_STAGE_LABELS = {
  idle: 'Idle', preparing: 'Preparing…', hardware: 'Checking hardware',
  environment: 'Setting up virtualenv', creating_environment: 'Creating environment',
  installing_dependencies: 'Installing dependencies', preflight: 'Running preflight checks',
  starting: 'Starting app', running: 'Running', failed: 'Failed', stopped: 'Stopped',
};

function renderLocalState(slug, state) {
  if (!state) return;
  const s = state.state || 'idle';
  const isRunning = s === 'running';
  const isLoading = ['preparing', 'hardware', 'environment', 'creating_environment',
    'installing_dependencies', 'preflight', 'starting'].includes(s);
  const isFailed  = s === 'failed';

  // Status badge
  const badge = document.getElementById('localStatusBadge');
  if (badge) {
    const dotCls = isRunning ? 'running' : isLoading ? 'loading' : isFailed ? 'failed' : 'idle';
    badge.innerHTML = `<span class="status-dot status-dot--${dotCls}"></span> ${esc(LOCAL_STAGE_LABELS[s] || s)}`;
  }

  // Buttons
  const startBtn = document.getElementById('localStartBtn');
  const stopBtn  = document.getElementById('localStopBtn');
  if (startBtn) startBtn.style.display = (isRunning || isLoading) ? 'none' : '';
  if (stopBtn)  stopBtn.style.display  = (isRunning || isLoading) ? '' : 'none';

  // Progress stages
  const progress = document.getElementById('localProgress');
  const stageList = document.getElementById('localStageList');
  if (isLoading || isRunning) {
    progress.style.display = '';
    const curIdx = LOCAL_STAGES.indexOf(s);
    stageList.innerHTML = LOCAL_STAGES.map((st, i) => {
      const cls = i < curIdx ? 'done' : i === curIdx ? 'active' : '';
      return `<div class="deploy-stage-item ${cls}"><div class="deploy-stage-dot"></div>${esc(LOCAL_STAGE_LABELS[st] || st)}</div>`;
    }).join('');
    const pct = curIdx >= 0 ? ((curIdx + 1) / LOCAL_STAGES.length * 100) : 0;
    document.getElementById('localProgressBar').style.width = pct + '%';
  } else {
    progress.style.display = 'none';
  }

  // URL
  const urlCard = document.getElementById('localUrlCard');
  if (isRunning && state.url) {
    urlCard.style.display = '';
    const urlEl = document.getElementById('localUrl');
    urlEl.href = state.url;
    urlEl.textContent = state.url;
  } else {
    urlCard.style.display = 'none';
  }

  // Info grid
  const infoGrid = document.getElementById('localInfoGrid');
  if (infoGrid) {
    const items = [
      ['Python', state.python || '—'],
      ['Environment', state.environment ? state.environment.split(/[/\\]/).slice(-2).join('/') : '—'],
      ['Hardware', state.hardware || '—'],
      ['PID', state.pid || '—'],
    ];
    infoGrid.innerHTML = items.map(([k, v]) => `<div class="deploy-info-item"><div class="dinfo-label">${esc(k)}</div><div class="dinfo-val">${esc(v)}</div></div>`).join('');
  }

  // Logs
  const logEl = document.getElementById('localLog');
  const logBody = document.getElementById('localLogBody');
  if (state.logs?.length > 0) {
    logEl.style.display = '';
    logBody.textContent = state.logs.slice(-80).join('\n');
    logBody.scrollTop = logBody.scrollHeight;
  } else {
    logEl.style.display = 'none';
  }

  // Error
  if (isFailed && state.error) {
    showToast('error', 'Local deployment failed', state.error, 8000);
  }
}

// ── Colab Deployment ──────────────────────────────────────────────────────
function initColabDeployment(slug) {
  const checkBtn  = document.getElementById('colabCheckBtn');
  const startBtn  = document.getElementById('colabStartBtn');
  const stopBtn   = document.getElementById('colabStopBtn');
  const copyBtn   = document.getElementById('colabCopyUrl');
  const logRefBtn = document.getElementById('colabLogRefresh');

  if (checkBtn && !checkBtn._wired) {
    checkBtn._wired = true;
    checkBtn.addEventListener('click', checkColabConnection);
  }
  if (startBtn && !startBtn._wired) {
    startBtn._wired = true;
    startBtn.addEventListener('click', () => startColabDeploy(slug));
  }
  if (stopBtn && !stopBtn._wired) {
    stopBtn._wired = true;
    stopBtn.addEventListener('click', () => stopColabDeploy(slug));
  }
  if (copyBtn && !copyBtn._wired) {
    copyBtn._wired = true;
    copyBtn.addEventListener('click', () => {
      const url = document.getElementById('colabUrl')?.href;
      if (url && url !== '#') { navigator.clipboard?.writeText(url); showToast('info', 'URL copied!', url, 2000); }
    });
  }
  if (logRefBtn && !logRefBtn._wired) {
    logRefBtn._wired = true;
    logRefBtn.addEventListener('click', () => fetchAndShowColabLogs(slug));
  }

  // Restore state
  renderColabState(slug, State.getColabState(slug));

  // Resume polling if a deployment is already in progress
  const state = State.getColabState(slug);
  if (state && !['idle', 'stopped', 'failed', 'cancelled', 'disconnected'].includes(state.state)) {
    const pollFn = () => pollColabState(slug);
    pollFn(); // immediate check
    startPoll(`colab-${slug}`, pollFn, POLL_COLAB);
  }
}

async function checkColabConnection() {
  const dot = document.getElementById('colabConnDot');
  const text = document.getElementById('colabConnText');
  if (dot)  { dot.className = 'conn-dot conn-dot--checking'; }
  if (text) text.textContent = 'Checking…';

  try {
    const res = await API.checkColab();
    const ok = res.status === 'ok';
    if (dot)  { dot.className = `conn-dot conn-dot--${ok ? 'ok' : 'error'}`; }
    if (text) text.textContent = ok ? 'Connected and authenticated' : (res.message || 'Connection failed');
    State.setColabConnection(res);

    if (res.gpus) {
      const sel = document.getElementById('colabGpuSelect');
      if (sel && res.gpus.length > 0) {
        const existing = Array.from(sel.options).map(o => o.value);
        res.gpus.forEach(gpu => {
          if (!existing.includes(gpu)) sel.add(new Option(gpu, gpu));
        });
      }
    }
  } catch (err) {
    if (dot)  { dot.className = 'conn-dot conn-dot--error'; }
    if (text) text.textContent = 'Check failed: ' + err.message;
    showToast('warning', 'Colab connection', err.message, 6000);
  }
}

async function startColabDeploy(slug) {
  const gpu  = document.getElementById('colabGpuSelect')?.value || null;
  const user = document.getElementById('colabAuthUser')?.value?.trim() || '';
  const pw   = document.getElementById('colabAuthPw')?.value || '';
  const auth = (user && pw) ? [user, pw] : null;

  setColabUI(slug, 'loading');
  try {
    const res = await API.startColab(slug, gpu, auth);
    State.setColabState(slug, res);
    renderColabState(slug, res);
    // Start polling — call once immediately then on interval
    const pollFn = () => pollColabState(slug);
    pollFn();
    startPoll(`colab-${slug}`, pollFn, POLL_COLAB);
    showToast('info', 'Colab deployment started', 'This may take several minutes…', 5000);
  } catch (err) {
    showToast('error', 'Colab start failed', err.message, 8000);
    setColabUI(slug, 'idle');
  }
}

async function stopColabDeploy(slug) {
  stopPoll(`colab-${slug}`);
  try {
    const res = await API.stopColab(slug);
    State.setColabState(slug, res);
    renderColabState(slug, res);
    showToast('info', 'Colab runtime stopping…', '', 3000);
  } catch (err) {
    showToast('error', 'Stop failed', err.message);
  }
}

async function pollColabState(slug) {
  try {
    const state = await API.getColabDeployment(slug);
    State.setColabState(slug, state);
    renderColabState(slug, state);
    if (['idle', 'stopped', 'failed', 'cancelled', 'disconnected'].includes(state.state)) {
      stopPoll(`colab-${slug}`);
    }
  } catch (err) {
    console.warn('Colab poll error:', err.message);
  }
}

async function fetchAndShowColabLogs(slug) {
  try {
    const res = await API.fetchColabLogs(slug);
    const logBody = document.getElementById('colabLogBody');
    if (logBody && res.logs) {
      logBody.textContent = Array.isArray(res.logs) ? res.logs.join('\n') : res.logs;
      logBody.scrollTop = logBody.scrollHeight;
    }
  } catch (err) {
    console.warn('Colab logs error:', err.message);
  }
}

function setColabUI(slug, status) {
  renderColabState(slug, { state: status });
}

const COLAB_STAGE_ORDER = [
  'validating app', 'checking CLI', 'creating runtime', 'probing hardware',
  'installing dependencies', 'transferring files', 'starting app', 'waiting for readiness', 'running',
];

const COLAB_STAGE_LABELS = {
  'idle':                 'Idle',
  'validating app':       'Validating app',
  'checking CLI':         'Checking Colab CLI',
  'creating runtime':     'Creating runtime',
  'probing hardware':     'Probing hardware',
  'installing dependencies': 'Installing dependencies',
  'transferring files':   'Transferring files',
  'starting app':         'Starting app',
  'waiting for readiness':'Waiting for app to start',
  'running':              'Running',
  'failed':               'Failed',
  'cancelled':            'Cancelled',
  'stopped':              'Stopped',
  'disconnected':         'Disconnected',
};

function renderColabState(slug, state) {
  if (!state) return;
  const s = state.state || 'idle';
  const isRunning = s === 'running';
  const isLoading = COLAB_STAGE_ORDER.slice(0, -1).includes(s);
  const isFailed  = ['failed', 'cancelled', 'disconnected'].includes(s);

  // Status badge
  const badge = document.getElementById('colabStatusBadge');
  if (badge) {
    const dotCls = isRunning ? 'running' : isLoading ? 'loading' : isFailed ? 'failed' : 'idle';
    badge.innerHTML = `<span class="status-dot status-dot--${dotCls}"></span> ${esc(COLAB_STAGE_LABELS[s] || s)}`;
  }

  // Buttons
  const startBtn = document.getElementById('colabStartBtn');
  const stopBtn  = document.getElementById('colabStopBtn');
  if (startBtn) startBtn.style.display = (isRunning || isLoading) ? 'none' : '';
  if (stopBtn)  stopBtn.style.display  = (isRunning || isLoading) ? '' : 'none';

  // Progress stages
  const progress = document.getElementById('colabProgress');
  const stageList = document.getElementById('colabStageList');
  if (isLoading || isRunning) {
    progress.style.display = '';
    const curIdx = COLAB_STAGE_ORDER.indexOf(s);
    stageList.innerHTML = COLAB_STAGE_ORDER.map((st, i) => {
      const cls = i < curIdx ? 'done' : i === curIdx ? 'active' : '';
      return `<div class="deploy-stage-item ${cls}"><div class="deploy-stage-dot"></div>${esc(COLAB_STAGE_LABELS[st] || st)}</div>`;
    }).join('');
    const pct = curIdx >= 0 ? ((curIdx + 1) / COLAB_STAGE_ORDER.length * 100) : 0;
    document.getElementById('colabProgressBar').style.width = pct + '%';

    // Hardware info
    if (state.hardware) {
      const hw = state.hardware;
      document.getElementById('colabHwInfo').style.display = '';
      document.getElementById('colabHwInfo').innerHTML = [
        hw.gpu ? `GPU: ${hw.gpu}` : 'No GPU',
        hw.ram_gib ? `RAM: ${hw.ram_gib} GiB` : '',
        hw.python ? `Python ${hw.python}` : '',
      ].filter(Boolean).join(' · ');
    }
  } else {
    progress.style.display = 'none';
  }

  // URL
  const urlCard = document.getElementById('colabUrlCard');
  if (isRunning && state.share_url) {
    urlCard.style.display = '';
    const urlEl = document.getElementById('colabUrl');
    urlEl.href = state.share_url;
    urlEl.textContent = state.share_url;
  } else {
    urlCard.style.display = 'none';
  }

  // Logs
  const logEl = document.getElementById('colabLog');
  const logBody = document.getElementById('colabLogBody');
  if (state.logs?.length > 0) {
    logEl.style.display = '';
    const logText = Array.isArray(state.logs) ? state.logs.slice(-100).join('\n') : state.logs;
    if (logBody) {
      logBody.textContent = logText;
      logBody.scrollTop = logBody.scrollHeight;
    }
  }

  // Error/failure message
  if (isFailed && state.message) {
    showToast('error', 'Colab deployment ' + s, state.message.slice(0, 200), 10000);
  }
}

// ── ACTIVITY & LOGS ───────────────────────────────────────────────────────
let _activityFilter = 'all';

function initActivity() {
  loadActivity();

  const refreshBtn = document.getElementById('activityRefreshBtn');
  if (refreshBtn && !refreshBtn._wired) {
    refreshBtn._wired = true;
    refreshBtn.addEventListener('click', loadActivity);
  }

  document.querySelectorAll('.filter-chip').forEach(chip => {
    if (!chip._wired) {
      chip._wired = true;
      chip.addEventListener('click', () => {
        document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        _activityFilter = chip.dataset.filter;
        renderActivity();
      });
    }
  });

  State.subscribe('activity', renderActivity);
}

async function loadActivity() {
  try {
    const events = await API.getActivity(200);
    State.setActivity(events || []);
  } catch {
    // Not critical
  }
  renderActivity();
}

const ACT_TYPE_ICONS = {
  analysis:   `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z" clip-rule="evenodd"/></svg>`,
  generation: `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M12.316 3.051a1 1 0 01.633 1.265l-4 12a1 1 0 11-1.898-.632l4-12a1 1 0 011.265-.633zM5.707 6.293a1 1 0 010 1.414L3.414 10l2.293 2.293a1 1 0 11-1.414 1.414l-3-3a1 1 0 010-1.414l3-3a1 1 0 011.414 0zm8.586 0a1 1 0 011.414 0l3 3a1 1 0 010 1.414l-3 3a1 1 0 11-1.414-1.414L16.586 10l-2.293-2.293a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>`,
  deployment: `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M3 17a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1zm3.293-7.707a1 1 0 011.414 0L9 10.586V3a1 1 0 112 0v7.586l1.293-1.293a1 1 0 111.414 1.414l-3 3a1 1 0 01-1.414 0l-3-3a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>`,
  error:      `<svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>`,
};

function renderActivity() {
  const container = document.getElementById('activityList');
  if (!container) return;
  let events = State.getActivity();
  if (_activityFilter !== 'all') {
    events = events.filter(e => {
      if (_activityFilter === 'error') return e.status === 'error' || e.status === 'failed' || e.type === 'error';
      return e.type === _activityFilter;
    });
  }

  if (events.length === 0) {
    container.innerHTML = `<div class="empty-state" style="padding:3rem"><p style="color:var(--text-dim);font-size:0.85rem">No activity found.</p></div>`;
    return;
  }

  container.innerHTML = events.slice(0, 200).map(ev => {
    const iconClass = `act-icon--${ev.type || 'analysis'}`;
    const icon = ACT_TYPE_ICONS[ev.type] || ACT_TYPE_ICONS.analysis;
    const ts = ev.timestamp ? new Date(ev.timestamp).getTime() : Date.now();
    return `
      <div class="activity-full-item" data-id="${esc(ev.id || '')}">
        <div class="activity-full-icon ${iconClass}">${icon}</div>
        <div class="activity-full-body">
          <div class="activity-full-title">${esc(ev.title || ev.message || '—')}</div>
          ${ev.message && ev.title ? `<div class="activity-full-msg">${esc(ev.message)}</div>` : ''}
          <div class="activity-full-meta">${timeAgo(ts)} · ${ev.type || 'system'}</div>
          ${ev.details ? `<div class="activity-expand">${esc(ev.details)}</div>` : ''}
        </div>
        <div class="activity-full-badge">${statusBadgeHtml(ev.status)}</div>
      </div>`;
  }).join('');
}

// ── Bootstrap ─────────────────────────────────────────────────────────────
async function init() {
  // Load initial data (explicit calls — startPoll no longer calls immediately)
  await loadAppsData();
  loadActivity();

  // Periodic refresh every 10s (not on startup — already loaded above)
  startPoll('apps-refresh', loadAppsData, POLL_APPS);

  // Navigate to dashboard
  navigateTo('dashboard');
}

init();
