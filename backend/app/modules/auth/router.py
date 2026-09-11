"""Auth routes: register / login (JWT) / me (Stream A)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.auth.deps import require_user, scope_campaign
from app.modules.auth.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=120)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AuthOut(BaseModel):
    user: UserOut
    token: TokenOut


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> AuthOut:
    email = body.email.lower().strip()
    if db.query(User).filter(User.email == email).first() is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email already registered")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name or email.split("@")[0])
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(subject=user.id)
    return AuthOut(user=UserOut.model_validate(user), token=TokenOut(access_token=token))


@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> TokenOut:
    email = form.username.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenOut(access_token=create_access_token(subject=user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/campaigns/{campaign_id}")
def scoped_campaign(campaign_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)) -> dict:
    """Ownership-scoped campaign fetch: 404 unless the campaign belongs to the caller."""
    campaign = scope_campaign(db, user, campaign_id)
    return {"id": campaign.id, "name": campaign.name, "status": campaign.status}
