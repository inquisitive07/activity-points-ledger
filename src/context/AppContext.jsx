import { createContext, useContext, useState, useCallback } from 'react';
import { STUDENTS, EVENTS, LEDGER, REQUESTS, DOCS, AUDIT } from '../data/mock.js';

const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

export function AppProvider({ children }) {
  const [user, setUser] = useState(null);
  const [students, setStudents] = useState(STUDENTS);
  const [events, setEvents] = useState(EVENTS);
  const [ledger, setLedger] = useState(LEDGER);
  const [requests, setRequests] = useState(REQUESTS);
  const [docs, setDocs] = useState(DOCS);
  const [audit, setAudit] = useState(AUDIT);
  const [toastMsg, setToastMsg] = useState('');

  const toast = useCallback((m) => { setToastMsg(m); setTimeout(() => setToastMsg(''), 2600); }, []);
  const log = useCallback((act, ent, who) =>
    setAudit((a) => [{ t: new Date().toISOString().slice(0, 16).replace('T', ' '), who: who || user?.name || '—', act, ent }, ...a]), [user]);
  const login = (u) => { setUser(u); log('LOGIN', u.role, u.name); };
  const logout = () => { log('LOGOUT', user.role); setUser(null); };
  const eventName = (id) => events.find((e) => e.id === id)?.name ?? '—';

  const value = { user, login, logout, students, setStudents, events, setEvents, ledger, setLedger, requests, setRequests, docs, setDocs, audit, log, toast, toastMsg, eventName };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
