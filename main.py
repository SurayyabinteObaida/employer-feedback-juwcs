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
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

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

    html_body = f"""
    <p>Dear {name},</p>
    <p>Please click the link below to {action_label}:</p>
    <p><a href="{link}">{link}</a></p>
    <p>This link will expire in 48 hours.</p>
    <p>Regards,<br>Department of Computer Science and Software Engineering<br>Jinnah University for Women</p>
    """

    sent, error = send_email_smtp(email, subject, html_body)
    if not sent:
        print(f"Failed to send {action_type} email to {email}: {error}")
    return sent, link


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

    return {"sent": sent, "skipped_already_has_form_for_year": skipped}


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
        ("total_employers", "SELECT COUNT(DISTINCT employer_id) as cnt FROM org_proformas WHERE employer_id IS NOT NULL"),
        ("total_engagements", "SELECT COUNT(*) as cnt FROM org_proformas"),
        ("internship_engagements", "SELECT COUNT(*) as cnt FROM org_proformas WHERE engagement_type = 'internship'"),
        ("job_engagements", "SELECT COUNT(*) as cnt FROM org_proformas WHERE engagement_type = 'job'"),
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
            pass

    return stats


@app.get("/api/admin/engagements")
def list_engagements(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    try:
        rows = db.execute(text("""
            SELECT
                op.id, s.full_name as student_name, s.enrollment_number,
                e.name as employer_name, e.email as employer_email,
                op.engagement_type as type, op.validation_status,
                CASE
                    WHEN op.engagement_type = 'internship' AND ie.submitted_at IS NOT NULL THEN 'submitted'
                    WHEN op.engagement_type = 'job' AND es.submitted_at IS NOT NULL THEN 'submitted'
                    ELSE 'pending'
                END as feedback_status,
                op.created_at
            FROM org_proformas op
            LEFT JOIN students s ON op.student_id = s.id
            LEFT JOIN employers e ON op.employer_id = e.id
            LEFT JOIN internship_evaluations ie ON op.id = ie.proforma_id
            LEFT JOIN employer_surveys es ON op.id = es.proforma_id
            ORDER BY op.created_at DESC
        """)).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []


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

    students = []
    for r in rows:
        d = dict(r)
        d["statuses"] = ["student"]
        students.append(d)

    return {"students": students, "total": total}


@app.get("/api/admin/employers")
def list_employers(status: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """List employers with engagement types and feedback counts."""
    try:
        rows = db.execute(text("""
            SELECT
                e.id, e.email, e.name, e.designation, e.created_at,
                COUNT(DISTINCT op.id) as total_engagements,
                COUNT(DISTINCT op.id) FILTER (WHERE op.engagement_type = 'internship') as intern_engagements,
                COUNT(DISTINCT op.id) FILTER (WHERE op.engagement_type = 'job') as job_engagements,
                COUNT(DISTINCT ie.id) FILTER (WHERE ie.submitted_at IS NOT NULL) as evals_submitted,
                COUNT(DISTINCT es.id) FILTER (WHERE es.submitted_at IS NOT NULL) as surveys_submitted
            FROM employers e
            LEFT JOIN org_proformas op ON op.employer_id = e.id
            LEFT JOIN internship_evaluations ie ON ie.proforma_id = op.id
            LEFT JOIN employer_surveys es ON es.proforma_id = op.id
            GROUP BY e.id, e.email, e.name, e.designation, e.created_at
            ORDER BY e.created_at DESC
        """)).mappings().all()

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
    except Exception:
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
        text("SELECT id FROM employers WHERE email = :email"),
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
            text("""INSERT INTO employers (id, email, name, designation, created_at)
                    VALUES (:id, :email, :name, :desig, NOW())"""),
            {"id": employer_id, "email": data.employer_email,
             "name": data.supervisor_name, "desig": data.supervisor_designation}
        )

    proforma_id = str(uuid.uuid4())
    contact_email = data.contact_email or data.employer_email
    db.execute(
        text("""
            INSERT INTO org_proformas (
                id, student_id, employer_id, engagement_type,
                organization_name, role_designation, department_served,
                supervisor_name, supervisor_designation, contact_email, contact_phone,
                linkedin_url, start_date, end_date, validation_status,
                graduate_full_name, graduate_degree_program, year_of_graduation,
                current_job_role, job_department, duration_of_employment,
                created_at
            ) VALUES (
                :id, :student_id, :employer_id, :engagement_type,
                :org, :role, :dept,
                :supervisor, :supervisor_desig, :contact_email, :contact_phone,
                :linkedin, :start_date, :end_date, 'pending',
                :grad_name, :grad_prog, :grad_year,
                :grad_role, :grad_dept, :grad_duration,
                NOW()
            )
        """),
        {
            "id": proforma_id, "student_id": student["id"], "employer_id": employer_id,
            "engagement_type": data.engagement_type,
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
        token = secrets.token_urlsafe(32)
        link = f"{FRONTEND_URL}/employer/{data.engagement_type}/{token}"
        db.execute(
            text("UPDATE org_proformas SET invite_token = :token WHERE id = :id"),
            {"token": token, "id": proforma_id}
        ) if _has_column(db, "org_proformas", "invite_token") else None
        db.commit()

        action_label = "the internship evaluation form" if data.engagement_type == "internship" else "the graduate employer survey"
        subject = "Internship Evaluation Request - CSSE, JUW" if data.engagement_type == "internship" else "Graduate Employer Feedback Request - CSSE, JUW"
        html_body = f"""
        <p>Dear {data.supervisor_name or 'Sir/Madam'},</p>
        <p>You are being requested to complete {action_label} for {student['full_name']} ({student['enrollment_number']}).</p>
        <p><a href="{link}">{link}</a></p>
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


def _has_column(db: DBSession, table: str, column: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": table, "c": column}
    ).first()
    return row is not None


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
        rows = db.execute(text("SELECT DISTINCT e.email FROM employers e")).mappings().all()
        emails = [r["email"] for r in rows]
        if status_filter:
            filtered_rows = db.execute(text("""
                SELECT DISTINCT e.email FROM employers e
                JOIN org_proformas op ON op.employer_id = e.id
                WHERE op.engagement_type = :etype
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