from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session as DbSession

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Employer
from app.schemas.schemas import RequestLoginLink
from app.services.auth_service import (
    is_work_email, get_or_create_employer, issue_magic_link, verify_magic_link,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/request-link")
def request_login_link(payload: RequestLoginLink, db: DbSession = Depends(get_db)):
    if not is_work_email(payload.work_email):
        raise HTTPException(status_code=400, detail="Please use your work email address")

    employer = db.query(Employer).filter(Employer.work_email == payload.work_email.lower()).first()
    if not employer:
        raise HTTPException(
            status_code=404,
            detail="We couldn't find an account for that email. Contact your internship coordinator.",
        )

    issue_magic_link(db, employer)
    return {"message": "Sign-in link sent. Check your inbox."}


@router.get("/verify")
def verify(token: str, response: Response, db: DbSession = Depends(get_db)):
    session = verify_magic_link(db, token)
    if not session:
        raise HTTPException(status_code=400, detail="This link is invalid or has expired")

    response.set_cookie(
        key="session_token",
        value=session.token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="lax",
        max_age=settings.session_expire_days * 24 * 60 * 60,
    )
    return {"message": "Signed in"}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("session_token")
    return {"message": "Signed out"}
