import { NavLink, useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { useApp } from '../context/AppContext.jsx';
import { NAV } from '../nav.js';

export default function Layout({ children }) {
  const { user, logout, toastMsg } = useApp();
  const navigate = useNavigate();
  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-maroon text-white flex items-center justify-between px-4 py-3">
        <div className="font-bold">VTU Activity Points Ledger</div>
        <div className="flex items-center gap-3 text-sm">
          <span className="hidden sm:inline">{user.name}</span>
          <span className="px-2 py-0.5 rounded-full bg-rosered text-xs font-semibold">{user.role}</span>
          <button className="px-3 py-1.5 rounded-lg bg-brand hover:bg-brandl font-semibold flex items-center gap-1" onClick={() => { logout(); navigate('/login'); }}>
            <LogOut size={14} /> Logout
          </button>
        </div>
      </header>
      <div className="flex-1 flex flex-col md:flex-row min-w-0">
        <aside className="bg-maroon md:w-60 shrink-0 p-2">
          <nav className="flex md:flex-col gap-1 overflow-x-auto" aria-label="Main">
            {NAV[user.role].map(([to, label, Icon]) => (
              <NavLink key={to} to={to} end className={({ isActive }) => `whitespace-nowrap flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-white ${isActive ? 'bg-brand font-semibold' : 'hover:bg-white/10'}`}>
                <Icon size={16} /> {label}
              </NavLink>
            ))}
          </nav>
        </aside>
        <main className="flex-1 min-w-0">{children}</main>
      </div>
      {toastMsg && <div role="status" className="fixed left-1/2 -translate-x-1/2 bottom-6 bg-ok text-white px-4 py-2 rounded-lg shadow">{toastMsg}</div>}
    </div>
  );
}
