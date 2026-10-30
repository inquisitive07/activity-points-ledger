"""CP6: backend readiness for the React frontend. CORS, the Dean's student-status change and the eligibility rule,
the audit log API, the per-student document filter, and request document counts. Runs in the `db` fixture's
transaction (nothing persists); uploads go to the temporary test storage.
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import DEV_CORS_ORIGINS, Settings, parse_origins
from app.models import AuditLog, Event, PointLedger, PointRequest, Student
from scripts.seed import tiny_pdf

PW = "vtu@123"
DEAN, HOD_CS, HOD_EC = "dean@vtu.edu.in", "hod@vtu.edu.in", "hod.ece@vtu.edu.in"
AARAV, DIYA, ISHITA = "student@vtu.edu.in", "student2@vtu.edu.in", "student3@vtu.edu.in"
ROBOTICS, CODING = "club@vtu.edu.in", "coding.lead@vtu.edu.in"
AARAV_USN, DIYA_USN, ISHITA_USN = "1RV22CS001", "1RV22IS014", "1RV22EC008"
VITE = "http://localhost:5173"


@pytest.fixture
def as_(client):
    cache = {}

    def headers(email):
        if email not in cache:
            r = client.post("/auth/login", json={"email": email, "password": PW})
            assert r.status_code == 200, r.text
            cache[email] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[email]
    return headers


def event_id(db, name):
    return db.scalar(select(Event.event_id).where(Event.event_name == name))


def last_audit_id(db):
    return db.scalar(select(func.max(AuditLog.audit_id))) or 0


def ledger_rows(db):
    return db.execute(select(PointLedger.ledger_id, PointLedger.status).order_by(PointLedger.ledger_id)).all()


# --- CORS ---------------------------------------------------------------------------------------------------------

def preflight(client, origin, method="POST", headers="authorization,content-type"):
    return client.options("/requests", headers={"Origin": origin, "Access-Control-Request-Method": method,
                                                "Access-Control-Request-Headers": headers})


def test_cors_allows_the_vite_dev_server_with_a_bearer_token(client):
    r = preflight(client, VITE)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == VITE
    allowed = {h.strip().lower() for h in r.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "content-type"} <= allowed
    assert {"GET", "POST", "PATCH"} <= set(r.headers["access-control-allow-methods"].replace(" ", "").split(","))
    assert "access-control-allow-credentials" not in r.headers          # JWT in a header, never cookies
    simple = client.get("/health", headers={"Origin": VITE})
    assert simple.headers["access-control-allow-origin"] == VITE
    assert "content-disposition" in simple.headers["access-control-expose-headers"].lower()


@pytest.mark.parametrize("origin", ["http://evil.example", "http://localhost:3000", "https://localhost:5173"])
def test_cors_refuses_other_origins(client, origin):
    r = preflight(client, origin)
    assert r.status_code == 400 and "access-control-allow-origin" not in r.headers
    assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": origin}).headers


def test_cors_refuses_unlisted_methods_and_headers(client):
    assert preflight(client, VITE, method="DELETE").status_code == 400
    assert preflight(client, VITE, headers="x-custom").status_code == 400


def test_cors_origins_come_from_the_environment(monkeypatch):
    assert parse_origins(DEV_CORS_ORIGINS) == ["http://localhost:5173", "http://127.0.0.1:5173"]
    monkeypatch.setenv("CORS_ORIGINS", " https://ledger.example.edu/ , https://admin.example.edu ")
    assert Settings().cors_origin_list == ["https://ledger.example.edu", "https://admin.example.edu"]


@pytest.mark.parametrize("value", ["*", "https://*.example.edu", "ledger.example.edu", "http://ok.example,*"])
def test_cors_wildcards_and_bare_hosts_are_refused(monkeypatch, value):
    monkeypatch.setenv("CORS_ORIGINS", value)
    with pytest.raises(ValueError):
        Settings()


# --- student lifecycle (Dean) and the eligibility rule ------------------------------------------------------------

def set_status(client, h, usn, status, reason=None):
    return client.post(f"/students/{usn}/status", headers=h, json={"student_status": status, "reason": reason})


def test_dean_changes_a_student_status_with_an_audit_entry(client, db, as_):
    h = as_(DEAN)                                                       # log in first (LOGIN is audited too)
    before, start = ledger_rows(db), last_audit_id(db)
    r = set_status(client, h, AARAV_USN, "GRADUATED", "  Completed the programme.  ")
    assert r.status_code == 200, r.text
    assert r.json()["student_status"] == "GRADUATED" and Decimal(r.json()["earned_points"]) == 70
    [entry] = db.scalars(select(AuditLog).where(AuditLog.audit_id > start)).all()
    assert (entry.action, entry.entity_type, entry.entity_id, entry.user_email) == \
        ("STUDENT_STATUS_CHANGED", "student", 0, DEAN)
    assert entry.old_values == {"usn": AARAV_USN, "student_status": "ACTIVE"}
    assert entry.new_values == {"usn": AARAV_USN, "student_status": "GRADUATED", "reason": "Completed the programme."}
    assert ledger_rows(db) == before                                    # nothing about the record is deleted
    assert db.get(Student, AARAV_USN) is not None


@pytest.mark.parametrize("status", ["GRADUATED", "INACTIVE", "ARCHIVED", "ACTIVE"])
def test_every_status_can_be_set(client, db, as_, status):
    if status == "ACTIVE":
        assert set_status(client, as_(DEAN), AARAV_USN, "INACTIVE").status_code == 200
    assert set_status(client, as_(DEAN), AARAV_USN, status).json()["student_status"] == status


@pytest.mark.parametrize("email", [HOD_CS, AARAV, ROBOTICS])
def test_only_the_dean_changes_student_status(client, db, as_, email):
    h = as_(email)
    start = last_audit_id(db)
    assert set_status(client, h, AARAV_USN, "GRADUATED").status_code == 403
    assert db.get(Student, AARAV_USN).student_status == "ACTIVE" and last_audit_id(db) == start


def test_student_status_input_rules(client, db, as_):
    h = as_(DEAN)
    assert set_status(client, h, "1RV22CS777", "GRADUATED").status_code == 404
    assert set_status(client, h, AARAV_USN, "DELETED").status_code == 422
    assert client.post(f"/students/{AARAV_USN}/status", headers=h,
                       json={"student_status": "GRADUATED", "usn": "X"}).status_code == 422
    r = set_status(client, h, AARAV_USN, "ACTIVE")
    assert r.status_code == 409 and "already ACTIVE" in r.json()["detail"]
    assert set_status(client, h, AARAV_USN.lower(), "INACTIVE").status_code == 200      # USN case is normalised


def claim(client, h, event, points=10):
    return client.post("/requests", headers=h, json={"event_id": event, "request_type": "NEW_ACTIVITY",
                                                      "claimed_points": points, "reason": "I took part."})


def test_status_controls_new_requests_only(client, db, as_):
    """Only ACTIVE students submit; the Dean setting a student back to ACTIVE restores it; an existing request of a
    non-ACTIVE student is still reviewed and approved by the Dean (the same eligibility bulk commits now follow)."""
    cloud, dean = event_id(db, "Cloud Computing Workshop"), as_(DEAN)
    blood = db.scalar(select(PointRequest.request_id).where(PointRequest.usn == "1RV22CD033",
                                                            PointRequest.status == "PENDING_DEAN"))
    set_status(client, dean, AARAV_USN, "GRADUATED")
    set_status(client, dean, "1RV22CD033", "ARCHIVED")
    r = claim(client, as_(AARAV), cloud)
    assert r.status_code == 403 and "is GRADUATED" in r.json()["detail"]
    approve = client.post(f"/requests/{blood}/dean-review", headers=dean, json={"action": "APPROVE"})
    assert approve.status_code == 200 and approve.json()["status"] == "APPROVED" and approve.json()["ledger_id"]
    set_status(client, dean, AARAV_USN, "ACTIVE", "Claim for past activity.")
    assert claim(client, as_(AARAV), cloud).status_code == 201


# --- audit log API ------------------------------------------------------------------------------------------------

def test_dean_reads_the_audit_log_newest_first_with_pagination(client, db, as_):
    h = as_(DEAN)
    set_status(client, h, AARAV_USN, "INACTIVE")
    set_status(client, h, AARAV_USN, "ACTIVE")
    total = db.scalar(select(func.count()).select_from(AuditLog))
    page = client.get("/audit?limit=2", headers=h).json()
    assert page["total"] == total and (page["limit"], page["offset"]) == (2, 0) and len(page["items"]) == 2
    assert [i["action"] for i in page["items"]] == ["STUDENT_STATUS_CHANGED"] * 2
    assert page["items"][0]["new_values"]["student_status"] == "ACTIVE"           # newest first
    assert page["items"][0]["audit_id"] > page["items"][1]["audit_id"]
    nxt = client.get("/audit?limit=2&offset=2", headers=h).json()
    assert nxt["items"][0]["audit_id"] < page["items"][1]["audit_id"]
    assert client.get(f"/audit?offset={total}", headers=h).json()["items"] == []


def test_audit_log_filters(client, db, as_):
    h = as_(DEAN)
    set_status(client, h, AARAV_USN, "GRADUATED")
    only = client.get("/audit?action=student_status_changed&entity_type=student&user_email=DEAN@vtu.edu.in",
                      headers=h).json()
    assert only["total"] == 1 and only["items"][0]["old_values"]["student_status"] == "ACTIVE"
    by_entity = client.get("/audit?entity_type=student&entity_id=0", headers=h).json()
    assert by_entity["total"] == 1 and by_entity["items"][0]["new_values"]["usn"] == AARAV_USN
    assert client.get("/audit?action=NO_SUCH_ACTION", headers=h).json() == {"items": [], "total": 0,
                                                                            "limit": 50, "offset": 0}


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "offset=-1", "entity_id=x"])
def test_audit_log_rejects_bad_paging(client, as_, query):
    assert client.get(f"/audit?{query}", headers=as_(DEAN)).status_code == 422


@pytest.mark.parametrize("email", [HOD_CS, AARAV, ROBOTICS])
def test_only_the_dean_reads_the_audit_log(client, as_, email):
    assert client.get("/audit", headers=as_(email)).status_code == 403
    assert client.get("/audit").status_code == 401


# --- per-student document filter (HOD inspection view) -----------------------------------------------------------

def names(client, h, usn):
    r = client.get(f"/documents?usn={usn}", headers=h)
    assert r.status_code == 200, r.text
    return {d["file_name"] for d in r.json()}


AARAV_DOCS = {"RoboWars 2026 signed list.pdf", "Rhythm Fest certificate.pdf", "Internship letter.pdf",
              "HackRVCE certificate.pdf"}


def test_documents_of_one_student(client, db, as_):
    assert names(client, as_(HOD_CS), AARAV_USN) == AARAV_DOCS
    assert names(client, as_(DEAN), AARAV_USN.lower()) == AARAV_DOCS
    assert names(client, as_(DEAN), DIYA_USN) == {"RoboWars 2026 signed list.pdf"}
    assert names(client, as_(AARAV), AARAV_USN) == AARAV_DOCS


def test_the_student_filter_never_widens_a_vault(client, db, as_):
    """Out of scope gives an empty list, never an error and never a hint."""
    assert names(client, as_(HOD_EC), AARAV_USN) == set()             # another department
    assert names(client, as_(HOD_CS), DIYA_USN) == set()
    # Aarav sees the shared RoboWars signed list through his own award, but filtering by Diya must not reveal that
    # the same list also backs Diya's award.
    assert names(client, as_(AARAV), DIYA_USN) == set()
    # The Robotics lead sees awards for its own events, so the signed list backing them is in both students' views,
    # but none of Aarav's other documents are.
    assert names(client, as_(ROBOTICS), AARAV_USN) == {"RoboWars 2026 signed list.pdf"}
    assert names(client, as_(ROBOTICS), DIYA_USN) == {"RoboWars 2026 signed list.pdf"}
    assert names(client, as_(ROBOTICS), ISHITA_USN) == set()


# --- request document counts --------------------------------------------------------------------------------------

def test_requests_report_their_attached_document_count(client, db, as_):
    h = as_(AARAV)
    hack = db.scalar(select(PointRequest.request_id).where(PointRequest.usn == AARAV_USN,
                                                           PointRequest.event_id == event_id(db, "HackRVCE 24h")))
    assert client.get(f"/requests/{hack}", headers=h).json()["document_count"] == 1          # seeded certificate
    up = client.post("/documents", headers=h, data={"document_type": "PHOTO"},
                     files={"file": ("team.pdf", tiny_pdf(["team photo"]), "application/pdf")})
    assert up.status_code == 201, up.text
    assert client.post(f"/requests/{hack}/documents", headers=h,
                       json={"document_id": up.json()["document_id"]}).status_code == 201
    mine = {r["request_id"]: r["document_count"] for r in client.get("/requests", headers=h).json()}
    assert mine[hack] == 2
    assert client.get(f"/requests/{hack}", headers=as_(CODING)).json()["document_count"] == 2   # the club sees it too
    assert len(client.get(f"/documents?request_id={hack}", headers=as_(CODING)).json()) == 2
