import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext.jsx';
import { DEMO_ACCOUNTS, DEMO_PASSWORD } from '../dev/demoAccounts.js';
import { HOME } from '../nav.js';
import { BTN, INPUT, Field } from '../components/ui.jsx';

// Quick-select is a development convenience only; `import.meta.env.DEV` is false in `npm run build`.
const SHOW_DEMO = import.meta.env.DEV;

export default function Login() {
  const { login, notice, clearNotice } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    clearNotice();
    if (!/^\S+@\S+\.\S+$/.test(email.trim())) return setError('Enter a valid email address.');
    if (!password) return setError('Enter your password.');
    setBusy(true); setError('');
    try {
      const user = await login(email.trim(), password);
      if (!HOME[user.role]) { setError('This account role is not supported by this application.'); return; }
      navigate(HOME[user.role], { replace: true });
    } catch (err) {
      setError(err.message);           // 401 invalid credentials, 403 disabled account, 429 rate limit, network
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className={`w-full ${SHOW_DEMO ? 'max-w-3xl grid md:grid-cols-2' : 'max-w-sm'} gap-4`}>
        <form onSubmit={submit} className="bg-white rounded-xl border border-soft/40 p-6" noValidate>
          <div className="text-2xl font-bold text-maroon">RVCE Activity Points Ledger</div>
          <p className="text-mute text-sm mb-4">Sign in to your dashboard</p>
          {notice && <p className="bg-amber-100 text-amber-800 rounded-lg px-3 py-2 text-sm mb-3" role="status">{notice}</p>}
          <Field label="Email" className="mb-3"><input className={INPUT} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@vtu.edu.in" /></Field>
          <Field label="Password" className="mb-3"><input className={INPUT} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <p className="text-brandl text-sm mb-3" role="alert">{error}</p>}
          <button className={`${BTN} w-full`} type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
        {SHOW_DEMO && (
          <div className="bg-maroon text-white rounded-xl p-6">
            <div className="font-semibold mb-1">Demo credentials quick-select</div>
            <p className="text-soft text-xs mb-3">Development only · fills the form; sign-in still checks the API (password: {DEMO_PASSWORD})</p>
            {DEMO_ACCOUNTS.map(([name, mail]) => (
              <button key={mail} type="button" onClick={() => { setEmail(mail); setPassword(DEMO_PASSWORD); setError(''); }} className="w-full text-left bg-white/10 hover:bg-brand rounded-lg px-3 py-1.5 mb-1.5">
                <div className="font-medium text-sm">{name}</div><div className="text-xs text-soft">{mail}</div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
