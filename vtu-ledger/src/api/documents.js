/**
 * Document upload helpers on top of the shared API client (no separate fetch logic).
 * Client-side checks only mirror the backend for usability; the backend re-validates everything
 * (413 size, 415 type/signature, 422, 409) and its message is what the user sees on failure.
 */
import { api } from './client.js';

export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;             // backend MAX_UPLOAD_BYTES (10 MB)
export const ACCEPTED_MIME = ['application/pdf', 'image/jpeg', 'image/png'];
export const ACCEPT_ATTR = '.pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png';
/** Document types a STUDENT may upload (backend UPLOADABLE[STUDENT]). */
export const STUDENT_DOCUMENT_TYPES = ['CERTIFICATE', 'PHOTO', 'OTHER'];
/** Document types a CLUB_LEAD may upload (backend UPLOADABLE[CLUB_LEAD]); always tagged to one of the club's events. */
export const CLUB_DOCUMENT_TYPES = ['ATTENDANCE_SHEET', 'PHOTO', 'REPORT'];
/** Event documents the Dean uploads outside OCR. SIGNED_POINT_LIST is deliberately absent: signed lists belong to the
 *  OCR pipeline (upload -> OCR -> review -> commit); an upload here never starts OCR or awards points. */
export const DEAN_EVENT_DOCUMENT_TYPES = ['ATTENDANCE_SHEET', 'PHOTO', 'REPORT', 'OTHER'];

/** A problem the backend would certainly refuse, or null. */
export function fileProblem(file) {
  if (!ACCEPTED_MIME.includes(file.type)) return 'Only PDF, JPEG and PNG files are accepted';
  if (file.size === 0) return 'The file is empty';
  if (file.size > MAX_UPLOAD_BYTES) return 'File is larger than the 10 MB limit';
  return null;
}

/** Default document type from the file: PDF -> CERTIFICATE, image -> PHOTO. */
export const defaultDocumentType = (file) => (file.type === 'application/pdf' ? 'CERTIFICATE' : file.type.startsWith('image/') ? 'PHOTO' : 'OTHER');
/** Event document default (club lead / Dean uploads): image -> PHOTO, anything else -> ATTENDANCE_SHEET. */
export const defaultEventDocumentType = (file) => (file.type.startsWith('image/') ? 'PHOTO' : 'ATTENDANCE_SHEET');

/** POST /documents (multipart). Resolves to DocumentOut. */
export const uploadDocument = (file, documentType, eventId) =>
  api.upload('/documents', { file, document_type: documentType, event_id: eventId ?? undefined });

/**
 * Upload each item and attach it to a request or award, one at a time, recording every outcome.
 *
 * items:   [{ file, documentType, documentId? }]  (documentId set = already uploaded; only the attach is retried)
 * attach:  { kind: 'request' | 'ledger', id }, or null to upload only (e.g. club documents tagged to an event)
 * returns: [{ item, ok, stage: 'upload' | 'attach' | 'done', documentId, error }]
 *
 * Never throws for a single file: one failure does not hide the results of the others.
 */
export async function uploadAndAttach(items, eventId, attach) {
  const path = !attach ? null : attach.kind === 'request' ? `/requests/${attach.id}/documents` : `/ledger/${attach.id}/documents`;
  const results = [];
  for (const item of items) {
    let documentId = item.documentId ?? null;
    try {
      if (documentId == null) documentId = (await uploadDocument(item.file, item.documentType, eventId)).document_id;
    } catch (error) {
      results.push({ item, ok: false, stage: 'upload', documentId: null, error });
      continue;
    }
    if (!path) { results.push({ item, ok: true, stage: 'done', documentId, error: null }); continue; }
    try {
      await api.post(path, { document_id: documentId });
      results.push({ item, ok: true, stage: 'done', documentId, error: null });
    } catch (error) {
      results.push({ item, ok: false, stage: 'attach', documentId, error });
    }
  }
  return results;
}
