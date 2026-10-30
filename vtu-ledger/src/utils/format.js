/**
 * Display formatting shared by every screen: points, dates and status labels.
 * Values come from the backend; nothing here computes totals or business state.
 */

const EMPTY = '—';

/**
 * Points arrive as decimal strings ("70.00"). Format without float arithmetic: drop trailing zeros
 * ("70.00" -> "70", "12.50" -> "12.5"). null/undefined/'' -> '—'.
 */
export function formatPoints(value) {
  if (value === null || value === undefined || value === '') return EMPTY;
  const s = String(value).trim();
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s);
  if (!m) return s;
  const [, sign, whole, frac = ''] = m;
  const f = frac.replace(/0+$/, '');
  return `${sign}${whole.replace(/^0+(?=\d)/, '')}${f ? `.${f}` : ''}`;
}

/** Percentage (0–100) for progress bars only. Never used as a stored or displayed total. */
export function progressPercent(earned, required) {
  const e = Number(earned), r = Number(required);
  if (!Number.isFinite(e) || !Number.isFinite(r) || r <= 0) return 0;
  return Math.max(0, Math.min(100, (e / r) * 100));
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-03-18" -> "18 Mar 2026". Parsed by hand so a date never shifts across time zones. */
export function formatDate(value) {
  if (!value) return EMPTY;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value));
  if (!m) return String(value);
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
}

/** ISO timestamp (UTC from the API) -> local "18 Mar 2026, 17:30". */
export function formatDateTime(value) {
  if (!value) return EMPTY;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
}

/**
 * Readable labels for backend enum values (statuses, request/document/source types, roles, entry types).
 * Only values that need wording beyond "Title case" are listed; anything else is humanized generically.
 */
const LABELS = {
  // point requests
  PENDING_CLUB: 'Pending club review',
  PENDING_DEAN: 'Pending Dean approval',
  NEW_ACTIVITY: 'New activity',
  MISSING_FROM_LIST: 'Missing from list',
  // ledger source types
  BULK_APPROVAL: 'Bulk approval',
  INDIVIDUAL_CLAIM: 'Individual claim',
  MANUAL_CORRECTION: 'Correction',
  // OCR batches
  REVIEW: 'Awaiting review',
  // documents
  SIGNED_POINT_LIST: 'Signed point list',
  ATTENDANCE_SHEET: 'Attendance sheet',
  // roles
  CLUB_LEAD: 'Club Lead',
  HOD: 'HOD',
  DEAN: 'Dean',
};

const ACRONYMS = new Set(['USN', 'OCR', 'HOD', 'PDF', 'ID', 'URL']);

/**
 * 'PENDING_CLUB' -> 'Pending club review'; 'SUPERSEDED' -> 'Superseded'; 'UNKNOWN_USN' -> 'Unknown USN'.
 * Text that is not an UPPER_SNAKE identifier (e.g. 'Target reached') is returned unchanged.
 */
export function statusLabel(value) {
  if (value === null || value === undefined || value === '') return EMPTY;
  const s = String(value);
  if (LABELS[s]) return LABELS[s];
  if (!/^[A-Z][A-Z0-9_]*$/.test(s)) return s;
  return s.split('_').filter(Boolean).map((w, i) => {
    if (ACRONYMS.has(w)) return w;
    const lower = w.toLowerCase();
    return i === 0 ? lower[0].toUpperCase() + lower.slice(1) : lower;
  }).join(' ');
}
