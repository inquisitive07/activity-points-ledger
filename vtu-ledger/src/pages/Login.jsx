import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApp } from '../context/AppContext.jsx';
import { USERS, DEMO, DEMO_PASSWORD } from '../data/mock.js';
import { HOME } from '../nav.js';
import { BTN, INPUT, Field } from '../components/ui.jsx';

export default function Login() {
  const { login } = useApp();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  const submit = (e) => {
    e.preventDefault();
    if (!/^\S+@\S+\.\S+$/.test(email)) return setError('Enter a valid email address.');
    if (password.length < 6) return setError('Password must be at least 6 characters.');
    const u = USERS[email.toLowerCase()];
    if (!u || password !== DEMO_PASSWORD) return setError('Invalid email or password.');
    login(u); navigate(HOME[u.role]);
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <div className="w-full max-w-3xl grid md:grid-cols-2 gap-4">
        <form onSubmit={submit} className="bg-white rounded-xl border border-soft/40 p-6">
          <div className="text-2xl font-bold text-maroon">RVCE Activity Points Ledger</div>
          <p className="text-mute text-sm mb-4">Sign in to your dashboard</p>
          <Field label="Email" className="mb-3"><input className={INPUT} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@vtu.edu.in" /></Field>
          <Field label="Password" className="mb-3"><input className={INPUT} type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          {error && <p className="text-brandl text-sm mb-3" role="alert">{error}</p>}
          <button className={`${BTN} w-full`} type="submit">Sign in</button>
        </form>
        <div className="bg-maroon text-white rounded-xl p-6">
          <div className="font-semibold mb-1">Demo credentials quick-select</div>
          <p className="text-soft text-xs mb-3">Click a profile to auto-fill (password: {DEMO_PASSWORD})</p>
          {DEMO.map(([name, mail]) => (
            <button key={mail} type="button" onClick={() => { setEmail(mail); setPassword(DEMO_PASSWORD); setError(''); }} className="w-full text-left bg-white/10 hover:bg-brand rounded-lg px-3 py-2 mb-2">
              <div className="font-medium text-sm">{name}</div><div className="text-xs text-soft">{mail}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
