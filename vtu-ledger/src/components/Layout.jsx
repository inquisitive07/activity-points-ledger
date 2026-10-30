import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { useAuth } from '../context/AuthContext.jsx';
import { useToast } from '../context/ToastContext.jsx';
import { NAV } from '../nav.js';
import { statusLabel } from '../utils/format.js';

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  const signOut = async () => {
    setBusy(true);
    const revoked = await logout();
    if (!revoked) toast('Signed out on this device, but the server could not be reached to end the session.', 'error');
    navigate('/login');
  };

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-maroon text-white flex items-center justify-between px-4 py-3">
        <div className="font-bold">RVCE Activity Points Ledger</div>
        <div className="flex items-center gap-3 text-sm">
          <span className="hidden sm:inline">{user.display_name}</span>
          <span className="px-2 py-0.5 rounded-full bg-rosered text-xs font-semibold">{statusLabel(user.role)}</span>
          <button className="px-3 py-1.5 rounded-lg bg-brand hover:bg-brandl font-semibold flex items-center gap-1 disabled:opacity-50" disabled={busy} onClick={signOut}>
            <LogOut size={14} /> Logout
          </button>
        </div>
      </header>
      <div className="flex-1 flex flex-col md:flex-row min-w-0">
        <aside className="bg-maroon md:w-60 shrink-0 p-2">
          <nav className="flex md:flex-col gap-1 overflow-x-auto" aria-label="Main">
            {(NAV[user.role] || []).map(([to, label, Icon]) => (
              <NavLink key={to} to={to} end className={({ isActive }) => `whitespace-nowrap flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-white ${isActive ? 'bg-brand font-semibold' : 'hover:bg-white/10'}`}>
                <Icon size={16} /> {label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="flex-1 min-w-0">{children}</main>
      </div>
    </div>
  );
}
