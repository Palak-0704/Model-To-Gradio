/**
 * state.js — Shared application state for Model-to-Gradio frontend.
 * Provides a simple reactive store with subscriber pattern.
 */

const _subscribers = new Map();

function _emit(key, value) {
  const subs = _subscribers.get(key);
  if (subs) subs.forEach(fn => { try { fn(value); } catch (e) { console.error(e); } });
  // wildcard
  const wild = _subscribers.get('*');
  if (wild) wild.forEach(fn => { try { fn(key, value); } catch (e) { console.error(e); } });
}

/** Subscribe to state key changes. Returns unsubscribe function. */
export function subscribe(key, fn) {
  if (!_subscribers.has(key)) _subscribers.set(key, new Set());
  _subscribers.get(key).add(fn);
  return () => _subscribers.get(key)?.delete(fn);
}

/** Subscribe to all state changes. Returns unsubscribe function. */
export function subscribeAll(fn) {
  return subscribe('*', fn);
}

// ── Current navigation ────────────────────────────────────────────────────

let _page = 'dashboard';

export function getCurrentPage() { return _page; }

export function setCurrentPage(page) {
  if (_page !== page) {
    _page = page;
    _emit('page', page);
  }
}

// ── Selected model / analysis ─────────────────────────────────────────────

let _selectedAnalysis = null;   // Full analysis.json object
let _selectedSlug = null;       // e.g. "google__flan-t5-small"

export function getSelectedAnalysis() { return _selectedAnalysis; }
export function getSelectedSlug() { return _selectedSlug; }

export function setSelectedAnalysis(slug, analysis) {
  _selectedSlug = slug;
  _selectedAnalysis = analysis;
  _emit('analysis', { slug, analysis });
}

export function clearSelectedAnalysis() {
  _selectedSlug = null;
  _selectedAnalysis = null;
  _emit('analysis', null);
}

// ── Analysis job tracking ─────────────────────────────────────────────────

let _analysisJob = null;  // { jobId, url, status, error, result }

export function getAnalysisJob() { return _analysisJob; }

export function setAnalysisJob(job) {
  _analysisJob = job ? { ..._analysisJob, ...job } : null;
  _emit('analysisJob', _analysisJob);
}

// ── Apps list ─────────────────────────────────────────────────────────────

let _apps = null;     // null = not loaded, [] = loaded empty, [...] = loaded
let _appsLoading = false;

export function getApps() { return _apps; }
export function isAppsLoading() { return _appsLoading; }

export function setApps(apps) {
  _apps = apps;
  _appsLoading = false;
  _emit('apps', apps);
}

export function setAppsLoading(loading) {
  _appsLoading = loading;
  _emit('appsLoading', loading);
}

// ── Selected generated app ────────────────────────────────────────────────

let _selectedApp = null;    // Full app detail object

export function getSelectedApp() { return _selectedApp; }

export function setSelectedApp(app) {
  _selectedApp = app;
  _emit('selectedApp', app);
}

// ── Local deployment state ────────────────────────────────────────────────

let _localState = {};   // keyed by slug

export function getLocalState(slug) { return _localState[slug] || null; }

export function setLocalState(slug, state) {
  _localState[slug] = state;
  _emit('local:' + slug, state);
  _emit('local', { slug, state });
}

// ── Colab deployment state ────────────────────────────────────────────────

let _colabState = {};   // keyed by slug

export function getColabState(slug) { return _colabState[slug] || null; }

export function setColabState(slug, state) {
  _colabState[slug] = state;
  _emit('colab:' + slug, state);
  _emit('colab', { slug, state });
}

let _colabConnection = null;  // { status, checked_at }

export function getColabConnection() { return _colabConnection; }

export function setColabConnection(conn) {
  _colabConnection = conn;
  _emit('colabConnection', conn);
}

// ── Activity log ─────────────────────────────────────────────────────────

let _activity = [];

export function getActivity() { return _activity; }

export function setActivity(events) {
  _activity = events;
  _emit('activity', events);
}

export function appendActivity(event) {
  _activity = [event, ..._activity].slice(0, 500);
  _emit('activity', _activity);
}

// ── Notifications ─────────────────────────────────────────────────────────

let _notifId = 0;
let _notifications = [];

export function getNotifications() { return _notifications; }

export function addNotification(type, title, message, duration = 5000) {
  const id = ++_notifId;
  const notif = { id, type, title, message, at: Date.now() };
  _notifications = [notif, ..._notifications].slice(0, 20);
  _emit('notifications', _notifications);
  if (duration > 0) setTimeout(() => dismissNotification(id), duration);
  return id;
}

export function dismissNotification(id) {
  _notifications = _notifications.filter(n => n.id !== id);
  _emit('notifications', _notifications);
}

// ── Sidebar collapse ──────────────────────────────────────────────────────

let _sidebarCollapsed = false;

export function isSidebarCollapsed() { return _sidebarCollapsed; }

export function setSidebarCollapsed(v) {
  _sidebarCollapsed = v;
  _emit('sidebar', v);
}
