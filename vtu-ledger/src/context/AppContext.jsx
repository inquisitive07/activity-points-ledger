import { createContext, useContext, useState, useCallback } from 'react';
import { STUDENTS, EVENTS, LEDGER, REQUESTS, DOCS, AUDIT } from '../data/mock.js';
import { useAuth } from './AuthContext.jsx';
import { useToast } from './ToastContext.jsx';

/**
 * Mock data for the screens not yet connected to the API (claims, vault, club, Dean, HOD).
 * The signed-in user comes from AuthContext; toasts from ToastContext. Screens move off this as they are integrated.
 */
const Ctx = createContext(null);
export const useApp = () => useContext(Ctx);

export function AppProvider({ children }) {
  const { user } = useAuth();
  const toast = useToast();
  const [students, setStudents] = useState(STUDENTS);
  const [events, setEvents] = useState(EVENTS);
  const [ledger, setLedger] = useState(LEDGER);
  const [requests, setRequests] = useState(REQUESTS);
  const [docs, setDocs] = useState(DOCS);
  const [audit, setAudit] = useState(AUDIT);

  const log = useCallback((act, ent, who) =>
    setAudit((a) => [{ t: new Date().toISOString().slice(0, 16).replace('T', ' '), who: who || user?.display_name || '—', act, ent }, ...a]), [user]);
  const eventName = (id) => events.find((e) => e.id === id)?.name ?? '—';

  const value = { students, setStudents, events, setEvents, ledger, setLedger, requests, setRequests, docs, setDocs, audit, log, toast, eventName };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
