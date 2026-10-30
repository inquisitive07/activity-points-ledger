from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
PW = "vtu@123"


def token(email, pw=PW):
    r = c.post("/auth/login", json={"email": email, "password": pw})
    return r, {"Authorization": f"Bearer {r.json().get('access_token', '')}"}


def test_login_ok_and_wrong_password():
    r, _ = token("dean@vtu.edu.in"); assert r.status_code == 200 and r.json()["user"]["role"] == "DEAN"
    assert token("dean@vtu.edu.in", "nope")[0].status_code == 401
    assert token("ghost@vtu.edu.in")[0].status_code == 401


def test_no_token_and_bad_token():
    assert c.get("/auth/me").status_code == 401
    assert c.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_hod_sees_only_own_department():
    _, h = token("hod@vtu.edu.in")
    usns = [s["usn"] for s in c.get("/students", headers=h).json()]
    assert usns and all("CS" in u for u in usns)
    assert c.get("/students/1VT22EC008", headers=h).status_code == 404   # EC student, CS HOD
    assert c.get("/students/1VT22CS001", headers=h).status_code == 200


def test_student_sees_only_self():
    _, h = token("student@vtu.edu.in")
    assert [s["usn"] for s in c.get("/students", headers=h).json()] == ["1VT22CS001"]
    assert c.get("/students/1VT22CS014", headers=h).status_code == 404


def test_roles_and_dean_scope():
    _, d = token("dean@vtu.edu.in"); assert len(c.get("/students", headers=d).json()) >= 6
    _, k = token("club@vtu.edu.in"); assert c.get("/students", headers=k).status_code == 403


# ---- extra step-2 coverage -------------------------------------------------
import jwt
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select, update
from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, User


def test_expired_and_tampered_tokens_rejected():
    uid = c.post("/auth/login", json={"email": "dean@vtu.edu.in", "password": PW}).json()["user"]["id"]
    expired = jwt.encode({"sub": uid, "role": "DEAN", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256")
    forged = jwt.encode({"sub": uid, "role": "DEAN", "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "wrong-secret", algorithm="HS256")
    for t in (expired, forged):
        assert c.get("/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_disabled_account_cannot_login_or_use_old_token():
    _, h = token("hod@vtu.edu.in")
    with SessionLocal() as db:
        db.execute(update(User).where(User.email == "hod@vtu.edu.in").values(is_active=False)); db.commit()
    assert c.get("/auth/me", headers=h).status_code == 401
    assert token("hod@vtu.edu.in")[0].status_code == 401


def test_login_events_are_audited():
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(AuditLog))
    token("dean@vtu.edu.in"); token("dean@vtu.edu.in", "bad")
    with SessionLocal() as db:
        acts = [a for a in db.scalars(select(AuditLog.action).order_by(AuditLog.id.desc()).limit(2))]
        assert db.scalar(select(func.count()).select_from(AuditLog)) == before + 2
    assert set(acts) == {"LOGIN", "LOGIN_FAILED"}


def test_brute_force_is_rate_limited_and_does_not_leak_which_emails_exist():
    for _ in range(5):
        assert token("dean@vtu.edu.in", "wrong1")[0].status_code == 401
    assert token("dean@vtu.edu.in")[0].status_code == 429       # even the right password is blocked now
    assert token("someone@else.com", "x" * 8)[0].status_code == 401


def test_password_hash_never_exposed():
    r, h = token("student@vtu.edu.in")
    assert "password" not in r.text and "password" not in c.get("/auth/me", headers=h).text
