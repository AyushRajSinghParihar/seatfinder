import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from seatfinder.config import DEFAULT_BMS_SEAT_KEY, Config
from seatfinder.models import City, MovieRef, Show
from seatfinder.providers.bms import (
    BookMyShowProvider,
    _extract_shows_from_state,
    _movie_slug_from_url,
    decode_bms_seat_layout,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _city():
    return City(
        name="Delhi",
        bms_slug="delhi",
        district_city_key="new-delhi",
        lat=28.6,
        lng=77.2,
        bms_region="NCR",
    )


def _movie():
    return MovieRef(
        title="Avengers: Doomsday",
        bms_event_code="ET00000001",
        bms_slug="avengers-doomsday",
    )


def _seat_url(*args, **kwargs):
    return "https://example.test/seat"


def test_movie_slug_is_read_from_city_first_urls():
    assert (
        _movie_slug_from_url(
            "https://in.bookmyshow.com/delhi/movies/avengers-doomsday/ET00439706"
        )
        == "avengers-doomsday"
    )
    assert (
        _movie_slug_from_url(
            "https://in.bookmyshow.com/movies/delhi/avengers-doomsday/ET00439706"
        )
        == "avengers-doomsday"
    )


def test_extracts_new_style_showtime_state():
    state = json.loads((FIXTURES / "bms_new_state.json").read_text())
    shows, dates = _extract_shows_from_state(
        state,
        date_code="20261217",
        city=_city(),
        movie=_movie(),
        event_code="ET00526287",
        format_hint="",
        language_hint="",
        include_previews=False,
        seat_url=_seat_url,
    )
    assert dates == {"20261217", "20261218"}
    assert len(shows) == 1
    show = shows[0]
    assert show.show_id == "bms|TEST|123|20261217"
    assert show.starts_at == "2026-12-17T21:00+05:30"
    assert show.language == "English"
    assert show.format.startswith("English")
    assert show.price_min == 500
    assert show.price_max == 900
    assert show.status == "available"
    assert show.area_counts[0]["label"] == "PREMIUM"


def test_extracts_old_style_showtime_state():
    state = json.loads((FIXTURES / "bms_old_state.json").read_text())
    shows, dates = _extract_shows_from_state(
        state,
        date_code="20261217",
        city=_city(),
        movie=_movie(),
        event_code="ET00000001",
        format_hint="",
        language_hint="",
        include_previews=False,
        seat_url=_seat_url,
    )
    assert dates == {"20261217"}
    assert len(shows) == 1
    assert shows[0].venue_name == "Old Cinema"
    assert shows[0].show_id == "bms|OLD|999|20261217"
    assert shows[0].area_counts[0]["label"] == "GOLD"


def test_decodes_and_parses_encrypted_layout():
    plaintext = (
        "PREMIUM:A:0000000001:1:N:0|RECLINER:B:0000000002:2:N:0||"
        "1:N:A4001+1:B1002+2:B0000+0:A1004+4|"
        "2:M:B1001+1:B1002+2"
    )
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(DEFAULT_BMS_SEAT_KEY), modes.CBC(bytes(16))
    ).encryptor()
    encoded = base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()
    assert decode_bms_seat_layout(encoded, DEFAULT_BMS_SEAT_KEY) == plaintext

    config = Config()
    provider = BookMyShowProvider(config)
    show = Show(
        platform="bms",
        show_id="bms|TEST|1|20261217",
        movie_title="Test",
        city="Delhi",
        venue_id="TEST",
        venue_name="Test Cinema",
        screen="",
        starts_at="2026-12-17T21:00+05:30",
        area_counts=[
            {"code": "0000000001", "label": "PREMIUM", "price": 500},
            {"code": "0000000002", "label": "RECLINER", "price": 900},
        ],
    )
    seat_map = provider._parse_layout(show, plaintext)
    assert [row.label for row in seat_map.rows] == ["N", "M"]
    assert [seat.number for seat in seat_map.rows[0].seats] == ["1", "2", "4"]
    assert seat_map.rows[0].seats[0].is_best is True
    assert seat_map.rows[0].seats[0].area_name == "PREMIUM"
    assert seat_map.rows[0].seats[0].price == 500
    assert seat_map.summary()["available"] == 5
