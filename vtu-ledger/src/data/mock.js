export const POINT_REQUIREMENT = { REGULAR: 100, LATERAL: 75 };
export const need = (s) => POINT_REQUIREMENT[s.type];
export const earned = (ledger, usn) =>
  ledger.filter((l) => l.usn === usn && l.st !== 'SUPERSEDED').reduce((a, l) => a + l.pts, 0);
export const today = () => new Date().toISOString().slice(0, 10);

export const USERS = {
  'student@vtu.edu.in': { role: 'STUDENT', name: 'Aarav Sharma', usn: '1RV22CS001' },
  'club@vtu.edu.in': { role: 'CLUB', name: 'Meera Iyer', club: 'Robotics Club' },
  'dean@vtu.edu.in': { role: 'DEAN', name: 'Dr. R. Narayan' },
  'hod@vtu.edu.in': { role: 'HOD', name: 'Dr. S. Kulkarni', dept: 'CS' },
};
export const DEMO_PASSWORD = 'vtu@123';
export const DEMO = [['Student Account', 'student@vtu.edu.in'], ['Club Lead Account', 'club@vtu.edu.in'], ['Dean Account', 'dean@vtu.edu.in'], ['HOD Account', 'hod@vtu.edu.in']];

export const STUDENTS = [
  ['1RV22CS001', 'Aarav Sharma', 'CS', 'REGULAR'], ['1RV22IS014', 'Diya Nair', 'IS', 'REGULAR'],
  ['1RV23CY402', 'Rohan Gowda', 'CY', 'LATERAL'], ['1RV22CD033', 'Neha Patil', 'CD', 'REGULAR'],
  ['1RV22EC008', 'Ishita Rao', 'EC', 'REGULAR'], ['1RV23EI419', 'Karthik Bhat', 'EI', 'LATERAL'],
].map(([usn, name, dept, type]) => ({ usn, name, dept, type, email: usn.toLowerCase() + '@vtu.edu.in' }));


export const EVENTS = [
  { id: 1, name: 'RoboWars 2026', club: 'Robotics Club', date: '2026-03-14', venue: 'Main Auditorium', url: 'vtu.edu.in/robowars', pts: 10 },
  { id: 2, name: 'Rhythm Fest', club: 'Cultural Club', date: '2026-02-20', venue: 'Open Air Theatre', url: 'vtu.edu.in/rhythm', pts: 20 },
  { id: 3, name: 'NSS Village Camp', club: 'NSS Unit', date: '2026-04-05', venue: 'Hesaraghatta', url: 'vtu.edu.in/nss', pts: 40 },
  { id: 4, name: 'HackRVCE 24h', club: 'Coding Club', date: '2026-01-18', venue: 'CS Lab Block', url: 'vtu.edu.in/hack', pts: 15 },
  { id: 5, name: 'Blood Donation Drive', club: '', date: '2026-02-02', venue: 'Campus Clinic', url: '', pts: 10 },
];

export const LEDGER = [
  [1, '1RV22CS001', 1, 10, 'ACTIVE'], [2, '1RV22CS001', 2, 15, 'SUPERSEDED'], [3, '1RV22CS001', 2, 20, 'CORRECTION'], [4, '1RV22CS001', 3, 40, 'ACTIVE'],
  [5, '1RV22IS014', 3, 45, 'ACTIVE'], [6, '1RV22IS014', 2, 20, 'ACTIVE'], [7, '1RV22IS014', 4, 15, 'ACTIVE'], [8, '1RV22IS014', 1, 10, 'ACTIVE'], [9, '1RV22IS014', 5, 10, 'ACTIVE'],
  [10, '1RV23CY402', 3, 40, 'ACTIVE'], [11, '1RV23CY402', 2, 20, 'ACTIVE'], [12, '1RV23CY402', 1, 10, 'ACTIVE'], [13, '1RV23CY402', 4, 5, 'ACTIVE'],
  [14, '1RV22EC008', 1, 10, 'ACTIVE'], [15, '1RV23EI419', 2, 20, 'ACTIVE'],
].map(([id, usn, ev, pts, st]) => ({ id, usn, ev, pts, st, date: '2026-04-12', src: id % 2 ? 'OCR_BATCH' : 'CLAIM' }));

export const REQUESTS = [
  { id: 501, usn: '1RV22CS001', ev: 4, type: 'NEW_ACTIVITY', pts: 15, reason: 'Participated in HackRVCE hackathon.', st: 'PENDING_CLUB', cm: '', files: 2 },
  { id: 502, usn: '1RV23CY402', ev: 1, type: 'MISSING_FROM_LIST', pts: 10, reason: 'Name absent from signed list.', st: 'PENDING_CLUB', cm: '', files: 1 },
  { id: 503, usn: '1RV22EC008', ev: 5, type: 'NEW_ACTIVITY', pts: 10, reason: 'Donated blood.', st: 'PENDING_DEAN', cm: 'College event', files: 1 },
  { id: 504, usn: '1RV22CD033', ev: 2, type: 'CORRECTION', pts: 20, reason: 'Awarded 15, standard is 20.', st: 'PENDING_DEAN', cm: 'Club verified.', files: 2 },
  { id: 505, usn: '1RV22CD033', ev: 1, type: 'NEW_ACTIVITY', pts: 10, reason: 'Volunteered at RoboWars.', st: 'PENDING_CLUB', cm: '', files: 1 },
];

export const DOCS = [
  { id: 1, usn: '1RV22CS001', name: 'RoboWars_Certificate.pdf', dt: 'Certificate PDF', ev: 1, purpose: 'Evidence' },
  { id: 2, usn: '1RV22CS001', name: 'NSS_Camp_Award_List_Signed.pdf', dt: 'Official Signed PDF', ev: 3, purpose: 'Official' },
  { id: 3, usn: '1RV22IS014', name: 'Rhythm_Fest_Attendance.jpg', dt: 'Attendance List', ev: 2, purpose: 'Evidence' },
  { id: 4, usn: '1RV22EC008', name: 'RoboWars_Signed_List.pdf', dt: 'Official Signed PDF', ev: 1, purpose: 'Official' },
  { id: 5, usn: '1RV23CY402', name: 'HackRVCE_Photo.jpg', dt: 'Event Photo', ev: 4, purpose: 'Evidence' },
];

export const AUDIT = [
  { t: '2026-04-12 10:14', who: 'Dr. R. Narayan', act: 'APPROVE_OCR_BATCH', ent: 'BATCH #41' },
  { t: '2026-04-02 16:40', who: 'Dr. R. Narayan', act: 'APPROVE_CORRECTION', ent: 'REQUEST #488' },
  { t: '2026-03-21 09:05', who: 'Meera Iyer', act: 'CLUB_VERIFY', ent: 'REQUEST #470' },
];
