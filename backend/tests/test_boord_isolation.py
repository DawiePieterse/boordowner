"""The rules that keep this app a safe guest in Boord's database.

Boord owns boord.db, is its only writer, and migrates it on its own startup.
Three things have to hold, and each has failed silently in the past in one
codebase or another:

  * we never write to it,
  * we never hold a read open across a slow outbound call,
  * we notice at boot when its schema has moved past what we read.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

import config
import main
import weather as weather_module
from db import boord_engine, get_boord_session, init_owner_db, owner_engine
from models_boord import Supplier, SystemSetting


# --------------------------------------------------------------------------- #
# Never write
# --------------------------------------------------------------------------- #
def test_orm_write_through_boord_session_is_refused():
    gen = get_boord_session()
    session = next(gen)
    try:
        row = session.get(SystemSetting, 1)
        row.packhouse_name = "MUTATED"
        session.add(row)
        with pytest.raises(Exception):
            session.commit()
        session.rollback()
    finally:
        gen.close()
    # ...and the value on disk is untouched.
    with Session(boord_engine) as check:
        assert check.get(SystemSetting, 1).packhouse_name == "Test Farm"


@pytest.mark.parametrize("stmt", [
    "UPDATE systemsetting SET packhouse_name = 'x'",
    "INSERT INTO block (id, name, variety, trees, hectares, active) VALUES ('99','x','y',1,1.0,1)",
    "DELETE FROM supplier",
    "CREATE TABLE sneaky (id INTEGER)",
    "DROP TABLE block",
])
def test_raw_sql_writes_are_refused(stmt):
    """query_only rejects at the SQL layer, so this covers the statements an
    ORM would never generate as well as the ones it would."""
    with boord_engine.connect() as conn:
        with pytest.raises(Exception) as exc:
            conn.execute(text(stmt))
        assert "readonly" in str(exc.value).lower() or "query_only" in str(exc.value).lower()


def test_boord_connection_carries_the_readonly_pragmas():
    with boord_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA query_only").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 3000


# --------------------------------------------------------------------------- #
# Never hold a read across a slow call
# --------------------------------------------------------------------------- #
class _OpenConnectionCounter:
    """Counts live DBAPI connections on an engine. boord_engine uses NullPool,
    so every checkout is a real connect and every release a real close -
    which makes this an exact measure of "is Boord's file open right now"."""

    def __init__(self, engine):
        self.engine = engine
        self.live = 0

    def _on_connect(self, *_a):
        self.live += 1

    def _on_close(self, *_a):
        self.live -= 1

    def __enter__(self):
        from sqlalchemy import event
        event.listen(self.engine, "connect", self._on_connect)
        event.listen(self.engine, "close", self._on_close)
        return self

    def __exit__(self, *_exc):
        from sqlalchemy import event
        event.remove(self.engine, "connect", self._on_connect)
        event.remove(self.engine, "close", self._on_close)
        return False


def test_farm_coords_and_release_closes_the_session():
    """Every Open-Meteo call site reads the farm location through this, so
    that Boord's file is not held open for the seconds-to-minutes the fetch
    takes."""
    with _OpenConnectionCounter(boord_engine) as counter:
        gen = get_boord_session()
        session = next(gen)
        try:
            coords = weather_module.farm_coords_and_release(session)
            assert coords is None  # the fixture farm has no GPS set
            # Nothing is open across what would be the fetch...
            assert counter.live == 0
            assert not session.in_transaction()
            # ...and the session still works afterwards, which is what lets
            # the forecast path release and then carry on.
            assert session.exec(select(Supplier)).first() is not None
            assert counter.live == 1
        finally:
            gen.close()
        assert counter.live == 0


def test_plain_farm_coords_does_hold_the_connection():
    """The counterpart of the test above - proof that it is the release, not
    something incidental, that closes the file. If this ever starts passing
    with 0, farm_coords_and_release has stopped being load-bearing."""
    with _OpenConnectionCounter(boord_engine) as counter:
        gen = get_boord_session()
        session = next(gen)
        try:
            weather_module.farm_coords(session)
            assert counter.live == 1
        finally:
            gen.close()


def test_no_boord_connection_is_left_open_after_a_request(client, manager_headers):
    with _OpenConnectionCounter(boord_engine) as counter:
        for path in ("/api/dashboard/summary?period_start=2026-01-01&period_end=2026-12-31",
                     "/api/lots/pending", "/api/lots/in-transit", "/api/lots/received",
                     "/api/suppliers", "/api/system-settings", "/api/analysis/summary",
                     "/api/weather/current"):
            assert client.get(path, headers=manager_headers).status_code == 200
            assert counter.live == 0, f"{path} left Boord's database open"


# --------------------------------------------------------------------------- #
# Notice when Boord's schema moves
# --------------------------------------------------------------------------- #
def test_assert_boord_schema_passes_on_the_real_fixture():
    with boord_engine.connect() as conn:
        main._assert_boord_schema(conn)  # must not raise


def test_assert_boord_schema_names_the_missing_column(tmp_path):
    """A Boord migration that renames a column this app reads must stop the
    service at boot with a message naming it - not 500 mid-harvest."""
    drifted = tmp_path / "drifted.db"
    con = sqlite3.connect(drifted)
    # Every table present and at the current Boord shape, EXCEPT that `block`
    # has lost `hectares` - so the failure this asserts can only be that one,
    # not some other column drifting at the same time.
    con.execute("CREATE TABLE block (id TEXT, name TEXT, variety TEXT, trees INT, "
                "active INT, supplier_id INT)")
    con.execute("CREATE TABLE worker (id TEXT, name TEXT, supplier_id INT, active INT)")
    con.execute("CREATE TABLE supplier (id INT, name TEXT, is_own_farm INT, active INT)")
    con.execute("CREATE TABLE systemsetting (id INT, packhouse_name TEXT, packhouse_location TEXT, "
                "packhouse_code TEXT, green_to_yellow_minutes INT, yellow_to_red_minutes INT, "
                "current_harvest_year INT, season_start_month INT, season_start_day INT, "
                "gps_lat REAL, gps_lon REAL)")
    con.execute("CREATE TABLE lot (id INT, slip_number TEXT, timestamp TEXT, device_id TEXT, "
                "team_id TEXT, supplier_id INT, driver TEXT, total_crates INT, total_kg REAL, "
                "status TEXT, notes TEXT, received_at TEXT, weather_temp REAL, "
                "weather_humidity REAL, weather_condition TEXT, split_from_slip_number TEXT)")
    con.execute("CREATE TABLE harvestrecord (uuid TEXT, timestamp TEXT, worker_id TEXT, "
                "block_id TEXT, weight_kg REAL, deduction_kg REAL, team_id TEXT, lot_id INT)")
    con.commit()
    con.close()

    engine = create_engine(f"sqlite:///{drifted}")
    with engine.connect() as conn:
        with pytest.raises(RuntimeError) as exc:
            main._assert_boord_schema(conn)
    message = str(exc.value)
    assert "block" in message and "hectares" in message
    engine.dispose()


def test_assert_boord_schema_accepts_empty_tables(tmp_path):
    """Columns are checked at statement-prepare time, so a farm that has not
    picked anything yet must still boot."""
    empty = tmp_path / "empty.db"
    src = sqlite3.connect(config.BOORD_DB_PATH)
    dst = sqlite3.connect(empty)
    src.backup(dst)
    for table in ("harvestrecord", "lot", "block", "worker", "supplier", "systemsetting"):
        dst.execute(f"DELETE FROM {table}")
    dst.commit()
    dst.close()
    src.close()

    engine = create_engine(f"sqlite:///{empty}")
    with engine.connect() as conn:
        main._assert_boord_schema(conn)
    engine.dispose()


# --------------------------------------------------------------------------- #
# Keep Boord's tables out of our own database
# --------------------------------------------------------------------------- #
def test_owner_db_holds_only_owner_tables(client):
    assert set(inspect(owner_engine).get_table_names()) == {
        "owneruser", "weatherhistory", "historicalharvest", "historicalannualyield"}


def test_init_owner_db_is_idempotent(client):
    before = set(inspect(owner_engine).get_table_names())
    init_owner_db()
    init_owner_db()
    assert set(inspect(owner_engine).get_table_names()) == before


def test_ensure_owner_columns_adds_a_missing_column(client):
    """The stand-in for a migration framework: a field added to an owner model
    reaches a database that predates it."""
    with owner_engine.begin() as conn:
        conn.execute(text('ALTER TABLE owneruser DROP COLUMN token_valid_from'))
    assert "token_valid_from" not in {c["name"] for c in inspect(owner_engine).get_columns("owneruser")}
    init_owner_db()
    assert "token_valid_from" in {c["name"] for c in inspect(owner_engine).get_columns("owneruser")}


def test_signing_in_still_works_after_a_column_is_added(client):
    """A column added to a table that already has rows lands as NULL. For
    token_valid_from that means comparing a float against None on every
    authenticated request - a 500 on the whole app, from an upgrade."""
    import config
    pw = open(config.INITIAL_PASSWORD_FILE).read().strip()
    with owner_engine.begin() as conn:
        conn.execute(text('ALTER TABLE owneruser DROP COLUMN token_valid_from'))
    init_owner_db()
    with owner_engine.begin() as conn:
        assert conn.execute(text("SELECT token_valid_from FROM owneruser")).scalar() is None

    r = client.post("/api/owner-auth/login", data={"username": "admin", "password": pw})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/owner-auth/me", headers=h).status_code == 200
