import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './context/AuthContext.jsx';
import Layout from './components/Layout.jsx';
import { Loading } from './components/ui.jsx';
import Login from './pages/Login.jsx';
import { StudentHome, StudentEvents, Claims, Vault } from './pages/Student.jsx';
import { ClubDashboard, ClubEvents, ClubQueue } from './pages/Club.jsx';
// StudentImport (pages/Dean.jsx) is kept for a later phase but not routed: student import is seed-only for the MVP.
import { AuditReports } from './pages/Dean.jsx';
import DeanOcr from './pages/DeanOcr.jsx';
import DeanClaims from './pages/DeanClaims.jsx';
import DeanStudents from './pages/DeanStudents.jsx';
import DeanEvents from './pages/DeanEvents.jsx';
import DeanClubs from './pages/DeanClubs.jsx';
import Hod from './pages/Hod.jsx';
import { HOME } from './nav.js';

const homeOf = (user) => (user && HOME[user.role]) || null;

/** Client-side routing only; the backend enforces every permission independently. */
function Guard({ role, children }) {
  const { user } = useAuth();          // App renders routes only once the session check is `ready`
  if (!homeOf(user)) return <Navigate to="/login" replace />;
  if (user.role !== role) return <Navigate to={homeOf(user)} replace />;
  return <Layout>{children}</Layout>;
}

export default function App() {
  const { user, ready } = useAuth();
  if (!ready) return <Loading label="Restoring your session…" />;
  const home = homeOf(user);
  return (
    <Routes>
      <Route path="/login" element={home ? <Navigate to={home} replace /> : <Login />} />
      <Route path="/student" element={<Guard role="STUDENT"><StudentHome /></Guard>} />
      <Route path="/student/events" element={<Guard role="STUDENT"><StudentEvents /></Guard>} />
      <Route path="/student/claims" element={<Guard role="STUDENT"><Claims /></Guard>} />
      <Route path="/student/vault" element={<Guard role="STUDENT"><Vault /></Guard>} />
      <Route path="/club" element={<Guard role="CLUB_LEAD"><ClubDashboard /></Guard>} />
      <Route path="/club/events" element={<Guard role="CLUB_LEAD"><ClubEvents /></Guard>} />
      <Route path="/club/queue" element={<Guard role="CLUB_LEAD"><ClubQueue /></Guard>} />
      <Route path="/dean" element={<Guard role="DEAN"><DeanOcr /></Guard>} />
      <Route path="/dean/claims" element={<Guard role="DEAN"><DeanClaims /></Guard>} />
      <Route path="/dean/students" element={<Guard role="DEAN"><DeanStudents /></Guard>} />
      <Route path="/dean/events" element={<Guard role="DEAN"><DeanEvents /></Guard>} />
      <Route path="/dean/clubs" element={<Guard role="DEAN"><DeanClubs /></Guard>} />
      <Route path="/dean/audit" element={<Guard role="DEAN"><AuditReports /></Guard>} />
      <Route path="/hod" element={<Guard role="HOD"><Hod /></Guard>} />
      <Route path="*" element={<Navigate to={home || '/login'} replace />} />
    </Routes>
  );
}
