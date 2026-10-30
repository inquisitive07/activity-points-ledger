import { useState } from 'react';
import { useApp } from '../context/AppContext.jsx';
import { useAuth } from '../context/AuthContext.jsx';
import { earned, need } from '../data/mock.js';
import { Page, Card, Badge, Table, row, GHOST, INPUT } from '../components/ui.jsx';
import { statusLabel } from '../utils/format.js';

/** Read-only. Every query is filtered to the HOD's department code. */
export default function Hod() {
  const { students, ledger, docs, eventName } = useApp();
  const { user } = useAuth();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(null);
  const dept = user.department_code;
  const list = students.filter((s) => s.dept === dept && (s.name + s.usn).toLowerCase().includes(q.toLowerCase()));
  const s = sel && students.find((x) => x.usn === sel && x.dept === dept);
  return (
    <Page title={`${dept} Department Directory`} sub="Read-only. Restricted to your department code.">
      <Card>
        <input className={`${INPUT} max-w-sm mb-3`} placeholder="Search name or USN" value={q} onChange={(e) => setQ(e.target.value)} />
        <Table cols={['USN', 'Name', 'Entry', 'Earned', 'Target', '']} empty="No students match."
          rows={list.map((x) => row(x.usn, [x.usn, x.name, statusLabel(x.type), earned(ledger, x.usn), need(x), <button className={GHOST} onClick={() => setSel(x.usn)}>Inspect</button>]))} />
      </Card>
      {s && (
        <Card title={`Inspection · ${s.name}`}>
          <div className="grid md:grid-cols-2 gap-4">
            <div>
              <div className="text-sm font-semibold mb-1">Activity summary ({earned(ledger, s.usn)}/{need(s)})</div>
              <Table cols={['Event', 'Pts', 'Status']} rows={ledger.filter((l) => l.usn === s.usn).map((l) => row(l.id, [eventName(l.ev), l.pts, <Badge s={l.st} />], l.st === 'SUPERSEDED' ? 'line-through text-mute' : ''))} />
            </div>
            <div>
              <div className="text-sm font-semibold mb-1">Evidence & official signed documents</div>
              <Table cols={['File', 'Purpose']} rows={docs.filter((d) => d.usn === s.usn).map((d) => row(d.id, [d.name, <Badge s={d.purpose} />]))} />
            </div>
          </div>
        </Card>
      )}
    </Page>
  );
}
