import enum
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Text,
    SmallInteger, Boolean, Integer, UniqueConstraint, func
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


def gen_uuid():
    return str(uuid.uuid4())


class EngagementType(str, enum.Enum):
    internship = "internship"
    job = "job"


class ValidationStatus(str, enum.Enum):
    pending = "pending"
    validated = "validated"
    edited = "edited"


class EngagementStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    archived = "archived"


class Recommend(str, enum.Enum):
    yes = "yes"
    no = "no"
    maybe = "maybe"


# Read-only mirror of the OBE student table, populated by the sync job.
# Only the fields this app needs to display are kept — this is not the
# source of truth, the OBE MySQL `student` table is.
class Student(Base):
    __tablename__ = "students"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    obe_student_id = Column(String, unique=True, nullable=False, index=True)  # FK back to OBE student table
    full_name = Column(String, nullable=False)
    enrollment_number = Column(String, nullable=False)
    degree_program = Column(String, nullable=True)
    batch = Column(String, nullable=True)
    current_semester = Column(String, nullable=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    engagements = relationship("Engagement", back_populates="student")


class Employer(Base):
    __tablename__ = "employers"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    work_email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=True)
    designation = Column(String, nullable=True)
    created_via = Column(String, nullable=False, default="manual")  # manual | bulk
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    engagements = relationship("Engagement", back_populates="employer")
    magic_links = relationship("MagicLink", back_populates="employer")
    sessions = relationship("Session", back_populates="employer")


class Engagement(Base):
    __tablename__ = "engagements"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    student_id = Column(UUID(as_uuid=False), ForeignKey("students.id"), nullable=False)
    employer_id = Column(UUID(as_uuid=False), ForeignKey("employers.id"), nullable=False)
    type = Column(Enum(EngagementType), nullable=False)
    status = Column(Enum(EngagementStatus), nullable=False, default=EngagementStatus.active)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    student = relationship("Student", back_populates="engagements")
    employer = relationship("Employer", back_populates="engagements")
    proforma = relationship("OrgProforma", back_populates="engagement", uselist=False)
    internship_evaluation = relationship("InternshipEvaluation", back_populates="engagement", uselist=False)
    employer_surveys = relationship("EmployerSurvey", back_populates="engagement")


# Submitted by the student/graduate; validated (and optionally edited) by the employer.
# Generalized version of the intern-details fields — used for both internship and job tracks.
class OrgProforma(Base):
    __tablename__ = "org_proformas"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    engagement_id = Column(UUID(as_uuid=False), ForeignKey("engagements.id"), unique=True, nullable=False)

    organization_name = Column(String, nullable=False)
    role_designation = Column(String, nullable=True)  # job role or internship role, as applicable
    department_served = Column(String, nullable=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)  # nullable — jobs may be ongoing

    supervisor_name = Column(String, nullable=False)
    supervisor_designation = Column(String, nullable=True)
    contact_email = Column(String, nullable=False)
    contact_phone = Column(String, nullable=True)
    linkedin_url = Column(String, nullable=True)

    submitted_by_student_at = Column(DateTime(timezone=True), server_default=func.now())
    validated_by_employer_at = Column(DateTime(timezone=True), nullable=True)
    validation_status = Column(Enum(ValidationStatus), nullable=False, default=ValidationStatus.pending)

    engagement = relationship("Engagement", back_populates="proforma")


# Internship Evaluation Proforma — Section C (9 GA-mapped indicators) + Section D + E.
# One per internship engagement.
class InternshipEvaluation(Base):
    __tablename__ = "internship_evaluations"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    engagement_id = Column(UUID(as_uuid=False), ForeignKey("engagements.id"), unique=True, nullable=False)

    # Section C — 9 indicators, each 1-5
    rating_core_knowledge = Column(SmallInteger, nullable=True)
    rating_problem_solving = Column(SmallInteger, nullable=True)
    rating_dev_contribution = Column(SmallInteger, nullable=True)
    rating_tool_usage = Column(SmallInteger, nullable=True)
    rating_teamwork = Column(SmallInteger, nullable=True)
    rating_communication = Column(SmallInteger, nullable=True)
    rating_societal_awareness = Column(SmallInteger, nullable=True)
    rating_ethics = Column(SmallInteger, nullable=True)
    rating_learning_attitude = Column(SmallInteger, nullable=True)

    # Section D
    attendance_bracket = Column(String, nullable=True)  # below_50 | 51_70 | 71_plus
    task_completion = Column(String, nullable=True)      # on_time | minor_delays
    overall_rating = Column(String, nullable=True)        # excellent | good | fair
    recommend = Column(Enum(Recommend), nullable=True)

    # Section E
    comments = Column(Text, nullable=True)

    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    engagement = relationship("Engagement", back_populates="internship_evaluation")


# Employer Survey — Section C (10 GA-mapped indicators). Repeatable per academic year,
# manually spawned by admin/IC, never system-scheduled.
class EmployerSurvey(Base):
    __tablename__ = "employer_surveys"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    engagement_id = Column(UUID(as_uuid=False), ForeignKey("engagements.id"), nullable=False)
    survey_year = Column(String, nullable=False)  # e.g. "2025-2026"

    current_job_role = Column(String, nullable=True)
    employment_department = Column(String, nullable=True)
    employment_duration = Column(String, nullable=True)

    # Section C — 10 indicators, each 1-5
    rating_core_knowledge = Column(SmallInteger, nullable=True)
    rating_knowledge_application = Column(SmallInteger, nullable=True)
    rating_problem_solving = Column(SmallInteger, nullable=True)
    rating_dev_contribution = Column(SmallInteger, nullable=True)
    rating_tool_usage = Column(SmallInteger, nullable=True)
    rating_teamwork = Column(SmallInteger, nullable=True)
    rating_communication = Column(SmallInteger, nullable=True)
    rating_professionalism = Column(SmallInteger, nullable=True)
    rating_ethics = Column(SmallInteger, nullable=True)
    rating_learning_attitude = Column(SmallInteger, nullable=True)

    comments = Column(Text, nullable=True)

    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    engagement = relationship("Engagement", back_populates="employer_surveys")

    __table_args__ = (
        UniqueConstraint("engagement_id", "survey_year", name="uq_survey_per_year"),
    )


class MagicLink(Base):
    __tablename__ = "magic_links"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    employer_id = Column(UUID(as_uuid=False), ForeignKey("employers.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employer = relationship("Employer", back_populates="magic_links")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    employer_id = Column(UUID(as_uuid=False), ForeignKey("employers.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    employer = relationship("Employer", back_populates="sessions")
