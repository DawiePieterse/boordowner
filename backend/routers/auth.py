"""Sign-in and first-login password change for Owner-app users.

Mirrors ../Boord/backend/routers/auth.py, retargeted at OwnerUser and with a
token-invalidation step on password change (see security.token_valid_from).
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, SQLModel, select

from db import DEFAULT_MANAGER_USERNAME, clear_initial_password_file, get_owner_session
from models_owner import OwnerUser, _now_epoch
from security import (create_access_token, get_user_pending_password_change,
                      hash_password, verify_password)

router = APIRouter(prefix="/api/owner-auth", tags=["owner-auth"])

MIN_PASSWORD_LENGTH = 8

# A real bcrypt hash of a value nothing can log in with, so a login attempt
# for a username that does not exist still costs one full verify. Computed
# once at import rather than per request. See login() below.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class ChangePasswordIn(SQLModel):
    """Request BODY, not a query parameter - a new password in the URL lands
    in the web server's access log, browser history and every proxy in
    between (see the same note in Boord's auth.py)."""
    new_password: str


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_owner_session)):
    user = session.exec(select(OwnerUser).where(OwnerUser.username == form.username)).first()
    # Hash something either way. Short-circuiting on a missing user would skip
    # bcrypt entirely and answer in a millisecond instead of ~250ms, which is
    # a clean read on which usernames exist - and the whole list of people
    # with access to a farm's figures is a short one to guess at.
    password_ok = verify_password(form.password, user.password_hash if user else _DUMMY_HASH)
    if not user or user.disabled or not password_ok:
        # One message for wrong-password and for disabled - don't tell an
        # attacker which usernames are real or which are switched off.
        raise HTTPException(401, "Invalid username or password")
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "must_change_password": user.must_change_password,
        "is_manager": user.is_manager,
    }


@router.post("/change-password")
def change_password(body: ChangePasswordIn, session: Session = Depends(get_owner_session),
                    current: OwnerUser = Depends(get_user_pending_password_change)):
    """The one endpoint a user who still owes a password change can reach.

    Bumping token_valid_from invalidates every token for this account -
    including the one that just made this call - so a fresh token is issued
    in the response for the client to switch to.
    """
    new_password = body.new_password
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if verify_password(new_password, current.password_hash):
        raise HTTPException(400, "That is already this account's password - choose a different one")
    current.password_hash = hash_password(new_password)
    current.must_change_password = False
    current.token_valid_from = _now_epoch()
    session.add(current)
    session.commit()
    session.refresh(current)
    if current.username == DEFAULT_MANAGER_USERNAME:
        clear_initial_password_file()
    return {"access_token": create_access_token(current), "token_type": "bearer"}


@router.get("/me")
def me(current: OwnerUser = Depends(get_user_pending_password_change)):
    """Pending-friendly on purpose: the frontend calls this on load to
    decide between the app, the password-setup screen and the login screen,
    and wants a 200 with must_change_password rather than a 403 to parse."""
    return {
        "username": current.username,
        "is_manager": current.is_manager,
        "must_change_password": current.must_change_password,
    }
