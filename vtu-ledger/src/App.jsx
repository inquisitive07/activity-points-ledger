import { Routes, Route, Navigate } from 'react-router-dom';
import { useApp } from './context/AppContext.jsx';
import Layout from './components/Layout.jsx';
import Login from './pages/Login.jsx';
import { StudentHome, Claims, Vault } from './pages/Student.jsx';
import { ClubEvents, ClubQueue } from './pages/Club.jsx';
import { OcrPipeline, ClaimApprovals, ClubMatrix, StudentImport, AuditReports } from './pages/Dean.jsx';
import Hod from './pages/Hod.jsx';
import { HOME } from './nav.js';

function Guard({ role, children }) {
  const { user } = useApp();
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== role) return <Navigate to={HOME[user.role]} replace />;
  return <Layout>{children}</Layout>;
}

export default function App() {
  const { user } = useApp();
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to={HOME[user.role]} replace /> : <Login />} />
      <Route path="/student" element={<Guard role="STUDENT"><StudentHome /></Guard>} />
      <Route path="/student/claims" element={<Guard role="STUDENT"><Claims /></Guard>} />
      <Route path="/student/vault" element={<Guard role="STUDENT"><Vault /></Guard>} />
      <Route path="/club" element={<Guard role="CLUB"><ClubEvents /></Guard>} />
      <Route path="/club/queue" element={<Guard role="CLUB"><ClubQueue /></Guard>} />
      <Route path="/dean" element={<Guard role="DEAN"><OcrPipeline /></Guard>} />
      <Route path="/dean/claims" element={<Guard role="DEAN"><ClaimApprovals /></Guard>} />
      <Route path="/dean/clubs" element={<Guard role="DEAN"><ClubMatrix /></Guard>} />
      <Route path="/dean/import" element={<Guard role="DEAN"><StudentImport /></Guard>} />
      <Route path="/dean/audit" element={<Guard role="DEAN"><AuditReports /></Guard>} />
      <Route path="/hod" element={<Guard role="HOD"><Hod /></Guard>} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
