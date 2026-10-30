/**
 * Request review shared by the club queue (VERIFY / REJECT) and Dean approvals (APPROVE / REJECT).
 * A rejection needs a comment: the UI only hints at it; the backend's 422 is what refuses an empty one.
 */
import { useState } from 'react';
import { useToast } from '../context/ToastContext.jsx';
import { api } from '../api/client.js';
import { formatDateTime } from '../utils/format.js';
import { BTN, GHOST, INPUT } from './ui.jsx';

/**
 * endpoint: 'club-review' | 'dean-review'
 * confirmText(request, action) -> text for window.confirm
 * successText(request, action, response) -> toast text
 * onDone(request, action, response | null) -> called after every attempt (refresh lists there)
 */
export function useRequestReview({ endpoint, confirmText, successText, onDone }) {
  const toast = useToast();
  const [comments, setComments] = useState({});
  const [errors, setErrors] = useState({});
  const [acting, setActing] = useState(null);

  const review = async (r, action) => {
    const id = r.request_id;
    const comment = (comments[id] || '').trim();
    if (!window.confirm(confirmText(r, action))) return;
    setActing(id); setErrors((e) => ({ ...e, [id]: '' }));
    let out = null;
    try {
      out = await api.post(`/requests/${id}/${endpoint}`, { action, comment: comment || null });
      toast(successText(r, action, out));
      setComments((c) => { const { [id]: _, ...rest } = c; return rest; });
    } catch (err) {
      setErrors((e) => ({ ...e, [id]: err.message }));
      toast(err);
    } finally {
      setActing(null);
      onDone(r, action, out);
    }
  };
  const setComment = (id, value) => setComments((c) => ({ ...c, [id]: value }));
  return { review, comments, setComment, errors, acting };
}

/** Comment box + action buttons for one request. actions: [{ action: 'VERIFY', label, primary }] */
export function ReviewCell({ request, reviewer, actions }) {
  const id = request.request_id;
  const comment = reviewer.comments[id] || '';
  return (
    <div className="min-w-[13rem]">
      <input className={INPUT} placeholder="Comment (required to reject)" maxLength={2000} value={comment}
        onChange={(e) => reviewer.setComment(id, e.target.value)} aria-label={`Comment for request ${id}`} />
      <div className="flex gap-1 mt-1">
        {actions.map((a) => (
          <button key={a.action} type="button" className={a.primary ? BTN : GHOST} disabled={reviewer.acting === id}
            onClick={() => reviewer.review(request, a.action)}>{a.label}</button>
        ))}
      </div>
      {!comment.trim() && <div className="text-xs text-mute mt-1">Rejecting needs a comment.</div>}
      {reviewer.errors[id] && <div className="text-xs text-brandl mt-1" role="alert">{reviewer.errors[id]}</div>}
    </div>
  );
}

/** Student name over USN. */
export const StudentCell = ({ request }) => (
  <div className="min-w-[8rem]"><div className="font-medium">{request.student_name}</div><div className="text-xs text-mute">{request.usn}</div></div>
);

/** A recorded review: when, by whom (optional) and the comment. */
export const ReviewNote = ({ at, by, comment, none = '—' }) => (at ? (
  <div className="text-xs min-w-[10rem]">
    <div className="text-mute">{formatDateTime(at)}{by ? ` · ${by}` : ''}</div>
    {comment || <span className="text-mute">No comment</span>}
  </div>
) : <span className="text-xs text-mute">{none}</span>);
