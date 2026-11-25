"""Small, stable data model shared by both providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

AVAILABLE = "available"
ACCESSIBLE = "accessible"
BOOKED = "booked"
HELD = "held"
UNKNOWN = "unknown"


@dataclass(slots=True)
class City:
    name: str
    bms_slug: str
    district_city_key: str
    lat: float
    lng: float
    bms_region: str | None = None
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bms_slug": self.bms_slug,
            "district_city_key": self.district_city_key,
            "lat": self.lat,
            "lng": self.lng,
            "bms_region": self.bms_region,
            "aliases": list(self.aliases),
        }


@dataclass(slots=True)
class MovieRef:
    title: str
    aliases: list[str] = field(default_factory=list)
    default: bool = False
    release_date: str | None = None
    bms_event_code: str | None = None
    bms_slug: str | None = None
    district_content_id: str | None = None
    district_slug: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "aliases": list(self.aliases),
            "default": self.default,
            "release_date": self.release_date,
            "bms": {"event_code": self.bms_event_code, "slug": self.bms_slug},
            "district": {
                "content_id": self.district_content_id,
                "slug": self.district_slug,
            },
        }


@dataclass(slots=True)
class Warning:
    code: str
    message: str
    platform: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.platform:
            payload["platform"] = self.platform
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(slots=True)
class Seat:
    row: str
    number: str
    grid_position: int
    status: str
    area_code: str
    area_name: str = ""
    price: float | None = None
    seat_type: str = ""
    is_best: bool = False

    @property
    def label(self) -> str:
        if self.number.casefold().startswith(self.row.casefold()):
            return self.number
        return f"{self.row}{self.number}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "number": self.number,
            "label": self.label,
            "grid_position": self.grid_position,
            "status": self.status,
            "area_code": self.area_code,
            "area_name": self.area_name,
            "price": self.price,
            "seat_type": self.seat_type,
            "is_best": self.is_best,
        }


@dataclass(slots=True)
class SeatRow:
    label: str
    order: int
    seats: list[Seat] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "order": self.order,
            "seats": [seat.to_dict() for seat in self.seats],
        }


@dataclass(slots=True)
class SeatArea:
    code: str
    name: str
    price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "price": self.price}


@dataclass(slots=True)
class SeatMap:
    platform: str
    show_id: str
    fetched_at: str
    areas: list[SeatArea] = field(default_factory=list)
    rows: list[SeatRow] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)

    def all_seats(self) -> list[Seat]:
        return [seat for row in self.rows for seat in row.seats]

    def summary(self) -> dict[str, Any]:
        seats = self.all_seats()
        total = len(seats)
        counts: dict[str, int] = {}
        for seat in seats:
            counts[seat.status] = counts.get(seat.status, 0) + 1
        available = counts.get(AVAILABLE, 0) + counts.get(ACCESSIBLE, 0)
        booked = counts.get(BOOKED, 0) + counts.get(HELD, 0)
        return {
            "total": total,
            "available": available,
            "booked": booked,
            "occupancy_pct": round((booked / total) * 100, 2) if total else 0.0,
            "by_status": counts,
        }

    def to_dict(self, *, include_rows: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "platform": self.platform,
            "show_id": self.show_id,
            "fetched_at": self.fetched_at,
            "areas": [area.to_dict() for area in self.areas],
            "summary": self.summary(),
            "warnings": [w.to_dict() for w in self.warnings],
        }
        if include_rows:
            payload["rows"] = [row.to_dict() for row in self.rows]
        return payload


@dataclass(slots=True)
class SeatBlock:
    row: str
    seats: list[str]
    area_code: str
    area_name: str
    score: float
    price: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "seats": list(self.seats),
            "area_code": self.area_code,
            "area_name": self.area_name,
            "score": round(self.score, 4),
            "price": self.price,
        }


@dataclass(slots=True)
class Show:
    platform: str
    show_id: str
    movie_title: str
    city: str
    venue_id: str
    venue_name: str
    screen: str
    starts_at: str
    format: str = ""
    language: str = ""
    price_min: float | None = None
    price_max: float | None = None
    total: int | None = None
    available: int | None = None
    status: str = UNKNOWN
    booking_url: str | None = None
    area_counts: list[dict[str, Any]] = field(default_factory=list)
    refs: dict[str, Any] = field(default_factory=dict)
    warnings: list[Warning] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "platform": self.platform,
            "show_id": self.show_id,
            "movie": self.movie_title,
            "city": self.city,
            "venue": {
                "id": self.venue_id,
                "name": self.venue_name,
                "screen": self.screen,
            },
            "starts_at": self.starts_at,
            "format": self.format,
            "language": self.language,
            "price": {
                "min": self.price_min,
                "max": self.price_max,
                "currency": "INR",
            },
            "availability": {
                "status": self.status,
                "total": self.total,
                "available": self.available,
            },
            "booking_url": self.booking_url,
            "area_counts": self.area_counts,
            "warnings": [w.to_dict() for w in self.warnings],
        }
        if self.refs:
            payload["refs"] = self.refs
        return payload


@dataclass(slots=True)
class ProviderResult:
    shows: list[Show] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    show_dates: list[str] = field(default_factory=list)
