"""Compatibility shim.

The Owner app has no migration framework - its four owned tables are created
and column-topped-up by db.init_owner_db(). This module keeps
`from migrate import run_migrations` working for the import scripts and the
update_owner_server.bat launcher, which predate that decision.
"""
from db import init_owner_db


def run_migrations() -> None:
    init_owner_db()
