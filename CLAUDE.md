# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Boord Owner is the owner's view of the farm: the season against its own history, the weather record, risk and harvest-forecast figures, and the owner's per-block crop estimate. It runs beside Boord on the farm PC, on port **8010**, published by `tailscale serve --https=8443`. `README.md` is the developer and setup reference, and `docs/` holds the owner's guides (`TRAINING_OWNER.md`, the weather and risk guide, the historical-data guide and their PDFs).

It has two databases:
- **Boord's `boord.db`, read-only**, for harvest, lots, workers, blocks and suppliers;
- **its own `data/owner.db`** for the weather history, the seasons before Boord, the crop estimates and forecast snapshots.

## The Boord suite

This app is one of seven that run together on the farm PC (`bekfontein-server`), each its own repo, process, port and database. They are reached only over Tailscale, and each binds `127.0.0.1` and is published with `tailscale serve`. The same section is in every app's `CLAUDE.md`. When a fact here changes, change it in all seven.

| App | Repo (`DawiePieterse/…`) | What it does | Port | Tailnet address | Mac folder | Farm PC folder |
|---|---|---|---|---|---|---|
| Boord | `Boord` | Harvest capture and picker pay per kg: field tablets, pack-house receiving, admin office. The hub. | 8000 | `https://bekfontein-server.taile32cb0.ts.net/` | `~/Documents/Projects/Boord` | `C:\Boord` |
| **Boord Owner** (this app) | `boordowner` | The owner's view: season vs history, weather, risk, crop estimate, optional AI. | 8010 | `https://bekfontein-server.taile32cb0.ts.net:8443/` | `~/Documents/Projects/BoordOwner` | `C:\BoordOwner` |
| Boord Notes | `boord-notes` | Private farm notebook for Andre and his son, offline-first, optional AI. | 8020 | `https://bekfontein-server.taile32cb0.ts.net:9443/` | `~/Documents/Projects/BoordNotes` | `C:\bekfontein-farm-notebook` |
| Kudde | `Kudde` | Cattle records: animals and their events. | 8030 | `https://bekfontein-server.taile32cb0.ts.net:8030/` | `~/Documents/Projects/Kudde` | `C:\Kudde` |
| Boord Labour | `Boord-labour` | Packhouse time clock: badge scans at the door, hourly pay with overtime. | 8040 | `https://bekfontein-server.taile32cb0.ts.net:8040/` | `~/Documents/Projects/Boord-labour` | `C:\Boord-labour` |
| Boord Monitor | `Boord-monitor` | Watches the PC and the other apps: status page, SLOs, incidents, Web Push alerts, a Restart button; runs the watchdog and heartbeat. | 8050 | `https://bekfontein-server.taile32cb0.ts.net:8050/` | `~/Documents/Projects/Boord-monitor` | `C:\boord-monitor` |
| Boord Packhouse | `boord-packhouse` | After receiving to the truck: sulfur, acid, lines, pre-pack, cold store and PPECB, dispatch. | 8060 | `https://bekfontein-server.taile32cb0.ts.net:8060/` | `~/Documents/Projects/Boord-packhouse` | `C:\Boord-packhouse` |

**How they depend on each other:**
- **Boord is the hub.** Its `data/boord.db` is opened read-only by Owner (harvest, lots, blocks, suppliers), Labour (the worker register, and Boord's `data/photos` for faces), Monitor (tablet station, last sync and outbox) and Packhouse (received lots, blocks, suppliers, workers).
  - Boord is that database's only writer and migrates it at startup.
  - Owner, Labour and Packhouse refuse to boot if a column they read disappears. A Boord migration that drops or renames a column needs those apps updated in step.
- **Owner also reads Packhouse.** For the Dashboard's Delivered vs Picked card, Owner opens Packhouse's `data/packhouse.db` read-only, the same way Boord's own admin dashboard does (delivery notes are Packhouse's data, not Boord's). Optional: missing or unreadable just drops that one figure.
- **Labour's badges are Boord's.** The badge QR holds exactly `worker.id` (`Boord/frontend/admin/print-badge.html`), and Labour and Packhouse vendor Boord's `frontend/shared/`.
- **Boord reads Owner and Packhouse.** The Admin Planner (Boord 3.50) opens Owner's `data/owner.db` read-only for the season's latest estimate, and Packhouse's `data/packhouse.db` for receivers, packs and delivery notes (Boord's Reports and Dashboard already read the latter). Optional: a missing database drops those figures with a message, never the screen. Owner's `yieldestimate`/`yieldestimateblock` and Packhouse's `receiver`, `packaging` and `deliverynote` columns it reads must stay.
- **Owner asks Notes.** The "Ask the farm notes" bridge calls Notes' `/api/ai/ask` at `OWNER_NOTES_URL` (`http://127.0.0.1:8020`) and can share Notes' `data/anthropic_key.txt`.
- **Monitor keeps the others running.** Since Monitor 2.0 its `watchdog.ps1` (restarts an app that stops answering) and `heartbeat.ps1` (pings healthchecks.io) cover all seven, as the scheduled tasks "Boord Watchdog" and "Boord Heartbeat". Until then they lived in Boord's repo; Boord's copies are now forwarders to Monitor's.
  - **The app table is Monitor's `apps.json`, the one copy.** Monitor's `backend/apps.py`, `watchdog.ps1` and `heartbeat.ps1` all read it. Adding or moving an app is one line there plus the port table above; the `tailscale serve` ports are also in Boord's MANUAL.md.
  - The watchdog stands down while any app's updater (its `updater` in `apps.json`) or an installer runs, and its `-Restart "<Name>"` is what Monitor's Restart button runs. It exits 2 when it stands down.
- **Monitor watches everything.** It shows each app's up/down state and `/api/version`, Tailscale and the PC, keeps uptime history, SLOs and incidents, and pushes an alert after three failed checks in a row.
  - Every app's `/api/version` keeps the same field names (`tag`, `state`, `frontend_version`), because Monitor and the heartbeat read them all.
  - An off-site copy of the monitor on the owner's Mac alerts when the whole farm PC is unreachable.
- **Kudde and Notes stand alone.** No other app reads their data. They are only watched and restarted.

**What they share by convention, not code:**
- One release key: fingerprint `67C64CFDD584DD140E58AF6E329C9B9DD0562A9D` in each app's `data\release_key.fpr`.
- Releases are signed tags, made from an up-to-date `main` on the Mac and installed by each app's updater on the farm PC.
- `stop_server.ps1` stops each server, never a bare `schtasks /end`. Labour's copy is the reference.
- Each installer adds a Git `safe.directory` exception so SYSTEM's Git can read the repo folder.
- Python 3.9-compatible code, plain JS with no CDNs (the phones are tailnet-only), and `VERSION` plus the service-worker `CACHE` bumped with every frontend change.
- Fixes to the copied deployment files (`install.ps1`, the updaters, `stop_server.ps1`, `release.sh`) flow between the repos.

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
- **`/api/version`** reports `tag`, `state` and `frontend_version`, the same field names as Boord's. Boord Monitor and its `heartbeat.ps1` read it.

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
   scripts/ship.sh vX.Y        # signs, verifies and pushes the tag
   ```
   `ship.sh` pushes `main`, then runs `release.sh`, which refuses a dirty tree, an existing tag, or a `VERSION` that disagrees with the tag, and verifies the signature before anything is pushed. Both ask before anything irreversible, so run them in a terminal.
   Never move or reuse a released tag. Fix a wrong one with the next version.
4. **On the farm PC**, in `C:\BoordOwner`, double-click **`update_owner_server.bat`**. It verifies the signature, stops the server with `stop_server.ps1`, installs dependencies, copies and migrates the database, and starts it again. If it stops with an error, nothing has changed. Read the message; never check the tag out by hand.
5. **If the release changed `install.ps1`** (its notes say "run install.bat once more"), double-click `install.bat` as well. The updater does not rewrite the launcher or the task.
6. **Check from your Mac:** `curl -s https://bekfontein-server.taile32cb0.ts.net:8443/api/version` should show `"tag":"vX.Y","state":"release"`.
7. **Close and reopen the app on the owner's phone so it loads the new version.**

## Conventions

Comments are long on purpose. They explain *why*, usually a farm constraint: one owner on a phone, a PC nobody administers, Boord's database that must never be disturbed. Keep that style.
