#!/usr/bin/env python3
"""Check every imported historical block id against Boord's register.

A historical row keyed to a block id Boord no longer has is the quiet
failure mode of these imports. Nothing errors. Season totals still add up,
because the Analysis tab sums by season as well as by block. The block
simply shows no history, and a block with no history looks exactly like a
block that was not bearing yet.

That is what happened when Boord's register lost its `10a`/`17a`/`19a`
lettering (see scripts/block_renames.py). Run this after either import -
or after any Boord release that touches Master Data - and it will say so
out loud instead.

Usage:
    backend/.venv/bin/python3 scripts/check_block_ids.py

Exits non-zero if anything is unmatched, so it can gate a re-import.
"""
import os
import sys
from collections import defaultdict

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)

from sqlmodel import Session, select  # noqa: E402

from db import boord_engine, owner_engine  # noqa: E402
from models_boord import Block  # noqa: E402
from models_owner import HistoricalAnnualYield, HistoricalHarvest  # noqa: E402


def main():
    with Session(boord_engine) as boord:
        blocks = {b.id: b for b in boord.exec(select(Block)).all()}

    used = defaultdict(lambda: {"daily": 0, "annual": 0, "kg": 0.0})
    with Session(owner_engine) as owner:
        for h in owner.exec(select(HistoricalHarvest)).all():
            used[h.block_id]["daily"] += 1
            used[h.block_id]["kg"] += h.kg
        for a in owner.exec(select(HistoricalAnnualYield)).all():
            # block_id None is deliberate: the 1987-2009 rows are whole-farm
            # totals from before today's register existed. Not a mismatch.
            if a.block_id is None:
                continue
            used[a.block_id]["annual"] += 1
            used[a.block_id]["kg"] += a.kg

    unknown = sorted(bid for bid in used if bid not in blocks)
    no_history = sorted(bid for bid in blocks if bid not in used)

    print(f"Boord register: {len(blocks)} blocks")
    print(f"History covers: {len(used)} block ids\n")

    if unknown:
        print("*** HISTORY POINTING AT BLOCKS BOORD DOES NOT HAVE ***")
        for bid in unknown:
            u = used[bid]
            print(f"    {bid:<6} {u['daily']} daily rows, {u['annual']} annual rows, "
                  f"{u['kg']:,.0f} kg - INVISIBLE in the app")
        print("\n    Boord's ids have moved. Add the mapping to")
        print("    scripts/block_renames.py and re-run the imports.\n")
    else:
        print("Every historical block id matches a block in Boord.\n")

    if no_history:
        # Not an error on its own - a young block genuinely has no past.
        print("Blocks with no history at all (fine for anything planted recently):")
        print("    " + ", ".join(no_history) + "\n")

    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
