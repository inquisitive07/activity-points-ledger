from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
PW = "vtu@123"
EV = {"robo": 1, "rhythm": 2, "nss": 3, "hack": 4, "blood": 5}      # seeded event ids


def H(email):
    return {"Authorization": f"Bearer {c.post('/auth/login', json={'email': email, 'password': PW}).json()['access_token']}"}




def claim(h, ev, pts=10, typ="NEW_ACTIVITY", reason="I took part"):
    return c.post("/requests", headers=h, json={"event_id": ev, "request_type": typ, "claimed_points": pts, "reason": reason})


def earned(h):
    return c.get("/students/1VT22CS001", headers=h).json()["earned"]


def test_college_event_goes_straight_to_dean_club_event_goes_to_club():
    s = H("student@vtu.edu.in")
    assert claim(s, EV["blood"]).json()["status"] == "PENDING_DEAN"
    assert claim(s, EV["hack"], 15).json()["status"] == "PENDING_CLUB"


def test_claim_validation_and_duplicate_rules():
    s = H("student@vtu.edu.in")
    assert claim(s, EV["hack"], 0).status_code == 422
    assert claim(s, EV["hack"], 51).status_code == 422
    assert claim(s, EV["hack"], reason="  ").status_code == 422
    assert claim(s, 9999).status_code == 404
    assert claim(s, EV["robo"]).status_code == 409                           # already awarded
    assert claim(s, EV["hack"], typ="CORRECTION", pts=20).status_code == 409  # nothing to correct
    assert claim(s, EV["robo"], pts=10, typ="CORRECTION").status_code == 409  # same points
    assert claim(s, EV["hack"], 15).status_code == 201
    assert claim(s, EV["hack"], 15).status_code == 409                       # duplicate open request


def test_full_flow_club_verify_then_dean_approve_creates_ledger_row():
    club, dean, s = H("club@vtu.edu.in"), H("dean@vtu.edu.in"), H("student@vtu.edu.in")
    ev = c.post("/events", headers=club, json={"title": "Robo Workshop", "event_date": "2026-05-01", "venue": "Lab 2", "standard_points": 12}).json()
    assert ev["club"] == "Robotics Club"
    rid = claim(s, ev["id"], 12).json()["id"]
    assert rid in [r["id"] for r in c.get("/requests?status=PENDING_CLUB", headers=club).json()]
    before = earned(s)
    assert c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "APPROVE"}).status_code == 409   # club step can't be skipped
    assert c.post(f"/requests/{rid}/club-review", headers=club, json={"action": "VERIFY", "comment": "Attended"}).json()["status"] == "PENDING_DEAN"
    assert c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "APPROVE"}).json()["status"] == "APPROVED"
    assert earned(s) == before + 12
    assert c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "APPROVE"}).status_code == 409   # no double approval
    assert earned(s) == before + 12


def test_correction_supersedes_old_row_and_total_updates():
    club, dean, s = H("club@vtu.edu.in"), H("dean@vtu.edu.in"), H("student@vtu.edu.in")
    before = earned(s)
    rid = claim(s, EV["robo"], 15, "CORRECTION", "Awarded 10, standard is 15").json()["id"]
    c.post(f"/requests/{rid}/club-review", headers=club, json={"action": "VERIFY"})
    c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "APPROVE"})
    assert earned(s) == before + 5
    rows = [l for l in c.get("/students/1VT22CS001", headers=s).json()["ledger"] if l["event"] == "RoboWars 2026"]
    assert sorted(l["status"] for l in rows) == ["CORRECTION", "SUPERSEDED"]


def test_rejections_need_comments_and_award_nothing():
    club, dean, s = H("club@vtu.edu.in"), H("dean@vtu.edu.in"), H("student@vtu.edu.in")
    before = earned(s)
    rid = claim(s, EV["blood"]).json()["id"]
    assert c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "REJECT"}).status_code == 422
    assert c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "REJECT", "comment": "  "}).status_code == 422
    r = c.post(f"/requests/{rid}/dean-review", headers=dean, json={"action": "REJECT", "comment": "No proof"}).json()
    assert r["status"] == "REJECTED" and r["reviewer_comment"] == "No proof" and earned(s) == before
    assert claim(s, EV["blood"]).status_code == 201                          # may re-apply after rejection


def test_roles_and_club_isolation():
    club, dean, s = H("club@vtu.edu.in"), H("dean@vtu.edu.in"), H("student@vtu.edu.in")
    hod = H("hod@vtu.edu.in")
    rid = claim(s, EV["hack"], 15).json()["id"]                              # Coding Club event, club lead is Robotics
    assert rid not in [r["id"] for r in c.get("/requests", headers=club).json()]
    assert c.post(f"/requests/{rid}/club-review", headers=club, json={"action": "VERIFY"}).status_code == 404
    assert c.post(f"/requests/{rid}/club-review", headers=s, json={"action": "VERIFY"}).status_code == 403
    assert c.post(f"/requests/{rid}/dean-review", headers=club, json={"action": "APPROVE"}).status_code == 403
    assert c.get("/requests", headers=hod).status_code == 403
    assert c.post("/events", headers=dean, json={"title": "X Fest", "event_date": "2026-05-01", "venue": "Hall", "standard_points": 5}).status_code == 403
    assert c.post("/events", headers=s, json={"title": "X Fest", "event_date": "2026-05-01", "venue": "Hall", "standard_points": 5}).status_code == 403
    assert c.post("/events", headers=club, json={"title": "X", "event_date": "bad", "venue": "Hall", "standard_points": 5}).status_code == 422


def test_students_cannot_see_each_others_requests_and_matrix_counts_update():
    s, dean = H("student@vtu.edu.in"), H("dean@vtu.edu.in")
    claim(s, EV["hack"], 15)
    assert [r["usn"] for r in c.get("/requests", headers=s).json()] == ["1VT22CS001"]
    m = {x["club"]: x for x in c.get("/clubs/accountability", headers=dean).json()}
    assert len(m) == 4 and m["Coding Club"]["open_club_issues"] == 1 and m["Robotics Club"]["open_club_issues"] == 0
    assert c.get("/clubs/accountability", headers=s).status_code == 403
