from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["calm", "watch", "warning", "critical"]
Units = Literal["metric", "imperial"]


class Place(BaseModel):
    name: str
    admin1: str | None = None
    country: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Alert(BaseModel):
    id: str
    level: Literal["watch", "warning", "critical"]
    title: str
    detail: str


class Stats(BaseModel):
    mean: float | None = None
    sd: float | None = None
    z: float | None = None


class Analysis(BaseModel):
    alerts: list[Alert]
    score: int = Field(ge=0, le=100)
    level: Level
    idx: int
    condition: str
    stats: Stats


class AnomalyResponse(BaseModel):
    place: Place | None
    units: Units
    data: dict  # Open-Meteo payload: current, hourly, timezone, utc_offset_seconds
    analysis: Analysis


class BoardRow(BaseModel):
    place: Place
    current: dict
    analysis: Analysis


class Health(BaseModel):
    status: str
    version: str
    cache_entries: int
    cache_hits: int
    cache_misses: int
