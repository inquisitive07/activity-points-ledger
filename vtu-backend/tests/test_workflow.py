"""CP2: events, clubs, point requests and Dean approval, against the seeded vtu_ledger_test database.

Most tests run inside the `db` fixture's transaction and leave nothing behind. The concurrency tests need two real
connections that commit, so they use the `committed` fixture, which deletes what they created afterwards (their
audit rows stay: audit_log is append-only, and the test database is dropped at the end of the run).
"""
import threading
import time
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

import app.db
from app.db import write_transaction
from app.models import AuditLog, Event, OcrBatch, PointLedger, PointRequest, Student, UserAccount
from app.services import request_workflow as wf
from db_safety import check_connected_database

PW = "vtu@123"
DEAN, HOD_CS, HOD_EC = "dean@vtu.edu.in", "hod@vtu.edu.in", "hod.ece@vtu.edu.in"
AARAV, DIYA, ISHITA, NEHA = ("student@vtu.edu.in", "student2@vtu.edu.in", "student3@vtu.edu.in",
                             "student4@vtu.edu.in")
ROBOTICS, CULTURAL, NSS, CODING = ("club@vtu.edu.in", "cultural.lead@vtu.edu.in", "nss.lead@vtu.edu.in",
                                   "coding.lead@vtu.edu.in")


@pytest.fixture
def as_(client):
    """as_(email) -> auth headers for that seeded account (logins are cached per test)."""
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


def request_id(db, usn, event_name, status=None):
    stmt = select(PointRequest.request_id).where(PointRequest.usn == usn,
                                                 PointRequest.event_id == event_id(db, event_name))
    return db.scalar(stmt.where(PointRequest.status == status) if status else stmt)


def active_award(db, usn, event_name):
    db.expire_all()
    return db.scalar(select(PointLedger).where(PointLedger.usn == usn, PointLedger.event_id == event_id(db, event_name),
                                               PointLedger.status == "ACTIVE"))


def ledger_count(db):
    return db.scalar(select(func.count()).select_from(PointLedger))


def last_audit_id(db):
    return db.scalar(select(func.max(AuditLog.audit_id))) or 0


def audits(db, since, action):
    return db.scalars(select(AuditLog).where(AuditLog.audit_id > since, AuditLog.action == action)
                      .order_by(AuditLog.audit_id)).all()


def claim(client, h, event, points=10, kind="NEW_ACTIVITY", reason="I took part in this event.", **extra):
    return client.post("/requests", headers=h, json={"event_id": event, "request_type": kind,
                                                      "claimed_points": points, "reason": reason, **extra})


def review(client, h, req_id, stage, action, comment=None):
    return client.post(f"/requests/{req_id}/{stage}-review", headers=h, json={"action": action, "comment": comment})


NEW_EVENT = {"event_name": "Line Follower Sprint", "event_date": "2026-12-12", "academic_year": "2026-27",
             "venue": "Robotics Lab", "standard_points": "5", "start_time": "10:00", "end_time": "12:00"}


# --- events: visibility ------------------------------------------------------------------------------------------

def names(client, h, query=""):
    r = client.get(f"/events{query}", headers=h)
    assert r.status_code == 200
    return {e["event_name"] for e in r.json()}


def test_students_see_published_events_but_never_drafts(client, db, as_):
    seen = names(client, as_(AARAV))
    assert "Robo Soccer Trials" not in seen                       # the only DRAFT
    assert {"Cloud Computing Workshop", "RoboWars 2026", "NSS Village Camp"} <= seen   # PUBLISHED/COMPLETED/CLOSED
    assert names(client, as_(AARAV), "?status=PUBLISHED") == {"Cloud Computing Workshop"}
    assert client.get(f"/events/{event_id(db, 'Robo Soccer Trials')}", headers=as_(AARAV)).status_code == 404


def test_dean_and_hod_see_all_events_club_leads_only_their_own(client, db, as_):
    assert len(names(client, as_(DEAN))) == len(names(client, as_(HOD_EC))) == 7
    assert names(client, as_(ROBOTICS)) == {"RoboWars 2026", "Robo Soccer Trials"}       # including its draft
    assert client.get(f"/events/{event_id(db, 'RoboWars 2026')}", headers=as_(CODING)).status_code == 404
    assert client.get(f"/events/{event_id(db, 'Blood Donation Drive')}", headers=as_(ROBOTICS)).status_code == 404
    assert client.get("/events/999999", headers=as_(DEAN)).status_code == 404


# --- events: writes ------------------------------------------------------------------------------------------------

def test_club_lead_creates_a_draft_for_their_own_club(client, db, as_):
    start = last_audit_id(db)
    r = client.post("/events", headers=as_(ROBOTICS), json=NEW_EVENT)
    assert r.status_code == 201
    e = r.json()
    assert (e["club_name"], e["event_status"], e["created_by"], e["standard_points"]) == \
        ("Robotics Club", "DRAFT", ROBOTICS, "5.00")
    [entry] = audits(db, start, "EVENT_CREATED")
    assert entry.entity_id == e["event_id"] and entry.new_values["club_id"] == e["club_id"]
    assert entry.new_values["event_status"] == "DRAFT" and entry.new_values["standard_points"] == "5.00"


def test_owner_fields_cannot_be_supplied_by_the_client(client, as_):
    for extra in ({"club_id": 2}, {"created_by": DEAN}, {"event_status": "PUBLISHED"}):
        assert client.post("/events", headers=as_(ROBOTICS), json={**NEW_EVENT, **extra}).status_code == 422


def test_dean_creates_non_club_events(client, as_):
    r = client.post("/events", headers=as_(DEAN), json=NEW_EVENT)
    assert r.status_code == 201 and r.json()["club_id"] is None and r.json()["created_by"] == DEAN


@pytest.mark.parametrize("bad", [
    {"event_name": "   "}, {"academic_year": "2026-28"}, {"standard_points": "-1"}, {"standard_points": "10000"},
    {"end_time": "09:00"}, {"registration_url": "javascript:alert(1)"}, {"event_date": "not-a-date"},
])
def test_event_input_is_validated(client, as_, bad):
    assert client.post("/events", headers=as_(ROBOTICS), json={**NEW_EVENT, **bad}).status_code == 422


def test_event_update_rules(client, db, as_):
    created = client.post("/events", headers=as_(ROBOTICS), json=NEW_EVENT).json()
    url = f"/events/{created['event_id']}"
    start = last_audit_id(db)
    r = client.patch(url, headers=as_(ROBOTICS), json={"venue": "Main Auditorium", "standard_points": "7.5"})
    assert r.status_code == 200 and (r.json()["venue"], r.json()["standard_points"]) == ("Main Auditorium", "7.50")
    [entry] = audits(db, start, "EVENT_UPDATED")
    assert entry.old_values == {"venue": "Robotics Lab", "standard_points": "5.00"}
    assert entry.new_values == {"venue": "Main Auditorium", "standard_points": "7.50"}
    assert client.patch(url, headers=as_(ROBOTICS), json={"end_time": "09:00"}).status_code == 422  # merged check
    assert client.patch(url, headers=as_(ROBOTICS), json={"event_name": None}).status_code == 422
    assert client.patch(url, headers=as_(ROBOTICS), json={"club_id": 3}).status_code == 422
    assert client.patch(url, headers=as_(CODING), json={"venue": "x"}).status_code == 404          # other club
    assert client.patch(url, headers=as_(DEAN), json={"venue": "x"}).status_code == 403            # club event
    blood = f"/events/{event_id(db, 'Blood Donation Drive')}"
    assert client.patch(blood, headers=as_(ROBOTICS), json={"venue": "x"}).status_code == 404
    # Blood Donation Drive is COMPLETED: no longer editable, even by its owner (the Dean).
    assert client.patch(blood, headers=as_(DEAN), json={"venue": "x"}).status_code == 409


def test_event_status_moves_forward_one_step_at_a_time(client, db, as_):
    eid = client.post("/events", headers=as_(DEAN), json=NEW_EVENT).json()["event_id"]

    def move(status):
        return client.post(f"/events/{eid}/status", headers=as_(DEAN), json={"event_status": status})
    assert move("COMPLETED").status_code == 409                    # cannot skip PUBLISHED
    assert move("DRAFT").status_code == 409                        # no-op is not a transition
    start = last_audit_id(db)
    for status in ("PUBLISHED", "COMPLETED", "CLOSED"):
        r = move(status)
        assert r.status_code == 200 and r.json()["event_status"] == status
    assert [(a.old_values["event_status"], a.new_values["event_status"])
            for a in audits(db, start, "EVENT_STATUS_CHANGED")] == \
        [("DRAFT", "PUBLISHED"), ("PUBLISHED", "COMPLETED"), ("COMPLETED", "CLOSED")]
    for status in ("DRAFT", "PUBLISHED", "COMPLETED", "CLOSED"):
        assert move(status).status_code == 409                     # CLOSED is final; nothing goes backwards
    assert move("ARCHIVED").status_code == 422


def test_only_the_owner_changes_event_status(client, db, as_):
    draft = event_id(db, "Robo Soccer Trials")
    body = {"event_status": "PUBLISHED"}
    assert client.post(f"/events/{draft}/status", headers=as_(CODING), json=body).status_code == 404
    assert client.post(f"/events/{draft}/status", headers=as_(DEAN), json=body).status_code == 403
    assert client.post(f"/events/{draft}/status", headers=as_(ROBOTICS), json=body).status_code == 200
    assert "Robo Soccer Trials" in names(client, as_(AARAV))      # published: now visible to students


# --- every write endpoint, every role ---------------------------------------------------------------------------

WRITES = [("post", "/events", NEW_EVENT), ("patch", "/events/{e}", {"venue": "x"}),
          ("post", "/events/{e}/status", {"event_status": "PUBLISHED"}),
          ("post", "/requests", {"event_id": 1, "request_type": "NEW_ACTIVITY", "claimed_points": 5, "reason": "x"*5}),
          ("post", "/requests/{r}/withdraw", None),
          ("post", "/requests/{r}/club-review", {"action": "VERIFY"}),
          ("post", "/requests/{r}/dean-review", {"action": "APPROVE"})]
ALLOWED = {"STUDENT": {"/requests", "/requests/{r}/withdraw"},
           "CLUB_LEAD": {"/events", "/events/{e}", "/events/{e}/status", "/requests/{r}/club-review"},
           "DEAN": {"/events", "/events/{e}", "/events/{e}/status", "/requests/{r}/dean-review"},
           "HOD": set()}


@pytest.mark.parametrize("email, role", [(HOD_CS, "HOD"), (HOD_EC, "HOD"), (AARAV, "STUDENT"),
                                         (ROBOTICS, "CLUB_LEAD"), (DEAN, "DEAN")])
def test_role_guard_on_every_write_endpoint(client, db, as_, email, role):
    ledger_before = ledger_count(db)
    # Each role is pointed at an event it owns, so a 403 here can only come from the role guard.
    own_event = "Blood Donation Drive" if role == "DEAN" else "Robo Soccer Trials"
    paths = {"e": event_id(db, own_event), "r": request_id(db, "1RV22CD033", "Blood Donation Drive")}
    for method, path, body in WRITES:
        r = getattr(client, method)(path.format(**paths), headers=as_(email), **({"json": body} if body else {}))
        if path in ALLOWED[role]:
            assert r.status_code != 403, (method, path, r.text)
        else:
            assert r.status_code == 403, (method, path, r.status_code)
    if role != "DEAN":
        assert ledger_count(db) == ledger_before                  # only the Dean's approval writes the ledger


def test_service_layer_checks_roles_too(db):
    """The routers' guards are not the only line: the service refuses the wrong role on its own."""
    club, hod, student = (db.get(UserAccount, e) for e in (ROBOTICS, HOD_CS, AARAV))
    req = request_id(db, "1RV22EC008", "RoboWars 2026")
    for user, call in [(club, lambda u: wf.dean_review(db, u, req, "APPROVE", None)),
                       (hod, lambda u: wf.club_review(db, u, req, "VERIFY", None)),
                       (student, lambda u: wf.dean_review(db, u, req, "APPROVE", None)),
                       (club, lambda u: wf.submit(db, u, None))]:
        with pytest.raises(HTTPException) as exc:
            call(user)
        assert exc.value.status_code == 403


# --- clubs -----------------------------------------------------------------------------------------------------

def test_clubs_list_for_any_signed_in_user(client, as_):
    for email in (AARAV, HOD_CS, ROBOTICS, DEAN):
        assert len(client.get("/clubs", headers=as_(email)).json()) == 4
    assert client.get("/clubs").status_code == 401


def counts(client, as_):
    r = client.get("/clubs/accountability", headers=as_(DEAN))
    assert r.status_code == 200
    return {c["club_name"]: (c["open_club_issues"], c["pending_dean_issues"], c["resolved_issues"]) for c in r.json()}


def test_club_accountability_counts_issues_only(client, db, as_):
    assert counts(client, as_) == {
        "Coding Club": (1, 0, 0),        # Aarav's MISSING_FROM_LIST for HackRVCE, PENDING_CLUB
        "Cultural Club": (0, 0, 1),      # Aarav's approved CORRECTION; Karthik's rejected NEW_ACTIVITY is ignored
        "NSS Unit": (0, 0, 0),           # only approved NEW_ACTIVITY claims
        "Robotics Club": (0, 1, 0)}      # Ishita's MISSING_FROM_LIST, PENDING_DEAN
    cloud = event_id(db, "Cloud Computing Workshop")
    assert claim(client, as_(NEHA), cloud).json()["status"] == "PENDING_CLUB"         # ordinary claim
    assert counts(client, as_)["Coding Club"] == (1, 0, 0)
    assert claim(client, as_(DIYA), cloud, kind="MISSING_FROM_LIST").status_code == 201
    assert counts(client, as_)["Coding Club"] == (2, 0, 0)
    review(client, as_(CODING), request_id(db, "1RV22CS001", "HackRVCE 24h"), "club", "VERIFY")
    assert counts(client, as_)["Coding Club"] == (1, 1, 0)        # moved from the club's queue to the Dean's
    for email in (HOD_CS, ROBOTICS, AARAV):
        assert client.get("/clubs/accountability", headers=as_(email)).status_code == 403


# --- requests: submission ----------------------------------------------------------------------------------------

def test_submission_routing_and_audit(client, db, as_):
    start = last_audit_id(db)
    club = claim(client, as_(AARAV), event_id(db, "Cloud Computing Workshop"))
    assert club.status_code == 201 and club.json()["status"] == "PENDING_CLUB"
    assert (club.json()["usn"], club.json()["submitted_by"]) == ("1RV22CS001", AARAV)   # from the account
    non_club = claim(client, as_(NEHA), event_id(db, "HackRVCE 24h"), kind="NEW_ACTIVITY")
    assert non_club.json()["status"] == "PENDING_CLUB"
    blood = claim(client, as_(ISHITA), event_id(db, "Blood Donation Drive"))
    assert blood.status_code == 201 and blood.json()["status"] == "PENDING_DEAN"        # non-club: straight to Dean
    entries = audits(db, start, "REQUEST_SUBMITTED")
    assert [e.entity_id for e in entries] == [club.json()["request_id"], non_club.json()["request_id"],
                                              blood.json()["request_id"]]
    assert entries[0].new_values == {"usn": "1RV22CS001", "event_id": club.json()["event_id"],
                                     "request_type": "NEW_ACTIVITY", "claimed_points": "10.00",
                                     "reason": "I took part in this event.", "submitted_by": AARAV,
                                     "correction_of_ledger_id": None, "status": "PENDING_CLUB"}


def test_identity_fields_cannot_be_supplied(client, db, as_):
    cloud = event_id(db, "Cloud Computing Workshop")
    for extra in ({"usn": "1RV22IS014"}, {"submitted_by": DIYA}, {"status": "APPROVED"}):
        assert claim(client, as_(AARAV), cloud, **extra).status_code == 422


@pytest.mark.parametrize("event, kind, points, reason, expected", [
    ("Robo Soccer Trials", "NEW_ACTIVITY", 5, "Took part.", 404),          # DRAFT: invisible to students
    ("Blood Donation Drive", "MISSING_FROM_LIST", 5, "Took part.", 422),   # non-club event has no signed club list
    ("RoboWars 2026", "NEW_ACTIVITY", 5, "Took part.", 409),               # Aarav already has an ACTIVE award
    ("HackRVCE 24h", "NEW_ACTIVITY", 5, "Took part.", 409),                # open request for this event exists
    ("Cloud Computing Workshop", "NEW_ACTIVITY", 5, "   ", 422),
    ("Cloud Computing Workshop", "NEW_ACTIVITY", -1, "Took part.", 422),
    ("Cloud Computing Workshop", "NEW_ACTIVITY", "10.001", "Took part.", 422),
    ("Cloud Computing Workshop", "CORRECTION", 5, "Took part.", 422),      # CORRECTION needs the ledger id
])
def test_submission_rules(client, db, as_, event, kind, points, reason, expected):
    assert claim(client, as_(AARAV), event_id(db, event), points, kind, reason).status_code == expected


def test_correction_must_target_the_students_own_active_award(client, db, as_):
    own = active_award(db, "1RV22CS001", "RoboWars 2026")
    robowars = event_id(db, "RoboWars 2026")

    def correction(ledger_id, event=robowars, points=15):
        return claim(client, as_(AARAV), event, points, "CORRECTION", "Finalist: 15 points.",
                     correction_of_ledger_id=ledger_id)
    diyas = active_award(db, "1RV22IS014", "RoboWars 2026")
    superseded = db.scalar(select(PointLedger.ledger_id).where(PointLedger.usn == "1RV22CS001",
                                                               PointLedger.status == "SUPERSEDED"))
    assert correction(diyas.ledger_id).status_code == 404              # another student's award
    assert correction(999999).status_code == 404
    assert correction(superseded, event_id(db, "Rhythm Fest")).status_code == 409
    assert correction(own.ledger_id, event_id(db, "Rhythm Fest")).status_code == 422   # award of another event
    assert correction(own.ledger_id, points=10).status_code == 422     # same as the current award
    assert claim(client, as_(AARAV), robowars, 15, "NEW_ACTIVITY", "x" * 5,
                 correction_of_ledger_id=own.ledger_id).status_code == 422
    r = correction(own.ledger_id)
    assert r.status_code == 201
    assert (r.json()["status"], r.json()["correction_of_ledger_id"]) == ("PENDING_CLUB", own.ledger_id)


# --- submission eligibility and graduated students (owner-confirmed rules) ---------------------------------------

def set_event_status(db, name, status):
    db.execute(update(Event).where(Event.event_name == name).values(event_status=status))
    db.flush()
    db.expire_all()


def test_students_can_view_published_completed_and_closed_events(client, db, as_):
    for name, status in (("Cloud Computing Workshop", "PUBLISHED"), ("RoboWars 2026", "COMPLETED"),
                         ("NSS Village Camp", "CLOSED")):
        r = client.get(f"/events/{event_id(db, name)}", headers=as_(NEHA))
        assert r.status_code == 200 and r.json()["event_status"] == status


@pytest.mark.parametrize("name, status, expected", [
    ("Cloud Computing Workshop", "PUBLISHED", 201),
    ("RoboWars 2026", "COMPLETED", 201),           # claims after the event has finished are allowed
    ("NSS Village Camp", "CLOSED", 409),           # visible, but no new requests
    ("Robo Soccer Trials", "DRAFT", 404),          # invisible, so it cannot be targeted either
])
def test_submission_eligibility_by_event_status(client, db, as_, name, status, expected):
    assert db.scalar(select(Event.event_status).where(Event.event_name == name)) == status
    r = claim(client, as_(NEHA), event_id(db, name), 5, "MISSING_FROM_LIST")    # Neha has no award for any of them
    assert r.status_code == expected, r.text
    if expected == 409:
        assert "no longer accepts new requests" in r.json()["detail"]


def test_closed_event_refuses_corrections_too(client, db, as_):
    nss = active_award(db, "1RV22CS001", "NSS Village Camp")
    r = claim(client, as_(AARAV), event_id(db, "NSS Village Camp"), 45, "CORRECTION", "Camp leader.",
              correction_of_ledger_id=nss.ledger_id)
    assert r.status_code == 409 and "CLOSED" in r.json()["detail"]


def test_open_requests_stay_reviewable_after_their_event_closes(client, db, as_):
    hack, ishita = request_id(db, "1RV22CS001", "HackRVCE 24h"), request_id(db, "1RV22EC008", "RoboWars 2026")
    set_event_status(db, "HackRVCE 24h", "CLOSED")
    set_event_status(db, "RoboWars 2026", "CLOSED")
    assert review(client, as_(CODING), hack, "club", "VERIFY").json()["status"] == "PENDING_DEAN"
    assert review(client, as_(DEAN), hack, "dean", "REJECT", "Not on the attendance sheet.").status_code == 200
    r = review(client, as_(DEAN), ishita, "dean", "APPROVE")
    assert r.status_code == 200 and r.json()["ledger_id"] is not None
    blood = request_id(db, "1RV22CD033", "Blood Donation Drive")
    set_event_status(db, "Blood Donation Drive", "CLOSED")
    assert client.post(f"/requests/{blood}/withdraw", headers=as_(NEHA)).json()["status"] == "WITHDRAWN"


def test_closing_an_event_through_the_api_stops_new_requests(client, db, as_):
    hack = event_id(db, "HackRVCE 24h")
    assert claim(client, as_(ISHITA), hack, 15, "MISSING_FROM_LIST").status_code == 201
    r = client.post(f"/events/{hack}/status", headers=as_(CODING), json={"event_status": "CLOSED"})
    assert r.status_code == 200
    assert claim(client, as_(DIYA), hack, 15, "CORRECTION", "Should be 20.",
                 correction_of_ledger_id=active_award(db, "1RV22IS014", "HackRVCE 24h").ledger_id).status_code == 409


def set_student_status(db, usn, status):
    db.execute(update(Student).where(Student.usn == usn).values(student_status=status))
    db.flush()
    db.expire_all()


NOT_ENROLLED = ["GRADUATED", "INACTIVE", "ARCHIVED"]


@pytest.mark.parametrize("status", NOT_ENROLLED)
def test_only_active_students_may_submit_any_request_type(client, db, as_, status):
    set_student_status(db, "1RV22CS001", status)
    h = as_(AARAV)                                         # the ACTIVE account still logs in
    cloud, robowars = event_id(db, "Cloud Computing Workshop"), event_id(db, "RoboWars 2026")
    attempts = {
        "NEW_ACTIVITY": dict(event=cloud),
        "MISSING_FROM_LIST": dict(event=cloud, kind="MISSING_FROM_LIST"),
        "CORRECTION": dict(event=robowars, points=15, kind="CORRECTION", reason="Finalist.",
                           correction_of_ledger_id=active_award(db, "1RV22CS001", "RoboWars 2026").ledger_id),
    }
    before, start = db.scalar(select(func.count()).select_from(PointRequest)), last_audit_id(db)
    for kind, body in attempts.items():
        r = claim(client, h, **body)
        assert r.status_code == 403, (kind, r.text)
        detail = r.json()["detail"]
        assert detail.startswith("Only currently enrolled (ACTIVE) students") and status in detail
    assert db.scalar(select(func.count()).select_from(PointRequest)) == before          # no request rows
    assert last_audit_id(db) == start                                                  # no audit rows


@pytest.mark.parametrize("status", NOT_ENROLLED)
def test_not_enrolled_students_keep_login_and_read_access(client, db, as_, status):
    set_student_status(db, "1RV22CS001", status)
    h = as_(AARAV)
    assert client.get("/auth/me", headers=h).json()["student_usn"] == "1RV22CS001"
    me = client.get("/students/1RV22CS001", headers=h).json()
    assert me["student_status"] == status and Decimal(me["earned_points"]) == 70 and me["ledger"]
    assert len(client.get("/requests", headers=h).json()) == 4                     # history stays visible
    assert client.get(f"/requests/{request_id(db, '1RV22CS001', 'HackRVCE 24h')}", headers=h).status_code == 200
    assert client.get("/events", headers=h).status_code == 200


@pytest.mark.parametrize("status", NOT_ENROLLED)
def test_existing_requests_stay_reviewable_and_withdrawable_after_status_change(client, db, as_, status):
    hack = request_id(db, "1RV22CS001", "HackRVCE 24h")                  # PENDING_CLUB
    ishita = request_id(db, "1RV22EC008", "RoboWars 2026")               # PENDING_DEAN
    blood = request_id(db, "1RV22CD033", "Blood Donation Drive")         # PENDING_DEAN
    for usn in ("1RV22CS001", "1RV22EC008", "1RV22CD033"):
        set_student_status(db, usn, status)
    assert review(client, as_(CODING), hack, "club", "VERIFY").json()["status"] == "PENDING_DEAN"
    r = review(client, as_(DEAN), hack, "dean", "APPROVE")
    assert r.status_code == 200 and r.json()["status"] == "APPROVED" and r.json()["ledger_id"] is not None
    assert review(client, as_(DEAN), blood, "dean", "REJECT", "No donation record.").json()["status"] == "REJECTED"
    assert client.post(f"/requests/{ishita}/withdraw", headers=as_(ISHITA)).json()["status"] == "WITHDRAWN"


def test_enrolled_students_can_still_submit(client, db, as_):
    set_student_status(db, "1RV22CS001", "GRADUATED")    # changing one student affects only that student
    for email in (DIYA, NEHA, ISHITA):
        r = claim(client, as_(email), event_id(db, "Cloud Computing Workshop"))
        assert r.status_code == 201 and r.json()["status"] == "PENDING_CLUB", email


# --- requests: visibility -----------------------------------------------------------------------------------------

def request_usns(client, h):
    return {r["usn"] for r in client.get("/requests", headers=h).json()}


def test_request_listing_and_detail_are_scoped(client, db, as_):
    assert request_usns(client, as_(AARAV)) == {"1RV22CS001"}
    assert request_usns(client, as_(ROBOTICS)) == {"1RV22EC008"}
    assert request_usns(client, as_(HOD_EC)) == {"1RV22EC008"}
    assert len(client.get("/requests", headers=as_(DEAN)).json()) == 11
    assert len(client.get("/requests?status=PENDING_DEAN", headers=as_(DEAN)).json()) == 2
    ishita = request_id(db, "1RV22EC008", "RoboWars 2026")
    for email, code in ((ISHITA, 200), (ROBOTICS, 200), (HOD_EC, 200), (DEAN, 200),
                        (AARAV, 404), (CODING, 404), (HOD_CS, 404)):
        assert client.get(f"/requests/{ishita}", headers=as_(email)).status_code == code, email


# --- withdrawal ----------------------------------------------------------------------------------------------------

def test_student_withdraws_only_their_own_open_requests(client, db, as_):
    hack = request_id(db, "1RV22CS001", "HackRVCE 24h")
    assert client.post(f"/requests/{hack}/withdraw", headers=as_(DIYA)).status_code == 404     # not hers
    start = last_audit_id(db)
    r = client.post(f"/requests/{hack}/withdraw", headers=as_(AARAV))
    assert r.status_code == 200 and r.json()["status"] == "WITHDRAWN"
    [entry] = audits(db, start, "REQUEST_WITHDRAWN")
    assert (entry.old_values, entry.new_values) == ({"status": "PENDING_CLUB"}, {"status": "WITHDRAWN"})
    assert client.post(f"/requests/{hack}/withdraw", headers=as_(AARAV)).status_code == 409    # terminal
    assert review(client, as_(CODING), hack, "club", "VERIFY").status_code == 409
    approved = request_id(db, "1RV22CS001", "NSS Village Camp")
    assert client.post(f"/requests/{approved}/withdraw", headers=as_(AARAV)).status_code == 409
    # PENDING_DEAN can be withdrawn too.
    assert client.post(f"/requests/{request_id(db, '1RV22EC008', 'RoboWars 2026')}/withdraw",
                       headers=as_(ISHITA)).json()["status"] == "WITHDRAWN"


# --- club review -------------------------------------------------------------------------------------------------

def test_club_verify_forwards_to_the_dean_without_touching_the_ledger(client, db, as_):
    hack, before = request_id(db, "1RV22CS001", "HackRVCE 24h"), ledger_count(db)
    assert review(client, as_(ROBOTICS), hack, "club", "VERIFY").status_code == 404          # not their club
    start = last_audit_id(db)
    r = review(client, as_(CODING), hack, "club", "VERIFY", "  Seen on the attendance sheet. ")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "PENDING_DEAN"
    assert (body["club_reviewed_by"], body["club_review_comment"]) == (CODING, "Seen on the attendance sheet.")
    assert body["club_reviewed_at"] and body["ledger_id"] is None and ledger_count(db) == before
    [entry] = audits(db, start, "REQUEST_CLUB_VERIFIED")
    assert entry.old_values["status"] == "PENDING_CLUB" and entry.old_values["club_reviewed_by"] is None
    assert entry.new_values["status"] == "PENDING_DEAN" and entry.new_values["club_reviewed_by"] == CODING
    assert review(client, as_(CODING), hack, "club", "VERIFY").status_code == 409            # already forwarded
    assert review(client, as_(CODING), hack, "club", "REJECT", "Changed my mind").status_code == 409


def test_club_reject_needs_a_comment_and_is_final(client, db, as_):
    hack = request_id(db, "1RV22CS001", "HackRVCE 24h")
    for comment in (None, "", "   "):
        assert review(client, as_(CODING), hack, "club", "REJECT", comment).status_code == 422
    r = review(client, as_(CODING), hack, "club", "REJECT", "Not on the attendance sheet.")
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert r.json()["club_review_comment"] == "Not on the attendance sheet."
    assert review(client, as_(DEAN), hack, "dean", "APPROVE").status_code == 409
    # The student may submit a new request for the same event after a rejection.
    assert claim(client, as_(AARAV), event_id(db, "HackRVCE 24h"), 15, "MISSING_FROM_LIST").status_code == 201


def test_review_actions_outside_each_stage_are_refused(client, db, as_):
    hack, ishita = request_id(db, "1RV22CS001", "HackRVCE 24h"), request_id(db, "1RV22EC008", "RoboWars 2026")
    assert review(client, as_(DEAN), hack, "dean", "APPROVE").status_code == 409       # still with the club
    assert review(client, as_(DEAN), hack, "dean", "REJECT", "No").status_code == 409
    assert review(client, as_(ROBOTICS), ishita, "club", "REJECT", "No").status_code == 409   # with the Dean now
    assert review(client, as_(CODING), hack, "club", "APPROVE").status_code == 422     # clubs cannot approve
    for action in ("RESOLVED", "WITHDRAWN", "VERIFY"):
        assert review(client, as_(DEAN), ishita, "dean", action).status_code == 422    # not Dean actions


# --- Dean decision -----------------------------------------------------------------------------------------------

def test_dean_approval_writes_one_individual_claim_award(client, db, as_):
    ishita = request_id(db, "1RV22EC008", "RoboWars 2026")
    earned = client.get("/students/1RV22EC008", headers=as_(DEAN)).json()["earned_points"]
    start = last_audit_id(db)
    r = review(client, as_(DEAN), ishita, "dean", "APPROVE", "Verified by the club.")
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["dean_decided_by"], body["dean_decision_comment"]) == \
        ("APPROVED", DEAN, "Verified by the club.")
    award = db.get(PointLedger, body["ledger_id"])
    assert (award.usn, award.event_id, award.points_awarded, award.status) == \
        ("1RV22EC008", event_id(db, "RoboWars 2026"), Decimal("10.00"), "ACTIVE")
    assert (award.source_type, award.source_request_id, award.source_ocr_batch_id, award.supersedes_ledger_id) == \
        ("INDIVIDUAL_CLAIM", ishita, None, None)
    assert award.approved_by == DEAN and award.approved_at is not None
    assert Decimal(client.get("/students/1RV22EC008", headers=as_(DEAN)).json()["earned_points"]) == \
        Decimal(earned) + 10
    [approved] = audits(db, start, "REQUEST_APPROVED")
    assert approved.old_values["status"] == "PENDING_DEAN" and approved.old_values["dean_decided_by"] is None
    assert approved.new_values["status"] == "APPROVED" and approved.new_values["ledger_id"] == award.ledger_id
    [created] = audits(db, start, "LEDGER_AWARD_CREATED")
    assert created.entity_id == award.ledger_id and created.new_values["source_type"] == "INDIVIDUAL_CLAIM"
    assert created.new_values["points_awarded"] == "10.00" and created.new_values["source_request_id"] == ishita


def test_repeated_approval_creates_nothing(client, db, as_):
    ishita = request_id(db, "1RV22EC008", "RoboWars 2026")
    assert review(client, as_(DEAN), ishita, "dean", "APPROVE").status_code == 200
    before, start = ledger_count(db), last_audit_id(db)
    for action, comment in (("APPROVE", None), ("REJECT", "Too late")):
        assert review(client, as_(DEAN), ishita, "dean", action, comment).status_code == 409
    assert ledger_count(db) == before and audits(db, start, "REQUEST_APPROVED") == []
    assert db.scalar(select(func.count()).select_from(PointLedger)
                     .where(PointLedger.source_request_id == ishita)) == 1


def test_dean_reject_needs_a_comment(client, db, as_):
    blood = request_id(db, "1RV22CD033", "Blood Donation Drive")
    for comment in (None, "  "):
        assert review(client, as_(DEAN), blood, "dean", "REJECT", comment).status_code == 422
    start, before = last_audit_id(db), ledger_count(db)
    r = review(client, as_(DEAN), blood, "dean", "REJECT", "No donation record.")
    assert r.status_code == 200 and r.json()["status"] == "REJECTED" and ledger_count(db) == before
    [entry] = audits(db, start, "REQUEST_REJECTED")
    assert entry.new_values["dean_decision_comment"] == "No donation record."
    assert entry.old_values["dean_decision_comment"] is None


def test_correction_approval_supersedes_the_original_award(client, db, as_):
    original = active_award(db, "1RV22CS001", "RoboWars 2026")
    original_id, earned = original.ledger_id, client.get("/students/1RV22CS001", headers=as_(DEAN)).json()
    r = claim(client, as_(AARAV), event_id(db, "RoboWars 2026"), "15.5", "CORRECTION", "Finalist.",
              correction_of_ledger_id=original_id)
    req = r.json()["request_id"]
    assert review(client, as_(ROBOTICS), req, "club", "VERIFY").json()["status"] == "PENDING_DEAN"
    start = last_audit_id(db)
    body = review(client, as_(DEAN), req, "dean", "APPROVE").json()
    assert body["status"] == "APPROVED"
    db.expire_all()
    old, new = db.get(PointLedger, original_id), db.get(PointLedger, body["ledger_id"])
    assert (old.status, old.points_awarded, old.source_type) == ("SUPERSEDED", Decimal("10.00"), "BULK_APPROVAL")
    assert (new.status, new.points_awarded, new.source_type) == ("ACTIVE", Decimal("15.50"), "MANUAL_CORRECTION")
    assert (new.supersedes_ledger_id, new.source_request_id, new.source_ocr_batch_id) == (original_id, req, None)
    after = client.get("/students/1RV22CS001", headers=as_(DEAN)).json()
    assert Decimal(after["earned_points"]) == Decimal(earned["earned_points"]) + Decimal("5.5")
    [superseded] = audits(db, start, "LEDGER_AWARD_SUPERSEDED")
    assert superseded.entity_id == original_id and superseded.old_values == {"status": "ACTIVE"}
    assert superseded.new_values == {"status": "SUPERSEDED", "superseded_by_ledger_id": new.ledger_id}
    [created] = audits(db, start, "LEDGER_AWARD_CREATED")
    assert created.new_values["supersedes_ledger_id"] == original_id


def test_correction_of_an_award_that_changed_meanwhile_is_refused_atomically(client, db, as_):
    original = active_award(db, "1RV22CS001", "RoboWars 2026")
    req = claim(client, as_(AARAV), event_id(db, "RoboWars 2026"), 15, "CORRECTION", "Finalist.",
                correction_of_ledger_id=original.ledger_id).json()["request_id"]
    review(client, as_(ROBOTICS), req, "club", "VERIFY")
    db.execute(update(PointLedger).where(PointLedger.ledger_id == original.ledger_id).values(status="REVOKED"))
    db.flush()
    before, start = ledger_count(db), last_audit_id(db)
    assert review(client, as_(DEAN), req, "dean", "APPROVE").status_code == 409
    db.expire_all()
    assert db.get(PointRequest, req).status == "PENDING_DEAN"            # rolled back, not half-approved
    assert ledger_count(db) == before and audits(db, start, "REQUEST_APPROVED") == []


def test_one_active_award_per_student_and_event(client, db, as_):
    """An award that appeared after submission (e.g. a bulk list) blocks a second ACTIVE award for the same event."""
    blood = request_id(db, "1RV22CD033", "Blood Donation Drive")
    batch = db.scalar(select(OcrBatch.ocr_batch_id).limit(1))
    db.add(PointLedger(usn="1RV22CD033", event_id=event_id(db, "Blood Donation Drive"), points_awarded=10,
                       award_date="2026-02-03", source_type="BULK_APPROVAL", source_ocr_batch_id=batch,
                       approved_by=DEAN, approved_at="2026-02-03T10:00:00+00:00", status="ACTIVE"))
    db.flush()
    before = ledger_count(db)
    assert review(client, as_(DEAN), blood, "dean", "APPROVE").status_code == 409
    db.expire_all()
    assert ledger_count(db) == before and db.get(PointRequest, blood).status == "PENDING_DEAN"


def test_write_transaction_maps_the_unique_index_to_409(db):
    dean = db.get(UserAccount, DEAN)
    existing = active_award(db, "1RV22CS001", "NSS Village Camp")
    blood = request_id(db, "1RV22CD033", "Blood Donation Drive")
    before = ledger_count(db)
    with pytest.raises(HTTPException) as exc:
        with write_transaction(db):
            db.add(PointLedger(usn=existing.usn, event_id=existing.event_id, points_awarded=1, award_date="2026-04-06",
                               source_type="INDIVIDUAL_CLAIM", source_request_id=blood, approved_by=dean.email,
                               approved_at="2026-04-06T10:00:00+00:00", status="ACTIVE"))
            db.flush()
    assert exc.value.status_code == 409 and "conflicts" in exc.value.detail
    assert ledger_count(db) == before


# --- concurrency (two real connections) --------------------------------------------------------------------------

@pytest.fixture
def committed(db_engine):
    """Sessions that really commit, on the test database only; created requests and their awards are deleted after."""
    sessions, created = [], []

    def session():
        s = Session(app.db.engine, expire_on_commit=False)
        check_connected_database(s.connection(), app.db.engine.url.database)
        sessions.append(s)
        return s

    yield session, created
    for s in sessions:
        s.close()
    with Session(app.db.engine) as s, s.begin():
        s.execute(delete(PointLedger).where(PointLedger.source_request_id.in_(created)))
        s.execute(delete(PointRequest).where(PointRequest.request_id.in_(created)))


def pending_dean_request(session, created, usn, event_name, points=7):
    s = session()
    req = PointRequest(usn=usn, event_id=s.scalar(select(Event.event_id).where(Event.event_name == event_name)),
                       request_type="NEW_ACTIVITY", claimed_points=points, reason="Concurrency test.",
                       submitted_by=DEAN, status="PENDING_DEAN")
    s.add(req)
    s.commit()
    created.append(req.request_id)
    return req.request_id


def approve_in_thread(session, req_id, results):
    def run():
        s = session()
        try:
            with write_transaction(s):
                wf.dean_review(s, s.get(UserAccount, DEAN), req_id, "APPROVE", None)
            results.append(200)
        except HTTPException as exc:
            results.append(exc.status_code)
    t = threading.Thread(target=run)
    t.start()
    return t


def test_concurrent_approvals_of_one_request_award_once(committed):
    session, created = committed
    req = pending_dean_request(session, created, "1RV22CD033", "Cloud Computing Workshop")
    first = session()
    wf.dean_review(first, first.get(UserAccount, DEAN), req, "APPROVE", None)     # holds the row lock
    results = []
    second = approve_in_thread(session, req, results)
    time.sleep(0.5)
    assert second.is_alive() and results == []          # blocked on SELECT ... FOR UPDATE
    first.commit()
    second.join(10)
    assert results == [409]                             # it then sees APPROVED
    check = session()
    assert check.scalar(select(func.count()).select_from(PointLedger)
                        .where(PointLedger.source_request_id == req)) == 1


def test_concurrent_approvals_for_the_same_student_and_event_award_once(committed):
    """Two different requests, same student and event: the second insert waits on the partial unique index and
    fails when the first commits; write_transaction turns that into a 409 and rolls the second approval back."""
    session, created = committed
    r1 = pending_dean_request(session, created, "1RV22CD033", "Cloud Computing Workshop")
    r2 = pending_dean_request(session, created, "1RV22CD033", "Cloud Computing Workshop")
    first = session()
    wf.dean_review(first, first.get(UserAccount, DEAN), r1, "APPROVE", None)
    results = []
    second = approve_in_thread(session, r2, results)
    time.sleep(0.5)
    assert second.is_alive()                            # blocked on the uncommitted ACTIVE row
    first.commit()
    second.join(10)
    assert results == [409]
    check = session()
    awards = check.scalars(select(PointLedger).where(PointLedger.source_request_id.in_([r1, r2]))).all()
    assert [(a.source_request_id, a.status) for a in awards] == [(r1, "ACTIVE")]
    assert check.get(PointRequest, r2).status == "PENDING_DEAN"
