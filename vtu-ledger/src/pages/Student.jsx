import { useState, useMemo } from 'react';
import { useApp } from '../context/AppContext.jsx';
import { useAuth } from '../context/AuthContext.jsx';
import { api } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { formatDate, formatPoints, progressPercent, statusLabel } from '../utils/format.js';
import { Page, Card, Field, Badge, Table, row, FileTagger, Loading, ErrorState, BTN, GHOST, INPUT } from '../components/ui.jsx';

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

  const done = !!me.target_reached;
  const ledger = me.ledger || [];
  const supersededBy = Object.fromEntries(ledger.filter((l) => l.supersedes_ledger_id != null).map((l) => [l.supersedes_ledger_id, l.ledger_id]));
  const inactive = (l) => l.status !== 'ACTIVE';
  const note = (l) => (l.status === 'SUPERSEDED' && supersededBy[l.ledger_id] ? `Replaced by award #${supersededBy[l.ledger_id]}`
    : l.supersedes_ledger_id != null ? `Corrects award #${l.supersedes_ledger_id}`
    : l.status === 'REVOKED' ? 'Revoked; not counted' : '');

  return (
    <Page title="My Dashboard" sub={`${me.name} · ${me.usn} · ${statusLabel(me.entry_type)} entry · ${me.department_code}`}>
      {error && <ErrorState error={error} title="Could not refresh" onRetry={reload} />}
      {me.student_status !== 'ACTIVE' && (
        <p className="bg-amber-100 text-amber-800 rounded-lg px-3 py-2 text-sm mb-4">
          Your record is <Badge s={me.student_status} />. Your history stays available; only active students can submit new requests.
        </p>
      )}
      <Card title="Progress">
        <div className="flex justify-between text-sm mb-1">
          <span><b className="text-lg">{formatPoints(me.earned_points)}</b> / {formatPoints(me.required_points)} points</span>
          <Badge s={done ? 'Target reached' : 'In progress'} tone={done ? 'ok' : 'warn'} />
        </div>
        <div className="h-3 rounded-full bg-blush border border-soft/50 overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progressPercent(me.earned_points, me.required_points))}>
          <div className={`h-full ${done ? 'bg-ok' : 'bg-brand'}`} style={{ width: `${progressPercent(me.earned_points, me.required_points)}%` }} />
        </div>
        <div className="text-xs text-mute mt-1">
          {done ? 'Target reached.' : `${formatPoints(me.remaining_points)} points remaining.`} Only active awards count towards the total.
        </div>
      </Card>
      <Card title="Activity Points Ledger">
        <Table cols={['Event', 'Points', 'Awarded', 'Source', 'Status']}
          empty="No official awards yet. Points appear here once the Dean approves them."
          rows={ledger.map((l) => row(l.ledger_id, [
            l.event_name || '—',
            <span className={inactive(l) ? 'line-through' : 'font-semibold'}>{formatPoints(l.points_awarded)}</span>,
            formatDate(l.award_date),
            statusLabel(l.source_type),
            <div><Badge s={l.status} />{note(l) && <div className="text-xs text-mute mt-1">{note(l)}</div>}</div>,
          ], inactive(l) ? 'text-mute' : ''))} />
      </Card>
    </Page>
  );
}

export function Claims() {
  const { events, ledger, requests, setRequests, setDocs, log, toast, eventName } = useApp();
  const { user } = useAuth();
  const [form, setForm] = useState({ type: 'NEW_ACTIVITY', ev: events[0].id, pts: '', reason: '' });
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [rowId, setRowId] = useState('');
  const [evidence, setEvidence] = useState([]);
  const set = (k) => (e) => setForm({ ...form, [k]: k === 'ev' ? +e.target.value : e.target.value });
  const awarded = ledger.filter((l) => l.usn === user.student_usn && l.st !== 'SUPERSEDED');

  const saveDocs = (list, fallbackEv) =>
    setDocs((d) => [...list.map((x, i) => ({ id: d.length + i + 1, usn: user.student_usn, name: x.name, dt: x.dt, ev: x.ev || fallbackEv, purpose: x.purpose })), ...d]);

  const submit = (e) => {
    e.preventDefault();
    const p = +form.pts;
    if (!(p >= 1 && p <= 50)) return setError('Points must be between 1 and 50.');
    if (!form.reason.trim()) return setError('Please give a reason.');
    const ev = events.find((x) => x.id === form.ev), id = 506 + requests.length;
    setRequests((r) => [{ id, usn: user.student_usn, ev: ev.id, type: form.type, pts: p, reason: form.reason, st: ev.club ? 'PENDING_CLUB' : 'PENDING_DEAN', cm: '', files: files.length }, ...r]);
    saveDocs(files, ev.id); log('SUBMIT_CLAIM', `REQUEST #${id}`);
    toast(ev.club ? 'Sent to club for verification' : 'Sent to Dean');
    setForm({ ...form, pts: '', reason: '' }); setFiles([]); setError('');
  };
  const attach = () => {
    const l = ledger.find((x) => x.id === +rowId);
    saveDocs(evidence.map((x) => ({ ...x, ev: l.ev })), l.ev); log('ATTACH_EVIDENCE', `LEDGER #${rowId}`);
    toast('Evidence attached — no new claim created'); setEvidence([]); setRowId('');
  };

  return (
    <Page title="Claims & Evidence" sub="Request points or attach evidence to existing awards.">
      <Card title="New claim request">
        <form onSubmit={submit} className="grid md:grid-cols-3 gap-3">
          <Field label="Request type"><select className={INPUT} value={form.type} onChange={set('type')}>{['NEW_ACTIVITY', 'MISSING_FROM_LIST', 'CORRECTION'].map((t) => <option key={t} value={t}>{statusLabel(t)}</option>)}</select></Field>
          <Field label="Event"><select className={INPUT} value={form.ev} onChange={set('ev')}>{events.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>
          <Field label="Claimed points"><input type="number" className={INPUT} value={form.pts} onChange={set('pts')} /></Field>
          <Field label="Reason" className="md:col-span-3"><textarea rows="2" className={INPUT} value={form.reason} onChange={set('reason')} /></Field>
          <div className="md:col-span-3"><div className="text-xs font-medium text-mute mb-1">Attachments (certificate, attendance list, photos…)</div><FileTagger files={files} setFiles={setFiles} events={events} /></div>
          {error && <p className="md:col-span-3 text-brandl text-sm" role="alert">{error}</p>}
          <div className="md:col-span-3"><button className={BTN} type="submit">Submit request</button></div>
        </form>
      </Card>
      <Card title="Attach evidence to an awarded row (no new claim)">
        <Field label="Awarded ledger row" className="max-w-md mb-3">
          <select className={INPUT} value={rowId} onChange={(e) => setRowId(e.target.value)}><option value="">Select…</option>{awarded.map((l) => <option key={l.id} value={l.id}>{eventName(l.ev)} · {l.pts} pts</option>)}</select>
        </Field>
        {rowId && <FileTagger files={evidence} setFiles={setEvidence} events={events} />}
        <button className={`${BTN} mt-2`} disabled={!rowId || !evidence.length} onClick={attach}>Attach evidence only</button>
      </Card>
      <Card title="My requests">
        <Table cols={['ID', 'Event', 'Type', 'Pts', 'Files', 'Status', 'Comment']}
          rows={requests.filter((r) => r.usn === user.student_usn).map((r) => row(r.id, [`#${r.id}`, eventName(r.ev), statusLabel(r.type), r.pts, r.files, <Badge s={r.st} />, r.cm || '—']))} />
      </Card>
    </Page>
  );
}

export function Vault() {
  const { docs, eventName } = useApp();
  const { user } = useAuth();
  const [q, setQ] = useState('');
  const list = useMemo(() => docs.filter((d) => d.usn === user.student_usn && (d.name + d.dt).toLowerCase().includes(q.toLowerCase())), [docs, user, q]);
  return (
    <Page title="Personal Document Vault" sub="Certificates, evidence and official signed PDFs.">
      <Card>
        <input className={`${INPUT} max-w-sm mb-3`} placeholder="Search documents…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Table cols={['File', 'Type', 'Event', 'Purpose']} empty="No matching documents."
          rows={list.map((d) => row(d.id, [d.name, d.dt, eventName(d.ev), <Badge s={d.purpose} />]))} />
      </Card>
    </Page>
  );
}
