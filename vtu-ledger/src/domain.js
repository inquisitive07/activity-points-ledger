/** Backend enum values used in more than one screen (vtu-backend/app/models.py). */
export const REQUEST_TYPES = ['NEW_ACTIVITY', 'MISSING_FROM_LIST', 'CORRECTION'];
/** Requests that count as club accountability issues (CLAUDE.md 4.5, 18.5); NEW_ACTIVITY claims do not. */
export const ISSUE_TYPES = ['MISSING_FROM_LIST', 'CORRECTION'];
