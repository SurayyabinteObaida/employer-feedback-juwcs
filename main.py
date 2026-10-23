from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session as DBSession
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone, date
from typing import Optional, List
import asyncio
import uuid
import os
import secrets
import hashlib
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# --- SMTP configuration ---
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "CSSE, JUW - OBE Indirect Assessments")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() != "false"

# --- Link lifetimes ---
ENGAGEMENT_LINK_HOURS = 48
EXIT_FORM_WINDOW_HOURS = 72
EXIT_FORM_MAX_REMINDERS = 3
EXIT_REMINDER_INTERVAL_MINUTES = int(os.getenv("EXIT_REMINDER_INTERVAL_MINUTES", "60"))

EMPLOYER_TYPES = ("internship_supervisor", "graduate_employer", "both")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine)


@event.listens_for(engine, "connect")
def _set_utc(dbapi_connection, connection_record):
    # Timestamps are stored without a zone and read back as UTC; pin the
    # session to UTC so NOW() and Python-side expiry checks always agree.
    cur = dbapi_connection.cursor()
    cur.execute("SET TIME ZONE 'UTC'")
    cur.close()
    dbapi_connection.commit()

print(f"[STARTUP] FRONTEND_URL = {FRONTEND_URL}")
print(f"[STARTUP] SMTP_HOST = {'SET (' + SMTP_HOST + ')' if SMTP_HOST else 'NOT SET'}")
print(f"[STARTUP] SMTP_USER = {'SET (' + SMTP_USER + ')' if SMTP_USER else 'NOT SET'}")
print(f"[STARTUP] SMTP_PASSWORD = {'SET (length={})'.format(len(SMTP_PASSWORD)) if SMTP_PASSWORD else 'NOT SET'}")
print(f"[STARTUP] SMTP_PORT = {SMTP_PORT}, SMTP_USE_TLS = {SMTP_USE_TLS}")

app = FastAPI(title="OBE Indirect Assessments Panel")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Small helpers ---

def utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt):
    return utc(dt).isoformat() if dt else None


def now_utc():
    return datetime.now(timezone.utc)

# --- Password helpers ---

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${hashed}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or '$' not in stored_hash:
        return False
    salt, hashed = stored_hash.split('$', 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == hashed

# --- Email helper ---

def send_email_smtp(to_email: str, subject: str, html_body: str) -> tuple[bool, str]:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        return False, "SMTP is not configured (missing SMTP_HOST/SMTP_USER/SMTP_PASSWORD)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        if SMTP_USE_TLS:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
        server.quit()
        return True, ""
    except Exception as e:
        return False, str(e)


EMAIL_SIGNATURE = "<p>Regards,<br>Department of Computer Science and Software Engineering<br>Jinnah University for Women</p>"

# --- Batch helpers ---
# Batch = graduation year. A batch is graduated once its year <= current year.

BATCH_YEAR_SQL = r"CAST(substring(s.batch from '(\d{4})\s*$') AS INTEGER)"
INTERN_SQL = "EXISTS (SELECT 1 FROM engagements gi WHERE gi.student_id = s.id AND gi.type = 'internship')"


def get_batch_status(batch_year: str) -> str:
    try:
        return "graduated" if int(str(batch_year).split()[-1]) <= datetime.now().year else "active"
    except (ValueError, IndexError):
        return "active"


def combine_program_batch(degree_program: str, batch: str) -> str:
    degree_program = (degree_program or "").strip()
    batch = (batch or "").strip()
    if degree_program and batch:
        return f"{degree_program} {batch}"
    return degree_program or batch


def split_program_batch(combined: str):
    combined = (combined or "").strip()
    if not combined:
        return "", ""
    parts = combined.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", combined


def add_batch_filter(where: list, params: dict, batch: str):
    if not batch:
        return
    program, year = split_program_batch(batch)
    where.append("s.batch = :batch")
    params["batch"] = year
    if program:
        where.append("s.degree_program = :program")
        params["program"] = program

# --- Startup migrations ---

@app.on_event("startup")
def ensure_columns():
    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS admins (
                id VARCHAR PRIMARY KEY,
                email VARCHAR UNIQUE NOT NULL,
                name VARCHAR,
                password_hash VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                id VARCHAR PRIMARY KEY,
                admin_id VARCHAR NOT NULL REFERENCES admins(id),
                token VARCHAR UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS alumni (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                student_id UUID UNIQUE NOT NULL REFERENCES students(id),
                email VARCHAR NOT NULL,
                contact_number VARCHAR,
                linkedin_url VARCHAR,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS alumni_info_confirmations (
                id VARCHAR PRIMARY KEY,
                alumnus_id UUID UNIQUE NOT NULL REFERENCES alumni(id),
                confirmed_name VARCHAR,
                confirmed_batch VARCHAR,
                confirmed_enrollment_number VARCHAR,
                confirmed_email VARCHAR,
                confirmed_linkedin_url VARCHAR,
                confirmed_contact_number VARCHAR,
                validation_status VARCHAR NOT NULL DEFAULT 'pending',
                submitted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # Legacy alumni exit survey table. Kept so existing submissions remain
        # visible; new exit surveys go to student_exit_forms.
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS alumni_exit_surveys (
                id VARCHAR PRIMARY KEY,
                alumnus_id UUID UNIQUE NOT NULL REFERENCES alumni(id),
                rating_ga2 SMALLINT, rating_ga3 SMALLINT, rating_ga4 SMALLINT,
                rating_ga5 SMALLINT, rating_ga6 SMALLINT, rating_ga7 SMALLINT,
                rating_ga8 SMALLINT, rating_ga9 SMALLINT, rating_ga10 SMALLINT,
                liked_most TEXT,
                improvement_suggestions TEXT,
                post_grad_plan VARCHAR,
                job_offer_source VARCHAR,
                intends_higher_studies VARCHAR,
                higher_studies_specialization VARCHAR,
                submitted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS alumni_feedback_forms (
                id VARCHAR PRIMARY KEY,
                alumnus_id UUID NOT NULL REFERENCES alumni(id),
                survey_year VARCHAR NOT NULL,
                current_status VARCHAR,
                years_experience VARCHAR,
                current_employer VARCHAR,
                job_title VARCHAR,
                is_first_job BOOLEAN,
                working_as VARCHAR,
                employment_region VARCHAR,
                had_offer_before_graduation BOOLEAN,
                first_job_source VARCHAR,
                starting_salary_bracket VARCHAR,
                pursuing_higher_studies BOOLEAN,
                higher_studies_degree VARCHAR,
                higher_studies_university VARCHAR,
                higher_studies_country VARCHAR,
                higher_studies_interest VARCHAR,
                rating_ga1 SMALLINT, rating_ga2 SMALLINT, rating_ga3 SMALLINT, rating_ga4 SMALLINT,
                rating_ga5 SMALLINT, rating_ga6 SMALLINT, rating_ga7 SMALLINT, rating_ga8 SMALLINT,
                rating_ga9 SMALLINT, rating_ga10 SMALLINT,
                feedback TEXT,
                submitted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (alumnus_id, survey_year)
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS alumni_action_links (
                id VARCHAR PRIMARY KEY,
                alumnus_id UUID NOT NULL REFERENCES alumni(id),
                action_type VARCHAR NOT NULL,
                target_id VARCHAR,
                token VARCHAR UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        # Merged exit survey: one row per student. 72h link, up to 3 automatic
        # reminders (each a fresh 72h link), then status 'expired'.
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS student_exit_forms (
                id VARCHAR PRIMARY KEY,
                student_id UUID NOT NULL REFERENCES students(id),
                token VARCHAR UNIQUE NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                reminder_count SMALLINT NOT NULL DEFAULT 0,
                expires_at TIMESTAMP NOT NULL,
                used_at TIMESTAMP,
                submitted_at TIMESTAMP,
                rating_ga2 SMALLINT, rating_ga3 SMALLINT, rating_ga4 SMALLINT,
                rating_ga5 SMALLINT, rating_ga6 SMALLINT, rating_ga7 SMALLINT,
                rating_ga8 SMALLINT, rating_ga9 SMALLINT, rating_ga10 SMALLINT,
                liked_most TEXT,
                improvement_suggestions TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS alumni_survey_schedule (
                id VARCHAR PRIMARY KEY,
                alumnus_id UUID UNIQUE NOT NULL REFERENCES alumni(id),
                next_send_at TIMESTAMP NOT NULL,
                last_sent_at TIMESTAMP,
                interval_months SMALLINT NOT NULL DEFAULT 8,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        migrations = [
            ("employers", "password_hash", "VARCHAR"),
            ("employers", "employer_type", "VARCHAR"),
            ("employers", "organization", "VARCHAR"),
            ("students", "email", "VARCHAR"),
            ("internship_evaluations", "rating_work_quality", "SMALLINT"),
            ("internship_evaluations", "rating_task_completion", "SMALLINT"),
            ("internship_evaluations", "rating_overall_competence", "SMALLINT"),
            ("employer_surveys", "overall_performance", "VARCHAR"),
            ("employer_surveys", "year_of_graduation", "VARCHAR"),
            ("org_proformas", "linkedin_url", "VARCHAR"),
            ("org_proformas", "graduate_full_name", "VARCHAR"),
            ("org_proformas", "graduate_degree_program", "VARCHAR"),
            ("org_proformas", "year_of_graduation", "VARCHAR"),
            ("org_proformas", "current_job_role", "VARCHAR"),
            ("org_proformas", "job_department", "VARCHAR"),
            ("org_proformas", "duration_of_employment", "VARCHAR"),
            ("org_proformas", "invite_token", "VARCHAR"),
            ("org_proformas", "invite_expires_at", "TIMESTAMP"),
            ("org_proformas", "invite_used_at", "TIMESTAMP"),
            # Merged exit survey: fields carried over from the alumni exit survey.
            ("student_exit_forms", "post_grad_plan", "VARCHAR"),
            ("student_exit_forms", "job_offer_source", "VARCHAR"),
            ("student_exit_forms", "intends_higher_studies", "VARCHAR"),
            ("student_exit_forms", "higher_studies_specialization", "VARCHAR"),
        ]
        for table, col, coltype in migrations:
            db.execute(text(f"""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = '{table}' AND column_name = '{col}'
                    ) THEN
                        ALTER TABLE {table} ADD COLUMN {col} {coltype};
                    END IF;
                END $$;
            """))

        # Pre-fill organization and employer type for existing employers from
        # their engagements. Only fills blanks; admin edits are never overwritten.
        db.execute(text("""
            UPDATE employers e SET organization = sub.org
            FROM (
                SELECT DISTINCT ON (g.employer_id) g.employer_id, op.organization_name AS org
                FROM engagements g JOIN org_proformas op ON op.engagement_id = g.id
                WHERE COALESCE(op.organization_name, '') <> ''
                ORDER BY g.employer_id, g.created_at DESC
            ) sub
            WHERE sub.employer_id = e.id AND COALESCE(e.organization, '') = ''
        """))
        db.execute(text("""
            UPDATE employers e SET employer_type = CASE
                WHEN t.has_intern AND t.has_job THEN 'both'
                WHEN t.has_intern THEN 'internship_supervisor'
                ELSE 'graduate_employer' END
            FROM (
                SELECT employer_id,
                       BOOL_OR(type = 'internship') AS has_intern,
                       BOOL_OR(type = 'job') AS has_job
                FROM engagements GROUP BY employer_id
            ) t
            WHERE t.employer_id = e.id AND e.employer_type IS NULL
        """))

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Startup migrations error: {e}")
    finally:
        db.close()

# --- Database dependency ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Auth helpers ---

def get_current_admin(authorization: str = Header(None), db: DBSession = Depends(get_db)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.split(" ")[1]
    session = db.execute(
        text("SELECT admin_id, expires_at FROM admin_sessions WHERE token = :token"),
        {"token": token}
    ).mappings().first()

    if not session or now_utc() > utc(session["expires_at"]):
        raise HTTPException(status_code=401, detail="Session expired")

    admin = db.execute(
        text("SELECT id, name, email FROM admins WHERE id = :id"),
        {"id": session["admin_id"]}
    ).mappings().first()

    return dict(admin) if admin else {}

# --- Pydantic models ---

class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminSetupRequest(BaseModel):
    email: str
    password: str
    name: str


class CreateAdminRequest(BaseModel):
    email: str
    password: str
    name: str


class StudentRequest(BaseModel):
    full_name: str
    enrollment_number: str
    degree_program: str
    batch: str
    current_semester: Optional[str] = None
    email: Optional[str] = None


class BulkStudentsRequest(BaseModel):
    rows: List[dict]


class EmployerRequest(BaseModel):
    name: str
    email: str
    organization: Optional[str] = None
    designation: Optional[str] = None
    employer_type: str


class AlumniSaveRequest(BaseModel):
    student_id: str
    email: str
    contact_number: Optional[str] = None
    linkedin_url: Optional[str] = None


class AlumniSendRequest(BaseModel):
    action: str  # 'info_confirmation' | 'feedback_form'
    batch: Optional[str] = None
    student_ids: Optional[List[str]] = None
    survey_year: Optional[str] = None


class ExitSurveySendRequest(BaseModel):
    batch: Optional[str] = None
    student_ids: Optional[List[str]] = None


class CreateEngagementRequest(BaseModel):
    student_id: str
    employer_email: str
    engagement_type: str  # 'internship' | 'job'
    organization_name: Optional[str] = None
    role_designation: Optional[str] = None
    department_served: Optional[str] = None
    supervisor_name: Optional[str] = None
    supervisor_designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    send_invite: bool = True
    graduate_full_name: Optional[str] = None
    graduate_degree_program: Optional[str] = None
    year_of_graduation: Optional[str] = None
    current_job_role: Optional[str] = None
    job_department: Optional[str] = None
    duration_of_employment: Optional[str] = None


class UpdateEngagementRequest(BaseModel):
    organization_name: Optional[str] = None
    department_served: Optional[str] = None
    supervisor_name: Optional[str] = None
    supervisor_designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    graduate_full_name: Optional[str] = None
    graduate_degree_program: Optional[str] = None
    year_of_graduation: Optional[str] = None
    current_job_role: Optional[str] = None
    job_department: Optional[str] = None
    duration_of_employment: Optional[str] = None

# --- Admin authentication ---

@app.post("/api/admin/login")
def admin_login(data: AdminLoginRequest, db: DBSession = Depends(get_db)):
    admin = db.execute(
        text("SELECT id, name, password_hash FROM admins WHERE email = :email"),
        {"email": data.email}
    ).mappings().first()

    if not admin or not verify_password(data.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = secrets.token_urlsafe(32)
    expires_at = now_utc() + timedelta(hours=8)
    db.execute(
        text("INSERT INTO admin_sessions (id, admin_id, token, expires_at) VALUES (:id, :admin_id, :token, :expires)"),
        {"id": str(uuid.uuid4()), "admin_id": admin["id"], "token": token, "expires": expires_at}
    )
    db.commit()
    return {"token": token, "admin_name": admin["name"], "expires_at": expires_at.isoformat()}


@app.get("/api/admin/me")
def get_current_admin_info(admin: dict = Depends(get_current_admin)):
    return {"id": admin.get("id"), "name": admin.get("name"), "email": admin.get("email")}


@app.get("/api/admin/check-setup")
def check_admin_setup(db: DBSession = Depends(get_db)):
    count = db.execute(text("SELECT COUNT(*) AS cnt FROM admins")).mappings().first()
    return {"has_admins": count["cnt"] > 0}


@app.post("/api/admin/setup")
def setup_first_admin(data: AdminSetupRequest, db: DBSession = Depends(get_db)):
    count = db.execute(text("SELECT COUNT(*) AS cnt FROM admins")).mappings().first()
    if count["cnt"] > 0:
        raise HTTPException(status_code=400, detail="Admins already exist. Use create-admin endpoint.")
    admin_id = str(uuid.uuid4())
    try:
        db.execute(
            text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :hash)"),
            {"id": admin_id, "email": data.email, "name": data.name, "hash": hash_password(data.password)}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Admin already exists")
    return {"message": "Admin created", "admin_id": admin_id}


@app.post("/api/admin/create-admin")
def create_admin(data: CreateAdminRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    admin_id = str(uuid.uuid4())
    try:
        db.execute(
            text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :hash)"),
            {"id": admin_id, "email": data.email, "name": data.name, "hash": hash_password(data.password)}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Admin already exists")
    return {"message": "Admin created", "admin_id": admin_id}

# --- Dashboard ---

@app.get("/api/admin/dashboard-stats")
def get_dashboard_stats(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    stats = {}
    queries = [
        ("total_students", "SELECT COUNT(*) FROM students"),
        ("total_employers", "SELECT COUNT(*) FROM employers"),
        ("total_engagements", "SELECT COUNT(*) FROM engagements"),
        ("internship_engagements", "SELECT COUNT(*) FROM engagements WHERE type = 'internship'"),
        ("job_engagements", "SELECT COUNT(*) FROM engagements WHERE type = 'job'"),
        ("proformas_validated", "SELECT COUNT(*) FROM org_proformas WHERE validation_status IN ('validated', 'edited')"),
        ("proformas_pending", "SELECT COUNT(*) FROM org_proformas WHERE validation_status = 'pending'"),
        ("evals_submitted", "SELECT COUNT(*) FROM internship_evaluations WHERE submitted_at IS NOT NULL"),
        ("surveys_submitted", "SELECT COUNT(*) FROM employer_surveys WHERE submitted_at IS NOT NULL"),
    ]
    for key, q in queries:
        try:
            stats[key] = db.execute(text(q)).scalar() or 0
        except Exception:
            db.rollback()
            traceback.print_exc()
            stats[key] = 0
    return stats

# --- Batches ---

@app.get("/api/admin/batches-list")
def list_all_batches_with_status(status: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(text("""
        SELECT DISTINCT s.degree_program, s.batch FROM students s
        WHERE COALESCE(s.batch, '') <> ''
        ORDER BY s.batch DESC, s.degree_program
    """)).mappings().all()
    result = []
    for r in rows:
        batch_status = get_batch_status(r["batch"])
        if status and batch_status != status:
            continue
        result.append({"batch": combine_program_batch(r["degree_program"], r["batch"]), "status": batch_status})
    return result

# --- Students ---

@app.get("/api/admin/students")
def list_students(q: str = "", status: str = "", batch: str = "", page: int = 1, page_size: int = 20,
                  admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    where, params = ["1=1"], {}

    if q:
        where.append("(s.full_name ILIKE :q OR s.enrollment_number ILIKE :q OR s.email ILIKE :q)")
        params["q"] = f"%{q}%"
    add_batch_filter(where, params, batch)
    if status == "intern":
        where.append(INTERN_SQL)
    elif status == "graduate":
        where.append(f"{BATCH_YEAR_SQL} <= :cy")
        params["cy"] = datetime.now().year

    where_sql = " AND ".join(where)
    total = db.execute(text(f"SELECT COUNT(*) FROM students s WHERE {where_sql}"), params).scalar()

    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = db.execute(text(f"""
        SELECT s.id, s.full_name, s.enrollment_number, s.degree_program, s.batch,
               s.current_semester, s.email, {INTERN_SQL} AS is_intern
        FROM students s WHERE {where_sql}
        ORDER BY s.full_name LIMIT :limit OFFSET :offset
    """), params).mappings().all()

    students = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        statuses = []
        if d.pop("is_intern"):
            statuses.append("intern")
        if get_batch_status(d["batch"]) == "graduated":
            statuses.append("graduate")
        d["statuses"] = statuses
        students.append(d)
    return {"students": students, "total": total}


def _validate_student(data: StudentRequest):
    for field in ("full_name", "enrollment_number", "degree_program", "batch"):
        if not (getattr(data, field) or "").strip():
            raise HTTPException(status_code=400, detail="Name, enrollment number, program and batch are required.")
    if data.email and "@" not in data.email:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")


@app.post("/api/admin/students")
def create_student(data: StudentRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    _validate_student(data)
    exists = db.execute(text("SELECT 1 FROM students WHERE enrollment_number = :e"),
                        {"e": data.enrollment_number.strip()}).first()
    if exists:
        raise HTTPException(status_code=400, detail="A student with this enrollment number already exists.")
    student_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO students (id, full_name, enrollment_number, degree_program, batch, current_semester, email)
        VALUES (:id, :name, :enr, :prog, :batch, :sem, :email)
    """), {"id": student_id, "name": data.full_name.strip(), "enr": data.enrollment_number.strip(),
           "prog": data.degree_program.strip(), "batch": data.batch.strip(),
           "sem": (data.current_semester or "").strip() or None, "email": (data.email or "").strip() or None})
    db.commit()
    return {"message": "Student added", "id": student_id}


@app.put("/api/admin/students/{student_id}")
def update_student(student_id: str, data: StudentRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    _validate_student(data)
    clash = db.execute(text("SELECT 1 FROM students WHERE enrollment_number = :e AND id::text <> :id"),
                       {"e": data.enrollment_number.strip(), "id": student_id}).first()
    if clash:
        raise HTTPException(status_code=400, detail="Another student already has this enrollment number.")
    result = db.execute(text("""
        UPDATE students SET full_name = :name, enrollment_number = :enr, degree_program = :prog,
               batch = :batch, current_semester = :sem, email = :email
        WHERE id::text = :id
    """), {"id": student_id, "name": data.full_name.strip(), "enr": data.enrollment_number.strip(),
           "prog": data.degree_program.strip(), "batch": data.batch.strip(),
           "sem": (data.current_semester or "").strip() or None, "email": (data.email or "").strip() or None})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"message": "Student updated"}


@app.delete("/api/admin/students/{student_id}")
def delete_student(student_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    try:
        db.execute(text("DELETE FROM alumni WHERE student_id::text = :id"), {"id": student_id})
        result = db.execute(text("DELETE FROM students WHERE id::text = :id"), {"id": student_id})
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Student not found")
        return {"message": "Student deleted"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        if "foreign key" in str(e).lower():
            raise HTTPException(status_code=400, detail="Student has related records and cannot be deleted.")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/admin/students/bulk")
def bulk_upsert_students(data: BulkStudentsRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    has_obe_col = db.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'students' AND column_name = 'obe_student_id'
    """)).first() is not None

    created, updated, failed = 0, 0, []
    for i, row in enumerate(data.rows, start=2):  # row 1 is the CSV header
        name = (row.get("full_name") or "").strip()
        enr = (row.get("enrollment_number") or "").strip()
        prog = (row.get("degree_program") or "").strip()
        batch = (row.get("batch") or "").strip()
        obe_id = (row.get("obe_student_id") or "").strip() or None
        if not name or not enr:
            failed.append({"row": i, "enrollment_number": enr, "error": "Missing name or enrollment number"})
            continue
        try:
            existing = db.execute(text("SELECT id FROM students WHERE enrollment_number = :e"), {"e": enr}).first()
            params = {"name": name, "enr": enr, "prog": prog, "batch": batch, "obe": obe_id}
            obe_set = ", obe_student_id = COALESCE(:obe, obe_student_id)" if has_obe_col else ""
            if existing:
                params["id"] = existing[0]
                db.execute(text(f"""UPDATE students SET full_name = :name, degree_program = :prog, batch = :batch{obe_set}
                                    WHERE id = :id"""), params)
                updated += 1
            else:
                params["id"] = str(uuid.uuid4())
                cols = "id, full_name, enrollment_number, degree_program, batch" + (", obe_student_id" if has_obe_col else "")
                vals = ":id, :name, :enr, :prog, :batch" + (", :obe" if has_obe_col else "")
                db.execute(text(f"INSERT INTO students ({cols}) VALUES ({vals})"), params)
                created += 1
            db.commit()
        except Exception as e:
            db.rollback()
            failed.append({"row": i, "enrollment_number": enr, "error": str(e).splitlines()[0]})
    return {"created": created, "updated": updated, "failed": failed, "failed_count": len(failed)}

# --- Employers ---

def _validate_employer(data: EmployerRequest):
    if not data.name.strip() or not data.email.strip():
        raise HTTPException(status_code=400, detail="Name and email are required.")
    if "@" not in data.email:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if data.employer_type not in EMPLOYER_TYPES:
        raise HTTPException(status_code=400, detail="Select an employer type.")


EMPLOYER_ORG_SQL = """
    COALESCE(NULLIF(e.organization, ''), (
        SELECT op.organization_name FROM org_proformas op JOIN engagements g ON g.id = op.engagement_id
        WHERE g.employer_id = e.id AND COALESCE(op.organization_name, '') <> ''
        ORDER BY g.created_at DESC LIMIT 1))
"""


@app.get("/api/admin/employers")
def list_employers(q: str = "", page: int = 1, page_size: int = 10,
                   admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Search covers employer name, email, organization, designation, and the
    name or batch of any student linked through an engagement."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    where, params = "1=1", {}
    if q:
        where = """(e.name ILIKE :q OR e.work_email ILIKE :q OR e.organization ILIKE :q OR e.designation ILIKE :q
                   OR EXISTS (SELECT 1 FROM engagements g JOIN org_proformas op ON op.engagement_id = g.id
                              WHERE g.employer_id = e.id AND op.organization_name ILIKE :q)
                   OR EXISTS (SELECT 1 FROM engagements g JOIN students s ON s.id = g.student_id
                              WHERE g.employer_id = e.id
                                AND (s.full_name ILIKE :q OR s.batch ILIKE :q
                                     OR (s.degree_program || ' ' || s.batch) ILIKE :q)))"""
        params["q"] = f"%{q}%"

    total = db.execute(text(f"SELECT COUNT(*) FROM employers e WHERE {where}"), params).scalar()
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = db.execute(text(f"""
        SELECT e.id, e.work_email AS email, e.name, e.designation, e.employer_type, e.created_at,
               {EMPLOYER_ORG_SQL} AS organization,
               (SELECT COUNT(*) FROM engagements g WHERE g.employer_id = e.id) AS total_engagements
        FROM employers e WHERE {where}
        ORDER BY e.created_at DESC NULLS LAST LIMIT :limit OFFSET :offset
    """), params).mappings().all()

    employers = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["created_at"] = iso(d["created_at"])
        employers.append(d)
    return {"employers": employers, "total": total}


@app.get("/api/admin/employers/{employer_id}")
def get_employer(employer_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    emp = db.execute(text(f"""
        SELECT e.id, e.work_email AS email, e.name, e.designation, e.employer_type, e.created_at,
               e.organization AS organization_stored, {EMPLOYER_ORG_SQL} AS organization
        FROM employers e WHERE e.id::text = :id
    """), {"id": employer_id}).mappings().first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employer not found")

    engagements = db.execute(text("""
        SELECT g.id, g.type, g.created_at, s.full_name AS student_name, s.degree_program, s.batch,
               op.organization_name,
               (SELECT MAX(submitted_at) FROM internship_evaluations WHERE engagement_id = g.id) AS eval_at,
               (SELECT MAX(submitted_at) FROM employer_surveys WHERE engagement_id = g.id) AS survey_at
        FROM engagements g
        JOIN students s ON s.id = g.student_id
        LEFT JOIN org_proformas op ON op.engagement_id = g.id
        WHERE g.employer_id::text = :id ORDER BY g.created_at DESC
    """), {"id": employer_id}).mappings().all()

    result = dict(emp)
    result["id"] = str(result["id"])
    result["created_at"] = iso(result["created_at"])
    result["engagements"] = [{
        "id": str(g["id"]),
        "type": str(g["type"]),
        "student_name": g["student_name"],
        "batch": combine_program_batch(g["degree_program"], g["batch"]),
        "organization": g["organization_name"],
        "created_at": iso(g["created_at"]),
        "feedback_submitted": bool(g["eval_at"] if str(g["type"]) == "internship" else g["survey_at"]),
    } for g in engagements]
    return result


@app.post("/api/admin/employers")
def create_employer(data: EmployerRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    _validate_employer(data)
    email = data.email.strip().lower()
    if db.execute(text("SELECT 1 FROM employers WHERE LOWER(work_email) = :email"), {"email": email}).first():
        raise HTTPException(status_code=400, detail="An employer with this email already exists.")
    employer_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO employers (id, work_email, name, designation, organization, employer_type, created_via, created_at)
        VALUES (:id, :email, :name, :desig, :org, :etype, 'admin', NOW())
    """), {"id": employer_id, "email": email, "name": data.name.strip(),
           "desig": (data.designation or "").strip() or None, "org": (data.organization or "").strip() or None,
           "etype": data.employer_type})
    db.commit()
    return {"message": "Employer added", "id": employer_id}


@app.put("/api/admin/employers/{employer_id}")
def update_employer(employer_id: str, data: EmployerRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    _validate_employer(data)
    email = data.email.strip().lower()
    clash = db.execute(text("SELECT 1 FROM employers WHERE LOWER(work_email) = :email AND id::text <> :id"),
                       {"email": email, "id": employer_id}).first()
    if clash:
        raise HTTPException(status_code=400, detail="Another employer already uses this email.")
    result = db.execute(text("""
        UPDATE employers SET work_email = :email, name = :name, designation = :desig,
               organization = :org, employer_type = :etype
        WHERE id::text = :id
    """), {"id": employer_id, "email": email, "name": data.name.strip(),
           "desig": (data.designation or "").strip() or None, "org": (data.organization or "").strip() or None,
           "etype": data.employer_type})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Employer not found")
    return {"message": "Employer updated"}


@app.delete("/api/admin/employers/{employer_id}")
def delete_employer(employer_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    count = db.execute(text("SELECT COUNT(*) FROM engagements WHERE employer_id::text = :id"), {"id": employer_id}).scalar()
    if count:
        raise HTTPException(status_code=400, detail=f"This employer has {count} engagement(s) and cannot be deleted.")
    try:
        result = db.execute(text("DELETE FROM employers WHERE id::text = :id"), {"id": employer_id})
        db.commit()
    except Exception as e:
        db.rollback()
        if "foreign key" in str(e).lower():
            raise HTTPException(status_code=400, detail="Employer has related records and cannot be deleted.")
        raise HTTPException(status_code=400, detail=str(e))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Employer not found")
    return {"message": "Employer deleted"}

# --- Engagements (Internship Supervisor / Graduate Employer) ---

def _merged_employer_type(current: Optional[str], engagement_type: str) -> str:
    new = "internship_supervisor" if engagement_type == "internship" else "graduate_employer"
    if not current or current == new or current == "both":
        return current or new
    return "both"


def _invite_status(submitted_at, has_link, expires_at) -> str:
    if submitted_at:
        return "submitted"
    if not has_link:
        return "not_sent"
    if expires_at and utc(expires_at) < now_utc():
        return "expired"
    return "pending"


def _send_engagement_email(to_email, supervisor_name, engagement_type, student_name, enrollment, token, expires_at, reminder=False):
    link = f"{FRONTEND_URL}/employer/{engagement_type}/{token}"
    action_label = "the internship evaluation form" if engagement_type == "internship" else "the graduate employer survey"
    subject = "Internship Evaluation Request - CSSE, JUW" if engagement_type == "internship" else "Graduate Employer Feedback Request - CSSE, JUW"
    if reminder:
        subject = "Reminder: " + subject
    expiry_text = utc(expires_at).strftime("%d %b %Y, %H:%M UTC")
    html_body = f"""
    <p>Dear {supervisor_name or 'Sir/Madam'},</p>
    {'<p>This is a reminder of our earlier request.</p>' if reminder else ''}
    <p>You are requested to complete {action_label} for {student_name} ({enrollment}).</p>
    <p><a href="{link}">{link}</a></p>
    <p>This link is personalized to you, can only be used once, and expires on {expiry_text}.</p>
    {EMAIL_SIGNATURE}
    """
    sent, error = send_email_smtp(to_email, subject, html_body)
    if not sent:
        print(f"Failed to send engagement email to {to_email}: {error}")
    return sent, link


def _engagement_context(db: DBSession, engagement_id: str) -> dict:
    row = db.execute(text("""
        SELECT g.id, g.type, op.id AS proforma_id, op.invite_token, op.invite_expires_at, op.supervisor_name,
               e.work_email, s.full_name, s.enrollment_number,
               CASE WHEN g.type = 'internship'
                    THEN (SELECT MAX(submitted_at) FROM internship_evaluations WHERE engagement_id = g.id)
                    ELSE (SELECT MAX(submitted_at) FROM employer_surveys WHERE engagement_id = g.id) END AS submitted_at
        FROM engagements g
        JOIN org_proformas op ON op.engagement_id = g.id
        JOIN employers e ON e.id = g.employer_id
        JOIN students s ON s.id = g.student_id
        WHERE g.id::text = :id
    """), {"id": engagement_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Engagement not found")
    d = dict(row)
    d["type"] = str(d["type"])
    return d


def _issue_engagement_link(db: DBSession, ctx: dict):
    token = secrets.token_urlsafe(32)
    expires_at = now_utc() + timedelta(hours=ENGAGEMENT_LINK_HOURS)
    db.execute(text("""UPDATE org_proformas SET invite_token = :token, invite_expires_at = :expires, invite_used_at = NULL
                       WHERE id = :id"""), {"token": token, "expires": expires_at, "id": ctx["proforma_id"]})
    db.commit()
    return _send_engagement_email(ctx["work_email"], ctx["supervisor_name"], ctx["type"],
                                  ctx["full_name"], ctx["enrollment_number"], token, expires_at)


@app.get("/api/admin/engagements")
def list_engagements(page: int = 1, page_size: int = 10, engagement_type: str = "",
                     admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    where, params = "1=1", {}
    if engagement_type:
        where = "g.type = :etype"
        params["etype"] = engagement_type

    total = db.execute(text(f"SELECT COUNT(*) FROM engagements g WHERE {where}"), params).scalar()
    params["limit"] = page_size
    params["offset"] = (page - 1) * page_size
    rows = db.execute(text(f"""
        SELECT g.id, g.type, g.created_at,
               s.full_name AS student_name, s.enrollment_number, s.degree_program, s.batch,
               e.name AS employer_name, e.work_email AS employer_email,
               op.organization_name, op.validation_status,
               op.invite_token IS NOT NULL AS has_link, op.invite_expires_at,
               CASE WHEN g.type = 'internship'
                    THEN (SELECT MAX(submitted_at) FROM internship_evaluations WHERE engagement_id = g.id)
                    ELSE (SELECT MAX(submitted_at) FROM employer_surveys WHERE engagement_id = g.id) END AS submitted_at
        FROM engagements g
        LEFT JOIN org_proformas op ON op.engagement_id = g.id
        LEFT JOIN students s ON s.id = g.student_id
        LEFT JOIN employers e ON e.id = g.employer_id
        WHERE {where}
        ORDER BY g.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).mappings().all()

    engagements = []
    for r in rows:
        created = utc(r["created_at"])
        engagements.append({
            "id": str(r["id"]),
            "type": str(r["type"]),
            "student_name": r["student_name"],
            "enrollment_number": r["enrollment_number"],
            "batch": combine_program_batch(r["degree_program"], r["batch"]),
            "employer_name": r["employer_name"] or r["employer_email"],
            "employer_email": r["employer_email"],
            "organization": r["organization_name"],
            "validation_status": str(r["validation_status"]) if r["validation_status"] else None,
            "status": _invite_status(r["submitted_at"], r["has_link"], r["invite_expires_at"]),
            "link_expires_at": iso(r["invite_expires_at"]),
            "feedback_date": iso(r["submitted_at"]),
            "evaluation_year": created.year if created else None,
            "created_at": iso(created),
        })
    return {"engagements": engagements, "total": total, "page": page, "page_size": page_size}


@app.get("/api/admin/engagements/{engagement_id}")
def get_engagement(engagement_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    row = db.execute(text("""
        SELECT g.id, g.type, s.full_name AS student_name, s.enrollment_number, s.degree_program, s.batch,
               e.work_email AS employer_email, e.name AS employer_name,
               op.organization_name, op.department_served, op.supervisor_name, op.supervisor_designation,
               op.contact_email, op.contact_phone, op.linkedin_url, op.start_date, op.end_date,
               op.graduate_full_name, op.graduate_degree_program, op.year_of_graduation,
               op.current_job_role, op.job_department, op.duration_of_employment
        FROM engagements g
        JOIN students s ON s.id = g.student_id
        JOIN employers e ON e.id = g.employer_id
        LEFT JOIN org_proformas op ON op.engagement_id = g.id
        WHERE g.id::text = :id
    """), {"id": engagement_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Engagement not found")
    d = dict(row)
    d["id"] = str(d["id"])
    d["type"] = str(d["type"])
    d["batch"] = combine_program_batch(d.pop("degree_program"), d["batch"])
    for k in ("start_date", "end_date"):
        d[k] = d[k].isoformat() if d[k] else ""
    return d


def _check_intern_dates(start_date, end_date):
    if not start_date or not end_date:
        raise HTTPException(status_code=400, detail="Internship starting and completion dates are required")
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Dates must be valid dates in YYYY-MM-DD format")
    if not (2000 <= start.year <= 2099 and 2000 <= end.year <= 2099):
        raise HTTPException(status_code=400, detail="Dates must be between 2000 and 2099")
    if end <= start:
        raise HTTPException(status_code=400, detail="Internship completion date must be after the starting date")


@app.post("/api/admin/engagements")
def create_engagement(data: CreateEngagementRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    if data.engagement_type not in ("internship", "job"):
        raise HTTPException(status_code=400, detail="engagement_type must be 'internship' or 'job'")
    employer_email = (data.employer_email or "").strip().lower()
    if "@" not in employer_email:
        raise HTTPException(status_code=400, detail="Enter a valid employer email.")

    student = db.execute(text("SELECT id, full_name, enrollment_number FROM students WHERE id::text = :id"),
                         {"id": data.student_id}).mappings().first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if data.engagement_type == "internship":
        _check_intern_dates(data.start_date, data.end_date)

    # Employers are created on the spot if they don't exist yet; Add employer
    # on the Employers tab is optional, never a prerequisite.
    employer = db.execute(text("SELECT id, employer_type FROM employers WHERE LOWER(work_email) = :email"),
                          {"email": employer_email}).mappings().first()
    if employer:
        employer_id = employer["id"]
        db.execute(text("""
            UPDATE employers SET name = COALESCE(NULLIF(:name, ''), name),
                   designation = COALESCE(NULLIF(:desig, ''), designation),
                   organization = COALESCE(NULLIF(organization, ''), NULLIF(:org, '')),
                   employer_type = :etype
            WHERE id = :id
        """), {"name": data.supervisor_name or "", "desig": data.supervisor_designation or "",
               "org": data.organization_name or "", "id": employer_id,
               "etype": _merged_employer_type(employer["employer_type"], data.engagement_type)})
    else:
        employer_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO employers (id, work_email, name, designation, organization, employer_type, created_via, created_at)
            VALUES (:id, :email, :name, :desig, :org, :etype, 'admin', NOW())
        """), {"id": employer_id, "email": employer_email, "name": data.supervisor_name,
               "desig": data.supervisor_designation, "org": data.organization_name or None,
               "etype": _merged_employer_type(None, data.engagement_type)})

    engagement_id = str(uuid.uuid4())
    db.execute(text("""INSERT INTO engagements (id, student_id, employer_id, type, status, created_at)
                       VALUES (:id, :student_id, :employer_id, :type, 'active', NOW())"""),
               {"id": engagement_id, "student_id": student["id"], "employer_id": employer_id, "type": data.engagement_type})

    proforma_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO org_proformas (
            id, engagement_id, organization_name, role_designation, department_served,
            supervisor_name, supervisor_designation, contact_email, contact_phone,
            linkedin_url, start_date, end_date, validation_status,
            graduate_full_name, graduate_degree_program, year_of_graduation,
            current_job_role, job_department, duration_of_employment
        ) VALUES (
            :id, :engagement_id, :org, :role, :dept,
            :supervisor, :supervisor_desig, :contact_email, :contact_phone,
            :linkedin, :start_date, :end_date, 'pending',
            :grad_name, :grad_prog, :grad_year, :grad_role, :grad_dept, :grad_duration
        )
    """), {
        "id": proforma_id, "engagement_id": engagement_id,
        "org": data.organization_name, "role": data.role_designation, "dept": data.department_served,
        "supervisor": data.supervisor_name, "supervisor_desig": data.supervisor_designation,
        "contact_email": data.contact_email or employer_email, "contact_phone": data.contact_phone,
        "linkedin": data.linkedin_url,
        "start_date": data.start_date or None, "end_date": data.end_date or None,
        "grad_name": data.graduate_full_name, "grad_prog": data.graduate_degree_program,
        "grad_year": data.year_of_graduation, "grad_role": data.current_job_role,
        "grad_dept": data.job_department, "grad_duration": data.duration_of_employment,
    })
    db.commit()

    sent, manual_url = False, None
    if data.send_invite:
        ctx = _engagement_context(db, engagement_id)
        sent, link = _issue_engagement_link(db, ctx)
        if not sent:
            manual_url = link

    if not data.send_invite:
        message = "Engagement created. Use Send link from the table when ready."
    elif sent:
        message = "Engagement created and invitation sent."
    else:
        message = "Engagement created, but the email could not be sent. Use the manual link below."
    return {"message": message, "engagement_id": engagement_id, "sent": sent, "manual_url": manual_url}


@app.put("/api/admin/engagements/{engagement_id}")
def update_engagement(engagement_id: str, data: UpdateEngagementRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    ctx = _engagement_context(db, engagement_id)
    if ctx["submitted_at"]:
        raise HTTPException(status_code=400, detail="Feedback is already submitted; this engagement can no longer be modified.")
    if ctx["type"] == "internship":
        _check_intern_dates(data.start_date, data.end_date)
    db.execute(text("""
        UPDATE org_proformas SET
            organization_name = :org, department_served = :dept,
            supervisor_name = :supervisor, supervisor_designation = :supervisor_desig,
            contact_email = :contact_email, contact_phone = :contact_phone, linkedin_url = :linkedin,
            start_date = :start_date, end_date = :end_date,
            graduate_full_name = :grad_name, graduate_degree_program = :grad_prog,
            year_of_graduation = :grad_year, current_job_role = :grad_role,
            job_department = :grad_dept, duration_of_employment = :grad_duration
        WHERE id = :id
    """), {
        "id": ctx["proforma_id"],
        "org": data.organization_name, "dept": data.department_served,
        "supervisor": data.supervisor_name, "supervisor_desig": data.supervisor_designation,
        "contact_email": data.contact_email, "contact_phone": data.contact_phone, "linkedin": data.linkedin_url,
        "start_date": data.start_date or None, "end_date": data.end_date or None,
        "grad_name": data.graduate_full_name, "grad_prog": data.graduate_degree_program,
        "grad_year": data.year_of_graduation, "grad_role": data.current_job_role,
        "grad_dept": data.job_department, "grad_duration": data.duration_of_employment,
    })
    db.commit()
    return {"message": "Engagement updated."}


@app.post("/api/admin/engagements/{engagement_id}/resend")
def resend_engagement_link(engagement_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Re-email the same link. Only allowed while it is unexpired and unsubmitted."""
    ctx = _engagement_context(db, engagement_id)
    status = _invite_status(ctx["submitted_at"], ctx["invite_token"] is not None, ctx["invite_expires_at"])
    if status != "pending":
        raise HTTPException(status_code=400, detail={
            "submitted": "Feedback is already submitted.",
            "not_sent": "No link has been sent yet. Use Send link.",
            "expired": "This link has expired. Use Send fresh link.",
        }[status])
    sent, link = _send_engagement_email(ctx["work_email"], ctx["supervisor_name"], ctx["type"], ctx["full_name"],
                                        ctx["enrollment_number"], ctx["invite_token"], ctx["invite_expires_at"], reminder=True)
    return {"sent": sent, "message": "Email sent." if sent else "Email could not be sent.", "manual_url": None if sent else link}


@app.post("/api/admin/engagements/{engagement_id}/fresh-link")
def fresh_engagement_link(engagement_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Issue a new 48h link (old one stops working). Used for expired or never-sent links."""
    ctx = _engagement_context(db, engagement_id)
    if ctx["submitted_at"]:
        raise HTTPException(status_code=400, detail="Feedback is already submitted.")
    sent, link = _issue_engagement_link(db, ctx)
    return {"sent": sent, "message": "Fresh link sent." if sent else "Link created, but the email could not be sent.",
            "manual_url": None if sent else link}


@app.get("/api/admin/engagements/invite/{token}")
def resolve_engagement_invite(token: str, db: DBSession = Depends(get_db)):
    """Check an employer-facing link without consuming it."""
    row = db.execute(text("""SELECT op.id, op.invite_expires_at, op.invite_used_at, op.validation_status, g.type AS engagement_type
                             FROM org_proformas op JOIN engagements g ON op.engagement_id = g.id
                             WHERE op.invite_token = :token"""), {"token": token}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid link")
    if row["invite_used_at"] is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")
    if row["invite_expires_at"] and now_utc() > utc(row["invite_expires_at"]):
        raise HTTPException(status_code=410, detail="This link has expired")
    return {"proforma_id": row["id"], "engagement_type": str(row["engagement_type"]),
            "validation_status": str(row["validation_status"]) if row["validation_status"] else None}


def consume_engagement_invite(db: DBSession, token: str) -> dict:
    """Mark an employer-facing link as used. To be called by the submission endpoint."""
    row = db.execute(text("SELECT id, invite_expires_at, invite_used_at FROM org_proformas WHERE invite_token = :token"),
                     {"token": token}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid link")
    if row["invite_used_at"] is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")
    if row["invite_expires_at"] and now_utc() > utc(row["invite_expires_at"]):
        raise HTTPException(status_code=410, detail="This link has expired")
    result = db.execute(text("UPDATE org_proformas SET invite_used_at = NOW() WHERE id = :id AND invite_used_at IS NULL"),
                        {"id": row["id"]})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=410, detail="This link has already been used")
    return dict(row)

# --- Exit Survey (merged) ---

EXIT_STUDENTS_SQL = """
    SELECT s.id AS student_id, s.full_name, s.enrollment_number, s.degree_program, s.batch,
           COALESCE(NULLIF(s.email, ''), a.email) AS email,
           f.id AS form_id, f.token, f.status AS form_status, f.reminder_count, f.expires_at,
           f.created_at AS initiated_at, f.submitted_at,
           aes.submitted_at AS legacy_submitted_at
    FROM students s
    LEFT JOIN alumni a ON a.student_id = s.id
    LEFT JOIN student_exit_forms f ON f.student_id = s.id
    LEFT JOIN alumni_exit_surveys aes ON aes.alumnus_id = a.id
"""


def _exit_row_status(r) -> dict:
    """Returns status (not_sent | initiated | submitted | expired) and whether the
    current link is still active (re-email allowed)."""
    if r["submitted_at"] or r["legacy_submitted_at"] or r["form_status"] == "submitted":
        return {"status": "submitted", "link_active": False}
    if not r["form_id"]:
        return {"status": "not_sent", "link_active": False}
    active = r["expires_at"] is not None and utc(r["expires_at"]) > now_utc()
    if active:
        return {"status": "initiated", "link_active": True}
    if r["form_status"] == "expired" or (r["reminder_count"] or 0) >= EXIT_FORM_MAX_REMINDERS:
        return {"status": "expired", "link_active": False}
    return {"status": "initiated", "link_active": False}  # expired attempt, automatic reminder due


def _send_exit_form_email(name, email, token, expires_at, kind):
    link = f"{FRONTEND_URL}/student/exit-form/{token}"
    subject = {"initial": "Exit survey - CSSE, JUW",
               "reminder": "Reminder: Exit survey - CSSE, JUW",
               "resend": "Reminder: Exit survey - CSSE, JUW",
               "fresh": "Exit survey (new link) - CSSE, JUW"}[kind]
    note = {"initial": "",
            "reminder": "<p>Your previous link expired before the survey was submitted, so here is a new one.</p>",
            "resend": "<p>This is a reminder to complete your exit survey.</p>",
            "fresh": "<p>A new link has been issued for your exit survey. Earlier links no longer work.</p>"}[kind]
    expiry_text = utc(expires_at).strftime("%d %b %Y, %H:%M UTC")
    html_body = f"""
    <p>Dear {name},</p>
    {note}
    <p>Please complete your exit survey using the link below. The link is personalized to you,
    can only be used once, and expires on {expiry_text}.</p>
    <p><a href="{link}">{link}</a></p>
    {EMAIL_SIGNATURE}
    """
    sent, error = send_email_smtp(email, subject, html_body)
    if not sent:
        print(f"Failed to send exit survey email to {email}: {error}")
    return sent, link


def _issue_exit_form_link(db: DBSession, form_id: str, name: str, email: str, kind: str):
    token = secrets.token_urlsafe(32)
    expires_at = now_utc() + timedelta(hours=EXIT_FORM_WINDOW_HOURS)
    db.execute(text("""UPDATE student_exit_forms SET token = :token, expires_at = :expires, used_at = NULL, status = 'pending'
                       WHERE id = :id"""), {"token": token, "expires": expires_at, "id": form_id})
    db.commit()
    return _send_exit_form_email(name, email, token, expires_at, kind)


def _exit_row(db: DBSession, form_id: str):
    row = db.execute(text(EXIT_STUDENTS_SQL + " WHERE f.id = :id"), {"id": form_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Exit survey record not found")
    return row


@app.get("/api/admin/exit-survey/students")
def list_exit_survey_students(batch: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    where, params = [], {}
    add_batch_filter(where, params, batch)
    if not where:
        raise HTTPException(status_code=400, detail="Select a batch")
    rows = db.execute(text(EXIT_STUDENTS_SQL + " WHERE " + " AND ".join(where) + " ORDER BY s.full_name"), params).mappings().all()
    result = []
    for r in rows:
        st = _exit_row_status(r)
        result.append({
            "student_id": str(r["student_id"]), "form_id": r["form_id"],
            "full_name": r["full_name"], "enrollment_number": r["enrollment_number"],
            "batch": combine_program_batch(r["degree_program"], r["batch"]), "email": r["email"],
            "initiated_at": iso(r["initiated_at"]), "reminder_count": r["reminder_count"] or 0,
            "submitted_at": iso(r["submitted_at"] or r["legacy_submitted_at"]),
            **st,
        })
    return result


@app.post("/api/admin/exit-survey/send")
def send_exit_survey(data: ExitSurveySendRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """First send for students who have not received the survey yet. Students
    already sent a link are handled with Re-email or Send fresh link."""
    where, params = [], {}
    if data.student_ids:
        where.append("s.id::text = ANY(:ids)")
        params["ids"] = data.student_ids
    else:
        add_batch_filter(where, params, data.batch)
    if not where:
        raise HTTPException(status_code=400, detail="Select a batch or students")
    rows = db.execute(text(EXIT_STUDENTS_SQL + " WHERE " + " AND ".join(where)), params).mappings().all()

    sent, failed, already, no_email = 0, 0, 0, []
    manual_url = None
    for r in rows:
        if _exit_row_status(r)["status"] != "not_sent":
            already += 1
            continue
        if not r["email"]:
            no_email.append(r["full_name"])
            continue
        form_id = str(uuid.uuid4())
        db.execute(text("""INSERT INTO student_exit_forms (id, student_id, token, status, expires_at)
                           VALUES (:id, :sid, :tmp, 'pending', NOW())"""),
                   {"id": form_id, "sid": r["student_id"], "tmp": secrets.token_urlsafe(16)})
        db.commit()
        ok, link = _issue_exit_form_link(db, form_id, r["full_name"], r["email"], "initial")
        if ok:
            sent += 1
        else:
            failed += 1
            manual_url = link if len(rows) == 1 else None
    return {"sent": sent, "failed": failed, "skipped_already_sent": already,
            "skipped_no_email": len(no_email), "no_email_names": no_email[:10], "manual_url": manual_url}


@app.post("/api/admin/exit-survey/{form_id}/resend")
def resend_exit_survey(form_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    r = _exit_row(db, form_id)
    st = _exit_row_status(r)
    if not st["link_active"]:
        raise HTTPException(status_code=400, detail="The current link is no longer active. Use Send fresh link.")
    if not r["email"]:
        raise HTTPException(status_code=400, detail="Student has no email on file.")
    sent, link = _send_exit_form_email(r["full_name"], r["email"], r["token"], r["expires_at"], "resend")
    return {"sent": sent, "message": "Email sent." if sent else "Email could not be sent.", "manual_url": None if sent else link}


@app.post("/api/admin/exit-survey/{form_id}/fresh-link")
def fresh_exit_survey_link(form_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Manual fresh 72h link. Does not reset the automatic reminder count."""
    r = _exit_row(db, form_id)
    if _exit_row_status(r)["status"] == "submitted":
        raise HTTPException(status_code=400, detail="Exit survey is already submitted.")
    if not r["email"]:
        raise HTTPException(status_code=400, detail="Student has no email on file.")
    sent, link = _issue_exit_form_link(db, form_id, r["full_name"], r["email"], "fresh")
    return {"sent": sent, "message": "Fresh link sent." if sent else "Link created, but the email could not be sent.",
            "manual_url": None if sent else link}


def run_exit_form_reminders(db: DBSession) -> dict:
    """Expired, unsubmitted links get a fresh 72h link, up to 3 times. After the
    third reminder expires the record is marked 'expired'."""
    due = db.execute(text("""
        SELECT f.id AS form_id, f.reminder_count, s.full_name,
               COALESCE(NULLIF(s.email, ''), a.email) AS email
        FROM student_exit_forms f
        JOIN students s ON s.id = f.student_id
        LEFT JOIN alumni a ON a.student_id = s.id
        WHERE f.status = 'pending' AND f.submitted_at IS NULL AND f.used_at IS NULL AND f.expires_at <= NOW()
    """)).mappings().all()

    sent, failed, expired = 0, 0, 0
    for r in due:
        if (r["reminder_count"] or 0) >= EXIT_FORM_MAX_REMINDERS:
            db.execute(text("UPDATE student_exit_forms SET status = 'expired' WHERE id = :id"), {"id": r["form_id"]})
            db.commit()
            expired += 1
            continue
        if not r["email"]:
            continue
        ok, _ = _issue_exit_form_link(db, r["form_id"], r["full_name"], r["email"], "reminder")
        db.execute(text("UPDATE student_exit_forms SET reminder_count = reminder_count + 1 WHERE id = :id"), {"id": r["form_id"]})
        db.commit()
        if ok:
            sent += 1
        else:
            failed += 1
    return {"reminders_sent": sent, "failed": failed, "marked_expired": expired}


@app.post("/api/admin/exit-survey/run-reminders")
def trigger_exit_form_reminders(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    return run_exit_form_reminders(db)


def _run_exit_reminders_once():
    db = SessionLocal()
    try:
        result = run_exit_form_reminders(db)
        if result["reminders_sent"] or result["failed"] or result["marked_expired"]:
            print(f"[EXIT REMINDERS] {result}")
    finally:
        db.close()


async def _exit_reminder_loop():
    while True:
        try:
            await asyncio.to_thread(_run_exit_reminders_once)
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(EXIT_REMINDER_INTERVAL_MINUTES * 60)


@app.on_event("startup")
async def start_background_jobs():
    asyncio.create_task(_exit_reminder_loop())

# --- Alumni Feedback ---

ALUMNI_EXPIRY_HOURS = {"info_confirmation": 48, "feedback_form": 24 * 7}


def _ensure_alumnus(db: DBSession, student_id, email: str):
    existing = db.execute(text("SELECT id FROM alumni WHERE student_id = :sid"), {"sid": student_id}).mappings().first()
    if existing:
        return existing["id"]
    alumnus_id = str(uuid.uuid4())
    db.execute(text("INSERT INTO alumni (id, student_id, email) VALUES (:id, :sid, :email)"),
               {"id": alumnus_id, "sid": student_id, "email": email})
    db.execute(text("""INSERT INTO alumni_survey_schedule (id, alumnus_id, next_send_at, interval_months)
                       VALUES (:id, :aid, :next_send, 8) ON CONFLICT (alumnus_id) DO NOTHING"""),
               {"id": str(uuid.uuid4()), "aid": alumnus_id, "next_send": now_utc() + timedelta(days=8 * 30)})
    db.commit()
    return alumnus_id


def _issue_alumni_action_link(db: DBSession, alumnus_id, name: str, email: str, action_type: str, target_id: str = None):
    token = secrets.token_urlsafe(32)
    expiry_hours = ALUMNI_EXPIRY_HOURS.get(action_type, 48)
    expires_at = now_utc() + timedelta(hours=expiry_hours)
    db.execute(text("""INSERT INTO alumni_action_links (id, alumnus_id, action_type, target_id, token, expires_at)
                       VALUES (:id, :aid, :action, :target, :token, :expires)"""),
               {"id": str(uuid.uuid4()), "aid": alumnus_id, "action": action_type, "target": target_id,
                "token": token, "expires": expires_at})
    db.commit()

    link = f"{FRONTEND_URL}/alumni/{action_type}/{token}"
    action_label = {"info_confirmation": "confirm your contact information",
                    "feedback_form": "share your alumni feedback"}.get(action_type, "complete the requested form")
    subject = {"info_confirmation": "Please confirm your information",
               "feedback_form": "Alumni feedback form"}.get(action_type, "Action required") + " - CSSE, JUW"
    expiry_copy = "1 week" if expiry_hours == 24 * 7 else f"{expiry_hours} hours"
    html_body = f"""
    <p>Dear {name},</p>
    <p>Please click the link below to {action_label}:</p>
    <p><a href="{link}">{link}</a></p>
    <p>This link is personalized to you, will expire in {expiry_copy}, and can only be used once.</p>
    {EMAIL_SIGNATURE}
    """
    sent, error = send_email_smtp(email, subject, html_body)
    if not sent:
        print(f"Failed to send {action_type} email to {email}: {error}")
    return sent, link


def _consume_alumni_action_link(db: DBSession, token: str, expected_action_type: str = None):
    """Validate and consume a single-use alumni link. To be called by the submission endpoints."""
    row = db.execute(text("SELECT * FROM alumni_action_links WHERE token = :token"), {"token": token}).mappings().first()
    if not row or (expected_action_type and row["action_type"] != expected_action_type):
        raise HTTPException(status_code=404, detail="Invalid link")
    if row["used_at"] is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")
    if now_utc() > utc(row["expires_at"]):
        raise HTTPException(status_code=410, detail="This link has expired")
    result = db.execute(text("UPDATE alumni_action_links SET used_at = NOW() WHERE token = :token AND used_at IS NULL"),
                        {"token": token})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=410, detail="This link has already been used")
    return dict(row)


ALUMNI_STUDENTS_SQL = """
    SELECT s.id AS student_id, s.full_name, s.enrollment_number, s.degree_program, s.batch,
           COALESCE(a.email, NULLIF(s.email, '')) AS email, a.id AS alumnus_id,
           ic.submitted_at AS info_submitted_at,
           (SELECT MAX(l.created_at) FROM alumni_action_links l
             WHERE l.alumnus_id = a.id AND l.action_type = 'info_confirmation') AS info_sent_at,
           (SELECT COUNT(*) FROM alumni_feedback_forms f WHERE f.alumnus_id = a.id) AS feedback_sent,
           (SELECT COUNT(*) FROM alumni_feedback_forms f WHERE f.alumnus_id = a.id AND f.submitted_at IS NOT NULL) AS feedback_completed
    FROM students s
    LEFT JOIN alumni a ON a.student_id = s.id
    LEFT JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id
"""


@app.get("/api/admin/alumni/students")
def list_alumni_batch_students(batch: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    where, params = [], {}
    add_batch_filter(where, params, batch)
    if not where:
        raise HTTPException(status_code=400, detail="Select a batch")
    rows = db.execute(text(ALUMNI_STUDENTS_SQL + " WHERE " + " AND ".join(where) + " ORDER BY s.full_name"), params).mappings().all()
    result = []
    for r in rows:
        if r["info_submitted_at"]:
            info_status = "confirmed"
        elif r["info_sent_at"]:
            info_status = "sent"
        else:
            info_status = "not_sent"
        result.append({
            "student_id": str(r["student_id"]), "full_name": r["full_name"],
            "enrollment_number": r["enrollment_number"],
            "batch": combine_program_batch(r["degree_program"], r["batch"]),
            "email": r["email"], "info_status": info_status,
            "feedback_sent": r["feedback_sent"] or 0, "feedback_completed": r["feedback_completed"] or 0,
        })
    return result


@app.post("/api/admin/alumni/send")
def send_alumni_forms(data: AlumniSendRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Info confirmation or alumni feedback form, to a whole batch or to selected students."""
    if data.action not in ("info_confirmation", "feedback_form"):
        raise HTTPException(status_code=400, detail="Unknown action")
    survey_year = (data.survey_year or "").strip()
    if data.action == "feedback_form" and not survey_year:
        raise HTTPException(status_code=400, detail="Enter a survey year")

    where, params = [], {}
    individual = bool(data.student_ids)
    if individual:
        where.append("s.id::text = ANY(:ids)")
        params["ids"] = data.student_ids
    else:
        add_batch_filter(where, params, data.batch)
    if not where:
        raise HTTPException(status_code=400, detail="Select a batch or students")
    rows = db.execute(text(ALUMNI_STUDENTS_SQL + " WHERE " + " AND ".join(where)), params).mappings().all()

    sent, failed, skipped, no_email = 0, 0, 0, []
    manual_url = None
    for r in rows:
        if not r["email"]:
            no_email.append(r["full_name"])
            continue
        alumnus_id = r["alumnus_id"] or _ensure_alumnus(db, r["student_id"], r["email"])

        if data.action == "info_confirmation":
            if r["info_submitted_at"]:
                skipped += 1
                continue
            ok, link = _issue_alumni_action_link(db, alumnus_id, r["full_name"], r["email"], "info_confirmation")
        else:
            form = db.execute(text("SELECT id, submitted_at FROM alumni_feedback_forms WHERE alumnus_id = :aid AND survey_year = :year"),
                              {"aid": alumnus_id, "year": survey_year}).mappings().first()
            if form and (form["submitted_at"] or not individual):
                skipped += 1
                continue
            form_id = form["id"] if form else str(uuid.uuid4())
            if not form:
                db.execute(text("INSERT INTO alumni_feedback_forms (id, alumnus_id, survey_year) VALUES (:id, :aid, :year)"),
                           {"id": form_id, "aid": alumnus_id, "year": survey_year})
                db.commit()
            ok, link = _issue_alumni_action_link(db, alumnus_id, r["full_name"], r["email"], "feedback_form", target_id=form_id)
            db.execute(text("""UPDATE alumni_survey_schedule SET last_sent_at = NOW(), next_send_at = :next_send
                               WHERE alumnus_id = :aid"""),
                       {"aid": alumnus_id, "next_send": now_utc() + timedelta(days=8 * 30)})
            db.commit()
        if ok:
            sent += 1
        else:
            failed += 1
            manual_url = link if len(rows) == 1 else None

    return {"sent": sent, "failed": failed, "skipped": skipped,
            "skipped_no_email": len(no_email), "no_email_names": no_email[:10], "manual_url": manual_url}


@app.get("/api/admin/alumni/recent")
def list_recent_alumni_sends(page: int = 1, page_size: int = 10, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    base = "FROM alumni_action_links l WHERE l.action_type IN ('info_confirmation', 'feedback_form')"
    total = db.execute(text(f"SELECT COUNT(*) {base}")).scalar()
    rows = db.execute(text("""
        SELECT l.action_type, l.created_at, l.expires_at, l.used_at,
               s.full_name, s.degree_program, s.batch, f.survey_year, f.submitted_at AS form_submitted_at
        FROM alumni_action_links l
        JOIN alumni a ON a.id = l.alumnus_id
        JOIN students s ON s.id = a.student_id
        LEFT JOIN alumni_feedback_forms f ON l.action_type = 'feedback_form' AND f.id = l.target_id
        WHERE l.action_type IN ('info_confirmation', 'feedback_form')
        ORDER BY l.created_at DESC LIMIT :limit OFFSET :offset
    """), {"limit": page_size, "offset": (page - 1) * page_size}).mappings().all()

    items = []
    for r in rows:
        if r["used_at"] or r["form_submitted_at"]:
            status = "submitted"
        elif utc(r["expires_at"]) < now_utc():
            status = "expired"
        else:
            status = "pending"
        items.append({
            "full_name": r["full_name"],
            "batch": combine_program_batch(r["degree_program"], r["batch"]),
            "form": "Info confirmation" if r["action_type"] == "info_confirmation" else f"Alumni feedback {r['survey_year'] or ''}".strip(),
            "sent_at": iso(r["created_at"]),
            "status": status,
        })
    return {"items": items, "total": total}


@app.post("/api/admin/alumni/save")
def save_alumni_record(data: AlumniSaveRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    student = db.execute(text("SELECT id FROM students WHERE id::text = :sid"), {"sid": data.student_id}).mappings().first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    alumnus_id = _ensure_alumnus(db, student["id"], data.email)
    db.execute(text("UPDATE alumni SET email = :email, contact_number = :contact, linkedin_url = :li WHERE id = :id"),
               {"email": data.email, "contact": data.contact_number, "li": data.linkedin_url, "id": alumnus_id})
    db.commit()
    return {"message": "Alumnus record saved", "alumnus_id": str(alumnus_id)}


def run_recurring_alumni_survey_dispatch(db: DBSession) -> dict:
    """Send the alumni survey to everyone whose next_send_at has passed. Not
    automated yet: the 8-vs-12-month cycle is still to be confirmed."""
    due = db.execute(text("""
        SELECT sch.id AS schedule_id, sch.alumnus_id, a.email, s.full_name
        FROM alumni_survey_schedule sch
        JOIN alumni a ON a.id = sch.alumnus_id
        JOIN students s ON s.id = a.student_id
        WHERE sch.next_send_at <= NOW()
    """)).mappings().all()

    survey_year = str(now_utc().year)
    sent, failed = 0, 0
    for r in due:
        existing = db.execute(text("SELECT id FROM alumni_feedback_forms WHERE alumnus_id = :aid AND survey_year = :year"),
                              {"aid": r["alumnus_id"], "year": survey_year}).mappings().first()
        form_id = existing["id"] if existing else str(uuid.uuid4())
        if not existing:
            db.execute(text("INSERT INTO alumni_feedback_forms (id, alumnus_id, survey_year) VALUES (:id, :aid, :year)"),
                       {"id": form_id, "aid": r["alumnus_id"], "year": survey_year})
            db.commit()
        ok, _ = _issue_alumni_action_link(db, r["alumnus_id"], r["full_name"], r["email"], "feedback_form", target_id=form_id)
        sent, failed = (sent + 1, failed) if ok else (sent, failed + 1)
        db.execute(text("UPDATE alumni_survey_schedule SET last_sent_at = NOW(), next_send_at = :next_send WHERE id = :sid"),
                   {"sid": r["schedule_id"], "next_send": now_utc() + timedelta(days=8 * 30)})
        db.commit()
    return {"sent": sent, "failed": failed, "checked": len(due)}


@app.post("/api/admin/alumni/campaigns/run-recurring-dispatch")
def trigger_recurring_alumni_survey_dispatch(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    return run_recurring_alumni_survey_dispatch(db)

# --- Bulk email ---

@app.post("/api/admin/send-email")
def send_email(data: dict, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    audience = data.get("audience", "all_employers")
    subject = data.get("subject", "")
    body = data.get("body", "")

    if audience == "specific":
        emails = [e for e in data.get("emails", []) if e]
    elif audience in ("intern_employers", "graduate_employers"):
        etype = "internship" if audience == "intern_employers" else "job"
        rows = db.execute(text("""SELECT DISTINCT e.work_email AS email FROM employers e
                                  JOIN engagements g ON g.employer_id = e.id WHERE g.type = :etype"""),
                          {"etype": etype}).mappings().all()
        emails = [r["email"] for r in rows]
    else:
        rows = db.execute(text("SELECT DISTINCT work_email AS email FROM employers")).mappings().all()
        emails = [r["email"] for r in rows]

    sent, failed = 0, 0
    for email in emails:
        ok, _ = send_email_smtp(email, subject, body)
        sent, failed = (sent + 1, failed) if ok else (sent, failed + 1)
    return {"sent": sent, "failed": failed, "total": len(emails)}

# --- Serve frontend ---

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/admin")
    def serve_admin():
        admin_path = os.path.join(STATIC_DIR, "admin.html")
        if os.path.isfile(admin_path):
            return FileResponse(admin_path)
        return HTMLResponse("<h1>Admin page not found</h1>", status_code=404)

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        file_path = os.path.join(STATIC_DIR, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))