import { useEffect, useRef, useState } from 'react';
import { isAbortError } from '../api/client.js';

/**
 * Poll an endpoint while `active` is true.
 *
 *   const poll = usePoll(({ signal }) => api.get(`/ocr/batches/${id}`, { signal }), { key: id, active: inFlight, onData });
 *
 * - One request at a time: the next one is scheduled only after the previous one settles (no overlapping calls).
 * - The delay grows from `interval` by `factor` up to `maxInterval`, so a long job is polled less and less often;
 *   it also grows after errors (e.g. API restarting), and polling continues until `active` turns false.
 * - Stops, and aborts the in-flight request, when `active` becomes false, `key` changes or the component unmounts
 *   (including navigating away), so nothing is fetched for a finished job and no state is set after unmount.
 * - `onData(data)` is called with every response; the caller ends polling by turning `active` false.
 *
 * Returns { error, polls, nextDelay, polling } for display. The first request is sent after `interval`, because the
 * caller already holds a fresh value (e.g. the 202 response) when polling starts.
 */
export function usePoll(fetcher, { key, active, onData, interval = 2000, maxInterval = 15000, factor = 1.5 }) {
  const fetcherRef = useRef(fetcher);
  const onDataRef = useRef(onData);
  fetcherRef.current = fetcher;
  onDataRef.current = onData;
  const [state, setState] = useState({ error: null, polls: 0, nextDelay: interval });

  useEffect(() => {
    if (!active) return undefined;
    let timer = null;
    let ctrl = null;
    let stopped = false;
    let delay = interval;
    setState({ error: null, polls: 0, nextDelay: delay });

    const tick = async () => {
      ctrl = new AbortController();
      let error = null;
      try {
        const data = await fetcherRef.current({ signal: ctrl.signal });
        if (stopped) return;
        onDataRef.current?.(data);
      } catch (e) {
        if (stopped || isAbortError(e)) return;
        error = e;
      }
      delay = Math.min(Math.round(delay * factor), maxInterval);
      setState((s) => ({ error, polls: s.polls + 1, nextDelay: delay }));
      if (!stopped) timer = setTimeout(tick, delay);
    };
    timer = setTimeout(tick, delay);
    return () => { stopped = true; clearTimeout(timer); ctrl?.abort(); };
  }, [key, active, interval, maxInterval, factor]);

  return { ...state, polling: !!active };
}

/** The current time, refreshed every second while `active` (for elapsed-time displays). */
export function useNow(active) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}
