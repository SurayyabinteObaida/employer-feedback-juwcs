import csv
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session as DbSession

from app.core.database import get_db
from app.core.deps import require_admin_key
from app.models.models import Student, Engagement, OrgProforma, EngagementType, Employer
from app.services.auth_service import get_or_create_employer, issue_magic_link

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


class ManualEngagementIn(BaseModel):
    student_obe_id: str
    engagement_type: EngagementType
    organization_name: str
    role_designation: str | None = None
    supervisor_name: str
    supervisor_email: EmailStr
    supervisor_designation: str | None = None
    contact_phone: str | None = None


@router.post("/engagements")
def create_engagement(payload: ManualEngagementIn, db: DbSession = Depends(get_db)):
    student = db.query(Student).filter(Student.obe_student_id == payload.student_obe_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found — run the OBE sync first")

    employer = get_or_create_employer(
        db, work_email=payload.supervisor_email, name=payload.supervisor_name,
        designation=payload.supervisor_designation, created_via="manual",
    )

    engagement = Engagement(student_id=student.id, employer_id=employer.id, type=payload.engagement_type)
    db.add(engagement)
    db.flush()

    proforma = OrgProforma(
        engagement_id=engagement.id,
        organization_name=payload.organization_name,
        role_designation=payload.role_designation,
        supervisor_name=payload.supervisor_name,
        supervisor_designation=payload.supervisor_designation,
        contact_email=payload.supervisor_email,
        contact_phone=payload.contact_phone,
    )
    db.add(proforma)
    db.commit()

    issue_magic_link(db, employer)
    return {"message": "Engagement created and sign-in link sent", "engagement_id": engagement.id}


@router.post("/engagements/bulk")
def bulk_create_engagements(file: UploadFile = File(...), db: DbSession = Depends(get_db)):
    """CSV columns: student_obe_id, engagement_type, organization_name,
    role_designation, supervisor_name, supervisor_email,
    supervisor_designation, contact_phone"""
    content = file.file.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))

    created, errors = [], []
    emailed_employer_ids = set()

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        try:
            student = db.query(Student).filter(Student.obe_student_id == row["student_obe_id"]).first()
            if not student:
                errors.append(f"Row {i}: student {row['student_obe_id']} not found")
                continue

            employer = get_or_create_employer(
                db, work_email=row["supervisor_email"], name=row["supervisor_name"],
                designation=row.get("supervisor_designation"), created_via="bulk",
            )

            engagement = Engagement(
                student_id=student.id, employer_id=employer.id,
                type=EngagementType(row["engagement_type"]),
            )
            db.add(engagement)
            db.flush()

            proforma = OrgProforma(
                engagement_id=engagement.id,
                organization_name=row["organization_name"],
                role_designation=row.get("role_designation"),
                supervisor_name=row["supervisor_name"],
                supervisor_designation=row.get("supervisor_designation"),
                contact_email=row["supervisor_email"],
                contact_phone=row.get("contact_phone"),
            )
            db.add(proforma)
            created.append(engagement)

            if employer.id not in emailed_employer_ids:
                emailed_employer_ids.add(employer.id)

        except Exception as e:
            errors.append(f"Row {i}: {e}")

    db.commit()

    # Send one magic link per unique employer after all rows are committed
    for employer_id in emailed_employer_ids:
        employer = db.query(Employer).filter(Employer.id == employer_id).first()
        if employer:
            issue_magic_link(db, employer)

    return {"created": len(created), "errors": errors}
