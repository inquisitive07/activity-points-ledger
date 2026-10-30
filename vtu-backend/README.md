# VTU Activity Points Ledger — Backend

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
database.

| Email | Role | Linked to |
|---|---|---|
| `student@vtu.edu.in` | STUDENT | 1VT22CS001 Aarav Sharma (CSE) |
| `student2@vtu.edu.in` | STUDENT | 1VT22CS014 Diya Nair (CSE) — has reached the 100-point target |
| `student3@vtu.edu.in` | STUDENT | 1VT22EC008 Ishita Rao (ECE) |
| `student4@vtu.edu.in` | STUDENT | 1VT22CS033 Neha Patil (CSE) |
| `student5@vtu.edu.in` | STUDENT | 1VT22EC019 Karthik Bhat (ECE, lateral) |
| `student6@vtu.edu.in` | STUDENT | 1VT22CS027 Rohan Gowda (CSE, lateral) — **DISABLED**, login must be refused |
| `club@vtu.edu.in` | CLUB_LEAD | Robotics Club |
| `cultural.lead@vtu.edu.in` | CLUB_LEAD | Cultural Club |
| `nss.lead@vtu.edu.in` | CLUB_LEAD | NSS Unit |
| `coding.lead@vtu.edu.in` | CLUB_LEAD | Coding Club |
| `dean@vtu.edu.in` | DEAN | — |
| `hod@vtu.edu.in` | HOD | CSE |
| `hod.ece@vtu.edu.in` | HOD | ECE |

Departments seeded: CSE, ISE, ECE, ME, CV.

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

## Step 2 — Auth (done)
    POST /auth/login   {email, password} -> {access_token, user}
    GET  /auth/me      (Bearer token)
    GET  /students, GET /students/{usn}   (scoped by role)
    docker compose exec api pytest -q tests

## Step 3 — Events, claims, approval workflow (done)
    GET/POST /events          POST = club lead only (own club)
    GET  /clubs, /clubs/accountability (Dean matrix)
    POST /requests            student submits NEW_ACTIVITY / MISSING_FROM_LIST / CORRECTION
    GET  /requests?status=    students: own · club leads: own club's events · Dean: all
    POST /requests/{id}/club-review   {action: VERIFY|REJECT, comment}   (comment mandatory on reject)
    POST /requests/{id}/dean-review   {action: APPROVE|REJECT, comment}  (approve writes POINT_LEDGER; correction supersedes old row)
Tests use a separate `<db>_test` database, never your real data: `docker compose exec api pytest -q tests`
