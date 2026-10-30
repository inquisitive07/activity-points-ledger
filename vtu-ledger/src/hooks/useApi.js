import { useCallback, useEffect, useRef, useState } from 'react';
import { isAbortError } from '../api/client.js';

/**
 * Load data from the API with loading/error state and cancellation.
 *
 *   const { data, error, loading, reload } = useApi(({ signal }) => api.get(`/students/${usn}`, { signal }), [usn]);
 *
 * The in-flight request is aborted when `deps` change or the component unmounts, so a late response never
 * overwrites newer state. `enabled: false` skips the call (e.g. while a required id is missing).
 */
export function useApi(fetcher, deps = [], { enabled = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: enabled });
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    if (!enabled) { setState((s) => ({ ...s, loading: false })); return undefined; }
    const ctrl = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcherRef.current({ signal: ctrl.signal }).then(
      (data) => { if (!ctrl.signal.aborted) setState({ data, error: null, loading: false }); },
      (error) => {
        if (ctrl.signal.aborted || isAbortError(error)) return;
        setState((s) => ({ data: s.data, error, loading: false }));
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { ...state, reload };
}
