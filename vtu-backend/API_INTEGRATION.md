# Frontend integration guide — RVCE Activity Points Ledger API

For the React app in `vtu-ledger/`. Every endpoint and example here is implemented and covered by `tests/`. The
interactive OpenAPI reference is at `http://localhost:8001/docs` (JSON at `/openapi.json`).

## 1. Basics

| | |
|---|---|
| Base URL (development) | `http://localhost:8001` (the `api` container; see `README.md`) |
| Base URL (frontend integration) | `http://localhost:8002` (`api-frontend`, database `vtu_ledger_frontend`; see `README.md`). The React app uses this via `VITE_API_BASE_URL` |
| Format | JSON in and out, except uploads (`multipart/form-data`) and file downloads (the file's bytes) |
| Points | Decimal **strings** with two places: `"10.00"`. Send numbers or strings; never use floats for totals |
| Times | ISO 8601 UTC (`"2026-03-18T12:00:00Z"`); dates `"2026-03-18"`; times `"10:00:00"` |
| CORS | Allowed origins come from `CORS_ORIGINS` (default `http://localhost:5173,http://127.0.0.1:5173`) |

### CORS
- Methods `GET, POST, PATCH, OPTIONS`; request headers `Authorization, Content-Type`; exposed `Content-Disposition`.
- `allow_credentials` is off: the token is sent in the `Authorization` header, never as a cookie. Do not use
  `credentials: 'include'`.
- `CORS_ORIGINS` is a comma-separated list of exact origins. `*`, wildcards and bare hosts are refused at startup.
  Production sets its own value (e.g. `CORS_ORIGINS=https://ledger.example.edu`); the default is for the Vite dev
  server only.

## 2. Authentication

```
POST /auth/login     {"email": "student@vtu.edu.in", "password": "vtu@123"}
```
```json
{"access_token": "eyJhbGciOi...", "token_type": "bearer", "expires_in": 28800,
 "user": {"email": "student@vtu.edu.in", "role": "STUDENT", "display_name": "Aarav Sharma",
          "student_usn": "1RV22CS001", "department_code": "CS", "club_id": null, "club_name": null,
          "last_login_at": "2026-10-30T15:30:16.268197Z"}}
```
- Send `Authorization: Bearer <access_token>` on every other request. The token is an HS256 JWT; treat it as opaque
  (do not decode it for roles: role and account status are re-read from the database on every request).
- `expires_in` is seconds (default 8 h). After expiry, or after logout, requests get **401**: send the user to login.
- `GET /auth/me` returns the `user` object again (use it to restore a session on page reload).
- `POST /auth/logout` (Bearer) → **204**. That token is revoked server-side; discard it.
- Login errors: 401 `Invalid email or password`, 403 disabled account, 429 after repeated failures.
- Store the token in memory (or `sessionStorage`), not in a cookie.

**Roles** are `STUDENT`, `CLUB_LEAD`, `HOD`, `DEAN` (the current mock uses `CLUB`; map it to `CLUB_LEAD`).
`user.display_name` replaces the mock's `user.name`; `club_name`, `department_code` and `student_usn` replace
`user.club`, `user.dept` and `user.usn`.

## 3. Errors

Every error body is `{"detail": ...}`. `detail` is a human-readable string, except for 422 request-validation errors
(FastAPI's list form):
```json
{"detail": [{"type": "greater_than_equal", "loc": ["body", "claimed_points"],
             "msg": "Input should be greater than or equal to 0", "input": -1, "ctx": {"ge": 0}}]}
```

| Status | Meaning | Example `detail` |
|---|---|---|
| 401 | Missing/invalid/expired/logged-out token | `Not authenticated` |
| 403 | Your role (or ownership) does not allow the action | `Your role cannot access this resource` |
| 404 | Unknown ID **or an ID outside your scope** (deliberately identical) | `Request not found` |
| 409 | Valid request, but the current state forbids it (workflow conflict, duplicate, concurrent change) | `Request is APPROVED; it cannot move to REJECTED` |
| 413 | Upload over the size limit | `File is larger than the 10 MB limit` |
| 415 | File type not PDF/JPEG/PNG, or contents do not match the declared type | `Only PDF, JPEG and PNG files are accepted` |
| 422 | Invalid input (schema or business validation) | see above |
| 429 | Too many failed logins | `Too many failed attempts. Try again in a few minutes.` |

Show `detail` strings to the user; they are written for that. Never infer existence from a 404.

## 4. Lists, filtering and pagination

- List endpoints return plain JSON arrays, already restricted to the caller's scope, in a stable order (students by
  USN, events by date, requests/batches/documents newest first). Filters are optional query parameters (below);
  unknown enum values give 422.
- `GET /audit` is the only paginated endpoint (the log is unbounded): `limit` 1–200 (default 50), `offset` ≥ 0,
  response `{"items": [...], "total": n, "limit": 50, "offset": 0}`.
- The other lists are bounded by the institution's data (students, events, requests, documents per scope) and are
  not paginated yet.

## 5. Endpoints by screen

### Who can see what (enforced by the backend; out-of-scope IDs are 404)
| Role | Students | Events | Requests / awards | Documents |
|---|---|---|---|---|
| STUDENT | self | all except DRAFT | own | uploaded by them, or backing their requests/awards |
| CLUB_LEAD | none (403 on `/students`) | own club's (all statuses) | on own club's events | tagged to own club's events, or backing those requests/awards |
| HOD | own department | all | of own department's students | of own department's students; read-only |
| DEAN | all | all | all | all (including soft-deleted) |

### Students and points
```
GET  /students?q=           DEAN, HOD (department), STUDENT (self); q matches name or USN
GET  /students/{usn}        summary + full ledger history (ACTIVE, SUPERSEDED, REVOKED rows)
POST /students/{usn}/status DEAN  {"student_status": "GRADUATED", "reason": "Completed the programme."}
```
`GET /students/{usn}`:
```json
{"usn": "1RV22CS001", "name": "Aarav Sharma", "department_code": "CS", "entry_type": "REGULAR",
 "student_status": "ACTIVE", "required_points": "100.00", "earned_points": "70.00", "remaining_points": "30.00",
 "target_reached": false,
 "ledger": [{"ledger_id": 1, "event_id": 1, "event_name": "RoboWars 2026", "points_awarded": "10.00",
             "award_date": "2026-03-18", "source_type": "BULK_APPROVAL", "status": "ACTIVE",
             "supersedes_ledger_id": null}]}
```
- Totals come from the `STUDENT_POINT_SUMMARY` view (ACTIVE awards only). Do not recompute them in the browser.
- Status change (Dean only): any of `ACTIVE, GRADUATED, INACTIVE, ARCHIVED`; 409 if unchanged; audited; nothing is
  deleted. Returns the student summary (without `ledger`).
- **Eligibility rule.** Only `ACTIVE` students may *submit new* requests (others get 403
  `Only currently enrolled (ACTIVE) students can submit new requests; ...`). Status never affects login, read access,
  or the Dean's review of requests that already exist. Awards follow one rule on both paths: an individual Dean
  approval and a bulk OCR commit can both award a non-ACTIVE student. In OCR review such a row is flagged
  `STUDENT_NOT_ACTIVE` (an EXCEPTION), so the Dean must confirm it individually.

### Events and clubs
```
GET   /events?status=&academic_year=&club_id=
GET   /events/{id}
POST  /events               CLUB_LEAD (own club) or DEAN (non-club event); created as DRAFT
PATCH /events/{id}          owner; while DRAFT or PUBLISHED; send only changed fields
POST  /events/{id}/status   {"event_status": "PUBLISHED"}: DRAFT -> PUBLISHED -> COMPLETED -> CLOSED, one step
GET   /clubs                any signed-in user
GET   /clubs/accountability DEAN: per club {open_club_issues, pending_dean_issues, resolved_issues}
```
`POST /events` body: `event_name` (required), `event_date` (required), `academic_year` (required, `"2026-27"`),
optional `description, start_time, end_time, venue, registration_url (http/https), standard_points`.
"Publish event" in the club screen is two calls: `POST /events`, then `POST /events/{id}/status` `PUBLISHED`.
Students may submit requests for `PUBLISHED` and `COMPLETED` events; filter the claim form's event list on
`event_status`. Accountability counts only `MISSING_FROM_LIST` and `CORRECTION` requests.

### Point requests
```
POST /requests                      STUDENT  {"event_id": 6, "request_type": "NEW_ACTIVITY",
                                              "claimed_points": 5, "reason": "Took part in the workshop."}
GET  /requests?status=&request_type=&event_id=
GET  /requests/{id}
POST /requests/{id}/withdraw        the requesting student, while pending
POST /requests/{id}/club-review     CLUB_LEAD of the event's club  {"action": "VERIFY" | "REJECT", "comment": "..."}
POST /requests/{id}/dean-review     DEAN  {"action": "APPROVE" | "REJECT", "comment": "..."}
POST /requests/{id}/documents       the requesting student  {"document_id": 12}   (while pending)
```
```json
{"request_id": 8, "usn": "1RV22CS001", "student_name": "Aarav Sharma", "event_id": 4, "event_name": "HackRVCE 24h",
 "club_id": 4, "request_type": "MISSING_FROM_LIST", "claimed_points": "15.00",
 "reason": "I took part in HackRVCE but my USN is not on the signed list.", "submitted_by": "student@vtu.edu.in",
 "correction_of_ledger_id": null, "status": "PENDING_CLUB", "club_reviewed_by": null, "club_reviewed_at": null,
 "club_review_comment": null, "dean_decided_by": null, "dean_decided_at": null, "dean_decision_comment": null,
 "created_at": "2026-01-25T10:00:00Z", "updated_at": "2026-01-25T10:00:00Z", "ledger_id": null, "document_count": 1}
```
- `CORRECTION` requires `correction_of_ledger_id` (an ACTIVE award of the student for that event);
  `MISSING_FROM_LIST` is for club events only. One open request per student and event; no request if an ACTIVE award
  exists (use CORRECTION or attach evidence instead).
- Club events start in `PENDING_CLUB`, non-club events in `PENDING_DEAN`. A rejection needs a `comment` (422 without).
- `ledger_id` is set once APPROVED. `document_count` counts attached, non-deleted documents.
- Status flow: `PENDING_CLUB -> PENDING_DEAN -> APPROVED | REJECTED`; `WITHDRAWN` by the student while pending;
  `RESOLVED` only by a committed signed list (OCR). Terminal states never change (409).

### Ledger (official awards)
The ledger is read through `GET /students/{usn}` (history) and written only by `dean-review APPROVE` and the OCR
commit. Approving a CORRECTION marks the old row `SUPERSEDED` and adds a new ACTIVE row with
`supersedes_ledger_id`; nothing is overwritten. The only ledger endpoint adds evidence, never points:
```
POST /ledger/{ledger_id}/documents   the award's student (own document) or DEAN  {"document_id": 12}
```

### Documents (vaults) and uploads
```
POST /documents                       multipart: file, document_type, event_id (optional)  -> 201
GET  /documents?document_type=&event_id=&status=&request_id=&ledger_id=&usn=
GET  /documents/{id}                  metadata + request_ids / ledger_ids it backs (within your scope)
GET  /documents/{id}/content?disposition=attachment|inline   the file, after the scope check
POST /documents/{id}/archive          uploader or Dean: ACTIVE -> ARCHIVED
POST /documents/{id}/delete           uploader or Dean: soft delete (blocked while it backs awards/open requests)
```
- **Upload format:** `multipart/form-data` with `file`, `document_type` and optional `event_id`. PDF, JPEG or PNG
  only, at most **10 MB** (`MAX_UPLOAD_BYTES`); the declared type must match the file's contents (415 otherwise).
  One file per call: upload each file, then attach it (`/requests/{id}/documents` or `/ledger/{id}/documents`).
- Allowed `document_type` per role: STUDENT `CERTIFICATE, PHOTO, OTHER`; CLUB_LEAD `ATTENDANCE_SHEET, PHOTO,
  REPORT` (must tag one of their club's events); DEAN any, including `SIGNED_POINT_LIST`; HOD none (403).
- There is no "purpose" field: a document is evidence when it is linked to a request/award, official when its type
  is `SIGNED_POINT_LIST`. The mock's "Certificate PDF / Event Photo / Official Signed PDF" map to `CERTIFICATE /
  PHOTO / SIGNED_POINT_LIST`.
- `usn=` narrows a vault to one student's documents (for the HOD inspection panel); it never widens it: a USN outside
  your scope gives `[]`.
- Downloads: fetch `/documents/{id}/content` with the Bearer header and use the blob (an `<a href>` cannot send the
  header). The suggested filename is in `Content-Disposition`. Storage keys are never exposed.
```json
{"document_id": 6, "event_id": null, "event_name": null, "document_type": "CERTIFICATE", "file_name": "cert.pdf",
 "mime_type": "application/pdf", "file_size_bytes": 585, "file_hash": "565e00ae...", "uploaded_by":
 "student@vtu.edu.in", "uploaded_at": "2026-10-30T15:30:18.278325Z", "document_status": "ACTIVE"}
```

### OCR workflow (Dean only)
```
1. POST /documents                          document_type=SIGNED_POINT_LIST, event_id=<the event>  (tag it here)
2. POST /ocr/batches                        {"document_id": 7}  -> 202, status QUEUED
3. GET  /ocr/batches/{id}                   poll every few seconds until REVIEW or FAILED
4. GET  /ocr/batches/{id}/records?validation_status=EXCEPTION
5. PATCH /ocr/records/{rid}                 {"matched_student_usn": "1RV22CS001", "extracted_name": "...",
                                             "extracted_points": 10}   (send only what changes)
   POST  /ocr/records/{rid}/confirm | /dismiss | /reopen      optional {"comment": "..."}
   POST  /ocr/batches/{id}/confirm-valid    confirm every record that is still VALID, in one step
6. POST /ocr/batches/{id}/commit            every record CONFIRMED or DISMISSED -> awards, all or nothing
```
- Batch states: `QUEUED -> PROCESSING -> REVIEW -> COMMITTED`, or `FAILED`. `POST /ocr/batches/{id}/retry` retries a
  FAILED batch (as a new batch). `GET /ocr/batches?status=&document_id=` lists batches.
- The event comes from the signed list's `event_id` tag (the mock's "resolve event & club" step). OCR's own
  event/club reading is reported in `extracted_metadata.event_check` and as `EVENT_MISMATCH` / `CLUB_MISMATCH` /
  `UNMATCHED_EVENT` issues; nothing is created from OCR text.
- Batch counters: `pages_processed`, `records_detected`, `valid_records`, `exception_count` (as extracted),
  `record_counts` (current per status) and `issue_counts` (current EXCEPTION records per issue code).
- Each record has `issues`: codes derived from `validation_message`: `UNKNOWN_USN, INVALID_USN, MISSING_USN,
  UNMATCHED_LOW_CONFIDENCE, SELECTED_STUDENT_DIFFERS, MISSING_NAME, NAME_MISMATCH, STUDENT_NOT_ACTIVE,
  MISSING_POINTS, INVALID_POINTS, LOW_CONFIDENCE, DUPLICATE_ROW, POSSIBLE_DUPLICATE_AWARD, UNEXPECTED_STRUCTURE,
  UNMATCHED_EVENT, EVENT_MISMATCH, CLUB_MISMATCH`, and `OTHER` for text that is none of these (the seeded demo batch
  uses free text). Show `validation_message` for the details.
- Confirming is not awarding. Only `commit` writes `POINT_LEDGER`, resolves matching open `MISSING_FROM_LIST`
  requests (`RESOLVED`), and marks the batch `COMMITTED`. A signed list is committed at most once; a repeated commit
  is 409 and changes nothing.
- **Stalled batches.** If the API restarts while a batch is QUEUED or PROCESSING, the batch would stay there and
  block new batches for that document. After `OCR_STALE_MINUTES` (default 30) without progress the Dean can call
  `POST /ocr/batches/{id}/mark-failed` (409 before that, or if the batch is not in flight), then `retry`. Nothing is
  reprocessed automatically; a run that finishes after being marked failed is discarded, so no records or awards are
  duplicated.

`GET /ocr/batches/{id}/records` item:
```json
{"ocr_record_id": 5, "ocr_batch_id": 2, "page_number": 1, "row_number": 2, "extracted_usn": "1RV22CS0O1",
 "matched_student_usn": null, "matched_student_name": null, "extracted_name": "Aarav Sharma",
 "extracted_points": "15.00", "confidence_score": "0.6100", "raw_extraction": {"text": "1RV22CS0O1 Aarav Sharma 15"},
 "validation_status": "EXCEPTION", "validation_message": "Unknown USN (possible OCR misread)",
 "issues": ["UNKNOWN_USN"]}
```
`POST /ocr/batches/{id}/confirm-valid` → `{"batch": {...}, "records_confirmed": 2, "confirmed_record_ids": [41, 42]}`.
`POST /ocr/batches/{id}/commit` → `{"batch": {...}, "awards_created": 3, "ledger_ids": [...], "requests_resolved": 1,
"resolved_request_ids": [...], "records_confirmed": 3, "records_dismissed": 1}`.

### Audit log (Dean only)
```
GET /audit?action=&entity_type=&entity_id=&user_email=&limit=50&offset=0
```
```json
{"items": [{"audit_id": 9, "user_email": "dean@vtu.edu.in", "action": "STUDENT_STATUS_CHANGED",
            "entity_type": "student", "entity_id": 0,
            "old_values": {"usn": "1RV22CS001", "student_status": "ACTIVE"},
            "new_values": {"usn": "1RV22CS001", "reason": "Completed the programme.", "student_status": "GRADUATED"},
            "ip_address": null, "created_at": "2026-10-30T15:30:15.127127Z"}],
 "total": 9, "limit": 1, "offset": 0}
```
Entities keyed by text (user accounts, students) use `entity_id = 0`; the email or USN is in the values.

## 6. Screen-to-endpoint map

| Screen (`vtu-ledger/src`) | Calls |
|---|---|
| Login | `POST /auth/login`, `GET /auth/me`, `POST /auth/logout` |
| Student · Dashboard | `GET /students/{me.student_usn}` |
| Student · Claims & Evidence | `GET /events`, `POST /requests`, `POST /documents`, `POST /requests/{id}/documents`, `POST /ledger/{id}/documents`, `GET /requests` |
| Student · Vault | `GET /documents`, `GET /documents/{id}/content` |
| Club · Events | `GET /events`, `POST /events`, `POST /events/{id}/status` |
| Club · Verification Queue | `GET /requests?status=PENDING_CLUB`, `GET /documents?request_id=`, `POST /requests/{id}/club-review` |
| Dean · OCR Pipeline | `POST /documents`, `POST /ocr/batches`, `GET /ocr/batches/{id}`, `GET .../records`, `PATCH /ocr/records/{id}`, `POST .../confirm` / `dismiss` / `reopen`, `POST .../confirm-valid`, `POST .../commit`, `POST .../retry`, `POST .../mark-failed` |
| Dean · Claim Approvals | `GET /requests?status=PENDING_DEAN`, `GET /documents?request_id=`, `POST /requests/{id}/dean-review` |
| Dean · Club Accountability | `GET /clubs/accountability` |
| Dean · Student Import | none: student import is seed data only for the MVP (CLAUDE.md 18.5) |
| Dean · Audit & Reports | `GET /students` (completion report), `GET /audit` |
| HOD · Department Directory | `GET /students?q=`, `GET /students/{usn}`, `GET /documents?usn=` |
