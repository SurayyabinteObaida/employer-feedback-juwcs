from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession, joinedload

from app.core.database import get_db
from app.core.deps import get_current_employer
from app.models.models import Employer, Engagement
from app.schemas.schemas import EngagementOut

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/engagements", response_model=list[EngagementOut])
def list_engagements(
    employer: Employer = Depends(get_current_employer),
    db: DbSession = Depends(get_db),
):
    engagements = (
        db.query(Engagement)
        .filter(Engagement.employer_id == employer.id)
        .options(joinedload(Engagement.student), joinedload(Engagement.proforma))
        .order_by(Engagement.created_at.desc())
        .all()
    )
    return engagements
