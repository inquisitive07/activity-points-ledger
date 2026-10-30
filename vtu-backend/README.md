# RVCE Activity Points Ledger — Backend

Stack: FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16 · PaddleOCR (Docker Compose).
Uploaded files use private local storage (a Docker volume); S3-compatible object storage is a later phase.

## Services
| Service | Runs in | Reach it from the Mac | Reach it from other containers |
|---|---|---|---|
| `db` (PostgreSQL 16) | Docker | `localhost:5433` (pgAdmin, psql) | `db:5432` |
| `api` (FastAPI) | Docker, `linux/amd64` (emulated on Apple Silicon) | `http://localhost:8001` (docs: `/docs`) | `api:8000` |

Port 5432 on the Mac is a separate Homebrew PostgreSQL server; it is not this project's database.

Volumes: `pgdata` (database files), `storage` (uploaded files), `paddle_models` (OCR model cache).
`docker compose stop` / `down` keep volumes; only `docker compose down -v` deletes them.

## Run
    cp .env.example .env
    docker compose build api                              # first build is slow (OCR libraries)
    docker compose run --rm --no-deps api python scripts/ocr_smoke.py   # optional OCR health check
    docker compose up -d                                  # db + api; migrations run on api start
    docker compose exec api python scripts/seed.py
    curl http://localhost:8001/health                     # {"status":"ok"}

### Demo accounts — LOCAL DEVELOPMENT / DEMO ONLY
Created by `scripts/seed.py` for the local Docker database. Every account uses the password **`vtu@123`** (stored
only as a bcrypt hash, with a separate salt per account). Never seed these accounts into a shared or production
database. The `@vtu.edu.in` login emails (and the students' contact emails) are fictional development/demo
values; they are kept unchanged because the append-only audit history references the login emails.

Demo USNs are fictional and follow the RVCE format `1RV` + joining year (YY) + department code (DD) + serial (NNN),
e.g. `1RV24IS063`.

| Email | Role | Linked to |
|---|---|---|
| `student@vtu.edu.in` | STUDENT | 1RV22CS001 Aarav Sharma (CS) |
| `student2@vtu.edu.in` | STUDENT | 1RV22IS014 Diya Nair (IS) — has reached the 100-point target |
| `student3@vtu.edu.in` | STUDENT | 1RV22EC008 Ishita Rao (EC) |
| `student4@vtu.edu.in` | STUDENT | 1RV22CD033 Neha Patil (CD) |
| `student5@vtu.edu.in` | STUDENT | 1RV23EI419 Karthik Bhat (EI, lateral) |
| `student6@vtu.edu.in` | STUDENT | 1RV23CY402 Rohan Gowda (CY, lateral) — **DISABLED**, login must be refused |
| `club@vtu.edu.in` | CLUB_LEAD | Robotics Club |
| `cultural.lead@vtu.edu.in` | CLUB_LEAD | Cultural Club |
| `nss.lead@vtu.edu.in` | CLUB_LEAD | NSS Unit |
| `coding.lead@vtu.edu.in` | CLUB_LEAD | Coding Club |
| `dean@vtu.edu.in` | DEAN | — |
| `hod@vtu.edu.in` | HOD | CS |
| `hod.ece@vtu.edu.in` | HOD | EC |

Also seeded (no account): 1RV19AI050 Ananya Hegde (AI), GRADUATED — kept for history.

Departments seeded: IS, CS, CY, CD, AI, EC, EI, ME, CV.

An existing database seeded before the RVCE change is converted once with `scripts/rvce_correction.py` (dry run by
default; see its docstring for the transaction and file-handling sequence).

## Migrations
    docker compose exec api alembic upgrade head
    docker compose exec api alembic current
    docker compose exec api alembic revision -m "describe change"

## Schema highlights (pre-rebuild — being replaced by the 14-table schema in CLAUDE.md)
- `point_requirements`: 100 Regular / 75 Lateral. `point_ledger`: rows are never deleted, only SUPERSEDED.
- Partial unique index: one live (non-superseded) award per student per event.
- Rejections must carry a reviewer comment (CHECK). HOD rows must have a department, club leads a club.
- `request_attachments` (many files per claim) and `ledger_evidence` (evidence-only uploads on awarded rows).
- `ocr_batches` / `ocr_records`: issue + status per record; commit lock is enforced in step 5.
- `audit_log` is append-only (DB trigger blocks UPDATE/DELETE).

Next: step 2 — auth (bcrypt + JWT) and role/department scoping.

## CP1 — Authentication and authorization
    POST /auth/login    {email, password} -> {access_token, token_type, expires_in, user}
    GET  /auth/me       (Bearer token)
    POST /auth/logout   (Bearer token) -> 204; that token stops working
    GET  /students, GET /students/{usn}   (DEAN: all · HOD: own department · STUDENT: self · CLUB_LEAD: 403)
- bcrypt password hashes; HS256 JWT with the login email as `sub`, lifetime `ACCESS_TOKEN_MINUTES` (default 480).
- Every request reloads `user_account`: role and `account_status` come from the database, never from the token.
  DISABLED accounts get 403 at login and 401 with any existing token.
- Logout writes a `LOGOUT` audit row carrying the token's `jti`; tokens with a logged-out `jti` are rejected.
- Object scopes for students, requests, awards and documents live in `app/permissions.py`.
- Login, failed login, disabled-account login and logout are audited (`entity_type = user_account`, `entity_id = 0`).
- Set a long random `JWT_SECRET` outside local development (the API logs a warning for the default).

    docker compose run --rm --no-deps api pytest -q tests     # uses vtu_ledger_test only

## CP2 — Events and the point request workflow
    GET   /events?status=&academic_year=&club_id=   STUDENT: published (non-DRAFT) · CLUB_LEAD: own club · DEAN/HOD: all
    GET   /events/{id}
    POST  /events                       CLUB_LEAD (own club) or DEAN (non-club); created as DRAFT
    PATCH /events/{id}                  owner only, while DRAFT or PUBLISHED
    POST  /events/{id}/status           {event_status}: DRAFT -> PUBLISHED -> COMPLETED -> CLOSED, one step
    GET   /clubs                        any signed-in user
    GET   /clubs/accountability         DEAN; MISSING_FROM_LIST and CORRECTION requests only
    POST  /requests                     STUDENT; club event -> PENDING_CLUB, non-club -> PENDING_DEAN
    GET   /requests?status=&request_type=&event_id=, GET /requests/{id}   scoped by role
    POST  /requests/{id}/withdraw       the requesting student, while pending
    POST  /requests/{id}/club-review    CLUB_LEAD of the event's club {action: VERIFY|REJECT, comment}
    POST  /requests/{id}/dean-review    DEAN {action: APPROVE|REJECT, comment}; APPROVE writes POINT_LEDGER
New requests: PUBLISHED and COMPLETED events only (CLOSED is view-only; open requests on it stay reviewable);
Only ACTIVE students may submit (GRADUATED, INACTIVE, ARCHIVED: 403) but all keep read access. Rejections need a comment. RESOLVED is set only by the signed-list (OCR) workflow. Approval locks the request
(and, for corrections, the corrected award) and commits request, award and audit rows in one transaction.

Tests use a separate `<db>_test` database, never your real data: `docker compose exec api pytest -q tests`
