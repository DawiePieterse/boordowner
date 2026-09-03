#!/usr/bin/env python3
"""Say why a sign-in is refused, when the password is right.

The symptom this exists for: the sign-in screen takes a correct password,
then bounces straight back to itself saying "Session ended - sign in again".

That message is the frontend's, and it is misleading here. It means the very
next call after the sign-in - GET /api/owner-auth/me - was answered 401, so
the server rejected a token it had itself issued seconds earlier. Nothing on
the screen can tell you why, and the two causes look identical from there:

  * the account's token_valid_from cutoff is in the FUTURE, so every token
    minted now is already "too old" (see security._user_for_credentials).
    A clock that was wrong when somebody changed a password does this, and
    it does not heal when the clock is fixed - the cutoff stays where it was
    written.
  * the signing key is not the same one that signed the token: the key file
    could not be persisted, so a restart generated a new one mid-session.

Run on the server that has the data:
    backend\\.venv\\Scripts\\python.exe scripts\\diagnose_auth.py

Read-only. It mints a throwaway token for each account and runs it back
through the app's own check - the same function the API uses - rather than
re-implementing the rules here, so it cannot drift away from what the server
actually does. No password is read, changed or printed, and no session is
created: the tokens exist inside this process only.

    --fix-future-cutoffs   the one repair, opt-in: lower a token_valid_from
                           that sits in the future back to now. For when a
                           bad clock has locked everyone out, managers
                           included, and there is no signed-in manager left
                           to reset anything from inside the app. It grants
                           nobody access - the password is still required.
"""
import os
import sys
from datetime import datetime, timezone

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from fastapi.security import HTTPAuthorizationCredentials  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import config  # noqa: E402
from db import owner_engine  # noqa: E402
from models_owner import OwnerUser, _now_epoch  # noqa: E402
from security import _user_for_credentials, create_access_token  # noqa: E402


def _stamp(epoch: float) -> str:
    """A cutoff as something a person can compare to a wall clock."""
    if not epoch:
        return "never set"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _describe_skew(seconds: float) -> str:
    seconds = abs(seconds)
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


def check_signing_key() -> bool:
    """Whether the key that signs tokens will still be there after a restart.

    It is not a fault on its own - a running server signs and verifies with
    whatever it has in memory, so sign-in works fine until the process
    restarts. It matters because an unpersisted key turns every restart into
    a silent mass sign-out, which reads as "it keeps saying my session
    expired" rather than as anything to do with restarting.
    """
    print("Signing key")
    if os.environ.get(config.SECRET_KEY_ENV):
        print(f"  {config.SECRET_KEY_ENV} is set in the environment - stable across restarts.")
        return True
    path = config.SECRET_KEY_FILE
    try:
        with open(path) as f:
            stored = f.read().strip()
    except OSError as e:
        print(f"  NOT PERSISTED: {path}")
        print(f"    cannot be read ({e.strerror}).")
        print("    Every restart signs with a NEW key, so every token issued")
        print("    before it stops working - everyone is signed out, and the")
        print("    app calls that an expired session.")
        return False
    if not stored:
        print(f"  NOT PERSISTED: {path} is empty - same effect as missing.")
        return False
    print(f"  persisted in {path} ({len(stored)} chars) - survives a restart.")
    return True


def check_users(fix_future: bool) -> int:
    """Mint a token per account and run it through the app's own check.

    This is the whole point of the script: it reproduces, offline, exactly
    what happens in the second between a successful sign-in and the bounce
    back to the sign-in screen.
    """
    now = _now_epoch()
    print(f"\nServer clock: {_stamp(now)}  (local: {datetime.now():%Y-%m-%d %H:%M:%S})")
    if abs(datetime.now(timezone.utc).timestamp() - now) > 5:
        print("  NOTE: this machine's clock moved while the script ran.")

    with Session(owner_engine) as session:
        users = session.exec(select(OwnerUser).order_by(OwnerUser.username)).all()
        if not users:
            print("\nNo accounts in owner.db - the app seeds one on first boot.")
            return 0

        print(f"\nAccounts in {config.OWNER_DB_PATH}\n")
        broken = []
        for user in users:
            cutoff = user.token_valid_from or 0
            flags = []
            if user.disabled:
                flags.append("DISABLED")
            if user.must_change_password:
                flags.append("must change password")
            if user.is_manager:
                flags.append("manager")
            print(f"  {user.username}")
            print(f"    {', '.join(flags) if flags else 'active'}")
            print(f"    token cutoff : {_stamp(cutoff)}")

            # The real check, not a copy of it: mint a token the way /login
            # does and hand it to the dependency every request goes through.
            token = create_access_token(user)
            _, err = _user_for_credentials(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
            if err is None:
                print("    a token minted right now : ACCEPTED")
            elif err == "revoked" and user.disabled:
                print("    a token minted right now : refused - the account is disabled.")
                print("      Expected. A manager can re-enable it in the Users tab.")
            elif err == "revoked":
                ahead = cutoff - now
                print("    a token minted right now : REFUSED AS TOO OLD  <-- THIS IS THE BUG")
                print(f"      The cutoff is {_describe_skew(ahead)} in the FUTURE, so every")
                print("      token this server issues is already expired on arrival. Signing")
                print("      in will keep bouncing to 'Session ended - sign in again' until")
                print("      the cutoff is moved back.")
                broken.append(user)
            else:
                print(f"    a token minted right now : REFUSED ({err})  <-- THIS IS THE BUG")
                print("      The server could not verify a token it had just signed. The")
                print("      signing key changed underneath it - see the key section above.")
                broken.append(user)
            print()

        if broken and fix_future:
            print("Repairing future cutoffs (--fix-future-cutoffs):")
            for user in broken:
                if (user.token_valid_from or 0) <= now:
                    continue   # not a clock problem; nothing here to lower
                print(f"  {user.username}: {_stamp(user.token_valid_from)} -> {_stamp(now)}")
                user.token_valid_from = now
                session.add(user)
            session.commit()
            print("\n  Done. Existing sessions were already dead; everyone signs in again.")
            print("  Check the server's clock too, or the next password change repeats this.")
        elif broken:
            print("To repair, once the clock is right:")
            print("    backend\\.venv\\Scripts\\python.exe scripts\\diagnose_auth.py "
                  "--fix-future-cutoffs")
        return len(broken)


def main():
    fix_future = "--fix-future-cutoffs" in sys.argv[1:]
    print(f"\nowner.db : {config.OWNER_DB_PATH}\n")
    key_ok = check_signing_key()
    broken = check_users(fix_future)

    print()
    if not broken and key_ok:
        print("Sign-in is healthy here. Every account accepts a token issued now,")
        print("and the signing key survives a restart. If a phone still says the")
        print("session ended, it is that device: an app cached before the last")
        print("update, or a token from before a password change. Signing out and")
        print("in again on the device clears both.")
    elif not broken:
        print("Every account accepts a token issued now, so sign-in works until")
        print("this service restarts - at which point the key above changes and")
        print("everyone is signed out again.")
    print()


if __name__ == "__main__":
    main()
