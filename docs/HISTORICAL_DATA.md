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

---

### When Boord's block ids change

The source workbooks name their columns with the block ids that were
current when they were prepared. Boord's register moves — blocks get split,
merged, relettered — and a historical row keyed to an id that no longer
exists is the **quiet** failure of this whole import. Nothing errors.
Season totals still add up, because the Analysis tab sums by season as well
as by block. The block simply shows no history, and a block with no history
looks exactly like a block that was not bearing yet.

It has happened once already. Three pairs lost their `a`/`b` lettering,
with the **second** of each pair taking the bare number:

| In the workbooks | In Boord today | |
| --- | --- | --- |
| `10a` Mauritius, 512 trees, 2.5 ha | `10b` Block 10b-Mau | |
| `10b` Early Delight, 512 trees, 2.4 ha | `10` Block 10-ED | |
| `17a` Mauritius, 584 trees, 2.8 ha | `17b` Block 17b-Mau | |
| `17b` Early Delight, 584 trees, 2.8 ha | `17` Block 17-ED | |
| `19a` Mauritius, 358 trees, 1.7 ha | `19b` Block 19b-Mau | |
| `19b` Early Delight, 358 trees, 1.7 ha | `19` Block 19-ED | |

Note this is the **opposite of what the ids suggest**: the bare `10` is the
old `10b`, not the old `10a`. The mapping was established from variety,
tree count and hectares, which agree on all six — hectares settle it, since
new `10` is 2.4 ha and old `10a` was 2.5. Mapping by id instead would put
each Mauritius block's six seasons onto an Early Delight block of a
different size; season totals would still be right, and only kg/ha, kg/tree
and the variety charts would be wrong, against a past nobody can check by
eye.

`8a`/`8b` were not affected.

The map lives in `scripts/block_renames.py` and both import scripts apply
it. **Add to it rather than editing the workbooks** — those are the farm's
own records and should keep saying what they said when they were written.

After any import, and after any Boord release that touches Master Data:

```bat
backend\.venv\Scripts\python.exe scripts\check_block_ids.py
```

It lists any imported id Boord no longer has, and exits non-zero — so a
future rename is reported out loud instead of silently emptying a block.

---

### Market statement workbooks (the Markets tab)

The office keeps one "LIETJIES Markte <year> SEISOEN" workbook per season:
a sheet per receiver with a row per delivery, and from 2024 a
"<year> Totaal" sheet. `scripts/import_market_prices.py` reads one into the
Markets tab: the season totals table and the daily price chart. Like the
harvest workbooks, they are the farm's own records and are not in the
repository; put them in `data\imports\`.

Import each season from **its own** workbook, in any order. From Command
Prompt in `C:\BoordOwner`:
```bat
backend\.venv\Scripts\python.exe scripts\import_market_prices.py "data\imports\LIETJIES Markte 2022 SEISOEN.xlsx"
backend\.venv\Scripts\python.exe scripts\import_market_prices.py "data\imports\LIETJIES Markte 2023 SEISOEN.xlsx"
backend\.venv\Scripts\python.exe scripts\import_market_prices.py "data\imports\LIETJIES Markte 2024 SEISOEN.xlsx"
backend\.venv\Scripts\python.exe scripts\import_market_prices.py "data\imports\2025 LIETJIES Markte 2025 SEISOEN Finaal (003).xlsx"
```
Each run replaces that one season and touches no other, so it is safe to
re-run. No server restart is needed; reopen the Markets tab.

What it does with each season's workbook:

- **The season** is the workbook's newest Totaal sheet, or for 2022 and
  2023, which have none, the year in the file name. A file with neither
  is refused rather than filed under a guess.
- **The season totals** come from the season's own Totaal sheet. 2022 and
  2023 have none, so theirs are added up from the receiver sheets.
- **Last season's Totaal sheet, copied into the new workbook, is not
  imported.** The 2024 workbook's "2023 Totaal" has a row of formula junk
  (Dapper's cartons at R0.03 to R2.33 a carton). It also files 2023's
  4.5kg air sales under "2kg MIX", because the copy kept 2024's column
  headings. 2023's own workbook adds up to the same figures as that sheet,
  less the junk.
- **Copies of a sheet are left out** (`SKIP_SHEETS`). In 2022 these are
  "AgriLink", "AgriLink (3)" and "AgriLink (5)", and in 2023 "Agrilink LU
  23". 2022's "AgriLink" puts the air and sea sheets together, with the sea
  loads at the R87.63 advance payment rather than the R336 to R360 the sea
  sheet has. The sea sheet is used, as for every later season.
- **A second line on the same delivery note** (the office leaves its date
  blank) counts as part of that delivery, and **the totals row** at the
  bottom of a sheet does not. Every sheet in the 2022 to 2024 workbooks adds
  up to its own totals row.
- **A date that is a typo** keeps that delivery off the daily chart, but
  its sales still count in the season totals. The import prints each one.
  2023 has two: Dapper note 922 ("26/09/202") and BoereTrust note 965
  ("20/14//2023").
- **Names are lined up with Packhouse's** where the workbooks spelled them
  differently (`PACK_RENAMES`, `RECEIVER_RENAMES`). 2022's "4.55kg XL" is
  the 4.5kg Rhino carton. AgriLink's sea sheets are "Agrilink USA Sea" in
  every season, and Boeretrust and BoereTrust are "Boere Mark Trust".
  **Add to these maps rather than editing the workbooks.**
