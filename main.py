from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as DBSession
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone, date
from typing import Optional
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

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

# --- Startup diagnostics ---
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
    """
    Send a single email via SMTP. Returns (success, error_message).
    Requires SMTP_HOST, SMTP_USER, SMTP_PASSWORD to be set in the environment;
    if they're missing, this is a no-op that reports the misconfiguration
    instead of silently pretending the email went out.
    """
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

# --- Batch status helper ---

def get_batch_status(batch_year: str) -> str:
    try:
        year_str = batch_year.split()[-1]
        batch_year_int = int(year_str)
        return "graduated" if batch_year_int <= datetime.now().year else "active"
    except (ValueError, IndexError):
        return "active"

# --- Combined program+batch helpers ---

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

# --- Startup migrations ---

@app.on_event("startup")
def ensure_columns():
    db = SessionLocal()
    try:
        migrations = [
            ("employers", "password_hash", "VARCHAR"),
            ("internship_evaluations", "rating_work_quality", "SMALLINT"),
            ("internship_evaluations", "rating_task_completion", "SMALLINT"),
            ("internship_evaluations", "rating_overall_competence", "SMALLINT"),
            ("employer_surveys", "overall_performance", "VARCHAR"),
            ("org_proformas", "linkedin_url", "VARCHAR"),
            ("employer_surveys", "year_of_graduation", "VARCHAR"),
            # Graduate info fields, editable inline in the admin panel when
            # initiating a graduate-employer engagement (per Feb 2026 revision).
            ("org_proformas", "graduate_full_name", "VARCHAR"),
            ("org_proformas", "graduate_degree_program", "VARCHAR"),
            ("org_proformas", "year_of_graduation", "VARCHAR"),
            ("org_proformas", "current_job_role", "VARCHAR"),
            ("org_proformas", "job_department", "VARCHAR"),
            ("org_proformas", "duration_of_employment", "VARCHAR"),
            # Invite-token lifecycle fields (expiry + single-use), matching
            # what alumni_action_links already had. Applies to both the
            # Internship Supervisor and Graduate Employer employer-facing links.
            ("org_proformas", "invite_token", "VARCHAR"),
            ("org_proformas", "invite_expires_at", "TIMESTAMP"),
            ("org_proformas", "invite_used_at", "TIMESTAMP"),
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

        # --- Student exit form (pre-graduation, distinct from the alumni
        # exit survey). Student gets a link on email that must be submitted
        # within 72 hours; if not, a new link is auto-issued, up to 3
        # reminder emails, after which it's left pending for manual follow-up.
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

        # --- Recurring alumni survey schedule. One row per alumnus tracks
        # when their next survey email is due (every 8 months, or after a
        # year -- interpreted as: send at 8 months, then again 12 months
        # after that, i.e. a repeating cycle) and when it was last sent.
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

    if not session or datetime.now(timezone.utc) > session["expires_at"].replace(tzinfo=timezone.utc):
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

class AlumniSaveRequest(BaseModel):
    student_id: str
    email: str
    contact_number: Optional[str] = None
    linkedin_url: Optional[str] = None

class AlumniCampaignRequest(BaseModel):
    batch: str

class AlumniFeedbackCampaignRequest(BaseModel):
    batch: str
    survey_year: Optional[str] = None

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
    # Graduate-only fields (engagement_type == 'job'), editable inline in the
    # admin panel rather than collected from the employer separately.
    graduate_full_name: Optional[str] = None
    graduate_degree_program: Optional[str] = None
    year_of_graduation: Optional[str] = None
    current_job_role: Optional[str] = None
    job_department: Optional[str] = None
    duration_of_employment: Optional[str] = None

class InitiateStudentExitFormRequest(BaseModel):
    student_id: str

# --- Admin Authentication ---

@app.post("/api/admin/login")
def admin_login(data: AdminLoginRequest, db: DBSession = Depends(get_db)):
    admin = db.execute(
        text("SELECT id, name, password_hash FROM admins WHERE email = :email"),
        {"email": data.email}
    ).mappings().first()

    if not admin or not verify_password(data.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
    session_id = str(uuid.uuid4())

    db.execute(
        text("INSERT INTO admin_sessions (id, admin_id, token, expires_at) VALUES (:id, :admin_id, :token, :expires)"),
        {"id": session_id, "admin_id": admin["id"], "token": token, "expires": expires_at}
    )
    db.commit()

    return {"token": token, "admin_name": admin["name"], "expires_at": expires_at.isoformat()}


@app.get("/api/admin/me")
def get_current_admin_info(admin: dict = Depends(get_current_admin)):
    return {"id": admin.get("id"), "name": admin.get("name"), "email": admin.get("email")}


@app.get("/api/admin/check-setup")
def check_admin_setup(db: DBSession = Depends(get_db)):
    count = db.execute(text("SELECT COUNT(*) as cnt FROM admins")).mappings().first()
    return {"has_admins": count["cnt"] > 0}


@app.post("/api/admin/setup")
def setup_first_admin(data: AdminSetupRequest, db: DBSession = Depends(get_db)):
    count = db.execute(text("SELECT COUNT(*) as cnt FROM admins")).mappings().first()
    if count["cnt"] > 0:
        raise HTTPException(status_code=400, detail="Admins already exist. Use create-admin endpoint.")

    admin_id = str(uuid.uuid4())
    hashed = hash_password(data.password)

    try:
        db.execute(
            text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :hash)"),
            {"id": admin_id, "email": data.email, "name": data.name, "hash": hashed}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Admin already exists")

    return {"message": "Admin created", "admin_id": admin_id}


@app.post("/api/admin/create-admin")
def create_admin(data: CreateAdminRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Create new admin (requires active admin session)."""
    admin_id = str(uuid.uuid4())
    hashed = hash_password(data.password)

    try:
        db.execute(
            text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :hash)"),
            {"id": admin_id, "email": data.email, "name": data.name, "hash": hashed}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Admin already exists")

    return {"message": "Admin created", "admin_id": admin_id}

# --- Alumni Management ---

@app.post("/api/admin/alumni/save")
def save_alumni_record(data: AlumniSaveRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    student = db.execute(
        text("SELECT id FROM students WHERE id = :sid"), {"sid": data.student_id}
    ).mappings().first()

    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    existing = db.execute(text("SELECT id FROM alumni WHERE student_id = :sid"), {"sid": student["id"]}).mappings().first()
    if existing:
        db.execute(
            text("UPDATE alumni SET email = :email, contact_number = :contact, linkedin_url = :li WHERE id = :id"),
            {"email": data.email, "contact": data.contact_number, "li": data.linkedin_url, "id": existing["id"]}
        )
        alumnus_id = existing["id"]
    else:
        alumnus_id = str(uuid.uuid4())
        db.execute(
            text("""INSERT INTO alumni (id, student_id, email, contact_number, linkedin_url)
                    VALUES (:id, :sid, :email, :contact, :li)"""),
            {"id": alumnus_id, "sid": student["id"], "email": data.email,
             "contact": data.contact_number, "li": data.linkedin_url}
        )
    db.commit()

    # Seed this alumnus into the recurring survey schedule if not already
    # present, so the background dispatcher will pick them up. First send is
    # due 8 months out per the finalized requirement.
    existing_schedule = db.execute(
        text("SELECT id FROM alumni_survey_schedule WHERE alumnus_id = :aid"), {"aid": alumnus_id}
    ).mappings().first()
    if not existing_schedule:
        db.execute(
            text("""INSERT INTO alumni_survey_schedule (id, alumnus_id, next_send_at, interval_months)
                    VALUES (:id, :aid, :next_send, 8)"""),
            {"id": str(uuid.uuid4()), "aid": alumnus_id,
             "next_send": datetime.now(timezone.utc) + timedelta(days=8 * 30)}
        )
        db.commit()

    return {"message": "Alumnus record saved", "alumnus_id": alumnus_id}


@app.get("/api/admin/alumni/roster")
def list_alumni_roster(batch: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    query = """
        SELECT a.id, s.full_name, s.enrollment_number, s.degree_program, s.batch, a.email,
               ic.validation_status AS info_status, ic.submitted_at AS info_submitted_at,
               es.submitted_at AS exit_survey_submitted_at,
               (SELECT COUNT(*) FROM alumni_feedback_forms f WHERE f.alumnus_id = a.id AND f.submitted_at IS NOT NULL) AS feedback_completed,
               (SELECT COUNT(*) FROM alumni_feedback_forms f WHERE f.alumnus_id = a.id) AS feedback_sent
        FROM alumni a
        JOIN students s ON a.student_id = s.id
        LEFT JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id
        LEFT JOIN alumni_exit_surveys es ON es.alumnus_id = a.id
    """
    params = {}
    if batch:
        program, year = split_program_batch(batch)
        if program:
            query += " WHERE s.batch = :batch AND s.degree_program = :program"
            params["batch"] = year
            params["program"] = program
        else:
            query += " WHERE s.batch = :batch"
            params["batch"] = year
    query += " ORDER BY s.batch DESC, s.full_name"

    rows = db.execute(text(query), params).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        d["info_status"] = d["info_status"] if d["info_submitted_at"] else "pending"
        d["exit_survey_status"] = "completed" if d["exit_survey_submitted_at"] else "pending"
        d["batch_status"] = get_batch_status(d["batch"])
        result.append(d)
    return result


@app.get("/api/admin/alumni/batches")
def list_alumni_batches(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(text("""
        SELECT
            s.degree_program, s.batch,
            COUNT(DISTINCT a.id) AS total_alumni,
            COUNT(DISTINCT a.id) FILTER (WHERE ic.submitted_at IS NOT NULL) AS info_confirmed,
            COUNT(DISTINCT a.id) FILTER (WHERE ic.submitted_at IS NULL) AS info_pending,
            COUNT(DISTINCT a.id) FILTER (WHERE ic.submitted_at IS NOT NULL AND es.submitted_at IS NULL) AS exit_survey_eligible,
            COUNT(DISTINCT a.id) FILTER (WHERE es.submitted_at IS NOT NULL) AS exit_survey_completed
        FROM alumni a
        JOIN students s ON a.student_id = s.id
        LEFT JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id
        LEFT JOIN alumni_exit_surveys es ON es.alumnus_id = a.id
        GROUP BY s.degree_program, s.batch
        ORDER BY s.batch DESC, s.degree_program
    """)).mappings().all()

    result = []
    for r in rows:
        d = dict(r)
        combined = combine_program_batch(d["degree_program"], d["batch"])
        d["batch"] = combined
        d["batch_status"] = get_batch_status(d["batch"])
        result.append(d)
    return result


@app.get("/api/admin/alumni/students-without-record")
def list_students_without_alumni_record(batch: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    query = """
        SELECT s.id, s.full_name, s.enrollment_number, s.degree_program, s.batch
        FROM students s LEFT JOIN alumni a ON a.student_id = s.id
        WHERE a.id IS NULL
    """
    params = {}
    if batch:
        program, year = split_program_batch(batch)
        if program:
            query += " AND s.batch = :batch AND s.degree_program = :program"
            params["batch"] = year
            params["program"] = program
        else:
            query += " AND s.batch = :batch"
            params["batch"] = year
    query += " ORDER BY s.full_name"

    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) | {"batch_status": get_batch_status(r["batch"])} for r in rows]


@app.get("/api/admin/batches-list")
def list_all_batches_with_status(status: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(text("""
        SELECT DISTINCT s.degree_program, s.batch
        FROM students s ORDER BY s.batch DESC, s.degree_program
    """)).mappings().all()

    result = []
    for r in rows:
        combined = combine_program_batch(r["degree_program"], r["batch"])
        batch_status = get_batch_status(r["batch"])
        if status and batch_status != status:
            continue
        result.append({"batch": combined, "status": batch_status})
    return result


def _issue_alumni_action_link(db: DBSession, alumnus_id: str, name: str, email: str, action_type: str, target_id: str = None):
    token = secrets.token_urlsafe(32)
    link_id = str(uuid.uuid4())

    # Expiry window varies by action type per the finalized requirements:
    # the alumni survey link expires after a week; info confirmation and
    # exit survey links expire after 48 hours, same as the employer-facing
    # links.
    expiry_hours_by_type = {
        "info_confirmation": 48,
        "exit_survey": 48,
        "feedback_form": 24 * 7,  # alumni survey: expires after a week
    }
    expiry_hours = expiry_hours_by_type.get(action_type, 48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)

    db.execute(
        text("""INSERT INTO alumni_action_links (id, alumnus_id, action_type, target_id, token, expires_at)
                VALUES (:id, :aid, :action, :target, :token, :expires)"""),
        {"id": link_id, "aid": alumnus_id, "action": action_type, "target": target_id,
         "token": token, "expires": expires_at}
    )
    db.commit()

    link = f"{FRONTEND_URL}/alumni/{action_type}/{token}"
    action_labels = {
        "info_confirmation": "confirm your contact information",
        "exit_survey": "complete the exit survey",
        "feedback_form": "share your alumni feedback",
    }
    action_label = action_labels.get(action_type, "complete the requested form")

    subject_labels = {
        "info_confirmation": "Please confirm your information",
        "exit_survey": "Exit survey",
        "feedback_form": "Alumni feedback form",
    }
    subject = f"{subject_labels.get(action_type, 'Action required')} - CSSE, JUW"

    expiry_copy = "1 week" if expiry_hours == 24 * 7 else f"{expiry_hours} hours"
    html_body = f"""
    <p>Dear {name},</p>
    <p>Please click the link below to {action_label}:</p>
    <p><a href="{link}">{link}</a></p>
    <p>This link will expire in {expiry_copy} and can only be used once.</p>
    <p>Regards,<br>Department of Computer Science and Software Engineering<br>Jinnah University for Women</p>
    """

    sent, error = send_email_smtp(email, subject, html_body)
    if not sent:
        print(f"Failed to send {action_type} email to {email}: {error}")
    return sent, link


def _consume_alumni_action_link(db: DBSession, token: str, expected_action_type: str = None):
    """
    Validate and consume a single-use alumni action link. Returns the link
    row (as a dict) on success. Raises HTTPException(410) if expired, or
    HTTPException(410) if already used, or HTTPException(404) if the token
    doesn't exist / doesn't match the expected action type.

    This enforces the "same link shouldn't work twice" and "link expires"
    requirements shared by info confirmation, exit survey, and feedback
    form links. Callers should call this before accepting a submission, and
    it marks the link used_at as part of the same call so a second request
    with the same token is rejected even under concurrent submission.
    """
    row = db.execute(
        text("SELECT * FROM alumni_action_links WHERE token = :token"), {"token": token}
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Invalid link")

    if expected_action_type and row["action_type"] != expected_action_type:
        raise HTTPException(status_code=404, detail="Invalid link")

    if row["used_at"] is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=410, detail="This link has expired")

    result = db.execute(
        text("""UPDATE alumni_action_links SET used_at = NOW()
                WHERE token = :token AND used_at IS NULL"""),
        {"token": token}
    )
    db.commit()

    if result.rowcount == 0:
        # Lost a race with a concurrent submission on the same token.
        raise HTTPException(status_code=410, detail="This link has already been used")

    return dict(row)


@app.post("/api/admin/alumni/campaigns/info-confirmation")
def send_alumni_info_confirmation_campaign(data: AlumniCampaignRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    program, year = split_program_batch(data.batch)
    query = """SELECT a.id, a.email, s.full_name FROM alumni a JOIN students s ON a.student_id = s.id
                LEFT JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id
                WHERE s.batch = :batch AND ic.submitted_at IS NULL"""
    params = {"batch": year}
    if program:
        query += " AND s.degree_program = :program"
        params["program"] = program
    rows = db.execute(text(query), params).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No pending alumni found for batch {data.batch}")

    sent = 0
    for r in rows:
        ok, _ = _issue_alumni_action_link(db, r["id"], r["full_name"], r["email"], "info_confirmation")
        if ok:
            sent += 1
    return {"sent": sent}


@app.post("/api/admin/alumni/campaigns/exit-survey")
def send_alumni_exit_survey_campaign(data: AlumniCampaignRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    program, year = split_program_batch(data.batch)
    query = """SELECT a.id, a.email, s.full_name FROM alumni a
                JOIN students s ON a.student_id = s.id
                JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id AND ic.submitted_at IS NOT NULL
                LEFT JOIN alumni_exit_surveys es ON es.alumnus_id = a.id
                WHERE s.batch = :batch AND es.submitted_at IS NULL"""
    params = {"batch": year}
    if program:
        query += " AND s.degree_program = :program"
        params["program"] = program
    rows = db.execute(text(query), params).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No eligible alumni found for batch {data.batch} "
                                                       f"(info confirmation must be completed first)")

    sent = 0
    for r in rows:
        ok, _ = _issue_alumni_action_link(db, r["id"], r["full_name"], r["email"], "exit_survey")
        if ok:
            sent += 1
    return {"sent": sent}


@app.post("/api/admin/alumni/campaigns/feedback-form")
def send_alumni_feedback_campaign(data: AlumniFeedbackCampaignRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    program, year = split_program_batch(data.batch)
    query = "SELECT a.id, a.email, s.full_name FROM alumni a JOIN students s ON a.student_id = s.id WHERE s.batch = :batch"
    params = {"batch": year}
    if program:
        query += " AND s.degree_program = :program"
        params["program"] = program
    rows = db.execute(text(query), params).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No alumni found for batch {data.batch}")

    sent, skipped = 0, 0
    for r in rows:
        existing = db.execute(
            text("SELECT id FROM alumni_feedback_forms WHERE alumnus_id = :aid AND survey_year = :year"),
            {"aid": r["id"], "year": data.survey_year}
        ).mappings().first()
        if existing:
            skipped += 1
            continue
        form_id = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO alumni_feedback_forms (id, alumnus_id, survey_year) VALUES (:id, :aid, :year)"),
            {"id": form_id, "aid": r["id"], "year": data.survey_year}
        )
        db.commit()
        ok, _ = _issue_alumni_action_link(db, r["id"], r["full_name"], r["email"], "feedback_form", target_id=form_id)
        if ok:
            sent += 1
        # Whenever a feedback form is manually sent, treat that as fulfilling
        # (and resetting) this alumnus's place in the recurring schedule so
        # the background dispatcher doesn't send a duplicate shortly after.
        db.execute(
            text("""UPDATE alumni_survey_schedule
                    SET last_sent_at = NOW(), next_send_at = :next_send
                    WHERE alumnus_id = :aid"""),
            {"aid": r["id"], "next_send": datetime.now(timezone.utc) + timedelta(days=8 * 30)}
        )
        db.commit()

    return {"sent": sent, "skipped_already_has_form_for_year": skipped}


def run_recurring_alumni_survey_dispatch(db: DBSession) -> dict:
    """
    Send the alumni feedback/survey form to every alumnus whose
    alumni_survey_schedule.next_send_at has passed. Per the finalized
    requirement ("sent via email every 8 months or after a year"), this is
    interpreted as a repeating cycle: first send at 8 months post-signup,
    then every 8 months thereafter, so alumni are re-surveyed at least once
    a year. The survey_year sent is derived from the current date so repeat
    sends land in distinct records rather than colliding on the unique
    (alumnus_id, survey_year) constraint.

    This function does the sending itself (not via HTTP) so it can be
    invoked either by an external scheduler hitting the trigger endpoint
    below, or by an in-process cron-style call if one is wired up later.
    """
    due = db.execute(text("""
        SELECT sch.id AS schedule_id, sch.alumnus_id, a.email, s.full_name
        FROM alumni_survey_schedule sch
        JOIN alumni a ON a.id = sch.alumnus_id
        JOIN students s ON s.id = a.student_id
        WHERE sch.next_send_at <= NOW()
    """)).mappings().all()

    survey_year = str(datetime.now(timezone.utc).year)
    sent, failed = 0, 0

    for r in due:
        existing = db.execute(
            text("SELECT id FROM alumni_feedback_forms WHERE alumnus_id = :aid AND survey_year = :year"),
            {"aid": r["alumnus_id"], "year": survey_year}
        ).mappings().first()
        form_id = existing["id"] if existing else str(uuid.uuid4())
        if not existing:
            db.execute(
                text("INSERT INTO alumni_feedback_forms (id, alumnus_id, survey_year) VALUES (:id, :aid, :year)"),
                {"id": form_id, "aid": r["alumnus_id"], "year": survey_year}
            )
            db.commit()

        ok, _ = _issue_alumni_action_link(db, r["alumnus_id"], r["full_name"], r["email"], "feedback_form", target_id=form_id)
        if ok:
            sent += 1
        else:
            failed += 1

        db.execute(
            text("""UPDATE alumni_survey_schedule
                    SET last_sent_at = NOW(), next_send_at = :next_send
                    WHERE id = :sid"""),
            {"sid": r["schedule_id"], "next_send": datetime.now(timezone.utc) + timedelta(days=8 * 30)}
        )
        db.commit()

    return {"sent": sent, "failed": failed, "checked": len(due)}


@app.post("/api/admin/alumni/campaigns/run-recurring-dispatch")
def trigger_recurring_alumni_survey_dispatch(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """
    Manually trigger the recurring alumni survey dispatch (every 8 months /
    after a year). In production this endpoint is meant to be hit by an
    external scheduler (cron, hosting-platform scheduled job, etc.) since
    this deployment has no in-process background worker; exposing it here
    also lets an admin trigger it on demand.
    """
    return run_recurring_alumni_survey_dispatch(db)


# --- Student Exit Form (pre-graduation) ---

STUDENT_EXIT_FORM_WINDOW_HOURS = 72
STUDENT_EXIT_FORM_MAX_REMINDERS = 3


def _issue_student_exit_form_link(db: DBSession, form_id: str, student_id: str, name: str, email: str, is_reminder: bool):
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=STUDENT_EXIT_FORM_WINDOW_HOURS)

    db.execute(
        text("""UPDATE student_exit_forms
                SET token = :token, expires_at = :expires, used_at = NULL, status = 'pending'
                WHERE id = :id"""),
        {"token": token, "expires": expires_at, "id": form_id}
    )
    db.commit()

    link = f"{FRONTEND_URL}/student/exit-form/{token}"
    subject = "Exit form reminder - CSSE, JUW" if is_reminder else "Exit form - CSSE, JUW"
    reminder_note = "<p>This is a reminder -- your previous link expired before it was submitted.</p>" if is_reminder else ""
    html_body = f"""
    <p>Dear {name},</p>
    <p>Please click the link below to complete your exit form. This link is personalized to you, can only be
    used once, and will expire {STUDENT_EXIT_FORM_WINDOW_HOURS} hours from now.</p>
    {reminder_note}
    <p><a href="{link}">{link}</a></p>
    <p>Regards,<br>Department of Computer Science and Software Engineering<br>Jinnah University for Women</p>
    """

    sent, error = send_email_smtp(email, subject, html_body)
    if not sent:
        print(f"Failed to send student exit form email to {email}: {error}")
    return sent, link


@app.post("/api/admin/students/exit-form/initiate")
def initiate_student_exit_form(data: InitiateStudentExitFormRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    student = db.execute(
        text("""SELECT s.id, s.full_name, a.email
                FROM students s LEFT JOIN alumni a ON a.student_id = s.id
                WHERE s.id = :id"""), {"id": data.student_id}
    ).mappings().first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if not student["email"]:
        raise HTTPException(status_code=400, detail="Student has no email on file (no alumni record with email found)")

    existing = db.execute(
        text("SELECT id, status FROM student_exit_forms WHERE student_id = :sid"), {"sid": student["id"]}
    ).mappings().first()
    if existing and existing["status"] == "submitted":
        raise HTTPException(status_code=400, detail="Exit form already submitted for this student")

    if existing:
        form_id = existing["id"]
    else:
        form_id = str(uuid.uuid4())
        db.execute(
            text("""INSERT INTO student_exit_forms (id, student_id, token, status, expires_at)
                    VALUES (:id, :sid, :placeholder_token, 'pending', NOW())"""),
            {"id": form_id, "sid": student["id"], "placeholder_token": secrets.token_urlsafe(8)}
        )
        db.commit()

    sent, link = _issue_student_exit_form_link(db, form_id, student["id"], student["full_name"], student["email"], is_reminder=False)
    message = "Exit form sent." if sent else "Exit form created, but the email could not be sent -- use the manual link below."
    return {"message": message, "form_id": form_id, "sent": sent, "manual_url": None if sent else link}


def run_student_exit_form_reminders(db: DBSession) -> dict:
    """
    For every pending, unsubmitted exit form whose link has expired, issue a
    fresh link automatically -- up to STUDENT_EXIT_FORM_MAX_REMINDERS times.
    Forms that have already used up their reminders are left as-is for
    manual admin follow-up rather than reminded indefinitely.

    Meant to be invoked periodically by an external scheduler, same as the
    recurring alumni survey dispatch above.
    """
    expired = db.execute(text("""
        SELECT f.id AS form_id, f.reminder_count, s.id AS student_id, s.full_name, a.email
        FROM student_exit_forms f
        JOIN students s ON s.id = f.student_id
        LEFT JOIN alumni a ON a.student_id = s.id
        WHERE f.status = 'pending'
          AND f.used_at IS NULL
          AND f.expires_at <= NOW()
          AND f.reminder_count < :max_reminders
          AND a.email IS NOT NULL
    """), {"max_reminders": STUDENT_EXIT_FORM_MAX_REMINDERS}).mappings().all()

    sent, failed = 0, 0
    for r in expired:
        ok, _ = _issue_student_exit_form_link(db, r["form_id"], r["student_id"], r["full_name"], r["email"], is_reminder=True)
        db.execute(
            text("UPDATE student_exit_forms SET reminder_count = reminder_count + 1 WHERE id = :id"),
            {"id": r["form_id"]}
        )
        db.commit()
        if ok:
            sent += 1
        else:
            failed += 1

    return {"reminders_sent": sent, "failed": failed, "checked": len(expired)}


@app.post("/api/admin/students/exit-form/run-reminders")
def trigger_student_exit_form_reminders(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """
    Manually trigger the exit-form reminder sweep. As with the alumni
    dispatch endpoint, this is meant to be called by an external scheduler
    on a short interval (e.g. hourly) so expired-but-unsubmitted forms get
    a fresh link promptly within the 72-hour-per-attempt cadence, and is
    also callable by an admin on demand.
    """
    return run_student_exit_form_reminders(db)


@app.get("/api/admin/students/exit-form/status")
def list_student_exit_form_status(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(text("""
        SELECT f.id, s.full_name, s.enrollment_number, s.batch, f.status,
               f.reminder_count, f.expires_at, f.submitted_at
        FROM student_exit_forms f
        JOIN students s ON s.id = f.student_id
        ORDER BY f.created_at DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


# --- Dashboard Stats & Tracking ---

@app.get("/api/admin/dashboard-stats")
def get_dashboard_stats(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    stats = {
        "total_students": 0, "total_employers": 0, "total_engagements": 0,
        "internship_engagements": 0, "job_engagements": 0,
        "proformas_validated": 0, "proformas_pending": 0,
        "evals_submitted": 0, "surveys_submitted": 0
    }

    queries = [
        ("total_students", "SELECT COUNT(*) as cnt FROM students"),
        ("total_employers", "SELECT COUNT(DISTINCT employer_id) as cnt FROM engagements"),
        ("total_engagements", "SELECT COUNT(*) as cnt FROM engagements"),
        ("internship_engagements", "SELECT COUNT(*) as cnt FROM engagements WHERE type = 'internship'"),
        ("job_engagements", "SELECT COUNT(*) as cnt FROM engagements WHERE type = 'job'"),
        ("proformas_validated", "SELECT COUNT(*) as cnt FROM org_proformas WHERE validation_status IN ('validated', 'edited')"),
        ("proformas_pending", "SELECT COUNT(*) as cnt FROM org_proformas WHERE validation_status = 'pending'"),
        ("evals_submitted", "SELECT COUNT(*) as cnt FROM internship_evaluations WHERE submitted_at IS NOT NULL"),
        ("surveys_submitted", "SELECT COUNT(*) as cnt FROM employer_surveys WHERE submitted_at IS NOT NULL"),
    ]
    for key, q in queries:
        try:
            result = db.execute(text(q)).mappings().first()
            if result:
                stats[key] = result["cnt"]
        except Exception:
            traceback.print_exc()

    return stats


@app.get("/api/admin/engagements")
def list_engagements(page: int = 1, page_size: int = 10, engagement_type: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """
    List engagements, most recent first. Supports pagination (default 10
    per page, per the finalized requirement that the Graduate Employer /
    Internship Supervisor tracking tables show 10 recent engagements with
    pagination) and an optional engagement_type filter ('internship' | 'job').
    """
    try:
        base_query = """
            FROM engagements eng
            LEFT JOIN org_proformas op ON op.engagement_id = eng.id
            LEFT JOIN students s ON eng.student_id = s.id
            LEFT JOIN employers e ON eng.employer_id = e.id
            LEFT JOIN internship_evaluations ie ON ie.engagement_id = eng.id
            LEFT JOIN employer_surveys es ON es.engagement_id = eng.id
            WHERE 1=1
        """
        params = {}
        if engagement_type:
            base_query += " AND eng.type = :etype"
            params["etype"] = engagement_type

        total = db.execute(text(f"SELECT COUNT(*) {base_query}"), params).scalar()

        offset = (page - 1) * page_size
        params["limit"] = page_size
        params["offset"] = offset

        rows = db.execute(text(f"""
            SELECT
                eng.id, s.full_name as student_name, s.enrollment_number,
                e.name as employer_name, e.work_email as employer_email,
                eng.type as type, op.validation_status,
                op.year_of_graduation,
                CASE
                    WHEN eng.type = 'internship' AND ie.submitted_at IS NOT NULL THEN 'submitted'
                    WHEN eng.type = 'job' AND es.submitted_at IS NOT NULL THEN 'submitted'
                    ELSE 'pending'
                END as feedback_status,
                eng.created_at
            {base_query}
            ORDER BY eng.created_at DESC
            LIMIT :limit OFFSET :offset
        """), params).mappings().all()

        engagements = []
        for r in rows:
            d = dict(r)
            # Explicit JSON-safe conversion for UUID/datetime/enum values
            d["id"] = str(d["id"]) if d.get("id") else None
            d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
            d["type"] = str(d["type"]) if d.get("type") else None
            d["validation_status"] = str(d["validation_status"]) if d.get("validation_status") else None
            engagements.append(d)
        return {"engagements": engagements, "total": total, "page": page, "page_size": page_size}
    except Exception as exc:
        traceback.print_exc()
        return {"engagements": [], "total": 0, "page": page, "page_size": page_size, "error": str(exc)}


@app.get("/api/admin/students")
def list_students(q: str = "", status: str = "", batch: str = "", page: int = 1, page_size: int = 20, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    query = "SELECT id, full_name, enrollment_number, degree_program, batch, current_semester FROM students WHERE 1=1"
    params = {}

    if q:
        query += " AND (full_name ILIKE :q OR enrollment_number ILIKE :q)"
        params["q"] = f"%{q}%"

    if batch:
        program, year = split_program_batch(batch)
        if program:
            query += " AND batch = :batch AND degree_program = :program"
            params["batch"] = year
            params["program"] = program
        else:
            query += " AND batch = :batch"
            params["batch"] = year

    count_query = query.replace("SELECT id, full_name, enrollment_number, degree_program, batch, current_semester", "SELECT COUNT(*)")
    total = db.execute(text(count_query), params).scalar()

    offset = (page - 1) * page_size
    query += f" ORDER BY full_name LIMIT {page_size} OFFSET {offset}"

    rows = db.execute(text(query), params).mappings().all()

    # Compute real status tags per student -- previously hardcoded to
    # ["student"], which silently broke the Intern/Graduate/Alumni filter
    # chips in the admin UI. A student can hold more than one status at once
    # (e.g. intern this semester, already an alumnus is not possible, but
    # intern + graduate-track can overlap), so this is a set, not a single
    # value.
    student_ids = [r["id"] for r in rows]
    intern_ids, graduate_ids, alumni_ids = set(), set(), set()
    if student_ids:
        intern_rows = db.execute(text("""
            SELECT DISTINCT student_id FROM engagements
            WHERE type = 'internship' AND student_id = ANY(:ids)
        """), {"ids": student_ids}).mappings().all()
        intern_ids = {r["student_id"] for r in intern_rows}

        graduate_rows = db.execute(text("""
            SELECT DISTINCT student_id FROM engagements
            WHERE type = 'job' AND student_id = ANY(:ids)
        """), {"ids": student_ids}).mappings().all()
        graduate_ids = {r["student_id"] for r in graduate_rows}

        alumni_rows = db.execute(text("""
            SELECT DISTINCT student_id FROM alumni WHERE student_id = ANY(:ids)
        """), {"ids": student_ids}).mappings().all()
        alumni_ids = {r["student_id"] for r in alumni_rows}

    students = []
    for r in rows:
        d = dict(r)
        statuses = ["undergrad"]
        if d["id"] in intern_ids:
            statuses.append("intern")
        if d["id"] in graduate_ids:
            statuses.append("graduate")
        if d["id"] in alumni_ids:
            statuses.append("alumni")
        d["statuses"] = statuses
        students.append(d)

    # Apply the status filter here (post-computation) since statuses are
    # derived from joins rather than a column that could be filtered in SQL
    # directly above.
    if status:
        students = [s for s in students if status in s["statuses"]]

    return {"students": students, "total": total}


@app.get("/api/admin/employers")
def list_employers(q: str = "", status: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """List employers with engagement types and feedback counts. Supports
    search by employer name (q), per the finalized requirement that the
    Employers tab support search using name."""
    try:
        query = """
            SELECT
                e.id, e.work_email AS email, e.name, e.designation, e.created_at,
                COUNT(DISTINCT eng.id) as total_engagements,
                COUNT(DISTINCT eng.id) FILTER (WHERE eng.type = 'internship') as intern_engagements,
                COUNT(DISTINCT eng.id) FILTER (WHERE eng.type = 'job') as job_engagements,
                COUNT(DISTINCT ie.id) FILTER (WHERE ie.submitted_at IS NOT NULL) as evals_submitted,
                COUNT(DISTINCT es.id) FILTER (WHERE es.submitted_at IS NOT NULL) as surveys_submitted
            FROM employers e
            LEFT JOIN engagements eng ON eng.employer_id = e.id
            LEFT JOIN internship_evaluations ie ON ie.engagement_id = eng.id
            LEFT JOIN employer_surveys es ON es.engagement_id = eng.id
        """
        params = {}
        if q:
            query += " WHERE e.name ILIKE :q"
            params["q"] = f"%{q}%"
        query += """
            GROUP BY e.id, e.work_email, e.name, e.designation, e.created_at
            ORDER BY e.created_at DESC
        """

        rows = db.execute(text(query), params).mappings().all()

        result = []
        for r in rows:
            d = dict(r)
            # Build statuses list from engagement types
            statuses = []
            if d["intern_engagements"] > 0:
                statuses.append("intern_employer")
            if d["job_engagements"] > 0:
                statuses.append("graduate_employer")
            d["statuses"] = statuses

            # Apply status filter
            if status and status not in statuses:
                continue

            result.append(d)
        return result
    except Exception as exc:
        traceback.print_exc()
        return []


@app.delete("/api/admin/students/{student_id}")
def delete_student(student_id: str, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    try:
        db.execute(text("DELETE FROM alumni WHERE student_id = :id"), {"id": student_id})
        result = db.execute(text("DELETE FROM students WHERE id = :id"), {"id": student_id})
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


# --- Engagements (internship supervisor / graduate employer initiation) ---

@app.post("/api/admin/engagements")
def create_engagement(data: CreateEngagementRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    if data.engagement_type not in ("internship", "job"):
        raise HTTPException(status_code=400, detail="engagement_type must be 'internship' or 'job'")

    student = db.execute(
        text("SELECT id, full_name, enrollment_number FROM students WHERE id = :id"),
        {"id": data.student_id}
    ).mappings().first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Restrict date: internship starting date must be before the completion date.
    if data.engagement_type == "internship" and data.start_date and data.end_date:
        try:
            start = date.fromisoformat(data.start_date)
            end = date.fromisoformat(data.end_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Dates must be in YYYY-MM-DD format")
        if start >= end:
            raise HTTPException(status_code=400, detail="Internship starting date must be before the completion date")

    # Employer = respondent: find-or-create by email, and keep name/designation
    # in sync with what was entered for this engagement (single identity, per
    # the merged Employer block on the Graduate Employer tab).
    employer = db.execute(
        text("SELECT id FROM employers WHERE work_email = :email"),
        {"email": data.employer_email}
    ).mappings().first()

    if employer:
        employer_id = employer["id"]
        db.execute(
            text("""UPDATE employers SET name = COALESCE(NULLIF(:name, ''), name),
                                          designation = COALESCE(NULLIF(:desig, ''), designation)
                    WHERE id = :id"""),
            {"name": data.supervisor_name, "desig": data.supervisor_designation, "id": employer_id}
        )
    else:
        employer_id = str(uuid.uuid4())
        db.execute(
            text("""INSERT INTO employers (id, work_email, name, designation, created_via, created_at)
                    VALUES (:id, :email, :name, :desig, 'admin', NOW())"""),
            {"id": employer_id, "email": data.employer_email,
             "name": data.supervisor_name, "desig": data.supervisor_designation}
        )

    # Create the engagement record first (central table with student/employer/type)
    engagement_id = str(uuid.uuid4())
    db.execute(
        text("""INSERT INTO engagements (id, student_id, employer_id, type, status, created_at)
                VALUES (:id, :student_id, :employer_id, :type, 'active', NOW())"""),
        {"id": engagement_id, "student_id": student["id"], "employer_id": employer_id,
         "type": data.engagement_type}
    )

    # Then create the org_proforma detail record linked via engagement_id
    proforma_id = str(uuid.uuid4())
    contact_email = data.contact_email or data.employer_email
    db.execute(
        text("""
            INSERT INTO org_proformas (
                id, engagement_id,
                organization_name, role_designation, department_served,
                supervisor_name, supervisor_designation, contact_email, contact_phone,
                linkedin_url, start_date, end_date, validation_status,
                graduate_full_name, graduate_degree_program, year_of_graduation,
                current_job_role, job_department, duration_of_employment
            ) VALUES (
                :id, :engagement_id,
                :org, :role, :dept,
                :supervisor, :supervisor_desig, :contact_email, :contact_phone,
                :linkedin, :start_date, :end_date, 'pending',
                :grad_name, :grad_prog, :grad_year,
                :grad_role, :grad_dept, :grad_duration
            )
        """),
        {
            "id": proforma_id, "engagement_id": engagement_id,
            "org": data.organization_name, "role": data.role_designation, "dept": data.department_served,
            "supervisor": data.supervisor_name, "supervisor_desig": data.supervisor_designation,
            "contact_email": contact_email, "contact_phone": data.contact_phone,
            "linkedin": data.linkedin_url,
            "start_date": data.start_date or None, "end_date": data.end_date or None,
            "grad_name": data.graduate_full_name, "grad_prog": data.graduate_degree_program,
            "grad_year": data.year_of_graduation, "grad_role": data.current_job_role,
            "grad_dept": data.job_department, "grad_duration": data.duration_of_employment,
        }
    )
    db.commit()

    sent = False
    manual_url = None
    if data.send_invite:
        # Employer-facing invite link: personalized (bound to this proforma),
        # expires 48 hours from issue, single-use (invite_used_at set on
        # consumption -- see consume_engagement_invite below). Matches the
        # finalized requirement for both Internship Supervisor and Graduate
        # Employer employer-facing links.
        token = secrets.token_urlsafe(32)
        invite_expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
        link = f"{FRONTEND_URL}/employer/{data.engagement_type}/{token}"
        db.execute(
            text("""UPDATE org_proformas
                    SET invite_token = :token, invite_expires_at = :expires, invite_used_at = NULL
                    WHERE id = :id"""),
            {"token": token, "expires": invite_expires_at, "id": proforma_id}
        )
        db.commit()

        action_label = "the internship evaluation form" if data.engagement_type == "internship" else "the graduate employer survey"
        subject = "Internship Evaluation Request - CSSE, JUW" if data.engagement_type == "internship" else "Graduate Employer Feedback Request - CSSE, JUW"
        html_body = f"""
        <p>Dear {data.supervisor_name or 'Sir/Madam'},</p>
        <p>You are being requested to complete {action_label} for {student['full_name']} ({student['enrollment_number']}).</p>
        <p><a href="{link}">{link}</a></p>
        <p>This link is personalized to you, can only be used once, and will expire 48 hours from now.</p>
        <p>Regards,<br>Department of Computer Science and Software Engineering<br>Jinnah University for Women</p>
        """
        sent, error = send_email_smtp(data.employer_email, subject, html_body)
        if not sent:
            manual_url = link
            print(f"Failed to send engagement invite to {data.employer_email}: {error}")

    message = "Engagement created and invitation sent." if sent else (
        "Engagement created, but the invitation email could not be sent -- use the manual link below."
        if data.send_invite else "Engagement created."
    )
    return {"message": message, "proforma_id": proforma_id, "sent": sent, "manual_url": manual_url}


@app.get("/api/admin/engagements/invite/{token}")
def resolve_engagement_invite(token: str, db: DBSession = Depends(get_db)):
    """
    Resolve an employer-facing invite link (Internship Supervisor or
    Graduate Employer) prior to consuming it, so the employer-facing
    frontend can confirm the proforma and show a read-only "already
    submitted" or "link expired" state without mutating anything yet.
    Actual consumption (marking invite_used_at) happens on submission via
    consume_engagement_invite, so a plain page load doesn't burn the link.
    """
    row = db.execute(
        text("""SELECT op.id, op.invite_expires_at, op.invite_used_at, op.validation_status, eng.type as engagement_type
               FROM org_proformas op JOIN engagements eng ON op.engagement_id = eng.id
               WHERE op.invite_token = :token"""),
        {"token": token}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid link")

    if row["invite_used_at"] is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")

    expires_at = row["invite_expires_at"]
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=410, detail="This link has expired")

    return {"proforma_id": row["id"], "engagement_type": row["engagement_type"], "validation_status": row["validation_status"]}


def consume_engagement_invite(db: DBSession, token: str) -> dict:
    """
    Mark an employer-facing invite link as used. Call this from whichever
    endpoint accepts the actual evaluation/survey submission, before writing
    the submission, so a second submission attempt on the same token is
    rejected even under concurrent requests. Not wired to a submission
    endpoint here since none exists in this file yet -- provided for that
    endpoint to call.
    """
    row = db.execute(
        text("SELECT id, invite_expires_at, invite_used_at FROM org_proformas WHERE invite_token = :token"),
        {"token": token}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Invalid link")
    if row["invite_used_at"] is not None:
        raise HTTPException(status_code=410, detail="This link has already been used")

    expires_at = row["invite_expires_at"]
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise HTTPException(status_code=410, detail="This link has expired")

    result = db.execute(
        text("UPDATE org_proformas SET invite_used_at = NOW() WHERE id = :id AND invite_used_at IS NULL"),
        {"id": row["id"]}
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=410, detail="This link has already been used")
    return dict(row)


@app.post("/api/admin/send-email")
def send_email(data: dict, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Send bulk email to a filtered set of employers."""
    audience = data.get("audience", "all_employers")
    subject = data.get("subject", "")
    body = data.get("body", "")
    explicit_emails = data.get("emails", [])

    if audience == "specific":
        emails = [e for e in explicit_emails if e]
    else:
        status_filter = {
            "intern_employers": "intern_employer",
            "graduate_employers": "graduate_employer",
        }.get(audience)
        rows = db.execute(text("SELECT DISTINCT e.work_email AS email FROM employers e")).mappings().all()
        emails = [r["email"] for r in rows]
        if status_filter:
            filtered_rows = db.execute(text("""
                SELECT DISTINCT e.work_email AS email FROM employers e
                JOIN engagements eng ON eng.employer_id = e.id
                WHERE eng.type = :etype
            """), {"etype": "internship" if status_filter == "intern_employer" else "job"}).mappings().all()
            emails = [r["email"] for r in filtered_rows]

    sent, failed = 0, 0
    for email in emails:
        ok, _ = send_email_smtp(email, subject, body)
        if ok:
            sent += 1
        else:
            failed += 1

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