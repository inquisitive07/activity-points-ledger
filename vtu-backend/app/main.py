import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.routers import auth, claims, events, students

if settings.jwt_secret == "change-me-in-production" or len(settings.jwt_secret) < 32:
    logging.getLogger("app").warning("JWT_SECRET is the default or shorter than 32 characters; "
                                     "set a long random value outside local development.")

app = FastAPI(title="RVCE Activity Points Ledger API")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(auth.router)
app.include_router(students.router)
app.include_router(events.router)
app.include_router(claims.router)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}
