"""One-off correction of the demo data to RVCE USNs, department codes and event naming.

Run inside the api container (it needs the private storage volume):

    docker compose run --rm --no-deps api python scripts/rvce_correction.py --database <name>             # dry run
    docker compose run --rm --no-deps api python scripts/rvce_correction.py --database <name> --execute   # commit

What changes (approved by the project owner)
--------------------------------------------
* 7 student USNs (STUDENT_MAP) and their departments; departments CSE/ISE/ECE are replaced by IS, CS, CY, CD, AI,
  EC, EI. ME and CV are kept. HOD accounts move CSE -> CS and ECE -> EC.
* "HackVTU" -> "HackRVCE" in the demo text (event name/description, request reasons, OCR metadata, file names).
* The five seeded demo PDFs (their text shows USNs and the event name) are regenerated from scripts/seed.py under
  new storage keys (seed/rvce/...), and the OCR text read from them follows.
Unchanged: login emails, student contact emails, and every other value of every row.

How references are preserved
----------------------------
All FKs to student.usn / department.department_code are NO ACTION and not deferrable, so a primary key cannot be
updated in place while children point at it. Inside ONE transaction the script copies each student under the new USN,
repoints every child column, then deletes the old rows; a reference left behind makes that DELETE fail and the whole
transaction roll back. Before COMMIT the result is compared with a snapshot taken at the start: everything must be
identical except for exactly the approved replacements.

audit_log is append-only (trigger): its earlier rows keep the old values; one appended row records the mapping.

Files and crashes
-----------------
New PDFs never overwrite anything: they go to new keys, written to a temp name, fsynced, then hard-linked to the final
name (which fails if the name exists). They are in their final place and durable BEFORE COMMIT. Old files are deleted
only AFTER a successful COMMIT.
* Error before COMMIT: rollback; the new files placed by this run are removed; old files were never touched.
* Process crash before COMMIT: PostgreSQL discards the uncommitted transaction, so the database still references the
  old files, which still exist. New files may remain as orphans; a re-run reuses one if its bytes are exactly the
  expected bytes, removes stale temp files, and aborts if a file at a new key has unexpected content.
* Error or crash during/after COMMIT: new files are never removed once COMMIT has been attempted (an orphan file is
  preferable to a database reference to a missing file). If old files are left behind, re-run with
  --cleanup-old-files, which deletes them only if the database is corrected and no document references them.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings
from scripts.seed import DEPARTMENTS, DOCUMENT_LINES, tiny_pdf

ALLOWED_DATABASES = {"vtu_ledger", "vtu_ledger_rvce_rehearsal"}
EXPECTED_REVISION = "v2_0001"

# old USN -> (new USN, new department code)
STUDENT_MAP = {
    "1VT22CS001": ("1RV22CS001", "CS"),   # Aarav Sharma
    "1VT22CS014": ("1RV22IS014", "IS"),   # Diya Nair
    "1VT22CS027": ("1RV23CY402", "CY"),   # Rohan Gowda (lateral, joined 2023)
    "1VT22CS033": ("1RV22CD033", "CD"),   # Neha Patil
    "1VT22EC008": ("1RV22EC008", "EC"),   # Ishita Rao
    "1VT22EC019": ("1RV23EI419", "EI"),   # Karthik Bhat (lateral, joined 2023)
    "1VT19CS050": ("1RV19AI050", "AI"),   # Ananya Hegde (graduated)
}
# Text that is not a student USN: an unknown USN and an OCR misread (letter O) on the signed lists; the event name.
OTHER_TEXT = {"1VT22CS099": "1RV22CS099", "1VT22CS0O1": "1RV22CS0O1", "HackVTU": "HackRVCE"}
TEXT_MAP = {**{old: new for old, (new, _) in STUDENT_MAP.items()}, **OTHER_TEXT}
PRE_DEPARTMENTS = {"CSE", "ISE", "ECE", "ME", "CV"}
REMOVED_DEPARTMENTS = {"CSE", "ISE", "ECE"}
HOD_DEPARTMENT_MAP = {"CSE": "CS", "ECE": "EC"}
DOCUMENT_KEY_MAP = {
    "seed/robowars-2026-signed-list.pdf": "seed/rvce/robowars-2026-signed-list.pdf",
    "seed/1VT22CS001-rhythm-fest-certificate.pdf": "seed/rvce/1RV22CS001-rhythm-fest-certificate.pdf",
    "seed/1VT22CS001-archive-internship-letter.pdf": "seed/rvce/1RV22CS001-archive-internship-letter.pdf",
    "seed/1VT22CS001-hackvtu-certificate.pdf": "seed/rvce/1RV22CS001-hackrvce-certificate.pdf",
    "seed/hackvtu-24h-signed-list.pdf": "seed/rvce/hackrvce-24h-signed-list.pdf",
}
USN_PATTERN = re.compile(r"1RV(\d{2})(IS|CS|CY|CD|AI|EC|EI|ME|CV)(\d{3})")
TMP_SUFFIX = ".rvce-tmp"


class Abort(Exception):
    pass


def replace_text(value):
    """Apply the approved text replacements (recursing into tuples/lists; other types are returned unchanged)."""
    if isinstance(value, str):
        for old, new in TEXT_MAP.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, (tuple, list)):
        return type(value)(replace_text(v) for v in value)
    return value


def rows(conn, sql, **params):
    return [tuple(r) for r in conn.execute(text(sql), params)]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def new_documents() -> dict[str, bytes]:
    return {key: tiny_pdf(DOCUMENT_LINES[key]) for key in DOCUMENT_KEY_MAP.values()}


# --- snapshot / checks ----------------------------------------------------------------------------------------------

def snapshot(conn) -> dict:
    """Everything that must survive, with students identified by NAME (names are unique in the demo data)."""
    who = "(SELECT name FROM student s WHERE s.usn = {col})"
    return {
        "counts": {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in (
            "student", "user_account", "club", "event", "document", "ocr_batch", "ocr_record", "point_request",
            "point_request_document", "point_ledger", "point_ledger_document", "point_requirement", "audit_log")},
        "students": rows(conn, "SELECT name, email, entry_type, admission_year, current_semester, student_status "
                               "FROM student ORDER BY name"),
        "summary": rows(conn, "SELECT s.name, v.required_points, v.earned_points, v.remaining_points, "
                              "v.target_reached FROM student_point_summary v JOIN student s USING (usn) ORDER BY 1"),
        "accounts": rows(conn, f"SELECT email, password_hash, role, account_status, "
                               f"{who.format(col='t.student_usn')}, club_id, t.department_code IS NOT NULL, created_at, "
                               "last_login_at FROM user_account t ORDER BY email"),
        "kept_departments": rows(conn, "SELECT * FROM department WHERE department_code IN ('ME', 'CV') ORDER BY 1"),
        "requirements": rows(conn, "SELECT * FROM point_requirement ORDER BY 1"),
        "clubs": rows(conn, "SELECT * FROM club ORDER BY 1"),
        "events": rows(conn, "SELECT * FROM event ORDER BY 1"),
        "requests": rows(conn, f"SELECT request_id, {who.format(col='t.usn')}, event_id, request_type, claimed_points, "
                               "reason, submitted_by, correction_of_ledger_id, status, club_reviewed_by, "
                               "club_reviewed_at, club_review_comment, dean_decided_by, dean_decided_at, "
                               "dean_decision_comment, created_at, updated_at FROM point_request t ORDER BY 1"),
        "ledger": rows(conn, f"SELECT ledger_id, {who.format(col='t.usn')}, event_id, points_awarded, award_date, "
                             "source_type, source_request_id, source_ocr_batch_id, approved_by, approved_at, status, "
                             "supersedes_ledger_id FROM point_ledger t ORDER BY 1"),
        "ledger_docs": rows(conn, "SELECT * FROM point_ledger_document ORDER BY 1, 2"),
        "request_docs": rows(conn, "SELECT * FROM point_request_document ORDER BY 1, 2"),
        "ocr_batches": rows(conn, "SELECT ocr_batch_id, document_id, started_by, status, started_at, completed_at, "
                                  "pages_processed, records_detected, valid_records, exception_count, "
                                  "extracted_metadata::text, error_message FROM ocr_batch ORDER BY 1"),
        "ocr": rows(conn, f"SELECT ocr_record_id, ocr_batch_id, page_number, row_number, extracted_usn, "
                          f"{who.format(col='t.matched_student_usn')}, extracted_name, extracted_points, "
                          "confidence_score, raw_extraction::text, validation_status, validation_message "
                          "FROM ocr_record t ORDER BY 1"),
        "documents": rows(conn, "SELECT document_id, event_id, document_type, file_name, mime_type, uploaded_by, "
                                "uploaded_at, document_status FROM document ORDER BY 1"),
        "audit": rows(conn, "SELECT audit_id, user_email, action, entity_type, entity_id, old_values::text, "
                            "new_values::text, ip_address, created_at FROM audit_log ORDER BY 1"),
    }


def precheck(conn, storage: Path) -> dict[str, bool]:
    """Refuse anything but the exact pre-correction state. Returns {new key: reuse an identical existing file}."""
    db = conn.execute(text("SELECT current_database()")).scalar()
    if db not in ALLOWED_DATABASES:
        raise Abort(f"connected to {db!r}, which is not an allowed target")
    rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if rev != EXPECTED_REVISION:
        raise Abort(f"alembic revision is {rev!r}, expected {EXPECTED_REVISION!r}")
    usns = {r[0] for r in rows(conn, "SELECT usn FROM student")}
    if usns != set(STUDENT_MAP):
        raise Abort(f"students are not the expected pre-correction set (already corrected?): {sorted(usns)}")
    depts = {r[0] for r in rows(conn, "SELECT department_code FROM department")}
    if depts != PRE_DEPARTMENTS:
        raise Abort(f"departments are not the expected pre-correction set: {sorted(depts)}")
    hod = {r[0] for r in rows(conn, "SELECT department_code FROM user_account WHERE role = 'HOD'")}
    if not hod <= set(HOD_DEPARTMENT_MAP):
        raise Abort(f"HOD departments without a mapping: {sorted(hod - set(HOD_DEPARTMENT_MAP))}")
    docs = dict(rows(conn, "SELECT storage_key, file_hash FROM document"))
    if set(docs) != set(DOCUMENT_KEY_MAP):
        raise Abort(f"documents are not the expected seeded set: {sorted(docs)}")
    for key, digest in docs.items():
        path = storage / key
        if not path.is_file() or sha256(path.read_bytes()) != digest.strip():
            raise Abort(f"stored file for {key!r} is missing or does not match its recorded hash")
    reuse = {}
    for key, data in new_documents().items():
        path = storage / key
        Path(str(path) + TMP_SUFFIX).unlink(missing_ok=True)        # stale temp file from a crashed run
        if path.exists():
            if not path.is_file() or sha256(path.read_bytes()) != sha256(data):
                raise Abort(f"{key!r} already exists with unexpected content; inspect it manually")
            reuse[key] = True                                       # identical orphan from a crashed run
        else:
            reuse[key] = False
    return reuse


def postcheck(conn, before: dict):
    after = snapshot(conn)
    for key, old in before.items():
        new = after[key]
        if key == "counts":
            expected = dict(old, audit_log=old["audit_log"] + 1)
        elif key == "audit":
            expected, new = old, new[:len(old)]          # earlier rows untouched; exactly one row appended
        else:
            expected = [replace_text(r) for r in old]    # identical apart from the approved replacements
        if expected != new:
            raise Abort(f"post-check failed: {key} differs from the expected result")
    depts = dict(rows(conn, "SELECT department_code, department_name FROM department"))
    if depts != dict(DEPARTMENTS):
        raise Abort(f"post-check failed: departments are {depts}")
    for usn, dept, year in rows(conn, "SELECT usn, department_code, admission_year FROM student"):
        m = USN_PATTERN.fullmatch(usn)
        if not m or m.group(2) != dept or int(m.group(1)) != year % 100:
            raise Abort(f"post-check failed: {usn} does not match department {dept} / joining year {year}")
    leftovers = rows(conn, """
        SELECT 'student', usn FROM student WHERE usn LIKE '1VT%'
        UNION ALL SELECT 'user_account', email FROM user_account
                  WHERE student_usn LIKE '1VT%' OR department_code IN ('CSE', 'ISE', 'ECE')
        UNION ALL SELECT 'point_request', request_id::text FROM point_request
                  WHERE usn LIKE '1VT%' OR reason LIKE '%HackVTU%'
        UNION ALL SELECT 'point_ledger', ledger_id::text FROM point_ledger WHERE usn LIKE '1VT%'
        UNION ALL SELECT 'event', event_id::text FROM event
                  WHERE event_name LIKE '%HackVTU%' OR description LIKE '%HackVTU%'
        UNION ALL SELECT 'ocr_batch', ocr_batch_id::text FROM ocr_batch WHERE extracted_metadata::text LIKE '%HackVTU%'
        UNION ALL SELECT 'ocr_record', ocr_record_id::text FROM ocr_record
                  WHERE extracted_usn LIKE '1VT%' OR matched_student_usn LIKE '1VT%' OR raw_extraction::text LIKE '%1VT%'
        UNION ALL SELECT 'document', storage_key FROM document
                  WHERE storage_key NOT LIKE 'seed/rvce/%' OR lower(storage_key || file_name) LIKE '%vtu%'""")
    if leftovers:
        raise Abort(f"post-check failed: old values remain: {leftovers}")
    expected_docs = {k: (sha256(d), len(d)) for k, d in new_documents().items()}
    stored = {k: (h.strip(), n) for k, h, n in rows(conn, "SELECT storage_key, file_hash, file_size_bytes FROM document")}
    if stored != expected_docs:
        raise Abort("post-check failed: document metadata does not match the regenerated files")
    return after


# --- the correction -------------------------------------------------------------------------------------------------

def correct(conn) -> dict:
    conn.execute(text("LOCK TABLE department, student, user_account, event, point_request, point_ledger, ocr_batch, "
                      "ocr_record, document IN SHARE ROW EXCLUSIVE MODE"))

    # 1. departments: free the UNIQUE names of the removed rows, add the new ones; ME/CV are kept.
    conn.execute(text("UPDATE department SET department_name = left('[pre-RVCE ' || department_code || '] ' || "
                      "department_name, 100) WHERE department_code = ANY(:old)"), {"old": list(REMOVED_DEPARTMENTS)})
    for code, name in DEPARTMENTS:
        conn.execute(text("INSERT INTO department (department_code, department_name) VALUES (:c, :n) "
                          "ON CONFLICT (department_code) DO UPDATE SET department_name = EXCLUDED.department_name"),
                     {"c": code, "n": name})

    # 2-3. students: copy under the new USN (contact email kept), then repoint every child column.
    for old, (new, dept) in STUDENT_MAP.items():
        email = conn.execute(text("SELECT email FROM student WHERE usn = :old"), {"old": old}).scalar()
        conn.execute(text("UPDATE student SET email = :tmp WHERE usn = :old"),   # free the UNIQUE email first
                     {"old": old, "tmp": f"pre-rvce-{old.lower()}@invalid"})
        conn.execute(text("""
            INSERT INTO student (usn, name, email, department_code, entry_type, admission_year, current_semester,
                                 student_status)
            SELECT :new, name, :email, :dept, entry_type, admission_year, current_semester, student_status
            FROM student WHERE usn = :old"""), {"new": new, "old": old, "dept": dept, "email": email})
        for table, column in (("user_account", "student_usn"), ("point_request", "usn"), ("point_ledger", "usn"),
                              ("ocr_record", "matched_student_usn")):
            conn.execute(text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"), {"new": new, "old": old})
    for old, new in HOD_DEPARTMENT_MAP.items():
        conn.execute(text("UPDATE user_account SET department_code = :new WHERE department_code = :old"),
                     {"new": new, "old": old})

    # 4. remove the old rows. NO ACTION FKs make these fail if anything still references them.
    conn.execute(text("DELETE FROM student WHERE usn = ANY(:old)"), {"old": list(STUDENT_MAP)})
    conn.execute(text("DELETE FROM department WHERE department_code = ANY(:old)"), {"old": list(REMOVED_DEPARTMENTS)})

    # 5. approved text replacements in the demo text.
    for eid, name, desc in rows(conn, "SELECT event_id, event_name, description FROM event"):
        conn.execute(text("UPDATE event SET event_name = :n, description = :d WHERE event_id = :id"),
                     {"id": eid, "n": replace_text(name), "d": replace_text(desc)})
    for rid, reason in rows(conn, "SELECT request_id, reason FROM point_request"):
        conn.execute(text("UPDATE point_request SET reason = :r WHERE request_id = :id"),
                     {"id": rid, "r": replace_text(reason)})
    for bid, meta in rows(conn, "SELECT ocr_batch_id, extracted_metadata FROM ocr_batch"):
        conn.execute(text("UPDATE ocr_batch SET extracted_metadata = CAST(:m AS jsonb) WHERE ocr_batch_id = :id"),
                     {"id": bid, "m": None if meta is None else replace_text(json.dumps(meta))})
    for rid, usn, raw in rows(conn, "SELECT ocr_record_id, extracted_usn, raw_extraction FROM ocr_record"):
        conn.execute(text("UPDATE ocr_record SET extracted_usn = :usn, raw_extraction = CAST(:raw AS jsonb) "
                          "WHERE ocr_record_id = :id"),
                     {"id": rid, "usn": replace_text(usn),
                      "raw": None if raw is None else replace_text(json.dumps(raw))})

    # 6. documents: new keys, names and metadata for the regenerated PDFs.
    data = new_documents()
    documents = []
    for old_key, new_key in DOCUMENT_KEY_MAP.items():
        conn.execute(text("UPDATE document SET storage_key = :new, file_name = :name, file_hash = :h, "
                          "file_size_bytes = :n WHERE storage_key = :old"),
                     {"new": new_key, "old": old_key, "h": sha256(data[new_key]), "n": len(data[new_key]),
                      "name": replace_text(conn.execute(text("SELECT file_name FROM document WHERE storage_key = :k"),
                                                        {"k": old_key}).scalar())})
        documents.append({"old_key": old_key, "new_key": new_key, "sha256": sha256(data[new_key]),
                          "bytes": len(data[new_key])})

    # 7. append-only record of the mapping (entity_id 0 is a sentinel: this is not one row of one table).
    conn.execute(text("""
        INSERT INTO audit_log (user_email, action, entity_type, entity_id, old_values, new_values)
        VALUES (NULL, 'DEMO_DATA_CORRECTED_TO_RVCE', 'data_correction', 0, CAST(:old AS jsonb), CAST(:new AS jsonb))"""),
        {"old": json.dumps({"departments": sorted(PRE_DEPARTMENTS)}),
         "new": json.dumps({"text_map": TEXT_MAP, "hod_department_map": HOD_DEPARTMENT_MAP,
                            "departments": [c for c, _ in DEPARTMENTS], "documents": documents,
                            "note": "Earlier audit rows keep the old values (append-only); this map translates them."})})
    return {"documents": documents}


# --- files ----------------------------------------------------------------------------------------------------------

def fsync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def place_new_files(storage: Path, reuse: dict[str, bool], placed: list[Path]):
    """Put every new file in its final place, durably, without ever overwriting an existing name."""
    for key, data in new_documents().items():
        if reuse[key]:
            continue
        final = storage / key
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(final) + TMP_SUFFIX)
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, final)               # atomic; raises FileExistsError instead of overwriting
        placed.append(final)
        tmp.unlink()
        fsync_dir(final.parent)
    for key, data in new_documents().items():
        if sha256((storage / key).read_bytes()) != sha256(data):
            raise Abort(f"placed file {key!r} does not read back correctly")


def cleanup_old_files(conn, storage: Path):
    usns = {r[0] for r in rows(conn, "SELECT usn FROM student")}
    if usns != {new for new, _ in STUDENT_MAP.values()}:
        raise Abort("the database is not corrected; the old files are still referenced")
    for old_key in DOCUMENT_KEY_MAP:
        if conn.execute(text("SELECT 1 FROM document WHERE storage_key = :k"), {"k": old_key}).first():
            raise Abort(f"{old_key!r} is still referenced by a document")
        if (storage / old_key).exists():
            (storage / old_key).unlink()
            print("removed", old_key)


# --- main -----------------------------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", required=True, choices=sorted(ALLOWED_DATABASES))
    parser.add_argument("--storage-dir", default=settings.storage_dir)
    parser.add_argument("--execute", action="store_true", help="commit (default: dry run, always rolled back)")
    parser.add_argument("--cleanup-old-files", action="store_true",
                        help="after a crash past COMMIT: delete old seed files that are no longer referenced")
    parser.add_argument("--simulate-failure-before-commit", action="store_true",
                        help="rehearsal only: fail after the new files are placed, just before COMMIT")
    args = parser.parse_args()
    if args.simulate_failure_before_commit and args.database == "vtu_ledger":
        parser.error("--simulate-failure-before-commit is only allowed on the rehearsal database")

    storage = Path(args.storage_dir).resolve()
    engine = create_engine(make_url(settings.database_url).set(database=args.database))
    placed: list[Path] = []
    commit_attempted = False
    try:
        with engine.connect() as conn:
            if args.cleanup_old_files:
                with conn.begin():
                    cleanup_old_files(conn, storage)
                return
            tx = conn.begin()
            try:
                reuse = precheck(conn, storage)
                before = snapshot(conn)
                report = correct(conn)
                after = postcheck(conn, before)
                if not args.execute:
                    tx.rollback()
                    print("DRY RUN: all checks passed; transaction rolled back, no files written.")
                else:
                    place_new_files(storage, reuse, placed)      # final place + fsync, BEFORE commit
                    if args.simulate_failure_before_commit:
                        raise Abort(f"simulated failure before COMMIT (new files were placed: {len(placed)})")
                    commit_attempted = True
                    tx.commit()
                    print("COMMITTED.")
            except BaseException:
                if tx.is_active:
                    tx.rollback()
                raise
        if args.execute:                                           # reached only after a successful COMMIT
            for old_key in DOCUMENT_KEY_MAP:
                (storage / old_key).unlink(missing_ok=True)
    except Abort as exc:
        print(f"ABORTED, nothing changed: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        if not commit_attempted:
            for path in placed:                                    # failed before COMMIT: undo this run's files
                path.unlink(missing_ok=True)
        engine.dispose()

    print(f"database={args.database} storage={storage}")
    print("students:", *after["summary"], sep="\n  ")
    print("documents:", *report["documents"], sep="\n  ")


if __name__ == "__main__":
    main()
