import { useEffect, useState } from 'react';
import { Download, Eye, X } from 'lucide-react';
import { api, downloadDocument } from '../api/client.js';
import { useApi } from '../hooks/useApi.js';
import { useToast } from '../context/ToastContext.jsx';
import { formatBytes, formatDateTime, statusLabel } from '../utils/format.js';
import { Badge, ErrorState, Loading, Table, row, GHOST } from './ui.jsx';

const PREVIEWABLE = ['application/pdf', 'image/jpeg', 'image/png'];

/**
 * Documents visible to the caller for a filter (GET /documents?request_id= | event_id= ...), with authenticated
 * download and inline preview. The backend scopes the list: a document the caller may not see is simply absent
 * (an empty list never implies that something exists). Storage keys are never part of the API.
 */
export function DocumentList({ query, empty = 'No documents.', reloadKey = 0 }) {
  const toast = useToast();
  const key = JSON.stringify(query);
  const { data, error, loading, reload } = useApi(({ signal }) => api.get('/documents', { signal, query }), [key, reloadKey]);
  const [busy, setBusy] = useState(null);
  const [preview, setPreview] = useState(null);

  const download = async (d) => {
    setBusy(d.document_id);
    try { await downloadDocument(d.document_id, d.file_name); } catch (err) { toast(err); } finally { setBusy(null); }
  };

  if (error && !data) return <ErrorState error={error} title="Could not load documents" onRetry={reload} />;
  if (loading && !data) return <Loading label="Loading documents…" />;
  return (
    <>
      <Table cols={['File', 'Type', 'Uploaded by', 'Uploaded', 'Size', 'Status', '']} empty={empty}
        rows={data.map((d) => row(d.document_id, [
          <div className="min-w-[9rem] break-words font-medium">{d.file_name}{d.event_name && <div className="text-xs text-mute font-normal">{d.event_name}</div>}</div>,
          statusLabel(d.document_type),
          <span className="text-xs break-all">{d.uploaded_by}</span>,
          <span className="text-xs whitespace-nowrap">{formatDateTime(d.uploaded_at)}</span>,
          <span className="whitespace-nowrap">{formatBytes(d.file_size_bytes)}</span>,
          <Badge s={d.document_status} />,
          <div className="flex gap-1">
            {PREVIEWABLE.includes(d.mime_type) && (
              <button type="button" className={`${GHOST} flex items-center gap-1`} onClick={() => setPreview(d)} aria-label={`Preview ${d.file_name}`}><Eye size={14} />View</button>
            )}
            <button type="button" className={`${GHOST} flex items-center gap-1`} disabled={busy === d.document_id} onClick={() => download(d)} aria-label={`Download ${d.file_name}`}>
              <Download size={14} />{busy === d.document_id ? '…' : 'Download'}
            </button>
          </div>,
        ]))} />
      {preview && <DocumentPreview doc={preview} onClose={() => setPreview(null)} />}
    </>
  );
}

/**
 * Inline preview: the file is fetched with the Bearer token (GET /documents/{id}/content?disposition=inline) and
 * shown through an object URL, which is revoked when the preview closes or changes.
 */
export function DocumentPreview({ doc, onClose }) {
  const [state, setState] = useState({ url: null, error: null });

  useEffect(() => {
    const ctrl = new AbortController();
    let url = null;
    setState({ url: null, error: null });
    api.blob(`/documents/${doc.document_id}/content`, { query: { disposition: 'inline' }, signal: ctrl.signal })
      .then(({ blob }) => {
        // Re-type with the verified MIME type from the metadata (PDF/JPEG/PNG only) before rendering.
        url = URL.createObjectURL(new Blob([blob], { type: doc.mime_type }));
        setState({ url, error: null });
      })
      .catch((error) => { if (error.name !== 'AbortError') setState({ url: null, error }); });
    return () => { ctrl.abort(); if (url) URL.revokeObjectURL(url); };
  }, [doc.document_id, doc.mime_type]);

  return (
    <div className="mt-3 border border-soft/50 rounded-xl bg-white p-3">
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="font-semibold text-maroon text-sm truncate">Preview · {doc.file_name}</div>
        <button type="button" className={GHOST} onClick={onClose} aria-label="Close preview"><X size={14} /></button>
      </div>
      {state.error && <ErrorState error={state.error} title="Could not open this document" />}
      {!state.url && !state.error && <Loading label="Opening document…" />}
      {state.url && (doc.mime_type === 'application/pdf'
        ? <iframe title={`Preview of ${doc.file_name}`} src={state.url} className="w-full h-[70vh] rounded-lg border border-soft/40" />
        : <img src={state.url} alt={doc.file_name} className="max-w-full max-h-[70vh] mx-auto rounded-lg" />)}
    </div>
  );
}
