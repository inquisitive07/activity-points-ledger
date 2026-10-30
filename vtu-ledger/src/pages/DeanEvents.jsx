import { useState } from 'react';
import { api } from '../api/client.js';
import { DEAN_EVENT_DOCUMENT_TYPES, defaultEventDocumentType } from '../api/documents.js';
import { useApi } from '../hooks/useApi.js';
import { statusLabel } from '../utils/format.js';
import { Page, Card, Loading, ErrorState, INPUT } from '../components/ui.jsx';
import { DocumentList } from '../components/Documents.jsx';
import { EVENT_STATUSES, STATUS_LEGEND, EventDocumentUpload, EventForm, EventTable, useEventTransition } from '../components/Events.jsx';

/**
 * All events, filterable. The Dean manages non-club events (club_id NULL); club events are read-only here because
 * the backend lets only the organising club's lead change them.
 */
export default function DeanEvents() {
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/events', { signal }), []);
  const clubs = useApi(({ signal }) => api.get('/clubs', { signal }), []);
  const [status, setStatus] = useState('');
  const [year, setYear] = useState('');
  const [club, setClub] = useState('');               // '' all | 'none' | club_id
  const [editing, setEditing] = useState(null);
  const [docsFor, setDocsFor] = useState(null);
  const [docsKey, setDocsKey] = useState(0);
  const transition = useEventTransition((e) => { if (editing?.event_id === e.event_id) setEditing(null); reload(); });

  const all = data || [];
  const years = [...new Set(all.map((e) => e.academic_year))].sort().reverse();
  const events = all
    .filter((e) => !status || e.event_status === status)
    .filter((e) => !year || e.academic_year === year)
    .filter((e) => !club || (club === 'none' ? e.club_id == null : String(e.club_id) === club));

  return (
    <Page title="Events" sub="Create and manage non-club activities. Club events are managed by their club leads and are read-only here.">
      <Card title={editing ? `Edit · ${editing.event_name}` : 'Create non-club event'}>
        <EventForm key={editing ? `edit-${editing.event_id}` : 'create'} event={editing} createLabel="Create non-club draft event"
          onSaved={() => { setEditing(null); reload(); }} onCancel={editing ? () => setEditing(null) : null} />
      </Card>
      <Card title="All events">
        <div className="flex flex-wrap gap-2 mb-2">
          <select className={`${INPUT} max-w-[10rem]`} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">All statuses</option>
            {EVENT_STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
          </select>
          <select className={`${INPUT} max-w-[10rem]`} value={year} onChange={(e) => setYear(e.target.value)} aria-label="Academic year">
            <option value="">All years</option>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <select className={`${INPUT} max-w-[12rem]`} value={club} onChange={(e) => setClub(e.target.value)} aria-label="Club">
            <option value="">All organisers</option><option value="none">Non-club events</option>
            {(clubs.data || []).map((c) => <option key={c.club_id} value={c.club_id}>{c.club_name}</option>)}
          </select>
        </div>
        <p className="text-xs text-mute mb-2">{STATUS_LEGEND}</p>
        {error && <ErrorState error={error} onRetry={reload} />}
        {loading && !data ? <Loading label="Loading events…" /> : (
          <EventTable events={events} showClub transition={transition} docsFor={docsFor} onDocs={setDocsFor}
            canManage={(e) => e.club_id == null} readOnlyNote="Managed by the club lead"
            onEdit={(e) => { setEditing(e); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
            empty={all.length ? 'No events match these filters.' : 'No events yet.'} />
        )}
      </Card>
      {docsFor && (
        <Card title={`Documents · ${docsFor.event_name}`}>
          <DocumentList key={docsFor.event_id} query={{ event_id: docsFor.event_id }} reloadKey={docsKey} empty="No documents are tagged to this event." />
          <EventDocumentUpload key={docsFor.event_id} event={docsFor} types={DEAN_EVENT_DOCUMENT_TYPES} defaultType={defaultEventDocumentType}
            note="Upload supporting documents (attendance sheet, photo, report or other). Signed point lists are uploaded in the OCR Pipeline: an upload here never starts OCR or awards points."
            onUploaded={() => setDocsKey((k) => k + 1)} />
        </Card>
      )}
    </Page>
  );
}
