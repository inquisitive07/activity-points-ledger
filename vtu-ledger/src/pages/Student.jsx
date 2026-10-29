import { useState, useMemo } from 'react';
import { useApp } from '../context/AppContext.jsx';
import { earned, need } from '../data/mock.js';
import { Page, Card, Field, Badge, Table, row, FileTagger, BTN, GHOST, INPUT } from '../components/ui.jsx';

const useMe = () => { const a = useApp(); return { ...a, me: a.students.find((s) => s.usn === a.user.usn) }; };

export function StudentHome() {
  const { me, ledger, eventName } = useMe();
  const pts = earned(ledger, me.usn), target = need(me), done = pts >= target;
  return (
    <Page title="My Dashboard" sub={`${me.name} · ${me.usn} · ${me.type} entry`}>
      <Card title="Progress">
        <div className="flex justify-between text-sm mb-1"><span><b className="text-lg">{pts}</b> / {target} points</span><Badge s={done ? 'Target reached' : 'In progress'} tone={done ? 'ok' : 'warn'} /></div>
        <div className="h-3 rounded-full bg-blush border border-soft/50 overflow-hidden"><div className={`h-full ${done ? 'bg-ok' : 'bg-brand'}`} style={{ width: `${Math.min(100, (pts / target) * 100)}%` }} /></div>
      </Card>
      <Card title="Activity Points Ledger">
        <Table cols={['Event', 'Points', 'Awarded', 'Source', 'Status']}
          rows={ledger.filter((l) => l.usn === me.usn).map((l) => row(l.id, [eventName(l.ev), l.pts, l.date, <span className="font-mono text-xs">{l.src}</span>, <Badge s={l.st} />], l.st === 'SUPERSEDED' ? 'line-through text-mute' : ''))} />
      </Card>
    </Page>
  );
}

export function Claims() {
  const { user, events, ledger, requests, setRequests, setDocs, log, toast, eventName } = useApp();
  const [form, setForm] = useState({ type: 'NEW_ACTIVITY', ev: events[0].id, pts: '', reason: '' });
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [rowId, setRowId] = useState('');
  const [evidence, setEvidence] = useState([]);
  const set = (k) => (e) => setForm({ ...form, [k]: k === 'ev' ? +e.target.value : e.target.value });
  const awarded = ledger.filter((l) => l.usn === user.usn && l.st !== 'SUPERSEDED');

  const saveDocs = (list, fallbackEv) =>
    setDocs((d) => [...list.map((x, i) => ({ id: d.length + i + 1, usn: user.usn, name: x.name, dt: x.dt, ev: x.ev || fallbackEv, purpose: x.purpose })), ...d]);

  const submit = (e) => {
    e.preventDefault();
    const p = +form.pts;
    if (!(p >= 1 && p <= 50)) return setError('Points must be between 1 and 50.');
    if (!form.reason.trim()) return setError('Please give a reason.');
    const ev = events.find((x) => x.id === form.ev), id = 506 + requests.length;
    setRequests((r) => [{ id, usn: user.usn, ev: ev.id, type: form.type, pts: p, reason: form.reason, st: ev.club ? 'PENDING_CLUB' : 'PENDING_DEAN', cm: '', files: files.length }, ...r]);
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
          <Field label="Request type"><select className={INPUT} value={form.type} onChange={set('type')}><option>NEW_ACTIVITY</option><option>MISSING_FROM_LIST</option><option>CORRECTION</option></select></Field>
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
          rows={requests.filter((r) => r.usn === user.usn).map((r) => row(r.id, [`#${r.id}`, eventName(r.ev), r.type, r.pts, r.files, <Badge s={r.st} />, r.cm || '—']))} />
      </Card>
    </Page>
  );
}

export function Vault() {
  const { user, docs, eventName } = useApp();
  const [q, setQ] = useState('');
  const list = useMemo(() => docs.filter((d) => d.usn === user.usn && (d.name + d.dt).toLowerCase().includes(q.toLowerCase())), [docs, user, q]);
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
