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

app = FastAPI(title="Employer Feedback Panel")

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

        db.commit()
    except Exception as e:
        print(f"[STARTUP] Migration note: {e}")
    finally:
        db.close()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Dependency ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_session_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    return authorization.replace("Bearer ", "")

def get_current_employer(token: str = Depends(get_session_token), db: DBSession = Depends(get_db)):
    row = db.execute(
        text("SELECT s.employer_id, e.name, e.work_email, e.designation FROM sessions s JOIN employers e ON s.employer_id = e.id WHERE s.token = :token AND s.expires_at > NOW()"),
        {"token": token}
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return dict(row)

# --- Schemas ---

class LoginRequest(BaseModel):
    token: str

class RequestLinkRequest(BaseModel):
    email: str

class PasswordLoginRequest(BaseModel):
    email: str
    password: str

class AdminLoginRequest(BaseModel):
    email: str
    password: str

class CreateAdminRequest(BaseModel):
    email: str
    name: str
    password: str

class SurveySubmission(BaseModel):
    """Employer Survey — for graduate/job engagements (10 GA-rated questions)."""
    engagement_id: str
    current_job_role: Optional[str] = ""
    employment_department: Optional[str] = ""
    employment_duration: Optional[str] = ""
    rating_core_knowledge: int
    rating_knowledge_application: int
    rating_problem_solving: int
    rating_dev_contribution: int
    rating_tool_usage: int
    rating_teamwork: int
    rating_communication: int
    rating_professionalism: int
    rating_ethics: int
    rating_learning_attitude: int
    overall_performance: Optional[str] = ""  # outstanding / above_average / average / below_average
    comments: Optional[str] = ""


class InternshipEvalSubmission(BaseModel):
    """Internship Evaluation Proforma — for internship engagements (9 GA-rated + Section D)."""
    engagement_id: str
    # Section C — 9 indicators (1-5)
    rating_core_knowledge: int
    rating_problem_solving: int
    rating_work_quality: int
    rating_tool_usage: int
    rating_teamwork: int
    rating_communication: int
    rating_societal_awareness: int
    rating_ethics: int
    rating_learning_attitude: int
    # Section D
    rating_task_completion: int       # 1-5
    rating_overall_competence: int    # 1-5
    attendance_bracket: str           # above_80 / 71_80 / 61_70 / 50_60 / below_50
    comments: Optional[str] = ""

class ValidationUpdate(BaseModel):
    engagement_id: str
    status: str  # "confirmed" or "rejected"

class ProformaEdit(BaseModel):
    engagement_id: str
    organization_name: Optional[str] = None
    role_designation: Optional[str] = None
    department_served: Optional[str] = None
    supervisor_name: Optional[str] = None
    supervisor_designation: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    linkedin_url: Optional[str] = None

# --- Auth ---

@app.post("/api/auth/request-link")
def request_magic_link(data: RequestLinkRequest, db: DBSession = Depends(get_db)):
    """Employer requests a sign-in link by email. Always returns success
    to avoid leaking whether the email exists in the system."""
    email = data.email.strip().lower()

    emp = db.execute(
        text("SELECT id, name, work_email FROM employers WHERE work_email = :email"),
        {"email": email}
    ).mappings().first()

    if emp:
        # Generate magic link
        token = secrets.token_urlsafe(32)
        db.execute(
            text("INSERT INTO magic_links (id, employer_id, token, expires_at) VALUES (:id, :employer_id, :token, :expires_at)"),
            {
                "id": str(uuid.uuid4()),
                "employer_id": emp["id"],
                "token": token,
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=48),
            }
        )
        db.commit()

        # Send email (fail silently — don't reveal email status to caller)
        try:
            send_magic_link_email(emp["work_email"], emp["name"], token)
        except Exception as e:
            import traceback
            traceback.print_exc()

    # Always return success
    return {"message": "If this email is registered, a sign-in link has been sent."}


@app.post("/api/auth/login-password")
def login_with_password(data: PasswordLoginRequest, db: DBSession = Depends(get_db)):
    """Authenticate employer with email + password."""
    email = data.email.strip().lower()

    emp = db.execute(
        text("SELECT id, name, work_email, designation, password_hash FROM employers WHERE work_email = :email"),
        {"email": email}
    ).mappings().first()

    if not emp or not emp["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not verify_password(data.password, emp["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Create session
    session_token = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO sessions (id, employer_id, token, expires_at) VALUES (:id, :employer_id, :token, :expires_at)"),
        {
            "id": session_id,
            "employer_id": emp["id"],
            "token": session_token,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7)
        }
    )
    db.commit()

    return {
        "session_token": session_token,
        "employer": {
            "name": emp["name"],
            "email": emp["work_email"],
            "designation": emp["designation"],
        }
    }


@app.post("/api/auth/login")
def login(request: LoginRequest, db: DBSession = Depends(get_db)):
    row = db.execute(
        text("SELECT id, employer_id FROM magic_links WHERE token = :token AND expires_at > NOW() AND used_at IS NULL"),
        {"token": request.token}
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid or expired link")

    # Mark magic link as used
    db.execute(
        text("UPDATE magic_links SET used_at = NOW() WHERE id = :id"),
        {"id": row["id"]}
    )

    # Create session
    session_token = secrets.token_urlsafe(32)
    session_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO sessions (id, employer_id, token, expires_at) VALUES (:id, :employer_id, :token, :expires_at)"),
        {
            "id": session_id,
            "employer_id": row["employer_id"],
            "token": session_token,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7)
        }
    )
    db.commit()

    # Get employer info
    emp = db.execute(
        text("SELECT name, work_email, designation FROM employers WHERE id = :id"),
        {"id": row["employer_id"]}
    ).mappings().first()

    return {
        "session_token": session_token,
        "employer": {
            "name": emp["name"] if emp else None,
            "email": emp["work_email"] if emp else None,
            "designation": emp["designation"] if emp else None,
        }
    }

@app.post("/api/auth/logout")
def logout(token: str = Depends(get_session_token), db: DBSession = Depends(get_db)):
    db.execute(text("DELETE FROM sessions WHERE token = :token"), {"token": token})
    db.commit()
    return {"message": "Logged out"}

# --- Dashboard ---

@app.get("/api/dashboard")
def get_dashboard(employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT
                e.id AS engagement_id,
                e.type AS engagement_type,
                e.status AS engagement_status,
                s.full_name,
                s.enrollment_number,
                s.degree_program,
                s.batch,
                op.organization_name,
                op.role_designation,
                op.department_served,
                op.start_date,
                op.end_date,
                op.supervisor_name,
                op.supervisor_designation,
                op.contact_email,
                op.contact_phone,
                op.linkedin_url,
                op.validation_status,
                op.submitted_by_student_at,
                CASE
                    WHEN e.type = 'internship' AND ie.submitted_at IS NOT NULL THEN 'submitted'
                    WHEN e.type = 'job' AND es.submitted_at IS NOT NULL THEN 'submitted'
                    ELSE 'pending'
                END AS feedback_status,
                COALESCE(ie.submitted_at, es.submitted_at) AS feedback_submitted_at
            FROM engagements e
            JOIN students s ON e.student_id = s.id
            LEFT JOIN org_proformas op ON e.id = op.engagement_id
            LEFT JOIN employer_surveys es ON e.id = es.engagement_id
            LEFT JOIN internship_evaluations ie ON e.id = ie.engagement_id
            WHERE e.employer_id = :employer_id
            ORDER BY e.created_at DESC
        """),
        {"employer_id": employer["employer_id"]}
    ).mappings().all()

    return {
        "employer": {
            "name": employer["name"],
            "email": employer["work_email"],
            "designation": employer["designation"],
        },
        "engagements": [dict(r) for r in rows]
    }

# --- Validate org_proforma ---

@app.post("/api/validate")
def validate_proforma(data: ValidationUpdate, employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    # Verify engagement belongs to this employer
    eng = db.execute(
        text("SELECT id FROM engagements WHERE id = :id AND employer_id = :employer_id"),
        {"id": data.engagement_id, "employer_id": employer["employer_id"]}
    ).mappings().first()

    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    result = db.execute(
        text("UPDATE org_proformas SET validation_status = :status, validated_by_employer_at = NOW() WHERE engagement_id = :engagement_id"),
        {"status": data.status, "engagement_id": data.engagement_id}
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="No proforma found for this engagement")

    return {"message": f"Details {data.status} successfully"}


@app.post("/api/proforma/edit")
def edit_proforma(data: ProformaEdit, employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    """Employer edits student-submitted proforma fields, then auto-validates as 'edited'."""
    eng = db.execute(
        text("SELECT id FROM engagements WHERE id = :id AND employer_id = :employer_id"),
        {"id": data.engagement_id, "employer_id": employer["employer_id"]}
    ).mappings().first()
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    updates = data.model_dump(exclude={"engagement_id"}, exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["engagement_id"] = data.engagement_id
    db.execute(
        text(f"UPDATE org_proformas SET {set_clauses}, validation_status = 'edited', validated_by_employer_at = NOW() WHERE engagement_id = :engagement_id"),
        updates
    )
    db.commit()
    return {"message": "Details updated and confirmed"}


# --- Feedback Form ---

# --- Internship Evaluation (for internship engagements) ---

@app.get("/api/internship-eval/{engagement_id}")
def get_internship_eval(engagement_id: str, employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    eng = db.execute(
        text("SELECT id, type FROM engagements WHERE id = :id AND employer_id = :employer_id"),
        {"id": engagement_id, "employer_id": employer["employer_id"]}
    ).mappings().first()
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    if eng["type"] != "internship":
        raise HTTPException(status_code=400, detail="This engagement is not an internship")

    evaluation = db.execute(
        text("SELECT * FROM internship_evaluations WHERE engagement_id = :engagement_id"),
        {"engagement_id": engagement_id}
    ).mappings().first()

    student = db.execute(
        text("""
            SELECT s.full_name, s.enrollment_number, s.batch, s.degree_program, s.current_semester,
                   op.organization_name, op.role_designation, op.department_served,
                   op.start_date, op.end_date
            FROM engagements e
            JOIN students s ON e.student_id = s.id
            LEFT JOIN org_proformas op ON e.id = op.engagement_id
            WHERE e.id = :engagement_id
        """),
        {"engagement_id": engagement_id}
    ).mappings().first()

    return {
        "student": dict(student) if student else None,
        "evaluation": dict(evaluation) if evaluation else None,
    }


@app.post("/api/internship-eval/submit")
def submit_internship_eval(data: InternshipEvalSubmission, employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    eng = db.execute(
        text("SELECT id, type FROM engagements WHERE id = :id AND employer_id = :employer_id"),
        {"id": data.engagement_id, "employer_id": employer["employer_id"]}
    ).mappings().first()
    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")
    if eng["type"] != "internship":
        raise HTTPException(status_code=400, detail="This engagement is not an internship")

    existing = db.execute(
        text("SELECT id, submitted_at FROM internship_evaluations WHERE engagement_id = :engagement_id"),
        {"engagement_id": data.engagement_id}
    ).mappings().first()

    if existing and existing["submitted_at"] is not None:
        raise HTTPException(status_code=409, detail="This evaluation has already been submitted and cannot be edited.")

    eval_id = existing["id"] if existing else str(uuid.uuid4())
    params = {
        "id": eval_id,
        "engagement_id": data.engagement_id,
        "rating_core_knowledge": data.rating_core_knowledge,
        "rating_problem_solving": data.rating_problem_solving,
        "rating_work_quality": data.rating_work_quality,
        "rating_tool_usage": data.rating_tool_usage,
        "rating_teamwork": data.rating_teamwork,
        "rating_communication": data.rating_communication,
        "rating_societal_awareness": data.rating_societal_awareness,
        "rating_ethics": data.rating_ethics,
        "rating_learning_attitude": data.rating_learning_attitude,
        "rating_task_completion": data.rating_task_completion,
        "rating_overall_competence": data.rating_overall_competence,
        "attendance_bracket": data.attendance_bracket,
        "comments": data.comments,
    }

    if existing:
        db.execute(text("""
            UPDATE internship_evaluations SET
                rating_core_knowledge = :rating_core_knowledge,
                rating_problem_solving = :rating_problem_solving,
                rating_work_quality = :rating_work_quality,
                rating_tool_usage = :rating_tool_usage,
                rating_teamwork = :rating_teamwork,
                rating_communication = :rating_communication,
                rating_societal_awareness = :rating_societal_awareness,
                rating_ethics = :rating_ethics,
                rating_learning_attitude = :rating_learning_attitude,
                rating_task_completion = :rating_task_completion,
                rating_overall_competence = :rating_overall_competence,
                attendance_bracket = :attendance_bracket,
                comments = :comments,
                submitted_at = NOW()
            WHERE id = :id
        """), params)
    else:
        db.execute(text("""
            INSERT INTO internship_evaluations
                (id, engagement_id, rating_core_knowledge, rating_problem_solving, rating_work_quality,
                 rating_tool_usage, rating_teamwork, rating_communication, rating_societal_awareness,
                 rating_ethics, rating_learning_attitude, rating_task_completion, rating_overall_competence,
                 attendance_bracket, comments, submitted_at)
            VALUES
                (:id, :engagement_id, :rating_core_knowledge, :rating_problem_solving, :rating_work_quality,
                 :rating_tool_usage, :rating_teamwork, :rating_communication, :rating_societal_awareness,
                 :rating_ethics, :rating_learning_attitude, :rating_task_completion, :rating_overall_competence,
                 :attendance_bracket, :comments, NOW())
        """), params)

    db.commit()
    return {"eval_id": eval_id, "message": "Internship evaluation submitted successfully"}


# --- Employer Survey (for graduate/job engagements) ---

@app.get("/api/survey/{engagement_id}")
def get_survey(engagement_id: str, employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    # Verify engagement belongs to this employer
    eng = db.execute(
        text("SELECT id FROM engagements WHERE id = :id AND employer_id = :employer_id"),
        {"id": engagement_id, "employer_id": employer["employer_id"]}
    ).mappings().first()

    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    # Get existing survey if any
    survey = db.execute(
        text("SELECT * FROM employer_surveys WHERE engagement_id = :engagement_id"),
        {"engagement_id": engagement_id}
    ).mappings().first()

    # Get student info
    student = db.execute(
        text("""
            SELECT s.full_name, s.enrollment_number, s.batch, s.degree_program,
                   op.organization_name, op.role_designation, op.department_served,
                   op.start_date, op.end_date
            FROM engagements e
            JOIN students s ON e.student_id = s.id
            LEFT JOIN org_proformas op ON e.id = op.engagement_id
            WHERE e.id = :engagement_id
        """),
        {"engagement_id": engagement_id}
    ).mappings().first()

    return {
        "student": dict(student) if student else None,
        "survey": dict(survey) if survey else None,
    }

@app.post("/api/survey/submit")
def submit_survey(data: SurveySubmission, employer: dict = Depends(get_current_employer), db: DBSession = Depends(get_db)):
    # Verify engagement belongs to this employer
    eng = db.execute(
        text("SELECT id FROM engagements WHERE id = :id AND employer_id = :employer_id"),
        {"id": data.engagement_id, "employer_id": employer["employer_id"]}
    ).mappings().first()

    if not eng:
        raise HTTPException(status_code=404, detail="Engagement not found")

    # Check for existing survey
    existing = db.execute(
        text("SELECT id, submitted_at FROM employer_surveys WHERE engagement_id = :engagement_id"),
        {"engagement_id": data.engagement_id}
    ).mappings().first()

    # Block re-editing after submission
    if existing and existing["submitted_at"] is not None:
        raise HTTPException(
            status_code=409,
            detail="Feedback has already been submitted for this student and cannot be edited. Contact the institution if corrections are needed."
        )

    survey_year = str(datetime.now(timezone.utc).year)

    if existing:
        db.execute(
            text("""
                UPDATE employer_surveys SET
                    current_job_role = :current_job_role,
                    employment_department = :employment_department,
                    employment_duration = :employment_duration,
                    rating_core_knowledge = :rating_core_knowledge,
                    rating_knowledge_application = :rating_knowledge_application,
                    rating_problem_solving = :rating_problem_solving,
                    rating_dev_contribution = :rating_dev_contribution,
                    rating_tool_usage = :rating_tool_usage,
                    rating_teamwork = :rating_teamwork,
                    rating_communication = :rating_communication,
                    rating_professionalism = :rating_professionalism,
                    rating_ethics = :rating_ethics,
                    rating_learning_attitude = :rating_learning_attitude,
                    overall_performance = :overall_performance,
                    comments = :comments,
                    submitted_at = NOW()
                WHERE id = :id
            """),
            {
                "id": existing["id"],
                "current_job_role": data.current_job_role,
                "employment_department": data.employment_department,
                "employment_duration": data.employment_duration,
                "rating_core_knowledge": data.rating_core_knowledge,
                "rating_knowledge_application": data.rating_knowledge_application,
                "rating_problem_solving": data.rating_problem_solving,
                "rating_dev_contribution": data.rating_dev_contribution,
                "rating_tool_usage": data.rating_tool_usage,
                "rating_teamwork": data.rating_teamwork,
                "rating_communication": data.rating_communication,
                "rating_professionalism": data.rating_professionalism,
                "rating_ethics": data.rating_ethics,
                "rating_learning_attitude": data.rating_learning_attitude,
                "overall_performance": data.overall_performance,
                "comments": data.comments,
            }
        )
        survey_id = existing["id"]
    else:
        survey_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO employer_surveys
                    (id, engagement_id, survey_year, current_job_role, employment_department, employment_duration,
                     rating_core_knowledge, rating_knowledge_application, rating_problem_solving,
                     rating_dev_contribution, rating_tool_usage, rating_teamwork, rating_communication,
                     rating_professionalism, rating_ethics, rating_learning_attitude, overall_performance, comments, submitted_at)
                VALUES
                    (:id, :engagement_id, :survey_year, :current_job_role, :employment_department, :employment_duration,
                     :rating_core_knowledge, :rating_knowledge_application, :rating_problem_solving,
                     :rating_dev_contribution, :rating_tool_usage, :rating_teamwork, :rating_communication,
                     :rating_professionalism, :rating_ethics, :rating_learning_attitude, :overall_performance, :comments, NOW())
            """),
            {
                "id": survey_id,
                "engagement_id": data.engagement_id,
                "survey_year": survey_year,
                "current_job_role": data.current_job_role,
                "employment_department": data.employment_department,
                "employment_duration": data.employment_duration,
                "rating_core_knowledge": data.rating_core_knowledge,
                "rating_knowledge_application": data.rating_knowledge_application,
                "rating_problem_solving": data.rating_problem_solving,
                "rating_dev_contribution": data.rating_dev_contribution,
                "rating_tool_usage": data.rating_tool_usage,
                "rating_teamwork": data.rating_teamwork,
                "rating_communication": data.rating_communication,
                "rating_professionalism": data.rating_professionalism,
                "rating_ethics": data.rating_ethics,
                "rating_learning_attitude": data.rating_learning_attitude,
                "overall_performance": data.overall_performance,
                "comments": data.comments,
            }
        )

    db.commit()
    return {"survey_id": survey_id, "message": "Feedback submitted successfully"}

# --- Health ---

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

# --- Email / Admin ---

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logger = logging.getLogger(__name__)

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8000")

def send_magic_link_email(employer_email: str, employer_name: str, token: str, password: str = None, engagement_type: str = None):
    access_url = f"{APP_URL}/?token={token}"
    login_url = APP_URL

    # Tailor message for intern vs graduate
    if engagement_type == "internship":
        context_line = "You are invited to evaluate the performance of an <strong>intern</strong> who completed their internship under your supervision."
        subject_suffix = "Internship Evaluation"
    elif engagement_type == "job":
        context_line = "You are invited to provide feedback on a <strong>graduate</strong> of our program who is currently employed at your organization."
        subject_suffix = "Graduate Feedback"
    else:
        context_line = "You are invited to provide feedback on students who have completed their internship or employment under your supervision."
        subject_suffix = "Access Link"

    credentials_block = ""
    if password:
        credentials_block = f"""
            <div style="background: #f8f9fb; border: 1px solid #e2e6ed; border-radius: 6px; padding: 1.25rem; margin: 1.25rem 0;">
                <p style="font-size: 13px; font-weight: 600; color: #1a1f2e; margin: 0 0 0.75rem;">Your login credentials</p>
                <table style="font-size: 14px; color: #1a1f2e;">
                    <tr><td style="padding: 2px 0; color: #5a6274; width: 80px;">Portal:</td><td style="padding: 2px 0;"><a href="{login_url}" style="color: #2563eb;">{login_url}</a></td></tr>
                    <tr><td style="padding: 2px 0; color: #5a6274;">Email:</td><td style="padding: 2px 0; font-family: monospace;">{employer_email}</td></tr>
                    <tr><td style="padding: 2px 0; color: #5a6274;">Password:</td><td style="padding: 2px 0; font-family: monospace; font-weight: 600;">{password}</td></tr>
                </table>
                <p style="font-size: 12px; color: #8a91a0; margin: 0.75rem 0 0;">You can use these credentials to log in anytime, even after the magic link expires.</p>
            </div>
        """

    html = f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 2rem;">
        <div style="background: #1e3a5f; padding: 1.25rem 1.5rem; border-radius: 8px 8px 0 0;">
            <h1 style="color: #ffffff; font-size: 18px; margin: 0;">Employer Feedback Portal</h1>
        </div>
        <div style="background: #ffffff; border: 1px solid #e2e6ed; border-top: none; padding: 2rem 1.5rem; border-radius: 0 0 8px 8px;">
            <p style="font-size: 15px; color: #1a1f2e; margin: 0 0 1rem;">Dear {employer_name or 'Employer'},</p>
            <p style="font-size: 14px; color: #5a6274; line-height: 1.6; margin: 0 0 1.5rem;">
                {context_line}
            </p>

            <p style="font-size: 13px; font-weight: 600; color: #1a1f2e; margin: 0 0 0.5rem;">Quick access (one-click sign in):</p>
            <div style="text-align: center; margin: 1rem 0 1.5rem;">
                <a href="{access_url}"
                   style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none;
                          padding: 12px 28px; border-radius: 6px; font-size: 14px; font-weight: 600;">
                    Access feedback portal
                </a>
            </div>
            <p style="font-size: 12px; color: #8a91a0; margin: 0 0 1.5rem;">This link expires in 48 hours and can only be used once.</p>

            {credentials_block}

            <p style="font-size: 13px; color: #8a91a0; margin: 1.5rem 0 0; line-height: 1.5;">
                If you have any questions, please contact the Internship Coordinator
                at the Department of Computer Science and Software Engineering, Jinnah University for Women.
            </p>
        </div>
        <p style="font-size: 12px; color: #8a91a0; text-align: center; margin-top: 1rem;">
            Department of Computer Science &amp; Software Engineering, Jinnah University for Women, Karachi
        </p>
    </div>
    """

    print(f"[EMAIL] Step 1: Preparing message to {employer_email}")
    print(f"[EMAIL] SMTP_SERVER={SMTP_SERVER}, SMTP_PORT={SMTP_PORT}, SMTP_USER={SMTP_USER}")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Employer Feedback Portal - {subject_suffix}"
    msg["From"] = SMTP_USER
    msg["To"] = employer_email
    msg.attach(MIMEText(html, "html"))

    print(f"[EMAIL] Step 2: Connecting to {SMTP_SERVER}:{SMTP_PORT}...")
    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15)
    print(f"[EMAIL] Step 3: Connected. Starting TLS...")
    server.starttls()
    print(f"[EMAIL] Step 4: TLS started. Logging in as {SMTP_USER}...")
    server.login(SMTP_USER, SMTP_PASSWORD)
    print(f"[EMAIL] Step 5: Logged in. Sending email to {employer_email}...")
    server.sendmail(SMTP_USER, employer_email, msg.as_string())
    print(f"[EMAIL] Step 6: Email sent successfully!")
    server.quit()


class InviteRequest(BaseModel):
    email: str
    name: Optional[str] = ""
    designation: Optional[str] = ""
    engagement_type: Optional[str] = ""  # "internship" or "job"


class BulkEmailRequest(BaseModel):
    audience: str  # "all_employers", "intern_employers", "graduate_employers", "specific"
    emails: Optional[list] = []
    subject: str
    body: str


# --- Admin Auth ---

def get_current_admin(request: Request, db: DBSession = Depends(get_db)):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    session = db.execute(
        text("SELECT admin_id FROM admin_sessions WHERE token = :token AND expires_at > NOW()"),
        {"token": token}
    ).mappings().first()
    if not session:
        raise HTTPException(status_code=401, detail="Admin session expired or invalid")
    admin = db.execute(
        text("SELECT id, email, name FROM admins WHERE id = :id"),
        {"id": session["admin_id"]}
    ).mappings().first()
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    return dict(admin)


@app.post("/api/admin/login")
def admin_login(data: AdminLoginRequest, db: DBSession = Depends(get_db)):
    admin = db.execute(
        text("SELECT id, email, name, password_hash FROM admins WHERE email = :email"),
        {"email": data.email.strip().lower()}
    ).mappings().first()
    if not admin or not verify_password(data.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = secrets.token_urlsafe(32)
    db.execute(
        text("INSERT INTO admin_sessions (id, admin_id, token, expires_at) VALUES (:id, :aid, :token, :exp)"),
        {"id": str(uuid.uuid4()), "aid": admin["id"], "token": token, "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    )
    db.commit()
    return {"token": token, "admin": {"name": admin["name"], "email": admin["email"]}}


@app.get("/api/admin/check-setup")
def check_admin_setup(db: DBSession = Depends(get_db)):
    """Check if any admin accounts exist."""
    count = db.execute(text("SELECT COUNT(*) FROM admins")).scalar()
    return {"has_admins": count > 0}


@app.post("/api/admin/setup")
def create_first_admin(data: CreateAdminRequest, db: DBSession = Depends(get_db)):
    """Create the first admin account. Blocked after first admin exists."""
    count = db.execute(text("SELECT COUNT(*) FROM admins")).scalar()
    if count > 0:
        raise HTTPException(status_code=403, detail="Admin already exists. Log in and use Settings to add more.")
    admin_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :pw)"),
        {"id": admin_id, "email": data.email.strip().lower(), "name": data.name, "pw": hash_password(data.password)}
    )
    db.commit()
    return {"message": f"Admin account created for {data.email}"}


@app.post("/api/admin/create-admin")
def create_additional_admin(data: CreateAdminRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    admin_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO admins (id, email, name, password_hash) VALUES (:id, :email, :name, :pw)"),
        {"id": admin_id, "email": data.email.strip().lower(), "name": data.name, "pw": hash_password(data.password)}
    )
    db.commit()
    return {"message": f"Admin account created for {data.email}"}


@app.get("/api/admin/me")
def admin_me(admin: dict = Depends(get_current_admin)):
    return admin


@app.get("/api/admin/dashboard-stats")
def admin_dashboard_stats(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    stats = {}
    stats["total_students"] = db.execute(text("SELECT COUNT(*) FROM students")).scalar()
    stats["total_employers"] = db.execute(text("SELECT COUNT(*) FROM employers")).scalar()
    stats["total_engagements"] = db.execute(text("SELECT COUNT(*) FROM engagements")).scalar()
    stats["internship_engagements"] = db.execute(text("SELECT COUNT(*) FROM engagements WHERE type = 'internship'")).scalar()
    stats["job_engagements"] = db.execute(text("SELECT COUNT(*) FROM engagements WHERE type = 'job'")).scalar()
    stats["proformas_validated"] = db.execute(text("SELECT COUNT(*) FROM org_proformas WHERE validation_status IN ('validated', 'edited')")).scalar()
    stats["proformas_pending"] = db.execute(text("SELECT COUNT(*) FROM org_proformas WHERE validation_status = 'pending'")).scalar()
    stats["surveys_submitted"] = db.execute(text("SELECT COUNT(*) FROM employer_surveys WHERE submitted_at IS NOT NULL")).scalar()
    stats["evals_submitted"] = db.execute(text("SELECT COUNT(*) FROM internship_evaluations WHERE submitted_at IS NOT NULL")).scalar()
    return stats


@app.get("/api/admin/students")
def list_students(q: str = "", tag: str = "", admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """List students with derived tags: graduate (has job engagement), intern (has internship engagement), enrolled (no engagement)."""
    query = """
        SELECT s.id, s.full_name, s.enrollment_number, s.degree_program, s.batch, s.current_semester,
               BOOL_OR(e.type = 'job') AS is_graduate,
               BOOL_OR(e.type = 'internship') AS is_intern
        FROM students s
        LEFT JOIN engagements e ON s.id = e.student_id
    """
    params = {}
    conditions = []
    if q:
        conditions.append("(LOWER(s.full_name) LIKE :q OR LOWER(s.enrollment_number) LIKE :q)")
        params["q"] = f"%{q.lower()}%"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY s.id ORDER BY s.full_name"

    rows = db.execute(text(query), params).mappings().all()
    result = []
    for r in rows:
        d = dict(r)
        if d["is_graduate"]:
            d["tag"] = "graduate"
        elif d["is_intern"]:
            d["tag"] = "intern"
        else:
            d["tag"] = "enrolled"
        result.append(d)

    if tag:
        result = [r for r in result if r["tag"] == tag]
    return result


@app.post("/api/admin/send-email")
def send_bulk_email(data: BulkEmailRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Send email to selected audience."""
    if data.audience == "all_employers":
        rows = db.execute(text("SELECT DISTINCT work_email, name FROM employers")).mappings().all()
    elif data.audience == "intern_employers":
        rows = db.execute(text("""
            SELECT DISTINCT e.work_email, e.name FROM employers e
            JOIN engagements eng ON e.id = eng.employer_id WHERE eng.type = 'internship'
        """)).mappings().all()
    elif data.audience == "graduate_employers":
        rows = db.execute(text("""
            SELECT DISTINCT e.work_email, e.name FROM employers e
            JOIN engagements eng ON e.id = eng.employer_id WHERE eng.type = 'job'
        """)).mappings().all()
    elif data.audience == "specific":
        rows = [{"work_email": e, "name": e.split("@")[0]} for e in data.emails]
    else:
        raise HTTPException(status_code=400, detail="Invalid audience")

    sent = 0
    failed = 0
    for r in rows:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = data.subject
            msg["From"] = SMTP_FROM
            msg["To"] = r["work_email"]
            html = f"""
            <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 2rem;">
                <div style="background: #1e3a5f; padding: 1.25rem 1.5rem; border-radius: 8px 8px 0 0;">
                    <h1 style="color: #ffffff; font-size: 18px; margin: 0;">Employer Feedback Portal</h1>
                </div>
                <div style="background: #ffffff; border: 1px solid #e2e6ed; border-top: none; padding: 2rem 1.5rem; border-radius: 0 0 8px 8px;">
                    <p style="font-size: 15px; color: #1a1f2e; margin: 0 0 1rem;">Dear {r['name'] or 'Employer'},</p>
                    <div style="font-size: 14px; color: #5a6274; line-height: 1.6;">{data.body}</div>
                    <p style="font-size: 13px; color: #8a91a0; margin: 1.5rem 0 0; line-height: 1.5;">
                        Department of Computer Science and Software Engineering, Jinnah University for Women, Karachi
                    </p>
                </div>
            </div>
            """
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(SMTP_USER, [r["work_email"]], msg.as_string())
            sent += 1
        except Exception as e:
            print(f"Failed to send to {r['work_email']}: {e}")
            failed += 1

    return {"sent": sent, "failed": failed, "total": len(rows)}


# --- Admin Protected Routes ---

@app.post("/api/admin/invite")
def invite_employer(data: InviteRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Create employer (if not exists) and send magic link email with login credentials."""
    # Find or create employer
    emp = db.execute(
        text("SELECT id, name, work_email, password_hash FROM employers WHERE work_email = :email"),
        {"email": data.email}
    ).mappings().first()

    plain_password = None  # Only set for new employers or those without a password

    if emp:
        employer_id = emp["id"]
        employer_name = emp["name"]
        if data.name:
            db.execute(
                text("UPDATE employers SET name = :name, designation = :designation WHERE id = :id"),
                {"name": data.name, "designation": data.designation, "id": employer_id}
            )
            employer_name = data.name
        # Generate password only if they don't have one yet
        if not emp["password_hash"]:
            plain_password = generate_password()
            db.execute(
                text("UPDATE employers SET password_hash = :pw WHERE id = :id"),
                {"pw": hash_password(plain_password), "id": employer_id}
            )
    else:
        employer_id = str(uuid.uuid4())
        employer_name = data.name
        plain_password = generate_password()
        db.execute(
            text("INSERT INTO employers (id, work_email, name, designation, created_via, password_hash) VALUES (:id, :email, :name, :designation, 'admin_invite', :pw)"),
            {"id": employer_id, "email": data.email, "name": data.name, "designation": data.designation, "pw": hash_password(plain_password)}
        )

    # Create magic link
    token = secrets.token_urlsafe(32)
    db.execute(
        text("INSERT INTO magic_links (id, employer_id, token, expires_at) VALUES (:id, :employer_id, :token, :expires_at)"),
        {
            "id": str(uuid.uuid4()),
            "employer_id": employer_id,
            "token": token,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=48),
        }
    )
    db.commit()

    # Send email
    access_url = f"{APP_URL}/?token={token}"
    try:
        send_magic_link_email(data.email, employer_name, token, plain_password, data.engagement_type or None)
        return {"message": f"Invitation sent to {data.email}", "sent": True}
    except Exception as e:
        import traceback
        traceback.print_exc()  # This will show in Render logs
        return {
            "message": f"Employer created but email failed: {str(e)}",
            "sent": False,
            "manual_url": access_url
        }

@app.get("/api/admin/employers")
def list_employers(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(text("""
        SELECT e.id, e.work_email, e.name, e.designation, e.created_at,
               COUNT(DISTINCT eng.id) AS total_engagements,
               COUNT(DISTINCT es.id) AS surveys_submitted,
               COUNT(DISTINCT ie.id) AS evals_submitted
        FROM employers e
        LEFT JOIN engagements eng ON e.id = eng.employer_id
        LEFT JOIN employer_surveys es ON eng.id = es.engagement_id AND es.submitted_at IS NOT NULL
        LEFT JOIN internship_evaluations ie ON eng.id = ie.engagement_id AND ie.submitted_at IS NOT NULL
        GROUP BY e.id
        ORDER BY e.created_at DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


@app.get("/api/admin/engagements")
def list_engagements(admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    rows = db.execute(text("""
        SELECT
            eng.id, eng.type, eng.status, eng.created_at,
            s.full_name AS student_name, s.enrollment_number, s.degree_program, s.batch,
            e.work_email AS employer_email, e.name AS employer_name,
            op.validation_status,
            CASE
                WHEN eng.type = 'internship' AND ie.submitted_at IS NOT NULL THEN 'submitted'
                WHEN eng.type = 'job' AND es.submitted_at IS NOT NULL THEN 'submitted'
                ELSE 'pending'
            END AS feedback_status
        FROM engagements eng
        JOIN students s ON eng.student_id = s.id
        JOIN employers e ON eng.employer_id = e.id
        LEFT JOIN org_proformas op ON eng.id = op.engagement_id
        LEFT JOIN employer_surveys es ON eng.id = es.engagement_id
        LEFT JOIN internship_evaluations ie ON eng.id = ie.engagement_id
        ORDER BY eng.created_at DESC
    """)).mappings().all()
    return [dict(r) for r in rows]


class CreateEngagementRequest(BaseModel):
    student_id: str
    employer_email: str
    engagement_type: str  # "internship" or "job"
    organization_name: Optional[str] = ""
    role_designation: Optional[str] = ""
    department_served: Optional[str] = ""
    supervisor_name: Optional[str] = ""
    supervisor_designation: Optional[str] = ""
    contact_email: Optional[str] = ""
    contact_phone: Optional[str] = ""
    start_date: Optional[str] = ""
    end_date: Optional[str] = ""
    send_invite: bool = True


@app.post("/api/admin/engagements")
def create_engagement(data: CreateEngagementRequest, admin: dict = Depends(get_current_admin), db: DBSession = Depends(get_db)):
    """Create engagement + proforma, optionally invite employer."""
    # Find or create employer
    emp = db.execute(
        text("SELECT id, name, work_email, password_hash FROM employers WHERE work_email = :email"),
        {"email": data.employer_email}
    ).mappings().first()

    plain_password = None
    if emp:
        employer_id = emp["id"]
        employer_name = emp["name"]
        if not emp["password_hash"]:
            plain_password = generate_password()
            db.execute(
                text("UPDATE employers SET password_hash = :pw WHERE id = :id"),
                {"pw": hash_password(plain_password), "id": employer_id}
            )
    else:
        employer_id = str(uuid.uuid4())
        employer_name = data.employer_email.split("@")[0]
        plain_password = generate_password()
        db.execute(
            text("INSERT INTO employers (id, work_email, name, created_via, password_hash) VALUES (:id, :email, :name, 'admin_panel', :pw)"),
            {"id": employer_id, "email": data.employer_email, "name": employer_name, "pw": hash_password(plain_password)}
        )

    # Create engagement
    engagement_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO engagements (id, student_id, employer_id, type, status) VALUES (:id, :sid, :eid, :type, 'active')"),
        {"id": engagement_id, "sid": data.student_id, "eid": employer_id, "type": data.engagement_type}
    )

    # Create proforma
    db.execute(
        text("""INSERT INTO org_proformas (id, engagement_id, organization_name, role_designation, department_served,
                supervisor_name, supervisor_designation, contact_email, contact_phone, start_date, end_date, validation_status)
                VALUES (:id, :eid, :org, :role, :dept, :sname, :sdesig, :cemail, :cphone,
                        NULLIF(:start, '')::date, NULLIF(:end, '')::date, 'pending')"""),
        {
            "id": str(uuid.uuid4()), "eid": engagement_id,
            "org": data.organization_name, "role": data.role_designation, "dept": data.department_served,
            "sname": data.supervisor_name, "sdesig": data.supervisor_designation,
            "cemail": data.contact_email, "cphone": data.contact_phone,
            "start": data.start_date, "end": data.end_date,
        }
    )
    db.commit()

    # Send invite email if requested
    result = {"message": "Engagement created", "engagement_id": engagement_id, "sent": False}
    if data.send_invite:
        try:
            token = secrets.token_urlsafe(32)
            db.execute(
                text("INSERT INTO magic_links (id, employer_id, token, expires_at) VALUES (:id, :eid, :token, :exp)"),
                {"id": str(uuid.uuid4()), "eid": employer_id, "token": token, "exp": datetime.now(timezone.utc) + timedelta(hours=48)}
            )
            db.commit()
            send_magic_link_email(data.employer_email, employer_name, token, plain_password, data.engagement_type)
            result["sent"] = True
            result["message"] = f"Engagement created and invitation sent to {data.employer_email}"
        except Exception as e:
            import traceback
            traceback.print_exc()
            result["message"] = f"Engagement created but email failed: {str(e)}"
            result["manual_url"] = f"{APP_URL}/?token={token}"

    return result

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