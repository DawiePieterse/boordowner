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

# How long startup waits for Boord's database to become readable before
# giving up. Both apps start from scheduled tasks at boot, and Boord
# creates/migrates boord.db on ITS startup - so on a fresh install or right
# after a Boord update this app can start first and find the file missing
# or locked. Without a wait, that one lost race left the service down until
# the next reboot. 0 = fail immediately (tests).
BOORD_STARTUP_WAIT_SECONDS = int(os.environ.get("BOORD_STARTUP_WAIT_SECONDS", "300"))

FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")

# Ask about this estimate: an AI model that explains the Estimate tab's
# figures in words (see ai.py, routers/ai.py). Off unless a key is set - the
# app works exactly as before without it.
#
# The key lives here on the server, never in a browser: every phone and
# laptop on the tailnet gets the feature without anyone typing a key in, and
# nobody can read it out of a page. What is sent is a summary of the farm's
# figures (block kg, the owner's estimate and notes, pack-out mix) - to
# Google or Groq, or to whatever OWNER_AI_ENDPOINT points at.
#
#   OWNER_AI_PROVIDER  gemini (default) | groq | custom
#   OWNER_AI_API_KEY   the provider's key; custom endpoints may need none
#   OWNER_AI_ENDPOINT  custom only: an OpenAI-compatible chat/completions URL
#   OWNER_AI_MODEL     blank = the provider's default, replaced automatically
#                      when the provider retires it
AI_PROVIDER = (os.environ.get("OWNER_AI_PROVIDER") or "gemini").strip().lower()
AI_API_KEY = (os.environ.get("OWNER_AI_API_KEY") or "").strip()
AI_ENDPOINT = (os.environ.get("OWNER_AI_ENDPOINT") or "").strip()
AI_MODEL = (os.environ.get("OWNER_AI_MODEL") or "").strip()
