# Owner View — Guide

A read-only way to check on harvest progress — no username/password,
nothing to remember.

---

## Opening your dashboard

You'll have been sent a link that looks like:

```
https://.../owner/?key=...
```

Open it in any browser, on any phone, tablet, or computer. That's the
whole login process — the long code at the end of the link *is* your
access, so there's no separate username/password to enter.

**Worth bookmarking it** (or adding it to your home screen) so you don't
need to dig up the link again next time.

### If you're checking this from off the farm

If you'll normally open this away from the farm itself (from home, an
office, etc.), you'll also need **Tailscale** installed and connected on
whatever device you use — ask the farm office to set this up for you
once, it's a one-time thing. The link's code proves you're allowed to
see the dashboard, but reaching the farm's server from outside its own
network needs Tailscale regardless — the two aren't the same thing. If
you're always on the farm's own Wi-Fi when you check, you can skip this.

If the link ever stops working, ask the farm office for a new one — see
"If something looks wrong" below.

---

## What you're looking at

At the top: the farm name, the current date/time, and today's weather.
Below that, four tabs:

| Tab | What it answers |
|---|---|
| **Dashboard** | What's happening right now, for a date range you pick |
| **Analysis** | How this season is tracking against 2020–2025 history |
| **Weather** | What the weather has actually done here, 1987 to today |
| **Risk** | How risky this season's weather looks, and what it implies for the harvest |

The Dashboard is the day-to-day view; the other three are the
season-level ones. **Analysis, Weather and Risk are on this link only** —
the farm office's own Admin app doesn't have them, so this is the place
they're looked at.

---

## Dashboard tab

Below the header, a filter bar:

- **Farm / Supplier** — narrow the view to one farm/supplier, or leave it
  on "All farms / suppliers."
- **Period start / Period end** — the date range you're looking at, or
  tap **Today**, **This Week**, or **Season** for a quick preset (whichever
  one matches the current dates stays highlighted).

Changing any of these pulls fresh numbers straight away — there's no
separate Refresh button to look for.

Then the numbers themselves:

- **KPI cards** — teams/workers/blocks active, total kg and crates,
  averages, and a Harvesting/In Transit/Received breakdown, all for the
  selected date range.
- **Harvesting / In Transit / Received** — tap any of these to expand
  the actual list of loads. Harvesting and In Transit are color-coded:
  🟢 green (recent), 🟡 yellow (been a while), 🔴 red (waiting too
  long) — the same color scheme used throughout the farm's own app.
- **Workers** — tap to expand a per-worker breakdown: crates, kg, and
  average yield per crate for the period (no wage/amount-due figures —
  see "What this doesn't show" below).
- **Blocks** — tap to expand a per-block breakdown: crates, kg, and
  average yield per crate, per tree, and per hectare for the period.

---

## Analysis tab

How this season is doing against **2020–2025** history. This tab ignores
the Dashboard's date range — it always covers the full history plus
whatever's been picked so far this season.

Four cards across the top: **Season to Date** kg, **vs 5-Yr Average
Pace** (how far ahead or behind the season is running at this same point
in the year — green ahead, red behind), the **Current Season** year, and
how many **Years of History** are on file.

Then the charts:

- **Season Pace** — cumulative kg picked so far, one line per year plus a
  5-year average, so you can see at a glance whether the season is running
  ahead or behind.
- **Per-Block Yield** — each block's yield this season against its own
  history (switch between kg/hectare and kg/tree), with a farm-wide
  average line and red/green coloring for under/over-performing blocks.
- **Harvest Volume by Block and Season** and **Yield per Tree/Hectare by
  Block and Season** — a bubble chart and a heatmap covering every block
  against every season at once.
- **Variety Performance Over Time** — average kg per tree by variety,
  season by season (filter to a single variety with the dropdown).
- **Harvest Season Length** — when each season started and ended, and how
  many of those days actually had picking.
- **Monthly Harvest Volume** — a heatmap of kg picked by month, with each
  season's grand total alongside.

Every chart has a small <i class="fa-solid fa-file-pdf"></i> button in its
top-right corner to download it as a one-page PDF, headed with the farm
name and the chart's title.

> A few blocks (8a/8b, 10a/10b, 17a/17b, 19a/19b) didn't exist separately
> before the app — their historical figures are split out of one combined
> old record by hectares, so those are estimates rather than measured
> numbers. They carry a small ⓘ icon as a reminder.

---

## Weather tab

The farm's own weather record, **1987 to today**, drawn from the weather
service for the farm's GPS location. Opening the tab also tops the record
up with any hours recorded since the last time someone looked, so the
line under the heading tells you how current it is — *"Weather data
current to 2026-08-17 09:00"*.

Two sets of tick boxes control the chart:

- **Measurements** — Temperature, Humidity, Dew Point, Precipitation,
  Wind Speed, Soil Temp (6cm), UV Index, and Sunshine. It opens on
  Temperature; tick as many as you want to compare.
- **Years** — every year on file, from 1987 down to the current one. It
  opens on the most recent year alone.

Every year you tick is drawn as its own line over a shared 1 January –
31 December axis, so the point of the tab is **comparing years against
each other** — tick 2024 and 2025 alongside this year to see whether a
spring is running warmer or drier than usual, rather than reading one
long line across four decades. Untick everything and the chart tells you
so instead of going blank. This chart downloads as a PDF the same way as
the Analysis ones.

---

## Risk tab

A 0–100 score of how risky a season's weather looks for a poor harvest —
higher is worse — plus a harvest forecast built from the same figures.

### Where the score comes from

Four weather factors, each worth up to 25 points. These are not a generic
agronomy model: they came out of a one-off study of **this farm's own
harvest and weather records back to 1987**, keeping the factors that
lined up best with bigger or smaller crops *and* that measure genuinely
different things, rather than four versions of the same one.

| Factor | Window | What it measures |
|---|---|---|
| **Fruit Development Air Dryness** | 16 Sep – 31 Oct | Average dew point — the strongest single signal in the whole record. Dry air while the fruit is sizing means the crop loses water faster than it can take it up. |
| **Fruit Development Warmth** | 16 Sep – 15 Nov | Average daily maximum temperature. Persistently warm afternoons line up with smaller crops. |
| **Flowering-Period Sunshine** | 1 Aug – 15 Sep | Total sunshine hours. Bright flowering weather produced the bigger crops; dull, overcast spells mean poorer fruit set. |
| **Fruit-Sizing Rainfall** | 1 Oct – 30 Nov | Total rainfall. Rain while the fruit fills out fed the bigger harvests; dry Octobers and Novembers line up with the smaller ones. |

Each factor is scored against the best and worst that factor has actually
been since **2012** — the season the replanted orchard came into bearing.
So 25 out of 25 means "as bad as the worst season on file for this
factor," not some absolute agronomic threshold.

### Reading the tab

Use the **Season** dropdown to inspect any season on file or the current
one. You get its score, its band — **Low** (under 25), **Moderate**
(under 50), **Elevated** (under 75) or **High** (75 and above) — and a
bar per factor
showing what that season actually measured, how that compares to the
farm's range, and how many risk points it contributed.

A factor is only counted once its own stretch of the calendar has
finished. Until then it's left out of the total rather than assumed to be
harmless, so the current season reads as a **"score so far"** with a
count of how many of the four factors are known yet, and only becomes a
final score once the last window closes at the end of November.

Below the inspector, two bar charts — **Risk Score by Season** and
**Actual Harvest by Season** — sit stacked so you can check by eye
whether a higher score really did line up with a smaller harvest that
year.

### Harvest Forecast card

At the top of the tab, three scenarios — **Favorable**, **Expected** and
**Unfavorable** — turn the same four factors into predicted kg for this
season. Three figures rather than one, because most of a season's outcome
still depends on weather that hasn't happened yet.

For whatever part of a factor's window is still ahead, the forecast uses
a real weather forecast for the next 15 days and a historical
best/average/worst assumption beyond that. The **Basis** column under the
three cards shows exactly how much of each factor is which — e.g.
*"14d actual + 16d forecast + 16d assumed"*. If the weather service can't
be reached, or a factor's own data has a genuine gap, that factor falls
back to its historical range for the whole window and the row says so,
rather than quietly treating missing data as "no risk."

Treat **Favorable** and **Unfavorable** as "about as well or as badly as
it has ever gone here" rather than precise numbers — each one combines
every factor's own best or worst *year*, which is four different real
years, not one real season that went that way on everything at once.

### What the score can't tell you

The collapsible **"How this score is calculated"** panel at the bottom of
the tab spells the method out in full, and is deliberately upfront about
the limits. The two worth knowing:

- **Weather explains roughly a quarter to a half** of this farm's swing
  from season to season. That's real signal, but most of what makes a
  season good or bad is something this score simply cannot see. Use it as
  one input among several, not a verdict.
- **There is no alternate-bearing pattern here.** Lychees are often
  expected to follow a heavy year with a light one, but across 35
  back-to-back season pairs on this farm, last season's crop tells you
  essentially nothing about this one. (An earlier version of this guide
  and of the app said the opposite — the longer record disproved it.)

---

## What this doesn't show

This link is deliberately limited to progress information — it doesn't
show individual worker pay, contact details, or anything you could
change. There's no way to edit data from this view; it's read-only by
design. If you need more detail than this covers, ask the farm office
directly.

---

## If something looks wrong

- **The link shows "This link isn't valid"** — this is about the link
  itself, not your connection. It may have been regenerated (this happens
  if it needed to be revoked and reissued) or mistyped/cut off when it
  was shared with you. Ask the farm office for the current link.
- **An amber bar says you're offline** — this one *is* the connection.
  The page shows the last figures it saved for whatever date range/farm
  is selected, labeled with how old they are (e.g. *"Offline - showing
  figures from 12 min ago"*), rather than just freezing whatever happened
  to be on screen. If it's never loaded that particular combination
  before, it says so plainly instead of guessing. It updates itself once
  it can reach the farm again. Check **Tailscale** is connected (below).
- **Numbers look outdated** — drag down from the top of the page and let
  go, or double-check the date range at the top actually covers the
  period you're expecting (changing any filter also pulls fresh numbers
  automatically).
- **The Weather or Risk tab is slow, or its figures look stale** — those
  two are the only ones that reach out to the weather service while
  loading, so they take longer than the others and are the first to
  suffer if that service is unreachable. Both fall back to the stored
  record on their own, so this shows up as slowness or slightly old
  figures rather than an empty screen. If the rest of the page is fine,
  the farm's server is fine.
- **Nothing loads at all, and you're off the farm** — check that
  **Tailscale** is open and shows **"Connected"** first (see "If you're
  checking this from off the farm" above) - this is the most common
  cause. If Tailscale is connected and it still won't load, check your
  own internet connection, then let the farm office know if it persists.
