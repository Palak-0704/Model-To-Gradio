/**
 * api.js — Backend communication layer for Model-to-Gradio frontend.
 * All requests go to the lightweight REST backend at /api/*.
 * Handles timeouts, error normalization, and duplicate-request prevention.
 */

const API_BASE = '';  // same origin

// Active abort controllers keyed by endpoint group
const _controllers = {};

function _abort(group) {
  if (_controllers[group]) {
    _controllers[group].abort();
    delete _controllers[group];
  }
}

function _controller(group) {
  _abort(group);
  const ctrl = new AbortController();
  _controllers[group] = ctrl;
  return ctrl;
}

async function _request(method, path, body, options = {}) {
  const { group, timeoutMs = 300_000 } = options;
  const ctrl = group ? _controller(group) : new AbortController();

  const timeoutId = timeoutMs > 0
    ? setTimeout(() => ctrl.abort(), timeoutMs)
    : null;

  const init = {
    method,
    signal: ctrl.signal,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) init.body = JSON.stringify(body);

  try {
    const res = await fetch(API_BASE + path, init);
    const contentType = res.headers.get('Content-Type') || '';
    const data = contentType.includes('application/json') ? await res.json() : await res.text();

    if (!res.ok) {
      const msg = (typeof data === 'object' && data.error) ? data.error
        : (typeof data === 'string' && data) ? data
        : `HTTP ${res.status}`;
      throw new ApiError(msg, res.status, data);
    }
    return data;
  } catch (err) {
    if (err.name === 'AbortError') throw new ApiError('Request was cancelled', 0);
    throw err instanceof ApiError ? err : new ApiError(err.message || 'Network error', 0);
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    if (group && _controllers[group]?.signal === ctrl.signal) delete _controllers[group];
  }
}

export class ApiError extends Error {
  constructor(message, status = 0, data = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

// ── Analysis ──────────────────────────────────────────────────────────────

/**
 * Start model analysis. Returns { job_id } immediately.
 * @param {string} url  HuggingFace or GitHub URL
 */
export function startAnalysis(url) {
  return _request('POST', '/api/analyze', { url }, { group: 'analyze' });
}

/**
 * Poll analysis status.
 * @param {string} jobId
 * @returns {{ status, stage, result?, error? }}
 */
export function getAnalysisStatus(jobId) {
  return _request('GET', `/api/analyze/${encodeURIComponent(jobId)}`);
}

// ── Generated apps (output/ directory) ──────────────────────────────────

/** List all apps in output/ directory. */
export function listApps() {
  return _request('GET', '/api/apps');
}

/** Get a single app's details including file contents. */
export function getApp(slug) {
  return _request('GET', `/api/apps/${encodeURIComponent(slug)}`);
}

/** Get the content of a specific file inside an app. */
export function getAppFile(slug, filename) {
  return _request('GET', `/api/apps/${encodeURIComponent(slug)}/files/${encodeURIComponent(filename)}`);
}

/** Trigger re-validation of a generated app. */
export function validateApp(slug) {
  return _request('POST', `/api/apps/${encodeURIComponent(slug)}/validate`);
}

// ── App generation ────────────────────────────────────────────────────────

/**
 * Generate a Gradio app for an already-analyzed model.
 * @param {string} slug  The app/analysis slug (owner__model)
 */
export function generateApp(slug) {
  return _request('POST', `/api/apps/${encodeURIComponent(slug)}/generate`, {}, { group: 'generate' });
}

/** Poll generation status. */
export function getGenerationStatus(jobId) {
  return _request('GET', `/api/generate/${encodeURIComponent(jobId)}`);
}

// ── Local deployment ──────────────────────────────────────────────────────

/** Start local deployment for a generated app. */
export function startLocal(slug) {
  return _request('POST', `/api/local/${encodeURIComponent(slug)}/start`, {}, { group: 'local' });
}

/** Get local deployment status + logs. */
export function getLocalStatus(slug) {
  return _request('GET', `/api/local/${encodeURIComponent(slug)}/status`);
}

/** Stop local deployment. */
export function stopLocal(slug) {
  return _request('POST', `/api/local/${encodeURIComponent(slug)}/stop`);
}

// ── Colab deployment ──────────────────────────────────────────────────────

/** Get Colab CLI connection status. */
export function getColabStatus() {
  return _request('GET', '/api/colab/status');
}

/** Check Colab CLI + auth. */
export function checkColab() {
  return _request('POST', '/api/colab/check', {}, { timeoutMs: 60_000 });
}

/** List available Colab GPU options. */
export function getColabGpus() {
  return _request('GET', '/api/colab/gpus');
}

/** Start a Colab deployment. */
export function startColab(slug, gpu = null, auth = null) {
  return _request('POST', `/api/colab/${encodeURIComponent(slug)}/start`, { gpu, auth }, { group: 'colab' });
}

/** Get Colab deployment snapshot. */
export function getColabDeployment(slug) {
  return _request('GET', `/api/colab/${encodeURIComponent(slug)}/status`);
}

/** Stop Colab deployment and release runtime. */
export function stopColab(slug) {
  return _request('POST', `/api/colab/${encodeURIComponent(slug)}/stop`);
}

/** Fetch live logs from the Colab runtime. */
export function fetchColabLogs(slug) {
  return _request('GET', `/api/colab/${encodeURIComponent(slug)}/logs`);
}

// ── Activity log ─────────────────────────────────────────────────────────

/** Get activity/event log. */
export function getActivity(limit = 100) {
  return _request('GET', `/api/activity?limit=${limit}`);
}

// ── Utilities ─────────────────────────────────────────────────────────────

/** Cancel an in-flight request group. */
export function cancelRequest(group) {
  _abort(group);
}
