"""Auth dependencies: current-user resolution + campaign ownership scoping (Stream A).

Session model is a stateless JWT (see app.core.security): reconnects just
re-send the Bearer token, and in-progress scene state lives server-side under
the campaign row, so nothing is lost on reconnect.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.modules.auth.models import User
from app.modules.campaign.models import Campaign

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def require_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode the Bearer JWT, load the User, 401 on any failure."""
    subject: str | None = decode_access_token(token)
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = db.query(User).filter(User.id == subject).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown user",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def scope_campaign(db: Session, user: User, campaign_id: str):
    """Return the campaign iff it belongs to `user`, else raise 404.

    404 (not 403) on cross-account access so no endpoint can leak whether
    another account's campaign id exists.
    """
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == campaign_id, Campaign.owner_user_id == user.id)
        .first()
    )
    if campaign is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="campaign not found")
    return campaign
