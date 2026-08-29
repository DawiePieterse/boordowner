# Weather, Harvest & Risk — A Guide for the Farmer

This guide covers the three tabs that answer the question *"how is this
season shaping up, and why?"* — **Weather**, **Analysis** (the harvest
numbers), and **Risk** (the season risk score and harvest forecast). All
three live on the read-only Owner View — the link ending `/owner/?key=...`
that Admin issues under **Settings → Owner View**. They are not part of
the Admin app itself, which keeps the day-to-day operational tabs.

> There is no tab literally called "Harvesting" — the harvest data lives
> under **Analysis**, and the harvest *prediction* lives under **Risk**.
> Both are covered here.

The last part of this guide — [The risk model](#the-risk-model) — explains
in plain language where the score comes from, what it can and cannot tell
you, and how to actually use it in a season.

---

## Table of contents

- [The Weather tab](#the-weather-tab)
- [The Analysis tab — the harvest numbers](#the-analysis-tab--the-harvest-numbers)
- [The risk model](#the-risk-model)
- — [What the score is](#what-the-score-is)
- — [Where the four factors came from](#where-the-four-factors-came-from)
- — [The four factors](#the-four-factors)
- — [How a season is scored](#how-a-season-is-scored)
- — [The Harvest Forecast card](#the-harvest-forecast-card)
- — [Checking it against reality](#checking-it-against-reality)
- — [What the score cannot tell you](#what-the-score-cannot-tell-you)
- — [How to use it through a season](#how-to-use-it-through-a-season)

---

## The Weather tab

### What you're looking at

The farm's weather history, **1987 to today**, one line per year you tick.
The data comes from a weather service (Open-Meteo) for the farm's own GPS
position — set in Settings — not from an on-farm weather station. It is
recorded hour by hour and then averaged or totalled into one point per day
for the chart.

Every time you open the tab, the app quietly fetches any hours that have
happened since the last time and adds them to the stored history. The line
under the chart title — *"Weather data current to …"* — tells you how
fresh it is. If the internet is down, the tab still shows everything
already stored; it just won't be topped up.

### The measurements

Tick as many as you like along the top:

| Measurement | What the daily figure is |
|---|---|
| Temperature | The day's average |
| Humidity | The day's average |
| Dew Point | The day's average |
| Precipitation | The day's **total** rain, in mm |
| Wind Speed | The day's average |
| Soil Temp (6cm) | The day's average |
| UV Index | The day's **peak**, not its average |
| Sunshine | The day's **total** hours of sunshine |

> **One gap worth knowing about:** Soil Temp and UV Index are only
> available from **2020 onward**. The long weather archive that reaches
> back to 1987 doesn't carry them, so those two lines simply stop for
> earlier years. Everything else runs the full record.

### The years

Below the measurements, tick whichever years you want to compare. Every
year is drawn on the same **1 January – 31 December** axis, laid over each
other, so you can see directly whether this October was wetter than last
October.

- The tab opens on the **most recent year on file**.
- Past years are drawn in **grey-blue** — the lighter the shade, the older
  the year.
- The **current year** is always drawn in a **red/orange** shade, and drawn
  slightly heavier, so the season in progress is never mistaken for
  history.

> Note the calendar here is a plain **calendar year**, not the harvest
> season. The Analysis tab, by contrast, runs its season from **1 August**.
> That's deliberate: "what was the weather like in 2023" naturally means
> the whole of 2023, while "the 2023 harvest" means the picking season.

### Getting it off the screen

The small **PDF button** in the card's top-right corner downloads the
chart exactly as it appears as a one-page PDF, headed
with the farm name — ready to print, email, or drop into a report.

### What it's good for

- Comparing this spring against previous springs, before the crop tells you.
- Checking the risk factors yourself. If the Risk tab says
  "Flowering-Period Sunshine" was poor this year, tick Sunshine and the
  last few years here and you can see it with your own eyes.
- Settling arguments about whether a season really was drier or hotter than
  people remember.

---

## The Analysis tab — the harvest numbers

This is the harvest side of the same question: not what the weather did,
but what actually came off the trees. It compares the **current season**
against the daily historical records for **2020–2025**, which were imported
once from the farm's own pre-app spreadsheets and never written to again.

At the top, four quick numbers: **Season to Date** (kg picked so far),
**vs 5-Yr Average Pace** (green if ahead of where the 5-year average stood
on this same day of the season, red if behind), the **Current Season**
being compared, and how many **Years of History** are on file.

Below that, each card answers a different question:

- **Season Pace** — cumulative kg day by day from 1 August, this season in
  navy against every prior year plus a dashed 5-year-average line. Shows
  not just *whether* you're ahead or behind, but *when* the gap opened.
- **Per-Block Yield** — this season's kg per hectare (or per tree) for each
  block against that block's own history, with a farm-average line. Green
  or red in the **vs Average** column, so under-performing blocks stand out
  straight away.
- **Harvest Volume by Block and Season** — a bubble per block per season,
  sized by kg, blocks ranked by total volume.
- **Yield per Tree/Hectare by Block and Season** — a heatmap of every block
  against every season. Read across a row for one block's history, down a
  column for how a whole season went.
- **Variety Performance Over Time** — average kg per tree per variety, by
  season, for spotting a variety trending up or down.
- **Harvest Season Length** — one bar per season from first pick to last,
  labelled with the span and how many of those days actually had picking.
- **Monthly Harvest Volume** — kg by month for every season, with each
  month's share of that season's total, so a shift in *when* the crop comes
  in is visible.

Every one of these cards has the same PDF download button as the Weather
chart.

> **A caveat on the historical block figures.** Blocks **8a/8b**,
> **10a/10b**, **17a/17b** and **19a/19b** didn't exist separately before
> the app — the old spreadsheets recorded one combined total for each pair.
> Those totals have been split between the two sub-blocks in proportion to
> their hectares. It's a fair estimate, not a record of what was actually
> picked from each half, and every figure built from a split carries a
> small info icon so it can't be mistaken for an exact number.

---

## The risk model

### What the score is

The **Critical Season Risk Indicator** is a score from **0 to 100** of how
risky *this season's weather* looks for a poor harvest. Higher is worse.

It is built from **four weather factors that best explained this farm's own
harvest variation** across its own record. It is **not** a generic lychee
agronomy model, and it is **not** a weather forecast dressed up as a yield
figure. Every number in it is anchored to seasons that actually happened on
this farm.

The score falls into one of four bands:

| Score | Band |
|---|---|
| below 25 | **Low** |
| 25 to below 50 | **Moderate** |
| 50 to below 75 | **Elevated** |
| 75 and above | **High** |

### Where the four factors came from

The four factors are the result of a **one-off study**, not something the
app recalculates every time you open it. That matters: if the app re-picked
its "best" factors each season, the meaning of the score would drift and
you could never compare one year's score to another's.

The study was last re-run once the farm's full **1987–2025** weather and
harvest record had been imported. It tested roughly **230 combinations** of
weather measure and time window against three separate stretches of the
farm's history:

- **Block 7 alone, 1987–2025** (37 seasons) — the one block continuously in
  production across the whole record; the old workbook's "Blok 7" is
  today's block 7, the same 2,132 trees planted in 1982.
- **The old orchard's whole-farm total, 1987–2009** (23 seasons).
- **The replanted orchard, 2012–2025** (14 seasons).

Only factors that **pointed the same way in all three** were kept, and they
were also checked against **each other** so the score isn't counting one
underlying signal four times over. That last check threw out a lot: dew
point, humidity, dry hours and day-night temperature swing all measure much
the same thing — spring dryness — and move together so closely that any one
of them stands in for the rest. Only one of them survives in the score, and
the four that were kept overlap with each other very little.

> An earlier version of this score, fitted on 2020–2025 alone, included
> **winter chill hours** and **rain days during flowering**. Once there was
> enough history to test them properly, both turned out to have no
> relationship with this farm's yields at all — winter chill correlated at
> essentially zero across 37 seasons — and they were replaced.

### The four factors

Each is measured over a **fixed calendar window** — the same dates every
year, regardless of when picking actually starts — and contributes up to
**25 risk points**.

| Factor | Window | What's measured | Which way is bad |
|---|---|---|---|
| **Fruit Development Air Dryness** | 16 Sep – 31 Oct | Average dew point (°C) | **Lower** (drier air) is worse |
| **Fruit Development Warmth** | 16 Sep – 15 Nov | Average of each day's *maximum* temperature (°C) | **Higher** is worse |
| **Flowering-Period Sunshine** | 1 Aug – 15 Sep | Total hours of sunshine | **Lower** is worse |
| **Fruit-Sizing Rainfall** | 1 Oct – 30 Nov | Total rain (mm) | **Lower** is worse |

**Why each one:**

- **Air dryness during fruit development** is the single strongest weather
  signal in the whole record. Dry air while the fruit is sizing means the
  crop loses water faster than the tree can take it up, and those seasons
  came in smaller. Dew point measures that dryness more directly than
  humidity on its own.
- **Warmth during fruit development** — persistently warm afternoons
  through this period line up with smaller crops here. It's measured as the
  average of each day's peak rather than a flat average across day and
  night, so it picks up a genuinely hot spring rather than being washed out
  by cool nights. It's also a steadier measure than counting rare extreme
  days.
- **Sunshine during flowering** — bright flowering weather produced the
  bigger crops in this farm's record. Dull, overcast spells while the trees
  are in flower mean poorer pollination and fruit set.
- **Rain while the fruit fills out** fed this farm's bigger harvests; the
  dry Octobers and Novembers line up with the smaller ones.

### How a season is scored

Each factor is scored on a sliding scale between the **best and the worst
value seen across the reference seasons — 2012 onward**:

- The best value ever recorded for that factor scores **0 points**.
- The worst value ever recorded scores **25 points**.
- Anything in between scales proportionally.
- Anything *beyond* either extreme is held at 0 or 25 rather than pushed
  further.

So **25 out of 25 means "as bad as the worst season we have on file for
this factor"** — it is not an absolute agronomic threshold. The four scores
are added up to give the 0–100 total.

> **Why 2012?** That's the first season of the replanted orchard with
> per-block records. Going further back would mix in the *old* orchard,
> which was grubbed and replanted around 2010 — a completely different set
> of trees.

**Windows that haven't closed yet are left out entirely.** They are never
assumed to be zero risk. This is why the current season shows a greyed
**"Score so far"** with a count — *"2 of 4 factors known"* — instead of a
final number, until the last window closes on 30 November.

The **Season** dropdown lets you look at any past season the same way, with
its own score, band, and a card per factor showing that season's actual
value against the reference range and how many of the 25 points it earned.

### The Harvest Forecast card

At the top of the Risk tab, the same four factors are turned into **three
kg predictions for the current season** — **Favorable**, **Expected** and
**Unfavorable** — rather than one falsely-precise number, since most of a
season's outcome still depends on weather that hasn't happened yet.

**How each factor's window is filled in.** For any window not yet closed,
the app splits the remaining days three ways, and the **Basis** column in
the table shows you exactly how the split fell (e.g. *"14d actual + 15d
forecast + 16d assumed"*):

1. **Actual** — days already past, using real recorded weather.
2. **Forecast** — up to **15 days ahead**, using a real short-range weather
   forecast.
3. **Assumed** — everything beyond that, filled in from the historical
   range. This is where the three scenarios differ: **Favorable** assumes
   the best value seen in any reference season, **Expected** the average,
   **Unfavorable** the worst.

Each scenario's projected weather is then scored through the **exact same
0–100 scoring** used for real seasons, so a scenario's score is directly
comparable to any past year's.

**Turning a score into kilograms.** The score is converted to kg by a
straight line fitted through the (risk score, harvest total) pairs from
**2016 onward**. That's a narrower range than the score's own 2012
reference, and deliberately so: in 2012–2015 block 7 was the only block
bearing at all — the replanted blocks produced literally nothing until 2016
— so those small totals reflect a young orchard, not bad weather. Including
them would teach the line that a middling risk score means a tiny crop. The
line's strength and how many seasons it was fitted on are both shown in the
methodology panel.

**The two extremes are held to what has actually happened.** Predictions
are clamped to the best and worst harvests on record rather than letting
the line run out past them (at the unfavorable end, it otherwise ran
straight past zero into negative kg). So read the outer two cards as
*"about as good, or as bad, as it has ever gone here"* — not as exact
figures.

> **The Unfavorable scenario is harsher than any real season.** It combines
> each factor's own worst historical *year* — four different years, not one
> real season that went wrong on everything at once. Because the four
> factors are close to independent, a season that is simultaneously at its
> worst on all four is very unlikely. Treat it as a floor to plan against,
> not as a probable outcome.

**If the live forecast can't be reached**, the card still appears — every
factor simply falls back to the historical range for its whole remaining
window, and a note says so. Likewise, a genuine gap in the recorded data
(a service outage) makes that factor fall back to the historical range for
its entire window rather than quietly treating missing data as "no risk";
that row is flagged in the table.

The Risk tab makes a live call out to the weather service and reads the
whole history, so it can take a few seconds to fill in. The
*"Working out this season's forecast…"* message means it's still going, not
that something is broken.

### Checking it against reality

Below the season inspector, two bar charts sit stacked deliberately:
**Risk Score by Season** and **Actual Harvest by Season**, same years, same
order. This is the back-test — you can check by eye whether the seasons
that scored badly really did come in smaller. It is there so the score can
be judged, not just believed. The current season is marked with an asterisk
and may still be partial.

### What the score cannot tell you

These limits are printed in the app itself, under *"How this score is
calculated"*, and they matter:

- **Weather explains roughly a quarter to a half of this farm's swing from
  season to season.** That is real signal — but most of what makes a season
  good or bad is something this score simply cannot see: pruning, nutrition,
  irrigation, pests, labour, timing of the pick.
- **Around 230 combinations were tested to arrive at these four.** In any
  search that wide, some of what looks like signal is chance. These four
  were kept because they held up across separate stretches of the farm's
  history, not because they scored well once. The three stretches also
  overlap — block 7 is a large share of the modern farm's total — so they
  aren't three fully independent confirmations.
- **There is no alternate-bearing pattern on this farm.** Growers often
  expect lychees to alternate a big year with a small one. Across 35
  back-to-back season pairs here, last season's crop tells you essentially
  nothing about this one. (An earlier version of the app claimed the
  opposite; the longer record disproved it.)
- **It says nothing about quality, size, or price** — only about total kg.

### How to use it through a season

**The score fills in on a fixed timetable.** Because the windows are fixed
calendar dates, you always know when each factor locks in:

| From | What you know |
|---|---|
| 1 Aug | Nothing final yet — flowering sunshine window opens |
| 16 Sep | **1 of 4** — Flowering-Period Sunshine is final |
| 1 Nov | **2 of 4** — Fruit Development Air Dryness is final |
| 16 Nov | **3 of 4** — Fruit Development Warmth is final |
| 1 Dec | **4 of 4** — the season's score is final |

So the score is at its least certain exactly when it would be most useful,
and fully certain only once the crop is largely determined. That is honest,
and it's the reason for the three-scenario forecast: early in the season,
**the spread between Favorable and Unfavorable is the real information**,
not the Expected figure in the middle.

**Practical use:**

- **Plan against the range, not the middle.** In August and September, look
  at the gap between Favorable and Unfavorable and make sure both ends are
  survivable — crates, labour, transport, packhouse slots, cash flow. As
  the season closes the windows one by one, that gap narrows and you can
  commit harder.
- **Look at *which* factor is scoring badly, not just the total.** The
  per-factor cards tell you where the risk is coming from, and two of the
  four are things you can partly answer:
  - **Air dryness** and **fruit-sizing rainfall** are both water. A season
    scoring high on these is a season to bring irrigation forward and be
    less sparing with it.
  - **Flowering sunshine** and **fruit-development warmth** you cannot do
    anything about — but knowing early that they've gone against you is
    worth having when you're deciding how much to commit elsewhere.
- **Use it as one input among several.** Walk the orchard, look at fruit
  set, and read the **Season Pace** chart in the Analysis tab once picking
  starts. If the pace chart and the risk score disagree, believe the fruit
  on the trees — the pace chart is measuring what actually happened, and
  the score is only measuring the weather's contribution to it.
- **Look back before you trust it forward.** Use the Season dropdown to
  pull up two or three past seasons you remember well, and see whether the
  score matched what you lived through. That's the fastest way to calibrate
  how much weight to give it.

**What not to do with it:**

- Don't quote the Expected kg figure as a commitment. It's a description of
  what the historical pattern implies about this season's weather — not a
  promise of what will be harvested.
- Don't compare this score to another farm's. Every number in it is scaled
  against *this* farm's own best and worst seasons.
- Don't read last season's score as telling you anything about this one.
  The record here says it doesn't.
