/**
 * Event management shared by the Club Lead and Dean screens: create/edit form with per-field backend errors,
 * one-step status transitions with confirmation, the events table, and event document uploads.
 * The backend decides who may manage an event (club lead: own club; Dean: non-club events only).
 */
import { useState } from 'react';
import { useToast } from '../context/ToastContext.jsx';
import { api } from '../api/client.js';
import { ACCEPT_ATTR, fileProblem, uploadAndAttach } from '../api/documents.js';
import { formatDate, formatPoints, formatTime, safeHttpUrl, statusLabel } from '../utils/format.js';
import { Field, Badge, Table, row, FileAttachments, AttachmentResults, BTN, GHOST, INPUT } from './ui.jsx';

export const EVENT_STATUSES = ['DRAFT', 'PUBLISHED', 'COMPLETED', 'CLOSED'];
/** The backend allows exactly one step forward (app/services/events.py NEXT_STATUS). */
export const NEXT_STATUS = { DRAFT: 'PUBLISHED', PUBLISHED: 'COMPLETED', COMPLETED: 'CLOSED' };
export const EDITABLE = ['DRAFT', 'PUBLISHED'];
const STEP = {
  PUBLISHED: { label: 'Publish', confirm: 'Students will be able to see this event and submit point requests for it.' },
  COMPLETED: { label: 'Mark completed', confirm: 'Students can still submit requests for a completed event, but it can no longer be edited.' },
  CLOSED: { label: 'Close', confirm: 'A closed event stays visible to students but accepts no new requests. This is the final status.' },
};
export const STATUS_LEGEND = 'Draft: only the organiser sees it · Published: visible to students, accepts requests · Completed: still accepts requests, no longer editable · Closed: visible, no new requests.';

const EVENT_FIELDS = ['event_name', 'event_date', 'academic_year', 'description', 'start_time', 'end_time', 'venue', 'registration_url', 'standard_points'];
const REQUIRED = ['event_name', 'event_date', 'academic_year'];

const toForm = (e) => Object.fromEntries(EVENT_FIELDS.map((f) => [f,
  f === 'start_time' || f === 'end_time' ? formatTime(e?.[f]) : e?.[f] == null ? '' : String(e[f])]));

/** Form value -> API value: trimmed text, blank optional fields become null. Points stay strings (no floats). */
function apiValue(field, value) {
  const v = String(value ?? '').trim();
  if (!v) return REQUIRED.includes(field) ? '' : null;
  return field === 'standard_points' ? formatPoints(v) : v;
}

/** Spread backend validation errors onto fields. Anything not tied to a field stays as the general message. */
function fieldErrorsFrom(err) {
  const out = {};
  Object.entries(err.fieldErrors || {}).forEach(([k, msg]) => { if (EVENT_FIELDS.includes(k)) out[k] = msg; });
  const text = typeof err.detail === 'string' ? err.detail : Array.isArray(err.detail) ? err.detail.map((d) => d.msg).join(' ') : '';
  EVENT_FIELDS.forEach((f) => { if (!out[f] && text.includes(f)) out[f] = text.replace(/^Value error, /, ''); });
  return out;
}

/**
 * Create (event = null) or edit an event. Creation never sends club_id/created_by/event_status: the backend sets
 * the owner from the account (club lead -> own club, Dean -> non-club) and starts every event as DRAFT.
 * Editing sends only the changed fields.
 */
export function EventForm({ event, onSaved, onCancel, createLabel = 'Create draft event' }) {
  const toast = useToast();
  const original = toForm(event);
  const [form, setForm] = useState(original);
  const [errors, setErrors] = useState({});
  const [general, setGeneral] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    const missing = REQUIRED.filter((f) => !form[f].trim());
    if (missing.length) {
      setErrors(Object.fromEntries(missing.map((f) => [f, 'Required'])));
      setGeneral('Name, date and academic year are required.');
      return;
    }
    let body;
    if (event) {
      body = Object.fromEntries(EVENT_FIELDS.filter((f) => apiValue(f, form[f]) !== apiValue(f, original[f])).map((f) => [f, apiValue(f, form[f])]));
      if (!Object.keys(body).length) { setGeneral('Nothing has changed.'); setErrors({}); return; }
    } else {
      body = Object.fromEntries(EVENT_FIELDS.map((f) => [f, apiValue(f, form[f])]));
    }
    setBusy(true); setErrors({}); setGeneral('');
    try {
      const saved = event ? await api.patch(`/events/${event.event_id}`, body) : await api.post('/events', body);
      toast(event ? `Saved changes to ${saved.event_name}.` : `${saved.event_name} created as a draft. Publish it when students should see it.`);
      onSaved(saved);
    } catch (err) {
      setErrors(fieldErrorsFrom(err));
      setGeneral(err.message);
      toast(err);
    } finally {
      setBusy(false);
    }
  };

  // Called as functions (not <F/>) so inputs keep focus across renders.
  const F = ({ name, label, className = '', children }) => (
    <Field label={label} className={className}>
      {children}
      {errors[name] && <span className="block text-xs text-brandl mt-1" role="alert">{errors[name]}</span>}
    </Field>
  );
  const input = (name, props = {}) => <input className={`${INPUT} ${errors[name] ? 'border-brandl' : ''}`} value={form[name]} onChange={set(name)} aria-invalid={!!errors[name]} {...props} />;

  return (
    <form onSubmit={submit} className="grid md:grid-cols-3 gap-3" noValidate>
      {F({ name: 'event_name', label: 'Event name *', children: input('event_name', { maxLength: 200 }) })}
      {F({ name: 'event_date', label: 'Date *', children: input('event_date', { type: 'date' }) })}
      {F({ name: 'academic_year', label: 'Academic year *', children: input('academic_year', { placeholder: 'e.g. 2026-27', maxLength: 20 }) })}
      {F({ name: 'start_time', label: 'Start time', children: input('start_time', { type: 'time' }) })}
      {F({ name: 'end_time', label: 'End time', children: input('end_time', { type: 'time' }) })}
      {F({ name: 'venue', label: 'Venue', children: input('venue', { maxLength: 200 }) })}
      {F({ name: 'registration_url', label: 'Registration URL (http/https)', children: input('registration_url', { placeholder: 'https://…' }) })}
      {F({ name: 'standard_points', label: 'Standard points', children: input('standard_points', { inputMode: 'decimal', placeholder: 'e.g. 10' }) })}
      {F({ name: 'description', label: 'Description', className: 'md:col-span-3', children: <textarea rows="2" maxLength={5000} className={INPUT} value={form.description} onChange={set('description')} /> })}
      {general && <p className="md:col-span-3 text-brandl text-sm" role="alert">{general}</p>}
      <div className="md:col-span-3 flex gap-2">
        <button className={BTN} type="submit" disabled={busy}>{busy ? 'Saving…' : event ? 'Save changes' : createLabel}</button>
        {onCancel && <button type="button" className={GHOST} onClick={onCancel} disabled={busy}>Cancel</button>}
      </div>
    </form>
  );
}

/** One-step status change with an explanatory confirmation. Returns { advance(event), moving }. */
export function useEventTransition(onDone) {
  const toast = useToast();
  const [moving, setMoving] = useState(null);
  const advance = async (e) => {
    const target = NEXT_STATUS[e.event_status];
    if (!target) return;
    if (!window.confirm(`${STEP[target].label} "${e.event_name}"?\n\n${STEP[target].confirm}`)) return;
    setMoving(e.event_id);
    try {
      const saved = await api.post(`/events/${e.event_id}/status`, { event_status: target });
      toast(`${saved.event_name} is now ${statusLabel(saved.event_status)}.`);
    } catch (err) {
      toast(err);
    } finally {
      setMoving(null);
      onDone(e);
    }
  };
  return { advance, moving };
}

/**
 * Events table. `canManage(event)` decides whether Edit / next-status controls appear (the backend enforces it
 * independently); other events are shown read-only with `readOnlyNote`.
 */
export function EventTable({ events, showClub = false, canManage = () => true, readOnlyNote = 'Read-only', onEdit, transition, docsFor, onDocs, empty }) {
  return (
    <Table cols={['Event', ...(showClub ? ['Club'] : []), 'Date', 'Venue', 'Points', 'Registration', 'Status', 'Actions']} empty={empty}
      rows={events.map((e) => {
        const next = NEXT_STATUS[e.event_status];
        const url = safeHttpUrl(e.registration_url);
        const times = [formatTime(e.start_time), formatTime(e.end_time)].filter(Boolean).join('–');
        const manage = canManage(e);
        return row(e.event_id, [
          <div className="min-w-[9rem]"><div className="font-medium">{e.event_name}</div><div className="text-xs text-mute">{e.academic_year}</div></div>,
          ...(showClub ? [e.club_name || <span className="text-mute">Non-club</span>] : []),
          <div className="whitespace-nowrap">{formatDate(e.event_date)}{times && <div className="text-xs text-mute">{times}</div>}</div>,
          e.venue || '—',
          e.standard_points == null ? '—' : formatPoints(e.standard_points),
          url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-info underline">Link</a> : '—',
          <Badge s={e.event_status} />,
          <div className="flex flex-wrap gap-1 items-center">
            {manage && EDITABLE.includes(e.event_status) && <button type="button" className={GHOST} onClick={() => onEdit(e)}>Edit</button>}
            {manage && next && <button type="button" className={BTN} disabled={transition.moving === e.event_id} onClick={() => transition.advance(e)}>{transition.moving === e.event_id ? '…' : STEP[next].label}</button>}
            {!manage && <span className="text-xs text-mute">{readOnlyNote}</span>}
            {onDocs && <button type="button" className={GHOST} onClick={() => onDocs(docsFor?.event_id === e.event_id ? null : e)}>Documents</button>}
          </div>,
        ], docsFor?.event_id === e.event_id ? 'bg-soft/20' : '');
      })} />
  );
}

/** Upload documents tagged to an event (no request or award link). `types` is what the caller's role may upload. */
export function EventDocumentUpload({ event, types, defaultType, note, onUploaded }) {
  const toast = useToast();
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);

  const run = async (items) => {
    setBusy(true);
    const list = await uploadAndAttach(items, event.event_id, null);
    const merged = results ? results.map((r) => list.find((x) => x.item.key === r.item.key) || r) : list;
    const failed = merged.filter((r) => !r.ok).length;
    if (failed) toast(`${failed} of ${merged.length} upload(s) failed. See the details below.`, 'error');
    else toast(`${merged.length} document(s) uploaded to ${event.event_name}.`);
    setResults(failed ? merged : null);
    setFiles([]); setBusy(false);
    if (list.some((r) => r.ok)) onUploaded();
  };

  return (
    <div className="mt-3">
      <div className="text-xs font-medium text-mute mb-1">{note}</div>
      <FileAttachments files={files} setFiles={setFiles} types={types} accept={ACCEPT_ATTR} check={fileProblem}
        defaultType={defaultType} eventLabel={event.event_name} disabled={busy} />
      <button type="button" className={BTN} disabled={!files.length || busy || files.some((f) => f.problem)} onClick={() => run(files)}>
        {busy ? 'Uploading…' : 'Upload'}
      </button>
      {files.some((f) => f.problem) && <span className="text-xs text-brandl ml-2">Remove the files marked with a problem.</span>}
      {results && <AttachmentResults results={results} okText="uploaded" noun="upload(s)" retrying={busy}
        onRetry={() => run(results.filter((r) => !r.ok).map((r) => r.item))} onDismiss={() => setResults(null)} />}
    </div>
  );
}
