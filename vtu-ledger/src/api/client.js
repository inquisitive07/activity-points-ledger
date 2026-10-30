/**
 * Centralized API client for the FastAPI backend (see vtu-backend/API_INTEGRATION.md).
 *
 * - Bearer token from sessionStorage on every request (never a cookie; no `credentials: 'include'`).
 * - JSON bodies are serialized here; FormData is passed through so the browser sets the multipart boundary.
 * - Every non-2xx response becomes an ApiError carrying the backend's `detail`.
 * - A 401 on an authenticated request clears the token and notifies listeners (AuthContext signs the user out).
 */

export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8002').replace(/\/+$/, '');

const TOKEN_KEY = 'vtu-ledger.token';

export const getToken = () => sessionStorage.getItem(TOKEN_KEY);
export const setToken = (token) => sessionStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => sessionStorage.removeItem(TOKEN_KEY);

const unauthorizedListeners = new Set();
/** Subscribe to "the backend rejected our token". Returns an unsubscribe function. */
export function onUnauthorized(listener) {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

/** A failed API call. `status` is 0 when the server could not be reached. */
export class ApiError extends Error {
  constructor(status, message, detail = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    /** 422 validation errors as { fieldName: message }, from FastAPI's list-form `detail`. */
    this.fieldErrors = Array.isArray(detail)
      ? Object.fromEntries(detail.map((d) => [d.loc?.[d.loc.length - 1] ?? '_', d.msg]))
      : {};
  }
}

export const isAbortError = (err) => err?.name === 'AbortError';

const FALLBACK_MESSAGES = {
  401: 'Your session has ended. Please sign in again.',
  403: 'You do not have permission to do this.',
  404: 'Not found.',
  409: 'This action conflicts with the current state. Refresh and try again.',
  413: 'The file is too large.',
  415: 'Unsupported file type.',
  422: 'Some of the information entered is invalid.',
  429: 'Too many attempts. Try again in a few minutes.',
};

/** Turn `detail` (a string, or FastAPI's 422 list) into one readable sentence. */
function messageFrom(status, detail) {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => {
      const field = (d.loc || []).filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.');
      return field ? `${field}: ${d.msg}` : d.msg;
    }).join('; ');
  }
  return FALLBACK_MESSAGES[status] || (status >= 500 ? 'The server had a problem. Please try again.' : `Request failed (${status}).`);
}

function buildUrl(path, query) {
  const url = new URL(API_BASE_URL + path);
  Object.entries(query || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  });
  return url.toString();
}

/** Suggested filename from `Content-Disposition` (RFC 5987 `filename*` first, then `filename`). */
function filenameFrom(header) {
  if (!header) return null;
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) { try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); } catch { /* fall through */ } }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : null;
}

/**
 * @param {string} path      e.g. '/students/1RV22CS001'
 * @param {object} options   method, body (object | FormData), query, signal,
 *                           auth (default true: send the token and handle 401 globally),
 *                           responseType ('json' | 'blob')
 */
export async function request(path, { method = 'GET', body, query, signal, auth = true, responseType = 'json' } = {}) {
  const headers = { Accept: responseType === 'blob' ? '*/*' : 'application/json' };
  const token = auth ? getToken() : null;
  if (token) headers.Authorization = `Bearer ${token}`;

  let payload;
  if (body instanceof FormData) payload = body;                 // browser sets multipart Content-Type + boundary
  else if (body !== undefined) { payload = JSON.stringify(body); headers['Content-Type'] = 'application/json'; }

  let res;
  try {
    res = await fetch(buildUrl(path, query), { method, headers, body: payload, signal });
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw new ApiError(0, `Cannot reach the server at ${API_BASE_URL}. Check that the API is running.`);
  }

  if (!res.ok) {
    let detail = null;
    try { detail = (await res.json()).detail ?? null; } catch { /* non-JSON error body */ }
    if (res.status === 401 && auth) {
      clearToken();
      unauthorizedListeners.forEach((fn) => fn());
    }
    throw new ApiError(res.status, messageFrom(res.status, detail), detail);
  }

  if (responseType === 'blob') {
    return { blob: await res.blob(), filename: filenameFrom(res.headers.get('Content-Disposition')) };
  }
  if (res.status === 204) return null;
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  get: (path, opts) => request(path, { ...opts, method: 'GET' }),
  post: (path, body, opts) => request(path, { ...opts, method: 'POST', body }),
  patch: (path, body, opts) => request(path, { ...opts, method: 'PATCH', body }),
  /** multipart upload: `fields` is a plain object; File/Blob values are appended as files. */
  upload: (path, fields, opts) => {
    const form = new FormData();
    Object.entries(fields).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') form.append(k, v); });
    return request(path, { ...opts, method: 'POST', body: form });
  },
  /** Authenticated binary fetch: resolves to { blob, filename }. */
  blob: (path, opts) => request(path, { ...opts, method: 'GET', responseType: 'blob' }),
};

/** Save a blob through a temporary object URL (an <a href> to the API cannot send the Bearer header). */
export function saveBlob(blob, filename = 'download') {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Download a document the caller is authorized to read (GET /documents/{id}/content). */
export async function downloadDocument(documentId, fallbackName) {
  const { blob, filename } = await api.blob(`/documents/${encodeURIComponent(documentId)}/content`, {
    query: { disposition: 'attachment' },
  });
  saveBlob(blob, filename || fallbackName || `document-${documentId}`);
}
