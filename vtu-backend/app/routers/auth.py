from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.deps import get_current_user
from app.models import Student, User
from app.schemas import LoginIn, TokenOut, UserOut
from app.security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Hash used to keep response time similar when the email does not exist (no user enumeration).
_DUMMY = hash_password("timing-equaliser")


def to_out(user: User, db: Session) -> UserOut:
    usn = db.scalar(select(Student.usn).where(Student.user_id == user.id))
    return UserOut(id=str(user.id), email=user.email, name=user.name, role=user.role.value,
                   department_code=user.department_code, club_id=user.club_id, usn=usn)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    ok = verify_password(body.password, user.password_hash if user else _DUMMY)
    if not user or not ok or not user.is_active:
        audit(db, user.id if user else None, "LOGIN_FAILED", "user", detail={"email": body.email[:120]})
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    audit(db, user.id, "LOGIN", "user", user.id)
    db.commit()
    return TokenOut(access_token=create_token(str(user.id), user.role.value), user=to_out(user, db))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return to_out(user, db)
