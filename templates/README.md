# Import templates - historical harvest

Moved out of Boord's `templates/README.md` with the two CSVs they
describe. Both fed `HistoricalHarvest` / `HistoricalAnnualYield`, which
left Boord for this app.

## historical_harvest.csv

Daily per-block kg from seasons **before** this app existed. Optional -
skip it entirely if the farm has no records to load, or is only starting
to keep them now. Import via the setup wizard, or **Admin → Settings**
afterwards.

| Column | Required | Notes |
|---|---|---|
| `block_id` | yes | Must match an `id` from `blocks.csv`. Import blocks first, or the rows load but nothing lines them up with an orchard. |
| `date` | yes | The day the fruit was picked. `2024-11-18` is safest; `18/11/2024` is also read as day-first. |
| `kg` | yes | Kilograms picked from that block on that day. |
| `season_year` | no | Which season the day belongs to. Taken from `date` when blank, which is right unless a season straddles New Year. |
| `estimated` | no | `true` marks a figure that was worked out rather than recorded — most often an old combined block total divided between today's sub-blocks. Reports carry the flag through so an estimate is never mistaken for a measurement. |

**Splitting old blocks is your call, not the app's.** If the historical
records use a coarser block register than the farm does today — one
"block 8" where there are now 8a and 8b — decide the split yourself
(hectare ratio is the usual basis), write the resulting rows, and set
`estimated` on them. The app will not guess at that mapping: it is a
judgement about one particular orchard's past, and a wrong one produces
per-block yield figures that look entirely ordinary.

## historical_annual_yield.csv

Season **totals** from further back than daily records reach. Reference
only — this feeds an extra sheet on the Historical Harvest Data export,
not the Analysis tab or the Risk indicator.

| Column | Required | Notes |
|---|---|---|
| `season_year` | yes | e.g. `1994`. |
| `kg` | yes | Total kilograms for that season. |
| `block_id` | no | Leave **blank** for a whole-farm total with no block breakdown. That is the normal case for older books, whose block numbering usually has no reliable mapping to today's. |
| `estimated` | no | As above. |

Both files import from `.csv` or `.xlsx`, and each one **replaces its
whole table** rather than adding to it — so correcting a sheet and
importing it again is safe, and importing a partial file loses the rest.
Neither table is ever written by the app itself; these imports are the
only thing that fills them.
