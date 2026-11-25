import pytest

from seatfinder.catalog import Catalog
from seatfinder.config import Config
from seatfinder.errors import UsageError
from seatfinder.models import (
    AVAILABLE,
    City,
    Seat,
    SeatArea,
    SeatMap,
    SeatRow,
    Show,
)
from seatfinder.service import SeatService, ShowBundle


def test_platform_validation():
    with pytest.raises(UsageError):
        SeatService(Config(), platform="nope")


def test_earliest_uses_release_date_when_in_the_future(monkeypatch):
    monkeypatch.setattr("seatfinder.service.today_ist", lambda: "2026-11-25")
    service = SeatService(Config(), platform="district")
    catalog = Catalog()
    city = catalog.resolve_city("delhi")
    movie = catalog.resolve_movie("doomsday")
    target, earliest = service._target_date(city, movie, None)
    assert target == "2026-12-17"
    assert earliest is True


def test_fetch_seats_validates_minimum_before_network():
    service = SeatService(Config(), platform="district")
    with pytest.raises(UsageError):
        service.fetch_seats(None, min_seats=0)  # type: ignore[arg-type]


def _show(platform, show_id, starts_at):
    return Show(
        platform=platform,
        show_id=show_id,
        movie_title="Test",
        city="Delhi",
        venue_id=show_id,
        venue_name=show_id,
        screen="",
        starts_at=starts_at,
    )


def test_both_platform_limit_includes_both_providers():
    service = SeatService(Config(), platform="both")
    shows = [
        _show("bms", f"bms-{index}", f"2026-12-17T{index:02d}:00+05:30")
        for index in range(10)
    ] + [
        _show("district", f"district-{index}", f"2026-12-17T{index:02d}:00+05:30")
        for index in range(10)
    ]
    selected = service._select_shows(shows, 4)
    assert {show.platform for show in selected} == {"bms", "district"}
    assert len(selected) == 4


def test_unknown_city_falls_back_to_bms_regions(monkeypatch):
    service = SeatService(Config(), platform="both")
    monkeypatch.setattr(
        service.providers["bms"],
        "regions",
        lambda: [
            {"name": "Kota", "slug": "kota", "code": "KOTA", "lat": 25.1, "lng": 75.8}
        ],
    )
    city, _ = service.resolve("Kota", None)
    assert city.name == "Kota"
    assert city.bms_region == "KOTA"
    assert city.lat == 25.1


def test_seat_progress_callback_reports_each_show():
    service = SeatService(Config(), platform="district")
    city = City(
        name="Delhi",
        bms_slug="delhi",
        district_city_key="new-delhi",
        lat=28.6,
        lng=77.2,
    )
    movie = Catalog().resolve_movie("doomsday")
    show = _show("district", "district-1", "2026-12-17T21:00+05:30")
    seat_map = SeatMap(
        platform="district",
        show_id=show.show_id,
        fetched_at="2026-01-01T00:00:00+05:30",
        areas=[SeatArea(code="A", name="PREMIUM", price=500)],
        rows=[
            SeatRow(
                label="A",
                order=1,
                seats=[
                    Seat(
                        row="A",
                        number="1",
                        grid_position=1,
                        status=AVAILABLE,
                        area_code="A",
                        area_name="PREMIUM",
                        price=500,
                    )
                ],
            )
        ],
    )
    service.providers["district"].fetch_seat_map = lambda *args: seat_map
    calls = []
    bundle = ShowBundle(city=city, movie=movie, date="2026-12-17", shows=[show])
    results = service.fetch_seats(
        bundle,
        on_progress=lambda index, total, current: calls.append(
            (index, total, current.show_id)
        ),
        prefer="center",
    )
    assert calls == [(1, 1, show.show_id)]
    assert results[0]["block_found"] is True
