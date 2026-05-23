"""
OpenWEC API — Response Schemas
Pydantic models for all API responses.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


# ── Series & Navigation ───────────────────────────────────────

class SeriesOut(BaseModel):
    id:   int
    key:  str
    name: str


class SeasonOut(BaseModel):
    id:     int
    raw_id: str
    year:   int
    label:  str


class EventOut(BaseModel):
    id:        int
    raw_id:    str
    name:      str
    round:     Optional[int]


class SessionOut(BaseModel):
    id:           int
    raw_id:       str
    name:         str
    session_type: str
    session_at:   Optional[str]
    imsa_series:  Optional[str]
    snapshot_hour: Optional[int]


# ── Results ───────────────────────────────────────────────────

class DriverSlot(BaseModel):
    slot:       int
    first_name: str
    last_name:  str
    country:    Optional[str]
    imsa_rating: Optional[str]


class ResultOut(BaseModel):
    position:        Optional[int]
    car_number:      str
    car_class:       Optional[str]
    vehicle:         Optional[str]
    team:            Optional[str]
    status:          str
    laps_completed:  Optional[int]
    total_time_s:    Optional[float]
    gap_to_first_s:  Optional[float]
    fl_lap_number:   Optional[int]
    fl_time_s:       Optional[float]
    fl_kph:          Optional[float]
    drivers:         list[DriverSlot] = []


# ── Laps ─────────────────────────────────────────────────────

class LapOut(BaseModel):
    car_number:   str
    driver_name:  Optional[str]
    lap_number:   int
    lap_time_s:   Optional[float]
    s1_s:         Optional[float]
    s2_s:         Optional[float]
    s3_s:         Optional[float]
    kph:          Optional[float]
    top_speed_kph: Optional[float]
    lap_improvement:           bool
    crossing_finish_in_pit:    bool
    flag_at_fl:   Optional[str]
    pit_time_s:   Optional[float]
    elapsed_raw:  Optional[str]
    hour_raw:     Optional[str]


# ── Pagination ────────────────────────────────────────────────

class PaginatedLaps(BaseModel):
    session_id: int
    car_number: Optional[str]
    total:      int
    page:       int
    page_size:  int
    results:    list[LapOut]