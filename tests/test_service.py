import pytest

from seatfinder.catalog import Catalog
from seatfinder.config import Config
from seatfinder.errors import UsageError
from seatfinder.service import SeatService


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
