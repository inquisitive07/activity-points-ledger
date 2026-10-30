import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { api } from '../api/client.js';
import { CLUB_DOCUMENT_TYPES, defaultEventDocumentType } from '../api/documents.js';
import { useApi } from '../hooks/useApi.js';
import { formatDateTime, formatPoints, statusLabel } from '../utils/format.js';
import { Page, Card, Stat, Badge, Table, row, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';
import { DocumentList } from '../components/Documents.jsx';
import { EVENT_STATUSES, STATUS_LEGEND, EventDocumentUpload, EventForm, EventTable, useEventTransition } from '../components/Events.jsx';
import { ReviewCell, ReviewNote, StudentCell, useRequestReview } from '../components/Review.jsx';
import { ISSUE_TYPES, REQUEST_TYPES } from '../domain.js';

const REQUEST_STATUSES = ['PENDING_CLUB', 'PENDING_DEAN', 'APPROVED', 'RESOLVED', 'REJECTED', 'WITHDRAWN'];

const useClubEvents = () => useApi(({ signal }) => api.get('/events', { signal }), []);

// --- dashboard --------------------------------------------------------------------------------------------------

/** Counts are derived from GET /events and GET /requests (already restricted to the lead's club by the API). */
export function ClubDashboard() {
  const { user } = useAuth();
  const events = useClubEvents();
  const requests = useApi(({ signal }) => api.get('/requests', { signal }), []);
  const evs = events.data || [], reqs = requests.data || [];
  const count = (pred) => reqs.filter(pred).length;
  const openIssues = count((r) => r.status === 'PENDING_CLUB' && ISSUE_TYPES.includes(r.request_type));
  const newPending = count((r) => r.status === 'PENDING_CLUB' && r.request_type === 'NEW_ACTIVITY');

  return (
    <Page title={`${user.club_name || 'Club'} — Dashboard`} sub="Live counts for your club's events and student requests.">
      {events.error && <ErrorState error={events.error} title="Could not load events" onRetry={events.reload} />}
      {requests.error && <ErrorState error={requests.error} title="Could not load requests" onRetry={requests.reload} />}
      {(events.loading && !events.data) || (requests.loading && !requests.data) ? <Loading label="Loading your club's activity…" /> : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-4">
            <Stat n={openIssues} label="Open club issues (missing from list / correction)" tone={openIssues ? 'text-brandl' : 'text-ok'} />
            <Stat n={newPending} label="New activity claims awaiting your review" tone={newPending ? 'text-amber-800' : 'text-ok'} />
            <Stat n={count((r) => r.status === 'PENDING_DEAN')} label="Verified, pending Dean approval" tone="text-blue-800" />
            <Stat n={count((r) => r.status === 'APPROVED')} label="Approved" tone="text-ok" />
            <Stat n={count((r) => r.status === 'RESOLVED')} label="Resolved by signed list" tone="text-ok" />
            <Stat n={count((r) => r.status === 'REJECTED')} label="Rejected" tone="text-brandl" />
          </div>
          <div className="flex flex-wrap gap-2 mb-4">
            <Link to="/club/queue" className={BTN}>Verification queue ({openIssues + newPending})</Link>
            <Link to="/club/events" className={GHOST}>Manage events</Link>
          </div>
          <div className="grid md:grid-cols-2 gap-4">
            <Card title="Events by status">
              {evs.length ? (
                <Table cols={['Status', 'Events']} rows={EVENT_STATUSES.map((s) => row(s, [<Badge s={s} />, evs.filter((e) => e.event_status === s).length]))} />
              ) : <p className="text-sm text-mute">Your club has no events yet. <Link to="/club/events" className="text-brand underline">Create one</Link>.</p>}
            </Card>
            <Card title="Requests by status and type">
              {reqs.length ? (
                <Table cols={['Status', ...REQUEST_TYPES.map(statusLabel), 'Total']}
                  rows={REQUEST_STATUSES.map((s) => row(s, [
                    <Badge s={s} />,
                    ...REQUEST_TYPES.map((t) => count((r) => r.status === s && r.request_type === t)),
                    <b>{count((r) => r.status === s)}</b>,
                  ], s === 'PENDING_CLUB' ? 'bg-amber-50' : ''))} />
              ) : <p className="text-sm text-mute">No student requests for your club's events yet.</p>}
            </Card>
          </div>
        </>
      )}
    </Page>
  );
}

// --- events -----------------------------------------------------------------------------------------------------

export function ClubEvents() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useClubEvents();
  const [editing, setEditing] = useState(null);       // event being edited
  const [docsFor, setDocsFor] = useState(null);       // event whose documents are open
  const [docsKey, setDocsKey] = useState(0);
  const transition = useEventTransition((e) => { if (editing?.event_id === e.event_id) setEditing(null); reload(); });

  return (
    <Page title={`${user.club_name || 'Club'} — Events`} sub="Create events as drafts, publish them to students, then complete and close them.">
      <Card title={editing ? `Edit · ${editing.event_name}` : 'Create event'}>
        <EventForm key={editing ? `edit-${editing.event_id}` : 'create'} event={editing}
          onSaved={() => { setEditing(null); reload(); }} onCancel={editing ? () => setEditing(null) : null} />
      </Card>
      <Card title="Your club's events">
        <p className="text-xs text-mute mb-2">{STATUS_LEGEND}</p>
        {error && <ErrorState error={error} onRetry={reload} />}
        {loading && !data ? <Loading label="Loading events…" /> : (
          <EventTable events={data || []} transition={transition} docsFor={docsFor} onDocs={setDocsFor}
            onEdit={(e) => { setEditing(e); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
            empty="Your club has no events yet. Create one above." />
        )}
      </Card>
      {docsFor && (
        <Card title={`Documents · ${docsFor.event_name}`}>
          <DocumentList key={docsFor.event_id} query={{ event_id: docsFor.event_id }} reloadKey={docsKey} empty="No documents are tagged to this event yet." />
          <EventDocumentUpload key={docsFor.event_id} event={docsFor} types={CLUB_DOCUMENT_TYPES} defaultType={defaultEventDocumentType}
            note="Upload club documents (attendance sheet, photo or report; PDF, JPEG or PNG up to 10 MB)" onUploaded={() => setDocsKey((k) => k + 1)} />
        </Card>
      )}
    </Page>
  );
}

// --- verification queue -----------------------------------------------------------------------------------------

export function ClubQueue() {
  const [tab, setTab] = useState('pending');
  const [type, setType] = useState('');
  const [eventId, setEventId] = useState('');
  const events = useClubEvents();
  const query = { status: tab === 'pending' ? 'PENDING_CLUB' : undefined, request_type: type, event_id: eventId };
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/requests', { signal, query }), [tab, type, eventId]);
  const list = (data || []).filter((r) => (tab === 'pending' ? r.status === 'PENDING_CLUB' : r.status !== 'PENDING_CLUB'));
  const [evidence, setEvidence] = useState(null);

  const reviewer = useRequestReview({
    endpoint: 'club-review',
    confirmText: (r, action) => (action === 'VERIFY'
      ? `Verify request #${r.request_id} (${r.student_name}, ${r.event_name}) and forward it to the Dean for the final decision?`
      : `Reject request #${r.request_id} (${r.student_name}, ${r.event_name})?\n\nA club rejection is final; the student sees your comment and may submit a new request.`),
    successText: (r, action, out) => (action === 'VERIFY' ? `Request #${r.request_id} verified — now ${statusLabel(out.status)}.` : `Request #${r.request_id} rejected.`),
    onDone: (r, action, out) => { if (out && evidence?.request_id === r.request_id) setEvidence(null); reload(); },
  });

  const tabBtn = (key, label) => (
    <button type="button" onClick={() => { setTab(key); setEvidence(null); }}
      className={`px-3 py-1.5 rounded-lg text-sm font-semibold ${tab === key ? 'bg-brand text-white' : 'border border-brand text-brand hover:bg-soft/30'}`}>{label}</button>
  );
  const files = (r) => (
    <button type="button" className={GHOST} onClick={() => setEvidence(evidence?.request_id === r.request_id ? null : r)}>
      {r.document_count} · {evidence?.request_id === r.request_id ? 'Hide' : 'View'}
    </button>
  );

  return (
    <Page title="Verification Queue" sub="Verify claims for your events, then forward them to the Dean. Rejections need a comment. Only the Dean can approve or award points.">
      <div className="flex flex-wrap gap-2 mb-3">
        {tabBtn('pending', 'Pending club review')}
        {tabBtn('reviewed', 'Reviewed / history')}
        <select className={`${INPUT} max-w-[12rem]`} value={type} onChange={(e) => setType(e.target.value)} aria-label="Request type">
          <option value="">All request types</option>
          {REQUEST_TYPES.map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}
        </select>
        <select className={`${INPUT} max-w-[14rem]`} value={eventId} onChange={(e) => setEventId(e.target.value)} aria-label="Event">
          <option value="">All events</option>
          {(events.data || []).map((e) => <option key={e.event_id} value={e.event_id}>{e.event_name}</option>)}
        </select>
        {loading && data && <span className="text-xs text-mute self-center" role="status">Refreshing…</span>}
      </div>
      <Card>
        {error && <ErrorState error={error} onRetry={reload} />}
        {loading && !data ? <Loading label="Loading requests…" /> : tab === 'pending' ? (
          <Table cols={['ID', 'Student', 'Event', 'Type', 'Claimed', 'Reason', 'Corrects', 'Files', 'Submitted', 'Review']}
            empty={type || eventId ? 'No pending requests match these filters.' : 'Queue is clear.'}
            rows={list.map((r) => row(r.request_id, [
              `#${r.request_id}`, <StudentCell request={r} />, r.event_name,
              <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
              formatPoints(r.claimed_points),
              <div className="text-xs min-w-[12rem] max-w-xs break-words">{r.reason}</div>,
              r.correction_of_ledger_id != null ? `Award #${r.correction_of_ledger_id}` : '—',
              files(r),
              <span className="text-xs whitespace-nowrap">{formatDateTime(r.created_at)}</span>,
              <ReviewCell request={r} reviewer={reviewer} actions={[{ action: 'VERIFY', label: 'Verify & forward', primary: true }, { action: 'REJECT', label: 'Reject' }]} />,
            ], evidence?.request_id === r.request_id ? 'bg-soft/20' : ''))} />
        ) : (
          <Table cols={['ID', 'Student', 'Event', 'Type', 'Claimed', 'Status', 'Club review', 'Dean decision', 'Files', 'Updated']}
            empty="No reviewed requests yet."
            rows={list.map((r) => row(r.request_id, [
              `#${r.request_id}`, <StudentCell request={r} />, r.event_name,
              <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
              formatPoints(r.claimed_points),
              <Badge s={r.status} />,
              <ReviewNote at={r.club_reviewed_at} comment={r.club_review_comment} none="Not club-reviewed" />,
              <ReviewNote at={r.dean_decided_at} comment={r.dean_decision_comment} />,
              files(r),
              <span className="text-xs whitespace-nowrap">{formatDateTime(r.updated_at)}</span>,
            ], evidence?.request_id === r.request_id ? 'bg-soft/20' : ''))} />
        )}
      </Card>
      {evidence && (
        <Card title={`Evidence · request #${evidence.request_id} · ${evidence.student_name} · ${evidence.event_name}`}>
          <p className="text-sm mb-2"><span className="text-mute">Reason:</span> {evidence.reason}</p>
          <DocumentList key={evidence.request_id} query={{ request_id: evidence.request_id }} empty="No documents are attached to this request." />
        </Card>
      )}
    </Page>
  );
}
