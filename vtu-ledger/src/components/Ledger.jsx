import { formatDate, formatPoints, progressPercent, statusLabel } from '../utils/format.js';
import { Badge, Table, row } from './ui.jsx';

/** Earned / required progress from a GET /students/{usn} summary. Totals come from the API, never from the rows. */
export function PointsProgress({ summary }) {
  const done = !!summary.target_reached;
  const pct = progressPercent(summary.earned_points, summary.required_points);
  return (
    <>
      <div className="flex justify-between text-sm mb-1">
        <span><b className="text-lg">{formatPoints(summary.earned_points)}</b> / {formatPoints(summary.required_points)} points</span>
        <Badge s={done ? 'Target reached' : 'In progress'} tone={done ? 'ok' : 'warn'} />
      </div>
      <div className="h-3 rounded-full bg-blush border border-soft/50 overflow-hidden" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <div className={`h-full ${done ? 'bg-ok' : 'bg-brand'}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="text-xs text-mute mt-1">
        {done ? 'Target reached.' : `${formatPoints(summary.remaining_points)} points remaining.`} Only active awards count towards the total.
      </div>
    </>
  );
}

/**
 * Ledger history including SUPERSEDED and REVOKED rows (muted, points struck through) with the supersession links.
 * `showIds` adds the award number column (Dean/HOD views).
 */
export function LedgerTable({ ledger = [], showIds = false, empty = 'No official awards yet. Points appear here once the Dean approves them.' }) {
  const supersededBy = Object.fromEntries(ledger.filter((l) => l.supersedes_ledger_id != null).map((l) => [l.supersedes_ledger_id, l.ledger_id]));
  const inactive = (l) => l.status !== 'ACTIVE';
  const note = (l) => (l.status === 'SUPERSEDED' && supersededBy[l.ledger_id] ? `Replaced by award #${supersededBy[l.ledger_id]}`
    : l.supersedes_ledger_id != null ? `Corrects award #${l.supersedes_ledger_id}`
    : l.status === 'REVOKED' ? 'Revoked; not counted' : '');
  return (
    <Table cols={[...(showIds ? ['Award'] : []), 'Event', 'Points', 'Awarded', 'Source', 'Status']} empty={empty}
      rows={ledger.map((l) => row(l.ledger_id, [
        ...(showIds ? [`#${l.ledger_id}`] : []),
        l.event_name || '—',
        <span className={inactive(l) ? 'line-through' : 'font-semibold'}>{formatPoints(l.points_awarded)}</span>,
        formatDate(l.award_date),
        statusLabel(l.source_type),
        <div><Badge s={l.status} />{note(l) && <div className="text-xs text-mute mt-1">{note(l)}</div>}</div>,
      ], inactive(l) ? 'text-mute' : ''))} />
  );
}
