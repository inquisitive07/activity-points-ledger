import { useState } from 'react';
import { api } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { formatDate, formatDateTime, formatPoints, statusLabel } from '../utils/format.js';
import { Page, Card, Badge, Table, row, Loading, ErrorState, GHOST, INPUT } from '../components/ui.jsx';
import { DocumentList } from '../components/Documents.jsx';
import { ReviewCell, ReviewNote, StudentCell, useRequestReview } from '../components/Review.jsx';
import { REQUEST_TYPES } from '../domain.js';

/** Final states. RESOLVED is set only by a committed signed list (OCR), never by a Dean action here. */
const DECIDED = ['APPROVED', 'REJECTED', 'RESOLVED', 'WITHDRAWN'];

const clubCell = (r) => r.club_id == null ? <span className="text-mute">Non-club</span> : null;

/**
 * What approving will do to the ledger, from the student's current awards (GET /students/{usn}): the award a
 * CORRECTION supersedes, or an existing ACTIVE award that makes a new award impossible (backend 409).
 */
function LedgerImpact({ request }) {
  const { data, error, loading } = useApi(({ signal }) => api.get(`/students/${encodeURIComponent(request.usn)}`, { signal }), [request.usn, request.request_id]);
  if (loading && !data) return <Loading label="Checking the student's ledger…" />;
  if (error) return <ErrorState error={error} title="Could not load the student's ledger" />;
  const ledger = data.ledger || [];
  if (request.request_type === 'CORRECTION') {
    const target = ledger.find((l) => l.ledger_id === request.correction_of_ledger_id);
    if (!target) return <p className="text-sm text-brandl">Award #{request.correction_of_ledger_id} is not in this student's ledger; approval will be refused.</p>;
    return (
      <div className={`text-sm rounded-lg p-3 ${target.status === 'ACTIVE' ? 'bg-blue-50' : 'bg-red-50'}`}>
        <div className="font-semibold mb-1">Correction of award #{target.ledger_id}</div>
        <div>{target.event_name} · <b>{formatPoints(target.points_awarded)}</b> points · awarded {formatDate(target.award_date)} · {statusLabel(target.source_type)} · <Badge s={target.status} /></div>
        {target.status === 'ACTIVE' ? (
          <div className="text-mute mt-1">
            Approving keeps award #{target.ledger_id} in the ledger as <b>Superseded</b> and creates a new active award of{' '}
            <b>{formatPoints(request.claimed_points)}</b> points that supersedes it. Nothing is overwritten.
          </div>
        ) : <div className="text-brandl mt-1">This award is no longer active, so the correction cannot be approved (the backend will refuse it).</div>}
      </div>
    );
  }
  const existing = ledger.find((l) => l.event_id === request.event_id && l.status === 'ACTIVE');
  return existing ? (
    <p className="text-sm bg-red-50 rounded-lg p-3 text-brandl">
      {request.student_name} already has an active award for {request.event_name} (award #{existing.ledger_id}, {formatPoints(existing.points_awarded)} points).
      Approval will be refused; a change to that award needs a correction request.
    </p>
  ) : (
    <p className="text-sm bg-blue-50 rounded-lg p-3">
      Approving creates an official award of <b>{formatPoints(request.claimed_points)}</b> points for {request.student_name} ({request.usn}) for {request.event_name}.
      Current total: {formatPoints(data.earned_points)} / {formatPoints(data.required_points)}.
    </p>
  );
}

export default function DeanClaims() {
  const [tab, setTab] = useState('pending');
  const [type, setType] = useState('');
  const [eventId, setEventId] = useState('');
  const [club, setClub] = useState('');            // '' all | 'none' non-club | club_id
  const [status, setStatus] = useState('');
  const [selected, setSelected] = useState(null);
  const [lastApproval, setLastApproval] = useState(null);
  const events = useApi(({ signal }) => api.get('/events', { signal }), []);
  const clubs = useApi(({ signal }) => api.get('/clubs', { signal }), []);
  const query = { status: tab === 'pending' ? 'PENDING_DEAN' : status || undefined, request_type: type, event_id: eventId };
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/requests', { signal, query }), [tab, type, eventId, status]);
  // The API has no club filter: narrow by the request's club_id here.
  const list = (data || [])
    .filter((r) => (tab === 'pending' ? r.status === 'PENDING_DEAN' : DECIDED.includes(r.status)))
    .filter((r) => !club || (club === 'none' ? r.club_id == null : String(r.club_id) === club));
  const clubName = (id) => (clubs.data || []).find((c) => c.club_id === id)?.club_name || `Club #${id}`;

  const reviewer = useRequestReview({
    endpoint: 'dean-review',
    confirmText: (r, action) => {
      if (action === 'REJECT') return `Reject request #${r.request_id} (${r.student_name}, ${r.event_name})?\n\nNo points are awarded and the student sees your comment.`;
      if (r.request_type === 'CORRECTION') {
        return `Approve correction #${r.request_id} for ${r.student_name} (${r.usn}), ${r.event_name}?\n\n`
          + `Award #${r.correction_of_ledger_id} stays in the ledger as SUPERSEDED (its values are kept) and a new ACTIVE `
          + `POINT_LEDGER award of ${formatPoints(r.claimed_points)} points is created. Nothing is overwritten.`;
      }
      return `Approve request #${r.request_id}?\n\nThis creates an official POINT_LEDGER award of ${formatPoints(r.claimed_points)} points `
        + `for ${r.student_name} (${r.usn}) for ${r.event_name}. Awards are never edited; a later change needs a correction that supersedes it.`;
    },
    successText: (r, action, out) => (action === 'APPROVE'
      ? `Request #${r.request_id} approved — award #${out.ledger_id} created (${formatPoints(out.claimed_points)} points).`
      : `Request #${r.request_id} rejected.`),
    onDone: (r, action, out) => {
      if (out && action === 'APPROVE') setLastApproval({ request: r, ledgerId: out.ledger_id });
      if (out && selected?.request_id === r.request_id) setSelected(null);
      reload();
    },
  });

  const tabBtn = (key, label) => (
    <button type="button" onClick={() => { setTab(key); setSelected(null); }}
      className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${tab === key ? 'bg-brand text-white' : 'border border-brand text-brand hover:bg-soft/30'}`}>{label}</button>
  );
  const eventCell = (r) => <div className="min-w-[8rem]">{r.event_name}<div className="text-xs">{clubCell(r) || <span className="text-mute">{clubName(r.club_id)}</span>}</div></div>;
  const files = (r) => (
    <button type="button" className={GHOST} onClick={() => setSelected(selected?.request_id === r.request_id ? null : r)}>
      {r.document_count} · {selected?.request_id === r.request_id ? 'Hide' : 'Details'}
    </button>
  );

  return (
    <Page title="Claim Approvals" sub="Final decisions on student requests. Approval writes the official Activity Point Ledger; rejection needs a comment.">
      <div className="flex flex-wrap gap-2 mb-3">
        {tabBtn('pending', 'Pending Dean approval')}
        {tabBtn('decided', 'Decided')}
        <select className={`${INPUT} max-w-[11rem]`} value={type} onChange={(e) => setType(e.target.value)} aria-label="Request type">
          <option value="">All request types</option>
          {REQUEST_TYPES.map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}
        </select>
        <select className={`${INPUT} max-w-[11rem]`} value={club} onChange={(e) => setClub(e.target.value)} aria-label="Club">
          <option value="">All clubs</option><option value="none">Non-club events</option>
          {(clubs.data || []).map((c) => <option key={c.club_id} value={c.club_id}>{c.club_name}</option>)}
        </select>
        <select className={`${INPUT} max-w-[13rem]`} value={eventId} onChange={(e) => setEventId(e.target.value)} aria-label="Event">
          <option value="">All events</option>
          {(events.data || []).map((e) => <option key={e.event_id} value={e.event_id}>{e.event_name} ({e.academic_year})</option>)}
        </select>
        {tab === 'decided' && (
          <select className={`${INPUT} max-w-[10rem]`} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Outcome">
            <option value="">All outcomes</option>
            {DECIDED.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
          </select>
        )}
        {loading && data && <span className="text-xs text-mute self-center" role="status">Refreshing…</span>}
      </div>
      {lastApproval && (
        <p className="bg-emerald-50 text-ok rounded-lg px-3 py-2 text-sm mb-3 flex justify-between gap-2" role="status">
          <span>Request #{lastApproval.request.request_id} ({lastApproval.request.student_name}, {lastApproval.request.event_name}) approved — ledger award #{lastApproval.ledgerId} created.</span>
          <button type="button" className="text-mute" onClick={() => setLastApproval(null)} aria-label="Dismiss">✕</button>
        </p>
      )}
      <Card>
        {error && <ErrorState error={error} onRetry={reload} />}
        {loading && !data ? <Loading label="Loading requests…" /> : tab === 'pending' ? (
          <Table cols={['ID', 'Student', 'Event', 'Type', 'Claimed', 'Reason', 'Corrects', 'Club review', 'Files', 'Submitted', 'Decision']}
            empty={type || eventId || club ? 'No pending requests match these filters.' : 'Nothing is waiting for your decision.'}
            rows={list.map((r) => row(r.request_id, [
              `#${r.request_id}`, <StudentCell request={r} />, eventCell(r),
              <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
              formatPoints(r.claimed_points),
              <div className="text-xs min-w-[12rem] max-w-xs break-words">{r.reason}</div>,
              r.correction_of_ledger_id != null ? `Award #${r.correction_of_ledger_id}` : '—',
              r.club_id == null ? <span className="text-xs text-mute">Non-club: no club review</span>
                : <ReviewNote at={r.club_reviewed_at} by={r.club_reviewed_by} comment={r.club_review_comment} none="Not club-reviewed" />,
              files(r),
              <span className="text-xs whitespace-nowrap">{formatDateTime(r.created_at)}</span>,
              <ReviewCell request={r} reviewer={reviewer} actions={[{ action: 'APPROVE', label: 'Approve', primary: true }, { action: 'REJECT', label: 'Reject' }]} />,
            ], selected?.request_id === r.request_id ? 'bg-soft/20' : ''))} />
        ) : (
          <Table cols={['ID', 'Student', 'Event', 'Type', 'Claimed', 'Outcome', 'Award', 'Club review', 'Dean decision', 'Files', 'Updated']}
            empty="No decided requests match these filters."
            rows={list.map((r) => row(r.request_id, [
              `#${r.request_id}`, <StudentCell request={r} />, eventCell(r),
              <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
              formatPoints(r.claimed_points),
              <Badge s={r.status} />,
              r.ledger_id != null ? `#${r.ledger_id}` : '—',
              <ReviewNote at={r.club_reviewed_at} by={r.club_reviewed_by} comment={r.club_review_comment} none={r.club_id == null ? 'Non-club' : 'Not club-reviewed'} />,
              <ReviewNote at={r.dean_decided_at} by={r.dean_decided_by} comment={r.dean_decision_comment} />,
              files(r),
              <span className="text-xs whitespace-nowrap">{formatDateTime(r.updated_at)}</span>,
            ], selected?.request_id === r.request_id ? 'bg-soft/20' : ''))} />
        )}
      </Card>
      {selected && (
        <Card title={`Request #${selected.request_id} · ${selected.student_name} (${selected.usn}) · ${selected.event_name}`}>
          <p className="text-sm mb-3"><span className="text-mute">Reason:</span> {selected.reason}</p>
          {selected.status === 'PENDING_DEAN' && <div className="mb-3"><LedgerImpact key={selected.request_id} request={selected} /></div>}
          <div className="text-sm font-semibold mb-1">Evidence</div>
          <DocumentList key={selected.request_id} query={{ request_id: selected.request_id }} empty="No documents are attached to this request." />
        </Card>
      )}
    </Page>
  );
}
