import { useState, useMemo } from 'react';
import { useApp } from '../context/AppContext.jsx';
import { earned, need, POINT_REQUIREMENT } from '../data/mock.js';
import { Page, Card, Field, Badge, Table, row, BTN, GHOST, INPUT } from '../components/ui.jsx';

/* ---------- Student import (hidden: not routed) ---------- */
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
