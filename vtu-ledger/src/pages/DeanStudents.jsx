import { useEffect, useState } from 'react';
import { useToast } from '../context/ToastContext.jsx';
import { api } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { formatPoints, statusLabel } from '../utils/format.js';
import { Page, Card, Field, Badge, Table, row, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';
import { DocumentList } from '../components/Documents.jsx';
import { LedgerTable, PointsProgress } from '../components/Ledger.jsx';

const STUDENT_STATUSES = ['ACTIVE', 'GRADUATED', 'INACTIVE', 'ARCHIVED'];

/** Dean-only lifecycle change (POST /students/{usn}/status). Records are never deleted. */
function StatusChange({ student, onChanged }) {
  const toast = useToast();
  const [target, setTarget] = useState(student.student_status);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!reason.trim()) { setError('Give a reason for the change; it is recorded in the audit log.'); return; }
    const effect = target === 'ACTIVE'
      ? 'An ACTIVE student can submit new point requests.'
      : `Only ACTIVE students can submit new point requests, so ${student.name} will not be able to submit new ones.`;
    if (!window.confirm(`Change ${student.name} (${student.usn}) from ${statusLabel(student.student_status)} to ${statusLabel(target)}?\n\n${effect}\n\n`
      + 'Login, read access to their history, their open requests and their existing awards are not affected. Nothing is deleted.')) return;
    setBusy(true); setError('');
    try {
      const out = await api.post(`/students/${encodeURIComponent(student.usn)}/status`, { student_status: target, reason: reason.trim() });
      toast(`${out.name} is now ${statusLabel(out.student_status)}.`);
      setReason('');
      onChanged();
    } catch (err) {
      setError(err.message);           // e.g. 409 when the status is unchanged
      toast(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid md:grid-cols-4 gap-3 items-end" noValidate>
      <Field label="New status">
        <select className={INPUT} value={target} onChange={(e) => setTarget(e.target.value)}>
          {STUDENT_STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}{s === student.student_status ? ' (current)' : ''}</option>)}
        </select>
      </Field>
      <Field label="Reason *" className="md:col-span-2">
        <input className={INPUT} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Completed the programme" />
      </Field>
      <div><button className={BTN} type="submit" disabled={busy}>{busy ? 'Saving…' : 'Change status'}</button></div>
      <p className="md:col-span-4 text-xs text-mute">
        Only ACTIVE students may submit new requests. Login, history, open requests and awards stay as they are; nothing is deleted.
        {target === student.student_status && ' This is the current status.'}
      </p>
      {error && <p className="md:col-span-4 text-sm text-brandl" role="alert">{error}</p>}
    </form>
  );
}

function StudentDetail({ usn, onChanged, onClose }) {
  const [key, setKey] = useState(0);
  const { data: s, error, loading, reload } = useApi(({ signal }) => api.get(`/students/${encodeURIComponent(usn)}`, { signal }), [usn, key]);
  if (!s) {
    return <Card title={`Student · ${usn}`}>{error ? <ErrorState error={error} onRetry={reload} /> : <Loading label="Loading student…" />}</Card>;
  }
  return (
    <Card title={`${s.name} · ${s.usn}`}>
      <div className="flex flex-wrap gap-2 items-center text-sm mb-3">
        <Badge s={s.student_status} /> <span>{s.department_code}</span> · <span>{statusLabel(s.entry_type)} entry</span>
        <button type="button" className={`${GHOST} ml-auto`} onClick={onClose}>Close</button>
      </div>
      {error && <ErrorState error={error} title="Could not refresh" onRetry={reload} />}
      <div className="mb-4"><PointsProgress summary={s} /></div>
      <div className="border border-soft/40 rounded-lg p-3 mb-4">
        <div className="text-sm font-semibold mb-2">Student status</div>
        <StatusChange key={`${s.usn}-${s.student_status}`} student={s} onChanged={() => { setKey((k) => k + 1); onChanged(); }} />
      </div>
      <div className="text-sm font-semibold mb-1">Ledger history</div>
      <LedgerTable ledger={s.ledger || []} showIds empty="No awards yet." />
      <div className="text-sm font-semibold mt-4 mb-1">Documents</div>
      <DocumentList query={{ usn: s.usn }} reloadKey={key} empty="No documents for this student." />
    </Card>
  );
}

export default function DeanStudents() {
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  useEffect(() => { const t = setTimeout(() => setSearch(q.trim()), 300); return () => clearTimeout(t); }, [q]);
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/students', { signal, query: { q: search } }), [search]);

  return (
    <Page title="Students" sub="All students with backend point totals. Change a student's lifecycle status; nothing is ever deleted.">
      <Card>
        <div className="flex flex-wrap gap-2 mb-3 items-center">
          <input className={`${INPUT} max-w-sm`} placeholder="Search name or USN…" value={q} onChange={(e) => setQ(e.target.value)} />
          {loading && data && <span className="text-xs text-mute" role="status">Searching…</span>}
        </div>
        {error && <ErrorState error={error} onRetry={reload} />}
        {loading && !data ? <Loading label="Loading students…" /> : (
          <Table cols={['USN', 'Name', 'Dept', 'Entry', 'Status', 'Earned', 'Required', 'Target', '']}
            empty={search ? 'No students match this search.' : 'No students found.'}
            rows={(data || []).map((s) => row(s.usn, [
              <span className="font-mono text-xs">{s.usn}</span>, s.name, s.department_code, statusLabel(s.entry_type),
              <Badge s={s.student_status} />,
              <b>{formatPoints(s.earned_points)}</b>, formatPoints(s.required_points),
              <Badge s={s.target_reached ? 'Target reached' : 'Below target'} tone={s.target_reached ? 'ok' : 'warn'} />,
              <button type="button" className={GHOST} onClick={() => setSelected(selected === s.usn ? null : s.usn)}>{selected === s.usn ? 'Hide' : 'Open'}</button>,
            ], selected === s.usn ? 'bg-soft/20' : ''))} />
        )}
      </Card>
      {selected && <StudentDetail key={selected} usn={selected} onChanged={reload} onClose={() => setSelected(null)} />}
    </Page>
  );
}
