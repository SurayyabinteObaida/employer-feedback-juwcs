import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.models.models import Employer, MagicLink, Session as SessionModel
from app.services.email_service import send_magic_link_email

WORK_EMAIL_BLOCKLIST_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "icloud.com", "aol.com", "protonmail.com",
}


def is_work_email(email: str) -> bool:
    """Reject obvious free/personal email domains. Not exhaustive —
    a determined user can still enter a personal email at a custom
    domain, so this is a light guard, not a hard guarantee."""
    domain = email.strip().lower().split("@")[-1]
    return domain not in WORK_EMAIL_BLOCKLIST_DOMAINS


def get_or_create_employer(db: DbSession, work_email: str, name: str | None = None,
                            designation: str | None = None, created_via: str = "manual") -> Employer:
    email = work_email.strip().lower()
    employer = db.query(Employer).filter(Employer.work_email == email).first()
    if employer:
        return employer
    employer = Employer(
        work_email=email, name=name, designation=designation, created_via=created_via
    )
    db.add(employer)
    db.commit()
    db.refresh(employer)
    return employer


def issue_magic_link(db: DbSession, employer: Employer) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.magic_link_expire_minutes)
    link = MagicLink(employer_id=employer.id, token=token, expires_at=expires_at)
    db.add(link)
    db.commit()

    url = f"{settings.frontend_url}/auth/verify?token={token}"
    send_magic_link_email(to_email=employer.work_email, magic_link_url=url)
    return token


def _as_aware(dt: datetime) -> datetime:
    """SQLite (used in tests) strips tzinfo on round-trip even for
    TIMESTAMP WITH TIME ZONE columns; Postgres does not. Normalize to
    UTC-aware before comparing so this works on both."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def verify_magic_link(db: DbSession, token: str) -> SessionModel | None:
    link = db.query(MagicLink).filter(MagicLink.token == token).first()
    if not link:
        return None
    now = datetime.now(timezone.utc)
    if link.used_at is not None or _as_aware(link.expires_at) < now:
        return None

    link.used_at = now
    db.commit()

    session_token = secrets.token_urlsafe(32)
    session_expires = now + timedelta(days=settings.session_expire_days)
    session = SessionModel(employer_id=link.employer_id, token=session_token, expires_at=session_expires)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_employer_from_session_token(db: DbSession, token: str) -> Employer | None:
    session = db.query(SessionModel).filter(SessionModel.token == token).first()
    if not session:
        return None
    if _as_aware(session.expires_at) < datetime.now(timezone.utc):
        return None
    return session.employer
