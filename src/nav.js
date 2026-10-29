import { LayoutDashboard, FilePlus2, FolderLock, CalendarPlus, ClipboardCheck, ScanSearch, Inbox, Building2, Upload, ScrollText, GraduationCap } from 'lucide-react';

export const NAV = {
  STUDENT: [['/student', 'Dashboard', LayoutDashboard], ['/student/claims', 'Claims & Evidence', FilePlus2], ['/student/vault', 'Document Vault', FolderLock]],
  CLUB: [['/club', 'Events', CalendarPlus], ['/club/queue', 'Verification Queue', ClipboardCheck]],
  DEAN: [['/dean', 'OCR Pipeline', ScanSearch], ['/dean/claims', 'Claim Approvals', Inbox], ['/dean/clubs', 'Club Accountability', Building2], ['/dean/import', 'Student Import', Upload], ['/dean/audit', 'Audit & Reports', ScrollText]],
  HOD: [['/hod', 'Department Directory', GraduationCap]],
};
export const HOME = { STUDENT: '/student', CLUB: '/club', DEAN: '/dean', HOD: '/hod' };
