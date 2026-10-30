"""CP1: authentication, role guards and object-level scoping, against the seeded vtu_ledger_test database.

Every test runs inside the `db` fixture's transaction (the API client shares that session), so nothing persists.
Seed facts used here come from scripts/seed.py.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.config import settings
from app.db import get_db
from app.models import (AuditLog, Document, PointLedger, PointRequest, Role, Student, UserAccount)
from app.permissions import (can_manage_event, document_scope, ledger_scope, request_scope, require_roles,
                             student_scope)
from app.security import ALGORITHM, decode_token

PW = "vtu@123"
DEAN, STUDENT, HOD_CS, HOD_EC, ROBOTICS = ("dean@vtu.edu.in", "student@vtu.edu.in", "hod@vtu.edu.in",
                                           "hod.ece@vtu.edu.in", "club@vtu.edu.in")
DISABLED = "student6@vtu.edu.in"


def login(client, email, password=PW):
    return client.post("/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def headers(client, email):
    r = login(client, email)
    assert r.status_code == 200, r.text
    return bearer(r.json()["access_token"])


def audit_rows(db, since_id, action=None):
    stmt = select(AuditLog).where(AuditLog.audit_id > since_id).order_by(AuditLog.audit_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    return db.scalars(stmt).all()


def last_audit_id(db):
    return db.scalar(select(AuditLog.audit_id).order_by(AuditLog.audit_id.desc()).limit(1)) or 0


def forge(sub=DEAN, *, secret=None, exp_delta=timedelta(hours=1), drop=(), **extra):
    now = datetime.now(timezone.utc)
    claims = {"sub": sub, "role": "DEAN", "iat": now, "exp": now + exp_delta, "jti": uuid.uuid4().hex, **extra}
    for key in drop:
        claims.pop(key)
    return jwt.encode(claims, secret or settings.jwt_secret, algorithm=ALGORITHM)


# --- login --------------------------------------------------------------------------------------------------------

def test_login_success_returns_token_for_the_email_and_updates_last_login(client, db):
    before = db.get(UserAccount, DEAN).last_login_at
    start = last_audit_id(db)
    r = login(client, "  Dean@VTU.edu.in ")                 # email is normalised
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == settings.access_token_minutes * 60
    assert body["user"] == {**body["user"], "email": DEAN, "role": "DEAN", "student_usn": None, "club_id": None}
    claims = decode_token(body["access_token"])
    assert claims["sub"] == DEAN and claims["role"] == "DEAN" and claims["jti"]
    db.expire_all()
    assert db.get(UserAccount, DEAN).last_login_at not in (None, before)
    [entry] = audit_rows(db, start)
    assert (entry.action, entry.user_email, entry.entity_type, entry.entity_id) == ("LOGIN", DEAN, "user_account", 0)


@pytest.mark.parametrize("email, role, extra", [
    (STUDENT, "STUDENT", {"student_usn": "1RV22CS001", "department_code": "CS", "display_name": "Aarav Sharma"}),
    (HOD_CS, "HOD", {"department_code": "CS", "student_usn": None}),
    (ROBOTICS, "CLUB_LEAD", {"club_name": "Robotics Club", "department_code": None}),
])
def test_me_reports_each_roles_associations(client, email, role, extra):
    r = client.get("/auth/me", headers=headers(client, email))
    assert r.status_code == 200
    assert r.json() == {**r.json(), "email": email, "role": role, **extra}


def test_wrong_password_is_rejected_and_audited_against_the_account(client, db):
    start = last_audit_id(db)
    r = login(client, DEAN, "not-the-password")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"
    [entry] = audit_rows(db, start)
    assert (entry.action, entry.user_email, entry.new_values) == ("LOGIN_FAILED", DEAN, {"reason": "wrong_password"})


def test_unknown_email_gets_the_same_answer_and_is_not_written_to_the_audit_log(client, db):
    start = last_audit_id(db)
    typed = "my-secret-password-typed-in-the-email-box"
    r = login(client, typed, PW)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid email or password"
    [entry] = audit_rows(db, start)
    assert entry.action == "LOGIN_FAILED" and entry.user_email is None
    assert entry.new_values == {"reason": "unknown_account"} and typed not in str(entry.new_values)


def test_disabled_account_cannot_log_in(client, db):
    start = last_audit_id(db)
    r = login(client, DISABLED)
    assert r.status_code == 403 and "disabled" in r.json()["detail"]
    assert "access_token" not in r.json()
    [entry] = audit_rows(db, start)
    assert (entry.action, entry.user_email) == ("LOGIN_REJECTED", DISABLED)


def test_disabling_an_account_invalidates_its_existing_token(client, db):
    h = headers(client, HOD_CS)
    assert client.get("/auth/me", headers=h).status_code == 200
    db.execute(update(UserAccount).where(UserAccount.email == HOD_CS).values(account_status="DISABLED"))
    db.flush()
    db.expire_all()
    assert client.get("/auth/me", headers=h).status_code == 401
    assert login(client, HOD_CS).status_code == 403


def test_brute_force_is_rate_limited(client):
    for _ in range(5):
        assert login(client, DEAN, "wrong").status_code == 401
    assert login(client, DEAN).status_code == 429          # even the right password, for this email + IP
    assert login(client, STUDENT).status_code == 200       # other accounts are unaffected


def test_login_input_is_validated(client):
    assert client.post("/auth/login", json={"email": DEAN}).status_code == 422
    assert client.post("/auth/login", json={"email": DEAN, "password": "x" * 129}).status_code == 422


# --- token validation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("token", [
    "junk",
    forge(exp_delta=timedelta(minutes=-1)),                               # expired
    forge(secret="a-different-secret-of-sufficient-length"),             # bad signature
    forge(drop=("jti",)),                                                 # missing required claim
    forge(drop=("exp",)),
    forge(sub="nobody@vtu.edu.in"),                                       # no such account
    jwt.encode({"sub": DEAN, "iat": 0, "exp": 9999999999, "jti": "x"}, None, algorithm="none"),  # unsigned
])
def test_invalid_tokens_are_rejected(client, token):
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_missing_token_is_rejected(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_role_claim_is_not_trusted(client):
    """A validly signed token's role claim is ignored: the role always comes from user_account."""
    token = forge(sub=STUDENT)                       # claims DEAN, but the account is a STUDENT
    r = client.get("/auth/me", headers=bearer(token))
    assert r.status_code == 200 and r.json()["role"] == "STUDENT"
    assert [s["usn"] for s in client.get("/students", headers=bearer(token)).json()] == ["1RV22CS001"]


# --- logout -------------------------------------------------------------------------------------------------------

def test_logout_revokes_only_that_token_and_is_audited(client, db):
    first = login(client, STUDENT).json()["access_token"]
    second = login(client, STUDENT).json()["access_token"]
    start = last_audit_id(db)
    r = client.post("/auth/logout", headers=bearer(first))
    assert r.status_code == 204 and r.content == b""
    [entry] = audit_rows(db, start)
    assert (entry.action, entry.user_email, entry.entity_type) == ("LOGOUT", STUDENT, "user_account")
    assert entry.new_values == {"jti": decode_token(first)["jti"]}
    assert client.get("/auth/me", headers=bearer(first)).status_code == 401
    assert client.post("/auth/logout", headers=bearer(first)).status_code == 401
    assert client.get("/auth/me", headers=bearer(second)).status_code == 200


def test_logout_requires_a_valid_token(client):
    assert client.post("/auth/logout").status_code == 401
    assert client.post("/auth/logout", headers=bearer(forge(exp_delta=timedelta(seconds=-5)))).status_code == 401


# --- secrets ------------------------------------------------------------------------------------------------------

def test_no_secrets_in_responses_audit_rows_or_logs(client, db, caplog):
    caplog.set_level(logging.DEBUG)
    start = last_audit_id(db)
    r = login(client, STUDENT)
    token = r.json()["access_token"]
    responses = [r.text, client.get("/auth/me", headers=bearer(token)).text, login(client, STUDENT, "Wr0ng!").text,
                 client.post("/auth/logout", headers=bearer(token)).text]
    stored_hash = db.get(UserAccount, STUDENT).password_hash
    audit_text = " ".join(f"{a.old_values} {a.new_values}" for a in audit_rows(db, start))
    for text in responses + [audit_text, caplog.text]:
        for secret in (PW, "Wr0ng!", stored_hash, settings.jwt_secret, "password_hash"):
            assert secret not in text
    for text in responses[1:] + [audit_text, caplog.text]:   # the token appears only in the login response
        assert token not in text


# --- role guards --------------------------------------------------------------------------------------------------

def guarded_app(db):
    app = FastAPI()
    for name, roles in {"dean": (Role.DEAN,), "staff": (Role.DEAN, Role.HOD), "club": (Role.CLUB_LEAD,),
                        "student": (Role.STUDENT,)}.items():
        app.get(f"/{name}")(lambda user=Depends(require_roles(*roles)): {"email": user.email})
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.mark.parametrize("email, allowed", [
    (DEAN, {"dean", "staff"}), (HOD_CS, {"staff"}), (ROBOTICS, {"club"}), (STUDENT, {"student"}),
])
def test_require_roles(client, db, email, allowed):
    h, guarded = headers(client, email), guarded_app(db)
    for route in ("dean", "staff", "club", "student"):
        assert guarded.get(f"/{route}", headers=h).status_code == (200 if route in allowed else 403), route
    assert guarded.get("/dean").status_code == 401


# --- scoping through the API -------------------------------------------------------------------------------------

def test_student_sees_only_themself(client):
    h = headers(client, STUDENT)
    assert [s["usn"] for s in client.get("/students", headers=h).json()] == ["1RV22CS001"]
    me = client.get("/students/1rv22cs001", headers=h).json()
    # 10 RoboWars + 40 NSS + 20 corrected Rhythm Fest; the superseded 15 does not count.
    assert (Decimal(me["earned_points"]), Decimal(me["remaining_points"]), me["target_reached"]) == (70, 30, False)
    assert {(l["event_name"], l["status"]) for l in me["ledger"]} >= {("Rhythm Fest", "SUPERSEDED")}
    assert client.get("/students/1RV22IS014", headers=h).status_code == 404


def test_hod_sees_only_own_department(client):
    h = headers(client, HOD_CS)
    assert [s["usn"] for s in client.get("/students", headers=h).json()] == ["1RV22CS001"]
    assert client.get("/students/1RV22EC008", headers=h).status_code == 404
    assert client.get("/students/1RV22CS001", headers=h).status_code == 200
    assert [s["usn"] for s in client.get("/students", headers=headers(client, HOD_EC)).json()] == ["1RV22EC008"]


def test_dean_sees_every_student_including_graduated(client):
    rows = client.get("/students", headers=headers(client, DEAN)).json()
    assert len(rows) == 7 and {"1RV19AI050", "1RV23CY402"} <= {s["usn"] for s in rows}
    assert [s["usn"] for s in client.get("/students?q=diya", headers=headers(client, DEAN)).json()] == ["1RV22IS014"]


def test_club_lead_has_no_student_directory(client):
    h = headers(client, ROBOTICS)
    assert client.get("/students", headers=h).status_code == 403
    assert client.get("/students/1RV22CS001", headers=h).status_code == 403


# --- object-level scopes (used by the CP2+ routers) --------------------------------------------------------------

def account(db, email):
    return db.get(UserAccount, email)


def doc_names(db, email):
    return set(db.scalars(select(Document.file_name).where(document_scope(account(db, email)))))


ALL_DOCS = {"RoboWars 2026 signed list.pdf", "Rhythm Fest certificate.pdf", "Internship letter.pdf",
            "HackRVCE certificate.pdf", "HackRVCE 24h signed list.pdf"}
AARAV_DOCS = ALL_DOCS - {"HackRVCE 24h signed list.pdf"}   # the unlinked HackRVCE list is still under review


@pytest.mark.parametrize("email, expected", [
    (DEAN, ALL_DOCS),
    (STUDENT, AARAV_DOCS),                                       # own uploads + signed list behind own award
    ("student2@vtu.edu.in", {"RoboWars 2026 signed list.pdf"}),  # not Aarav's certificates
    ("student3@vtu.edu.in", set()),
    (HOD_CS, AARAV_DOCS),
    (HOD_EC, set()),
    (ROBOTICS, {"RoboWars 2026 signed list.pdf"}),
    ("cultural.lead@vtu.edu.in", {"Rhythm Fest certificate.pdf"}),
    ("coding.lead@vtu.edu.in", {"HackRVCE certificate.pdf", "HackRVCE 24h signed list.pdf"}),
    ("nss.lead@vtu.edu.in", set()),
])
def test_document_scope(db, email, expected):
    assert doc_names(db, email) == expected


def test_soft_deleted_documents_are_dean_only(db):
    db.execute(update(Document).where(Document.file_name == "Internship letter.pdf").values(document_status="DELETED"))
    db.flush()
    assert "Internship letter.pdf" not in doc_names(db, STUDENT) | doc_names(db, HOD_CS)
    assert "Internship letter.pdf" in doc_names(db, DEAN)


@pytest.mark.parametrize("email, expected_usns, count", [
    (DEAN, None, 11),
    (STUDENT, {"1RV22CS001"}, 4),
    (HOD_EC, {"1RV22EC008"}, 1),
    (ROBOTICS, {"1RV22EC008"}, 1),                     # the only request on a Robotics Club event
    ("cultural.lead@vtu.edu.in", {"1RV22CS001", "1RV22IS014", "1RV23EI419"}, 4),
])
def test_request_scope(db, email, expected_usns, count):
    usns = db.scalars(select(PointRequest.usn).where(request_scope(account(db, email)))).all()
    assert len(usns) == count and (expected_usns is None or set(usns) == expected_usns)


def test_ledger_scope(db):
    assert set(db.scalars(select(PointLedger.usn).where(ledger_scope(account(db, ROBOTICS))))) == \
        {"1RV22CS001", "1RV22IS014"}                   # the two RoboWars bulk awards
    assert set(db.scalars(select(PointLedger.usn).where(ledger_scope(account(db, HOD_CS))))) == {"1RV22CS001"}
    assert db.scalars(select(PointLedger.usn).where(ledger_scope(account(db, "student3@vtu.edu.in")))).all() == []


def test_student_scope_matches_roles(db):
    def usns(email):
        return set(db.scalars(select(Student.usn).where(student_scope(account(db, email)))))
    assert usns(STUDENT) == {"1RV22CS001"} and usns(HOD_EC) == {"1RV22EC008"} and usns(ROBOTICS) == set()
    assert len(usns(DEAN)) == 7


def test_can_manage_event(db):
    from app.models import Event
    robowars = db.scalar(select(Event).where(Event.event_name == "RoboWars 2026"))
    blood = db.scalar(select(Event).where(Event.event_name == "Blood Donation Drive"))      # non-club
    assert can_manage_event(account(db, ROBOTICS), robowars)
    assert not can_manage_event(account(db, "coding.lead@vtu.edu.in"), robowars)
    assert not can_manage_event(account(db, DEAN), robowars) and can_manage_event(account(db, DEAN), blood)
    assert not can_manage_event(account(db, ROBOTICS), blood)
    assert not any(can_manage_event(account(db, e), robowars) for e in (HOD_CS, STUDENT))
