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

### Market history (the Markets tab)

The Markets tab's seasons before Boord kept market records, 2022 to 2025,
come from **one** clean workbook, "Markte History.xlsx". Seasons after 2025
need no workbook: Owner reads them live from Boord Packhouse's delivery
notes and Boord's market invoices and statements.

"Markte History.xlsx" has three sheets:

- **Sales**: one row per delivery note and pack type, with its season,
  date, receiver, delivery note, pack type, cartons sold and R per carton.
  Gross is worked out, "From" says which old workbook and sheet the row
  came from, and Note is for anything to check. The Markets tab's season
  totals are added up from these rows, weighted by cartons. A row with no
  date counts in its season's total but has no point on the daily chart.
- **Check**: every row with a Note. Correct it on Sales, clear the Note and
  import again.
- **Read me**: the above, for the office.

Like the harvest workbooks it is the farm's own record and is not in the
repository; keep it in `data\imports\`. To load it, or to load it again
after a correction, from Command Prompt in `C:\BoordOwner`:
```bat
backend\.venv\Scripts\python.exe scripts\import_market_prices.py "data\imports\Markte History.xlsx"
```
It replaces every season the file has rows for and touches no other. No
server restart is needed; reopen the Markets tab. It names any row it left
out (no season, receiver, pack type, cartons or price) and any date it
could not read.

#### How it was built

`scripts/convert_market_workbooks.py` built it once from the office's
"LIETJIES Markte <year> SEISOEN" workbooks, and can build it again if
another old season turns up:
```bat
backend\.venv\Scripts\python.exe scripts\convert_market_workbooks.py "data\imports\Markte History.xlsx" "data\imports\LIETJIES Markte 2022 SEISOEN.xlsx" "data\imports\LIETJIES Markte 2023 SEISOEN.xlsx" ...
```
That overwrites any corrections made in the file, so correct the rebuilt
copy again or merge by hand.

No two seasons were kept the same way. What the converter does about it:

- **The season** is the year in the file name.
- **The receiver sheets are read, not the Totaal sheets.** 2024's receiver
  sheets add up exactly to its Totaal sheet, and 2025's do too apart from
  AgriLink's sea loads, which its Totaal leaves blank. Last season's Totaal
  copied into the next workbook is unreliable: the 2024 workbook's
  "2023 Totaal" has a row of formula junk (Dapper's cartons at R0.03 to
  R2.33 a carton), and files 2023's 4.5kg air sales under "2kg MIX".
- **2022 is an older layout.** The header row is a row lower, the grade
  sits where SEND would be, and only the carton size is on the row above,
  so the pack type is the two together. Its "Local" sheet holds every local
  market, so each row's MARK is its receiver.
- **Copies of a sheet are left out** (`SKIP_SHEETS`): 2022's "AgriLink",
  "AgriLink (3)" and "AgriLink (5)", and 2023's "Agrilink LU 23". 2022's
  "AgriLink" combines the air and sea sheets, with the sea loads at the
  R87.63 advance payment.
- **A second line on the same delivery note** (the office leaves its date
  blank) counts as part of that delivery, and **the totals row** at the
  bottom of a sheet does not. Every sheet adds up to its own totals row.
- **A date that is a typo** is worked out from the delivery notes either
  side, which are numbered in the order they went out (`DATE_FIXES`), and
  the row's From column says so. 2023 has two: Dapper note 922
  ("26/09/202") is 26 September, between note 920 (20 Sep) and note 923
  (26 Sep), and BoereTrust note 965 ("20/14//2023") is 20 November,
  between note 964 (20 Nov) and note 966 (21 Nov). A typo not in the list
  is left blank with a Note for the office.
- **AgriLink's sea prices:** 2022 uses its sea sheet's R336 to R360 a
  carton, the higher of the two figures, not the R87.63 advance on the
  payments sheet. 2025's sea rows (R78 to R84, likely the advance; the 2025
  Totaal leaves sea blank) carry a Note until the final price is known.
- **Names are lined up with Packhouse's** where the workbooks spelled them
  differently (`PACK_RENAMES`, `RECEIVER_RENAMES`). 2022's "4.55kg XL" is
  the 4.5kg Rhino carton, AgriLink's sea sheets are "Agrilink USA Sea" in
  every season, and Boeretrust and BoereTrust are "Boere Mark Trust".
  **Add to these maps rather than editing the old workbooks.**
