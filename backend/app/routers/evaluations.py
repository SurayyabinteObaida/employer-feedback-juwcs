from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from app.core.database import get_db
from app.core.deps import get_current_employer
from app.models.models import (
    Employer, Engagement, EngagementType,
    InternshipEvaluation, EmployerSurvey,
)
from app.schemas.schemas import InternshipEvaluationIn, EmployerSurveyIn

router = APIRouter(prefix="/engagements/{engagement_id}", tags=["evaluations"])


def _get_owned_engagement(db: DbSession, engagement_id: str, employer: Employer) -> Engagement:
    engagement = (
        db.query(Engagement)
        .filter(Engagement.id == engagement_id, Engagement.employer_id == employer.id)
        .first()
    )
    if not engagement:
        raise HTTPException(status_code=404, detail="Engagement not found")
    return engagement


@router.put("/internship-evaluation")
def submit_internship_evaluation(
    engagement_id: str,
    payload: InternshipEvaluationIn,
    employer: Employer = Depends(get_current_employer),
    db: DbSession = Depends(get_db),
):
    engagement = _get_owned_engagement(db, engagement_id, employer)
    if engagement.type != EngagementType.internship:
        raise HTTPException(status_code=400, detail="This engagement is not an internship")
    if not engagement.proforma or engagement.proforma.validation_status.value == "pending":
        raise HTTPException(status_code=400, detail="Validate the organization proforma before submitting an evaluation")

    evaluation = engagement.internship_evaluation
    if not evaluation:
        evaluation = InternshipEvaluation(engagement_id=engagement.id)
        db.add(evaluation)

    for field, value in payload.model_dump().items():
        setattr(evaluation, field, value)
    evaluation.submitted_at = datetime.now(timezone.utc)

    db.commit()
    return {"message": "Evaluation submitted"}


@router.post("/employer-survey")
def submit_employer_survey(
    engagement_id: str,
    payload: EmployerSurveyIn,
    employer: Employer = Depends(get_current_employer),
    db: DbSession = Depends(get_db),
):
    """Creates a new survey instance for the given year. Admin/IC decides
    when a new year's survey should be opened — this endpoint doesn't
    check for duplicates beyond the DB's unique (engagement, year)
    constraint, which will reject an accidental second submission
    for the same year."""
    engagement = _get_owned_engagement(db, engagement_id, employer)
    if engagement.type != EngagementType.job:
        raise HTTPException(status_code=400, detail="This engagement is not a job/graduate engagement")

    existing = next(
        (s for s in engagement.employer_surveys if s.survey_year == payload.survey_year), None
    )
    if existing:
        raise HTTPException(status_code=400, detail=f"A survey for {payload.survey_year} already exists")

    survey = EmployerSurvey(
        engagement_id=engagement.id,
        submitted_at=datetime.now(timezone.utc),
        **payload.model_dump(),
    )
    db.add(survey)
    db.commit()
    return {"message": "Survey submitted"}
