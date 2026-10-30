import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api, clearToken, getToken, isAbortError, onUnauthorized, setToken } from '../api/client.js';

const Ctx = createContext(null);
export const useAuth = () => useContext(Ctx);

/**
 * Session state backed by the API. The JWT lives in sessionStorage; the user object always comes from the backend
 * (POST /auth/login, GET /auth/me) and is never decoded from the token. Route guards wait for `ready`.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(() => !getToken());   // nothing to restore without a token
  const [notice, setNotice] = useState('');                 // shown on the login page (expired session etc.)

  const userRef = useRef(null);
  userRef.current = user;

  // Any 401 on an authenticated call: the client has already cleared the token; drop the user.
  useEffect(() => onUnauthorized(() => {
    if (userRef.current) setNotice('Your session has ended. Please sign in again.');
    setUser(null);
  }), []);

  // Restore the session on page load.
  useEffect(() => {
    if (!getToken()) return undefined;
    const ctrl = new AbortController();
    api.get('/auth/me', { signal: ctrl.signal })
      .then((me) => setUser(me))
      .catch((err) => {
        if (isAbortError(err)) return;
        if (err.status !== 401) setNotice(`Could not restore your session: ${err.message}`);
      })
      .finally(() => { if (!ctrl.signal.aborted) setReady(true); });
    return () => ctrl.abort();
  }, []);

  const login = useCallback(async (email, password) => {
    // auth: false -> no stale token is sent and a 401 here means "wrong credentials", not "session expired".
    const res = await api.post('/auth/login', { email, password }, { auth: false });
    setToken(res.access_token);
    setNotice('');
    setUser(res.user);
    return res.user;
  }, []);

  /** Revoke the token server-side, then forget it locally. Resolves to false if revocation could not be confirmed. */
  const logout = useCallback(async () => {
    let revoked = true;
    try {
      if (getToken()) await api.post('/auth/logout');
    } catch (err) {
      revoked = err.status === 401;   // already invalid: nothing left to revoke
    } finally {
      clearToken();
      setUser(null);
      setNotice('');   // a deliberate sign-out is not an "expired session"
    }
    return revoked;
  }, []);

  const clearNotice = useCallback(() => setNotice(''), []);

  return (
    <Ctx.Provider value={{ user, ready, notice, clearNotice, login, logout }}>
      {children}
    </Ctx.Provider>
  );
}
