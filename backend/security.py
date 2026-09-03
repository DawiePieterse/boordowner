"""Authentication for the Boord Owner service.

Structure copied from Boord (../Boord/backend/security.py): stateless
HS256 JWT bearer tokens, bcrypt password hashes, a signing key that survives
restarts. What Boord does not have and this adds:

  * a real user table with more than one row (models_owner.OwnerUser),
  * an is_manager flag and a get_current_manager dependency,
  * token_valid_from vs the JWT's iat, so a password change / manager reset /
    disable revokes an account's live sessions on their next request.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlmodel import Session, select

import config
from db import owner_engine, pwd_context
from models_owner import OwnerUser


def _load_or_create_secret() -> str:
    """The JWT signing key, stable across restarts. OWNER_SECRET_KEY wins if
    set; otherwise generated once into data/.owner_secret_key (0600) so a
    scheduled-task restart doesn't silently sign everyone out."""
    env = os.environ.get(config.SECRET_KEY_ENV)
    if env:
        return env
    try:
        with open(config.SECRET_KEY_FILE) as f:
            existing = f.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    key = os.urandom(32).hex()
    try:
        fd = os.open(config.SECRET_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
    except OSError as e:
        print(f"[security] could not persist {config.SECRET_KEY_FILE} ({e!r}) - sessions "
              f"will not survive a restart", flush=True)
    return key


SECRET_KEY = _load_or_create_secret()
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

bearer_scheme = HTTPBearer(auto_error=False)

# The frontend matches these 403/401 bodies to route between the "set your
# password" screen and the sign-in screen.
PASSWORD_CHANGE_REQUIRED = "Set a new password before continuing"
SESSION_REVOKED = "Session ended - sign in again"


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def create_access_token(user: OwnerUser) -> str:
    now = datetime.now(timezone.utc)
    # iat carries sub-second precision so it can be compared against
    # OwnerUser.token_valid_from without a same-second tie (see _now_epoch).
    return jwt.encode(
        {"sub": user.username, "iat": now.timestamp(),
         "exp": now + timedelta(days=TOKEN_EXPIRE_DAYS)},
        SECRET_KEY, algorithm=ALGORITHM,
    )


# Usernames already warned about, so a locked-out device retrying cannot fill
# the log with the same line.
_CUTOFF_WARNED = set()


def _warn_if_cutoff_is_in_the_future(username: str, cutoff: float) -> None:
    """Say so on the console when a rejection is really a clock fault.

    A cutoff later than now cannot revoke anything - every token this server
    is able to issue is stamped earlier than it, so the account is locked out
    of its own sign-in and each attempt looks, from the app, exactly like an
    expired session. Only a clock that moved backwards after a password
    change puts a cutoff there, and it does not heal when the clock is put
    right. Without a word here the server looks healthy while refusing
    everybody: it answers 200 to the sign-in and 401 to the call after it.
    """
    if cutoff <= datetime.now(timezone.utc).timestamp() or username in _CUTOFF_WARNED:
        return
    _CUTOFF_WARNED.add(username)
    when = datetime.fromtimestamp(cutoff, timezone.utc)
    print(f"[security] {username}: token_valid_from is in the FUTURE "
          f"({when:%Y-%m-%d %H:%M:%S} UTC) - every token issued now is rejected "
          f"on arrival and this account cannot sign in. Check this server's "
          f"clock, then run scripts/diagnose_auth.py --fix-future-cutoffs.",
          flush=True)


def _user_for_credentials(credentials: Optional[HTTPAuthorizationCredentials]):
    """(user, error) for a bearer token. error is one of None / "missing" /
    "bad" / "revoked". Loads the OwnerUser row every call, like Boord's
    _admin_for_credentials, so disable / rename take effect immediately."""
    if credentials is None:
        return None, "missing"
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None, "bad"
    with Session(owner_engine) as session:
        user = session.exec(
            select(OwnerUser).where(OwnerUser.username == payload.get("sub"))
        ).first()
    if not user:
        return None, "bad"
    if user.disabled:
        return None, "revoked"
    # `or 0` guards the one case db._ensure_owner_columns() cannot fill in:
    # a column added to a table that already has rows lands as NULL, and
    # comparing a float to None raises rather than answering. Treat an
    # unknown cutoff as "never revoked" - the row predates the mechanism.
    cutoff = user.token_valid_from or 0
    if float(payload.get("iat", 0)) < cutoff:
        _warn_if_cutoff_is_in_the_future(user.username, cutoff)
        return None, "revoked"
    return user, None


def get_user_pending_password_change(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> OwnerUser:
    """The signed-in user, even if they still owe a first-login password
    change. ONLY /api/owner-auth/change-password and /me depend on this."""
    user, err = _user_for_credentials(credentials)
    if err == "revoked":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_REVOKED)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> OwnerUser:
    user = get_user_pending_password_change(credentials)
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, PASSWORD_CHANGE_REQUIRED)
    return user


def get_current_manager(user: OwnerUser = Depends(get_current_user)) -> OwnerUser:
    if not user.is_manager:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Manager access required")
    return user
