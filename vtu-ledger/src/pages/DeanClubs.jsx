import { useState } from 'react';
import { api } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { formatDateTime, formatPoints, statusLabel } from '../utils/format.js';
import { Page, Card, Stat, Badge, Table, row, Loading, ErrorState, GHOST, INPUT } from '../components/ui.jsx';
import { ReviewNote, StudentCell } from '../components/Review.jsx';
import { ISSUE_TYPES } from '../domain.js';

/** Which requests each accountability column covers (app/routers/events.py: accountability). */
const BUCKETS = {
  OPEN: { label: 'Open club issues', statuses: ['PENDING_CLUB'] },
  DEAN: { label: 'Pending Dean', statuses: ['PENDING_DEAN'] },
  RESOLVED: { label: 'Resolved (approved, rejected or resolved)', statuses: ['APPROVED', 'REJECTED', 'RESOLVED'] },
  WITHDRAWN: { label: 'Withdrawn (not counted)', statuses: ['WITHDRAWN'] },
};

/**
 * Counts come only from GET /clubs/accountability (MISSING_FROM_LIST and CORRECTION requests, computed by the
 * backend from request status). The drill-down lists the underlying requests from GET /requests for context.
 */
export default function DeanClubs() {
  const acc = useApi(({ signal }) => api.get('/clubs/accountability', { signal }), []);
  const requests = useApi(({ signal }) => api.get('/requests', { signal }), []);
  const [club, setClub] = useState(null);
  const [bucket, setBucket] = useState('');
  const rows = acc.data || [];
  const total = (k) => rows.reduce((n, c) => n + c[k], 0);
  const issues = (requests.data || [])
    .filter((r) => club && r.club_id === club.club_id && ISSUE_TYPES.includes(r.request_type))
    .filter((r) => !bucket || BUCKETS[bucket].statuses.includes(r.status));
  const refresh = () => { acc.reload(); requests.reload(); };

  return (
    <Page title="Club Accountability" sub="Missing-from-list and correction requests per club, counted live from request status.">
      {acc.error && <ErrorState error={acc.error} onRetry={acc.reload} />}
      {acc.loading && !acc.data ? <Loading label="Loading accountability…" /> : (
        <>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <Stat n={total('open_club_issues')} label="Open club issues (all clubs)" tone={total('open_club_issues') ? 'text-brandl' : 'text-ok'} />
            <Stat n={total('pending_dean_issues')} label="Issues pending Dean" tone="text-blue-800" />
            <Stat n={total('resolved_issues')} label="Resolved issues" tone="text-ok" />
          </div>
          <Card>
            <div className="flex justify-end mb-2">
              <button type="button" className={GHOST} onClick={refresh} disabled={acc.loading}>{acc.loading ? 'Refreshing…' : 'Refresh'}</button>
            </div>
            <Table cols={['Club', 'Open club issues', 'Pending Dean', 'Resolved', '']} empty="No clubs."
              rows={rows.map((c) => row(c.club_id, [
                <span className="font-medium">{c.club_name}</span>,
                <Badge s={String(c.open_club_issues)} tone={c.open_club_issues ? 'err' : 'ok'} />,
                <Badge s={String(c.pending_dean_issues)} tone={c.pending_dean_issues ? 'info' : 'mute'} />,
                <Badge s={String(c.resolved_issues)} tone="ok" />,
                <button type="button" className={GHOST} onClick={() => { setClub(club?.club_id === c.club_id ? null : c); setBucket(''); }}>
                  {club?.club_id === c.club_id ? 'Hide' : 'View issues'}
                </button>,
              ], club?.club_id === c.club_id ? 'bg-soft/20' : c.open_club_issues ? 'bg-amber-50' : ''))} />
          </Card>
        </>
      )}
      {club && (
        <Card title={`Issues · ${club.club_name}`}>
          <div className="flex flex-wrap gap-2 mb-2 items-center">
            <select className={`${INPUT} max-w-[18rem]`} value={bucket} onChange={(e) => setBucket(e.target.value)} aria-label="Issue status">
              <option value="">All issue requests</option>
              {Object.entries(BUCKETS).map(([k, b]) => <option key={k} value={k}>{b.label}</option>)}
            </select>
            <span className="text-xs text-mute">Counts above come from the accountability endpoint; withdrawn requests are listed for context but not counted.</span>
          </div>
          {requests.error && <ErrorState error={requests.error} title="Could not load requests" onRetry={requests.reload} />}
          {requests.loading && !requests.data ? <Loading label="Loading requests…" /> : (
            <Table cols={['ID', 'Student', 'Event', 'Type', 'Claimed', 'Status', 'Club review', 'Dean decision', 'Updated']}
              empty="No issue requests in this view."
              rows={issues.map((r) => row(r.request_id, [
                `#${r.request_id}`, <StudentCell request={r} />, r.event_name,
                <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
                formatPoints(r.claimed_points),
                <Badge s={r.status} />,
                <ReviewNote at={r.club_reviewed_at} comment={r.club_review_comment} none="Not club-reviewed" />,
                <ReviewNote at={r.dean_decided_at} comment={r.dean_decision_comment} />,
                <span className="text-xs whitespace-nowrap">{formatDateTime(r.updated_at)}</span>,
              ]))} />
          )}
        </Card>
      )}
    </Page>
  );
}
