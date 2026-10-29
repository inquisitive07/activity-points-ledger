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
