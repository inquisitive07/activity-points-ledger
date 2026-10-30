import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

const Ctx = createContext(null);
export const useToast = () => useContext(Ctx);

const TONES = { success: 'bg-ok', error: 'bg-brand', info: 'bg-info' };

/**
 * toast('Saved')                 -> success
 * toast('Heads up', 'info')
 * toast(apiError)                -> error tone, the backend's `detail` as the message
 */
export function ToastProvider({ children }) {
  const [msg, setMsg] = useState(null);
  const timer = useRef(null);

  const toast = useCallback((input, tone) => {
    const isError = input instanceof Error;
    const t = tone || (isError ? 'error' : 'success');
    setMsg({ text: isError ? input.message : String(input), tone: t });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(null), t === 'error' ? 5000 : 2600);
  }, []);
  useEffect(() => () => clearTimeout(timer.current), []);

  return (
    <Ctx.Provider value={toast}>
      {children}
      {msg && (
        <div role={msg.tone === 'error' ? 'alert' : 'status'}
          className={`fixed left-1/2 -translate-x-1/2 bottom-6 z-50 max-w-[90vw] ${TONES[msg.tone] || TONES.success} text-white px-4 py-2 rounded-lg shadow text-sm`}>
          {msg.text}
        </div>
      )}
    </Ctx.Provider>
  );
}
