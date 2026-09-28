"""The service's log file: data/owner.log.

On the farm server this runs as a Scheduled Task with no console, so
anything printed goes nowhere. A Harvest Forecast that failed right after
the v1.5.5 update, and cleared itself on the next restart, left no trace
of why - its one line of error had been printed to a window that did not
exist. Everything worth reading after the fact goes through `log` below,
which writes to the file as well as to the console (so running
start_owner_server.bat in a window still shows it live).

uvicorn's own error logger is pointed at the same file, so an unhandled
exception in any request - a plain 500 - lands there with its traceback
too. The access log (one line per request) deliberately does not: it
would bury the errors.

Rotated at 1 MB, three old files kept - a farm server's disk is not
somewhere to let a log grow unwatched.
"""
import logging
import logging.handlers
import os

import config

LOG_PATH = os.path.join(config.DATA_DIR, "owner.log")

log = logging.getLogger("boord_owner")


def setup_logging() -> None:
    """Attach the file handler once. Safe to call again (tests, reloads)."""
    if getattr(log, "_owner_configured", False):
        return
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    log.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(console)
    log.propagate = False
    # uvicorn configures this logger (console only) before importing the
    # app, and leaves existing handlers alone, so adding ours here sticks.
    logging.getLogger("uvicorn.error").addHandler(file_handler)
    log._owner_configured = True
