import { AlertTriangle, Loader2, Paperclip, X } from 'lucide-react';
import { formatBytes, statusLabel } from '../utils/format.js';

export const BTN = 'px-3 py-1.5 rounded-lg bg-brand hover:bg-brandl text-white font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed';
export const GHOST = 'px-3 py-1.5 rounded-lg border border-brand text-brand hover:bg-soft/30 font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed';
export const INPUT = 'w-full border border-soft rounded-lg px-2.5 py-1.5 bg-white text-ink text-sm focus:outline-none focus:ring-2 focus:ring-info';

const TONE = { ok: 'bg-emerald-100 text-ok', warn: 'bg-amber-100 text-amber-800', info: 'bg-blue-100 text-blue-800', err: 'bg-red-100 text-brandl', mute: 'bg-gray-100 text-mute', soft: 'bg-soft text-maroon' };

/**
 * Badge colour for every backend status value (app/models.py enums). The same value means the same thing in every
 * domain where it appears (e.g. ACTIVE for students, accounts, documents and ledger rows), so one map serves all.
 */
export const STATUS_TONE = {
  // ledger rows
  ACTIVE: 'ok', SUPERSEDED: 'mute', REVOKED: 'err',
  // point requests
  PENDING_CLUB: 'warn', PENDING_DEAN: 'info', APPROVED: 'ok', REJECTED: 'err', RESOLVED: 'ok', WITHDRAWN: 'mute',
  CORRECTION: 'info',
  // events
  DRAFT: 'mute', PUBLISHED: 'info', COMPLETED: 'ok', CLOSED: 'mute',
  // documents
  ARCHIVED: 'mute', DELETED: 'err',
  // OCR batches and records
  QUEUED: 'mute', PROCESSING: 'info', REVIEW: 'warn', COMMITTED: 'ok', FAILED: 'err',
  VALID: 'ok', EXCEPTION: 'err', CONFIRMED: 'ok', DISMISSED: 'mute',
  // students and accounts
  GRADUATED: 'info', INACTIVE: 'warn', DISABLED: 'err',
  // mock-only values used by screens not yet connected to the API
  Official: 'ok', Evidence: 'soft',
};

/** Status pill. `s` is a backend value ('PENDING_CLUB') or display text; the label is readable either way. */
export const Badge = ({ s, tone }) => (
  <span className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap ${TONE[tone || STATUS_TONE[s] || 'soft']}`}>{statusLabel(s)}</span>
);

export const Loading = ({ label = 'Loading…' }) => (
  <div role="status" className="flex items-center gap-2 text-mute text-sm py-6 justify-center">
    <Loader2 size={16} className="animate-spin" /> {label}
  </div>
);

/** Inline error panel for a failed load; shows the backend's message and an optional retry. */
export const ErrorState = ({ error, title = 'Could not load this page', onRetry }) => (
  <div role="alert" className="bg-white border border-red-200 rounded-xl p-4 mb-4 flex items-start gap-3">
    <AlertTriangle size={18} className="text-brandl shrink-0 mt-0.5" />
    <div className="flex-1 min-w-0">
      <div className="font-semibold text-brandl text-sm">{title}</div>
      <div className="text-sm text-ink break-words">{error?.message || String(error)}</div>
    </div>
    {onRetry && <button type="button" className={GHOST} onClick={onRetry}>Retry</button>}
  </div>
);
export const Page = ({ title, sub, children }) => (
  <div className="p-4 md:p-6 max-w-6xl">
    <h1 className="text-xl font-bold text-brandl">{title}</h1>
    <p className="text-mute text-sm mb-4">{sub}</p>
    {children}
  </div>
);
export const Card = ({ title, children, className = '' }) => (
  <section className={`bg-white border border-soft/40 rounded-xl p-4 mb-4 ${className}`}>
    {title && <h2 className="font-semibold text-maroon mb-3">{title}</h2>}
    {children}
  </section>
);
export const Field = ({ label, children, className = '' }) => (
  <label className={`block text-xs font-medium text-mute ${className}`}>{label}<div className="mt-1">{children}</div></label>
);
export const Stat = ({ n, label, tone = 'text-ink' }) => (
  <div className="bg-white border border-soft/40 rounded-xl p-3"><div className={`text-2xl font-bold ${tone}`}>{n}</div><div className="text-xs text-mute">{label}</div></div>
);

/** rows: [{ key, cells: [...], cls }] — build with row() */
export const Table = ({ cols, rows, empty = 'Nothing here yet.' }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead><tr>{cols.map((c) => <th key={c} className="text-left text-xs uppercase text-mute py-2 px-2 border-b border-soft/50">{c}</th>)}</tr></thead>
      <tbody>
        {rows.length ? rows.map((r) => (
          <tr key={r.key} className={`border-b border-soft/30 hover:bg-soft/20 ${r.cls || ''}`}>
            {r.cells.map((c, i) => <td key={i} className="py-2 px-2 align-top">{c}</td>)}
          </tr>
        )) : <tr><td colSpan={cols.length} className="py-6 text-center text-mute">{empty}</td></tr>}
      </tbody>
    </table>
  </div>
);
export const row = (key, cells, cls) => ({ key, cells, cls });

/**
 * Files to upload with a request or award. Each file gets a backend document type; the event is set by the caller
 * (shown as `eventLabel`), never chosen per file. There is no "purpose": a document becomes evidence by being linked.
 * files: [{ key, file, documentType, problem }]   (problem: client-side check result, or null)
 */
export function FileAttachments({ files, setFiles, types, eventLabel, accept, check, defaultType, disabled }) {
  const add = (e) => {
    const next = [...e.target.files].map((file, i) => ({
      key: `${Date.now()}-${i}-${file.name}`, file, documentType: defaultType(file), problem: check(file),
    }));
    setFiles([...files, ...next]); e.target.value = '';
  };
  const patch = (key, p) => setFiles(files.map((f) => (f.key === key ? { ...f, ...p } : f)));
  return (
    <div>
      <input type="file" multiple accept={accept} onChange={add} disabled={disabled} className="text-sm mb-2" />
      {files.map((f) => (
        <div key={f.key} className="grid grid-cols-2 md:grid-cols-5 gap-2 items-end bg-blush rounded-lg p-2 mb-2">
          <div className="col-span-2 text-sm min-w-0">
            <div className="font-medium truncate flex items-center gap-1"><Paperclip size={14} className="shrink-0" />{f.file.name}</div>
            <div className="text-xs text-mute">{formatBytes(f.file.size)}{eventLabel ? ` · tagged to ${eventLabel}` : ''}</div>
            {f.problem && <div className="text-xs text-brandl" role="alert">{f.problem}</div>}
          </div>
          <Field label="Document type" className="md:col-span-2">
            <select className={INPUT} value={f.documentType} disabled={disabled} onChange={(e) => patch(f.key, { documentType: e.target.value })}>
              {types.map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}
            </select>
          </Field>
          <div className="flex items-end">
            <button type="button" className={GHOST} disabled={disabled} aria-label={`Remove ${f.file.name}`} onClick={() => setFiles(files.filter((x) => x.key !== f.key))}><X size={14} /></button>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Per-file outcome of uploadAndAttach(): which files failed, at which step, and why. */
export function AttachmentResults({ results, onRetry, onDismiss, retrying, okText = 'attached', noun = 'attachment(s)' }) {
  const failed = results.filter((r) => !r.ok);
  if (!failed.length) return null;
  return (
    <div role="alert" className="border border-red-200 bg-white rounded-lg p-3 mt-3 text-sm">
      <div className="font-semibold text-brandl mb-1">{failed.length} of {results.length} {noun} failed</div>
      <ul className="space-y-1 mb-2">
        {results.map((r) => (
          <li key={r.item.key} className="flex gap-2">
            <span className={r.ok ? 'text-ok' : 'text-brandl'}>{r.ok ? '✓' : '✗'}</span>
            <span className="min-w-0 break-words">
              <b>{r.item.file.name}</b>{' '}
              {r.ok ? okText
                : r.stage === 'upload' ? `not uploaded: ${r.error.message}`
                : `uploaded to your vault (document #${r.documentId}) but not attached: ${r.error.message}`}
            </span>
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        {onRetry && <button type="button" className={BTN} disabled={retrying} onClick={onRetry}>{retrying ? 'Retrying…' : 'Retry failed files'}</button>}
        {onDismiss && <button type="button" className={GHOST} disabled={retrying} onClick={onDismiss}>Dismiss</button>}
      </div>
    </div>
  );
}
