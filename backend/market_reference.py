"""Joburg Market's own published daily commodity totals, read for a free
Litchis reference price the farm's own market-statement figures
(MarketPackPrice, scripts/import_market_prices.py) can be judged against.
Fetched from the market's public "Daily Prices" page - no login, no key,
no CDN (it is plain WordPress + a legacy PHP table, same shape weather.py
already scrapes a JSON API for).

The page itself only ever shows one trading day's totals plus a running
month-to-date figure that resets every month - there is no archive, API or
date to ask for (its form takes a commodity and nothing else) - so
sync_joburg_litchi_price() is what turns it into a history: each trading
day's row is a snapshot, kept in MarketDailyPrice because the site itself
never will. A day the job doesn't run is a day missing for good. The
Markets tab draws it as a dashed line: average price per unit sold (value
over units - the market's units for litchis are mostly 2kg boxes, so it
sits beside the farm's R/carton) and total units sold. Run from main.py on
the same kind of timer as
risk.snapshot_expected_forecast - a background job with no return value,
failures logged there and simply retried next cycle, never fatal to the app
since this is a reference figure, not a farm record."""
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from models_owner import MarketDailyPrice

_URL = "https://joburgmarket.co.za/jhb-market/dailyprices.php"
_COMMODITY = "LITCHIS"
_TAG_RE = re.compile(r"<[^>]+>")
_NUMBER_RE = re.compile(r"-?[\d,]+\.?\d*")
# The site's bot protection 406s urllib's default "Python-urllib/x.y"
# User-Agent outright - a browser-shaped one is all it checks for.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BoordOwner/1.0; "
                          "+https://github.com/DawiePieterse/boordowner)"}


def _fetch_html() -> str:
    url = _URL + "?" + urllib.parse.urlencode({"all": "View All Commodities"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=_HEADERS), timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _first_number(cell_html: str) -> Optional[float]:
    """Each cell holds today's figure on its own line, then "MTD: ..." on
    a second - the cell's own first number is always the one wanted here."""
    text = _TAG_RE.sub(" ", cell_html)
    m = _NUMBER_RE.search(text)
    return float(m.group(0).replace(",", "")) if m else None


def page_date(html: str) -> Optional[date]:
    """The trading day the page's figures are for - "This information is
    for <b>28 October 2026</b>". Not always today: over a weekend, and on a
    weekday morning before its noon update, it still shows the last trading
    day."""
    m = re.search(r"This information is for\s*(?:<[^>]+>\s*)*(\d{1,2} [A-Za-z]+ \d{4})", html)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d %B %Y").date()
    except ValueError:
        return None


def parse_litchi_row(html: str) -> Optional[dict]:
    """The commodity table is one <tr> per row: Commodity, Total Value
    Sold, Total Qty Sold, Total Kg Sold, Qty Available. Matched by regex,
    not an HTML parser - it's a plain table and every other format this
    app reads is already handled by openpyxl or the stdlib."""
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 4:
            continue
        name = _TAG_RE.sub("", cells[0]).strip().upper()
        if name != _COMMODITY:
            continue
        value, qty, kg = (_first_number(cells[i]) for i in (1, 2, 3))
        if value is None or kg is None:
            return None
        return {"value_sold": value, "qty_sold": qty, "kg_sold": kg}
    return None


def sync_joburg_litchi_price() -> None:
    """Background-job entry point (main.py): the Litchis total for the
    trading day the page shows, upserted by that day - so the several runs a
    day (config.MARKET_SYNC_HOURS), and the weekend's, just rewrite the same
    row rather than file Friday's figures under Saturday. Most of the year
    there is no litchi trading at all, in which case the page lists the
    commodity at 0 kg or leaves it out entirely - either way, nothing is
    stored; a missing day is "no data", never a fabricated zero. Nor is a
    day it can't read the date of: filed under the wrong day it would be
    worse than missing."""
    html = _fetch_html()
    parsed = parse_litchi_row(html)
    if not parsed or not parsed["kg_sold"]:
        return
    day = page_date(html)
    if day is None:
        print("[market] Joburg Market page had no readable date - nothing stored", flush=True)
        return
    from db import owner_engine
    with Session(owner_engine) as owner:
        row = owner.exec(select(MarketDailyPrice).where(MarketDailyPrice.price_date == day)).first()
        if row is None:
            row = MarketDailyPrice(price_date=day, fetched_at=datetime.now(timezone.utc).replace(tzinfo=None))
        row.value_sold = parsed["value_sold"]
        row.qty_sold = parsed["qty_sold"]
        row.kg_sold = parsed["kg_sold"]
        row.rand_per_kg = round(parsed["value_sold"] / parsed["kg_sold"], 2)
        row.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
        owner.add(row)
        owner.commit()
