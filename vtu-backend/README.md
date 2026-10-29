# VTU Activity Points Ledger — Backend (step 1: infrastructure + schema)

Stack: FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 16 · Redis · MinIO (Docker Compose)

## Run
    cp .env.example .env
    docker compose up --build        # db, redis, minio, api (migrations run on start)
    docker compose exec api python scripts/seed.py
    open http://localhost:8000/health   # {"status":"ok"}   docs: /docs

Demo users (password `vtu@123`): student@ / club@ / dean@ / hod@ vtu.edu.in

## Migrations
    docker compose exec api alembic upgrade head
    docker compose exec api alembic revision --autogenerate -m "describe change"
    docker compose exec api alembic downgrade -1

## Schema highlights
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
