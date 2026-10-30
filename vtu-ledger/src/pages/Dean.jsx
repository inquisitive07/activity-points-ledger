import { useState, useMemo } from 'react';
import { Lock } from 'lucide-react';
import { useApp } from '../context/AppContext.jsx';
import { earned, need, today, CLUBS, OCR_EXCEPTIONS, POINT_REQUIREMENT } from '../data/mock.js';
import { Page, Card, Field, Stat, Badge, Table, row, BTN, GHOST, INPUT } from '../components/ui.jsx';

/* ---------- OCR verification pipeline ---------- */
export function OcrPipeline() {
  const { students, events, setLedger, log, toast } = useApp();
  const [rows, setRows] = useState(OCR_EXCEPTIONS);
  const [match, setMatch] = useState({ ev: '', club: '' });
  const [upload, setUpload] = useState('');
  const [committed, setCommitted] = useState(false);

  const patch = (id, p) => setRows(rows.map((r) => (r.id === id ? { ...r, ...p } : r)));
  const open = rows.filter((r) => r.st === 'open');
  const count = (k) => open.filter((r) => r.issue === k).length;
  const lookup = (usn) => students.find((s) => s.usn === usn.trim().toUpperCase());

  const resolve = (r) => {
    const s = lookup(r.usn);
    let err = '';
    if (!s) err = 'USN not found in registry.';
    else if (!(r.pts >= 1 && r.pts <= 50)) err = 'Points must be 1–50.';
    else if (r.name !== s.name) err = `Name must match registry: ${s.name}`;
    patch(r.id, err ? { err } : { st: 'resolved', err: '', usn: s.usn });
  };
  const locked = open.length > 0 || !match.ev || !match.club || committed;
  const commit = () => {
    const good = rows.filter((r) => r.st === 'resolved');
    setLedger((l) => [...l, ...good.map((r, i) => ({ id: l.length + i + 1, usn: r.usn, ev: +match.ev, pts: +r.pts, date: today(), st: 'ACTIVE', src: 'OCR_BATCH' }))]);
    log('APPROVE_OCR_BATCH', 'BATCH #42'); toast('Committed to official ledger'); setCommitted(true);
  };
  const status = committed ? 'Batch committed' : open.length ? `${open.length} exception(s) must be cleared` : !match.ev || !match.club ? 'Resolve event & club match' : 'Ready to commit';

  return (
    <Page title="OCR Verification Pipeline" sub="OCR proposes → registry validates → Dean confirms.">
      <Card title="1 · Official signed PDF upload">
        <input type="file" accept="application/pdf" className="text-sm" onChange={(e) => setUpload(e.target.files[0]?.name || '')} />
        {upload && <span className="ml-2 text-ok text-sm">{upload} staged</span>}
      </Card>
      {upload && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mb-4">
            <Stat n={56} label="Pages processed" /><Stat n={487} label="Records detected" />
            <Stat n={487 - open.length} label="Valid records" tone="text-ok" />
            <Stat n={count('UNKNOWN_USN')} label="Unknown USNs" tone="text-brandl" />
            <Stat n={count('NAME_MISMATCH')} label="Name mismatches" tone="text-warn" />
            <Stat n={count('POINT_ERROR')} label="Point errors" tone="text-brandl" />
            <Stat n={open.length} label="Exceptions" tone={open.length ? 'text-brandl' : 'text-ok'} />
          </div>
          <Card title="2 · Event & club matching (low confidence)">
            <p className="text-sm text-mute mb-2">OCR read: <span className="font-mono">"Robo Wars 26"</span> / <span className="font-mono">"Robotcs Clb"</span> <Badge s="Low confidence" tone="warn" /></p>
            <div className="grid md:grid-cols-2 gap-3">
              <Field label="Resolve event"><select className={INPUT} value={match.ev} onChange={(e) => setMatch({ ...match, ev: e.target.value })}><option value="">Select event…</option>{events.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>
              <Field label="Resolve club"><select className={INPUT} value={match.club} onChange={(e) => setMatch({ ...match, club: e.target.value })}><option value="">Select club…</option>{CLUBS.map((c) => <option key={c}>{c}</option>)}</select></Field>
            </div>
          </Card>
          <Card title="3 · Exceptions">
            <Table cols={['Page', 'USN', 'Name', 'Points', 'Issue', 'Action']} empty="No exceptions."
              rows={rows.map((r) => row(r.id, [r.page,
                r.st === 'open' ? <input className={`${INPUT} font-mono`} value={r.usn} onChange={(e) => patch(r.id, { usn: e.target.value })} /> : r.usn,
                r.st === 'open' ? <input className={INPUT} value={r.name} onChange={(e) => patch(r.id, { name: e.target.value })} /> : r.name,
                r.st === 'open' ? <input type="number" className={`${INPUT} w-20`} value={r.pts} onChange={(e) => patch(r.id, { pts: +e.target.value })} /> : r.pts,
                <div><Badge s={r.st === 'open' ? r.issue : r.st} tone={r.st === 'open' ? 'err' : undefined} />{r.err && <div className="text-xs text-brandl mt-1">{r.err}</div>}</div>,
                r.st === 'open' ? (
                  <div className="flex flex-wrap gap-1">
                    <button className={BTN} onClick={() => resolve(r)}>Resolve</button>
                    {r.issue === 'NAME_MISMATCH' && lookup(r.usn) && <button className={GHOST} onClick={() => patch(r.id, { name: lookup(r.usn).name, err: '' })}>Use registry name</button>}
                    <button className={GHOST} onClick={() => patch(r.id, { st: 'dismissed' })}>Dismiss</button>
                  </div>) : '—']))} />
          </Card>
          <Card title="4 · Commit">
            <button className={`${BTN} inline-flex items-center gap-1`} disabled={locked} onClick={commit}>{locked && !committed && <Lock size={14} />}Commit to Official Ledger</button>
            <span className={`ml-3 text-sm ${locked && !committed ? 'text-brandl' : 'text-ok'}`}>{status}</span>
          </Card>
        </>
      )}
    </Page>
  );
}

/* ---------- Individual claim approval ---------- */
export function StudentImport() {
  const { students, setStudents, log, toast } = useApp();
  const [text, setText] = useState('');
  const rows = useMemo(() => {
    let data = [];
    try { data = JSON.parse(text); if (!Array.isArray(data)) data = []; }
    catch {
      const lines = text.trim().split('\n').filter(Boolean);
      if (lines.length > 1) { const keys = lines[0].split(',').map((k) => k.trim()); data = lines.slice(1).map((l) => Object.fromEntries(l.split(',').map((v, i) => [keys[i], (v || '').trim()]))); }
    }
    return data.map((r, i) => {
      const errors = [];
      if (!/^1RV\d{2}[A-Z]{2}\d{3}$/.test(r.usn || '')) errors.push('Invalid USN');
      if (!r.name) errors.push('Name required');
      if (!['IS', 'CS', 'CY', 'CD', 'AI', 'EC', 'EI', 'ME', 'CV'].includes(r.dept)) errors.push('Unknown dept');
      if (!POINT_REQUIREMENT[r.type]) errors.push('Type must be REGULAR/LATERAL');
      if (!/^\S+@\S+\.\S+$/.test(r.email || '')) errors.push('Invalid email');
      if (students.some((s) => s.usn === r.usn)) errors.push('Duplicate USN');
      return { ...r, i, errors };
    });
  }, [text, students]);
  const valid = rows.filter((r) => !r.errors.length);
  const readFile = async (e) => { const f = e.target.files[0]; if (f) setText(await f.text()); };
  return (
    <Page title="Student Batch Import" sub="Paste CSV or JSON, or upload a file. Fields are validated as you type.">
      <Card>
        <div className="flex gap-2 mb-2 flex-wrap"><input type="file" accept=".csv,.json" className="text-sm" onChange={readFile} /><button className={GHOST} onClick={() => setText(SAMPLE)}>Load sample</button></div>
        <textarea rows="5" className={`${INPUT} font-mono`} value={text} onChange={(e) => setText(e.target.value)} placeholder="usn,name,dept,type,email" />
        {rows.length > 0 && (
          <div className="mt-3">
            <Table cols={['USN', 'Name', 'Dept', 'Type', 'Validation']} rows={rows.map((r) => row(r.i, [r.usn, r.name, r.dept, r.type, r.errors.length ? <span className="text-brandl text-xs">{r.errors.join(' · ')}</span> : <Badge s="Valid" tone="ok" />]))} />
            <button className={`${BTN} mt-3`} disabled={!valid.length} onClick={() => {
              setStudents((s) => [...s, ...valid.map(({ i, errors, ...x }) => x)]); log('IMPORT_STUDENTS', `${valid.length} records`); toast(`${valid.length} students imported`); setText('');
            }}>Import {valid.length} valid row(s)</button>
          </div>
        )}
      </Card>
    </Page>
  );
}

/* ---------- Audit log & global reports ---------- */
export function AuditReports() {
  const { students, ledger, audit } = useApp();
  const [filter, setFilter] = useState('all');
  const list = students.filter((s) => { const ok = earned(ledger, s.usn) >= need(s); return filter === 'all' || (filter === 'done' ? ok : !ok); });
  return (
    <Page title="Audit Log & Global Reports" sub="System events and student completion status.">
      <Card title="Student completion report">
        <Field label="Filter" className="max-w-xs mb-3"><select className={INPUT} value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">All students</option><option value="done">Target reached</option><option value="below">Below target</option></select></Field>
        <Table cols={['USN', 'Name', 'Dept', 'Earned', 'Target', 'Status']} rows={list.map((s) => { const p = earned(ledger, s.usn), ok = p >= need(s); return row(s.usn, [s.usn, s.name, s.dept, p, need(s), <Badge s={ok ? 'Target reached' : 'Below target'} tone={ok ? 'ok' : 'warn'} />]); })} />
      </Card>
      <Card title="System audit log">
        <Table cols={['Time', 'User', 'Action', 'Entity']} rows={audit.map((a, i) => row(i, [a.t, a.who, <span className="font-mono text-xs">{a.act}</span>, a.ent]))} />
      </Card>
    </Page>
  );
}
