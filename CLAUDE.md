# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Boord Owner is the owner's view of the farm: the season against its own history, the weather record, risk and harvest-forecast figures, and the owner's per-block crop estimate. It runs beside Boord on the farm PC, on port **8010**, published by `tailscale serve --https=8443`. `README.md` is the developer and setup reference, and `docs/` holds the owner's guides (`TRAINING_OWNER.md`, the weather and risk guide, the historical-data guide and their PDFs).

It has two databases:
- **Boord's `boord.db`, read-only**, for harvest, lots, workers, blocks and suppliers;
- **its own `data/owner.db`** for the weather history, the seasons before Boord, the crop estimates and forecast snapshots.

## Commands

All backend commands run from `backend/`.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q                                   # all tests, no network
.venv/bin/python -m pytest tests/test_estimate.py -q            # one file
BOORD_DB_PATH=../../Boord/data/boord.db .venv/bin/python run_preview.py   # dev server, http://localhost:8010/
cd ../frontend && npx tailwindcss@3 -c tailwind.config.js -i shared/tailwind.src.css -o shared/tailwind.css --minify   # after adding a new Tailwind class
```

Keep code Python 3.9-compatible, the farm's minimum: no `X | None`, no `match`.

There is no CI in this repo. Run the tests yourself before pushing.

## Architecture

- **A safe guest in Boord's database** (`db.py`, `tests/test_boord_isolation.py`). Boord is `boord.db`'s only writer and migrates it on its own startup. Three rules hold:
  - this app never writes to it (`PRAGMA query_only`);
  - it never holds a read open across a slow outbound call, such as weather or AI;
  - it notices at boot when Boord's schema has moved past what it reads.
- **`main._BOORD_SCHEMA_CHECK`** lists every Boord column this app reads, and the app refuses to boot if one is gone. `tests/test_boord_schema.py` checks the list against a real Boord schema. `models_boord.py` holds partial read-only mirrors of Boord's tables.
- **No sign-in.** Reaching the port over Tailscale is the whole access control.
  - The server binds 127.0.0.1. Never bind 0.0.0.0.
  - Never `tailscale funnel`.
  - The installer deletes the port-8010 firewall rule older versions opened.
- **Estimate is the one screen that writes.** Each save is a version in `owner.db` (`YieldEstimate` with its block lines and pack-out mix), tracked against the picking as the season runs.
- **AI help is optional** (`ai.py`, `ai_tools.py`, `routers/ai.py`). It is configured by `OWNER_AI_*` in `config.py`.
  - The default provider is Gemini. Anthropic, Groq and any OpenAI-compatible endpoint also work.
  - Calls are capped daily by `OWNER_AI_DAILY_LIMIT`.
  - Each feature builds its figures and releases Boord's database before calling the model.
  - Tests never touch the network.
- **`/api/version`** reports `tag`, `state` and `frontend_version`, the same field names as Boord's. Boord's `heartbeat.ps1` and Boord Monitor read it.

## The farm PC

- **Releases are signed tags; merging to `main` deploys nothing.** `update_owner_server.bat` picks the newest `v*` tag and verifies its signature against `data\release_key.fpr`.
- **The updater's dependency check must name exactly `backend/requirements.txt`, by import name.** `tests/test_updater.py` holds the two lists together. Up to 1.9.0 it named two packages this app never installed, so every update stopped with a healthy server.
- **`stop_server.ps1`, never a bare `schtasks /end`.**
  - Ending the task ends only the launcher. The uvicorn it started keeps port 8010, so the new release can't bind it and the old one keeps answering.
  - Before 1.9.3 that hid v1.9.1 and v1.9.2 behind an older server on the farm PC.
  - The updater now stops the server before copying and migrating `owner.db`, and its "came back up" check asks `/api/version`.
  - `stop_server.ps1` is adapted from Boord Labour's, which is the reference copy.
- **`install.ps1` adds the repo folder to Git's machine-wide `safe.directory`.** The server runs as SYSTEM and the folder belongs to the admin account, so Git refused it as "dubious ownership" and `/api/version` reported `"tag": null`. The same block is in Boord Monitor's and Boord Notes' installers.

## Frontend

- **Plain JS, no build step except Tailwind.** `frontend/shared/tailwind.css` is built from `tailwind.config.js`; rebuild it and commit it after using a new class.
- **No CDNs.** The phones are tailnet-only.
- **`frontend/shared/` is vendored from Boord**, apart from this app's own modules and `VERSION`.
- **Any frontend change needs two bumps:**
  - `VERSION` in `frontend/shared/api.js`;
  - `CACHE` in `frontend/service-worker.js`.

## Installing and deploying

### Where it lives

| | |
|---|---|
| Your Mac (the only clone to release from) | `~/Documents/Projects/BoordOwner` |
| Farm PC folder | `C:\BoordOwner` |
| Farm PC scheduled task | "Boord Owner Server", runs as SYSTEM at boot |
| Port on the farm PC | `127.0.0.1:8010` |
| Address on the tailnet | `https://bekfontein-server.taile32cb0.ts.net:8443/` |

### First install on the farm PC (once)

Only for a new PC or a reinstall. An app that is already running is updated, not reinstalled.

1. Open a Command Prompt and clone the repo into the folder above:
   ```bat
   git clone https://github.com/DawiePieterse/boordowner.git C:\BoordOwner
   ```
2. In `C:\BoordOwner`, double-click **`install.bat`** and accept the administrator prompt. It asks for the full path to Boord's `data\boord.db`, an optional iWeathar station id, an optional AI key and provider, and Boord Notes' address (for the Ask the farm notes bridge).
   It installs Python if needed, builds the app's environment, writes the launcher, registers the scheduled task and starts the server. Re-running it is safe and keeps the data.
3. Tell the updater which release key to trust. In the same folder, type it exactly like this, with the redirect first:
   ```bat
   >data\release_key.fpr echo 67C64CFDD584DD140E58AF6E329C9B9DD0562A9D
   ```
4. Publish it on the tailnet, in a Command Prompt opened as administrator:
   ```bat
   tailscale serve --bg --https=8443 http://localhost:8010
   ```
5. If the installer did not already add it, let SYSTEM's Git read the folder, as administrator (forward slashes):
   ```bat
   git config --system --add safe.directory C:/BoordOwner
   ```
6. Check it from your Mac: `curl -s https://bekfontein-server.taile32cb0.ts.net:8443/api/version` should show the release tag.

### Deploying a change (every release)

Merging to `main` changes nothing on the farm. A release is a signed tag, and the farm PC installs it when its updater runs.

1. **Merge the PR on GitHub.** A frontend change must already bump `VERSION` in `frontend/shared/api.js` and the service worker's `CACHE`.
2. **On your Mac, bring the clone up to date.** This step is what stops a tag landing on an old commit:
   ```sh
   cd ~/Documents/Projects/BoordOwner && git checkout main && git pull --ff-only origin main
   git log -1 --format='%h %s'                  # the merge you expect
   grep -m1 'VERSION:' frontend/shared/api.js   # the version you are releasing
   ```
3. **Sign and push the tag** (`vX.Y` must equal `VERSION`):
   ```sh
   git tag -s vX.Y -m "vX.Y" && git verify-tag vX.Y && git push origin vX.Y
   ```
   This repo has no `release.sh`, so nothing else checks `VERSION` against the tag. Check it in step 2.
   Never move or reuse a released tag. Fix a wrong one with the next version.
4. **On the farm PC**, in `C:\BoordOwner`, double-click **`update_owner_server.bat`**. It verifies the signature, stops the server with `stop_server.ps1`, installs dependencies, copies and migrates the database, and starts it again. If it stops with an error, nothing has changed. Read the message; never check the tag out by hand.
5. **If the release changed `install.ps1`** (its notes say "run install.bat once more"), double-click `install.bat` as well. The updater does not rewrite the launcher or the task.
6. **Check from your Mac:** `curl -s https://bekfontein-server.taile32cb0.ts.net:8443/api/version` should show `"tag":"vX.Y","state":"release"`.
7. **Close and reopen the app on the owner's phone so it loads the new version.**

## Conventions

Comments are long on purpose. They explain *why*, usually a farm constraint: one owner on a phone, a PC nobody administers, Boord's database that must never be disturbed. Keep that style.
