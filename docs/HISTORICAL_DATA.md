# Historical harvest data - importing it, and what the numbers mean

Both sections below were in Boord's `MANUAL.md` while the historical
tables and the Historical Harvest Data report lived there. They describe
the import scripts in `owner-app/scripts/` and the one real caveat in the
figures, so they belong with whatever rebuilds that report.

Paths are as they were on the farm server (`C:\Boord`), and will need
adjusting for wherever this app ends up.

---

### Re-importing historical data on a server

> **The source workbooks are not shipped with the app.** They hold one
> farm's own harvest records, so they live in `data\imports\` - which is
> gitignored and per-farm - rather than in the repository. A new install
> has no workbooks and the two harvest import scripts simply skip, which is
> correct: a farm should never be importing another farm's history. To load
> your own, put the workbook in `data\imports\` (or pass its path as the
> first argument to the script) and re-run.


The historical import is a one-off script, not something an update
carries over - the source workbook and the script that reads it are both
in the repo, but running the script is a separate manual step against
*that server's own database*. This means a fresh install, or a server
whose database was reset, needs the import run on it directly - a pull
alone leaves the Historical Harvest Data report with nothing before the
current season, until this is done.

From Command Prompt in `C:\Boord`, after confirming the update
(`update_server.bat` - see
[Getting the code onto the server](#getting-the-code-onto-the-server-github-recommended-or-usbzip))
has already brought in the latest code:
```bat
backend\.venv\Scripts\python.exe scripts\import_historical_harvest.py
```
Safe to re-run any time (e.g. after regenerating the source workbook) -
it replaces the whole historical table each time rather than appending.
No server restart needed; the next report picks it up.

There's a second, separate import for the even-older 1987-2019 seasons
(annual totals only, no daily breakdown - these feed only the Historical
Harvest Data report's Annual Totals sheet).
2012-2019 has a per-block breakdown; 1987-2009 only has a whole-farm
total per year, since those records predate today's block register and
use an incompatible numbering scheme. `update_server.bat` runs this
automatically on every update (see "Pulling future updates" above), so
it's rarely needed by hand - but for a fresh install, or to run it in
isolation:
```bat
backend\.venv\Scripts\python.exe scripts\import_historical_annual_yield.py
```
Safe to re-run any time; replaces its table wholesale.

---

### A note on the historical numbers

A handful of today's blocks (**8a/8b**, **10a/10b**, **17a/17b**, **19a/19b**)
didn't exist as separate blocks before the app - the original spreadsheets
recorded one combined daily total for each pair. Those combined totals have
been split between the two sub-blocks in proportion to their hectares (e.g.
a day's picking on the old "block 8" is split roughly 58%/42% between 8a and
8b, matching their relative size). This is a reasonable **estimate**, not
what was actually picked from each sub-block on that day - every figure
built from an estimated split carries a small info icon next to its block
name in the Per-Block Yield table so it's never mistaken for an exact
historical record.
