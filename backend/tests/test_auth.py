"""Auth and user-management behaviour."""
from sqlmodel import Session, select

import config
from db import owner_engine
from models_owner import OwnerUser


def _login(client, username, password):
    return client.post("/api/owner-auth/login", data={"username": username, "password": password})


def test_seed_creates_one_pending_manager(client):
    with Session(owner_engine) as s:
        users = s.exec(select(OwnerUser)).all()
    assert len(users) == 1
    u = users[0]
    assert u.username == "admin" and u.is_manager and u.must_change_password
    mode = oct(__import__("os").stat(config.INITIAL_PASSWORD_FILE).st_mode)[-3:]
    assert mode == "600"


def test_init_owner_db_creates_only_owner_tables(client):
    from sqlalchemy import inspect
    names = set(inspect(owner_engine).get_table_names())
    assert names == {"owneruser", "weatherhistory", "historicalharvest", "historicalannualyield"}
    assert "block" not in names and "lot" not in names and "ownerviewtoken" not in names


def test_login_wrong_password(client):
    pw = open(config.INITIAL_PASSWORD_FILE).read().strip()
    assert _login(client, "admin", pw).status_code == 200
    assert _login(client, "admin", "nope").status_code == 401
    assert _login(client, "ghost", "nope").status_code == 401


def test_login_hashes_even_for_an_unknown_username(client, monkeypatch):
    """Otherwise a missing user answers in a millisecond and a real one takes
    ~250ms, which reads off the list of who has access to this farm."""
    import routers.auth as auth_module
    calls = []
    real = auth_module.verify_password
    monkeypatch.setattr(auth_module, "verify_password",
                        lambda plain, hashed: (calls.append(hashed), real(plain, hashed))[1])
    _login(client, "nobody-by-that-name", "whatever")
    assert len(calls) == 1, "no bcrypt work was done for an unknown username"
    assert calls[0] == auth_module._DUMMY_HASH


def test_pending_password_blocks_normal_routes_but_not_change_password(client):
    pw = open(config.INITIAL_PASSWORD_FILE).read().strip()
    tok = _login(client, "admin", pw).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/dashboard/summary?period_start=2026-01-01&period_end=2026-12-31",
                      headers=h).status_code == 403
    assert client.get("/api/owner-auth/me", headers=h).status_code == 200  # pending-friendly
    r = client.post("/api/owner-auth/change-password", json={"new_password": "brandnew12"}, headers=h)
    assert r.status_code == 200 and "access_token" in r.json()


def test_change_password_rules_and_old_token_death(client):
    pw = open(config.INITIAL_PASSWORD_FILE).read().strip()
    tok = _login(client, "admin", pw).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/api/owner-auth/change-password", json={"new_password": "short"}, headers=h).status_code == 400
    assert client.post("/api/owner-auth/change-password", json={"new_password": pw}, headers=h).status_code == 400
    r = client.post("/api/owner-auth/change-password", json={"new_password": "manager-pass-1"}, headers=h)
    assert r.status_code == 200
    # the token used to make the change is now invalid
    assert client.get("/api/owner-auth/me", headers=h).status_code == 401
    new_h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/owner-auth/me", headers=new_h).status_code == 200


def test_non_manager_blocked_from_user_admin(client, make_user):
    _uid, vh = make_user("viewer")
    assert client.get("/api/owner-users", headers=vh).status_code == 403
    assert client.post("/api/owner-users", json={"username": "x"}, headers=vh).status_code == 403
    assert client.post("/api/weather/history/backfill", headers=vh).status_code == 403


def test_create_user_returns_one_time_password_forced_through_setup(client, manager_headers):
    r = client.post("/api/owner-users", json={"username": "newbie", "is_manager": False},
                    headers=manager_headers)
    assert r.status_code == 200
    otp = r.json()["initial_password"]
    tok = client.post("/api/owner-auth/login", data={"username": "newbie", "password": otp}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/suppliers", headers=h).status_code == 403  # must_change_password
    client.post("/api/owner-auth/change-password", json={"new_password": "newbie-pass-1"}, headers=h)
    # duplicate username rejected
    assert client.post("/api/owner-users", json={"username": "newbie"}, headers=manager_headers).status_code == 409


def test_disable_revokes_live_session_and_blocks_login(client, make_user, manager_headers):
    uid, vh = make_user("temp")
    assert client.get("/api/suppliers", headers=vh).status_code == 200
    assert client.patch(f"/api/owner-users/{uid}", json={"disabled": True}, headers=manager_headers).status_code == 200
    assert client.get("/api/suppliers", headers=vh).status_code == 401
    assert client.post("/api/owner-auth/login", data={"username": "temp", "password": "temp-pass-1"}).status_code == 401


def test_reset_password_revokes_target_session(client, make_user, manager_headers):
    uid, vh = make_user("forgetful")
    assert client.get("/api/suppliers", headers=vh).status_code == 200
    r = client.post(f"/api/owner-users/{uid}/reset-password", headers=manager_headers)
    assert r.status_code == 200 and "initial_password" in r.json()
    assert client.get("/api/suppliers", headers=vh).status_code == 401


def test_last_manager_guard(client, manager_headers, make_user):
    users = client.get("/api/owner-users", headers=manager_headers).json()
    admin_id = next(u["id"] for u in users if u["username"] == "admin")
    assert client.patch(f"/api/owner-users/{admin_id}", json={"is_manager": False},
                        headers=manager_headers).status_code == 400
    assert client.patch(f"/api/owner-users/{admin_id}", json={"disabled": True},
                        headers=manager_headers).status_code == 400
    assert client.delete(f"/api/owner-users/{admin_id}", headers=manager_headers).status_code == 400
    # a second manager lifts the guard
    mid, _mh = make_user("mgr2", is_manager=True)
    assert client.patch(f"/api/owner-users/{admin_id}", json={"is_manager": False},
                        headers=manager_headers).status_code == 200
