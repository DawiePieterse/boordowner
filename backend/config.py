"""Paths and environment for the Boord Owner service.

This app runs beside Boord on the farm server as its own process. It owns
one database (data/owner.db - users, weather history, pre-Boord harvest
history) and reads Boord's database (data/boord.db) read-only. Every path
here can be overridden by an environment variable so the Windows installer
can point the service at wherever Boord actually lives.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))

# This app's own data directory - owner.db, the JWT signing key, the
# generated first-run password. Gitignored, never backed up off the server.
DATA_DIR = os.environ.get("OWNER_DATA_DIR", os.path.join(REPO_ROOT, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

OWNER_DB_PATH = os.environ.get("OWNER_DB_PATH", os.path.join(DATA_DIR, "owner.db"))

# Boord's live SQLite file, opened read-only. The default assumes Boord is
# checked out beside this repo (../Boord); on the farm server the installer
# sets BOORD_DB_PATH to the real absolute path (e.g. C:\Boord\data\boord.db).
BOORD_DB_PATH = os.path.abspath(os.environ.get(
    "BOORD_DB_PATH", os.path.join(REPO_ROOT, "..", "Boord", "data", "boord.db")))

# Clear of Boord's ports (8000 prod, 8811 its run_preview).
OWNER_PORT = int(os.environ.get("OWNER_PORT", "8010"))

# JWT signing key. OWNER_SECRET_KEY wins if set; otherwise security.py
# generates one once into this file and reuses it across restarts.
SECRET_KEY_ENV = "OWNER_SECRET_KEY"
SECRET_KEY_FILE = os.path.join(DATA_DIR, ".owner_secret_key")

# Where the password generated for the first manager account is left for
# whoever is standing at the machine. Deleted the moment that password is
# changed (routers/auth.change_password).
INITIAL_PASSWORD_FILE = os.path.join(DATA_DIR, "initial_owner_password.txt")

FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")
