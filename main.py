from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as DBSession
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid
import os
import secrets
import string
import hashlib
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="OBE Indirect Assessments Panel")

# --- Password helpers ---

def generate_password(length=10):
    """Generate a readable random password."""
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))

def hash_password(password: str) -> str:
    """Hash password using SHA-256 with salt."""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${hashed}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored hash."""
    if not stored_hash or '$' not in stored_hash:
        return False
    salt, hashed = stored_hash.split('$', 1)
    return hashlib.sha256((salt + password).encode()).hexdigest() == hashed

# --- Batch status helper ---

def get_batch_status(batch_year: str) -> str:
    """
    Determine if a batch is active or graduated based on batch year.
    Assumes 4-year program: batch graduating in year X is marked:
    - graduated if X <= current_year
    - active if X > current_year
    
    Example: "BS(CS) 2022" → 2022 <= 2026 → graduated
             "BS(CS) 2027" → 2027 > 2026 → active
    """
    try:
        # Extract year from batch string (e.g., "BS(CS) 2022" → 2022)
        year_str = batch_year.split()[-1]
        batch_year_int = int(year_str)
        current_year = datetime.now().year
        return "graduated" if batch_year_int <= current_year else "active"
    except (ValueError, IndexError):
        return "active"  # Default to active if parsing fails

# --- Ensure password_hash column exists ---

@app.on_event("startup")
def ensure_columns():
    """Add any missing columns/tables on startup — no manual migration needed."""
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

        # Create admins table if not exists
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS admins (
                id VARCHAR PRIMARY KEY,
                email VARCHAR UNIQUE NOT NULL,
                name VARCHAR,
                password_hash VARCHAR NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # Create admin_sessions table if not exists
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                id VARCHAR PRIMARY KEY,
                admin_id VARCHAR NOT NULL REFERENCES admins(id),
                token VARCHAR UNIQUE NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        # --- Alumni panel tables ---
        # `alumni` extends an existing `students` row with contact info the
        # OBE sync doesn't carry. Identity fields (name, enrollment_number,
        # degree_program, batch) are read from `students` via student_id,
        # never duplicated here. students.id is UUID, so student_id/alumnus_id
        # FK columns must be UUID too (not VARCHAR).
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

        # Section B = GA2-GA10 only (GA1 excluded per department decision).
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

        # Repeatable per survey_year -- admin spawns a new row each
        # campaign; GA1-GA10 + single consolidated feedback field.
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

        # Single-use action tokens -- one per info-confirmation /
        # exit-survey / feedback-form send. target_id points at the
        # relevant alumni_feedback_forms row for repeatable actions.
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

# --- Auth & session helpers ---

def require_admin_key(x_admin_key: str = Header(None)):
    """Require a static admin key for certain routes."""
    if x_admin_key != os.getenv("ADMIN_KEY"):
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return x_admin_key

def get_current_admin(authorization: str = Header(None), db: DBSession = Depends(get_db)) -> dict:
    """Extract admin from session token."""
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

def authHeaders():
    """JavaScript helper for auth headers."""
    return "Add to admin.html JS: function authHeaders() { return { 'Authorization': 'Bearer ' + localStorage.getItem('sessionToken'), 'Content-Type': 'application/json' }; }"

# --- Pydantic models ---

class AdminLoginRequest(BaseModel):
    email: str
    password: str

class AdminSessionResponse(BaseModel):
    token: str
    admin_name: str
    expires_at: str

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

# --- Admin Authentication ---

@app.post("/api/admin/login")
def admin_login(data: AdminLoginRequest, db: DBSession = Depends(get_db)):
    """Admin login — verify password, create session."""
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
    
    return {
        "token": token,
        "admin_name": admin["name"],
        "expires_at": expires_at.isoformat()
    }

@app.post("/api/admin/create-admin")
def create_admin(data: AdminLoginRequest, x_admin_key: str = Header(None), db: DBSession = Depends(get_db)):
    """Create new admin (requires X-Admin-Key header)."""
    if x_admin_key != os.getenv("ADMIN_KEY"):
        raise HTTPException(status_code=403, detail="Invalid admin key")
    
    admin_id = str(uuid.uuid4())
    hashed = hash_password(data.password)
    
    try:
        db.execute(
            text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :hash)"),
            {"id": admin_id, "email": data.email, "name": data.email.split('@')[0], "hash": hashed}
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=400, detail="Admin already exists")
    
    return {"message": "Admin created", "admin_id": admin_id}

# --- Alumni Management ---

@app.post("/api/admin/alumni/save")
def save_alumni_record(data: AlumniSaveRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Save or update an alumnus record."""
    student = db.execute(
        text("SELECT id FROM students WHERE id = :sid"),
        {"sid": data.student_id}
    ).mappings().first()
    
    if not student:
        raise HTTPException(status_code=404, detail="Student not found - check the student ID")

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
    """List alumni with info confirmation and exit survey status, filtered by batch."""
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
        query += " WHERE s.batch = :batch"
        params["batch"] = batch
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
    """Every batch that has at least one alumni record, with counts and status."""
    rows = db.execute(text("""
        SELECT
            s.batch,
            COUNT(DISTINCT a.id) AS total_alumni,
            COUNT(DISTINCT a.id) FILTER (WHERE ic.submitted_at IS NOT NULL) AS info_confirmed,
            COUNT(DISTINCT a.id) FILTER (WHERE ic.submitted_at IS NULL) AS info_pending,
            COUNT(DISTINCT a.id) FILTER (WHERE ic.submitted_at IS NOT NULL AND es.submitted_at IS NULL) AS exit_survey_eligible,
            COUNT(DISTINCT a.id) FILTER (WHERE es.submitted_at IS NOT NULL) AS exit_survey_completed
        FROM alumni a
        JOIN students s ON a.student_id = s.id
        LEFT JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id
        LEFT JOIN alumni_exit_surveys es ON es.alumnus_id = a.id
        GROUP BY s.batch
        ORDER BY s.batch DESC
    """)).mappings().all()
    
    result = []
    for r in rows:
        d = dict(r)
        d["batch_status"] = get_batch_status(d["batch"])
        result.append(d)
    return result


@app.get("/api/admin/alumni/students-without-record")
def list_students_without_alumni_record(batch: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """List students who don't have an alumni record yet, with batch status."""
    query = """
        SELECT s.id, s.full_name, s.enrollment_number, s.degree_program, s.batch
        FROM students s
        LEFT JOIN alumni a ON a.student_id = s.id
        WHERE a.id IS NULL
    """
    params = {}
    if batch:
        query += " AND s.batch = :batch"
        params["batch"] = batch
    query += " ORDER BY s.full_name"
    
    rows = db.execute(text(query), params).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        d["batch_status"] = get_batch_status(d["batch"])
        result.append(d)
    return result


@app.get("/api/admin/batches-list")
def list_all_batches_with_status(status: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """
    Return all distinct batches with computed status.
    Query param: status=active|graduated| (empty = all)
    """
    query = """
        SELECT DISTINCT s.batch
        FROM students s
        ORDER BY s.batch DESC
    """
    rows = db.execute(text(query)).mappings().all()
    
    result = []
    for r in rows:
        batch_name = r["batch"]
        batch_status = get_batch_status(batch_name)
        
        if status and batch_status != status:
            continue
        
        result.append({
            "batch": batch_name,
            "status": batch_status
        })
    
    return result


def _issue_alumni_action_link(db: DBSession, alumnus_id: str, name: str, email: str, action_type: str, target_id: str = None):
    """Issue a single-use action link for alumni forms."""
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
    subject = "OBE Alumni Program - Action Required"
    
    if action_type == "info_confirmation":
        subject = "Confirm Your Information | OBE Alumni Program"
        body = f"<p>Hi {name},</p><p>Please confirm your information: <a href='{link}'>Click here</a></p>"
    elif action_type == "exit_survey":
        subject = "Exit Survey | OBE Alumni Program"
        body = f"<p>Hi {name},</p><p>Your exit survey is ready: <a href='{link}'>Click here</a></p>"
    elif action_type == "feedback_form":
        subject = "Alumni Feedback Form | OBE Alumni Program"
        body = f"<p>Hi {name},</p><p>We'd love your feedback: <a href='{link}'>Click here</a></p>"
    
    # Email sending logic here (Gmail SMTP, etc.)
    # Placeholder for now


@app.post("/api/admin/alumni/campaigns/info-confirmation")
def send_alumni_info_confirmation_campaign(data: AlumniCampaignRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Send info confirmation form links to pending alumni in a batch."""
    rows = db.execute(
        text("""SELECT a.id, a.email, s.full_name FROM alumni a JOIN students s ON a.student_id = s.id
                LEFT JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id
                WHERE s.batch = :batch AND ic.submitted_at IS NULL"""),
        {"batch": data.batch}
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No pending alumni found for batch {data.batch}")

    sent = 0
    for r in rows:
        _issue_alumni_action_link(db, r["id"], r["full_name"], r["email"], "info_confirmation")
        sent += 1
    return {"sent": sent}


@app.post("/api/admin/alumni/campaigns/exit-survey")
def send_alumni_exit_survey_campaign(data: AlumniCampaignRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Send exit survey links to alumni who've confirmed their info."""
    rows = db.execute(
        text("""SELECT a.id, a.email, s.full_name FROM alumni a
                JOIN students s ON a.student_id = s.id
                JOIN alumni_info_confirmations ic ON ic.alumnus_id = a.id AND ic.submitted_at IS NOT NULL
                LEFT JOIN alumni_exit_surveys es ON es.alumnus_id = a.id
                WHERE s.batch = :batch AND es.submitted_at IS NULL"""),
        {"batch": data.batch}
    ).mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No eligible alumni found for batch {data.batch} "
                                                       f"(info confirmation must be completed first)")

    sent = 0
    for r in rows:
        _issue_alumni_action_link(db, r["id"], r["full_name"], r["email"], "exit_survey")
        sent += 1
    return {"sent": sent}


@app.post("/api/admin/alumni/campaigns/feedback-form")
def send_alumni_feedback_campaign(data: AlumniFeedbackCampaignRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Send alumni feedback form links for a specific survey year."""
    rows = db.execute(
        text("SELECT a.id, a.email, s.full_name FROM alumni a JOIN students s ON a.student_id = s.id WHERE s.batch = :batch"),
        {"batch": data.batch}
    ).mappings().all()
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
        _issue_alumni_action_link(db, r["id"], r["full_name"], r["email"], "feedback_form", target_id=form_id)
        sent += 1

    return {"sent": sent, "skipped_already_has_form_for_year": skipped}


# --- Serve frontend (static files) ---

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