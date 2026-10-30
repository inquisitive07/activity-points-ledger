/**
 * Dean OCR pipeline: signed-list upload -> OCR batch (polled) -> record review -> confirm -> commit to the ledger.
 * Rules live in vtu-backend/app/services/ocr.py; this screen never decides what may be awarded, it explains it and
 * shows the backend's answer. OCR and confirmation only stage records: only the commit writes POINT_LEDGER.
 *
 * Batch:  QUEUED -> PROCESSING -> REVIEW | FAILED;  REVIEW -> COMMITTED.  FAILED -> retry (new batch).
 *         QUEUED/PROCESSING stuck past OCR_STALE_MINUTES -> mark-failed -> FAILED -> retry.
 * Record: VALID | EXCEPTION -> confirm -> CONFIRMED;  -> dismiss -> DISMISSED;  -> correct -> re-validated.
 *         CONFIRMED | DISMISSED -> reopen -> re-validated (VALID | EXCEPTION). Only while the batch is in REVIEW.
 */
import { useEffect, useRef, useState } from 'react';
import { Lock } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { useToast } from '../context/ToastContext.jsx';
import { api } from '../api/client.js';
import { ACCEPT_ATTR, fileProblem, uploadDocument } from '../api/documents.js';
import { useApi } from '../hooks/useApi.js';
import { useNow, usePoll } from '../hooks/usePoll.js';
import { formatBytes, formatDateTime, formatDuration, formatPoints, statusLabel } from '../utils/format.js';
import { Page, Card, Field, Stat, Badge, Table, row, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';
import { DocumentPreview } from '../components/Documents.jsx';

const BATCH_STATUSES = ['QUEUED', 'PROCESSING', 'REVIEW', 'COMMITTED', 'FAILED'];
const IN_FLIGHT = ['QUEUED', 'PROCESSING'];
const RECORD_STATUSES = ['VALID', 'EXCEPTION', 'CONFIRMED', 'DISMISSED'];
const OPEN = ['VALID', 'EXCEPTION'];
/** Issues the backend refuses to confirm over (no matched student, no points, an existing ACTIVE award). The others
 *  are warnings the Dean may confirm over after checking (app/services/ocr.py confirm). Display only. */
const BLOCKING = ['MISSING_USN', 'INVALID_USN', 'UNKNOWN_USN', 'UNMATCHED_LOW_CONFIDENCE', 'MISSING_POINTS', 'INVALID_POINTS', 'POSSIBLE_DUPLICATE_AWARD'];
const issueTone = (code) => (BLOCKING.includes(code) ? 'err' : 'warn');

const batchElapsed = (b, now) => formatDuration((b.completed_at ? Date.parse(b.completed_at) : now) - Date.parse(b.started_at));

// --- 1. upload ----------------------------------------------------------------------------------------------------

function SignedListUpload({ onStarted }) {
  const toast = useToast();
  const events = useApi(({ signal }) => api.get('/events', { signal }), []);
  const eligible = (events.data || []).filter((e) => ['PUBLISHED', 'COMPLETED'].includes(e.event_status));
  const [eventId, setEventId] = useState('');
  const [file, setFile] = useState(null);
  const [doc, setDoc] = useState(null);           // uploaded, OCR not started yet (kept so a failed start can be retried)
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const inputRef = useRef(null);
  const problem = file && fileProblem(file);

  const start = async (document) => {
    setBusy('ocr');
    try {
      const batch = await api.post('/ocr/batches', { document_id: document.document_id });
      toast(`OCR batch #${batch.ocr_batch_id} queued for ${document.file_name}.`);
      setDoc(null); setFile(null); setError('');
      if (inputRef.current) inputRef.current.value = '';
      onStarted(batch);
    } catch (err) {
      setError(`The signed list was uploaded (document #${document.document_id}) but OCR did not start: ${err.message}`);
      toast(err);
    } finally {
      setBusy('');
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!eventId) { setError('Choose the event this signed list belongs to.'); return; }
    if (!file || problem) { setError(problem || 'Choose the signed list file.'); return; }
    setBusy('upload'); setError('');
    let uploaded;
    try {
      uploaded = await uploadDocument(file, 'SIGNED_POINT_LIST', Number(eventId));
    } catch (err) {
      setError(`Upload failed: ${err.message}`);
      toast(err);
      setBusy('');
      return;
    }
    setDoc(uploaded);
    await start(uploaded);
  };

  return (
    <Card title="1 · Upload a signed point list">
      <p className="text-sm text-mute mb-3">
        OCR only reads the list and stages its rows for your review. Nothing is awarded until you review every row and
        commit the batch.
      </p>
      {events.error && <ErrorState error={events.error} title="Could not load events" onRetry={events.reload} />}
      <form onSubmit={submit} className="grid md:grid-cols-3 gap-3 items-end" noValidate>
        <Field label="Event *">
          <select className={INPUT} value={eventId} onChange={(e) => setEventId(e.target.value)} disabled={!!busy}>
            <option value="">{events.loading && !events.data ? 'Loading events…' : 'Select a published or completed event…'}</option>
            {eligible.map((e) => <option key={e.event_id} value={e.event_id}>{e.event_name} · {e.club_name || 'Non-club'} · {e.academic_year} ({statusLabel(e.event_status)})</option>)}
          </select>
        </Field>
        <Field label="Signed list (PDF, JPEG or PNG, max 10 MB) *">
          <input ref={inputRef} type="file" accept={ACCEPT_ATTR} className="text-sm" disabled={!!busy}
            onChange={(e) => { setFile(e.target.files[0] || null); setDoc(null); setError(''); }} />
        </Field>
        <div>
          <button className={BTN} type="submit" disabled={!!busy || !!doc}>
            {busy === 'upload' ? 'Uploading…' : busy === 'ocr' ? 'Starting OCR…' : 'Upload and start OCR'}
          </button>
        </div>
      </form>
      {file && <div className="text-xs text-mute mt-2">{file.name} · {formatBytes(file.size)}{problem && <span className="text-brandl"> · {problem}</span>}</div>}
      {error && <p className="text-sm text-brandl mt-2" role="alert">{error}</p>}
      {doc && !busy && (
        <div className="mt-2 flex gap-2">
          <button type="button" className={BTN} onClick={() => start(doc)}>Start OCR for document #{doc.document_id}</button>
          <button type="button" className={GHOST} onClick={() => { setDoc(null); setError(''); }}>Upload a different file</button>
        </div>
      )}
    </Card>
  );
}

// --- 2. batches ---------------------------------------------------------------------------------------------------

function BatchList({ selectedId, onOpen, reloadKey }) {
  const [status, setStatus] = useState('');
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/ocr/batches', { signal, query: { status } }), [status, reloadKey]);
  return (
    <Card title="2 · OCR batches">
      <div className="flex flex-wrap gap-2 mb-2 items-center">
        <select className={`${INPUT} max-w-[12rem]`} value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Batch status">
          <option value="">All statuses</option>
          {BATCH_STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select>
        <button type="button" className={GHOST} onClick={reload} disabled={loading}>{loading && data ? 'Refreshing…' : 'Refresh'}</button>
        <span className="text-xs text-mute">Valid and exception counts are as extracted; open a batch for the current review state.</span>
      </div>
      {error && <ErrorState error={error} onRetry={reload} />}
      {loading && !data ? <Loading label="Loading OCR batches…" /> : (
        <Table cols={['Batch', 'Document', 'Event', 'Status', 'Pages', 'Detected', 'Valid', 'Exceptions', 'Started', 'Completed', '']}
          empty={status ? 'No batches with this status.' : 'No OCR batches yet. Upload a signed list above.'}
          rows={(data || []).map((b) => row(b.ocr_batch_id, [
            `#${b.ocr_batch_id}`,
            <div className="min-w-[9rem] break-words">{b.file_name}<div className="text-xs text-mute">Document #{b.document_id}</div></div>,
            b.event_name || <span className="text-brandl text-xs">No event tag</span>,
            <div><Badge s={b.status} />{b.error_message && <div className="text-xs text-brandl mt-1 max-w-[14rem]">{b.error_message}</div>}</div>,
            b.pages_processed ?? '—', b.records_detected ?? '—', b.valid_records ?? '—', b.exception_count ?? '—',
            <span className="text-xs whitespace-nowrap">{formatDateTime(b.started_at)}</span>,
            <span className="text-xs whitespace-nowrap">{b.completed_at ? formatDateTime(b.completed_at) : '—'}</span>,
            <button type="button" className={GHOST} onClick={() => onOpen(selectedId === b.ocr_batch_id ? null : b.ocr_batch_id)}>
              {selectedId === b.ocr_batch_id ? 'Close' : b.status === 'REVIEW' ? 'Review' : 'Open'}
            </button>,
          ], selectedId === b.ocr_batch_id ? 'bg-soft/20' : ''))} />
      )}
    </Card>
  );
}

// --- 3. one batch ---------------------------------------------------------------------------------------------------

/** QUEUED/PROCESSING: poll until terminal; offer the backend's stale-batch recovery. */
function InFlight({ batch, poll, now, onChanged }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const markFailed = async () => {
    if (!window.confirm(`Mark batch #${batch.ocr_batch_id} as failed?\n\n`
      + 'Use this only if processing was lost (for example the API restarted). The server allows it only after its '
      + 'stale period (OCR_STALE_MINUTES, 30 minutes by default) without progress, and refuses it before then.\n\n'
      + 'The batch becomes FAILED with no records and stays in the history. Nothing is reprocessed automatically: '
      + 'retry it afterwards to queue a new batch. A run that finishes late is discarded, so nothing is duplicated.')) return;
    setBusy(true); setError('');
    try {
      const out = await api.post(`/ocr/batches/${batch.ocr_batch_id}/mark-failed`);
      toast(`Batch #${out.ocr_batch_id} marked failed. You can retry it now.`);
      onChanged(out);
    } catch (err) {
      setError(err.message);
      toast(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bg-blue-50 rounded-lg p-3 text-sm mb-3" role="status">
      <div className="font-semibold">
        {batch.status === 'QUEUED' ? 'Waiting to start' : 'Reading the document'} · {batchElapsed(batch, now)} since the batch was started
      </div>
      <div className="text-xs text-mute mt-1">
        Checking automatically ({poll.polls} check{poll.polls === 1 ? '' : 's'} so far; next in about {Math.round(poll.nextDelay / 1000)} s).
        The page updates when the batch is ready for review or has failed.
      </div>
      {poll.error && <div className="text-xs text-brandl mt-1">Last check failed: {poll.error.message} Still retrying.</div>}
      <details className="mt-2">
        <summary className="text-xs cursor-pointer text-mute">Batch stuck?</summary>
        <p className="text-xs text-mute mt-1">
          If processing was interrupted (for example the API restarted), the batch never finishes and blocks new OCR runs for
          this document. After the server's stale period without progress (30 minutes by default) you can mark it failed and
          then retry it.
        </p>
        <button type="button" className={`${GHOST} mt-1`} disabled={busy} onClick={markFailed}>{busy ? 'Marking…' : 'Mark as failed'}</button>
        {error && <div className="text-xs text-brandl mt-1" role="alert">{error}</div>}
      </details>
    </div>
  );
}

function Failed({ batch, onRetried }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const retry = async () => {
    if (!window.confirm(`Retry batch #${batch.ocr_batch_id}?\n\nA new batch is queued for the same document. This failed batch and its error stay in the history.`)) return;
    setBusy(true); setError('');
    try {
      const out = await api.post(`/ocr/batches/${batch.ocr_batch_id}/retry`);
      toast(`Retry queued as batch #${out.ocr_batch_id}.`);
      onRetried(out);
    } catch (err) {
      setError(err.message);         // e.g. 409: the document has a newer batch already
      toast(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="bg-red-50 rounded-lg p-3 text-sm mb-3">
      <div className="font-semibold text-brandl">OCR failed</div>
      <div>{batch.error_message || 'No error message was recorded.'}</div>
      {batch.extracted_metadata?.retry_of_batch_id && <div className="text-xs text-mute mt-1">This batch was a retry of batch #{batch.extracted_metadata.retry_of_batch_id}.</div>}
      <button type="button" className={`${BTN} mt-2`} disabled={busy} onClick={retry}>{busy ? 'Queuing…' : 'Retry as a new batch'}</button>
      {error && <div className="text-xs text-brandl mt-1" role="alert">{error}</div>}
    </div>
  );
}

/** Event/club check against the document text (extracted_metadata.event_check) and batch-wide problems. */
function EventCheck({ batch }) {
  const meta = batch.extracted_metadata || {};
  const check = meta.event_check;
  if (!check) return null;
  const tone = check.event_status === 'MATCHED' ? 'ok' : 'warn';
  return (
    <div className="text-sm border border-soft/40 rounded-lg p-3 mb-3">
      <div className="flex flex-wrap gap-2 items-center">
        <span className="font-semibold">Event check</span>
        <span>Tagged event: <b>{batch.event_name || 'none'}</b></span>
        <Badge s={`Event ${statusLabel(check.event_status).toLowerCase()}`} tone={tone} />
        <Badge s={`Club ${statusLabel(check.club_status).toLowerCase()}`} tone={check.club_status === 'MISMATCH' ? 'warn' : 'mute'} />
      </div>
      {check.detected_text && <div className="text-xs text-mute mt-1">Title read by OCR: “{check.detected_text}”</div>}
      {check.suggested_events?.length > 0 && check.event_status !== 'MATCHED' && (
        <div className="text-xs mt-1">Events named in the document: {check.suggested_events.map((e) => `${e.event_name} (${e.academic_year}, #${e.event_id})`).join(', ')}</div>
      )}
      {(meta.batch_problems || []).map((p) => <div key={p} className="text-xs text-warn mt-1">{p}</div>)}
      <div className="text-xs text-mute mt-1">Nothing is created from OCR text: awards always go to the event the signed list is tagged to.</div>
    </div>
  );
}

function BatchDetail({ batchId, onChanged, onOpen }) {
  const toast = useToast();
  const { data, error, loading, reload } = useApi(({ signal }) => api.get(`/ocr/batches/${batchId}`, { signal }), [batchId]);
  const [live, setLive] = useState(null);                    // latest batch from polling / actions
  const batch = live && live.ocr_batch_id === batchId ? live : data;
  const inFlight = !!batch && IN_FLIGHT.includes(batch.status);
  const now = useNow(inFlight);
  const [recordsKey, setRecordsKey] = useState(0);
  const [showDoc, setShowDoc] = useState(false);
  const [result, setResult] = useState(null);                // confirm-valid / commit outcome
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');

  useEffect(() => { setLive(null); setResult(null); setActionError(''); }, [batchId]);

  const update = (next) => {
    const changed = !batch || next.status !== batch.status;
    setLive(next);
    if (changed) { setRecordsKey((k) => k + 1); onChanged(); }
  };
  const poll = usePoll(({ signal }) => api.get(`/ocr/batches/${batchId}`, { signal }), {
    key: batchId, active: inFlight, onData: update,
  });
  const doc = useApi(({ signal }) => api.get(`/documents/${batch.document_id}`, { signal }), [batch?.document_id], { enabled: showDoc && !!batch });

  if (!batch) {
    return <Card title={`Batch #${batchId}`}>{error ? <ErrorState error={error} onRetry={reload} /> : <Loading label="Loading batch…" />}</Card>;
  }
  const counts = batch.record_counts || {};
  const open = (counts.VALID || 0) + (counts.EXCEPTION || 0);
  const review = batch.status === 'REVIEW';

  const refreshAll = async () => {
    try { update(await api.get(`/ocr/batches/${batchId}`)); } catch (err) { toast(err); }
    setRecordsKey((k) => k + 1);
    onChanged();
  };

  const confirmValid = async () => {
    if (!window.confirm(`Confirm all ${counts.VALID} valid record(s) of batch #${batchId}?\n\n`
      + 'The backend validates the records again first; only records that are still VALID are confirmed. Exceptions are '
      + 'left for individual review. Confirming is not awarding: points are written only when you commit the batch.')) return;
    setBusy('confirm-valid'); setActionError('');
    try {
      const out = await api.post(`/ocr/batches/${batchId}/confirm-valid`, {});
      setResult({ kind: 'confirm-valid', ...out });
      toast(`${out.records_confirmed} record(s) confirmed. Nothing has been awarded yet.`);
    } catch (err) {
      setActionError(err.message); toast(err);
    } finally {
      setBusy(''); refreshAll();
    }
  };

  const commit = async () => {
    if (!window.confirm(`Commit batch #${batchId} (${batch.file_name}) to the official ledger?\n\n`
      + `• Each of the ${counts.CONFIRMED || 0} CONFIRMED record(s) becomes an official POINT_LEDGER award for "${batch.event_name}".\n`
      + '• All awards are written in one transaction: if any record cannot be awarded, nothing is awarded.\n'
      + '• Open missing-from-list requests of these students for this event are marked Resolved.\n'
      + '• An existing ACTIVE award is never replaced; such a record stops the commit instead.\n'
      + `• Dismissed records (${counts.DISMISSED || 0}) are not awarded. A signed list can be committed only once.`)) return;
    setBusy('commit'); setActionError('');
    try {
      const out = await api.post(`/ocr/batches/${batchId}/commit`);
      setResult({ kind: 'commit', ...out });
      toast(`Batch #${batchId} committed: ${out.awards_created} award(s) created.`);
    } catch (err) {
      setActionError(err.message); toast(err);
    } finally {
      setBusy(''); refreshAll();
    }
  };

  const rerun = async () => {
    if (!window.confirm(`Run OCR again on ${batch.file_name}?\n\nA new batch is created for the same document; batch #${batchId} and its review stay as they are. A signed list can be committed through only one batch.`)) return;
    setBusy('rerun'); setActionError('');
    try {
      const out = await api.post('/ocr/batches', { document_id: batch.document_id });
      toast(`OCR batch #${out.ocr_batch_id} queued.`);
      onChanged(); onOpen(out.ocr_batch_id);
    } catch (err) {
      setActionError(err.message); toast(err);
    } finally {
      setBusy('');
    }
  };

  const commitBlock = !review ? null : open ? `${open} record(s) still need a decision: confirm or dismiss every record first.`
    : !counts.CONFIRMED ? 'No record is confirmed; there is nothing to award.' : null;

  return (
    <Card title={`Batch #${batch.ocr_batch_id} · ${batch.file_name}`}>
      <div className="flex flex-wrap gap-2 items-center text-sm mb-3">
        <Badge s={batch.status} />
        <span>{batch.event_name || <span className="text-brandl">No event tag</span>}</span>
        <span className="text-mute text-xs">Started {formatDateTime(batch.started_at)} by {batch.started_by}{batch.completed_at && ` · finished in ${batchElapsed(batch, now)}`}</span>
        <div className="ml-auto flex gap-1">
          <button type="button" className={GHOST} onClick={() => setShowDoc(!showDoc)}>{showDoc ? 'Hide signed list' : 'Show signed list'}</button>
          {review && <button type="button" className={GHOST} disabled={!!busy} onClick={rerun}>Run OCR again</button>}
          <button type="button" className={GHOST} onClick={() => onOpen(null)}>Close</button>
        </div>
      </div>

      {inFlight && <InFlight batch={batch} poll={poll} now={now} onChanged={(b) => update(b)} />}
      {batch.status === 'FAILED' && <Failed batch={batch} onRetried={(b) => { onChanged(); onOpen(b.ocr_batch_id); }} />}

      {!inFlight && batch.status !== 'FAILED' && (
        <>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-3 mb-3">
            <Stat n={batch.pages_processed ?? '—'} label="Pages processed" />
            <Stat n={batch.records_detected ?? '—'} label="Records detected" />
            <Stat n={counts.VALID || 0} label="Valid (open)" tone="text-ok" />
            <Stat n={counts.EXCEPTION || 0} label="Exceptions (open)" tone={counts.EXCEPTION ? 'text-brandl' : 'text-ok'} />
            <Stat n={counts.CONFIRMED || 0} label="Confirmed" tone="text-ok" />
            <Stat n={counts.DISMISSED || 0} label="Dismissed" />
          </div>
          {Object.keys(batch.issue_counts || {}).length > 0 && (
            <div className="flex flex-wrap gap-1 mb-3 items-center text-xs">
              <span className="text-mute">Open exceptions by issue:</span>
              {Object.entries(batch.issue_counts).map(([code, n]) => <Badge key={code} s={`${statusLabel(code)} · ${n}`} tone={issueTone(code)} />)}
            </div>
          )}
          <EventCheck batch={batch} />
        </>
      )}

      {result?.kind === 'confirm-valid' && (
        <p className="bg-emerald-50 text-ok rounded-lg px-3 py-2 text-sm mb-3 flex justify-between gap-2" role="status">
          <span>{result.records_confirmed} record(s) confirmed{result.records_confirmed ? ` (#${result.confirmed_record_ids.join(', #')})` : ''}. Nothing has been awarded yet: commit the batch to write the ledger.</span>
          <button type="button" className="text-mute" onClick={() => setResult(null)} aria-label="Dismiss">✕</button>
        </p>
      )}
      {result?.kind === 'commit' && (
        <div className="bg-emerald-50 text-ok rounded-lg px-3 py-2 text-sm mb-3" role="status">
          <div className="font-semibold">Committed: {result.awards_created} official award(s) created.</div>
          <div>Ledger awards: {result.ledger_ids.map((id) => `#${id}`).join(', ') || '—'}</div>
          <div>Missing-from-list requests resolved: {result.requests_resolved}{result.requests_resolved ? ` (#${result.resolved_request_ids.join(', #')})` : ''}</div>
          <div className="text-xs">{result.records_confirmed} confirmed record(s) awarded · {result.records_dismissed} dismissed record(s) not awarded.</div>
        </div>
      )}
      {actionError && <p className="text-sm text-brandl mb-3" role="alert">{actionError}</p>}

      {!inFlight && (
        <div className={showDoc ? 'grid xl:grid-cols-5 gap-4' : ''}>
          <div className={showDoc ? 'xl:col-span-3 min-w-0' : ''}>
            {batch.status !== 'FAILED' && <RecordTable batch={batch} reloadKey={recordsKey} onActed={refreshAll} />}
          </div>
          {showDoc && (
            <div className="xl:col-span-2 xl:sticky xl:top-4 self-start">
              {doc.error && <ErrorState error={doc.error} title="Could not load the document" onRetry={doc.reload} />}
              {doc.data ? <DocumentPreview doc={doc.data} onClose={() => setShowDoc(false)} /> : !doc.error && <Loading label="Loading document…" />}
            </div>
          )}
        </div>
      )}
      {inFlight && showDoc && (doc.data ? <DocumentPreview doc={doc.data} onClose={() => setShowDoc(false)} /> : <Loading label="Loading document…" />)}

      {review && (
        <div className="border-t border-soft/40 mt-4 pt-3 flex flex-wrap gap-2 items-center">
          <button type="button" className={GHOST} disabled={!!busy || !counts.VALID} onClick={confirmValid}>
            {busy === 'confirm-valid' ? 'Confirming…' : `Confirm all valid (${counts.VALID || 0})`}
          </button>
          <button type="button" className={`${BTN} inline-flex items-center gap-1`} disabled={!!busy || !!commitBlock} onClick={commit}>
            {commitBlock && <Lock size={14} />}{busy === 'commit' ? 'Committing…' : 'Commit to official ledger'}
          </button>
          <span className={`text-sm ${commitBlock ? 'text-brandl' : 'text-ok'}`}>{commitBlock || `Ready: ${counts.CONFIRMED} confirmed record(s) will be awarded.`}</span>
        </div>
      )}
      {batch.status === 'COMMITTED' && <p className="text-sm text-mute mt-3">This batch is committed. Its records are kept as reviewed and can no longer change.</p>}
    </Card>
  );
}

// --- 4. records -----------------------------------------------------------------------------------------------------

const editForm = (r) => ({ matched_student_usn: r.matched_student_usn || '', extracted_name: r.extracted_name || '', extracted_points: r.extracted_points == null ? '' : formatPoints(r.extracted_points) });

function RecordTable({ batch, reloadKey, onActed }) {
  const toast = useToast();
  const batchId = batch.ocr_batch_id;
  const review = batch.status === 'REVIEW';
  const [filter, setFilter] = useState('');
  const { data, error, loading, reload } = useApi(({ signal }) => api.get(`/ocr/batches/${batchId}/records`, { signal, query: { validation_status: filter } }), [batchId, filter, reloadKey]);
  const students = useApi(({ signal }) => api.get('/students', { signal }), [], { enabled: review });
  const [comments, setComments] = useState({});
  const [edits, setEdits] = useState({});          // record id -> form; kept until the backend accepts it
  const [errors, setErrors] = useState({});
  const [acting, setActing] = useState(null);
  const [revalidated, setRevalidated] = useState([]);
  const baseline = useRef(null);

  // After an action the backend re-validates the whole batch: report other records whose status changed.
  useEffect(() => {
    if (!data || !baseline.current) return;
    const { before, except } = baseline.current;
    baseline.current = null;
    setRevalidated(data.filter((r) => r.ocr_record_id !== except && before[r.ocr_record_id] && before[r.ocr_record_id] !== r.validation_status)
      .map((r) => ({ id: r.ocr_record_id, from: before[r.ocr_record_id], to: r.validation_status })));
  }, [data]);

  const act = async (r, action, body, done) => {
    const id = r.ocr_record_id;
    setActing(id); setErrors((e) => ({ ...e, [id]: '' }));
    baseline.current = { before: Object.fromEntries((data || []).map((x) => [x.ocr_record_id, x.validation_status])), except: id };
    try {
      const out = action === 'correct' ? await api.patch(`/ocr/records/${id}`, body) : await api.post(`/ocr/records/${id}/${action}`, body);
      toast(`Record #${id} ${action === 'correct' ? 'corrected' : `${action === 'reopen' ? 'reopened' : `${action}ed`}`} — now ${statusLabel(out.validation_status)}.`);
      setComments((c) => { const { [id]: _, ...rest } = c; return rest; });
      done?.();
    } catch (err) {
      baseline.current = null;
      setErrors((e) => ({ ...e, [id]: err.message }));
      toast(err);
    } finally {
      setActing(null);
      onActed();            // refreshes the batch counts and these records
    }
  };
  const comment = (id) => ({ comment: (comments[id] || '').trim() || null });

  const saveEdit = (r) => {
    const form = edits[r.ocr_record_id];
    const orig = editForm(r);
    const body = {};
    if (form.matched_student_usn.trim().toUpperCase() !== orig.matched_student_usn) body.matched_student_usn = form.matched_student_usn.trim() || null;
    if (form.extracted_name.trim() !== orig.extracted_name) body.extracted_name = form.extracted_name.trim();
    if (form.extracted_points.trim() !== orig.extracted_points) body.extracted_points = form.extracted_points.trim();
    if (!Object.keys(body).length) { setErrors((e) => ({ ...e, [r.ocr_record_id]: 'Nothing has changed.' })); return; }
    act(r, 'correct', body, () => setEdits((x) => { const { [r.ocr_record_id]: _, ...rest } = x; return rest; }));
  };
  const setEdit = (id, k, v) => setEdits((x) => ({ ...x, [id]: { ...x[id], [k]: v } }));

  const readCell = (r) => (
    <div className="min-w-[8rem]">
      <div className="font-mono text-xs">{r.extracted_usn || <span className="text-mute">no USN read</span>}</div>
      <details className="text-xs text-mute">
        <summary className="cursor-pointer">Raw OCR</summary>
        <pre className="whitespace-pre-wrap break-all max-w-[16rem]">{JSON.stringify(r.raw_extraction, null, 1)}</pre>
      </details>
    </div>
  );

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-2 items-center">
        <span className="text-sm font-semibold">Records</span>
        <select className={`${INPUT} max-w-[11rem]`} value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Record status">
          <option value="">All records</option>
          {RECORD_STATUSES.map((s) => <option key={s} value={s}>{statusLabel(s)}</option>)}
        </select>
        {loading && data && <span className="text-xs text-mute" role="status">Refreshing…</span>}
      </div>
      {review && (
        <p className="text-xs text-mute mb-2">
          You can correct the matched student, name and points. Every correction is recorded in the audit log with the old and
          new values; what OCR read (USN and raw text) is never changed. Confirming is not awarding.
        </p>
      )}
      {revalidated.length > 0 && (
        <p className="text-xs bg-amber-50 rounded-lg px-3 py-2 mb-2" role="status">
          Re-validation also changed: {revalidated.map((c) => `#${c.id} ${statusLabel(c.from)} → ${statusLabel(c.to)}`).join(', ')}.
          <button type="button" className="ml-2 text-mute" onClick={() => setRevalidated([])} aria-label="Dismiss">✕</button>
        </p>
      )}
      {error && <ErrorState error={error} title="Could not load records" onRetry={reload} />}
      {review && <datalist id="ocr-students">{(students.data || []).map((s) => <option key={s.usn} value={s.usn}>{s.name}</option>)}</datalist>}
      {loading && !data ? <Loading label="Loading records…" /> : (
        <Table cols={['#', 'Page / row', 'OCR read', 'Matched student', 'Name on list', 'Points', 'Confidence', 'Status / issues', ...(review ? ['Review'] : [])]}
          empty={filter ? 'No records with this status.' : 'This batch has no records.'}
          rows={(data || []).map((r) => {
            const id = r.ocr_record_id;
            const form = edits[id];
            const isOpen = OPEN.includes(r.validation_status);
            const low = r.issues.includes('LOW_CONFIDENCE');
            return row(id, [
              `#${id}`,
              <span className="whitespace-nowrap">p{r.page_number}{r.row_number != null && ` · r${r.row_number}`}</span>,
              readCell(r),
              form ? <input className={`${INPUT} font-mono w-32`} list="ocr-students" value={form.matched_student_usn} onChange={(e) => setEdit(id, 'matched_student_usn', e.target.value)} aria-label="Matched student USN" placeholder="USN (blank = none)" />
                : r.matched_student_usn ? <div className="min-w-[7rem]"><div className="font-mono text-xs">{r.matched_student_usn}</div><div className="text-xs text-mute">{r.matched_student_name}</div></div>
                  : <span className="text-xs text-brandl">Not matched</span>,
              form ? <input className={`${INPUT} w-36`} value={form.extracted_name} maxLength={150} onChange={(e) => setEdit(id, 'extracted_name', e.target.value)} aria-label="Name" />
                : r.extracted_name || <span className="text-mute">—</span>,
              form ? <input className={`${INPUT} w-20`} inputMode="decimal" value={form.extracted_points} onChange={(e) => setEdit(id, 'extracted_points', e.target.value)} aria-label="Points" />
                : r.extracted_points == null ? <span className="text-xs text-brandl">Missing</span> : <b>{formatPoints(r.extracted_points)}</b>,
              r.confidence_score == null ? '—' : <span className={low ? 'text-brandl font-semibold' : ''}>{Math.round(Number(r.confidence_score) * 100)}%{low && ' (low)'}</span>,
              <div className="min-w-[12rem] max-w-xs">
                <Badge s={r.validation_status} />
                {r.issues.length > 0 && <div className="flex flex-wrap gap-1 mt-1">{r.issues.map((c) => <Badge key={c} s={c} tone={isOpen ? issueTone(c) : 'mute'} />)}</div>}
                {r.validation_message && <div className="text-xs text-mute mt-1 break-words">{r.validation_message}</div>}
              </div>,
              ...(review ? [
                <div className="min-w-[13rem]">
                  {form ? (
                    <div className="flex gap-1">
                      <button type="button" className={BTN} disabled={acting === id} onClick={() => saveEdit(r)}>{acting === id ? 'Saving…' : 'Save'}</button>
                      <button type="button" className={GHOST} disabled={acting === id} onClick={() => setEdits((x) => { const { [id]: _, ...rest } = x; return rest; })}>Cancel</button>
                    </div>
                  ) : (
                    <>
                      <input className={INPUT} placeholder="Comment (optional, audited)" maxLength={2000} value={comments[id] || ''}
                        onChange={(e) => setComments((c) => ({ ...c, [id]: e.target.value }))} aria-label={`Comment for record ${id}`} />
                      <div className="flex flex-wrap gap-1 mt-1">
                        {isOpen ? (
                          <>
                            <button type="button" className={BTN} disabled={acting === id} onClick={() => act(r, 'confirm', comment(id))}>Confirm</button>
                            <button type="button" className={GHOST} disabled={acting === id} onClick={() => act(r, 'dismiss', comment(id))}>Dismiss</button>
                            <button type="button" className={GHOST} disabled={acting === id} onClick={() => setEdits((x) => ({ ...x, [id]: editForm(r) }))}>Edit</button>
                          </>
                        ) : <button type="button" className={GHOST} disabled={acting === id} onClick={() => act(r, 'reopen', comment(id))}>Reopen</button>}
                      </div>
                    </>
                  )}
                  {errors[id] && <div className="text-xs text-brandl mt-1" role="alert">{errors[id]}</div>}
                </div>,
              ] : []),
            ], r.validation_status === 'DISMISSED' ? 'text-mute' : '');
          })} />
      )}
    </div>
  );
}

// --- page -----------------------------------------------------------------------------------------------------------

export default function DeanOcr() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('batch') ? Number(params.get('batch')) : null;
  const [listKey, setListKey] = useState(0);
  const open = (id) => setParams(id ? { batch: String(id) } : {}, { replace: true });
  const refreshList = () => setListKey((k) => k + 1);

  return (
    <Page title="OCR Verification Pipeline" sub="OCR proposes → the registry validates → you confirm → one transaction commits the awards.">
      <SignedListUpload onStarted={(b) => { refreshList(); open(b.ocr_batch_id); }} />
      <BatchList selectedId={selected} onOpen={open} reloadKey={listKey} />
      {selected && <BatchDetail key={selected} batchId={selected} onChanged={refreshList} onOpen={open} />}
    </Page>
  );
}
