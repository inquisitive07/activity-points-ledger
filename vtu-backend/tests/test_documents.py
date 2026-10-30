"""CP3: document upload, private storage, vaults, authorized retrieval, evidence links, archive and soft delete.

Runs against the seeded vtu_ledger_test database inside the `db` fixture's transaction (nothing persists), and
against the temporary storage directory that tests/conftest.py installs before the app is imported. Files written
here are removed with that directory at the end of the session. Seed facts come from scripts/seed.py.
"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import (AuditLog, Document, Event, PointLedger, PointLedgerDocument, PointRequest,
                        PointRequestDocument)
from app.storage import InvalidKey, LocalStorage, StorageError, check_key, get_storage, new_key
from scripts.seed import tiny_pdf

PW = "vtu@123"
DEAN, HOD_CS, HOD_EC = "dean@vtu.edu.in", "hod@vtu.edu.in", "hod.ece@vtu.edu.in"
AARAV, DIYA, ISHITA = "student@vtu.edu.in", "student2@vtu.edu.in", "student3@vtu.edu.in"
ROBOTICS, CODING, CULTURAL = "club@vtu.edu.in", "coding.lead@vtu.edu.in", "cultural.lead@vtu.edu.in"
AARAV_USN, DIYA_USN = "1RV22CS001", "1RV22IS014"

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + bytes(64) + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00" + bytes(32)
KEY_SHAPE = re.compile(r"uploads/\d{4}/\d{2}/[0-9a-f]{32}\.(pdf|jpg|png)")


def pdf(label="CP3 test document") -> bytes:
    return tiny_pdf([label])


# --- fixtures and helpers ------------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def temporary_storage_only():
    """Every test here must run against the throw-away storage folder, never the real volume."""
    root = Path(settings.storage_dir).resolve()
    assert root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith("vtu-test-storage-"), root
    assert Path(get_storage().root).resolve() == root
    yield


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


def storage_root() -> Path:
    return Path(settings.storage_dir)


def stored_files() -> set[Path]:
    return {p for p in storage_root().rglob("*") if p.is_file()}


def upload(client, h, data=None, mime="application/pdf", name="certificate.pdf", doc_type="CERTIFICATE", event=None):
    form = {"document_type": doc_type}
    if event is not None:
        form["event_id"] = str(event)
    return client.post("/documents", headers=h, data=form,
                       files={"file": (name, pdf() if data is None else data, mime)})


def uploaded(client, h, **kw) -> dict:
    r = upload(client, h, **kw)
    assert r.status_code == 201, r.text
    return r.json()


def event_id(db, name):
    return db.scalar(select(Event.event_id).where(Event.event_name == name))


def doc_id(db, file_name):
    return db.scalar(select(Document.document_id).where(Document.file_name == file_name))


def award_id(db, usn, event_name, status="ACTIVE"):
    return db.scalar(select(PointLedger.ledger_id).where(PointLedger.usn == usn, PointLedger.status == status,
                                                         PointLedger.event_id == event_id(db, event_name)))


def request_id(db, usn, event_name):
    return db.scalar(select(PointRequest.request_id).where(PointRequest.usn == usn,
                                                           PointRequest.event_id == event_id(db, event_name)))


def last_audit_id(db):
    return db.scalar(select(func.max(AuditLog.audit_id))) or 0


def audits(db, since, action=None):
    stmt = select(AuditLog).where(AuditLog.audit_id > since).order_by(AuditLog.audit_id)
    return db.scalars(stmt.where(AuditLog.action == action) if action else stmt).all()


def doc_row(db, document_id) -> Document:
    db.expire_all()
    return db.get(Document, document_id)


def vault(client, h, query=""):
    r = client.get(f"/documents{query}", headers=h)
    assert r.status_code == 200, r.text
    return {d["file_name"] for d in r.json()}


def ledger_state(db):
    """Every ledger row and every student's derived total: evidence must never change either."""
    db.expire_all()
    rows = db.execute(text("SELECT * FROM point_ledger ORDER BY ledger_id")).all()
    totals = db.execute(text("SELECT usn, earned_points FROM student_point_summary ORDER BY usn")).all()
    return [tuple(r) for r in rows], [tuple(t) for t in totals]


# --- upload: accepted formats, hashing, storage ------------------------------------------------------------------

@pytest.mark.parametrize("data, mime, name, ext", [
    (pdf("pdf upload"), "application/pdf", "Hackathon certificate.pdf", "pdf"),
    (JPEG, "image/jpeg", "stage photo.jpg", "jpg"),
    (PNG, "image/png", "badge.png", "png"),
])
def test_upload_accepts_pdf_jpeg_and_png(client, db, as_, data, mime, name, ext):
    before = stored_files()
    r = upload(client, as_(AARAV), data=data, mime=mime, name=name, doc_type="PHOTO" if ext != "pdf" else "CERTIFICATE")
    assert r.status_code == 201, r.text
    body = r.json()
    assert "storage_key" not in body and "storage_key" not in r.text
    assert (body["file_name"], body["mime_type"], body["file_size_bytes"], body["uploaded_by"],
            body["document_status"]) == (name, mime, len(data), AARAV, "ACTIVE")
    assert body["file_hash"] == hashlib.sha256(data).hexdigest()

    row = doc_row(db, body["document_id"])
    assert KEY_SHAPE.fullmatch(row.storage_key) and row.storage_key.endswith(f".{ext}")
    assert name not in row.storage_key                       # the user's filename never becomes a path
    [new_file] = stored_files() - before
    assert new_file == storage_root() / row.storage_key and new_file.read_bytes() == data
    assert hashlib.sha256(new_file.read_bytes()).hexdigest() == row.file_hash
    assert new_file.stat().st_mode & 0o077 == 0              # private to the API process


def test_upload_can_be_tagged_to_a_visible_event(client, db, as_):
    body = uploaded(client, as_(AARAV), event=event_id(db, "HackRVCE 24h"))
    assert (body["event_id"], body["event_name"]) == (event_id(db, "HackRVCE 24h"), "HackRVCE 24h")


@pytest.mark.parametrize("data, mime", [
    (b"just some text", "text/plain"),
    (b"GIF89a\x01\x00\x01\x00\x00\x00\x00;", "image/gif"),
    (b"MZ\x90\x00\x03\x00\x00\x00", "application/octet-stream"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
    (pdf(), "application/x-msdownload"),
])
def test_unsupported_types_are_refused(client, db, as_, data, mime):
    before, docs = stored_files(), db.scalar(select(func.count()).select_from(Document))
    r = upload(client, as_(AARAV), data=data, mime=mime, name="file.bin")
    assert r.status_code == 415
    assert stored_files() == before and db.scalar(select(func.count()).select_from(Document)) == docs


@pytest.mark.parametrize("data, declared", [
    (pdf(), "image/png"),                  # real PDF claiming to be a PNG
    (PNG, "application/pdf"),              # real PNG claiming to be a PDF
    (JPEG, "image/png"),
    (b"<html><script>alert(1)</script></html>", "application/pdf"),   # HTML disguised as a PDF
])
def test_spoofed_mime_types_are_refused(client, as_, data, declared):
    before = stored_files()
    assert upload(client, as_(AARAV), data=data, mime=declared, name="evil.pdf").status_code == 415
    assert stored_files() == before


@pytest.mark.parametrize("data, declared", [
    (b"%PD", "application/pdf"),                         # truncated signature
    (b"\x00" * 64, "application/pdf"),
    (b"\xff\xd8\x00garbage", "image/jpeg"),
    (b"\x89PNG\r\n\x00\x00broken", "image/png"),
    (b" %PDF-1.4 leading space", "application/pdf"),     # signature must be at offset 0
])
def test_invalid_file_signatures_are_refused(client, as_, data, declared):
    before = stored_files()
    assert upload(client, as_(AARAV), data=data, mime=declared).status_code == 415
    assert stored_files() == before


def test_empty_file_is_refused(client, as_):
    assert upload(client, as_(AARAV), data=b"").status_code == 422


def test_mime_parameters_and_case_are_tolerated(client, as_):
    assert upload(client, as_(AARAV), mime="Application/PDF; charset=binary").status_code == 201


# --- upload: size limit ----------------------------------------------------------------------------------------

def sized_pdf(size: int) -> bytes:
    head = pdf("size test")
    return head + b"%" * (size - len(head))


def test_file_of_exactly_the_limit_is_accepted(client, as_):
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    r = upload(client, as_(AARAV), data=sized_pdf(settings.max_upload_bytes))
    assert r.status_code == 201 and r.json()["file_size_bytes"] == settings.max_upload_bytes


def test_file_over_the_limit_is_refused(client, db, as_):
    before, docs = stored_files(), db.scalar(select(func.count()).select_from(Document))
    r = upload(client, as_(AARAV), data=sized_pdf(settings.max_upload_bytes + 1))
    assert r.status_code == 413
    assert stored_files() == before and db.scalar(select(func.count()).select_from(Document)) == docs


def test_oversized_bodies_are_refused_before_parsing(client, as_):
    # Declared too large: refused from the header, before the body is read.
    huge = {**as_(AARAV), "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(settings.max_upload_bytes * 2)}
    assert client.post("/documents", headers=huge, content=b"--x--").status_code == 413

    # Chunked (no Content-Length): refused while streaming.
    def chunks():
        for _ in range(12):
            yield b"a" * (1024 * 1024)
    r = client.post("/documents", headers={**as_(AARAV), "Content-Type": "multipart/form-data; boundary=x"},
                    content=chunks())
    assert r.status_code == 413


# --- upload: role rules ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("email, doc_type, event, status", [
    (AARAV, "CERTIFICATE", None, 201), (AARAV, "PHOTO", None, 201), (AARAV, "OTHER", None, 201),
    (AARAV, "SIGNED_POINT_LIST", None, 403), (AARAV, "ATTENDANCE_SHEET", None, 403), (AARAV, "REPORT", None, 403),
    (AARAV, "CERTIFICATE", "Robo Soccer Trials", 404),            # a DRAFT event is invisible to students
    (ROBOTICS, "ATTENDANCE_SHEET", "RoboWars 2026", 201), (ROBOTICS, "PHOTO", "Robo Soccer Trials", 201),
    (ROBOTICS, "REPORT", "RoboWars 2026", 201),
    (ROBOTICS, "REPORT", "HackRVCE 24h", 404),                    # another club's event
    (ROBOTICS, "REPORT", None, 422),                              # club documents need one of their events
    (ROBOTICS, "CERTIFICATE", "RoboWars 2026", 403), (ROBOTICS, "SIGNED_POINT_LIST", "RoboWars 2026", 403),
    (DEAN, "SIGNED_POINT_LIST", "HackRVCE 24h", 201), (DEAN, "OTHER", None, 201),
    (DEAN, "REPORT", "Robo Soccer Trials", 201),
    (HOD_CS, "CERTIFICATE", None, 403), (HOD_CS, "REPORT", "RoboWars 2026", 403),
])
def test_upload_role_rules(client, db, as_, email, doc_type, event, status):
    before = stored_files()
    r = upload(client, as_(email), doc_type=doc_type, event=event_id(db, event) if event else None)
    assert r.status_code == status, r.text
    if status != 201:
        assert stored_files() == before


def test_upload_validates_type_and_event(client, as_):
    assert upload(client, as_(DEAN), doc_type="PAYSLIP").status_code == 422
    assert upload(client, as_(DEAN), event=999999).status_code == 404
    assert client.post("/documents", headers=as_(AARAV), data={"document_type": "OTHER"}).status_code == 422
    assert client.post("/documents", files={"file": ("a.pdf", pdf(), "application/pdf")},
                       data={"document_type": "OTHER"}).status_code == 401


def test_uploader_and_status_cannot_be_supplied_by_the_client(client, as_):
    r = client.post("/documents", headers=as_(AARAV), files={"file": ("a.pdf", pdf(), "application/pdf")},
                    data={"document_type": "OTHER", "uploaded_by": DIYA, "document_status": "DELETED",
                          "storage_key": "../../escape.pdf"})
    assert r.status_code == 201
    assert (r.json()["uploaded_by"], r.json()["document_status"]) == (AARAV, "ACTIVE")


# --- storage-key safety and path traversal -----------------------------------------------------------------------

@pytest.mark.parametrize("key", ["../outside.pdf", "a/../../outside.pdf", "/etc/passwd", "uploads/../../x",
                                 "a\\..\\b", "", ".", "..", "a//b", "a/./b", "%2e%2e/x", "uploads/x\x00.pdf",
                                 "~/x", "a/b/", "C:/windows"])
def test_storage_rejects_unsafe_keys(tmp_path, key):
    store = LocalStorage(tmp_path / "root")
    with pytest.raises(InvalidKey):
        store.put(key, b"x")
    with pytest.raises(InvalidKey):
        store.open(key)
    assert not (tmp_path / "outside.pdf").exists()


def test_storage_refuses_symlinks_that_leave_the_root(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"secret")
    (root / "link").symlink_to(outside, target_is_directory=True)
    store = LocalStorage(root)
    with pytest.raises(InvalidKey):
        store.open("link/secret.pdf")
    with pytest.raises(InvalidKey):
        store.put("link/new.pdf", b"x")
    assert not (outside / "new.pdf").exists()


def test_storage_writes_atomically_and_never_overwrites(tmp_path):
    store = LocalStorage(tmp_path)
    key = new_key(".pdf")
    assert KEY_SHAPE.fullmatch(key) and key != new_key(".pdf") and check_key(key) == key
    store.put(key, b"first")
    with pytest.raises(StorageError):
        store.put(key, b"second")
    with store.open(key) as f:
        assert f.read() == b"first"
    assert [p.name for p in (tmp_path / key).parent.iterdir()] == [Path(key).name]   # no temp files left


@pytest.mark.parametrize("broken", ["link", "fsync"])
def test_failed_storage_write_leaves_nothing_behind(tmp_path, monkeypatch, broken):
    def fail(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(os, broken, fail)
    store, key = LocalStorage(tmp_path), new_key(".pdf")
    with pytest.raises(StorageError):
        store.put(key, b"data")
    monkeypatch.undo()
    assert not store.exists(key)
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []                     # no partial or temp file


@pytest.mark.parametrize("name, expected", [
    ("../../../etc/passwd.pdf", "passwd.pdf"),
    ("..\\..\\windows\\evil.pdf", "evil.pdf"),
    ("/abs/path/cert.pdf", "cert.pdf"),
    ("..", "document.pdf"),
    ("x" * 300 + ".pdf", "x" * 251 + ".pdf"),
])
def test_traversal_filenames_are_display_names_only(client, db, as_, name, expected):
    parent_before = set(storage_root().parent.iterdir())
    r = upload(client, as_(AARAV), name=name)
    assert r.status_code == 201, r.text
    assert r.json()["file_name"] == expected
    row = doc_row(db, r.json()["document_id"])
    assert KEY_SHAPE.fullmatch(row.storage_key)
    assert (storage_root() / row.storage_key).resolve().is_relative_to(storage_root().resolve())
    assert set(storage_root().parent.iterdir()) == parent_before        # nothing written next to the root


@pytest.mark.parametrize("raw, expected", [
    ("cert\r\nX-Injected: 1.pdf", "certX-Injected: 1.pdf"),       # header injection characters dropped
    ("a\x00b\x1b[31m.pdf", "ab[31m.pdf"),
    ("\u202egpj.exe.pdf", "gpj.exe.pdf"),                           # bidi override (format character) dropped
    ("  spaced name.pdf  ", "spaced name.pdf"),
    (None, "document.pdf"),
])
def test_file_name_sanitizer(raw, expected):
    from app.services.documents import clean_file_name
    assert clean_file_name(raw, ".pdf") == expected


# --- vaults (GET /documents) -------------------------------------------------------------------------------------

SEED_DOCS = {"RoboWars 2026 signed list.pdf", "Rhythm Fest certificate.pdf", "Internship letter.pdf",
             "HackRVCE certificate.pdf", "HackRVCE 24h signed list.pdf"}
AARAV_SEED = SEED_DOCS - {"HackRVCE 24h signed list.pdf"}


@pytest.fixture
def new_docs(client, db, as_):
    """One fresh upload per uploader kind."""
    uploaded(client, as_(AARAV), name="aarav-new.pdf", event=event_id(db, "HackRVCE 24h"))
    uploaded(client, as_(ISHITA), name="ishita-new.pdf")
    uploaded(client, as_(ROBOTICS), name="robotics-sheet.pdf", doc_type="ATTENDANCE_SHEET",
             event=event_id(db, "RoboWars 2026"))
    uploaded(client, as_(DEAN), name="dean-list.pdf", doc_type="SIGNED_POINT_LIST", event=event_id(db, "Rhythm Fest"))


@pytest.mark.parametrize("email, expected", [
    (DEAN, SEED_DOCS | {"aarav-new.pdf", "ishita-new.pdf", "robotics-sheet.pdf", "dean-list.pdf"}),
    (AARAV, AARAV_SEED | {"aarav-new.pdf"}),
    (DIYA, {"RoboWars 2026 signed list.pdf"}),
    (ISHITA, {"ishita-new.pdf"}),
    (HOD_CS, AARAV_SEED | {"aarav-new.pdf"}),
    (HOD_EC, {"ishita-new.pdf"}),
    (ROBOTICS, {"RoboWars 2026 signed list.pdf", "robotics-sheet.pdf"}),
    (CODING, {"HackRVCE certificate.pdf", "HackRVCE 24h signed list.pdf", "aarav-new.pdf"}),
    (CULTURAL, {"Rhythm Fest certificate.pdf", "dean-list.pdf"}),
    ("nss.lead@vtu.edu.in", set()),
])
def test_vault_for_every_role(client, as_, new_docs, email, expected):
    assert vault(client, as_(email)) == expected


def test_vault_filters(client, db, as_, new_docs):
    h = as_(AARAV)
    assert vault(client, h, "?document_type=CERTIFICATE") == {"Rhythm Fest certificate.pdf",
                                                               "HackRVCE certificate.pdf", "aarav-new.pdf"}
    assert vault(client, h, f"?event_id={event_id(db, 'HackRVCE 24h')}") == {"HackRVCE certificate.pdf",
                                                                            "aarav-new.pdf"}
    assert vault(client, h, "?document_type=SIGNED_POINT_LIST") == {"RoboWars 2026 signed list.pdf"}
    assert vault(client, h, f"?request_id={request_id(db, AARAV_USN, 'HackRVCE 24h')}") == {"HackRVCE certificate.pdf"}
    assert vault(client, h, f"?ledger_id={award_id(db, AARAV_USN, 'Rhythm Fest')}") == {"Rhythm Fest certificate.pdf"}
    # Filtering by someone else's award shows nothing, even for documents shared with it.
    assert vault(client, h, f"?ledger_id={award_id(db, DIYA_USN, 'RoboWars 2026')}") == set()
    assert vault(client, as_(DEAN), "?document_type=SIGNED_POINT_LIST") == {
        "RoboWars 2026 signed list.pdf", "HackRVCE 24h signed list.pdf", "dean-list.pdf"}
    assert client.get("/documents?document_type=NOPE", headers=h).status_code == 422
    assert client.get("/documents?status=GONE", headers=h).status_code == 422


def test_vault_status_filter_and_deleted_documents(client, db, as_):
    letter = doc_id(db, "Internship letter.pdf")
    assert client.post(f"/documents/{letter}/archive", headers=as_(AARAV)).status_code == 200
    assert vault(client, as_(AARAV), "?status=ARCHIVED") == {"Internship letter.pdf"}
    assert "Internship letter.pdf" in vault(client, as_(AARAV))                       # archived stays in the vault
    assert client.post(f"/documents/{letter}/delete", headers=as_(AARAV)).status_code == 200
    assert "Internship letter.pdf" not in vault(client, as_(AARAV)) | vault(client, as_(HOD_CS))
    assert vault(client, as_(AARAV), "?status=DELETED") == set()
    assert "Internship letter.pdf" not in vault(client, as_(DEAN))                    # hidden by default...
    assert vault(client, as_(DEAN), "?status=DELETED") == {"Internship letter.pdf"}   # ...but kept for the Dean


# --- metadata --------------------------------------------------------------------------------------------------

def test_metadata_is_scoped_including_its_links(client, db, as_):
    signed = doc_id(db, "RoboWars 2026 signed list.pdf")
    mine, hers = award_id(db, AARAV_USN, "RoboWars 2026"), award_id(db, DIYA_USN, "RoboWars 2026")
    r = client.get(f"/documents/{signed}", headers=as_(AARAV))
    assert r.status_code == 200 and "storage_key" not in r.text
    assert (r.json()["ledger_ids"], r.json()["request_ids"]) == ([mine], [])            # not Diya's award
    assert client.get(f"/documents/{signed}", headers=as_(DEAN)).json()["ledger_ids"] == sorted([mine, hers])
    cert = doc_id(db, "HackRVCE certificate.pdf")
    assert client.get(f"/documents/{cert}", headers=as_(CODING)).json()["request_ids"] == [
        request_id(db, AARAV_USN, "HackRVCE 24h")]


@pytest.mark.parametrize("email", [DIYA, ISHITA, HOD_EC, ROBOTICS, "nss.lead@vtu.edu.in"])
def test_out_of_scope_metadata_looks_like_a_missing_document(client, db, as_, email):
    letter = doc_id(db, "Internship letter.pdf")
    hidden = client.get(f"/documents/{letter}", headers=as_(email))
    missing = client.get("/documents/99999999", headers=as_(email))
    assert hidden.status_code == missing.status_code == 404 and hidden.json() == missing.json()


# --- download ----------------------------------------------------------------------------------------------------

def test_download_streams_the_file_with_safe_headers(client, db, as_):
    body = uploaded(client, as_(AARAV), data=PNG, mime="image/png", name="bad name;ü (1).png", doc_type="PHOTO")
    r = client.get(f"/documents/{body['document_id']}/content", headers=as_(AARAV))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "no-store" in r.headers["cache-control"]
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment; ") and "\r" not in cd and "\n" not in cd
    assert cd == "attachment; filename=\"bad name__ (1).png\"; filename*=UTF-8''bad%20name%3B%C3%BC%20%281%29.png"
    inline = client.get(f"/documents/{body['document_id']}/content?disposition=inline", headers=as_(AARAV))
    assert inline.headers["content-disposition"].startswith("inline; ")
    assert client.get(f"/documents/{body['document_id']}/content?disposition=evil",
                      headers=as_(AARAV)).status_code == 422


@pytest.mark.parametrize("name, fallback", [
    ('evil".pdf\r\nSet-Cookie: x=1', 'evil_.pdf__Set-Cookie_ x_1'),
    ("../../etc/passwd", "_.._etc_passwd"),           # no separators survive
    ("отчёт.pdf", "_____.pdf"),
    ("...", "document"),
])
def test_content_disposition_cannot_break_the_header(name, fallback):
    from app.routers.documents import content_disposition
    cd = content_disposition("attachment", name)
    assert cd.startswith(f'attachment; filename="{fallback}"; filename*=UTF-8\'\'')
    assert not set('\r\n"/\\') & set(cd.split("filename*=")[1])
    assert cd.count('"') == 2


@pytest.mark.parametrize("email, status", [
    (AARAV, 200), (DEAN, 200), (HOD_CS, 200), (CULTURAL, 200),        # owner, Dean, own-department HOD, event's club
    (DIYA, 404), (ISHITA, 404), (HOD_EC, 404), (ROBOTICS, 404), (CODING, 404),
])
def test_download_authorization(client, db, as_, email, status):
    cert = doc_id(db, "Rhythm Fest certificate.pdf")
    r = client.get(f"/documents/{cert}/content", headers=as_(email))
    assert r.status_code == status
    if status == 200:
        assert r.content.startswith(b"%PDF-") and r.headers["content-type"] == "application/pdf"


def test_another_students_document_id_never_exposes_it(client, db, as_):
    mine = uploaded(client, as_(AARAV), name="private.pdf", data=pdf("Aarav private"))
    for path in ("", "/content"):
        r = client.get(f"/documents/{mine['document_id']}{path}", headers=as_(DIYA))
        assert r.status_code == 404 and b"Aarav private" not in r.content and b"private.pdf" not in r.content
    for path in ("archive", "delete"):
        assert client.post(f"/documents/{mine['document_id']}/{path}", headers=as_(DIYA)).status_code == 404
    assert doc_row(db, mine["document_id"]).document_status == "ACTIVE"


def test_unauthenticated_access_is_refused(client, db):
    cert = doc_id(db, "Rhythm Fest certificate.pdf")
    for path in ("/documents", f"/documents/{cert}", f"/documents/{cert}/content"):
        assert client.get(path).status_code == 401


def test_guessed_storage_keys_do_not_give_access(client, db, as_):
    key = db.scalar(select(Document.storage_key).where(Document.file_name == "Internship letter.pdf"))
    h = as_(DIYA)
    for path in (f"/documents/{key}", f"/documents/{key}/content", f"/storage/{key}", f"/{key}",
                 f"/documents/content?key={key}", f"/documents/content?storage_key={key}",
                 f"/documents/{Path(key).name}/content", "/documents/..%2F..%2Fetc%2Fpasswd/content",
                 f"/documents/{doc_id(db, 'Internship letter.pdf')}/content?storage_key=../../{key}"):
        r = client.get(path, headers=h)
        assert r.status_code in (404, 405, 422), path
        assert not r.content.startswith(b"%PDF")
    # Not even the Dean can address a file by key: there is no such parameter anywhere in the API.
    assert "storage_key" not in json.dumps(client.get("/openapi.json").json())


def test_missing_stored_file_is_a_server_error_not_a_leak(client, db, as_):
    body = uploaded(client, as_(AARAV))
    (storage_root() / doc_row(db, body["document_id"]).storage_key).unlink()
    r = client.get(f"/documents/{body['document_id']}/content", headers=as_(AARAV))
    assert r.status_code == 500 and r.json() == {"detail": "The stored file is unavailable"}


# --- evidence for requests ---------------------------------------------------------------------------------------

def test_student_attaches_own_document_to_own_pending_request(client, db, as_):
    req = request_id(db, AARAV_USN, "HackRVCE 24h")                              # PENDING_CLUB
    existing = doc_id(db, "HackRVCE certificate.pdf")
    body = uploaded(client, as_(AARAV), name="team photo.pdf", event=event_id(db, "HackRVCE 24h"))
    start = last_audit_id(db)
    r = client.post(f"/requests/{req}/documents", headers=as_(AARAV), json={"document_id": body["document_id"]})
    assert r.status_code == 201 and r.json()["request_id"] == req and r.json()["document_id"] == body["document_id"]
    [entry] = audits(db, start, "REQUEST_DOCUMENT_ATTACHED")
    assert (entry.entity_type, entry.entity_id, entry.user_email) == ("point_request", req, AARAV)
    assert entry.old_values == {"document_ids": [existing]}
    assert entry.new_values["document_ids"] == sorted([existing, body["document_id"]])
    assert entry.new_values["file_name"] == "team photo.pdf"
    # The club reviewing the request can now see the evidence; the status of the request is untouched.
    assert "team photo.pdf" in vault(client, as_(CODING), f"?request_id={req}")
    assert client.get(f"/documents/{body['document_id']}/content", headers=as_(CODING)).status_code == 200
    assert db.get(PointRequest, req).status == "PENDING_CLUB"
    again = client.post(f"/requests/{req}/documents", headers=as_(AARAV), json={"document_id": body["document_id"]})
    assert again.status_code == 409


def test_request_attachment_rules(client, db, as_):
    h = as_(AARAV)
    mine = uploaded(client, h, name="mine.pdf")["document_id"]
    tagged_elsewhere = uploaded(client, h, name="nss.pdf", event=event_id(db, "NSS Village Camp"))["document_id"]
    archived = uploaded(client, h, name="old.pdf")["document_id"]
    client.post(f"/documents/{archived}/archive", headers=h)
    diyas = uploaded(client, as_(DIYA), name="diya.pdf")["document_id"]
    pending = request_id(db, AARAV_USN, "HackRVCE 24h")
    approved = request_id(db, AARAV_USN, "NSS Village Camp")                       # APPROVED: closed
    ishitas = request_id(db, "1RV22EC008", "RoboWars 2026")                        # someone else's request
    signed = doc_id(db, "RoboWars 2026 signed list.pdf")                           # visible to Aarav, not his

    def attach(req, doc, who=AARAV):
        return client.post(f"/requests/{req}/documents", headers=as_(who), json={"document_id": doc}).status_code

    assert attach(approved, mine) == 409
    assert attach(ishitas, mine) == 404
    assert attach(999999, mine) == 404
    assert attach(pending, diyas) == 404                                           # invisible document
    assert attach(pending, signed) == 403                                          # visible, but not his upload
    assert attach(pending, archived) == 409
    assert attach(pending, tagged_elsewhere) == 422
    for other in (CODING, DEAN, HOD_CS):
        assert attach(pending, mine, other) == 403
    assert client.post(f"/requests/{pending}/documents", headers=h, json={"document_id": mine,
                                                                          "request_id": 1}).status_code == 422
    assert attach(pending, mine) == 201


def test_withdrawn_request_takes_no_more_evidence(client, db, as_):
    h = as_(AARAV)
    req = request_id(db, AARAV_USN, "HackRVCE 24h")
    assert client.post(f"/requests/{req}/withdraw", headers=h).status_code == 200
    mine = uploaded(client, h)["document_id"]
    assert client.post(f"/requests/{req}/documents", headers=h, json={"document_id": mine}).status_code == 409


# --- evidence for awards -----------------------------------------------------------------------------------------

def test_student_attaches_evidence_to_own_award_without_changing_points(client, db, as_):
    award = award_id(db, AARAV_USN, "RoboWars 2026")
    signed = doc_id(db, "RoboWars 2026 signed list.pdf")
    before_ledger, before_requests = ledger_state(db), db.scalar(select(func.count()).select_from(PointRequest))
    body = uploaded(client, as_(AARAV), name="robowars certificate.pdf", event=event_id(db, "RoboWars 2026"))
    start = last_audit_id(db)
    r = client.post(f"/ledger/{award}/documents", headers=as_(AARAV), json={"document_id": body["document_id"]})
    assert r.status_code == 201 and (r.json()["ledger_id"], r.json()["linked_by"]) == (award, AARAV)
    assert ledger_state(db) == before_ledger                                     # no award created or changed
    assert db.scalar(select(func.count()).select_from(PointRequest)) == before_requests
    [entry] = audits(db, start, "LEDGER_DOCUMENT_LINKED")
    assert (entry.entity_type, entry.entity_id) == ("point_ledger", award)
    assert entry.old_values == {"document_ids": [signed]}
    assert entry.new_values["document_ids"] == sorted([signed, body["document_id"]])
    assert [a.action for a in audits(db, start)] == ["LEDGER_DOCUMENT_LINKED"]  # no ledger audit entries either
    assert client.get(f"/documents/{body['document_id']}", headers=as_(AARAV)).json()["ledger_ids"] == [award]
    # Evidence for an award is visible to the event's club and to the student's HOD.
    assert client.get(f"/documents/{body['document_id']}/content", headers=as_(ROBOTICS)).status_code == 200
    assert client.get(f"/documents/{body['document_id']}/content", headers=as_(HOD_CS)).status_code == 200
    assert client.post(f"/ledger/{award}/documents", headers=as_(AARAV),
                       json={"document_id": body["document_id"]}).status_code == 409


def test_evidence_only_upload_leaves_the_ledger_unchanged(client, db, as_):
    before = ledger_state(db)
    award = award_id(db, AARAV_USN, "NSS Village Camp")
    body = uploaded(client, as_(AARAV), name="nss certificate.pdf", event=event_id(db, "NSS Village Camp"))
    assert client.post(f"/ledger/{award}/documents", headers=as_(AARAV),
                       json={"document_id": body["document_id"]}).status_code == 201
    dean_doc = uploaded(client, as_(DEAN), name="nss signed list.pdf", doc_type="SIGNED_POINT_LIST",
                        event=event_id(db, "NSS Village Camp"))
    assert client.post(f"/ledger/{award}/documents", headers=as_(DEAN),
                       json={"document_id": dean_doc["document_id"]}).status_code == 201
    assert ledger_state(db) == before


def test_ledger_attachment_rules(client, db, as_):
    h = as_(AARAV)
    mine = uploaded(client, h, name="mine.pdf")["document_id"]
    diyas = uploaded(client, as_(DIYA), name="diya.pdf")["document_id"]
    rhythm_tagged = uploaded(client, h, name="rhythm.pdf", event=event_id(db, "Rhythm Fest"))["document_id"]
    own_award = award_id(db, AARAV_USN, "RoboWars 2026")
    superseded = award_id(db, AARAV_USN, "Rhythm Fest", status="SUPERSEDED")
    diyas_award = award_id(db, DIYA_USN, "RoboWars 2026")

    def link(award, doc, who=AARAV):
        return client.post(f"/ledger/{award}/documents", headers=as_(who), json={"document_id": doc}).status_code

    before = ledger_state(db)
    assert link(diyas_award, mine) == 404                    # someone else's award
    assert link(999999, mine) == 404
    assert link(superseded, mine) == 409                     # history is not re-evidenced
    assert link(own_award, diyas) == 404
    assert link(own_award, rhythm_tagged) == 422             # tagged to another event
    assert link(own_award, doc_id(db, "HackRVCE 24h signed list.pdf")) == 404
    for other in (ROBOTICS, HOD_CS, CODING):
        assert link(own_award, mine, other) == 403
    assert link(diyas_award, mine, DEAN) == 422              # Dean cannot expose Aarav's upload to Diya
    assert link(diyas_award, diyas, DEAN) == 201             # but can link Diya's own document to her award
    assert ledger_state(db) == before


# --- archive and soft delete -------------------------------------------------------------------------------------

def test_archive_and_delete_transitions(client, db, as_):
    h = as_(AARAV)
    doc = uploaded(client, h)["document_id"]
    key = doc_row(db, doc).storage_key
    start = last_audit_id(db)
    r = client.post(f"/documents/{doc}/archive", headers=h)
    assert r.status_code == 200 and r.json()["document_status"] == "ARCHIVED"
    assert client.post(f"/documents/{doc}/archive", headers=h).status_code == 409          # ARCHIVED -> ARCHIVED
    r = client.post(f"/documents/{doc}/delete", headers=h)
    assert r.status_code == 200 and r.json()["document_status"] == "DELETED"                # ARCHIVED -> DELETED
    assert client.post(f"/documents/{doc}/delete", headers=h).status_code == 404            # gone from his vault
    assert client.post(f"/documents/{doc}/delete", headers=as_(DEAN)).status_code == 409
    assert client.post(f"/documents/{doc}/archive", headers=as_(DEAN)).status_code == 409
    assert (storage_root() / key).is_file()                                                 # file is never removed
    assert client.get(f"/documents/{doc}/content", headers=h).status_code == 404
    assert client.get(f"/documents/{doc}/content", headers=as_(DEAN)).status_code == 200

    archived, deleted = audits(db, start, "DOCUMENT_ARCHIVED"), audits(db, start, "DOCUMENT_DELETED")
    assert [(a.old_values, a.new_values, a.entity_id, a.user_email) for a in archived] == [
        ({"document_status": "ACTIVE"}, {"document_status": "ARCHIVED"}, doc, AARAV)]
    assert [(a.old_values, a.new_values) for a in deleted] == [
        ({"document_status": "ARCHIVED"}, {"document_status": "DELETED"})]

    direct = uploaded(client, h)["document_id"]
    assert client.post(f"/documents/{direct}/delete", headers=h).json()["document_status"] == "DELETED"  # ACTIVE ->


@pytest.mark.parametrize("email, document, status", [
    (AARAV, "Internship letter.pdf", 200),                   # uploader
    (DEAN, "Internship letter.pdf", 200),                    # Dean
    (HOD_CS, "Internship letter.pdf", 403),                  # read-only, even inside the department
    (DIYA, "Internship letter.pdf", 404),                    # not in her vault
    (AARAV, "RoboWars 2026 signed list.pdf", 403),           # in his vault, but not his upload
    (ROBOTICS, "RoboWars 2026 signed list.pdf", 403),
    (CODING, "HackRVCE certificate.pdf", 403),               # club sees request evidence, cannot manage it
])
def test_archive_permissions(client, db, as_, email, document, status):
    doc = doc_id(db, document)
    assert client.post(f"/documents/{doc}/archive", headers=as_(email)).status_code == status
    assert doc_row(db, doc).document_status == ("ARCHIVED" if status == 200 else "ACTIVE")


def test_club_lead_manages_own_uploads(client, db, as_):
    doc = uploaded(client, as_(ROBOTICS), doc_type="REPORT", event=event_id(db, "RoboWars 2026"))["document_id"]
    assert client.post(f"/documents/{doc}/archive", headers=as_(CODING)).status_code == 404
    assert client.post(f"/documents/{doc}/delete", headers=as_(HOD_CS)).status_code == 403
    assert client.post(f"/documents/{doc}/delete", headers=as_(ROBOTICS)).status_code == 200


@pytest.mark.parametrize("document, who", [
    ("Rhythm Fest certificate.pdf", AARAV),           # linked to an ACTIVE award
    ("Rhythm Fest certificate.pdf", DEAN),
    ("RoboWars 2026 signed list.pdf", DEAN),          # linked to awards and a committed OCR batch
    ("HackRVCE certificate.pdf", AARAV),              # evidence for a pending request
    ("HackRVCE 24h signed list.pdf", DEAN),           # OCR batch under review
])
def test_award_linked_and_in_use_documents_cannot_be_deleted(client, db, as_, document, who):
    doc = doc_id(db, document)
    r = client.post(f"/documents/{doc}/delete", headers=as_(who))
    assert r.status_code == 409 and "cannot be deleted" in r.json()["detail"]
    assert doc_row(db, doc).document_status == "ACTIVE"


def test_award_linked_documents_can_be_archived_but_not_deleted(client, db, as_):
    cert = doc_id(db, "Rhythm Fest certificate.pdf")
    assert client.post(f"/documents/{cert}/archive", headers=as_(AARAV)).status_code == 200
    assert client.post(f"/documents/{cert}/delete", headers=as_(DEAN)).status_code == 409
    assert doc_row(db, cert).document_status == "ARCHIVED"


def test_evidence_of_a_rejected_request_can_be_deleted(client, db, as_):
    h = as_(AARAV)
    workshop = event_id(db, "Cloud Computing Workshop")
    req = client.post("/requests", headers=h, json={"event_id": workshop, "request_type": "NEW_ACTIVITY",
                                                    "claimed_points": 5, "reason": "Attended the workshop."})
    assert req.status_code == 201
    doc = uploaded(client, h, event=workshop)["document_id"]
    assert client.post(f"/requests/{req.json()['request_id']}/documents", headers=h,
                       json={"document_id": doc}).status_code == 201
    assert client.post(f"/documents/{doc}/delete", headers=h).status_code == 409             # under review
    assert client.post(f"/requests/{req.json()['request_id']}/club-review", headers=as_(CODING),
                       json={"action": "REJECT", "comment": "Not on the attendance sheet."}).status_code == 200
    assert client.post(f"/documents/{doc}/delete", headers=h).status_code == 200


# --- failure handling: storage and database never disagree --------------------------------------------------------

def test_database_failure_removes_the_stored_file(client, db, as_, monkeypatch):
    import app.services.documents as svc
    h = as_(AARAV)                                         # log in before the failure is installed
    data = pdf("db failure")
    files_before, start = stored_files(), last_audit_id(db)

    def broken_audit(*a, **kw):
        raise RuntimeError("database went away")
    monkeypatch.setattr(svc, "audit", broken_audit)
    with pytest.raises(RuntimeError):
        upload(client, h, data=data)
    assert stored_files() == files_before                  # the file written before the insert is gone
    db.expire_all()
    assert db.scalar(select(Document).where(Document.file_hash == hashlib.sha256(data).hexdigest())) is None
    assert audits(db, start, "DOCUMENT_UPLOADED") == []


def test_constraint_failure_removes_the_stored_file(client, db, as_, monkeypatch):
    import app.services.documents as svc
    h = as_(AARAV)
    files_before = stored_files()

    def conflicting_audit(*a, **kw):
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))
    monkeypatch.setattr(svc, "audit", conflicting_audit)
    r = upload(client, h, data=pdf("conflict"))
    assert r.status_code == 409
    assert stored_files() == files_before
    assert db.scalar(select(Document).where(Document.file_name == "certificate.pdf",
                                            Document.uploaded_by == AARAV)) is None


class FailingStorage(LocalStorage):
    def put(self, key, data):
        raise StorageError("disk full")


def test_storage_failure_writes_no_metadata(client, db, as_):
    from app.main import app
    h = as_(AARAV)
    docs, start, files = db.scalar(select(func.count()).select_from(Document)), last_audit_id(db), stored_files()
    app.dependency_overrides[get_storage] = lambda: FailingStorage(settings.storage_dir)
    try:
        r = upload(client, h)
    finally:
        app.dependency_overrides.pop(get_storage, None)
    assert r.status_code == 503 and "disk full" not in r.text and "Nothing was saved" in r.json()["detail"]
    assert db.scalar(select(func.count()).select_from(Document)) == docs
    assert audits(db, start) == [] and stored_files() == files


def test_real_write_failure_leaves_no_file_and_no_metadata(client, db, as_, monkeypatch):
    h = as_(AARAV)
    docs, files = db.scalar(select(func.count()).select_from(Document)), stored_files()

    def fail(*a, **kw):
        raise OSError("read-only file system")
    monkeypatch.setattr(os, "link", fail)
    r = upload(client, h)
    monkeypatch.undo()
    assert r.status_code == 503
    assert db.scalar(select(func.count()).select_from(Document)) == docs and stored_files() == files


# --- audit content -----------------------------------------------------------------------------------------------

def test_upload_audit_has_metadata_but_no_contents_or_storage_key(client, db, as_):
    data = pdf("SECRET-CONTENT-MARKER")
    start = last_audit_id(db)
    body = uploaded(client, as_(AARAV), data=data, name="audited.pdf", event=event_id(db, "HackRVCE 24h"))
    [entry] = audits(db, start, "DOCUMENT_UPLOADED")
    assert (entry.entity_type, entry.entity_id, entry.user_email, entry.old_values) == (
        "document", body["document_id"], AARAV, None)
    assert entry.new_values == {
        "document_id": body["document_id"], "event_id": event_id(db, "HackRVCE 24h"), "document_type": "CERTIFICATE",
        "file_name": "audited.pdf", "mime_type": "application/pdf", "file_size_bytes": len(data),
        "file_hash": hashlib.sha256(data).hexdigest(), "uploaded_by": AARAV,
        "uploaded_at": entry.new_values["uploaded_at"], "document_status": "ACTIVE"}
    dumped = json.dumps(entry.new_values)
    assert "SECRET-CONTENT-MARKER" not in dumped and "%PDF" not in dumped
    assert doc_row(db, body["document_id"]).storage_key not in dumped


def test_no_audit_entry_ever_contains_a_storage_key_or_secret(client, db, as_):
    h = as_(AARAV)
    doc = uploaded(client, h)["document_id"]
    client.post(f"/requests/{request_id(db, AARAV_USN, 'HackRVCE 24h')}/documents", headers=h,
                json={"document_id": doc})
    client.post(f"/ledger/{award_id(db, AARAV_USN, 'RoboWars 2026')}/documents", headers=h,
                json={"document_id": doc})
    other = uploaded(client, h)["document_id"]
    client.post(f"/documents/{other}/archive", headers=h)
    client.post(f"/documents/{other}/delete", headers=h)
    keys = db.scalars(select(Document.storage_key)).all()
    entries = db.execute(select(AuditLog.old_values, AuditLog.new_values)).all()
    blob = "\n".join(json.dumps(v) for row in entries for v in row if v)
    assert keys and not any(key in blob for key in keys)
    assert "storage_key" not in blob and "password_hash" not in blob and "$2b$" not in blob
    assert "Bearer" not in blob and "access_token" not in blob
    actions = {a.action for a in db.scalars(select(AuditLog))}
    assert {"DOCUMENT_UPLOADED", "REQUEST_DOCUMENT_ATTACHED", "LEDGER_DOCUMENT_LINKED", "DOCUMENT_ARCHIVED",
            "DOCUMENT_DELETED"} <= actions


# --- HOD is read-only --------------------------------------------------------------------------------------------

def test_hod_reads_department_documents_but_writes_nothing(client, db, as_):
    h = as_(HOD_CS)
    cert = doc_id(db, "Rhythm Fest certificate.pdf")
    assert client.get(f"/documents/{cert}", headers=h).status_code == 200
    assert client.get(f"/documents/{cert}/content", headers=h).status_code == 200
    assert upload(client, h).status_code == 403
    assert client.post(f"/documents/{cert}/archive", headers=h).status_code == 403
    assert client.post(f"/documents/{cert}/delete", headers=h).status_code == 403
    assert client.post(f"/requests/{request_id(db, AARAV_USN, 'HackRVCE 24h')}/documents", headers=h,
                       json={"document_id": cert}).status_code == 403
    assert client.post(f"/ledger/{award_id(db, AARAV_USN, 'Rhythm Fest')}/documents", headers=h,
                       json={"document_id": cert}).status_code == 403
    assert doc_row(db, cert).document_status == "ACTIVE"
