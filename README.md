# Boord Owner

The owner's view of the farm: the season against its own history, the weather
record, and the risk and harvest-forecast figures built from both. It used to
be a fourth screen inside Boord, reached by a single shared link. It is its
own application now, run **beside Boord on the farm server** — its own
process, its own port, and no login at all: it is published over Tailscale
and reaching it is the whole of the access control (see **Access** below).

## What it is

A single `uvicorn main:app` service that:

- serves a five-tab frontend — **Dashboard** (the Admin Dashboard's
  figures minus wages), **Analysis**, **Estimate**, **Weather**, **Risk**;
- reads Boord's live `boord.db` **read-only** for the harvest, lot,
  worker, block and supplier data;
- owns a separate database for the weather record, the seasons before
  Boord, and the owner's crop estimates.

Whoever opens it sees the summary the Admin Dashboard shows and the
analytical tabs. **Estimate** is the one screen that writes: the owner's
per-block crop estimate for a season (kg per tree against each block's
history, times its trees), saved as versions in `owner.db`, and tracked
against the picking once the season is running. Around it: each version's
pack-out mix turned into kg and cartons per channel (estimation only — no
pallets, transport or markets), the Risk tab's weather-driven Harvest
Forecast as a cross-check (snapshotted with each saved version), and the
past seasons whose weather so far came closest, and — when an AI key is
set on the server — **Ask AI about this estimate** (see below). With no sign-in, anyone
who can reach the app can edit it — see **Access** below. Boord's setup
and detail screens are not here — those live in Boord and were never part
of this app.

## Layout

```
backend/
  main.py            FastAPI app: startup checks, router registration, static mount
  config.py          paths + env (OWNER_DB_PATH, BOORD_DB_PATH, OWNER_PORT, OWNER_AI_*,
                     OWNER_NOTES_URL)
  ai.py              the AI provider: Anthropic Claude (SDK, JSON schemas, caching,
                     tool use) or Gemini / Groq / OpenAI-compatible (streamed,
                     retired-model fallback); daily cap (no database access)
  ai_tools.py        what Ask may look up for itself: a block's record, a season's
                     weather, the picking pace, and Boord Notes (the bridge)
  db.py              two engines: owner.db (read-write) + boord.db (read-only, PRAGMA query_only)
  models_owner.py    WeatherHistory, HistoricalHarvest, HistoricalAnnualYield,
                     YieldEstimate + YieldEstimateBlock + YieldEstimatePack
                     (the Estimate tab's versions, block lines and pack-out mix),
                     ForecastSnapshot (daily Expected kg for the Risk trend)
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
    estimate.py      /api/estimate (GET view, POST/PUT/DELETE versions, /{id}/export XLSX)
    analogs.py       /api/estimate/analogs (similar past seasons; reads owner.db
                     weather only, never fetches)
    ai.py            /api/ai/{status,ask,review,compare,brief,notes}: Ask about this
                     estimate, Check before I save, What changed?, the daily brief,
                     Ask the farm notes - each builds its figures, releases Boord,
                     then calls the model (Ask and Compare stream NDJSON); `tab: "weather"` on
                     ask is Ask AI about this weather (ai_weather.py)
    ai_weather.py    the Weather tab's summary: ticked years and measurements, the
                     record year by year, the last 7 days, the farm's forecast
    historical.py    /api/historical-*/import
    historical_report.py   /api/reports/historical-harvest-data (the XLSX workbook)
  tests/             pytest: data endpoints, Boord isolation, ported risk-function tests,
                     the AI provider plumbing and endpoints (no network)
frontend/
  index.html         the five tabs
  owner.js           startup, tab routing, dashboard offline cache
  service-worker.js  offline shell, cache prefix "boord-owner-"
  shared/            vendored from Boord: api.js (no credentials sent),
                     styles.css, ptr.js, fontawesome, charts + tab modules;
                     tailwind.css is BUILT (see "Frontend styles" below)
  tailwind.config.js the build config for shared/tailwind.css
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

**Frontend styles:** `frontend/shared/tailwind.css` is precompiled from the
classes the HTML/JS actually use (Tailwind used to ship as a 400 KB runtime
that recompiled them in the browser on every open). After adding a Tailwind
class the app hasn't used before, rebuild it and commit the result:

```bash
cd frontend && npx tailwindcss@3 -c tailwind.config.js -i shared/tailwind.src.css -o shared/tailwind.css --minify
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

That farm server now runs up to four of these apps, and each has its own
port:

| App | Tailscale port | Proxies to |
| --- | --- | --- |
| Boord | 443 | `localhost:8000` |
| Boord Owner (this app) | 8443 | `localhost:8010` |
| Boord Notes | 9443 | `localhost:8020` |
| Kudde | 8030 | `localhost:8030` |

```bat
tailscale serve reset
tailscale serve --bg --https=443  http://localhost:8000
tailscale serve --bg --https=8443 http://localhost:8010
tailscale serve --bg --https=9443 http://localhost:8020
tailscale serve --bg --https=8030 http://localhost:8030
```

`tailscale serve status` should then list every app this PC actually runs.

> **`tailscale serve reset` clears every mapping on the machine, including
> ones this file does not mention.** Run the whole block and drop only the
> lines for apps this PC genuinely does not have. This block listed two apps
> until 2026-09-09, so a machine set up from the older version of it — or
> from Boord's or Boord Notes' equally short copy — has had Notes and Kudde
> silently unpublished. The symptom is the `{"detail":"Not Found"}` one
> below, which is why it is worth checking rather than assuming.

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
| `FORECAST_SNAPSHOT_HOURS` | no | how often the background job records the forecast's Expected kg (default 6; 0 = off) |
| `OWNER_DATA_DIR` | no | default `<repo>/data` |
| `IWEATHAR_STATION_ID` | no | this farm's on-site iWeathar station id (e.g. `2235` for iWeathar Station Bekfontein), if it has one - unset means Open-Meteo only, the old behaviour |
| `OWNER_AI_PROVIDER` | no | `anthropic`, `gemini` (default), `groq` or `custom` - see **Ask AI about this estimate** |
| `OWNER_AI_API_KEY` | no | the provider's API key; unset = the AI features are off (the card says how to set it up). For `anthropic`, `ANTHROPIC_API_KEY` or `OWNER_AI_KEY_FILE` also count |
| `OWNER_AI_KEY_FILE` | anthropic only | a file holding the key alone on one line, default `data/anthropic_key.txt`; point it at Boord Notes' `data\anthropic_key.txt` to share that key |
| `OWNER_AI_ENDPOINT` | custom only | an OpenAI-compatible `.../chat/completions` URL |
| `OWNER_AI_MODEL` | no | blank = the provider's default (`claude-sonnet-5-5` for anthropic; `claude-opus-5-5` is stronger at about twice the price), swapped automatically when a free-tier provider retires it; a model set here is never swapped |
| `OWNER_AI_DAILY_LIMIT` | no | AI calls per day across every feature (default 200); no sign-in, so this caps what a stray device could spend |
| `OWNER_NOTES_URL` | no | Boord Notes' local address, e.g. `http://127.0.0.1:8020`; set = Ask can consult the farm notes - see **Ask the farm notes** |
| `OWNER_NOTES_PUBLIC_URL` | no | where phones open Notes (e.g. `https://<server>.<tailnet>.ts.net:9443`), for the links under a notes answer |
| `OWNER_BRIEF_HOURS` | no | how often the background job checks whether today's season brief is written (default 6; 0 = off) |

### Ask AI about this estimate

A question box on the Estimate tab: "Review this estimate", "Which blocks look
out of line with their history?", "Are we on track?", or anything typed. The
server summarises the tab's own figures (`routers/ai.py`: each block's
estimate beside its history and the similar-seasons figure, the weather-model
cross-check, progress, pack-out, versions, plus a few precomputed highlights),
sends them with the question to an AI model and streams the answer back. The
same design as the Weather Compare app's "Ask about this comparison", cloud
only, with the key on the server instead of in each browser.

- **Off until a key is set.** Either a free key (Gemini: aistudio.google.com/apikey;
  Groq: console.groq.com/keys) or an Anthropic key (console.anthropic.com -
  paid, a few cents a question, and the one that unlocks everything below).
  Re-run the installer and enter it at the Ask step, or add
  `set "OWNER_AI_API_KEY=..."` and `set "OWNER_AI_PROVIDER=anthropic"` (or
  `groq`) to `start_owner_server.bat` and restart the task. The key sits in
  that launcher in plain text, readable by the server's administrators - it is
  not committed. With `anthropic`, the key Boord Notes already has on this
  server can be shared: `set "OWNER_AI_KEY_FILE=C:\boord-notes\data\anthropic_key.txt"`.
- **What leaves the farm.** Each question sends the summary to the provider:
  block kg and kg/tree, the owner's estimate, notes and pack-out mix, farm
  totals. Not worker, supplier or lot data, and never the key to a browser.
  The card says so under the question box. With Claude, a lookup the model
  asks for (below) sends that block's or season's record too, and a notes
  question sends Notes' answer.
- **It only reads.** Answers are never written into an estimate; the owner can
  copy one or add it to the version's notes and save.
- **Unsaved edits count.** The browser sends its working copy and the
  weather-model figures it is showing, so a review covers what is on screen.
- **Guard rails.** The prompt holds the model to the figures sent; an answer
  that echoes JSON is asked again in words; one that names a season or block
  not in the summary gets a "check this against the table" note; an answer
  the provider cut off at its length limit, or declined, says so after
  whatever text arrived rather than passing as whole.
- **The daily cap.** `OWNER_AI_DAILY_LIMIT` calls a day across every feature
  here, in memory (resets at midnight and on restart); the panel shows the
  count. Same design as Boord Notes.
- Boord's database is read and closed before the provider is called
  (`tests/test_ai.py` asserts it), like every other outbound call here. A
  lookup the model asks for opens and closes its own sessions inside the
  tool (`tests/test_ai_claude.py` asserts that too).

**Check before I save** (button next to Save). The same summary, but the
model answers in a fixed shape (`REVIEW_SCHEMA`): for every block a flag
(high / medium / low / ok), the reason in a sentence, and the kg/tree range
the block's own history supports; then the whole-farm points. The server
checks every block id against the estimate before anything is shown, drops
the rest and says so, and the tab puts a badge on each block's row (the
finding is its tooltip) with the findings in a card. Flags only - nothing is
written to a figure. With Claude the shape is enforced by the API; with the
free-tier providers it is JSON mode plus the schema in the prompt, and the
same server-side check.

**What changed?** (next to the Version selector, once a season has two
versions). Sends the two versions with the per-block deltas worked out
(`build_compare_summary`) and asks what moved and why it matters; the answer
lands in the Ask panel and follow-ups carry on from it.

**Today's season brief** (Dashboard). Once a day, the background job in
`main.py` has the model write one short paragraph from the current season's
latest version - picking pace against the estimate, how the weather model's
Expected kg moved over the last days, a block or two worth walking - and
stores it in `owner.db` (`SeasonBrief`), so the Dashboard shows it instantly
and offline. Refresh writes today's again. It is written against an estimate:
with none for the season, nothing is written and nothing is spent.

**With Claude only.** Answers are shaped by JSON schemas where the app needs
them; the system prompt and the figures carry prompt-cache breakpoints, so a
follow-up within five minutes re-reads them at a tenth of the price (the
server log's `[ai] ask: ... cached=` line shows it); and Ask can look things
up for itself (`ai_tools.py`): a block's full season record, a season's
weather (the four model factors and a month-by-month summary), how a
season's picking ran, and the farm notes. The panel says what is being looked
up while it waits. The model is told to compare the summary first and look up
only what the question needs; at most six lookups per answer.

### Ask the farm notes

Boord Notes (the farm's notebook app) runs beside this one. With
`OWNER_NOTES_URL` set to its local address, the Estimate tab's panel gets an
"Ask the farm notes instead" toggle that sends the question to Notes' own
Ask (`/api/ai/ask` on Notes, over localhost) and lists the notes it used
under the answer - and, with Claude, the notes become a lookup Ask can make
on its own ("what do the farm notes say about the blocks that look out of
line?"). The two apps still share no data: this is one app asking the other
a question, and Notes answers from its notes with its own key and its own
daily cap. Notes' setup message ("AI help is not set up on the server yet")
comes through as-is when it has no key.

### Ask AI about this weather

The same question box under the Weather tab's chart (`tab: "weather"` on
`/api/ai/ask`; same key, same provider, nothing more to set up). The summary
(`routers/ai_weather.py`) is for one location - the farm's GPS from Boord's
Settings, which is also what the stored history was fetched for - and holds:
the years and measurements ticked on the tab (per year: days on file, mean,
lowest and highest day, totals for rain and sunshine, 12 monthly figures); every
year on file for those measurements, so a year can be ranked against the record
(the unfinished current year is compared over the same 1 Jan-to-date span);
the last 7 days; the forecast for today and the next 7 days from Open-Meteo,
with frost (night at or below 2 °C), heat (day at or above 35 °C) and rain-day
highlights; and current conditions. With no GPS set there is no forecast. If
the forecast service can't be reached the answer is built from the history
alone. The coordinates are used to fetch the forecast but are not sent to the
AI provider. It only reads; nothing is written.

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
- If `IWEATHAR_STATION_ID` is set, that current-conditions reading is a blend:
  `weather.fetch_iweathar_current()` scrapes the farm's own iWeathar station
  (there is no JSON API — `display?s_id=<id>` is a plain HTML page) and its
  temperature, humidity and rain-gauge reading win over Open-Meteo's
  grid-cell estimate for that farm's exact spot; Open-Meteo still supplies
  the cloud-based condition text, since the station has no sky sensor, and
  is the sole source whenever no station is configured or it's unreachable.
  It never touches `WeatherHistory` — the station's page has no historical
  export, so the Weather tab, Risk indicator and Harvest Forecast stay
  Open-Meteo only.

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
`2226750`) so the import scripts stay valid, and the Estimate tab's own
`YieldEstimate`, `YieldEstimateBlock` and `YieldEstimatePack`, and
`ForecastSnapshot` — one row per day holding that day's Expected kg from the
Risk tab's Harvest Forecast, which feeds the Expected card's last-7-days
trend. A background thread (`FORECAST_SNAPSHOT_HOURS`, default 6, 0 = off)
rebuilds the forecast so days nobody opens the app still get a point; days
the live weather forecast was unavailable are skipped. The trend starts
empty and fills over the first week. Schema init
is `create_all` on an explicit seven-table list (`db._OWNER_TABLES`) plus an
additive column top-up
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
cd backend && .venv/bin/python -m pytest        # 134 tests, a few seconds
```

| File | Covers |
| --- | --- |
| `test_data_endpoints.py` | every route answering without credentials, the wage-free dashboard shape, the own-farm supplier filter, sorting and derived per-block figures, the season anchor, own-farm block scoping, the XLSX workbook |
| `test_boord_schema.py` | the mirror and the boot check, against a real `../Boord` checkout — skipped when there isn't one |
| `test_boord_isolation.py` | writes refused (ORM *and* raw SQL), connections released, schema-drift detection, owner.db holding only its own six tables, the additive-column upgrade path (including an owner.db from before the pack-out table and forecast-snapshot columns) |
| `test_weather_and_history.py` | no Boord connection open during a fetch, graceful degradation when Open-Meteo is down, the historical CSV imports |
| `test_risk_functions.py` | the Risk/Forecast maths, ported from Boord's `scripts/selftest.py` |
| `test_risk_pipeline.py` | the Risk score and Harvest Forecast end to end on seeded seasons, including the fields the Estimate tab snapshots (`built_at`, `no_location`, `resid_sd_kg`, `fitted_kg_range`) |
| `test_estimate.py` | per-block reference figures, version lifecycle and validation, the in-season projection, pack-out arithmetic (against the farm's own "%" sheet) and validation, the weather-model cross-check and snapshots, farm totals, the XLSX export, and that neither viewing nor saving runs the weather model or the season scan |
| `test_analogs.py` | similar past seasons: same-calendar-days comparison, ties, the candidate pool (young orchard, old-orchard seasons, missing weather), block figures, timing descriptors, stale/future/empty weather, scaling of running totals, no network and Boord released, the weather cache vs a new harvest import |

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
