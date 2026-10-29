"""Load demo data (same people/events as the frontend mock). Safe to re-run."""
import bcrypt
from datetime import date

from app.db import SessionLocal
from app.models import (Club, EntryType, Event, LedgerSource, LedgerStatus, PointLedger,
                        PointRequirement, Role, Student, User)

PASSWORD = "vtu@123"
CLUBS = ["Robotics Club", "Cultural Club", "NSS Unit", "Coding Club"]
EVENTS = [("RoboWars 2026", "Robotics Club", "2026-03-14", "Main Auditorium", 10), ("Rhythm Fest", "Cultural Club", "2026-02-20", "Open Air Theatre", 20),
          ("NSS Village Camp", "NSS Unit", "2026-04-05", "Hesaraghatta", 40), ("HackVTU 24h", "Coding Club", "2026-01-18", "CS Lab Block", 15),
          ("Blood Donation Drive", None, "2026-02-02", "Campus Clinic", 10)]
STUDENTS = [("1VT22CS001", "Aarav Sharma", "CS", "REGULAR"), ("1VT22CS014", "Diya Nair", "CS", "REGULAR"), ("1VT22CS027", "Rohan Gowda", "CS", "LATERAL"),
            ("1VT22CS033", "Neha Patil", "CS", "REGULAR"), ("1VT22EC008", "Ishita Rao", "EC", "REGULAR"), ("1VT22EC019", "Karthik Bhat", "EC", "LATERAL")]
# (usn, event title, points, status)
LEDGER = [("1VT22CS001", "RoboWars 2026", 10, "ACTIVE"), ("1VT22CS001", "Rhythm Fest", 15, "SUPERSEDED"), ("1VT22CS001", "Rhythm Fest", 20, "CORRECTION"),
          ("1VT22CS001", "NSS Village Camp", 40, "ACTIVE"), ("1VT22CS014", "NSS Village Camp", 45, "ACTIVE"), ("1VT22CS014", "Rhythm Fest", 20, "ACTIVE"),
          ("1VT22CS014", "HackVTU 24h", 15, "ACTIVE"), ("1VT22CS014", "RoboWars 2026", 10, "ACTIVE"), ("1VT22CS014", "Blood Donation Drive", 10, "ACTIVE")]


def run():
    h = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
    with SessionLocal() as db:
        if db.query(User).count():
            print("Already seeded."); return
        db.add_all([PointRequirement(entry_type=EntryType.REGULAR, points_required=100), PointRequirement(entry_type=EntryType.LATERAL, points_required=75)])
        clubs = {n: Club(name=n) for n in CLUBS}; db.add_all(clubs.values()); db.flush()
        people = [("student@vtu.edu.in", "Aarav Sharma", Role.STUDENT, None, None), ("club@vtu.edu.in", "Meera Iyer", Role.CLUB, None, clubs["Robotics Club"].id),
                  ("dean@vtu.edu.in", "Dr. R. Narayan", Role.DEAN, None, None), ("hod@vtu.edu.in", "Dr. S. Kulkarni", Role.HOD, "CS", None)]
        users = {e: User(email=e, password_hash=h, name=n, role=r, department_code=d, club_id=c) for e, n, r, d, c in people}
        db.add_all(users.values()); db.flush()
        events = {}
        for t, c, d, v, p in EVENTS:
            events[t] = Event(title=t, club_id=clubs[c].id if c else None, event_date=date.fromisoformat(d), venue=v, standard_points=p); db.add(events[t])
        studs = {}
        for usn, n, dept, et in STUDENTS:
            studs[usn] = Student(usn=usn, name=n, email=usn.lower() + "@vtu.edu.in", department_code=dept, entry_type=EntryType(et),
                                 user_id=users["student@vtu.edu.in"].id if usn == "1VT22CS001" else None); db.add(studs[usn])
        db.flush()
        for usn, t, pts, st in LEDGER:
            db.add(PointLedger(student_id=studs[usn].id, event_id=events[t].id, points=pts, status=LedgerStatus(st), source=LedgerSource.CLAIM))
        db.commit(); print("Seeded.")


if __name__ == "__main__":
    run()
