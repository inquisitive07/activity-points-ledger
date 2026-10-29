import { Paperclip, X } from 'lucide-react';

export const BTN = 'px-3 py-1.5 rounded-lg bg-brand hover:bg-brandl text-white font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed';
export const GHOST = 'px-3 py-1.5 rounded-lg border border-brand text-brand hover:bg-soft/30 font-semibold text-sm disabled:opacity-50 disabled:cursor-not-allowed';
export const INPUT = 'w-full border border-soft rounded-lg px-2.5 py-1.5 bg-white text-ink text-sm focus:outline-none focus:ring-2 focus:ring-info';

const TONE = { ok: 'bg-emerald-100 text-ok', warn: 'bg-amber-100 text-amber-800', info: 'bg-blue-100 text-blue-800', err: 'bg-red-100 text-brandl', mute: 'bg-gray-100 text-mute', soft: 'bg-soft text-maroon' };
const STATUS_TONE = { ACTIVE: 'ok', CORRECTION: 'info', SUPERSEDED: 'mute', APPROVED: 'ok', REJECTED: 'err', PENDING_CLUB: 'warn', PENDING_DEAN: 'info', Official: 'ok', Evidence: 'soft', open: 'err', resolved: 'ok', dismissed: 'mute' };

export const Badge = ({ s, tone }) => (
  <span className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold ${TONE[tone || STATUS_TONE[s] || 'soft']}`}>{String(s).replace('_', ' ')}</span>
);
export const Page = ({ title, sub, children }) => (
  <div className="p-4 md:p-6 max-w-6xl">
    <h1 className="text-xl font-bold text-brandl">{title}</h1>
    <p className="text-mute text-sm mb-4">{sub}</p>
    {children}
  </div>
);
export const Card = ({ title, children, className = '' }) => (
  <section className={`bg-white border border-soft/40 rounded-xl p-4 mb-4 ${className}`}>
    {title && <h2 className="font-semibold text-maroon mb-3">{title}</h2>}
    {children}
  </section>
);
export const Field = ({ label, children, className = '' }) => (
  <label className={`block text-xs font-medium text-mute ${className}`}>{label}<div className="mt-1">{children}</div></label>
);
export const Stat = ({ n, label, tone = 'text-ink' }) => (
  <div className="bg-white border border-soft/40 rounded-xl p-3"><div className={`text-2xl font-bold ${tone}`}>{n}</div><div className="text-xs text-mute">{label}</div></div>
);

/** rows: [{ key, cells: [...], cls }] — build with row() */
export const Table = ({ cols, rows, empty = 'Nothing here yet.' }) => (
  <div className="overflow-x-auto">
    <table className="w-full text-sm">
      <thead><tr>{cols.map((c) => <th key={c} className="text-left text-xs uppercase text-mute py-2 px-2 border-b border-soft/50">{c}</th>)}</tr></thead>
      <tbody>
        {rows.length ? rows.map((r) => (
          <tr key={r.key} className={`border-b border-soft/30 hover:bg-soft/20 ${r.cls || ''}`}>
            {r.cells.map((c, i) => <td key={i} className="py-2 px-2 align-top">{c}</td>)}
          </tr>
        )) : <tr><td colSpan={cols.length} className="py-6 text-center text-mute">{empty}</td></tr>}
      </tbody>
    </table>
  </div>
);
export const row = (key, cells, cls) => ({ key, cells, cls });

/** Multi-file attachment with Document Type / Event / Purpose tagging */
export const DOC_TYPES = ['Certificate PDF', 'Attendance List', 'Event Photo', 'Official Signed PDF', 'Other'];
export function FileTagger({ files, setFiles, events }) {
  const add = (e) => {
    const next = [...e.target.files].map((f) => ({
      name: f.name,
      dt: /\.pdf$/i.test(f.name) ? 'Certificate PDF' : /\.(jpe?g|png)$/i.test(f.name) ? 'Event Photo' : 'Other',
      ev: events[0].id, purpose: 'Evidence',
    }));
    setFiles([...files, ...next]); e.target.value = '';
  };
  const patch = (i, p) => setFiles(files.map((f, j) => (j === i ? { ...f, ...p } : f)));
  return (
    <div>
      <input type="file" multiple onChange={add} className="text-sm mb-2" />
      {files.map((f, i) => (
        <div key={i} className="grid grid-cols-2 md:grid-cols-5 gap-2 items-end bg-blush rounded-lg p-2 mb-2">
          <div className="col-span-2 text-sm font-medium truncate flex items-center gap-1"><Paperclip size={14} />{f.name}</div>
          <Field label="Document type"><select className={INPUT} value={f.dt} onChange={(e) => patch(i, { dt: e.target.value })}>{DOC_TYPES.map((d) => <option key={d}>{d}</option>)}</select></Field>
          <Field label="Associated event"><select className={INPUT} value={f.ev} onChange={(e) => patch(i, { ev: +e.target.value })}>{events.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>
          <Field label="Purpose">
            <div className="flex gap-1">
              <select className={INPUT} value={f.purpose} onChange={(e) => patch(i, { purpose: e.target.value })}><option>Evidence</option><option>Official</option></select>
              <button type="button" className={GHOST} aria-label="Remove file" onClick={() => setFiles(files.filter((_, j) => j !== i))}><X size={14} /></button>
            </div>
          </Field>
        </div>
      ))}
    </div>
  );
}
