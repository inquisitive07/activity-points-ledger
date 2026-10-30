import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Download, Eye } from 'lucide-react';
import { useAuth } from '../context/AuthContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { api, downloadDocument } from '../api/client.js';
import { ACCEPT_ATTR, STUDENT_DOCUMENT_TYPES, defaultDocumentType, fileProblem, uploadAndAttach } from '../api/documents.js';
import { useApi } from '../hooks/useApi.js';
import { formatBytes, formatDate, formatDateTime, formatPoints, formatTime, safeHttpUrl, statusLabel } from '../utils/format.js';
import { Page, Card, Field, Badge, Table, row, FileAttachments, AttachmentResults, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';
import { LedgerTable, PointsProgress } from '../components/Ledger.jsx';
import { DocumentPreview, PREVIEWABLE } from '../components/Documents.jsx';

/** Event statuses that accept new requests (CLAUDE.md 18.5); CLOSED is view-only, DRAFT is never shown. */
const ACCEPTS_REQUESTS = ['PUBLISHED', 'COMPLETED'];
const OPEN_REQUEST = ['PENDING_CLUB', 'PENDING_DEAN'];
const REQUEST_TYPES = ['NEW_ACTIVITY', 'MISSING_FROM_LIST', 'CORRECTION'];
const DOCUMENT_TYPES = ['CERTIFICATE', 'PHOTO', 'OTHER', 'SIGNED_POINT_LIST', 'ATTENDANCE_SHEET', 'REPORT'];
/** Mirrors the backend Points type: NUMERIC(6,2), never negative. Kept as a string; no float arithmetic. */
const POINTS_PATTERN = /^\d{1,4}(\.\d{1,2})?$/;

const useEvents = () => useApi(({ signal }) => api.get('/events', { signal }), []);
const useStudent = (usn) => useApi(({ signal }) => api.get(`/students/${encodeURIComponent(usn)}`, { signal }), [usn], { enabled: !!usn });
const fileProps = { types: STUDENT_DOCUMENT_TYPES, accept: ACCEPT_ATTR, check: fileProblem, defaultType: defaultDocumentType };


/** Dashboard: totals and ledger history from GET /students/{usn}; nothing is summed in the browser. */
export function StudentHome() {
  const { user } = useAuth();
  const usn = user.student_usn;
  const { data: me, error, loading, reload } = useApi(
    ({ signal }) => api.get(`/students/${encodeURIComponent(usn)}`, { signal }), [usn], { enabled: !!usn });

  if (!usn) return <Page title="My Dashboard" sub=""><ErrorState error="This account is not linked to a student record." title="No student record" /></Page>;
  if (!me) {
    return (
      <Page title="My Dashboard" sub={`${user.display_name} · ${usn}`}>
        {error ? <ErrorState error={error} onRetry={reload} /> : <Loading label="Loading your activity points…" />}
      </Page>
    );
  }

  return (
    <Page title="My Dashboard" sub={`${me.name} · ${me.usn} · ${statusLabel(me.entry_type)} entry · ${me.department_code}`}>
      {error && <ErrorState error={error} title="Could not refresh" onRetry={reload} />}
      {me.student_status !== 'ACTIVE' && (
        <p className="bg-amber-100 text-amber-800 rounded-lg px-3 py-2 text-sm mb-4">
          Your record is <Badge s={me.student_status} />. Your history stays available; only active students can submit new requests.
        </p>
      )}
      <Card title="Progress"><PointsProgress summary={me} /></Card>
      <Card title="Activity Points Ledger"><LedgerTable ledger={me.ledger || []} /></Card>
    </Page>
  );
}

/** Published events (students never see DRAFT); shows which ones still take new requests. */
export function StudentEvents() {
  const { data, error, loading, reload } = useEvents();
  const [show, setShow] = useState('ALL');
  const [q, setQ] = useState('');
  const events = (data || []).filter((e) => e.event_status !== 'DRAFT')
    .filter((e) => show === 'ALL' || (show === 'OPEN') === ACCEPTS_REQUESTS.includes(e.event_status))
    .filter((e) => `${e.event_name} ${e.club_name || ''} ${e.venue || ''}`.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <Page title="Events" sub="Club and non-club activities. Published and completed events accept new point requests.">
      {error && <ErrorState error={error} onRetry={reload} />}
      <Card>
        <div className="flex flex-wrap gap-2 mb-3">
          <input className={`${INPUT} max-w-xs`} placeholder="Search event, club or venue…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className={`${INPUT} max-w-[14rem]`} value={show} onChange={(e) => setShow(e.target.value)} aria-label="Filter events">
            <option value="ALL">All events</option><option value="OPEN">Accepting requests</option><option value="VIEW">View only</option>
          </select>
        </div>
        {loading && !data ? <Loading label="Loading events…" /> : (
          <Table cols={['Event', 'Club', 'Date', 'Venue', 'Points', 'Registration', 'Status', 'Requests']}
            empty={data?.length ? 'No events match these filters.' : 'No events have been published yet.'}
            rows={events.map((e) => {
              const url = safeHttpUrl(e.registration_url);
              const open = ACCEPTS_REQUESTS.includes(e.event_status);
              const times = [formatTime(e.start_time), formatTime(e.end_time)].filter(Boolean).join('–');
              return row(e.event_id, [
                <div className="min-w-[10rem]"><div className="font-medium">{e.event_name}</div>{e.description && <div className="text-xs text-mute line-clamp-2">{e.description}</div>}</div>,
                e.club_name || <span className="text-mute">Non-club</span>,
                <div className="whitespace-nowrap">{formatDate(e.event_date)}{times && <div className="text-xs text-mute">{times}</div>}</div>,
                e.venue || '—',
                e.standard_points == null ? '—' : formatPoints(e.standard_points),
                url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-info underline">Register</a> : '—',
                <Badge s={e.event_status} />,
                open
                  ? <div className="flex flex-col items-start gap-1"><Badge s="Accepts requests" tone="ok" /><Link to={`/student/claims?event=${e.event_id}`} className="text-xs text-brand underline">Claim points</Link></div>
                  : <Badge s="View only" tone="mute" />,
              ]);
            })} />
        )}
      </Card>
    </Page>
  );
}

/** Point requests (POST /requests + attachments), evidence-only uploads, and the student's request history. */
export function Claims() {
  const { user } = useAuth();
  const toast = useToast();
  const usn = user.student_usn;
  const [params] = useSearchParams();
  const events = useEvents();
  const student = useStudent(usn);
  const requests = useApi(({ signal }) => api.get('/requests', { signal }), []);

  const eligible = (events.data || []).filter((e) => ACCEPTS_REQUESTS.includes(e.event_status));
  const activeAwards = (student.data?.ledger || []).filter((l) => l.status === 'ACTIVE');

  // --- new request ---------------------------------------------------------------------------------------------
  const blank = { type: 'NEW_ACTIVITY', eventId: '', points: '', reason: '', ledgerId: '' };
  const [form, setForm] = useState(() => ({ ...blank, eventId: params.get('event') || '' }));
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [results, setResults] = useState(null);       // { kind, id, eventId, list } after a partial failure
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const selected = eligible.find((e) => String(e.event_id) === form.eventId) || null;
  const correctable = selected ? activeAwards.filter((l) => l.event_id === selected.event_id) : [];
  const ledgerId = correctable.some((l) => String(l.ledger_id) === form.ledgerId) ? form.ledgerId
    : correctable.length === 1 ? String(correctable[0].ledger_id) : '';

  const problem = () => {
    if (!selected) return 'Choose an event that accepts requests.';
    if (!POINTS_PATTERN.test(form.points.trim())) return 'Claimed points must be a number from 0 to 9999.99 with at most two decimal places.';
    if (form.reason.trim().length < 3) return 'Please give a reason (at least 3 characters).';
    if (form.type === 'CORRECTION' && !ledgerId) return 'Select the award you want corrected.';
    if (files.some((f) => f.problem)) return 'Remove the files marked with a problem before submitting.';
    return '';
  };

  const finish = (outcome, successText) => {
    const failed = outcome.list.filter((r) => !r.ok);
    if (failed.length) {
      setResults(outcome);
      toast(`${outcome.label} ${failed.length} of ${outcome.list.length} attachment(s) failed. See the details below.`, 'error');
    } else {
      setResults(null);
      toast(successText);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    const p = problem();
    setError(p);
    if (p) return;
    setBusy('Submitting request…'); setResults(null);
    let req;
    try {
      req = await api.post('/requests', {
        event_id: selected.event_id, request_type: form.type, claimed_points: form.points.trim(), reason: form.reason.trim(),
        ...(form.type === 'CORRECTION' ? { correction_of_ledger_id: Number(ledgerId) } : {}),
      });
    } catch (err) {
      setError(err.message); toast(err); setBusy('');
      return;
    }
    const route = req.status === 'PENDING_CLUB' ? 'sent to the club for verification' : 'sent to the Dean';
    let list = [];
    if (files.length) {
      setBusy(`Uploading ${files.length} attachment(s)…`);
      list = await uploadAndAttach(files, req.event_id, { kind: 'request', id: req.request_id });
    }
    finish({ kind: 'request', id: req.request_id, eventId: req.event_id, list, label: `Request #${req.request_id} was submitted, but` },
      `Request #${req.request_id} submitted and ${route}${list.length ? ` with ${list.length} attachment(s)` : ''}.`);
    setForm({ ...blank }); setFiles([]); setBusy('');
    requests.reload();
  };

  // --- evidence only -------------------------------------------------------------------------------------------
  const [awardId, setAwardId] = useState('');
  const [evidence, setEvidence] = useState([]);
  const award = activeAwards.find((l) => String(l.ledger_id) === awardId) || null;

  const attachEvidence = async () => {
    if (!award || !evidence.length) return;
    if (evidence.some((f) => f.problem)) { toast('Remove the files marked with a problem first.', 'error'); return; }
    setBusy('Uploading evidence…'); setResults(null);
    const list = await uploadAndAttach(evidence, award.event_id, { kind: 'ledger', id: award.ledger_id });
    const ok = list.filter((r) => r.ok).length;
    finish({ kind: 'ledger', id: award.ledger_id, eventId: award.event_id, list, label: ok ? `${ok} file(s) were attached to award #${award.ledger_id}, but` : `No evidence was attached to award #${award.ledger_id}:` },
      `Evidence attached to award #${award.ledger_id} (${award.event_name}). No request was created and your points are unchanged.`);
    setEvidence([]); setBusy('');
  };

  const retry = async () => {
    const failed = results.list.filter((r) => !r.ok).map((r) => ({ ...r.item, documentId: r.documentId ?? undefined }));
    setBusy('Retrying attachments…');
    const again = await uploadAndAttach(failed, results.eventId, { kind: results.kind, id: results.id });
    const merged = results.list.map((r) => again.find((a) => a.item.key === r.item.key) || r);
    const target = results.kind === 'request' ? `request #${results.id}` : `award #${results.id}`;
    finish({ ...results, list: merged, label: `Retried attachments for ${target}:` }, `All attachments are now attached to ${target}.`);
    setBusy('');
    if (results.kind === 'request') requests.reload();
  };

  // --- withdraw ------------------------------------------------------------------------------------------------
  const [withdrawing, setWithdrawing] = useState(null);
  const withdraw = async (r) => {
    if (!window.confirm(`Withdraw request #${r.request_id} for ${r.event_name}? A withdrawn request cannot be reopened.`)) return;
    setWithdrawing(r.request_id);
    try {
      await api.post(`/requests/${r.request_id}/withdraw`);
      toast(`Request #${r.request_id} withdrawn.`);
    } catch (err) {
      toast(err);
    } finally {
      setWithdrawing(null);
      requests.reload();
    }
  };

  const status = student.data?.student_status;
  return (
    <Page title="Claims & Evidence" sub="Request points or attach evidence to existing awards.">
      {status && status !== 'ACTIVE' && (
        <p className="bg-amber-100 text-amber-800 rounded-lg px-3 py-2 text-sm mb-4">
          Your record is <Badge s={status} />. You can view your history, but only active students can submit new requests.
        </p>
      )}
      <Card title="New claim request">
        {events.error && <ErrorState error={events.error} title="Could not load events" onRetry={events.reload} />}
        {events.loading && !events.data ? <Loading label="Loading events…" /> : (
          <form onSubmit={submit} className="grid md:grid-cols-3 gap-3" noValidate>
            <Field label="Request type">
              <select className={INPUT} value={form.type} onChange={set('type')}>
                {REQUEST_TYPES.map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}
              </select>
            </Field>
            <Field label="Event">
              <select className={INPUT} value={selected ? form.eventId : ''} onChange={set('eventId')}>
                <option value="">{eligible.length ? 'Select an event…' : 'No events accept requests right now'}</option>
                {eligible.map((v) => <option key={v.event_id} value={v.event_id}>{v.event_name} · {formatDate(v.event_date)}{v.club_name ? ` · ${v.club_name}` : ' · Non-club'}</option>)}
              </select>
            </Field>
            <Field label="Claimed points">
              <input type="text" inputMode="decimal" className={INPUT} value={form.points} onChange={set('points')} placeholder={selected?.standard_points != null ? `Advertised: ${formatPoints(selected.standard_points)}` : 'e.g. 10'} />
            </Field>
            {form.type === 'CORRECTION' && (
              <Field label="Award to correct" className="md:col-span-3">
                {student.error ? <span className="text-brandl text-sm">{student.error.message}</span> : (
                  <select className={INPUT} value={ledgerId} onChange={set('ledgerId')} disabled={!correctable.length}>
                    <option value="">{!selected ? 'Choose an event first' : correctable.length ? 'Select your active award…' : 'You have no active award for this event'}</option>
                    {correctable.map((l) => <option key={l.ledger_id} value={l.ledger_id}>Award #{l.ledger_id} · {l.event_name} · {formatPoints(l.points_awarded)} pts · {formatDate(l.award_date)}</option>)}
                  </select>
                )}
              </Field>
            )}
            {selected && (
              <p className="md:col-span-3 text-xs text-mute -mt-1">
                Route: {selected.club_id ? `${selected.club_name} verifies, then the Dean decides.` : 'Non-club event: goes directly to the Dean.'}
                {form.type === 'MISSING_FROM_LIST' && !selected.club_id && <span className="text-amber-800"> Missing-from-list requests apply only to club events (their signed lists).</span>}
              </p>
            )}
            <Field label="Reason" className="md:col-span-3"><textarea rows="2" maxLength={2000} className={INPUT} value={form.reason} onChange={set('reason')} /></Field>
            <div className="md:col-span-3">
              <div className="text-xs font-medium text-mute mb-1">Attachments (PDF, JPEG or PNG, up to 10 MB each; tagged to the selected event)</div>
              <FileAttachments files={files} setFiles={setFiles} eventLabel={selected?.event_name} disabled={!!busy} {...fileProps} />
            </div>
            {error && <p className="md:col-span-3 text-brandl text-sm" role="alert">{error}</p>}
            <div className="md:col-span-3 flex items-center gap-3">
              <button className={BTN} type="submit" disabled={!!busy}>Submit request</button>
              {busy && <span className="text-sm text-mute" role="status">{busy}</span>}
            </div>
          </form>
        )}
        {results?.kind === 'request' && <AttachmentResults results={results.list} onRetry={retry} onDismiss={() => setResults(null)} retrying={!!busy} />}
      </Card>

      <Card title="Attach evidence to an awarded row (no new claim)">
        <p className="text-sm text-mute mb-3">This only adds supporting documents to an award you already have. It does not create a request and does not change your points.</p>
        {student.error && <ErrorState error={student.error} title="Could not load your awards" onRetry={student.reload} />}
        {student.loading && !student.data ? <Loading label="Loading your awards…" /> : (
          <>
            <Field label="Active award" className="max-w-md mb-3">
              <select className={INPUT} value={award ? awardId : ''} onChange={(e) => setAwardId(e.target.value)} disabled={!activeAwards.length}>
                <option value="">{activeAwards.length ? 'Select…' : 'You have no active awards yet'}</option>
                {activeAwards.map((l) => <option key={l.ledger_id} value={l.ledger_id}>{l.event_name} · {formatPoints(l.points_awarded)} pts · award #{l.ledger_id}</option>)}
              </select>
            </Field>
            {award && <FileAttachments files={evidence} setFiles={setEvidence} eventLabel={award.event_name} disabled={!!busy} {...fileProps} />}
            <button className={`${BTN} mt-2`} disabled={!award || !evidence.length || !!busy} onClick={attachEvidence}>Attach evidence only</button>
          </>
        )}
        {results?.kind === 'ledger' && <AttachmentResults results={results.list} onRetry={retry} onDismiss={() => setResults(null)} retrying={!!busy} />}
      </Card>

      <Card title="My requests">
        {requests.error && <ErrorState error={requests.error} title="Could not load your requests" onRetry={requests.reload} />}
        {requests.loading && !requests.data ? <Loading label="Loading your requests…" /> : (
          <Table cols={['ID', 'Event', 'Type', 'Claimed', 'Files', 'Status', 'Comments', 'Submitted', 'Updated', '']}
            empty="You have not submitted any requests yet."
            rows={(requests.data || []).map((r) => row(r.request_id, [
              `#${r.request_id}`,
              r.event_name,
              <span className="whitespace-nowrap">{statusLabel(r.request_type)}</span>,
              formatPoints(r.claimed_points),
              r.document_count,
              <div><Badge s={r.status} />{r.ledger_id != null && <div className="text-xs text-mute mt-1">Award #{r.ledger_id}</div>}</div>,
              (r.club_review_comment || r.dean_decision_comment) ? (
                <div className="text-xs space-y-1 min-w-[10rem]">
                  {r.club_review_comment && <div><span className="text-mute">Club:</span> {r.club_review_comment}</div>}
                  {r.dean_decision_comment && <div><span className="text-mute">Dean:</span> {r.dean_decision_comment}</div>}
                </div>
              ) : '—',
              <span className="text-xs whitespace-nowrap">{formatDateTime(r.created_at)}</span>,
              <span className="text-xs whitespace-nowrap">{formatDateTime(r.updated_at)}</span>,
              OPEN_REQUEST.includes(r.status)
                ? <button className={GHOST} disabled={withdrawing === r.request_id} onClick={() => withdraw(r)}>{withdrawing === r.request_id ? 'Withdrawing…' : 'Withdraw'}</button>
                : null,
            ]))} />
        )}
      </Card>
    </Page>
  );
}

/** The student's vault: documents they uploaded or that back their requests/awards (scope enforced by the API). */
export function Vault() {
  const { user } = useAuth();
  const toast = useToast();
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const { data, error, loading, reload } = useApi(
    ({ signal }) => api.get('/documents', { signal, query: { document_type: type, status } }), [type, status]);
  const [downloading, setDownloading] = useState(null);
  const [preview, setPreview] = useState(null);
  const list = (data || []).filter((d) => `${d.file_name} ${d.event_name || ''}`.toLowerCase().includes(q.trim().toLowerCase()));

  const download = async (d) => {
    setDownloading(d.document_id);
    try { await downloadDocument(d.document_id, d.file_name); } catch (err) { toast(err); } finally { setDownloading(null); }
  };

  const filtered = type || status || q.trim();
  return (
    <Page title="Personal Document Vault" sub="Certificates, evidence and the official signed documents behind your awards.">
      {error && <ErrorState error={error} onRetry={reload} />}
      <Card>
        <div className="flex flex-wrap gap-2 mb-3">
          <input className={`${INPUT} max-w-xs`} placeholder="Search file or event…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className={`${INPUT} max-w-[13rem]`} value={type} onChange={(e) => setType(e.target.value)} aria-label="Document type">
            <option value="">All types</option>
            {DOCUMENT_TYPES.map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}
          </select>
          <select className={`${INPUT} max-w-[11rem]`} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Document status">
            <option value="">Active and archived</option><option value="ACTIVE">Active</option><option value="ARCHIVED">Archived</option>
          </select>
          {loading && data && <span className="text-xs text-mute self-center" role="status">Refreshing…</span>}
        </div>
        {loading && !data ? <Loading label="Loading your documents…" /> : (
          <Table cols={['File', 'Type', 'Event', 'Uploaded', 'Size', 'Status', '']}
            empty={filtered ? 'No documents match these filters.' : 'Your vault is empty. Files you upload with claims or as evidence appear here.'}
            rows={list.map((d) => row(d.document_id, [
              <div className="min-w-[10rem]"><div className="font-medium break-words">{d.file_name}</div><div className="text-xs text-mute">{d.uploaded_by === user.email ? 'Uploaded by you' : `Uploaded by ${d.uploaded_by}`}</div></div>,
              statusLabel(d.document_type),
              d.event_name || <span className="text-mute">No event</span>,
              <span className="text-xs whitespace-nowrap">{formatDateTime(d.uploaded_at)}</span>,
              <span className="whitespace-nowrap">{formatBytes(d.file_size_bytes)}</span>,
              <Badge s={d.document_status} />,
              <div className="flex gap-1">
                {PREVIEWABLE.includes(d.mime_type) && (
                  <button className={`${GHOST} flex items-center gap-1`} onClick={() => setPreview(preview?.document_id === d.document_id ? null : d)} aria-label={`Preview ${d.file_name}`}><Eye size={14} />View</button>
                )}
                <button className={`${GHOST} flex items-center gap-1`} disabled={downloading === d.document_id} onClick={() => download(d)} aria-label={`Download ${d.file_name}`}>
                  <Download size={14} />{downloading === d.document_id ? 'Downloading…' : 'Download'}
                </button>
              </div>,
            ]))} />
        )}
        {preview && <DocumentPreview key={preview.document_id} doc={preview} onClose={() => setPreview(null)} />}
      </Card>
    </Page>
  );
}
