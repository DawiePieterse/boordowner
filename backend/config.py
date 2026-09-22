"""Paths and environment for the Boord Owner service.

This app runs beside Boord on the farm server as its own process. It owns
one database (data/owner.db - weather history, pre-Boord harvest history)
and reads Boord's database (data/boord.db) read-only. Every path
here can be overridden by an environment variable so the Windows installer
can point the service at wherever Boord actually lives.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

# This app's own data directory - owner.db and nothing else that matters.
# Gitignored, never backed up off the server.
DATA_DIR = os.environ.get("OWNER_DATA_DIR", os.path.join(REPO_ROOT, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

OWNER_DB_PATH = os.environ.get("OWNER_DB_PATH", os.path.join(DATA_DIR, "owner.db"))

# Boord's live SQLite file, opened read-only. The default assumes Boord is
# checked out beside this repo (../Boord); on the farm server the installer
# sets BOORD_DB_PATH to the real absolute path (e.g. C:\Boord\data\boord.db).
BOORD_DB_PATH = os.path.abspath(os.environ.get(
    "BOORD_DB_PATH", os.path.join(REPO_ROOT, "..", "Boord", "data", "boord.db")))

# Clear of Boord's ports (8000 prod, 8811 its run_preview).
#
# The installer binds this to 127.0.0.1 only, and `tailscale serve` is what
# publishes it to the tailnet. The app has no sign-in of its own, so that
# binding is not a detail - it is the access control. See README.md.
OWNER_PORT = int(os.environ.get("OWNER_PORT", "8010"))

# The farm's own on-site iWeathar station (e.g. "2235" for iWeathar Station
# Bekfontein, https://iweathar.co.za/display?s_id=2235), if it has one.
# Unset by default - a real station's readings beat Open-Meteo's grid-cell
# estimate for this exact spot, but only for the farm that actually owns it.
# Defaulting this to any one farm's station id would be the same silent
# wrong-place bug farm_coords() in weather.py refuses to allow for GPS: every
# other install of this app would quietly get Bekfontein's weather instead of
# its own. See weather.fetch_iweathar_current().
IWEATHAR_STATION_ID = os.environ.get("IWEATHAR_STATION_ID") or None

FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
