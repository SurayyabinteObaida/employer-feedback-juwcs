from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from app.core.database import get_db
from app.core.deps import get_current_employer
from app.models.models import Employer, Engagement, OrgProforma, ValidationStatus
from app.schemas.schemas import OrgProformaOut, OrgProformaValidate

router = APIRouter(prefix="/engagements/{engagement_id}/proforma", tags=["proforma"])


def _get_owned_proforma(db: DbSession, engagement_id: str, employer: Employer) -> OrgProforma:
    engagement = (
        db.query(Engagement)
        .filter(Engagement.id == engagement_id, Engagement.employer_id == employer.id)
        .first()
    )
    if not engagement or not engagement.proforma:
        raise HTTPException(status_code=404, detail="Proforma not found")
    return engagement.proforma


@router.get("", response_model=OrgProformaOut)
def get_proforma(
    engagement_id: str,
    employer: Employer = Depends(get_current_employer),
    db: DbSession = Depends(get_db),
):
    return _get_owned_proforma(db, engagement_id, employer)


@router.patch("/validate", response_model=OrgProformaOut)
def validate_proforma(
    engagement_id: str,
    payload: OrgProformaValidate,
    employer: Employer = Depends(get_current_employer),
    db: DbSession = Depends(get_db),
):
    """Employer confirms or edits the student-submitted proforma. If any
    field is changed from what the student submitted, status becomes
    'edited'; otherwise 'validated'."""
    proforma = _get_owned_proforma(db, engagement_id, employer)

    changed = False
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        if getattr(proforma, field) != value:
            setattr(proforma, field, value)
            changed = True

    proforma.validated_by_employer_at = datetime.now(timezone.utc)
    proforma.validation_status = ValidationStatus.edited if changed else ValidationStatus.validated

    db.commit()
    db.refresh(proforma)
    return proforma
