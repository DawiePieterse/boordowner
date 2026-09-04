"""The wage-free Admin-Dashboard summary.

This is the one genuinely Owner-specific aggregation: the same active-entity
counts and per-worker / per-block breakdowns Boord's admin Dashboard shows
(../Boord/backend/routers/dashboard.py), with the wage column removed -
amount_due and rate_configured are payroll, deliberately not surfaced here.

Reads Boord's live database only (boord_session, read-only). The three
payments helpers are inlined below, wage-stripped, so this app carries no
copy of Boord's RateSetting / tier logic.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from db import get_boord_session, get_own_supplier_id
from models_boord import Block, HarvestRecord, Supplier, Worker
from timeutil import day_bounds

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _worker_ids_for_supplier(session: Session, supplier_id: Optional[int]) -> Optional[set]:
    """Which workers belong to a supplier filter, or None for no filter.
    Own-farm workers are seeded with supplier_id left NULL rather than
    pointing at the "Own Farm" row, so matching the own-farm supplier has to
    include NULL too. Copied verbatim from Boord's payments.py."""
    if supplier_id is None:
        return None
    own_id = get_own_supplier_id(session)
    if supplier_id == own_id:
        ids = session.exec(select(Worker.id).where(
            (Worker.supplier_id == None) | (Worker.supplier_id == own_id)  # noqa: E711
        )).all()
    else:
        ids = session.exec(select(Worker.id).where(Worker.supplier_id == supplier_id)).all()
    return set(ids)


def _supplier_display_name(worker: Optional[Worker], suppliers_by_id: dict, own_id: Optional[int],
                            own_name: str) -> str:
    """The farm/supplier name to group a worker under. Own-farm workers have
    supplier_id NULL and fall back to the own-farm name. Copied verbatim
    from Boord's payments.py."""
    if not worker or worker.supplier_id is None or worker.supplier_id == own_id:
        return own_name
    supplier = suppliers_by_id.get(worker.supplier_id)
    return supplier.name if supplier else "Unknown"


def _worker_kg_totals(session: Session, period_start: date, period_end: date,
                       supplier_id: Optional[int] = None) -> dict:
    """Per-worker net kg over the period. This is Boord's payments._worker_totals
    with every wage concern removed - no RateSetting lookup, no tier parsing,
    no amount accumulation. The Owner app never computes wages."""
    start_dt, end_dt = day_bounds(period_start, period_end)
    query = select(HarvestRecord).where(
        HarvestRecord.timestamp >= start_dt, HarvestRecord.timestamp <= end_dt)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    totals: dict[str, dict] = {}
    for r in session.exec(query).all():
        if not r.worker_id:
            continue
        entry = totals.setdefault(r.worker_id, {"total_kg": 0.0})
        entry["total_kg"] += r.weight_kg - r.deduction_kg
    return totals


@router.get("/summary")
def dashboard_summary(period_start: date, period_end: date, supplier_id: Optional[int] = None,
                      boord: Session = Depends(get_boord_session)):
    """Active-entity counts + per-worker and per-block breakdowns, wage-free.
    "Active" means had harvest activity within the filtered period/supplier,
    not a static master-data flag - so the numbers move with the filters."""
    start_dt, end_dt = day_bounds(period_start, period_end)
    worker_ids = _worker_ids_for_supplier(boord, supplier_id)
    query = select(HarvestRecord).where(
        HarvestRecord.timestamp >= start_dt, HarvestRecord.timestamp <= end_dt)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = boord.exec(query).all()

    active_teams = {r.team_id for r in records if r.team_id}
    active_workers = {r.worker_id for r in records if r.worker_id}
    active_blocks = {r.block_id for r in records if r.block_id}

    crate_counts: dict[str, int] = {}
    for r in records:
        if r.worker_id:
            crate_counts[r.worker_id] = crate_counts.get(r.worker_id, 0) + 1

    totals = _worker_kg_totals(boord, period_start, period_end, supplier_id)
    workers_by_id = {w.id: w for w in boord.exec(select(Worker)).all()}
    suppliers_by_id = {s.id: s for s in boord.exec(select(Supplier)).all()}
    own_id = get_own_supplier_id(boord)
    own_supplier = suppliers_by_id.get(own_id)
    own_name = own_supplier.name if own_supplier else "Own Farm"
    workers = []
    for worker_id, data in totals.items():
        w = workers_by_id.get(worker_id)
        crates = crate_counts.get(worker_id, 0)
        workers.append({
            "worker_id": worker_id,
            "name": w.name if w else worker_id,
            "supplier_name": _supplier_display_name(w, suppliers_by_id, own_id, own_name),
            "crates": crates,
            "total_kg": round(data["total_kg"], 1),
            "avg_kg_crate": round(data["total_kg"] / crates, 1) if crates else 0,
        })
    workers.sort(key=lambda w: w["total_kg"], reverse=True)

    block_totals: dict[str, dict] = {}
    for r in records:
        if not r.block_id:
            continue
        entry = block_totals.setdefault(r.block_id, {"crates": 0, "total_kg": 0.0})
        entry["crates"] += 1
        entry["total_kg"] += r.weight_kg - r.deduction_kg
    blocks_by_id = {b.id: b for b in boord.exec(select(Block)).all()}
    blocks = []
    for block_id, data in block_totals.items():
        b = blocks_by_id.get(block_id)
        total_kg = round(data["total_kg"], 1)
        blocks.append({
            "block_id": block_id,
            "name": b.name if b else block_id,
            "crates": data["crates"],
            "total_kg": total_kg,
            "avg_kg_crate": round(total_kg / data["crates"], 1) if data["crates"] else 0,
            "avg_kg_tree": round(total_kg / b.trees, 1) if b and b.trees else None,
            "avg_kg_hectare": round(total_kg / b.hectares, 1) if b and b.hectares else None,
        })
    blocks.sort(key=lambda b: (b["name"] or "").lower())

    return {
        "active_teams": len(active_teams),
        "active_workers": len(active_workers),
        "active_blocks": len(active_blocks),
        "workers": workers,
        "blocks": blocks,
    }
