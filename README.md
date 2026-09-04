# Boord Owner

The owner's view of the farm: the season against its own history, the weather
record, and the risk and harvest-forecast figures built from both. It used to
be a fourth screen inside Boord, reached by a single shared link. It is its
own application now, run **beside Boord on the farm server** — its own
process, its own port, and no login at all: it is published over Tailscale
and reaching it is the whole of the access control (see **Access** below).

## What it is

A single `uvicorn main:app` service that:

- serves a four-tab read-only frontend — **Dashboard** (the Admin
  Dashboard's figures minus wages), **Analysis**, **Weather**, **Risk**;
- reads Boord's live `boord.db` **read-only** for the harvest, lot,
  worker, block and supplier data;
- owns a separate database for the weather record and the seasons before
  Boord.

Whoever opens it sees the summary the Admin Dashboard shows and the three
analytical tabs. Boord's setup and detail screens are not here — those live
in Boord and were never part of this app.

## Layout

```
backend/
  main.py            FastAPI app: startup checks, router registration, static mount
  config.py          paths + env (OWNER_DB_PATH, BOORD_DB_PATH, OWNER_PORT)
  db.py              two engines: owner.db (read-write) + boord.db (read-only, PRAGMA query_only)
  models_owner.py    WeatherHistory, HistoricalHarvest, HistoricalAnnualYield
  models_boord.py    read-only field-subset mirrors of Boord's Block/Worker/Supplier/…
  weather.py         Open-Meteo fetch/parse + WeatherHistory sync (session-split)
  timeutil.py        day_bounds / to_local, copied from Boord
  excel_io.py        parse_uploaded_table, for the historical imports
  migrate.py         shim: run_migrations() -> init_owner_db()
  routers/
    dashboard.py     /api/dashboard/summary  (wage-free)
    boord_data.py    /api/lots/*, /api/suppliers, /api/system-settings  (read boord.db)
    analysis.py      /api/analysis/summary
    weather.py       /api/weather/{current,history,history/backfill}
    risk.py          /api/risk/{summary,forecast}
    historical.py    /api/historical-*/import
    historical_report.py   /api/reports/historical-harvest-data (the XLSX workbook)
  tests/             pytest: data endpoints, Boord isolation, ported risk-function tests
frontend/
  index.html         the four tabs
  owner.js           startup, tab routing, dashboard offline cache
  service-worker.js  offline shell, cache prefix "boord-owner-"
  shared/            vendored from Boord: api.js (no credentials sent),
                     styles.css, tailwind.js, ptr.js, fontawesome, charts + tab modules
scripts/             the four historical-import scripts (need BOORD_DB_PATH set),
                     block_renames.py (workbook block ids -> Boord's register)
                     and check_block_ids.py (catches the next rename)
templates/           the two CSV templates for the historical imports
install.ps1 / install.bat            Windows installer (beside Boord, port 8010)
update_owner_server.bat              signed-tag update + restart
release-key.asc                      the public half of the release signing key
```

## Running it

**Dev:**

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
BOORD_DB_PATH=../../Boord/data/boord.db .venv/bin/python run_preview.py
```

Then open `http://localhost:8010/`. There is no sign-in — it opens straight
onto the Dashboard.

**Tests:**

```bash
cd backend && .venv/bin/python -m pytest
```

**Farm server (Windows):** double-click `install.bat`. It installs Python if
needed, makes the venv, asks where Boord's `boord.db` is, writes
`start_owner_server.bat`, opens firewall port 8010, and registers a
`Boord Owner Server` scheduled task that runs as SYSTEM at boot (same account
as Boord's task, so it can read `boord.db` and its `-wal`/`-shm` sidecars).
It finishes by telling you to write the release key's fingerprint into
`data/release_key.fpr` — which is what makes updates possible at all (see
below).

Note it binds the server to `127.0.0.1` and does **not** open a firewall
port; an upgrade actively deletes the port-8010 rule older versions added.
Publishing the app is `tailscale serve`'s job — see **Access** below.

`update_owner_server.bat` installs the newest **signed** release and
restarts.

### Access

**There is no sign-in.** Every endpoint answers anyone who can reach the
port, so *reaching it* is the entire access control. That is a deliberate
trade for a two-person farm app, and it only holds while the network side
is set up as below.

The server binds `127.0.0.1` (see `install.ps1`'s launcher step), so nothing
reaches it from the LAN. One command publishes it to the tailnet, with a
real Let's Encrypt certificate:

```bat
"C:\Program Files\Tailscale\tailscale.exe" serve --bg --https=8443 http://localhost:8010
```

**8443, not 443, because Boord is on the same machine and takes 443** — see
*Sharing a machine with Boord* below. On a box running only this app, 443 is
fine and drops the `:8443` from the address.

Needs **HTTPS Certificates** enabled for the tailnet (admin console → DNS).
The app then answers at `https://<machine>.<tailnet>.ts.net:8443/` for anyone
on the tailnet — Tailscale terminates TLS and proxies to 8010, and renews the
certificate itself. `API_BASE` is relative, so nothing in the frontend needs
to know.

Three things follow from having no password, and they are the whole security
model:

- **Use `serve`, never `funnel`.** Funnel would publish the farm's complete
  figures on the open internet to anyone who guessed the URL. There is no
  login screen behind it to stop them.
- **Do not widen the bind to `0.0.0.0`.** That re-opens the app to every
  device on the farm's wifi. It is how this used to be deployed, back when a
  password stood in front of it.
- **Tailnet membership is the only revocation.** Removing a departed
  person's device in the Tailscale admin console is the one way to take
  access away; there is no account to disable and no audit trail of who
  looked at what.

Note that enabling certificates puts the machine's name into public
Certificate Transparency logs, which is not reversible.

### Sharing a machine with Boord

`:443` is one slot per machine, and this app is designed to run beside Boord
on the farm server — so the two compete for it. Both repos' instructions used
to tell you to claim 443, and whichever command ran last silently won. The
other app then became unreachable over Tailscale with nothing reporting an
error.

**Boord takes 443; this app takes 8443.** Boord is what the farm uses all
day, and its Field QR scanner only works on an HTTPS origin, so a device
pointed at the bare `https://<machine>.<tailnet>.ts.net/` has to land on
Boord.

```bat
tailscale serve reset
tailscale serve --bg --https=443  http://localhost:8000
tailscale serve --bg --https=8443 http://localhost:8010
```

`tailscale serve status` should then list both.

**The symptom of getting it wrong does not look like a port conflict.** The
address loads and answers `{"detail":"Not Found"}` — that is the *other*
FastAPI app replying that it has no such page. Check `tailscale serve status`
before suspecting anything else. Seen for real on 2026-09-04: `:443` pointed
here, so Boord's `/admin/` and `/field/` both 404ed and Boord was reachable
only on `localhost`.

One more consequence worth knowing: the two apps share an origin's worth of
browser state when they swap ports. Both serve files under `/shared/`, so a
browser that cached this app's `/shared/api.js` will hand it to Boord after
the switch. Clear the site data for the host once, on each browser that used
the old mapping.

### Environment

| Var | Required | Meaning |
| --- | --- | --- |
| `BOORD_DB_PATH` | yes on the farm server | absolute path to Boord's `data/boord.db` (dev defaults to `../Boord/data/boord.db`) |
| `OWNER_PORT` | no | default 8010 |
| `OWNER_DATA_DIR` | no | default `<repo>/data` |

## Updates

`update_owner_server.bat` checks out the newest `v*` tag carrying a GPG
signature from the release key, and refuses to update at all if that
signature is missing, broken, or made by any other key. It does **not** pull
a branch.

The reason is the same one Boord's `update_server.bat` gives: the service
runs as SYSTEM, so a branch pull would make a stolen GitHub token equivalent
to code execution on every farm. Pushing code is not enough to ship it; you
also have to hold the signing key.

It is the **same key as Boord** — `67C64CFD D584 DD14 0E58 AF6E 329C 9B9D D056 2A9D`,
whose public half is `release-key.asc` here. One publisher, one key, so a
farm that already trusts it for Boord does not have to decide twice.

What decides which releases a given server accepts is
`data/release_key.fpr`, holding that fingerprint. It lives outside the
checkout on purpose: a file inside the repo would be rewritten by the very
update it is supposed to be vouching for. `install.ps1` deliberately does
not write it — it prints the command and lets a person run it, because a
fingerprint the installer wrote for you is the repo vouching for itself. If
Boord is on the same PC it offers you Boord's, which a human already put
there.

Cutting a release:

```bash
git tag -s v1.1 -m "Boord Owner v1.1"     # needs the signing key
git push origin v1.1
```

Keep `Boord.VERSION` in `frontend/shared/api.js` equal to the tag without
its `v` — it is what the header shows, and it is how you tell at a glance
whether a device's cached copy is actually the release you think it is.

## Authentication

None. There is no user table, no password, no token, and no session — the
app answers whoever reaches it. See **Access** above for what carries that
weight instead, and why the `127.0.0.1` bind and `tailscale serve` are not
optional.

This replaced per-user accounts (`OwnerUser`, bcrypt, 30-day HS256 JWTs, a
manager role and a Users tab), which in turn replaced the single shared
`?key=` link token (`OwnerViewToken`) the Owner View used inside Boord. Both
are gone. Two people use this app and both reach it over Tailscale, so the
accounts were machinery guarding a door the tailnet already guards.

## Data ownership

**Boord owns `data/boord.db`** — crates, lots, `HarvestRecord`, `Worker`,
`Block`, `Supplier`, `SystemSetting`, payments. Boord is the only writer and
migrates it on every startup. This app opens it read-only
(`PRAGMA query_only=ON`, `NullPool`) and holds every read short.

- **Never across a fetch.** Four endpoints read the farm's GPS from Boord and
  then call Open-Meteo — the weather backfill can spend minutes in there.
  They all go through `weather.farm_coords_and_release()`, which hands the
  connection back before the request starts. `tests/test_weather_and_history.py`
  asserts zero open Boord connections *at the moment each fetch runs*, so the
  rule is enforced rather than merely intended.
- The header's `/api/weather/current` is served from
  `fetch_weather_cached` (~10 min TTL): the strip refreshes on every dashboard
  load and pull-to-refresh, several owners may have it open, and Open-Meteo
  only updates every ~15 minutes anyway.

- **`models_boord.py` is a partial mirror.** Its header lists every column
  this app reads. Boord can rename or drop one in a migration — when that
  happens, `main._assert_boord_schema()` makes the service **refuse to
  boot** with a named-column error rather than 500 mid-harvest.

  **Verified against Boord v3.0–v3.1.** Boord v2.x is not supported: v3.0
  (`477ab72`, “Boord belongs to a pack house”) renamed
  `systemsetting.farm_name`/`farm_location` to
  `packhouse_name`/`packhouse_location`, made the season a recurring
  month+day anchor (`season_start_month`/`season_start_day`), and gave a
  block a `supplier_id`. This app reads all of those.
  `tests/test_boord_schema.py` re-checks the column list against `../Boord`
  whenever that checkout is present, so the pin is enforced rather than
  merely written down.
- `PRAGMA query_only` is used rather than `?mode=ro` because a true
  read-only handle can't open the `-wal`/`-shm` sidecars if Boord ever runs
  the DB in WAL mode. Confirm `journal_mode` on the real server if in doubt.

**This app owns `data/owner.db`** — `WeatherHistory`, `HistoricalHarvest`,
`HistoricalAnnualYield`, all three copied verbatim from Boord (git
`2226750`) so the import scripts stay valid. Schema init is `create_all` on
an explicit three-table list plus an additive column top-up
(`db._ensure_owner_columns`) — no
Alembic; the schema is small and single-writer. Import the pre-Boord
history with `scripts/import_historical_*.py` (the weather ones need
`BOORD_DB_PATH` set — they read the farm GPS from Boord); `docs/HISTORICAL_DATA.md`
covers the workbooks.

## What Boord no longer has

All of it moved here; Boord's migrations `748269cfa3ea` and `9e39262b1e30`
drop the four tables. Boord kept only two live Open-Meteo calls (the header
readout and the per-crate dispatch stamp) and never touched `WeatherHistory`.

| Was, in `../Boord` | Now here |
| --- | --- |
| `routers/analysis.py` | `backend/routers/analysis.py` |
| `routers/risk.py` | `backend/routers/risk.py` |
| `routers/historical.py` | `backend/routers/historical.py` |
| the history half of `weather.py` + `/api/weather/history` | `backend/weather.py` + `backend/routers/weather.py` |
| the Historical Harvest Data XLSX report | `backend/routers/historical_report.py` |
| the four import scripts / two CSV templates | `scripts/` / `templates/` |
| `OwnerViewToken` + the `?key=` link | dropped; access is the tailnet (see **Access**) |

## Tests

```bash
cd backend && .venv/bin/python -m pytest        # 74 tests, well under a second
```

| File | Covers |
| --- | --- |
| `test_data_endpoints.py` | every route answering without credentials, the wage-free dashboard shape, the own-farm supplier filter, sorting and derived per-block figures, the season anchor, own-farm block scoping, the XLSX workbook |
| `test_boord_schema.py` | the mirror and the boot check, against a real `../Boord` checkout — skipped when there isn't one |
| `test_boord_isolation.py` | writes refused (ORM *and* raw SQL), connections released, schema-drift detection, owner.db holding only its own three tables, the additive-column upgrade path |
| `test_weather_and_history.py` | no Boord connection open during a fetch, graceful degradation when Open-Meteo is down, the historical CSV imports |
| `test_risk_functions.py` | the Risk/Forecast maths, ported from Boord's `scripts/selftest.py` |

The rest of that selftest — Boord's Alembic migration chain and its backup
snapshots — was left behind
because this app doesn't have that machinery. It is recoverable in full:

```bash
git -C ../Boord show 2226750:scripts/selftest.py > selftest_recovered.py
```

## The Historical Harvest Data workbook

`GET /api/reports/historical-harvest-data`, behind the **Historical Harvest
Data** button on the Analysis tab. Every harvest figure the farm has on
file, 1987 through the current season, in one workbook — per-year block ×
date pivots for the daily-tracked seasons, Annual Totals for the
season-only ones before them, and two cross-era summary sheets that name
each season's grain so the two are never silently mixed.

It is the only endpoint that reads both databases at once: `Block` /
`HarvestRecord` / `SystemSetting` from Boord, `HistoricalHarvest` /
`HistoricalAnnualYield` from `owner.db`. The current season is rebuilt from
live harvest records on every download, so it never needs a re-import.
