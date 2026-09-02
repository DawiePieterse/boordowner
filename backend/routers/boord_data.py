"""Read-only Boord data the Owner frontend needs but that isn't Owner-specific:
the lot lists behind the Dashboard, the supplier dropdown, and the pack
house's system settings.

In Boord these endpoints are unauthenticated (field/pack-house tablets hit
them without logging in). Here they read Boord's database read-only and sit
behind the Owner login like everything else, so the frontend only ever talks
to this backend and none of this is readable without an account.

The lot-list bodies and their helpers are copied from
../Boord/backend/routers/lots.py, with the write paths (recompute_lot_totals,
upsert, split, external) left behind.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from db import get_boord_session
from models_boord import HarvestRecord, Lot, LotStatus, Supplier, SystemSetting
from security import get_current_user
from timeutil import day_bounds

router = APIRouter(tags=["boord-data"])


# --------------------------------------------------------------------------- #
# Lot lists  (from ../Boord/backend/routers/lots.py)
# --------------------------------------------------------------------------- #
def _urgency(age_minutes: float, settings: SystemSetting) -> str:
    if age_minutes >= settings.yellow_to_red_minutes:
        return "red"
    if age_minutes >= settings.green_to_yellow_minutes:
        return "yellow"
    return "green"


def _with_urgency(lot: Lot, settings: SystemSetting, suppliers: dict) -> dict:
    now = datetime.now(timezone.utc)
    ts = lot.timestamp if lot.timestamp.tzinfo else lot.timestamp.replace(tzinfo=timezone.utc)
    age_minutes = (now - ts).total_seconds() / 60
    supplier = suppliers.get(lot.supplier_id)
    return {
        **lot.model_dump(),
        "total_kg": round(lot.total_kg, 1),
        "age_minutes": round(age_minutes),
        "urgency": _urgency(age_minutes, settings),
        "supplier_name": supplier.name if supplier else "",
        "is_own_farm": supplier.is_own_farm if supplier else False,
    }


def _supplier_map(session: Session) -> dict:
    return {s.id: s for s in session.exec(select(Supplier)).all()}


def _build_split_index(session: Session):
    children = session.exec(select(Lot).where(Lot.split_from_slip_number != None)).all()  # noqa: E711
    parent_slips = {c.split_from_slip_number for c in children}
    parents = session.exec(select(Lot).where(Lot.slip_number.in_(parent_slips))).all() if parent_slips else []
    parents_by_slip = {p.slip_number: p for p in parents}
    children_by_parent_slip = defaultdict(list)
    for c in children:
        children_by_parent_slip[c.split_from_slip_number].append(c)
    return parents_by_slip, children_by_parent_slip


def _related_lots(session: Session, lot: Lot, parents_by_slip: dict, children_by_parent_slip: dict) -> list:
    related = []
    if lot.split_from_slip_number and lot.split_from_slip_number in parents_by_slip:
        related.append(parents_by_slip[lot.split_from_slip_number])
    related.extend(children_by_parent_slip.get(lot.slip_number, []))

    result = []
    for r in related:
        if r.status == LotStatus.created:
            crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == r.id)).all()
            total_crates = len(crates)
            total_kg = round(sum(c.weight_kg - c.deduction_kg for c in crates), 1)
        else:
            total_crates = r.total_crates
            total_kg = round(r.total_kg, 1)
        result.append({
            "slip_number": r.slip_number,
            "status": r.status,
            "total_crates": total_crates,
            "total_kg": total_kg,
            "received_at": r.received_at,
        })
    return result


@router.get("/api/lots/pending")
def list_pending(supplier_id: Optional[int] = None, period_start: Optional[date] = None,
                 period_end: Optional[date] = None, session: Session = Depends(get_boord_session),
                 user=Depends(get_current_user)):
    settings = session.exec(select(SystemSetting)).first() or SystemSetting()
    suppliers = _supplier_map(session)
    query = select(Lot).where(Lot.status == LotStatus.created)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    if period_start is not None and period_end is not None:
        start_dt, end_dt = day_bounds(period_start, period_end)
        query = query.where(Lot.timestamp >= start_dt, Lot.timestamp <= end_dt)
    lots = session.exec(query.order_by(Lot.timestamp.asc())).all()
    result = []
    for l in lots:
        crates = session.exec(select(HarvestRecord).where(HarvestRecord.lot_id == l.id)).all()
        if not crates:
            continue
        total_kg = sum(c.weight_kg - c.deduction_kg for c in crates)
        enriched = _with_urgency(l, settings, suppliers)
        enriched["total_crates"] = len(crates)
        enriched["total_kg"] = round(total_kg, 1)
        result.append(enriched)
    result.sort(key=lambda r: r["age_minutes"], reverse=True)
    return result


@router.get("/api/lots/in-transit")
def list_in_transit(supplier_id: Optional[int] = None, period_start: Optional[date] = None,
                    period_end: Optional[date] = None, session: Session = Depends(get_boord_session),
                    user=Depends(get_current_user)):
    settings = session.exec(select(SystemSetting)).first() or SystemSetting()
    suppliers = _supplier_map(session)
    query = select(Lot).where(Lot.status == LotStatus.in_transit)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    if period_start is not None and period_end is not None:
        start_dt, end_dt = day_bounds(period_start, period_end)
        query = query.where(Lot.timestamp >= start_dt, Lot.timestamp <= end_dt)
    lots = session.exec(query.order_by(Lot.timestamp.asc())).all()
    parents_by_slip, children_by_parent_slip = _build_split_index(session)
    enriched = []
    for l in lots:
        e = _with_urgency(l, settings, suppliers)
        e["related_lots"] = _related_lots(session, l, parents_by_slip, children_by_parent_slip)
        enriched.append(e)
    enriched.sort(key=lambda r: r["age_minutes"], reverse=True)
    return enriched


@router.get("/api/lots/received")
def list_received(period_start: Optional[date] = None, period_end: Optional[date] = None,
                  supplier_id: Optional[int] = None, session: Session = Depends(get_boord_session),
                  user=Depends(get_current_user)):
    settings = session.exec(select(SystemSetting)).first() or SystemSetting()
    suppliers = _supplier_map(session)
    query = select(Lot).where(Lot.received_at != None)  # noqa: E711
    if period_start is not None and period_end is not None:
        start_dt, end_dt = day_bounds(period_start, period_end)
        query = query.where(Lot.received_at >= start_dt, Lot.received_at <= end_dt)
    if supplier_id is not None:
        query = query.where(Lot.supplier_id == supplier_id)
    lots = session.exec(query.order_by(Lot.received_at.desc())).all()
    return [_with_urgency(l, settings, suppliers) for l in lots]


# --------------------------------------------------------------------------- #
# Suppliers + system settings
# --------------------------------------------------------------------------- #
@router.get("/api/suppliers")
def list_suppliers(session: Session = Depends(get_boord_session), user=Depends(get_current_user)):
    return session.exec(select(Supplier)).all()


@router.get("/api/system-settings")
def system_settings(session: Session = Depends(get_boord_session), user=Depends(get_current_user)):
    """The install-wide settings the frontend reads: packhouse_name (header),
    GPS (weather), season_start_month/day and current_harvest_year (the
    Season preset - see Boord.seasonYearFor in shared/api.js), and the
    urgency thresholds.

    Returned whole, so a column Boord adds reaches the frontend without a
    change here; models_boord.SystemSetting decides which ones exist."""
    return session.exec(select(SystemSetting)).first() or SystemSetting()
