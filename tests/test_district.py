from pathlib import Path

from seatfinder.cache import JsonCache
from seatfinder.config import Config
from seatfinder.http import HttpResponse
from seatfinder.models import City, MovieRef, Show
from seatfinder.providers.district import DistrictProvider, _release_date, _seat_status

FIXTURES = Path(__file__).parent / "fixtures"


def _provider(tmp_path):
    config = Config(cache_dir=tmp_path)
    return DistrictProvider(config, JsonCache(config, enabled=False))


def _city():
    return City(
        name="Delhi",
        bms_slug="delhi",
        district_city_key="new-delhi",
        lat=28.6,
        lng=77.2,
    )


def _movie():
    return MovieRef(
        title="Avengers: Doomsday",
        district_content_id="176735",
        district_slug="avengers-doomsday",
    )


def test_release_date_accepts_epoch_and_iso():
    assert _release_date("1797465600") == "2026-12-17"
    assert _release_date("2026-12-17") == "2026-12-17"


def test_seat_status_mapping():
    assert _seat_status("0") == "available"
    assert _seat_status("1") == "booked"
    assert _seat_status("1000") == "accessible"
    assert _seat_status("9999") == "unknown"


def test_parses_movie_response(monkeypatch, tmp_path):
    payload = (FIXTURES / "district_movie.json").read_text()
    provider = _provider(tmp_path)

    def fake_get(*args, **kwargs):
        return HttpResponse(status_code=200, text=payload, headers={}, url="https://example.test")

    monkeypatch.setattr(provider.client, "get", fake_get)
    result = provider.fetch_shows(_city(), _movie(), "2026-12-17")
    assert result.show_dates == ["2026-12-17", "2026-12-18"]
    assert len(result.shows) == 1
    show = result.shows[0]
    assert show.show_id == "district|1|123|2026-12-17"
    assert show.starts_at == "2026-12-17T21:00+05:30"
    assert show.language == "English"
    assert show.available == 10
    assert show.area_counts[0]["label"] == "PREMIUM"
    assert show.booking_url.endswith("MV176735?fromdate=2026-12-17")


def test_204_is_reported_as_not_open(monkeypatch, tmp_path):
    provider = _provider(tmp_path)

    def fake_get(*args, **kwargs):
        return HttpResponse(status_code=204, text="", headers={}, url="https://example.test")

    monkeypatch.setattr(provider.client, "get", fake_get)
    result = provider.fetch_shows(_city(), _movie(), "2026-12-17")
    assert result.shows == []
    assert result.warnings[0].code == "DATE_NOT_OPEN"


def test_parses_seat_layout_and_accessible_status(monkeypatch, tmp_path):
    payload = (FIXTURES / "district_seat.json").read_text()
    provider = _provider(tmp_path)

    def fake_get(*args, **kwargs):
        return HttpResponse(status_code=200, text="<html></html>", headers={}, url="https://example.test")

    def fake_post(*args, **kwargs):
        return HttpResponse(status_code=200, text=payload, headers={}, url="https://example.test")

    monkeypatch.setattr(provider.client, "get", fake_get)
    monkeypatch.setattr(provider.client, "post_json", fake_post)
    show = Show(
        platform="district",
        show_id="district|1|123|2026-12-17",
        movie_title="Test",
        city="Delhi",
        venue_id="1",
        venue_name="Test Cinema",
        screen="AUDI 1",
        starts_at="2026-12-17T21:00+05:30",
        format="2D",
        area_counts=[{"code": "A", "label": "PREMIUM", "price": 500}],
        refs={
            "cinema_id": "1",
            "session_id": "123",
            "provider_id": "335",
            "screen_format": "2D",
            "movie_code": "MOV123",
            "content_id": "176735",
            "date": "2026-12-17",
        },
    )
    seat_map = provider.fetch_seat_map(_city(), _movie(), show)
    assert [seat.number for seat in seat_map.rows[0].seats] == ["1", "2", "3", "4", "6"]
    statuses = [seat.status for seat in seat_map.rows[0].seats]
    assert statuses == ["available", "booked", "available", "accessible", "available"]
    assert seat_map.summary()["available"] == 4
