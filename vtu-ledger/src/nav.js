import { LayoutDashboard, CalendarDays, Users, CalendarCog, FilePlus2, FolderLock, CalendarPlus, ClipboardCheck, ScanSearch, Inbox, Building2, ScrollText, GraduationCap } from 'lucide-react';

/** Keys are the backend roles (USER_ACCOUNT.role). Student Import is hidden for the MVP: student data is seed-only. */
export const NAV = {
  STUDENT: [['/student', 'Dashboard', LayoutDashboard], ['/student/events', 'Events', CalendarDays], ['/student/claims', 'Claims & Evidence', FilePlus2], ['/student/vault', 'Document Vault', FolderLock]],
  CLUB_LEAD: [['/club', 'Dashboard', LayoutDashboard], ['/club/events', 'Events', CalendarPlus], ['/club/queue', 'Verification Queue', ClipboardCheck]],
  DEAN: [['/dean', 'OCR Pipeline', ScanSearch], ['/dean/claims', 'Claim Approvals', Inbox], ['/dean/students', 'Students', Users], ['/dean/events', 'Events', CalendarCog], ['/dean/clubs', 'Club Accountability', Building2], ['/dean/audit', 'Audit & Reports', ScrollText]],
  HOD: [['/hod', 'Department Directory', GraduationCap]],
};
export const HOME = { STUDENT: '/student', CLUB_LEAD: '/club', DEAN: '/dean', HOD: '/hod' };
