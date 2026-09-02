"""Read-only projection of Boord's schema.

These classes are how this app reads Boord's live database (data/boord.db).
The authoritative definitions live in ../Boord/backend/models.py - this is a
deliberately partial copy: only the columns this app actually reads, so that
schema drift review has one short list to check.

RULES:
  * Never create these tables (init_owner_db() passes an explicit table list
    that excludes them).
  * Never write through them - the Boord engine is opened read-only
    (db.boord_engine), and Boord is the only writer of that file.
  * When Boord renames or drops a column below, this app breaks loudly at
    startup via main._assert_boord_schema() rather than 500-ing mid-harvest.

Columns depended on, by table:
  block            : id, name, variety, trees, hectares, active, supplier_id
  worker           : id, name, supplier_id, active
  supplier         : id, name, is_own_farm, active
  systemsetting    : id, packhouse_name, packhouse_location, packhouse_code,
                     current_harvest_year, season_start_month,
                     season_start_day, gps_lat, gps_lon,
                     green_to_yellow_minutes, yellow_to_red_minutes
  lot              : id, slip_number, timestamp, device_id, team_id,
                     supplier_id, driver, total_crates, total_kg, status,
                     notes, received_at, weather_temp, weather_humidity,
                     weather_condition, split_from_slip_number
  harvestrecord    : uuid, timestamp, worker_id, block_id, weight_kg,
                     deduction_kg, team_id, lot_id
"""
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class LotStatus(str, Enum):
    created = "created"
    in_transit = "in_transit"
    received = "received"
    processing_complete = "processing_complete"


class Block(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str = ""
    variety: str = ""
    trees: int = 0
    hectares: float = 0.0
    active: bool = True
    # Whose orchard this block is. One Boord install is a pack house that
    # several growers deliver into (Boord v3.1, commit 477ab72), so a block
    # is no longer automatically this farm's. NULL means unallocated, which
    # Boord's own Field screen treats as available to everyone - see
    # db.own_farm_block_ids().
    supplier_id: Optional[int] = None


class Worker(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str = ""
    supplier_id: Optional[int] = None
    active: bool = True


class Supplier(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    is_own_farm: bool = False
    active: bool = True


class SystemSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    packhouse_name: str = ""
    packhouse_location: str = ""
    packhouse_code: str = ""      # PHC - the pack house's registered code
    green_to_yellow_minutes: int = 90
    yellow_to_red_minutes: int = 150
    # The season is a recurring anchor (month + day), not a calendar year: a
    # litchi season crosses the new year. Boord derives which season is
    # current and labels it by the year it starts in, keeping
    # current_harvest_year as that derived label. This app reads the anchor
    # itself - see timeutil.season_year_for / season_day - so the two never
    # disagree about which season a date belongs to.
    season_start_month: int = 1
    season_start_day: int = 1
    current_harvest_year: int = 0
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None


class Lot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    slip_number: str = Field(unique=True)
    timestamp: datetime
    device_id: Optional[str] = None
    team_id: Optional[str] = None
    supplier_id: Optional[int] = None
    driver: str = ""
    total_crates: int = 0
    total_kg: float = 0.0
    status: LotStatus = LotStatus.created
    notes: str = ""
    received_at: Optional[datetime] = None
    weather_temp: Optional[float] = None
    weather_humidity: Optional[float] = None
    weather_condition: str = ""
    split_from_slip_number: Optional[str] = None


class HarvestRecord(SQLModel, table=True):
    uuid: str = Field(primary_key=True)
    timestamp: datetime
    worker_id: Optional[str] = None
    block_id: Optional[str] = None
    weight_kg: float = 0.0
    deduction_kg: float = 0.0
    team_id: Optional[str] = None
    lot_id: Optional[int] = None
