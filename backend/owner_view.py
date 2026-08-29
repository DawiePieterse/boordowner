"""Read-only "Owner View" dashboard - a link-only alternative to full
Admin login for an owner or other interested party who just wants to
check on progress, not manage the farm.

Access is a single shared secret token embedded in the link
(.../owner/?key=...), not a username/password - see MANUAL.md, "Owner
View (read-only dashboard link)". The token lives in its own table
(OwnerViewToken), deliberately kept off the public SystemSetting model.

Shows per-worker kg/crates like the full Admin Dashboard, but
deliberately omits the wage figure (amount_due) - that's payroll
information the farm office needs, not something to hand out on a
shareable link.
"""
import secrets
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from db import get_own_supplier_id, get_session
from models import Block, HarvestRecord, OwnerViewToken, Supplier, Worker
from routers.analysis import build_analysis_summary
from routers.payments import _supplier_display_name, _worker_ids_for_supplier, _worker_totals
from routers.risk import build_harvest_forecast, build_risk_summary
from routers.weather import build_weather_history
from security import get_current_admin
from timeutil import day_bounds
from weather import sync_recent_weather

router = APIRouter(prefix="/api/owner-view", tags=["owner-view"])


def _get_token(session: Session) -> OwnerViewToken:
    token_row = session.exec(select(OwnerViewToken)).first()
    if not token_row:
        token_row = OwnerViewToken(token=secrets.token_urlsafe(24))
        session.add(token_row)
        session.commit()
        session.refresh(token_row)
    return token_row


def require_owner_token(token: str, session: Session) -> None:
    real = _get_token(session).token
    if not secrets.compare_digest(token or "", real):
        raise HTTPException(403, "Invalid or missing owner-view link")


@router.get("/link")
def get_owner_view_link(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Admin-only - returns the current token so Settings can display/copy
    the shareable link."""
    return {"token": _get_token(session).token}


@router.post("/regenerate")
def regenerate_owner_view_link(session: Session = Depends(get_session), admin=Depends(get_current_admin)):
    """Invalidates the old link (e.g. if it was shared with someone who
    shouldn't have access anymore) and issues a new one."""
    token_row = _get_token(session)
    token_row.token = secrets.token_urlsafe(24)
    session.add(token_row)
    session.commit()
    return {"token": token_row.token}


@router.get("/summary")
def owner_view_summary(token: str, period_start: date, period_end: date, supplier_id: Optional[int] = None,
                        session: Session = Depends(get_session)):
    """Token-gated (not admin-JWT-gated) equivalent of
    /api/dashboard/summary, minus the per-worker wage breakdown."""
    require_owner_token(token, session)

    start_dt, end_dt = day_bounds(period_start, period_end)
    worker_ids = _worker_ids_for_supplier(session, supplier_id)
    query = select(HarvestRecord).where(HarvestRecord.timestamp >= start_dt, HarvestRecord.timestamp <= end_dt)
    if worker_ids is not None:
        query = query.where(HarvestRecord.worker_id.in_(worker_ids))
    records = session.exec(query).all()

    active_teams = {r.team_id for r in records if r.team_id}
    active_workers = {r.worker_id for r in records if r.worker_id}
    active_blocks = {r.block_id for r in records if r.block_id}

    crate_counts: dict[str, int] = {}
    for r in records:
        if r.worker_id:
            crate_counts[r.worker_id] = crate_counts.get(r.worker_id, 0) + 1

    totals, _ = _worker_totals(session, period_start, period_end, supplier_id)
    workers_by_id = {w.id: w for w in session.exec(select(Worker)).all()}
    suppliers_by_id = {s.id: s for s in session.exec(select(Supplier)).all()}
    own_id = get_own_supplier_id(session)
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
    blocks_by_id = {b.id: b for b in session.exec(select(Block)).all()}
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


@router.get("/analysis")
def owner_view_analysis(token: str, session: Session = Depends(get_session)):
    """Token-gated equivalent of /api/analysis/summary - identical figures
    to the admin Analysis tab (nothing wage/payroll-related in there to
    redact), just reachable without an admin login."""
    require_owner_token(token, session)
    return build_analysis_summary(session)


@router.get("/weather")
def owner_view_weather(token: str, session: Session = Depends(get_session)):
    """Token-gated equivalent of /api/weather/history - identical figures
    to the admin Weather tab, reachable without an admin login."""
    require_owner_token(token, session)
    sync_recent_weather(session)
    return build_weather_history(session)


@router.get("/risk")
def owner_view_risk(token: str, session: Session = Depends(get_session)):
    """Token-gated equivalent of /api/risk/summary - identical figures to
    the admin Risk tab, reachable without an admin login."""
    require_owner_token(token, session)
    return build_risk_summary(session)


@router.get("/risk-forecast")
def owner_view_risk_forecast(token: str, session: Session = Depends(get_session)):
    """Token-gated equivalent of /api/risk/forecast - identical figures to
    the admin Harvest Forecast card, reachable without an admin login."""
    require_owner_token(token, session)
    return build_harvest_forecast(session)


# /api/lots/pending, /api/lots/in-transit, /api/lots/received, and
# /api/suppliers are already unauthenticated (Field/Pack House devices need
# them without logging in) - the Owner View frontend calls those directly
# rather than duplicating them here token-gated.
