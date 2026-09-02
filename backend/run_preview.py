"""Dev entrypoint. Runs on OWNER_PORT (8010 by default), clear of Boord's
8000 (prod) and 8811 (its own run_preview).

Set BOORD_DB_PATH if Boord isn't checked out at ../Boord:
    BOORD_DB_PATH=/path/to/boord/data/boord.db python run_preview.py
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_site = os.path.join(_here, ".venv", "lib", "python3.9", "site-packages")
if os.path.isdir(_site) and _site not in sys.path:
    sys.path.insert(0, _site)

import uvicorn  # noqa: E402

import config  # noqa: E402

uvicorn.run("main:app", host="127.0.0.1", port=config.OWNER_PORT,
            loop="asyncio", http="h11", app_dir=_here)
