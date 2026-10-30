import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.bodylimit import BodySizeLimit
from app.config import settings
from app.db import get_db
from app.routers import audit_log, auth, claims, documents, events, ledger, ocr, students

if settings.jwt_secret == "change-me-in-production" or len(settings.jwt_secret) < 32:
    logging.getLogger("app").warning("JWT_SECRET is the default or shorter than 32 characters; "
                                     "set a long random value outside local development.")

app = FastAPI(title="RVCE Activity Points Ledger API")
# Added first so it sits inside CORS: a 413 still carries the CORS headers the browser needs to read it.
app.add_middleware(BodySizeLimit)        # refuses oversized bodies while they stream in
# Only the configured origins (CORS_ORIGINS). The JWT travels in the Authorization header, never in a cookie, so
# credentials (cookies) are not allowed. Content-Disposition is exposed so the frontend can read download names.
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"],
                   expose_headers=["Content-Disposition"], max_age=600)
app.include_router(auth.router)
app.include_router(students.router)
app.include_router(events.router)
app.include_router(claims.router)
app.include_router(documents.router)
app.include_router(ledger.router)
app.include_router(ocr.router)
app.include_router(audit_log.router)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
