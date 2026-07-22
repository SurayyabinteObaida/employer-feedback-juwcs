from fastapi import Depends, HTTPException, status, Cookie, Header
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import Employer
from app.services.auth_service import get_employer_from_session_token


def get_current_employer(
    session_token: str | None = Cookie(default=None),
    db: DbSession = Depends(get_db),
) -> Employer:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    employer = get_employer_from_session_token(db, session_token)
    if not employer:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid")
    return employer


def require_admin_key(x_admin_key: str | None = Header(default=None)):
    """Guards /admin routes with a static shared secret, since this app
    has no separate admin user system. IC/admin enters the key once in
    the admin UI and it's sent as a header on every admin request.
    Rotate ADMIN_API_KEY if it's ever exposed."""
    if not settings.admin_api_key:
        raise HTTPException(status_code=500, detail="Admin access is not configured on this server")
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
