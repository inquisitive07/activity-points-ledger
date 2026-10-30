import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { api } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { formatDateTime, formatPoints, statusLabel } from '../utils/format.js';
import { Page, Card, Field, Badge, Stat, Table, row, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';
import { DocumentList } from '../components/Documents.jsx';
import { LedgerTable, PointsProgress } from '../components/Ledger.jsx';
import { ReviewNote } from '../components/Review.jsx';

const TARGET_FILTERS = { ALL: 'All students', BELOW: 'Below target', REACHED: 'Target reached' };
const byTarget = (filter) => (s) => filter === 'ALL' || (filter === 'REACHED') === !!s.target_reached;

/**
 * Department summary from GET /students (no search): the backend scopes the list to the HOD's department and supplies
 * target_reached per student, so the counts here are only a tally of backend values, never a points calculation.
 */
function DepartmentSummary({ students }) {
  const reached = students.filter((s) => s.target_reached).length;
  return (
    <div className="grid grid-cols-3 gap-3 mb-4">
      <Stat n={students.length} label="Students in department" />
      <Stat n={reached} label="Target reached" tone="text-ok" />
      <Stat n={students.length - reached} label="Below target" tone="text-brandl" />
    </div>
  );
}

/** The student's requests (GET /requests is already scoped to the department; this only picks one student's rows). */
function RequestHistory({ requests, usn }) {
  if (requests.error && !requests.data) return <ErrorState error={requests.error} title="Could not load requests" onRetry={requests.reload} />;
  if (requests.loading && !requests.data) return <Loading label="Loading requests…" />;
  const mine = (requests.data || []).filter((r) => r.usn === usn);
  return (
    <Table cols={['ID', 'Event', 'Type', 'Claimed', 'Files', 'Status', 'Club review', 'Dean decision', 'Submitted']}
      empty="No requests from this student."
      rows={mine.map((r) => row(r.request_id, [
        `#${r.request_id}`,
        r.event_name,
        <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
        formatPoints(r.claimed_points),
        r.document_count,
        <div><Badge s={r.status} />{r.ledger_id != null && <div className="text-xs text-mute mt-1">Award #{r.ledger_id}</div>}</div>,
        <ReviewNote at={r.club_reviewed_at} by={r.club_reviewed_by} comment={r.club_review_comment} />,
        <ReviewNote at={r.dean_decided_at} by={r.dean_decided_by} comment={r.dean_decision_comment} />,
        <span className="text-xs whitespace-nowrap">{formatDateTime(r.created_at)}</span>,
      ]))} />
  );
}

/** Read-only inspection: summary, full ledger history, requests and documents. No write controls of any kind. */
function StudentDetail({ usn, requests, onClose }) {
  const { data: s, error, reload } = useApi(({ signal }) => api.get(`/students/${encodeURIComponent(usn)}`, { signal }), [usn]);
  if (!s) {
    return (
      <Card title={`Student · ${usn}`}>
        {error
          ? <ErrorState error={error} title={error.status === 404 ? 'Student not available' : 'Could not load this student'} onRetry={error.status === 404 ? null : reload} />
          : <Loading label="Loading student…" />}
        {error?.status === 404 && <p className="text-xs text-mute mb-3">Only students of your department can be inspected. An unknown USN and a student of another department give the same answer.</p>}
        <button type="button" className={GHOST} onClick={onClose}>Close</button>
      </Card>
    );
  }
  return (
    <Card title={`${s.name} · ${s.usn}`}>
      <div className="flex flex-wrap gap-2 items-center text-sm mb-3">
        <Badge s={s.student_status} /> <span>{s.department_code}</span> · <span>{statusLabel(s.entry_type)} entry</span>
        <button type="button" className={`${GHOST} ml-auto`} onClick={onClose}>Close</button>
      </div>
      {error && <ErrorState error={error} title="Could not refresh" onRetry={reload} />}
      <div className="mb-4"><PointsProgress summary={s} /></div>
      <div className="text-sm font-semibold mb-1">Ledger history</div>
      <LedgerTable ledger={s.ledger || []} showIds empty="No awards yet." />
      <div className="text-sm font-semibold mt-4 mb-1">Requests</div>
      <RequestHistory requests={requests} usn={s.usn} />
      <div className="text-sm font-semibold mt-4 mb-1">Documents</div>
      <DocumentList query={{ usn: s.usn }} empty="No documents for this student." />
    </Card>
  );
}

/** HOD · Department Directory. Strictly read-only; the backend restricts every call to the HOD's department. */
export default function Hod() {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const selected = params.get('usn');
  const select = (usn) => setParams(usn ? { usn } : {}, { replace: true });

  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [target, setTarget] = useState('ALL');
  const [lookup, setLookup] = useState('');
  useEffect(() => { const t = setTimeout(() => setSearch(q.trim()), 300); return () => clearTimeout(t); }, [q]);

  const all = useApi(({ signal }) => api.get('/students', { signal }), []);
  const list = useApi(({ signal }) => api.get('/students', { signal, query: { q: search } }), [search]);
  const requests = useApi(({ signal }) => api.get('/requests', { signal }), []);
  const shown = (list.data || []).filter(byTarget(target));

  return (
    <Page title={`${user.department_code} Department Directory`} sub="Read-only. Students, awards, requests and documents of your department only.">
      {all.error && !all.data && <ErrorState error={all.error} title="Could not load the department summary" onRetry={all.reload} />}
      {all.data && <DepartmentSummary students={all.data} />}

      <Card>
        <div className="flex flex-wrap gap-3 mb-3 items-end">
          <Field label="Search" className="w-full max-w-sm">
            <input className={INPUT} placeholder="Name or USN…" value={q} onChange={(e) => setQ(e.target.value)} />
          </Field>
          <Field label="Target" className="w-44">
            <select className={INPUT} value={target} onChange={(e) => setTarget(e.target.value)}>
              {Object.entries(TARGET_FILTERS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </Field>
          <form className="flex gap-2 items-end ml-auto" onSubmit={(e) => { e.preventDefault(); if (lookup.trim()) select(lookup.trim().toUpperCase()); }}>
            <Field label="Open by USN" className="w-40">
              <input className={INPUT} placeholder="1RV22CS001" value={lookup} onChange={(e) => setLookup(e.target.value)} />
            </Field>
            <button type="submit" className={BTN} disabled={!lookup.trim()}>Open</button>
          </form>
          {list.loading && list.data && <span className="text-xs text-mute" role="status">Searching…</span>}
        </div>
        {list.error && <ErrorState error={list.error} title="Could not load students" onRetry={list.reload} />}
        {list.loading && !list.data ? <Loading label="Loading students…" /> : (
          <Table cols={['USN', 'Name', 'Entry', 'Status', 'Earned', 'Required', 'Remaining', 'Target', '']}
            empty={search || target !== 'ALL' ? 'No students match these filters.' : 'No students in your department.'}
            rows={shown.map((s) => row(s.usn, [
              <span className="font-mono text-xs">{s.usn}</span>, s.name, statusLabel(s.entry_type),
              <Badge s={s.student_status} />,
              <b>{formatPoints(s.earned_points)}</b>, formatPoints(s.required_points), formatPoints(s.remaining_points),
              <Badge s={s.target_reached ? 'Target reached' : 'Below target'} tone={s.target_reached ? 'ok' : 'warn'} />,
              <button type="button" className={GHOST} onClick={() => select(selected === s.usn ? null : s.usn)}>{selected === s.usn ? 'Hide' : 'Inspect'}</button>,
            ], selected === s.usn ? 'bg-soft/20' : ''))} />
        )}
      </Card>
      {selected && <StudentDetail key={selected} usn={selected} requests={requests} onClose={() => select(null)} />}
    </Page>
  );
}
