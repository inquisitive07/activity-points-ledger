# CLAUDE.md

# VTU Activity Point Management, Document Mapping and Verification System

> **Purpose of this file:** This is the master project specification for
> both implementation and academic understanding of the VTU Activity
> Point Management, Document Mapping and Verification System. Read this
> file before designing, modifying, or implementing any part of the
> project.
>
> **Schema authority:** The finalized relational schema documented in
> this file is based on the final detailed schema in the supplied DBMS
> project document (especially its detailed table definitions and final
> design decisions on pages 49--78). Do not use the earlier, superseded
> table definitions in that document.

------------------------------------------------------------------------

## 1. Project overview

### 1.1 Project title

**VTU Activity Point Management, Document Mapping and Verification
System**

Short project name: **Activity Points Ledger**

### 1.2 Project description

The system is a role-based web application designed to digitize the
college's Activity Point workflow, from event creation and student
claims to official point awarding, evidence storage, verification,
correction, reporting, and long-term audit.

The application maintains a centralized official Activity Point Ledger
for every student while separately managing:

-   Club-created events visible to students.
-   Official signed point-award documents uploaded by the Dean.
-   OCR-based extraction of student USNs, names, and points.
-   Human verification of OCR results by the Dean.
-   Individual student certificates and supporting evidence.
-   Missing-point and correction requests.
-   Club-level verification.
-   Dean-level approval.
-   Student, Club, Dean, and HOD document vault views.
-   Audit trails.
-   Department-level reporting.
-   Historical records of graduated students.

### 1.3 Core design principle

> **An event, a point request, a point award, and a document are
> different things and must be represented separately in the database.**

Do not collapse these concepts into one table or workflow.

-   `EVENT` represents an activity/event that can be displayed to
    students and associated with awards.
-   `POINT_REQUEST` represents a student's request for an Activity Point
    action.
-   `POINT_LEDGER` represents an official point award.
-   `DOCUMENT` represents metadata for an uploaded file.
-   `OCR_BATCH` represents one OCR processing operation on a document.
-   `OCR_RECORD` represents one extracted/staged row from an OCR
    operation.

### 1.4 Problem being addressed

The existing process relies on offline approval and signed documents,
often circulated as scanned PDFs. Students and staff may have difficulty
finding a particular student's entry in unindexed scanned documents.
Verification, missing-entry resolution, correction, and end-of-year
audit can therefore be cumbersome.

The system preserves the signed-document approval model while making its
information searchable, structured, traceable, and auditable.

### 1.5 Academic Activity Point requirements

The project specification records these targets:

-   Regular entry: **100 points**.
-   Lateral entry: **75 points**.

These values must be stored in `POINT_REQUIREMENT`, not hardcoded into
frontend components or duplicated in `STUDENT`.

The finalized schema does not include an AICTE category table or AICTE
category attribute.

------------------------------------------------------------------------

## 2. Finalized system architecture

### 2.1 Architecture overview

Keep the architecture below. Do not substitute technologies or introduce
another database without explicit approval.

  -----------------------------------------------------------------------
  Layer                   Finalized technology    Responsibility
  ----------------------- ----------------------- -----------------------
  Frontend                React + JavaScript      Dashboards, forms,
                                                  tables, filters,
                                                  search, uploads,
                                                  vaults, OCR review
                                                  interface

  Backend                 Python + FastAPI        REST APIs,
                                                  authentication,
                                                  authorization, business
                                                  logic, OCR integration,
                                                  validation,
                                                  transactions, audit
                                                  logging

  Relational database     PostgreSQL              Authoritative source of
                                                  truth for structured
                                                  data, relationships,
                                                  point awards, requests,
                                                  metadata, OCR staging,
                                                  and audit records

  OCR                     PaddleOCR +             PDF/image text
                          PP-Structure            extraction, table
                                                  recognition,
                                                  USN/name/point
                                                  extraction, structured
                                                  document processing

  Object storage          Private cloud object    Actual PDF, JPG, PNG,
                          storage (e.g. Supabase  certificate,
                          Storage, S3-compatible  photograph, and
                          storage, or Cloudflare  signed-document files
                          R2)                     

  Semi-structured storage PostgreSQL JSONB        OCR metadata, raw OCR
                                                  extraction, and audit
                                                  before/after values

  Transport               HTTPS/TLS               Secure communication
                                                  between client and
                                                  backend
  -----------------------------------------------------------------------

### 2.2 High-level request/data flow

``` text
Users
  |
  v
React + JavaScript frontend
  |
  | HTTPS / REST API
  v
Python + FastAPI backend
  |             \
  |              \----> PaddleOCR + PP-Structure
  |                         |
  |                         v
  |                     OCR results
  |                         |
  |                         v
  |                     PostgreSQL staging
  |
  +--------------------> PostgreSQL
  |                       - 14 relational tables
  |                       - constraints and relationships
  |                       - STUDENT_POINT_SUMMARY view
  |                       - JSONB OCR/audit data
  |
  +--------------------> Private object storage
                          - PDFs
                          - certificates
                          - photos
                          - signed lists
```

### 2.3 Responsibility boundaries

**React frontend** - Displays role-specific dashboards and pages. -
Collects and validates user input for usability. - Calls FastAPI
endpoints. - Displays server-calculated point summaries and request
statuses. - Provides document upload and authorized document viewing
interfaces. - Provides the Dean's OCR review and exception-resolution
interface. - Must not be treated as the security boundary.

**FastAPI backend** - Authenticates users. - Enforces role-based and
object-level authorization. - Validates input and workflow
transitions. - Implements business rules. - Coordinates PostgreSQL
operations and object-storage access. - Runs or invokes OCR
processing. - Ensures official point awarding is transactional. - Writes
audit records for important actions.

**PostgreSQL** - Is the authoritative source of truth for students,
users, clubs, events, documents' metadata, OCR staging, requests,
awards, relationships, and audit records. - Enforces primary keys,
foreign keys, uniqueness, nullability, and checks. - Calculates derived
information through queries/views rather than unnecessary duplicated
columns.

**Object storage** - Stores actual binary files. - PostgreSQL stores
file metadata and the `storage_key`, not the PDF/JPG/PNG binary
itself. - Files remain private and are delivered only after backend
authorization.

**OCR** - Assists with extraction and matching. - Does not make academic
decisions. - OCR output is staged and reviewed before it can become an
official award.

### 2.4 RDBMS and NoSQL decision

The finalized architecture uses **one PostgreSQL database**. Do not add
MongoDB or another NoSQL database merely because the project discusses
RDBMS/NoSQL.

PostgreSQL stores: - Structured relational data in ordinary typed
columns. - Semi-structured OCR data in `JSONB`. - Semi-structured audit
before/after values in `JSONB`.

Relevant fields: - `OCR_BATCH.extracted_metadata` -
`OCR_RECORD.raw_extraction` - `AUDIT_LOG.old_values` -
`AUDIT_LOG.new_values`

PDFs and images are binary objects, not NoSQL records. They belong in
private object storage.

------------------------------------------------------------------------

## 3. Users, roles, and permissions

The application has four roles. The backend must enforce all
permissions. Hiding a frontend control is not authorization.

### 3.1 STUDENT

Can: - Log in and log out. - View their own Activity Points. - View
required points, earned points, and remaining points. - View event
history and upcoming published events. - View official evidence
associated with their awards. - Upload certificates and supporting
evidence. - Tag documents to events. - Store archive documents. - Submit
`NEW_ACTIVITY`, `MISSING_FROM_LIST`, and `CORRECTION` requests as
applicable. - Track request status and see approved/rejected
decisions. - View documents associated with their awards.

Must not access another student's private records or documents unless an
explicitly authorized workflow allows it.

### 3.2 CLUB_LEAD

Can: - Log in. - Create and manage events belonging to their club. -
Publish event information. - View their club's events. - View student
issues related to their events. - Review missing-from-list and
correction requests. - Review relevant student evidence. - Verify or
reject requests at the club-review stage. - Forward verified requests to
the Dean. - View approved documents for their events. - Monitor
unresolved issues.

Cannot: - Directly award Activity Points. - Modify the official point
ledger. - Approve their own final point awards. - Access unrelated
clubs. - Create arbitrary correction records.

### 3.3 DEAN

The Dean is the final authority for official Activity Point awards.

Can: - View all events and students. - Upload signed official
documents. - Initiate and review OCR processing. - Resolve OCR
exceptions. - Approve or reject student requests. - Commit official
points. - View all authorized documents. - View club accountability and
pending issues. - Upload additional signed correction documents. -
Import student batches. - Generate reports. - Manage historical
records. - Access audit information.

Only explicit Dean authorization may turn staged OCR/request information
into an official ledger award.

### 3.4 HOD

HOD is primarily read-only.

Can: - View and search students in their own department. - View Activity
Points and event history. - View authorized supporting documents, signed
documents, and student evidence. - View request status. - Generate and
export department reports.

Cannot: - Award points. - Modify the ledger. - Approve claims. - Access
students from another department.

### 3.5 Role-to-account associations

`USER_ACCOUNT` uses nullable association columns because different roles
are associated with different records:

  Role        `student_usn`   `department_code`   `club_id`
  ----------- --------------- ------------------- -----------
  STUDENT     Required        NULL                NULL
  HOD         NULL            Required            NULL
  CLUB_LEAD   NULL            NULL                Required
  DEAN        NULL            NULL                NULL

The student account's department can be obtained through `STUDENT`; it
does not need to be duplicated in `USER_ACCOUNT`.

The source specification states that role-dependent population should be
enforced through application logic and/or database checks. Implement
explicit validation for these combinations. Do not assume ordinary
foreign keys enforce the role-to-association rule.

------------------------------------------------------------------------

## 4. Application workflows

### 4.1 Official bulk point-awarding workflow

``` text
CLUB
  |
  v
Create / publish event
  |
  v
STUDENT participates
  |
  v
Club prepares student list
  |
  v
Dean signs PDF offline
  |
  v
Dean uploads signed PDF
  |
  v
Store original document
  |
  v
OCR + table extraction
  |
  v
Extract event / club / student / USN / points
  |
  v
Match and validate against PostgreSQL
  |
  v
Generate verification report
  |
  v
Dean reviews exceptions and confirms records
  |
  v
Transactional database commit
  |
  v
POINT_LEDGER (official awards)
  |
  +------> Student view / vault
  +------> HOD read-only view
  +------> Club vault
```

The club event page means **"this event exists"**. It does not mean
**"these students have officially received points."** Official awarding
is a separate signed-document/OCR/verification/commit workflow.

### 4.2 Student-submitted claims

**Club event**

``` text
Student -> Club -> Dean -> Point Ledger
```

**Non-club event**

``` text
Student -> Dean -> Point Ledger
```

### 4.3 Request types

`POINT_REQUEST.request_type` has these defined values:

-   `NEW_ACTIVITY`: student claims points for an activity not currently
    recorded.
-   `MISSING_FROM_LIST`: student participated in a club event but their
    USN was absent from the official signed list.
-   `CORRECTION`: student reports that an existing point record is
    incorrect.

The request is not itself an award. Approval creates an official ledger
record through the appropriate controlled process.

### 4.4 Request status lifecycle

The source specification lists these statuses:

-   `PENDING_CLUB`
-   `PENDING_DEAN`
-   `APPROVED`
-   `REJECTED`
-   `RESOLVED`
-   `WITHDRAWN`

Typical flow:

``` text
PENDING_CLUB -> PENDING_DEAN -> APPROVED / REJECTED
```

A request can also be resolved through an offline signed-list workflow.
The exact transition rules should be implemented explicitly in backend
business logic and tested. Do not infer that every listed status can
transition to every other status.

### 4.5 Club accountability

The Dean can see clubs and events with issue counts, for example: - Open
club issues. - Pending Dean issues. - Resolved issues.

These counts are **calculated dynamically from `POINT_REQUEST.status`**.
Do not add or store `CLUB.open_issue_count`.

When a request moves from `PENDING_CLUB` to `PENDING_DEAN`, the open
club count naturally decreases. When the Dean resolves it, the pending
Dean count decreases.

### 4.6 Bulk offline resolution

A club may resolve many missing-entry issues through an additional
signed list:

``` text
Multiple student issues
  -> Club prepares additional signed list
  -> Dean uploads signed document
  -> OCR
  -> Dean verifies
  -> Points committed
  -> Matching requests marked RESOLVED
```

This must reuse the official document/OCR/verification process. Do not
create a separate correction-batch table or feature.

### 4.7 Correction handling

Approved awards must not be casually overwritten.

Example:

``` text
Original award: Event X -> 10 points
Correction approved: Event X -> 15 points

Original POINT_LEDGER row: SUPERSEDED
Corrected POINT_LEDGER row: ACTIVE
Corrected row.supersedes_ledger_id = original row.ledger_id
```

This preserves the original award and its history. The active total is
calculated from active ledger rows.

### 4.8 Evidence-only uploads

A student may upload a certificate after points have already been
awarded.

This action: - Does not create another award. - Does not automatically
create a duplicate request. - Can associate the document with an event
through `DOCUMENT.event_id`. - Makes the document available in the
student's vault and to authorized Dean/HOD/Club users according to
access policy. - Stores the physical file only once.

### 4.9 Document vaults

Vaults are **logical views of the same underlying files**, not separate
copies.

-   Student vault: own certificates, evidence, award documents,
    pending-request documents, archive documents.
-   Club vault: event documents, approved signed documents, relevant
    authorized student evidence.
-   Dean vault: institution-wide authorized documents.
-   HOD vault: authorized documents for students in the HOD's
    department.

All access must be authorized by the backend, including download/view
endpoints. A `storage_key` must never be treated as public
authorization.

### 4.10 Graduated students

Students must not be automatically deleted when they graduate.

Lifecycle values listed for `STUDENT.student_status`: - `ACTIVE` -
`GRADUATED` - `INACTIVE` - `ARCHIVED`

Historical records remain available for audit. Any future permanent
deletion/archive operation must be controlled, show affected records,
require Dean authorization, and must not blindly delete shared official
documents.

------------------------------------------------------------------------

## 5. Finalized relational database schema

### 5.1 Schema rules

The finalized core model contains **14 tables** and one view,
`STUDENT_POINT_SUMMARY`.

The following definitions are the detailed relational schema, not a
simplified ER diagram. Preserve every listed attribute and its data
type. Preserve the specified primary keys, foreign keys, nullability,
uniqueness, and checks.

Notation: - `PK`: primary key. - `FK -> TABLE.column`: foreign key
reference. - `NOT NULL`: mandatory value. - `NULL`: nullable. -
`UNIQUE`: unique constraint. - `CHECK`: value/domain constraint. -
`GENERATED ALWAYS AS IDENTITY`: PostgreSQL-generated identifier. -
Composite primary keys are explicitly identified.

Use PostgreSQL-compatible types. Do not replace `TIMESTAMPTZ` with
`TIMESTAMP`, `JSONB` with text, or `NUMERIC` with floating-point types
without explicit approval.

### 5.2 DEPARTMENT

**Purpose:** Stores college departments. Department codes are stable
college identifiers, such as CSE, ISE, ME, and CV. A generated
`department_id` is not required.

  -------------------------------------------------------------------------
  Attribute           PostgreSQL type   Constraints       Purpose
  ------------------- ----------------- ----------------- -----------------
  `department_code`   `VARCHAR(10)`     PK, NOT NULL      Existing college
                                                          department
                                                          identifier

  `department_name`   `VARCHAR(100)`    NOT NULL, UNIQUE  Human-readable
                                                          department name
  -------------------------------------------------------------------------

**Relational schema**

``` text
DEPARTMENT(
    department_code PK,
    department_name UNIQUE NOT NULL
)
```

### 5.3 STUDENT

**Purpose:** Stores the academic identity and lifecycle state of every
student.

  -----------------------------------------------------------------------------------------
  Attribute            PostgreSQL type   Constraints                     Purpose
  -------------------- ----------------- ------------------------------- ------------------
  `usn`                `VARCHAR(20)`     PK, NOT NULL                    Unique student
                                                                         identifier and
                                                                         central reference

  `name`               `VARCHAR(150)`    NOT NULL                        Student identity;
                                                                         also used during
                                                                         OCR matching

  `email`              `VARCHAR(150)`    NOT NULL, UNIQUE                Student
                                                                         academic/contact
                                                                         email

  `department_code`    `VARCHAR(10)`     FK -\>                          Department
                                         `DEPARTMENT.department_code`,   association and
                                         NOT NULL                        HOD visibility

  `entry_type`         `VARCHAR(20)`     NOT NULL, CHECK                 Determines
                                                                         required point
                                                                         target

  `admission_year`     `SMALLINT`        NOT NULL                        Batch
                                                                         identification and
                                                                         historical
                                                                         management

  `current_semester`   `SMALLINT`        CHECK; nullable in the detailed Current academic
                                         schema                          position

  `student_status`     `VARCHAR(20)`     NOT NULL, CHECK                 Student lifecycle
                                                                         status
  -----------------------------------------------------------------------------------------

Defined `entry_type` values: - `REGULAR` - `LATERAL`

Defined `student_status` values: - `ACTIVE` - `GRADUATED` - `INACTIVE` -
`ARCHIVED`

The source schema indicates checks for `entry_type`, `current_semester`,
and `student_status`, but does not give a complete numeric semester
range or a fully formal SQL expression for every check. Define those
exact domain checks during implementation with project-owner
confirmation; do not invent an institution-specific range.

**Relational schema**

``` text
STUDENT(
    usn PK,
    name NOT NULL,
    email UNIQUE NOT NULL,
    department_code FK -> DEPARTMENT.department_code NOT NULL,
    entry_type NOT NULL CHECK,
    admission_year NOT NULL,
    current_semester CHECK NULL,
    student_status NOT NULL CHECK
)
```

**Important design decisions** - Keep `STUDENT.email` even though
`USER_ACCOUNT.email` also exists. Student academic/contact information
and authentication identity are distinct. - Keep `current_semester` as
maintained academic data rather than blindly deriving it from admission
year. Students can progress differently and lateral-entry students have
different progression. - Do not add `total_points`; derive the total
from the ledger.

### 5.4 USER_ACCOUNT

**Purpose:** Stores authentication and authorization identity. The
college email is the primary login identity, so no artificial `user_id`
is required.

  ----------------------------------------------------------------------------------------
  Attribute           PostgreSQL type   Constraints                     Purpose
  ------------------- ----------------- ------------------------------- ------------------
  `email`             `VARCHAR(150)`    PK, NOT NULL                    Login identity

  `password_hash`     `TEXT`            NOT NULL                        Secure password
                                                                        hash

  `role`              `VARCHAR(20)`     NOT NULL, CHECK                 Determines role
                                                                        and permissions

  `account_status`    `VARCHAR(20)`     NOT NULL, CHECK                 Enables/disables
                                                                        account

  `student_usn`       `VARCHAR(20)`     FK -\> `STUDENT.usn`, NULL      Associates Student
                                                                        account with
                                                                        student

  `department_code`   `VARCHAR(10)`     FK -\>                          Associates HOD
                                        `DEPARTMENT.department_code`,   with department
                                        NULL                            

  `club_id`           `BIGINT`          FK -\> `CLUB.club_id`, NULL     Associates Club
                                                                        Lead with club

  `created_at`        `TIMESTAMPTZ`     NOT NULL                        Account creation
                                                                        timestamp

  `last_login_at`     `TIMESTAMPTZ`     NULL                            Account-security
                                                                        information
  ----------------------------------------------------------------------------------------

Defined roles: - `STUDENT` - `CLUB_LEAD` - `HOD` - `DEAN`

The source schema specifies a check for `role` and a non-null
`account_status`, but does not enumerate all account-status values.
Define the account-status domain deliberately during implementation
rather than silently assuming values.

**Role association rules** - STUDENT: `student_usn` populated;
`department_code` and `club_id` NULL. - HOD: `department_code`
populated; `student_usn` and `club_id` NULL. - CLUB_LEAD: `club_id`
populated; `student_usn` and `department_code` NULL. - DEAN: all three
association fields NULL.

Enforce these rules through a database check and/or application
validation. Retain the three nullable association columns as finalized.

**Relational schema**

``` text
USER_ACCOUNT(
    email PK,
    password_hash NOT NULL,
    role NOT NULL CHECK,
    account_status NOT NULL CHECK,
    student_usn FK -> STUDENT.usn NULL,
    department_code FK -> DEPARTMENT.department_code NULL,
    club_id FK -> CLUB.club_id NULL,
    created_at NOT NULL,
    last_login_at NULL
)
```

### 5.5 POINT_REQUIREMENT

**Purpose:** Stores the Activity Point target according to entry type.

  -------------------------------------------------------------------------
  Attribute           PostgreSQL type   Constraints       Purpose
  ------------------- ----------------- ----------------- -----------------
  `entry_type`        `VARCHAR(20)`     PK                `REGULAR` /
                                                          `LATERAL`

  `required_points`   `NUMERIC(6,2)`    NOT NULL, CHECK   Required point
                                        \> 0              target
  -------------------------------------------------------------------------

Initial data:

``` text
REGULAR -> 100
LATERAL -> 75
```

**Relational schema**

``` text
POINT_REQUIREMENT(
    entry_type PK,
    required_points NUMERIC(6,2) NOT NULL CHECK (required_points > 0)
)
```

`STUDENT.entry_type` determines the applicable target. The source design
does not explicitly show a foreign key from `STUDENT.entry_type` to
`POINT_REQUIREMENT.entry_type`; confirm and implement that referential
constraint if the implementation follows the stated lookup relationship.
Do not duplicate `required_points` in `STUDENT`.

### 5.6 CLUB

**Purpose:** Stores clubs that create/manage events.

  -----------------------------------------------------------------------
  Attribute         PostgreSQL type   Constraints       Purpose
  ----------------- ----------------- ----------------- -----------------
  `club_id`         `BIGINT`          PK, GENERATED     Stable internal
                                      ALWAYS AS         identifier
                                      IDENTITY          

  `club_name`       `VARCHAR(150)`    NOT NULL, UNIQUE  Name displayed to
                                                        users

  `club_email`      `VARCHAR(150)`    UNIQUE, NULL      Official club
                                                        contact/login
                                                        association if
                                                        applicable
  -----------------------------------------------------------------------

**Relational schema**

``` text
CLUB(
    club_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    club_name VARCHAR(150) NOT NULL UNIQUE,
    club_email VARCHAR(150) UNIQUE NULL
)
```

The finalized minimal table does not contain `description`, `status`, or
`created_at`. Do not add them without approval.

### 5.7 EVENT

**Purpose:** Represents an activity/event that can be displayed to
students and associated with official point awards. There is no separate
`ACTIVITY` table.

  -----------------------------------------------------------------------------------
  Attribute            PostgreSQL type   Constraints            Purpose
  -------------------- ----------------- ---------------------- ---------------------
  `event_id`           `BIGINT`          PK, GENERATED ALWAYS   Stable event
                                         AS IDENTITY            identifier

  `event_name`         `VARCHAR(200)`    NOT NULL               Event name

  `club_id`            `BIGINT`          FK -\> `CLUB.club_id`, Organizing club; NULL
                                         NULL                   for non-club activity

  `description`        `TEXT`            NULL                   Student-facing event
                                                                information

  `event_date`         `DATE`            NOT NULL               Event date

  `start_time`         `TIME`            NULL                   Event start time

  `end_time`           `TIME`            NULL                   Event end time

  `venue`              `VARCHAR(200)`    NULL                   Event location

  `registration_url`   `TEXT`            NULL                   Student-facing
                                                                registration link

  `standard_points`    `NUMERIC(6,2)`    NULL, CHECK \>= 0      Expected/advertised
                                                                points

  `academic_year`      `VARCHAR(20)`     NOT NULL               Academic-year
                                                                grouping

  `event_status`       `VARCHAR(30)`     NOT NULL               Event lifecycle state

  `created_by`         `VARCHAR(150)`    FK -\>                 Club Lead who created
                                         `USER_ACCOUNT.email`   event
  -----------------------------------------------------------------------------------

Defined event lifecycle examples: - `DRAFT` - `PUBLISHED` -
`COMPLETED` - `CLOSED`

The source says the exact status set can be finalized during
implementation. Use these documented values as the intended lifecycle,
and confirm whether any additional values are required before defining
the final check constraint.

**Relational schema**

``` text
EVENT(
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    event_name NOT NULL,
    club_id FK -> CLUB.club_id NULL,
    description NULL,
    event_date NOT NULL,
    start_time NULL,
    end_time NULL,
    venue NULL,
    registration_url NULL,
    standard_points NUMERIC(6,2) NULL CHECK (standard_points >= 0),
    academic_year NOT NULL,
    event_status NOT NULL,
    created_by FK -> USER_ACCOUNT.email
)
```

**Important decisions** - A club event has a `club_id`; a non-club event
has `club_id = NULL`. - `event_id`, not `event_name`, identifies an
event because the same name can recur in different years. -
`EVENT.standard_points` is the advertised/expected event value. -
`POINT_LEDGER.points_awarded` is the actual amount awarded to a
particular student. They are not interchangeable.

### 5.8 DOCUMENT

**Purpose:** Represents metadata about an uploaded document. The actual
binary file is stored in private object storage.

  --------------------------------------------------------------------------------------
  Attribute           PostgreSQL type   Constraints            Purpose
  ------------------- ----------------- ---------------------- -------------------------
  `document_id`       `BIGINT`          PK, GENERATED ALWAYS   Internal document
                                        AS IDENTITY            reference

  `event_id`          `BIGINT`          FK -\>                 Optional event tag
                                        `EVENT.event_id`, NULL 

  `document_type`     `VARCHAR(30)`     NOT NULL, CHECK        Certificate, signed list,
                                                               photo, etc.

  `file_name`         `VARCHAR(255)`    NOT NULL               Original/display filename

  `storage_key`       `TEXT`            NOT NULL, UNIQUE       Location/key of actual
                                                               object in private storage

  `mime_type`         `VARCHAR(100)`    NOT NULL               PDF/JPEG/PNG type

  `file_size_bytes`   `BIGINT`          NOT NULL, CHECK \> 0   File size

  `file_hash`         `CHAR(64)`        NOT NULL               SHA-256 hash for
                                                               duplicate/integrity
                                                               detection

  `uploaded_by`       `VARCHAR(150)`    FK -\>                 Uploader
                                        `USER_ACCOUNT.email`   

  `uploaded_at`       `TIMESTAMPTZ`     NOT NULL               Upload timestamp

  `document_status`   `VARCHAR(20)`     NOT NULL               Active/archived/deleted
                                                               state
  --------------------------------------------------------------------------------------

Defined document types: - `SIGNED_POINT_LIST` - `CERTIFICATE` -
`ATTENDANCE_SHEET` - `PHOTO` - `REPORT` - `OTHER`

The source indicates a check for `document_type` and a non-null
`document_status`, but does not provide a complete enumerated list for
document statuses. Confirm the final status domain before writing the
check constraint.

**Relational schema**

``` text
DOCUMENT(
    document_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    event_id FK -> EVENT.event_id NULL,
    document_type NOT NULL CHECK,
    file_name NOT NULL,
    storage_key NOT NULL UNIQUE,
    mime_type NOT NULL,
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes > 0),
    file_hash CHAR(64) NOT NULL,
    uploaded_by FK -> USER_ACCOUNT.email,
    uploaded_at NOT NULL,
    document_status NOT NULL
)
```

**Important decisions** - `event_id` is nullable because some
certificates/evidence are associated with an event while archive
documents may not be. - The finalized schema removes `purpose`. It is
not a column in `DOCUMENT`. - A document linked to a request is request
evidence; a document linked to a ledger is award evidence; signed-list
status is represented through document type and workflow
relationships. - Store the actual file in object storage, not in a
PostgreSQL binary column.

### 5.9 OCR_BATCH

**Purpose:** Represents one OCR processing operation on a document. A
document can be processed more than once, so OCR operation data must not
be collapsed into `DOCUMENT`.

  -------------------------------------------------------------------------------------------------------------
  Attribute              PostgreSQL type   Constraints               Purpose
  ---------------------- ----------------- ------------------------- ------------------------------------------
  `ocr_batch_id`         `BIGINT`          PK, GENERATED ALWAYS AS   Identifies one OCR processing operation
                                           IDENTITY                  

  `document_id`          `BIGINT`          FK -\>                    Document being processed
                                           `DOCUMENT.document_id`,   
                                           NOT NULL                  

  `started_by`           `VARCHAR(150)`    FK -\>                    User who initiated OCR
                                           `USER_ACCOUNT.email`      

  `status`               `VARCHAR(30)`     NOT NULL                  Processing/verification/failed/committed
                                                                     state

  `started_at`           `TIMESTAMPTZ`     NOT NULL                  Processing start timestamp

  `completed_at`         `TIMESTAMPTZ`     NULL                      Completion timestamp

  `pages_processed`      `INTEGER`         CHECK \>= 0               Number of processed pages

  `records_detected`     `INTEGER`         CHECK \>= 0               Number of detected rows

  `valid_records`        `INTEGER`         CHECK \>= 0               Number of successfully validated rows

  `exception_count`      `INTEGER`         CHECK \>= 0               Number requiring Dean review

  `extracted_metadata`   `JSONB`           NULL                      Semi-structured document-level OCR output

  `error_message`        `TEXT`            NULL                      Processing failure information
  -------------------------------------------------------------------------------------------------------------

**Relational schema**

``` text
OCR_BATCH(
    ocr_batch_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    document_id FK -> DOCUMENT.document_id NOT NULL,
    started_by FK -> USER_ACCOUNT.email,
    status NOT NULL,
    started_at NOT NULL,
    completed_at NULL,
    pages_processed INTEGER CHECK (pages_processed >= 0),
    records_detected INTEGER CHECK (records_detected >= 0),
    valid_records INTEGER CHECK (valid_records >= 0),
    exception_count INTEGER CHECK (exception_count >= 0),
    extracted_metadata JSONB NULL,
    error_message TEXT NULL
)
```

A document may have multiple OCR batches, allowing reprocessing and
preserving processing history. The source does not enumerate all OCR
batch status values; define the exact status state machine during
implementation.

### 5.10 OCR_RECORD

**Purpose:** Stores each extracted student row before Dean approval.
This is the staging area between OCR and the official ledger.

  ------------------------------------------------------------------------------------------------
  Attribute               PostgreSQL type   Constraints                 Purpose
  ----------------------- ----------------- --------------------------- --------------------------
  `ocr_record_id`         `BIGINT`          PK, GENERATED ALWAYS AS     Identifies an extracted
                                            IDENTITY                    row

  `ocr_batch_id`          `BIGINT`          FK -\>                      OCR run that produced the
                                            `OCR_BATCH.ocr_batch_id`,   row
                                            NOT NULL                    

  `page_number`           `INTEGER`         NOT NULL                    Locates source page

  `row_number`            `INTEGER`         NULL                        Locates row within
                                                                        page/table

  `extracted_usn`         `VARCHAR(20)`     NULL                        Raw USN read by OCR

  `matched_student_usn`   `VARCHAR(20)`     FK -\> `STUDENT.usn`, NULL  Student matched by
                                                                        database lookup

  `extracted_name`        `VARCHAR(150)`    NULL                        Raw OCR name

  `extracted_points`      `NUMERIC(6,2)`    NULL                        Raw extracted points

  `confidence_score`      `NUMERIC(5,4)`    CHECK 0--1                  OCR confidence

  `raw_extraction`        `JSONB`           NULL                        Raw/semi-structured OCR
                                                                        information

  `validation_status`     `VARCHAR(30)`     NOT NULL                    Valid/mismatch/exception
                                                                        state

  `validation_message`    `TEXT`            NULL                        Explanation of validation
                                                                        issue
  ------------------------------------------------------------------------------------------------

**Relational schema**

``` text
OCR_RECORD(
    ocr_record_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    ocr_batch_id FK -> OCR_BATCH.ocr_batch_id NOT NULL,
    page_number INTEGER NOT NULL,
    row_number INTEGER NULL,
    extracted_usn VARCHAR(20) NULL,
    matched_student_usn FK -> STUDENT.usn NULL,
    extracted_name VARCHAR(150) NULL,
    extracted_points NUMERIC(6,2) NULL,
    confidence_score NUMERIC(5,4) CHECK (confidence_score BETWEEN 0 AND 1),
    raw_extraction JSONB NULL,
    validation_status NOT NULL,
    validation_message TEXT NULL
)
```

**Keep both USN fields.** `extracted_usn` records what OCR actually
read. `matched_student_usn` records the database student selected after
matching. They can differ and both are necessary for review and
auditability.

The source does not give a complete enumeration of `validation_status`;
define it as a controlled set in implementation.

### 5.11 POINT_REQUEST

**Purpose:** Represents a request for an Activity Point action. It is
deliberately separate from the official ledger.

  ----------------------------------------------------------------------------------------------
  Attribute                   PostgreSQL type   Constraints                 Purpose
  --------------------------- ----------------- --------------------------- --------------------
  `request_id`                `BIGINT`          PK, GENERATED ALWAYS AS     Identifies an
                                                IDENTITY                    individual request

  `usn`                       `VARCHAR(20)`     FK -\> `STUDENT.usn`, NOT   Student
                                                NULL                        making/requested for

  `event_id`                  `BIGINT`          FK -\> `EVENT.event_id`,    Event involved
                                                NOT NULL                    

  `request_type`              `VARCHAR(30)`     NOT NULL, CHECK             Request category

  `claimed_points`            `NUMERIC(6,2)`    CHECK \>= 0; nullable in    Points
                                                source schema               requested/proposed

  `reason`                    `TEXT`            NOT NULL                    Student's
                                                                            explanation

  `submitted_by`              `VARCHAR(150)`    FK -\> `USER_ACCOUNT.email` User who submitted
                                                                            request

  `correction_of_ledger_id`   `BIGINT`          FK -\>                      Existing award being
                                                `POINT_LEDGER.ledger_id`,   corrected
                                                NULL                        

  `status`                    `VARCHAR(30)`     NOT NULL                    Workflow state

  `club_reviewed_by`          `VARCHAR(150)`    FK -\>                      Club reviewer
                                                `USER_ACCOUNT.email`, NULL  

  `club_reviewed_at`          `TIMESTAMPTZ`     NULL                        Club review time

  `club_review_comment`       `TEXT`            NULL                        Club review comment

  `dean_decided_by`           `VARCHAR(150)`    FK -\>                      Final Dean reviewer
                                                `USER_ACCOUNT.email`, NULL  

  `dean_decided_at`           `TIMESTAMPTZ`     NULL                        Dean decision time

  `dean_decision_comment`     `TEXT`            NULL                        Dean decision
                                                                            comment

  `created_at`                `TIMESTAMPTZ`     NOT NULL                    Request creation
                                                                            time

  `updated_at`                `TIMESTAMPTZ`     NOT NULL                    Last workflow update
  ----------------------------------------------------------------------------------------------

Defined request types: - `NEW_ACTIVITY` - `MISSING_FROM_LIST` -
`CORRECTION`

Defined statuses: - `PENDING_CLUB` - `PENDING_DEAN` - `APPROVED` -
`REJECTED` - `RESOLVED` - `WITHDRAWN`

**Relational schema**

``` text
POINT_REQUEST(
    request_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    usn FK -> STUDENT.usn NOT NULL,
    event_id FK -> EVENT.event_id NOT NULL,
    request_type NOT NULL CHECK,
    claimed_points NUMERIC(6,2) CHECK (claimed_points >= 0) NULL,
    reason TEXT NOT NULL,
    submitted_by FK -> USER_ACCOUNT.email,
    correction_of_ledger_id FK -> POINT_LEDGER.ledger_id NULL,
    status NOT NULL,
    club_reviewed_by FK -> USER_ACCOUNT.email NULL,
    club_reviewed_at TIMESTAMPTZ NULL,
    club_review_comment TEXT NULL,
    dean_decided_by FK -> USER_ACCOUNT.email NULL,
    dean_decided_at TIMESTAMPTZ NULL,
    dean_decision_comment TEXT NULL,
    created_at NOT NULL,
    updated_at NOT NULL
)
```

There are **17 attributes** in this detailed final definition. The
earlier summary table in the source document gives an inconsistent count
for this table; the attribute definition above is the authoritative
field-by-field list.

### 5.12 POINT_REQUEST_DOCUMENT

**Purpose:** Junction table allowing multiple supporting documents for
one request and a document to be associated with request evidence
according to the relationship model.

  -----------------------------------------------------------------------------------
  Attribute         PostgreSQL type   Constraints                   Purpose
  ----------------- ----------------- ----------------------------- -----------------
  `request_id`      `BIGINT`          FK -\>                        Request side
                                      `POINT_REQUEST.request_id`,   
                                      part of PK                    

  `document_id`     `BIGINT`          FK -\>                        Document side
                                      `DOCUMENT.document_id`, part  
                                      of PK                         

  `attached_at`     `TIMESTAMPTZ`     NOT NULL                      When document was
                                                                    attached
  -----------------------------------------------------------------------------------

**Composite primary key:** (`request_id`, `document_id`)

**Relational schema**

``` text
POINT_REQUEST_DOCUMENT(
    request_id FK -> POINT_REQUEST.request_id,
    document_id FK -> DOCUMENT.document_id,
    attached_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (request_id, document_id)
)
```

This is a genuine many-to-many relationship between requests and
documents. Do not replace it with a single `document_id` column in
`POINT_REQUEST`.

### 5.13 POINT_LEDGER

**Purpose:** The official academic Activity Point record. This is the
authoritative table for awarded points.

  ------------------------------------------------------------------------------------------
  Attribute                PostgreSQL type   Constraints                   Purpose
  ------------------------ ----------------- ----------------------------- -----------------
  `ledger_id`              `BIGINT`          PK, GENERATED ALWAYS AS       Identifies an
                                             IDENTITY                      individual award

  `usn`                    `VARCHAR(20)`     FK -\> `STUDENT.usn`, NOT     Student receiving
                                             NULL                          points

  `event_id`               `BIGINT`          FK -\> `EVENT.event_id`, NOT  Event for which
                                             NULL                          points were
                                                                           awarded

  `points_awarded`         `NUMERIC(6,2)`    NOT NULL, CHECK \>= 0         Actual official
                                                                           points

  `award_date`             `DATE`            NOT NULL                      Official award
                                                                           date

  `source_type`            `VARCHAR(30)`     NOT NULL                      Award source

  `source_request_id`      `BIGINT`          FK -\>                        Approved
                                             `POINT_REQUEST.request_id`,   individual
                                             NULL                          request source

  `source_ocr_batch_id`    `BIGINT`          FK -\>                        Bulk OCR
                                             `OCR_BATCH.ocr_batch_id`,     processing source
                                             NULL                          

  `approved_by`            `VARCHAR(150)`    FK -\> `USER_ACCOUNT.email`   Dean who
                                                                           authorized award

  `approved_at`            `TIMESTAMPTZ`     NOT NULL                      Approval
                                                                           timestamp

  `status`                 `VARCHAR(20)`     NOT NULL                      Award lifecycle
                                                                           state

  `supersedes_ledger_id`   `BIGINT`          FK -\>                        Previous award
                                             `POINT_LEDGER.ledger_id`,     superseded by
                                             NULL                          this award
  ------------------------------------------------------------------------------------------

Defined source types: - `BULK_APPROVAL` - `INDIVIDUAL_CLAIM` -
`MANUAL_CORRECTION`

Defined statuses: - `ACTIVE` - `SUPERSEDED` - `REVOKED`

**Relational schema**

``` text
POINT_LEDGER(
    ledger_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    usn FK -> STUDENT.usn NOT NULL,
    event_id FK -> EVENT.event_id NOT NULL,
    points_awarded NUMERIC(6,2) NOT NULL CHECK (points_awarded >= 0),
    award_date DATE NOT NULL,
    source_type NOT NULL,
    source_request_id FK -> POINT_REQUEST.request_id NULL,
    source_ocr_batch_id FK -> OCR_BATCH.ocr_batch_id NULL,
    approved_by FK -> USER_ACCOUNT.email,
    approved_at TIMESTAMPTZ NOT NULL,
    status NOT NULL,
    supersedes_ledger_id FK -> POINT_LEDGER.ledger_id NULL
)
```

**Important decisions** - `ledger_id` is needed because an individual
award needs a stable identifier for correction, audit, document links,
and supersession. - Keep both `source_request_id` and
`source_ocr_batch_id`. They represent two different award paths and
provide direct traceability without a polymorphic `source_id`. - Keep
`supersedes_ledger_id` to preserve correction history. - The finalized
schema removes `created_at`; `approved_at` is the relevant official
timestamp. - Do not store a `document_id` directly in this table. The
award-document relationship is many-to-many through
`POINT_LEDGER_DOCUMENT`.

The source schema does not explicitly state whether `source_request_id`
and `source_ocr_batch_id` must be mutually exclusive or which one is
required for each `source_type`. Implement and test source-type
consistency rules so that the source columns cannot contradict the award
path. Confirm exact rules with the project owner before enforcing them.

### 5.14 POINT_LEDGER_DOCUMENT

**Purpose:** Connects official awards to supporting documents.

  ---------------------------------------------------------------------------------
  Attribute         PostgreSQL type   Constraints                 Purpose
  ----------------- ----------------- --------------------------- -----------------
  `ledger_id`       `BIGINT`          FK -\>                      Award side
                                      `POINT_LEDGER.ledger_id`,   
                                      part of PK                  

  `document_id`     `BIGINT`          FK -\>                      Document side
                                      `DOCUMENT.document_id`,     
                                      part of PK                  

  `linked_at`       `TIMESTAMPTZ`     NOT NULL                    When relationship
                                                                  was created

  `linked_by`       `VARCHAR(150)`    FK -\> `USER_ACCOUNT.email` Who established
                                                                  the relationship
  ---------------------------------------------------------------------------------

**Composite primary key:** (`ledger_id`, `document_id`)

**Relational schema**

``` text
POINT_LEDGER_DOCUMENT(
    ledger_id FK -> POINT_LEDGER.ledger_id,
    document_id FK -> DOCUMENT.document_id,
    linked_at TIMESTAMPTZ NOT NULL,
    linked_by FK -> USER_ACCOUNT.email,
    PRIMARY KEY (ledger_id, document_id)
)
```

This many-to-many relationship is essential: - One award can have a
certificate, attendance proof, photograph, and official signed PDF. -
One signed PDF can support awards for many students.

### 5.15 AUDIT_LOG

**Purpose:** Provides long-term accountability by recording important
user actions and relevant before/after values.

  ----------------------------------------------------------------------------
  Attribute         PostgreSQL type   Constraints            Purpose
  ----------------- ----------------- ---------------------- -----------------
  `audit_id`        `BIGINT`          PK, GENERATED ALWAYS   Individual audit
                                      AS IDENTITY            entry

  `user_email`      `VARCHAR(150)`    FK -\>                 User who
                                      `USER_ACCOUNT.email`   performed action

  `action`          `VARCHAR(100)`    NOT NULL               What happened

  `entity_type`     `VARCHAR(50)`     NOT NULL               Kind of object
                                                             affected

  `entity_id`       `BIGINT`          NOT NULL               Identifier of
                                                             affected record

  `old_values`      `JSONB`           NULL                   Previous values
                                                             where relevant

  `new_values`      `JSONB`           NULL                   New values where
                                                             relevant

  `ip_address`      `INET`            NULL                   Security/audit
                                                             information

  `created_at`      `TIMESTAMPTZ`     NOT NULL               When action
                                                             happened
  ----------------------------------------------------------------------------

**Relational schema**

``` text
AUDIT_LOG(
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PK,
    user_email FK -> USER_ACCOUNT.email,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id BIGINT NOT NULL,
    old_values JSONB NULL,
    new_values JSONB NULL,
    ip_address INET NULL,
    created_at TIMESTAMPTZ NOT NULL
)
```

Examples of actions to audit: - Login-related actions where
appropriate. - Document upload/deletion. - OCR processing and
verification. - Point approval/rejection. - Request submission. - Club
verification. - Dean decisions. - Corrections. - Student imports. -
Authorized archival/deletion operations. - Important record changes.

`entity_type` and `entity_id` form a generic audit reference in the
finalized design. Do not pretend this pair is a normal foreign key to
every possible table; the source schema defines `entity_id` as
`BIGINT NOT NULL` without a direct FK.

------------------------------------------------------------------------

## 6. Database view: STUDENT_POINT_SUMMARY

This is a **view, not a fifteenth table**.

It derives: - USN. - Student name. - Required points. - Earned points. -
Remaining points. - Whether the target has been reached.

Inputs: - `STUDENT` - `POINT_REQUIREMENT` - `POINT_LEDGER`

The earned total must be calculated from `POINT_LEDGER` rows with
`status = 'ACTIVE'`. Required points are obtained through the student's
`entry_type` and the corresponding `POINT_REQUIREMENT` row.

Conceptual calculation:

``` text
earned_points = SUM(points_awarded for ACTIVE ledger rows)
remaining_points = GREATEST(required_points - earned_points, 0)
target_reached = earned_points >= required_points
```

Students may exceed their target. Do not cap the earned total at the
requirement. Remaining points may be displayed as zero once the target
is reached.

Do not store `STUDENT.total_points` or another duplicated total. The
view should account for students with no ledger rows (earned points
should be zero rather than NULL).

------------------------------------------------------------------------

## 7. Relationship and cardinality reference

The following relationships must be represented through the foreign keys
described in the schema.

  ------------------------------------------------------------------------------------
  Parent            Child                      Cardinality       Relationship detail
  ----------------- -------------------------- ----------------- ---------------------
  `DEPARTMENT`      `STUDENT`                  1:N               Each student belongs
                                                                 to one department

  `DEPARTMENT`      `USER_ACCOUNT`             1:N               HOD account
                                                                 association through
                                                                 `department_code`

  `CLUB`            `EVENT`                    1:N               Club may organize
                                                                 multiple events;
                                                                 `EVENT.club_id` is
                                                                 nullable for non-club
                                                                 events

  `USER_ACCOUNT`    `EVENT`                    1:N               Event creator through
                                                                 `created_by`

  `STUDENT`         `POINT_LEDGER`             1:N               Student can receive
                                                                 multiple awards

  `EVENT`           `POINT_LEDGER`             1:N               Event can generate
                                                                 awards for multiple
                                                                 students

  `STUDENT`         `POINT_REQUEST`            1:N               Student can submit
                                                                 multiple requests

  `EVENT`           `POINT_REQUEST`            1:N               Event can be
                                                                 referenced by
                                                                 multiple requests

  `POINT_REQUEST`   `POINT_REQUEST_DOCUMENT`   1:N               One request can have
                                                                 multiple attached
                                                                 documents

  `DOCUMENT`        `POINT_REQUEST_DOCUMENT`   1:N               A document can be
                                                                 linked through the
                                                                 junction relationship

  `POINT_LEDGER`    `POINT_LEDGER_DOCUMENT`    1:N               One award can have
                                                                 multiple supporting
                                                                 documents

  `DOCUMENT`        `POINT_LEDGER_DOCUMENT`    1:N               One document can
                                                                 support multiple
                                                                 awards

  `DOCUMENT`        `OCR_BATCH`                1:N               A document can be
                                                                 OCR-processed more
                                                                 than once

  `OCR_BATCH`       `OCR_RECORD`               1:N               One OCR batch
                                                                 produces multiple
                                                                 extracted records

  `USER_ACCOUNT`    `DOCUMENT`                 1:N               Uploader through
                                                                 `uploaded_by`

  `USER_ACCOUNT`    `AUDIT_LOG`                1:N               User may generate
                                                                 multiple audit
                                                                 entries

  `EVENT`           `DOCUMENT`                 1:N               Optional event tag
                                                                 through
                                                                 `DOCUMENT.event_id`

  `USER_ACCOUNT`    `POINT_REQUEST`            1:N               Submitter and
                                                                 reviewer references

  `USER_ACCOUNT`    `POINT_LEDGER`             1:N               Approver reference

  `POINT_REQUEST`   `POINT_LEDGER`             1:N /             Award may reference
                                               source-linked     an originating
                                                                 request

  `OCR_BATCH`       `POINT_LEDGER`             1:N /             Multiple awards may
                                               source-linked     originate from one
                                                                 bulk OCR batch

  `POINT_LEDGER`    `POINT_LEDGER`             1:N               A corrected award can
                                               self-reference    supersede a prior
                                                                 award
  ------------------------------------------------------------------------------------

The two junction tables implement many-to-many relationships: -
`POINT_REQUEST` \<-\> `DOCUMENT` through `POINT_REQUEST_DOCUMENT`. -
`POINT_LEDGER` \<-\> `DOCUMENT` through `POINT_LEDGER_DOCUMENT`.

The student-event award association is represented by `POINT_LEDGER`; do
not create a separate `EVENT_PARTICIPATION` table.

------------------------------------------------------------------------

## 8. Important design decisions and excluded structures

These are intentional exclusions. Do not reintroduce them without
explicit approval.

  ------------------------------------------------------------------------
  Excluded table/attribute            Reason
  ----------------------------------- ------------------------------------
  `ACTIVITY`                          `EVENT` is the single representation
                                      of a point-earning occurrence

  `EVENT_PARTICIPATION`               This is not an
                                      attendance/registration-management
                                      system; official signed lists are
                                      authoritative for
                                      participation/points

  `STUDENT_CLUB_MEMBERSHIP`           Club membership is not required for
                                      Activity Point calculation

  `AICTE_CATEGORY`                    Not required by finalized project
                                      requirements

  `STUDENT.total_points`              Derived from active ledger records

  `CLUB.open_issue_count`             Derived dynamically from request
                                      statuses

  `DOCUMENT.purpose`                  Derivable from document type,
                                      relationships, and workflow

  `POINT_LEDGER.document_id`          Award/document is many-to-many
                                      through `POINT_LEDGER_DOCUMENT`

  Duplicate physical files for each   Vaults are logical access views over
  vault                               one stored object

  Separate NoSQL database             PostgreSQL JSONB is sufficient for
                                      the defined semi-structured data
  ------------------------------------------------------------------------

Additional fields explicitly removed during finalization: -
`CLUB.description` - `CLUB.status` - `CLUB.created_at` -
`POINT_LEDGER.created_at`

Do not add these back merely because they may be common in other
systems.

------------------------------------------------------------------------

## 9. OCR implementation architecture

### 9.1 Tools

-   **PaddleOCR:** text recognition from scanned PDFs/images.
-   **PP-Structure:** document layout and table-structure recognition.

Use them for: - PDF/image text extraction. - Table detection and
recognition. - USN extraction. - Student-name extraction. - Point
extraction. - Event and club text extraction. - Structured document
processing.

### 9.2 OCR pipeline

``` text
Original signed document in object storage
  |
  v
DOCUMENT metadata in PostgreSQL
  |
  v
Create OCR_BATCH
  |
  v
Read private file through authorized backend/storage access
  |
  v
PaddleOCR + PP-Structure
  |
  v
Extract page text, layout, tables, rows, columns
  |
  v
Create OCR_RECORD rows
  |
  v
Match extracted USNs against STUDENT
  |
  v
Validate name, USN, points, duplicates, event/club
  |
  v
Generate verification report
  |
  v
Dean reviews/corrects exceptions
  |
  v
Dean explicitly confirms
  |
  v
Transactional ledger commit
```

### 9.3 Validation and exception detection

The system should detect and expose: - Unknown USN. - Invalid USN
format. - Name mismatch. - Invalid point value. - Missing point value. -
Duplicate student row. - Possible duplicate award. - Low OCR
confidence. - Unmatched event. - Unmatched club. - Unexpected table
structure. - Missing required information.

A verification report can include: - Pages processed. - Records
detected. - Valid records. - Unknown USNs. - Name mismatches. - Point
errors. - Possible duplicates. - Total exceptions.

### 9.4 Event and club matching

OCR may detect event and club names. The backend should attempt to match
them against existing `EVENT` and `CLUB` records.

The intended responsibility split is:

``` text
OCR proposes
    |
    v
Database validates and suggests matches
    |
    v
Dean confirms uncertain or final matches
```

Do not silently create new events or clubs from OCR text.

### 9.5 Human-in-the-loop requirement

**OCR output is never automatically an official Activity Point award.**

It remains staged in `OCR_BATCH` and `OCR_RECORD` until: 1. Extraction
is complete. 2. Validation is performed. 3. Exceptions are reviewed. 4.
Dean explicitly confirms the batch/records. 5. A database transaction
successfully commits the awards.

OCR confidence is evidence for review, not authorization.

------------------------------------------------------------------------

## 10. Document and object-storage architecture

### 10.1 Storage separation

PostgreSQL stores document metadata: - `document_id` - `file_name` -
`storage_key` - `file_hash` - `mime_type` - `file_size_bytes` -
`event_id` - `uploaded_by` - `uploaded_at` - `document_status`

Private object storage stores the actual: - PDF files. - JPG/JPEG
files. - PNG files. - Certificates. - Attendance sheets. -
Photographs. - Signed point lists. - Reports.

### 10.2 Upload and access requirements

-   Use private buckets/containers.
-   Validate MIME type and actual file content.
-   Enforce file-size limits.
-   Use safe generated storage keys; do not trust user-supplied
    filenames as storage paths.
-   Calculate SHA-256 hashes.
-   Store original display filename separately from storage key.
-   Authorize every view/download request through FastAPI.
-   Do not expose private storage credentials or unrestricted object
    URLs.
-   Do not duplicate the same file for each vault.
-   Handle database metadata and object-storage operations carefully so
    failed operations do not leave untracked files or broken metadata.

### 10.3 Document tagging

A document can optionally be tagged to an event using
`DOCUMENT.event_id`. This supports certificates associated with an event
and permits archive documents without a known event.

A document's relationship to a request or award is represented by the
appropriate junction table. Do not add a generic `purpose` attribute.

------------------------------------------------------------------------

## 11. Security requirements

### 11.1 Authentication

-   College email and password.
-   Passwords stored only as secure hashes.
-   Use Argon2id or bcrypt.
-   Never store plaintext passwords.
-   Validate account status during login.
-   Provide secure logout and session/token handling.

### 11.2 Authorization

-   Role-based access control (RBAC).
-   Student-level object authorization.
-   Club-level authorization.
-   Department-level authorization.
-   Dean-only global operations.
-   Document-level authorization.
-   Enforce authorization on backend API routes and service methods.
-   Prevent URL/object-ID manipulation from exposing another student's,
    club's, or department's records.

### 11.3 API and transport security

-   HTTPS/TLS in deployed environments.
-   Input validation.
-   Parameterized queries or safe ORM usage.
-   CORS restrictions.
-   Secure cookie/token configuration.
-   Rate limiting where appropriate.
-   Safe error handling that does not leak secrets or internal details.
-   Keep dependencies updated.

### 11.4 Database security

-   Least-privilege database credentials.
-   No database credentials in frontend code.
-   Environment-based secrets.
-   Backups and tested restore procedures.
-   Transaction management for official awards.
-   Avoid constructing SQL from untrusted strings.

FastAPI itself is not immune to attacks. Security depends on the
complete application and deployment.

------------------------------------------------------------------------

## 12. Transactions, consistency, and audit

### 12.1 Official award transaction

Final approval must be performed inside a database transaction. The
exact set of operations should include the relevant award inserts,
document links, request updates (when applicable), and audit records.

Conceptual transaction:

``` sql
BEGIN;

-- Validate the confirmed batch/request and prevent duplicate processing.
-- Insert all required POINT_LEDGER rows.
-- Insert related POINT_LEDGER_DOCUMENT rows.
-- Update associated POINT_REQUEST rows where applicable.
-- Update OCR_BATCH status where applicable.
-- Insert corresponding AUDIT_LOG rows.

COMMIT;
```

If any required database operation fails:

``` sql
ROLLBACK;
```

The database must not end up with only part of a signed list committed
as official points.

### 12.2 Idempotency and duplicate protection

The implementation must protect against accidental double submission or
retry of the same Dean approval. Before commit: - Check batch/request
state. - Check for existing awards or already-processed source
records. - Prevent duplicate commits through appropriate transaction
logic and, where justified, database constraints. - Make repeated API
requests safe against creating duplicate awards.

The source project specifies duplicate-award detection but does not
define a complete natural-key uniqueness rule. Do not invent one without
considering legitimate repeated events/awards and obtaining
confirmation.

### 12.3 Audit requirements

Audit important operations, including: - Request submission and
decisions. - Club review. - Dean approval/rejection. - OCR processing
and verification. - Point award creation. - Award
correction/supersession/revocation. - Document upload and
deletion/archive actions. - Student import. - Important record changes.

Store before/after values as JSONB where relevant. Audit entries should
be created in the same transaction as the important database change
whenever consistency requires it.

------------------------------------------------------------------------

## 13. Reporting and exports

Reports may include: - Student Activity Point summary. - Students below
target. - Students who reached target. - Department-wise status. -
Event-wise points. - Club-wise events. - Club issue counts. - Pending
requests. - Resolved requests. - Rejected requests. - OCR exceptions. -
Student evidence. - Graduated-student records. - Audit information.

Possible export formats: - CSV. - Excel. - PDF reports.

Department-level access must be restricted to the relevant HOD's
department. Institution-wide reporting is available to authorized Dean
users.

------------------------------------------------------------------------

## 14. Implementation roadmap

Implement incrementally. Do not attempt to generate the entire
application in one step. Each phase should be runnable and tested before
moving forward.

  -------------------------------------------------------------------------
  Phase                   Work                    Expected result
  ----------------------- ----------------------- -------------------------
  1                       Confirm requirements    Repository, environment,
                          and project setup       configuration

  2                       PostgreSQL database     Local database and
                          creation                connection

  3                       Implement all 14 tables Schema with keys, FKs,
                                                  checks, uniqueness

  4                       Seed/reference data and Point requirements and
                          view                    `STUDENT_POINT_SUMMARY`

  5                       FastAPI foundation      App entry point,
                                                  settings, database
                                                  connection

  6                       Authentication and RBAC Login, password hashing,
                                                  role/object checks

  7                       Student and department  Student search, profiles,
                          modules                 department restrictions

  8                       Club and event          Club Lead event creation
                          management              and publishing

  9                       Document upload and     Private upload, metadata,
                          storage                 authorized retrieval

  10                      Point request workflows Student requests, club
                                                  review, Dean decision

  11                      OCR pipeline            PDF/image processing,
                                                  batches, records,
                                                  validation

  12                      Dean verification and   Exception review and
                          awarding                transaction-safe commit

  13                      Ledger and vaults       Point history, evidence
                                                  links, logical vaults

  14                      HOD and Dean dashboards Role-specific reporting
                                                  and accountability

  15                      Audit and exports       Audit history and
                                                  CSV/Excel/PDF exports

  16                      Integration and testing End-to-end verified
                                                  workflows
  -------------------------------------------------------------------------

### 14.1 Suggested backend organization

This is an implementation organization suggestion, not a change to the
architecture or schema.

``` text
backend/
  app/
    api/
      routes/
    core/
      config.py
      security.py
      permissions.py
    db/
      session.py
    models/
    schemas/
    repositories/
    services/
    ocr/
    storage/
    main.py
  tests/
  requirements.txt
```

### 14.2 Suggested frontend organization

``` text
frontend/
  src/
    api/
    components/
    features/
      auth/
      student/
      club/
      dean/
      hod/
      events/
      requests/
      ledger/
      documents/
      ocr/
    layouts/
    routes/
    hooks/
    utils/
    App.jsx
    main.jsx
```

### 14.3 Suggested database files

``` text
database/
  schema.sql
  seed.sql
  views.sql
  indexes.sql
```

Use migrations for schema evolution after the initial schema is
established. Keep schema changes reviewed and reproducible.

------------------------------------------------------------------------

## 15. Testing requirements

At minimum, test the following.

### 15.1 Database tests

-   All 14 tables can be created in dependency-safe order.
-   Primary keys reject duplicates.
-   Foreign keys reject invalid references.
-   NOT NULL and CHECK constraints behave as intended.
-   Composite keys prevent duplicate junction rows.
-   Nullable relationships behave as documented.
-   `STUDENT_POINT_SUMMARY` handles students with no awards.
-   Only `ACTIVE` ledger rows contribute to earned totals.
-   Corrected/superseded records preserve history.

### 15.2 Authorization tests

-   Student cannot view another student's private records.
-   Club Lead cannot access unrelated clubs.
-   Club Lead cannot directly award points.
-   HOD cannot access another department.
-   HOD cannot modify/approve awards.
-   Dean can access institution-wide functions.
-   Unauthorized document IDs/storage keys do not bypass access control.

### 15.3 Workflow tests

-   Club event creation and publication.
-   Non-club event creation.
-   Student request submission.
-   Club review and forwarding.
-   Dean approval/rejection.
-   Evidence-only upload without new award.
-   Missing-from-list request resolved through signed bulk list.
-   Correction creates a superseding record rather than overwriting
    history.

### 15.4 OCR tests

-   Correct USN extraction.
-   Unknown/invalid USN handling.
-   Name mismatch.
-   Missing/invalid points.
-   Duplicate rows and possible duplicate awards.
-   Low confidence.
-   Event/club mismatch.
-   Reprocessing the same document.
-   OCR failure and retry.
-   Dean correction of extracted values.
-   OCR alone never creates a ledger award.

### 15.5 Transaction tests

Simulate a failure during a multi-student batch commit and verify that
the entire database transaction rolls back. No partial official ledger
update is acceptable.

------------------------------------------------------------------------

## 16. Academic explanation and project significance

### 16.1 Why this is a DBMS project

The project is not merely a CRUD application. Its central design work
involves: - Relational modeling of distinct real-world concepts. -
Primary and foreign key design. - One-to-many and many-to-many
relationships. - Composite primary keys in junction tables. -
Constraints and referential integrity. - Derived data and database
views. - JSONB for semi-structured information within a relational
database. - Transactional consistency for bulk awarding. - Auditability
and historical record preservation. - Role- and department-scoped data
access.

### 16.2 Key design justification

**Separate event, request, award, document, and OCR operation:** Each
represents a different fact and has a different lifecycle. Keeping them
separate prevents an uploaded certificate, a claim, or an OCR extraction
from being mistaken for an official award.

**Central point ledger:** `POINT_LEDGER` is the authoritative source for
official points. Totals are derived from active records.

**Junction tables:** `POINT_REQUEST_DOCUMENT` and
`POINT_LEDGER_DOCUMENT` accurately model many-to-many evidence
relationships.

**OCR staging:** `OCR_BATCH` and `OCR_RECORD` preserve extracted data
separately from official academic records.

**Human verification:** Dean approval is required because OCR can make
recognition or matching errors and points are academic records.

**Transactions:** All awards from a signed list are committed together
or rolled back together.

**Object storage separation:** Large binary files remain outside the
relational database while metadata and relationships remain queryable in
PostgreSQL.

**Audit log:** Important actions and before/after values remain
traceable.

### 16.3 Main innovative components

1.  Converts existing signed documents into searchable structured
    records.
2.  Uses human-in-the-loop OCR verification rather than blindly trusting
    OCR.
3.  Provides document-to-ledger traceability.
4.  Supports multiple evidence files without duplicate awards.
5.  Allows evidence to be uploaded after an award without creating
    another award.
6.  Calculates club accountability from request workflow state.
7.  Uses transaction-safe bulk processing to prevent partial ledger
    updates.

### 16.4 Three key traceability paths

**Official bulk awarding**

``` text
EVENT
  -> DOCUMENT
  -> OCR_BATCH
  -> OCR_RECORD
  -> Dean verification
  -> POINT_LEDGER
```

**Individual claim**

``` text
STUDENT
  -> POINT_REQUEST
  -> POINT_REQUEST_DOCUMENT / DOCUMENT
  -> Club and/or Dean verification
  -> POINT_LEDGER
```

**Evidence for an existing award**

``` text
STUDENT
  -> DOCUMENT
  -> optional EVENT tag
  -> POINT_LEDGER_DOCUMENT
  -> existing POINT_LEDGER
```

The third path adds evidence to an existing award and must not create
new points.

------------------------------------------------------------------------

## 17. Instructions to Claude Code

These instructions are mandatory for work in this repository.

### 17.1 Before making changes

1.  Read this entire `CLAUDE.md`.
2.  Inspect the existing repository and identify what is already
    implemented.
3.  Do not overwrite existing work without inspecting it.
4.  Identify the current implementation phase and work only on the
    requested next task.
5.  If a requirement is ambiguous or conflicts with this document,
    explain the conflict and ask before making a structural decision.

### 17.2 Preserve the architecture

-   Use React + JavaScript for the frontend.
-   Use Python + FastAPI for the backend.
-   Use PostgreSQL as the sole authoritative relational database.
-   Use PaddleOCR + PP-Structure for OCR.
-   Use PostgreSQL JSONB for the specified semi-structured data.
-   Use private object storage for actual uploaded files.
-   Do not introduce MongoDB, Firebase, a different frontend framework,
    or a different backend framework without explicit approval.

### 17.3 Preserve the database model

-   Keep exactly the 14 finalized core tables unless the project owner
    explicitly approves a schema change.
-   Keep the `STUDENT_POINT_SUMMARY` view as a view, not a table.
-   Preserve every attribute listed in Section 5.
-   Preserve the specified PostgreSQL data types.
-   Preserve primary keys, foreign keys, composite keys, nullability,
    uniqueness, and checks.
-   Do not simplify the detailed relational schema into an ER-style
    entity list.
-   Do not add `ACTIVITY`, `EVENT_PARTICIPATION`,
    `STUDENT_CLUB_MEMBERSHIP`, or `AICTE_CATEGORY`.
-   Do not add `STUDENT.total_points`, `CLUB.open_issue_count`, or
    `DOCUMENT.purpose`.
-   Do not add `POINT_LEDGER.document_id`.
-   Do not remove the two junction tables.
-   Do not silently rename columns or change identifier types.
-   If a missing constraint must be decided (for example, a status
    domain whose exact values were not specified), document the proposed
    decision and seek confirmation rather than presenting it as an
    already-finalized source requirement.

### 17.4 Preserve business rules

-   OCR is not an authority to award points.
-   Only Dean approval can authorize official point awarding.
-   Official ledger operations must be transactional.
-   A bulk approval must commit all relevant awards or none.
-   Corrections must preserve the original award and use supersession.
-   Evidence-only uploads must not create duplicate awards.
-   Club Leads cannot directly modify the official ledger.
-   HOD access is read-only and department-scoped.
-   Student, club, and document access must be checked on the backend.
-   Graduated student records must be retained for historical/audit
    purposes.
-   Vaults are logical views of one stored file, not duplicate file
    copies.
-   Derived point totals and club issue counts must not be stored
    redundantly.

### 17.5 Implementation approach

-   Work in small, verifiable increments.
-   Explain the purpose of each module and significant design choice.
-   Prefer clear, maintainable code over unnecessary abstraction.
-   Use typed request/response schemas and server-side validation.
-   Keep API routes, business services, persistence/repositories, OCR,
    and storage responsibilities separated.
-   Use migrations for schema changes.
-   Add tests alongside implementation.
-   Never claim a feature is complete unless it has been implemented and
    tested.
-   Do not create placeholder implementations that appear functional but
    bypass core security or approval rules.
-   Keep secrets in environment variables and never commit them.

### 17.6 When proposing a change

For any proposed schema or architecture change, state: 1. What is
changing. 2. Why the current design is insufficient. 3. Which tables,
attributes, relationships, workflows, or APIs are affected. 4. Whether
historical data migration is needed. 5. What alternatives exist. 6. What
approval is required.

Do not apply structural changes until the project owner approves them.

------------------------------------------------------------------------

## 18. Source fidelity, known clarifications, and items to confirm

This file preserves the finalized detailed schema and architecture from
the supplied project specification. Some implementation-level details
are intentionally not invented where the source does not fully define
them.

### 18.1 Attribute-count discrepancy

The source document's summary reports **126 attributes/columns**, but
the field-by-field final definitions do not arithmetically support that
total. Counting the attributes listed in the detailed definitions in
this file gives:

  Table                                   Detailed attribute count
  ------------------------------------- --------------------------
  DEPARTMENT                                                     2
  STUDENT                                                        8
  USER_ACCOUNT                                                   9
  POINT_REQUIREMENT                                              2
  CLUB                                                           3
  EVENT                                                         13
  DOCUMENT                                                      11
  OCR_BATCH                                                     12
  OCR_RECORD                                                    12
  POINT_REQUEST                                                 17
  POINT_REQUEST_DOCUMENT                                         3
  POINT_LEDGER                                                  12
  POINT_LEDGER_DOCUMENT                                          4
  AUDIT_LOG                                                      9
  **Total from detailed definitions**                      **117**

The detailed field-by-field definitions are preserved above. Do not add
nine speculative columns merely to force the total to 126. The source's
earlier summary also lists `POINT_REQUEST` with 16 attributes, whereas
its final detailed field list contains 17. This file preserves all 17
detailed fields.

### 18.2 Constraints requiring implementation-time confirmation

The source specifies that several columns have `CHECK` constraints but
does not always enumerate the complete allowed values or exact
expression. Confirm and document the final SQL expressions for: -
`STUDENT.entry_type`, `STUDENT.current_semester`, and
`STUDENT.student_status`. - `USER_ACCOUNT.role` and
`USER_ACCOUNT.account_status`. - `EVENT.event_status`. -
`DOCUMENT.document_type` and `DOCUMENT.document_status`. -
`OCR_BATCH.status`. - `OCR_RECORD.validation_status`. -
`POINT_REQUEST.request_type` and `POINT_REQUEST.status`. -
`POINT_LEDGER.source_type` and `POINT_LEDGER.status`. - Role-dependent
`USER_ACCOUNT` association columns. - `POINT_LEDGER` source-column
consistency.

Where the source does give allowed values, those values are listed in
this file. Do not invent additional institution-specific values and
label them as source-defined.

### 18.3 Referential details to confirm

-   The conceptual design connects `STUDENT.entry_type` to
    `POINT_REQUIREMENT.entry_type`; the detailed table definition does
    not explicitly show the FK constraint. Confirm before adding it to
    the final DDL.
-   Some reviewer/uploader/creator columns are shown as FKs to
    `USER_ACCOUNT.email` without always repeating nullability. Preserve
    the source's explicit nullability and make any additional
    nullability decision visible.
-   The source does not fully define uniqueness rules for preventing
    duplicate awards. Design duplicate protection carefully without
    blocking legitimate repeated events or awards.
-   The source does not fully define every workflow transition or OCR
    batch status. Define and test explicit state machines before
    implementation.

### 18.4 Do not silently resolve discrepancies

When the project owner asks for implementation, use the detailed field
definitions in Section 5 as the schema baseline. If a conflict arises
between a summary count and the detailed field definitions, point it out
and ask for a decision where necessary. Never silently remove or invent
attributes.

------------------------------------------------------------------------

## 19. Final project invariants

The following statements summarize the non-negotiable design:

1.  PostgreSQL is the authoritative source of truth for structured data
    and official points.
2.  Private object storage is the source of truth for actual uploaded
    files.
3.  OCR is an extraction assistant, not an awarding authority.
4.  The Dean is the final authority for official Activity Point awards.
5.  Official point awards are represented in `POINT_LEDGER`.
6.  Requests and awards are separate records.
7.  Documents and awards are separate records.
8.  OCR batches and OCR records are staging records, not awards.
9.  The 14-table relational model is the finalized baseline.
10. `STUDENT_POINT_SUMMARY` is a view, not a table.
11. Student totals are derived from active ledger rows.
12. Club issue counts are derived from request statuses.
13. Evidence can be attached without creating another award.
14. Corrections preserve historical awards through supersession.
15. Official bulk awarding is all-or-nothing through a database
    transaction.
16. Role, department, club, student, and document access is enforced by
    the backend.
17. The architecture remains React + JavaScript, Python + FastAPI,
    PostgreSQL, PaddleOCR + PP-Structure, PostgreSQL JSONB, and private
    object storage.

**End of master project specification.**
