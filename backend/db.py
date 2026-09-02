"""Database access for the Boord Owner service.

Two engines, on purpose:

  owner_engine - data/owner.db, READ-WRITE. This process is its only writer.
                 Holds OwnerUser, WeatherHistory, HistoricalHarvest,
                 HistoricalAnnualYield.

  boord_engine - Boord's data/boord.db, READ-ONLY. Boord is the sole writer
                 and migrates that file on its own startup, so every read
                 here is short-lived (NullPool, request-scoped session) and
                 never held across an await or an outbound HTTP call.

The read-only guarantee is enforced with `PRAGMA query_only=ON` on every
connection rather than sqlite's `?mode=ro`, because a true read-only handle
cannot open the -wal/-shm sidecars if Boord ever runs the DB in WAL mode and
fails with "attempt to write a readonly database". query_only rejects writes
at the SQL layer while letting SQLite journal normally.
"""
import os
import secrets
import threading

from passlib.context import CryptContext
from sqlalchemy import event, inspect, pool, text
from sqlmodel import Session, SQLModel, create_engine, select

import config
# Importing models_boord for its side effect as well as for Block/Supplier:
# it registers every Boord mirror table on the shared SQLModel.metadata,
# which is exactly why init_owner_db() has to pass an explicit table list.
from models_boord import Block, Supplier
from models_owner import (HistoricalAnnualYield, HistoricalHarvest, OwnerUser,
                          WeatherHistory)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --------------------------------------------------------------------------- #
# Owner DB - read/write
# --------------------------------------------------------------------------- #
owner_engine = create_engine(
    f"sqlite:///{config.OWNER_DB_PATH}",
    connect_args={"check_same_thread": False},
)
# Back-compat alias: scripts/import_historical_*.py do `from db import engine`.
engine = owner_engine

# --------------------------------------------------------------------------- #
# Boord DB - read-only
# --------------------------------------------------------------------------- #
boord_engine = create_engine(
    f"sqlite:///{config.BOORD_DB_PATH}",
    connect_args={"check_same_thread": False},
    poolclass=pool.NullPool,  # never hold a Boord connection between requests
)


@event.listens_for(boord_engine, "connect")
def _boord_readonly(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA query_only=ON")   # reject every write at the SQL layer
    cur.execute("PRAGMA busy_timeout=3000")  # wait briefly on Boord's write lock, don't error
    cur.close()


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def get_owner_session():
    with Session(owner_engine) as session:
        yield session


def get_boord_session():
    """Request-scoped and short. Never hold this across an await or an
    outbound HTTP call: a long read open across one of Boord's startup
    migrations or its pre-migration snapshot would see the schema change
    underneath it."""
    with Session(boord_engine) as session:
        yield session


def get_own_supplier_id(session: Session):
    """The Boord supplier row representing the farm's own fruit. Read from a
    boord_session. Copied from Boord's db.get_own_supplier_id."""
    own = session.exec(select(Supplier).where(Supplier.is_own_farm == True)).first()  # noqa: E712
    return own.id if own else None


def own_farm_block_ids(session: Session) -> set:
    """The blocks that are this farm's own. Read from a boord_session.

    One Boord install is a pack house several growers deliver into (Boord
    v3.0, commit 477ab72), so `block.supplier_id` now says whose orchard a
    block is. This app is the OWNER's view of their own farm, and its
    history tables hold only that farm's seasons - so an Analysis figure
    that quietly folded in a neighbour's blocks would be comparing this
    season against a different orchard's past.

    A NULL supplier_id counts as ours. That is Boord's own rule for an
    unallocated block (its Field screen offers a device its supplier's
    blocks plus the unallocated ones), and on a single-grower install -
    which is every install today - every block is NULL.
    """
    own_id = get_own_supplier_id(session)
    return {b.id for b in session.exec(select(Block)).all()
            if b.supplier_id is None or b.supplier_id == own_id}


# --------------------------------------------------------------------------- #
# Owner DB schema init
# --------------------------------------------------------------------------- #
_OWNER_TABLES = [
    OwnerUser.__table__,
    WeatherHistory.__table__,
    HistoricalHarvest.__table__,
    HistoricalAnnualYield.__table__,
]

# Serialises the append in weather.sync_recent_weather(): two tab-opens can
# otherwise both fetch the same missing hours and race the WeatherHistory
# timestamp unique index.
weather_append_lock = threading.Lock()


def _ensure_owner_columns() -> None:
    """Strictly-additive column top-up for the owner tables, so a new field
    on OwnerUser (say) reaches an existing owner.db without a migration
    framework. Never renames, retypes or drops anything - that is all this
    app's small, single-writer schema needs."""
    insp = inspect(owner_engine)
    existing = set(insp.get_table_names())
    for tbl in _OWNER_TABLES:
        if tbl.name not in existing:
            continue
        have = {c["name"] for c in insp.get_columns(tbl.name)}
        with owner_engine.begin() as conn:
            for col in tbl.columns:
                if col.name in have:
                    continue
                ddl = f'"{col.name}" {col.type.compile(owner_engine.dialect)}'
                default = getattr(col.default, "arg", None) if col.default is not None else None
                if not col.nullable and default is not None and not callable(default):
                    literal = f"'{default}'" if isinstance(default, str) else (
                        "1" if default is True else "0" if default is False else str(default))
                    ddl += f" NOT NULL DEFAULT {literal}"
                conn.execute(text(f'ALTER TABLE "{tbl.name}" ADD COLUMN {ddl}'))
                print(f"[owner-db] {tbl.name}: added column {col.name}", flush=True)


def init_owner_db() -> None:
    """Create the four owner tables if missing, then top up columns.

    The explicit `tables=` list is load-bearing: models_boord registers its
    read-only mirror classes on the SAME SQLModel.metadata, so a bare
    create_all(owner_engine) would also create block/lot/etc. in owner.db.
    """
    SQLModel.metadata.create_all(owner_engine, tables=_OWNER_TABLES)
    _ensure_owner_columns()


# --------------------------------------------------------------------------- #
# First-run manager account  (mirrors Boord's db.seed_defaults admin branch)
# --------------------------------------------------------------------------- #
DEFAULT_MANAGER_USERNAME = "admin"

# No I, O, 0 or 1: this password gets read off one screen and typed into
# another by someone who did not choose it.
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_initial_password() -> str:
    """A random password - three groups of four, ~60 bits."""
    groups = ("".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(4)) for _ in range(3))
    return "-".join(groups)


def _write_initial_password(username: str, password: str) -> None:
    rule = "=" * 60
    print(f"\n{rule}\n"
          f" Boord Owner created a manager account for this install:\n"
          f"     username: {username}\n"
          f"     password: {password}\n"
          f" You will be asked to replace this password at first sign-in.\n"
          f"{rule}\n", flush=True)
    try:
        fd = os.open(config.INITIAL_PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(password + "\n")
    except OSError as e:
        print(f"[owner] could not write {config.INITIAL_PASSWORD_FILE} ({e!r}) - the "
              f"password printed above is now the only copy of it", flush=True)


def clear_initial_password_file() -> None:
    try:
        os.remove(config.INITIAL_PASSWORD_FILE)
    except OSError:
        pass


def seed_default_manager() -> None:
    """On an empty OwnerUser table, create one manager with a generated
    password. Does nothing once any user exists."""
    with Session(owner_engine) as session:
        if session.exec(select(OwnerUser)).first():
            return
        initial_password = generate_initial_password()
        session.add(OwnerUser(
            username=DEFAULT_MANAGER_USERNAME,
            password_hash=pwd_context.hash(initial_password),
            is_manager=True,
            must_change_password=True,
        ))
        session.commit()
        _write_initial_password(DEFAULT_MANAGER_USERNAME, initial_password)
