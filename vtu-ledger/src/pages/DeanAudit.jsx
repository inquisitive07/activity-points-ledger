import { useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { api } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { formatDateTime, formatPoints } from '../utils/format.js';
import { Page, Card, Field, Badge, Table, row, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';

/**
 * Suggestions only (the filters accept any text): the action and entity names the backend currently writes
 * (app/audit.py callers). The backend matches action case-insensitively, entity_type exactly, user_email lower-cased.
 */
const KNOWN_ACTIONS = [
  'LOGIN', 'LOGIN_FAILED', 'LOGIN_REJECTED', 'LOGOUT',
  'STUDENT_STATUS_CHANGED',
  'EVENT_CREATED', 'EVENT_UPDATED', 'EVENT_STATUS_CHANGED',
  'DOCUMENT_UPLOADED', 'DOCUMENT_ARCHIVED', 'DOCUMENT_DELETED',
  'REQUEST_SUBMITTED', 'REQUEST_WITHDRAWN', 'REQUEST_DOCUMENT_ATTACHED', 'REQUEST_CLUB_VERIFIED', 'REQUEST_CLUB_REJECTED',
  'REQUEST_APPROVED', 'REQUEST_REJECTED', 'REQUEST_RESOLVED',
  'LEDGER_AWARD_CREATED', 'LEDGER_AWARD_SUPERSEDED', 'LEDGER_DOCUMENT_LINKED',
  'OCR_BATCH_CREATED', 'OCR_BATCH_PROCESSING', 'OCR_BATCH_REVIEW_READY', 'OCR_BATCH_FAILED', 'OCR_BATCH_MARKED_FAILED',
  'OCR_BATCH_RETRIED', 'OCR_BATCH_VALID_CONFIRMED', 'OCR_BATCH_COMMITTED',
  'OCR_RECORD_CORRECTED', 'OCR_RECORD_CONFIRMED', 'OCR_RECORD_DISMISSED', 'OCR_RECORD_REOPENED',
];
const KNOWN_ENTITIES = ['user_account', 'student', 'event', 'document', 'point_request', 'point_ledger', 'ocr_batch', 'ocr_record'];
const PAGE_SIZES = [25, 50, 100, 200];                  // backend limit is 1-200
const EMPTY_FILTERS = { action: '', entity_type: '', entity_id: '', user_email: '' };

/** JSON text with object keys sorted, so two values compare equal regardless of key order. */
function stable(value) {
  if (value === undefined) return 'undefined';
  return JSON.stringify(value, (_, v) => (v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map((k) => [k, v[k]])) : v));
}

/** One JSONB value: absent key, null, scalar, or nested object/array (pretty-printed). */
function JsonValue({ value }) {
  if (value === undefined) return <span className="text-mute italic">absent</span>;
  if (value === null) return <span className="text-mute font-mono">null</span>;
  if (typeof value === 'object') return <pre className="font-mono text-xs whitespace-pre-wrap break-all">{JSON.stringify(value, null, 2)}</pre>;
  if (typeof value === 'string') return <span className="font-mono text-xs break-all">"{value}"</span>;
  return <span className="font-mono text-xs">{String(value)}</span>;
}

/** Key-by-key comparison of old_values and new_values; changed keys are highlighted. Either side may be null. */
function ValueDiff({ oldValues, newValues }) {
  if (oldValues == null && newValues == null) return <p className="text-xs text-mute">No values recorded for this entry.</p>;
  const keys = [...new Set([...Object.keys(oldValues || {}), ...Object.keys(newValues || {})])].sort();
  if (!keys.length) return <p className="text-xs text-mute">Empty values.</p>;
  return (
    <table className="w-full text-xs border border-soft/40 rounded">
      <thead><tr className="text-mute uppercase">
        <th className="text-left p-1.5 w-40">Field</th>
        <th className="text-left p-1.5">Old {oldValues == null && <span className="normal-case">(none recorded)</span>}</th>
        <th className="text-left p-1.5">New {newValues == null && <span className="normal-case">(none recorded)</span>}</th>
      </tr></thead>
      <tbody>
        {keys.map((k) => {
          const before = oldValues?.[k];
          const after = newValues?.[k];
          const changed = oldValues != null && newValues != null && stable(before) !== stable(after);
          return (
            <tr key={k} className={`border-t border-soft/30 align-top ${changed ? 'bg-amber-50' : ''}`}>
              <td className="p-1.5 font-mono">{k}{changed && <span className="ml-1 text-amber-800" title="Changed">●</span>}</td>
              <td className={`p-1.5 ${changed ? 'text-brandl' : ''}`}>{oldValues == null ? '' : <JsonValue value={before} />}</td>
              <td className={`p-1.5 ${changed ? 'text-ok' : ''}`}>{newValues == null ? '' : <JsonValue value={after} />}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Dean · audit log (GET /audit). Read-only: the log is append-only in the database, and nothing here writes. */
function AuditLog() {
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState(null);
  const [problem, setProblem] = useState('');

  const key = JSON.stringify([filters, limit, offset]);
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/audit', { signal, query: { ...filters, limit, offset } }), [key]);

  const apply = (e) => {
    e.preventDefault();
    const next = Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v.trim()]));
    if (next.entity_id && !/^\d+$/.test(next.entity_id)) { setProblem('Entity ID must be a whole number.'); return; }
    setProblem(''); setFilters(next); setOffset(0); setOpen(null);
  };
  const clear = () => { setDraft(EMPTY_FILTERS); setFilters(EMPTY_FILTERS); setOffset(0); setOpen(null); setProblem(''); };
  const go = (o) => { setOffset(Math.max(0, o)); setOpen(null); };
  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  const active = Object.values(filters).some(Boolean);

  const total = data?.total ?? 0;
  const items = data?.items || [];
  const first = items.length ? offset + 1 : 0;
  const last = offset + items.length;

  return (
    <Card title="System audit log">
      <form onSubmit={apply} className="grid md:grid-cols-5 gap-3 items-end mb-3" noValidate>
        <Field label="Action">
          <input className={INPUT} list="audit-actions" placeholder="e.g. REQUEST_APPROVED" value={draft.action} onChange={set('action')} />
          <datalist id="audit-actions">{KNOWN_ACTIONS.map((a) => <option key={a} value={a} />)}</datalist>
        </Field>
        <Field label="Entity type">
          <input className={INPUT} list="audit-entities" placeholder="e.g. point_request" value={draft.entity_type} onChange={set('entity_type')} />
          <datalist id="audit-entities">{KNOWN_ENTITIES.map((a) => <option key={a} value={a} />)}</datalist>
        </Field>
        <Field label="Entity ID">
          <input className={INPUT} inputMode="numeric" placeholder="e.g. 8" value={draft.entity_id} onChange={set('entity_id')} />
        </Field>
        <Field label="User email">
          <input className={INPUT} type="email" placeholder="dean@vtu.edu.in" value={draft.user_email} onChange={set('user_email')} />
        </Field>
        <div className="flex gap-2">
          <button type="submit" className={BTN}>Apply</button>
          <button type="button" className={GHOST} onClick={clear} disabled={!active && !Object.values(draft).some((v) => v.trim())}>Clear</button>
        </div>
        {problem && <p className="md:col-span-5 text-sm text-brandl" role="alert">{problem}</p>}
      </form>
      <p className="text-xs text-mute mb-3">
        Newest first. Entries keyed by text (accounts, students) have entity ID 0; the email or USN is in the values. The log is append-only.
      </p>

      {error && <ErrorState error={error} title="Could not load the audit log" onRetry={reload} />}
      {loading && !data ? <Loading label="Loading audit log…" /> : (
        <>
          <Table cols={['Time', 'User', 'Action', 'Entity', 'IP address', '']}
            empty={active ? 'No audit entries match these filters.' : 'The audit log is empty.'}
            rows={items.map((a) => row(a.audit_id, [
              <span className="text-xs whitespace-nowrap" title={a.created_at}>{formatDateTime(a.created_at)}</span>,
              <span className="text-xs break-all">{a.user_email || <span className="text-mute">system</span>}</span>,
              <span className="font-mono text-xs">{a.action}</span>,
              <span className="text-xs whitespace-nowrap"><span className="font-mono">{a.entity_type}</span> {a.entity_id ? `#${a.entity_id}` : <span className="text-mute" title="Keyed by text; see the values">—</span>}</span>,
              <span className="font-mono text-xs">{a.ip_address || '—'}</span>,
              (a.old_values || a.new_values)
                ? <button type="button" className={GHOST} aria-expanded={open === a.audit_id} onClick={() => setOpen(open === a.audit_id ? null : a.audit_id)}>{open === a.audit_id ? 'Hide' : 'Values'}</button>
                : <span className="text-xs text-mute">No values</span>,
            ], open === a.audit_id ? 'bg-soft/20' : ''))} />
          {items.filter((a) => a.audit_id === open).map((a) => (
            <div key={a.audit_id} className="mt-3 border border-soft/50 rounded-xl p-3">
              <div className="text-sm font-semibold text-maroon mb-2">
                Entry #{a.audit_id} · <span className="font-mono">{a.action}</span> · {a.entity_type}{a.entity_id ? ` #${a.entity_id}` : ''}
              </div>
              <ValueDiff oldValues={a.old_values} newValues={a.new_values} />
            </div>
          ))}

          <div className="flex flex-wrap items-center gap-3 mt-3 text-sm">
            <span className="text-mute" role="status">
              {total ? `Showing ${first}–${last} of ${total}` : ''}{loading ? ' · Loading…' : ''}
            </span>
            {offset > 0 && offset >= total && total > 0 && (
              <button type="button" className={GHOST} onClick={() => go(0)}>Back to first page</button>
            )}
            <label className="text-xs text-mute ml-auto flex items-center gap-1">Per page
              <select className={`${INPUT} w-20`} value={limit} onChange={(e) => { setLimit(Number(e.target.value)); go(0); }}>
                {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <button type="button" className={`${GHOST} flex items-center`} disabled={loading || offset === 0} onClick={() => go(offset - limit)} aria-label="Previous page"><ChevronLeft size={14} />Prev</button>
            <button type="button" className={`${GHOST} flex items-center`} disabled={loading || last >= total} onClick={() => go(offset + limit)} aria-label="Next page">Next<ChevronRight size={14} /></button>
          </div>
        </>
      )}
    </Card>
  );
}

/** Completion report from GET /students: totals and target_reached come from the STUDENT_POINT_SUMMARY view. */
function CompletionReport() {
  const [filter, setFilter] = useState('ALL');
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/students', { signal }), []);
  const list = (data || []).filter((s) => filter === 'ALL' || (filter === 'REACHED') === !!s.target_reached);
  return (
    <Card title="Student completion report">
      <Field label="Filter" className="max-w-xs mb-3">
        <select className={INPUT} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="ALL">All students</option><option value="REACHED">Target reached</option><option value="BELOW">Below target</option>
        </select>
      </Field>
      {error && <ErrorState error={error} title="Could not load students" onRetry={reload} />}
      {loading && !data ? <Loading label="Loading students…" /> : (
        <Table cols={['USN', 'Name', 'Dept', 'Status', 'Earned', 'Required', 'Remaining', 'Target']}
          empty="No students match this filter."
          rows={list.map((s) => row(s.usn, [
            <span className="font-mono text-xs">{s.usn}</span>, s.name, s.department_code, <Badge s={s.student_status} />,
            <b>{formatPoints(s.earned_points)}</b>, formatPoints(s.required_points), formatPoints(s.remaining_points),
            <Badge s={s.target_reached ? 'Target reached' : 'Below target'} tone={s.target_reached ? 'ok' : 'warn'} />,
          ]))} />
      )}
      <p className="text-xs text-mute mt-2">
        CSV / Excel / PDF exports need backend export endpoints, which do not exist yet; this report is view-only.
      </p>
    </Card>
  );
}

export default function DeanAudit() {
  return (
    <Page title="Audit Log & Reports" sub="Append-only record of every important action, and student completion status.">
      <AuditLog />
      <CompletionReport />
    </Page>
  );
}
