from datetime import datetime
from pydantic import BaseModel, EmailStr, ConfigDict


class RequestLoginLink(BaseModel):
    work_email: EmailStr


class StudentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    full_name: str
    enrollment_number: str
    degree_program: str | None
    batch: str | None


class OrgProformaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_name: str
    role_designation: str | None
    department_served: str | None
    start_date: datetime | None
    end_date: datetime | None
    supervisor_name: str
    supervisor_designation: str | None
    contact_email: str
    contact_phone: str | None
    linkedin_url: str | None
    validation_status: str
    validated_by_employer_at: datetime | None


class OrgProformaValidate(BaseModel):
    organization_name: str | None = None
    role_designation: str | None = None
    department_served: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    supervisor_name: str | None = None
    supervisor_designation: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    linkedin_url: str | None = None


class EngagementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    status: str
    student: StudentOut
    proforma: OrgProformaOut | None


class InternshipEvaluationIn(BaseModel):
    rating_core_knowledge: int
    rating_problem_solving: int
    rating_dev_contribution: int
    rating_tool_usage: int
    rating_teamwork: int
    rating_communication: int
    rating_societal_awareness: int
    rating_ethics: int
    rating_learning_attitude: int
    attendance_bracket: str
    task_completion: str
    overall_rating: str
    recommend: str
    comments: str | None = None


class EmployerSurveyIn(BaseModel):
    survey_year: str
    current_job_role: str | None = None
    employment_department: str | None = None
    employment_duration: str | None = None
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
    comments: str | None = None
