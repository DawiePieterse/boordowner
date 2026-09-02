"""Test fixtures.

Every environment variable the app reads is set here BEFORE `config` (and
therefore `db`, `security`, `main`) is imported, because those modules read
their configuration at import time.

The fake Boord database is built from models_boord's own (partial) mirror
schema - the suite never needs a real Boord checkout.
"""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

_TMP = tempfile.mkdtemp(prefix="boordowner-tests-")
os.environ["OWNER_DATA_DIR"] = _TMP
os.environ["OWNER_DB_PATH"] = os.path.join(_TMP, "owner.db")
os.environ["BOORD_DB_PATH"] = os.path.join(_TMP, "boord.db")
os.environ["OWNER_SECRET_KEY"] = "test-secret-key-not-for-production"

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import config  # noqa: E402
from models_boord import (Block, HarvestRecord, Lot, LotStatus, Supplier,  # noqa: E402
                          SystemSetting, Worker)


# The season anchor the fixture pins Boord to: 1 August, so the fake database
# exercises a season that is NOT the calendar year. A 1-January anchor would
# make season_year_for() the identity on a calendar year and hide every
# ordering bug the anchor work was for.
SEASON_ANCHOR_MONTH = 8
SEASON_ANCHOR_DAY = 1

# Blocks 7 and 8a are the own farm's; NEIGHBOUR_BLOCK belongs to another
# grower delivering into the same pack house, and must never appear in an
# Analysis figure (see db.own_farm_block_ids).
NEIGHBOUR_BLOCK = "90"
NEIGHBOUR_SUPPLIER_ID = 2

# The two dates that separate an anchored season from a calendar year. Both
# are the own farm's, both on block 8a.
PRE_ANCHOR_TS = datetime(2026, 7, 15, 8, 0, 0)   # calendar 2026, season 2025
CROSS_YEAR_TS = datetime(2027, 1, 15, 8, 0, 0)   # calendar 2027, season 2026


def _build_fake_boord_db() -> None:
    """A minimal boord.db with the tables and columns this app reads.

    Shaped like a Boord v3.1 pack-house install, not the single-farm v2.14
    this app was lifted out of: packhouse_* naming, an explicit season
    anchor, and a block belonging to somebody else.
    """
    engine = create_engine(f"sqlite:///{config.BOORD_DB_PATH}")
    SQLModel.metadata.create_all(engine, tables=[
        Block.__table__, Worker.__table__, Supplier.__table__,
        SystemSetting.__table__, Lot.__table__, HarvestRecord.__table__,
    ])
    # Inside season 2026, which under the 1-August anchor runs
    # 2026-08-01 to 2027-07-31.
    now = datetime(2026, 10, 1, 8, 0, 0)
    with Session(engine) as s:
        s.add(Supplier(id=1, name="Own Farm", is_own_farm=True, active=True))
        s.add(Supplier(id=NEIGHBOUR_SUPPLIER_ID, name="Neighbour Co",
                       is_own_farm=False, active=True))
        s.add(SystemSetting(id=1, packhouse_name="Test Farm", packhouse_code="PHC1",
                            current_harvest_year=2026,
                            season_start_month=SEASON_ANCHOR_MONTH,
                            season_start_day=SEASON_ANCHOR_DAY,
                            green_to_yellow_minutes=90, yellow_to_red_minutes=150,
                            gps_lat=None, gps_lon=None))
        # supplier_id None and the own-farm id are both "ours" - Boord seeds
        # own-farm rows either way, so both paths need covering.
        s.add(Block(id="7", name="Block 7", variety="Mauritius", trees=2000,
                    hectares=4.0, active=True, supplier_id=None))
        s.add(Block(id="8a", name="Block 8a", variety="McLean", trees=500,
                    hectares=1.0, active=True, supplier_id=1))
        s.add(Block(id=NEIGHBOUR_BLOCK, name="Block 90", variety="Mauritius", trees=1000,
                    hectares=2.0, active=True, supplier_id=NEIGHBOUR_SUPPLIER_ID))
        s.add(Worker(id="001", name="Alice", supplier_id=None, active=True))
        s.add(Worker(id="002", name="Bob", supplier_id=NEIGHBOUR_SUPPLIER_ID, active=True))
        for i in range(5):
            s.add(HarvestRecord(uuid=f"r{i}", timestamp=now + timedelta(hours=i),
                                worker_id="001" if i % 2 == 0 else "002",
                                block_id="7" if i < 3 else "8a", team_id="A",
                                weight_kg=18.0, deduction_kg=1.0, lot_id=1))
        # The neighbour's picking, same hours, same season. The Dashboard
        # shows it (that is what the supplier filter is for); Analysis must
        # not.
        for i in range(3):
            s.add(HarvestRecord(uuid=f"n{i}", timestamp=now + timedelta(hours=i),
                                worker_id="002", block_id=NEIGHBOUR_BLOCK, team_id="B",
                                weight_kg=100.0, deduction_kg=0.0, lot_id=2))
        # Two records that only an anchored season gets right, both on 8a so
        # the per-block assertions on block 7 stay about the five above.
        # PRE_ANCHOR is three weeks BEFORE the anchor, so it belongs to
        # season 2025 despite sharing calendar year 2026 with the rest.
        s.add(HarvestRecord(uuid="pre-anchor", timestamp=PRE_ANCHOR_TS,
                            worker_id="001", block_id="8a", team_id="A",
                            weight_kg=50.0, deduction_kg=0.0, lot_id=1))
        # CROSS_YEAR is the other half of the same trap: calendar year 2027,
        # season 2026. A calendar-year filter drops it; the anchor keeps it.
        s.add(HarvestRecord(uuid="cross-year", timestamp=CROSS_YEAR_TS,
                            worker_id="001", block_id="8a", team_id="A",
                            weight_kg=40.0, deduction_kg=0.0, lot_id=1))
        s.add(Lot(id=1, slip_number="261001-001", timestamp=now, supplier_id=1,
                  total_crates=5, total_kg=85.0, status=LotStatus.created))
        s.add(Lot(id=2, slip_number="261001-002", timestamp=now - timedelta(hours=5),
                  supplier_id=2, total_crates=10, total_kg=190.0, status=LotStatus.in_transit))
        s.add(Lot(id=3, slip_number="261001-003", timestamp=now - timedelta(hours=9),
                  supplier_id=1, total_crates=8, total_kg=150.0, status=LotStatus.received,
                  received_at=now - timedelta(hours=1)))
        s.commit()
    engine.dispose()


_build_fake_boord_db()

import main  # noqa: E402  (imports after env + fake DB are ready)
from db import owner_engine  # noqa: E402


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    # Fresh owner.db per test: several tests change the seeded password (which
    # deletes the initial-password file) or add users, and startup only seeds
    # into an empty table.
    owner_engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(config.OWNER_DB_PATH + suffix)
        except OSError:
            pass
    try:
        os.remove(config.INITIAL_PASSWORD_FILE)
    except OSError:
        pass
    with TestClient(main.app) as c:   # startup: init_owner_db + seed_default_manager
        yield c


@pytest.fixture()
def manager_headers(client):
    """A signed-in manager past the first-login password change."""
    pw = open(config.INITIAL_PASSWORD_FILE).read().strip()
    r = client.post("/api/owner-auth/login", data={"username": "admin", "password": pw})
    tok = r.json()["access_token"]
    r = client.post("/api/owner-auth/change-password",
                    json={"new_password": "manager-pass-1"},
                    headers={"Authorization": f"Bearer {tok}"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def make_user(client, manager_headers):
    """Factory: create a user, complete their password change, return
    (id, headers). is_manager defaults to False."""
    def _make(username, is_manager=False):
        r = client.post("/api/owner-users",
                        json={"username": username, "is_manager": is_manager},
                        headers=manager_headers)
        assert r.status_code == 200, r.text
        uid, otp = r.json()["id"], r.json()["initial_password"]
        tok = client.post("/api/owner-auth/login",
                          data={"username": username, "password": otp}).json()["access_token"]
        r = client.post("/api/owner-auth/change-password",
                        json={"new_password": f"{username}-pass-1"},
                        headers={"Authorization": f"Bearer {tok}"})
        return uid, {"Authorization": f"Bearer {r.json()['access_token']}"}
    return _make
