from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session as DBSession
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional
import uuid
import os
import secrets
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="Employer Feedback Panel")

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

class SurveySubmission(BaseModel):
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
    comments: Optional[str] = ""

class ValidationUpdate(BaseModel):
    engagement_id: str
    status: str  # "confirmed" or "rejected"

# --- Auth ---

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
            "expires_at": datetime.utcnow() + timedelta(days=7)
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
                CASE WHEN es.submitted_at IS NOT NULL THEN 'submitted' ELSE 'pending' END AS feedback_status,
                es.submitted_at AS feedback_submitted_at
            FROM engagements e
            JOIN students s ON e.student_id = s.id
            LEFT JOIN org_proformas op ON e.id = op.engagement_id
            LEFT JOIN employer_surveys es ON e.id = es.engagement_id
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

# --- Feedback Form ---

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
        text("SELECT id FROM employer_surveys WHERE engagement_id = :engagement_id"),
        {"engagement_id": data.engagement_id}
    ).mappings().first()

    survey_year = str(datetime.utcnow().year)

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
                     rating_professionalism, rating_ethics, rating_learning_attitude, comments, submitted_at)
                VALUES
                    (:id, :engagement_id, :survey_year, :current_job_role, :employment_department, :employment_duration,
                     :rating_core_knowledge, :rating_knowledge_application, :rating_problem_solving,
                     :rating_dev_contribution, :rating_tool_usage, :rating_teamwork, :rating_communication,
                     :rating_professionalism, :rating_ethics, :rating_learning_attitude, :comments, NOW())
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
                "comments": data.comments,
            }
        )

    db.commit()
    return {"survey_id": survey_id, "message": "Feedback submitted successfully"}

# --- Health ---

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

# --- Serve frontend (static files) ---

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        file_path = os.path.join(STATIC_DIR, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))