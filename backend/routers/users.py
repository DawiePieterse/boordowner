"""Manager-only CRUD for Owner-app users.

There is no equivalent in Boord - Boord has exactly one admin and no way to
add another. Here a manager can add colleagues, hand out a one-time password,
disable someone who has left, or reset a forgotten password. The one
invariant: at least one enabled manager must always exist.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, SQLModel, select

from db import generate_initial_password, get_owner_session
from models_owner import OwnerUser, _now_epoch
from security import get_current_manager, hash_password

router = APIRouter(prefix="/api/owner-users", tags=["owner-users"])


class NewUserIn(SQLModel):
    username: str
    is_manager: bool = False


class PatchUserIn(SQLModel):
    is_manager: Optional[bool] = None
    disabled: Optional[bool] = None


def _public(u: OwnerUser) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "is_manager": u.is_manager,
        "disabled": u.disabled,
        "must_change_password": u.must_change_password,
        "created_at": u.created_at.isoformat() if isinstance(u.created_at, datetime) else u.created_at,
    }


def _enabled_manager_count(session: Session) -> int:
    return session.exec(
        select(func.count()).select_from(OwnerUser)
        .where(OwnerUser.is_manager == True, OwnerUser.disabled == False)  # noqa: E712
    ).one()


def _load(session: Session, user_id: int) -> OwnerUser:
    user = session.get(OwnerUser, user_id)
    if not user:
        raise HTTPException(404, "No such user")
    return user


@router.get("")
def list_users(session: Session = Depends(get_owner_session), _mgr=Depends(get_current_manager)):
    users = session.exec(select(OwnerUser).order_by(OwnerUser.username)).all()
    return [_public(u) for u in users]


@router.post("")
def create_user(body: NewUserIn, session: Session = Depends(get_owner_session),
                _mgr=Depends(get_current_manager)):
    username = body.username.strip()
    if not username:
        raise HTTPException(400, "Username is required")
    if session.exec(select(OwnerUser).where(OwnerUser.username == username)).first():
        raise HTTPException(409, f"A user called {username!r} already exists")
    initial_password = generate_initial_password()
    user = OwnerUser(
        username=username,
        password_hash=hash_password(initial_password),
        is_manager=body.is_manager,
        must_change_password=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    # initial_password is returned once and never stored in the clear.
    return {**_public(user), "initial_password": initial_password}


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, session: Session = Depends(get_owner_session),
                   _mgr=Depends(get_current_manager)):
    user = _load(session, user_id)
    initial_password = generate_initial_password()
    user.password_hash = hash_password(initial_password)
    user.must_change_password = True
    user.token_valid_from = _now_epoch()  # drop their existing sessions
    session.add(user)
    session.commit()
    return {"initial_password": initial_password}


@router.patch("/{user_id}")
def patch_user(user_id: int, body: PatchUserIn, session: Session = Depends(get_owner_session),
               mgr: OwnerUser = Depends(get_current_manager)):
    user = _load(session, user_id)
    would_demote = body.is_manager is False and user.is_manager
    would_disable = body.disabled is True and not user.disabled
    if (would_demote or would_disable) and user.is_manager and not user.disabled \
            and _enabled_manager_count(session) <= 1:
        raise HTTPException(
            400, "This is the only enabled manager - promote someone else first")

    if body.is_manager is not None:
        user.is_manager = body.is_manager
    if body.disabled is not None:
        user.disabled = body.disabled
        if body.disabled:
            user.token_valid_from = _now_epoch()  # revoke live sessions now
    session.add(user)
    session.commit()
    session.refresh(user)
    return _public(user)


@router.delete("/{user_id}")
def delete_user(user_id: int, session: Session = Depends(get_owner_session),
                mgr: OwnerUser = Depends(get_current_manager)):
    user = _load(session, user_id)
    if user.id == mgr.id:
        raise HTTPException(400, "You cannot delete your own account")
    if user.is_manager and not user.disabled and _enabled_manager_count(session) <= 1:
        raise HTTPException(400, "This is the only enabled manager - promote someone else first")
    session.delete(user)
    session.commit()
    return {"ok": True}
