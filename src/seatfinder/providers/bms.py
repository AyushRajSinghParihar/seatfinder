"""BookMyShow read-only provider.

BookMyShow sits behind Cloudflare. The page requests use a Safari/Firefox TLS
fingerprint via curl_cffi. Showtimes are server-rendered into
`window.__INITIAL_STATE__`; seat maps come from doTrans.aspx as an AES-CBC blob.
"""

from __future__ import annotations

import base64
import json
import random
import re
import time
from datetime import datetime, timezone, timedelta
from html import unescape
from typing import Any, Iterable
from urllib.parse import urljoin

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..cache import JsonCache
from ..config import Config
from ..errors import BlockedError, SchemaError, UpstreamError
from ..http import HttpClient, multipart_fields, new_request_id
from ..jsonutil import now_iso
from ..models import (
    AVAILABLE,
    BOOKED,
    UNKNOWN,
    MovieRef,
    ProviderResult,
    Seat,
    SeatArea,
    SeatMap,
    SeatRow,
    Show,
    Warning,
    City,
)

IST = timezone(timedelta(hours=5, minutes=30))
BMS_BASE = "https://in.bookmyshow.com"
BMS_SEAT_ENDPOINT = "https://services-in.bookmyshow.com/doTrans.aspx"
STATUS_MAP = {
    3: "available",
    2: "filling_fast",
    1: "almost_full",
    0: "sold_out",
}
SEAT_STATUS_MAP = {
    1: AVAILABLE,
    4: AVAILABLE,
    2: BOOKED,
}


class BookMyShowProvider:
    name = "bms"

    def __init__(self, config: Config, cache: JsonCache | None = None) -> None:
        self.config = config
        self.cache = cache or JsonCache(config)
        self.client = HttpClient(
            profiles=config.bms_tls_profiles,
            timeout=config.timeout,
            proxy=config.proxy,
        )
        self._page_cache: dict[str, Any] = {}

    # ---------- movie resolution ----------

    def search_movies(self, city: City, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        url = f"{BMS_BASE}/explore/movies-{city.bms_slug}"
        html = self._get_page(url)
        wanted = _normalize(query)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
            try:
                payload = json.loads(script)
            except (json.JSONDecodeError, TypeError):
                continue
            for item in _walk_items(payload):
                name = str(item.get("name") or "").strip()
                movie_url = str(item.get("url") or "").strip()
                if not name or "ET" not in movie_url or "/movies/" not in movie_url:
                    continue
                if wanted and wanted not in _normalize(name):
                    continue
                match = re.search(r"(ET\d{6,})", movie_url)
                if not match or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                slug = _movie_slug_from_url(movie_url) or _slugify(name)
                results.append(
                    {
                        "platform": self.name,
                        "title": name,
                        "event_code": match.group(1),
                        "slug": slug,
                        "url": urljoin(BMS_BASE, movie_url),
                    }
                )
                if len(results) >= limit:
                    return results
        return results

    def resolve_movie(self, city: City, movie: MovieRef) -> MovieRef:
        if movie.bms_event_code and movie.bms_slug:
            return movie
        results = self.search_movies(city, movie.title)
        if not results:
            raise SchemaError(
                f"Could not find {movie.title!r} on BookMyShow",
                hint="Pass --bms-event-code and --bms-slug, or use a known movie title",
            )
        match = results[0]
        movie.bms_event_code = match["event_code"]
        movie.bms_slug = match["slug"]
        if not movie.title:
            movie.title = match["title"]
        return movie

    # ---------- cities ----------

    def regions(self) -> list[dict[str, Any]]:
        cached = self.cache.get("bms:regions")
        if cached:
            return cached
        response = self.client.get(
            f"{BMS_BASE}/api/explore/v1/discover/regions",
            max_attempts=3,
            rotate_on=(403, 429),
            backoff=2.0,
        )
        payload = response.json()
        regions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in _walk_region_dicts(payload):
            code = str(item.get("RegionCode") or "")
            name = str(item.get("RegionName") or item.get("RegionNameDisplay") or "")
            if not code or not name or code in seen:
                continue
            seen.add(code)
            regions.append(
                {
                    "code": code,
                    "name": name,
                    "slug": str(item.get("RegionSlug") or item.get("Slug") or _slugify(name)),
                    "lat": _num(item.get("Latitude") or item.get("Lat")),
                    "lng": _num(item.get("Longitude") or item.get("Long")),
                }
            )
        regions.sort(key=lambda r: r["name"])
        self.cache.set("bms:regions", regions)
        return regions

    # ---------- showtimes ----------

    def event_code_refs(self, city: City, movie: MovieRef, *, refresh: bool = False) -> list[dict[str, Any]]:
        movie = self.resolve_movie(city, movie)
        key = f"bms:eventcodes:{city.bms_slug}:{movie.bms_event_code}"
        if not refresh:
            cached = self.cache.get(key)
            if cached:
                return cached
        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            url = f"{BMS_BASE}/movies/{city.bms_slug}/{movie.bms_slug}/{movie.bms_event_code}"
            state = _extract_initial_state(self._get_page(url))
            for query in (state.get("synopsisMoviesApi") or {}).get("queries", {}).values():
                data = query.get("data") or {}
                if not isinstance(data, dict):
                    continue
                for fmt in _walk_format_dicts(data):
                    code = str(fmt.get("eventCode") or "")
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    refs.append(
                        {
                            "code": code,
                            "language": str(fmt.get("language") or ""),
                            "dimension": str(fmt.get("dimension") or ""),
                        }
                    )
        except SeatfinderError:
            pass
        if movie.bms_event_code and movie.bms_event_code not in seen:
            refs.insert(0, {"code": movie.bms_event_code, "language": "", "dimension": ""})
            seen.add(movie.bms_event_code)
        if not refs and movie.bms_event_code:
            refs = [{"code": movie.bms_event_code, "language": "", "dimension": ""}]
        if refs:
            self.cache.set(key, refs)
        return refs

    def fetch_shows(
        self,
        city: City,
        movie: MovieRef,
        date: str,
        *,
        allow_empty: bool = False,
        include_previews: bool = False,
    ) -> ProviderResult:
        movie = self.resolve_movie(city, movie)
        date_code = date.replace("-", "")
        refs = self.event_code_refs(city, movie)
        shows: list[Show] = []
        warnings: list[Warning] = []
        show_dates: set[str] = set()
        seen_shows: set[str] = set()
        errors: list[SeatfinderError] = []

        for index, ref in enumerate(refs):
            code = ref["code"]
            if index:
                time.sleep(random.uniform(0.7, 1.2))
            url = (
                f"{BMS_BASE}/movies/{city.bms_slug}/{movie.bms_slug}"
                f"/buytickets/{code}/{date_code}"
            )
            try:
                state = _extract_initial_state(self._get_page(url))
            except SeatfinderError as exc:
                errors.append(exc)
                warnings.append(
                    Warning(
                        code=exc.code,
                        message=exc.message,
                        platform=self.name,
                        detail=f"{code}: {exc.detail or exc.hint or ''}".strip(),
                    )
                )
                continue

            extracted, dates = _extract_shows_from_state(
                state,
                date_code=date_code,
                city=city,
                movie=movie,
                event_code=code,
                format_hint=ref.get("dimension") or "",
                language_hint=ref.get("language") or "",
                include_previews=include_previews,
                seat_url=self._seat_url,
            )
            show_dates.update(dates)
            for show in extracted:
                if show.show_id in seen_shows:
                    continue
                seen_shows.add(show.show_id)
                shows.append(show)

        if not shows and errors and len(errors) == len(refs):
            raise errors[0]
        if not shows and not allow_empty:
            warnings.append(
                Warning(
                    code="DATE_NOT_OPEN",
                    message=f"BookMyShow is not selling {date} for this movie",
                    platform=self.name,
                    detail=(
                        "available_dates=" + ",".join(sorted(show_dates))
                        if show_dates
                        else "no event code returned showtimes"
                    ),
                )
            )
        if not show_dates:
            show_dates.update(show.starts_at[:10] for show in shows)
        shows.sort(key=lambda show: (show.starts_at, show.venue_name, show.show_id))
        return ProviderResult(
            shows=shows,
            warnings=warnings,
            show_dates=sorted(_iso_date_code(code) for code in show_dates),
        )

    # ---------- seat maps ----------

    def fetch_seat_map(self, city: City, movie: MovieRef, show: Show) -> SeatMap:
        movie = self.resolve_movie(city, movie)
        date_code = str(show.refs.get("date") or show.starts_at[:10].replace("-", ""))
        event_code = str(show.refs.get("event_code") or movie.bms_event_code)
        url = (
            f"{BMS_BASE}/movies/{city.bms_slug}/{movie.bms_slug}"
            f"/buytickets/{event_code}/{date_code}"
        )
        if url not in self._page_cache:
            self._page_cache[url] = self._get_page(url)
        fields = {
            "strAppCode": "WEB",
            "lngTransactionIdentifier": "0",
            "strCommand": "GETSEATLAYOUT",
            "strVenueCode": str(show.refs.get("venue_code") or show.venue_id),
            "strParam1": str(show.refs.get("session_id") or ""),
            "strParam2": "WEB",
            "strParam3": "",
            "strParam4": "",
            "strParam5": str(show.refs.get("show_seat_number") or "Y"),
            "strParam6": "N",
            "strParam7": "N",
            "strParam8": "",
            "strParam9": "",
            "strParam10": "",
            "strFormat": "json",
        }
        body, content_type = multipart_fields(fields)
        response = self.client.request(
            "POST",
            BMS_SEAT_ENDPOINT,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": content_type,
                "Origin": BMS_BASE,
                "Referer": f"{BMS_BASE}/",
                "x-app-code": "WEB",
                "x-bms-id": f"1.{random.randrange(1, 1_000_000_000)}.{random.randrange(1, 10**13)}",
            },
            data=body,
            max_attempts=2,
            rotate_on=(403, 429),
            backoff=2.0,
        )
        try:
            payload = response.json()
        except UpstreamError as exc:
            raise SchemaError("BookMyShow seat response was not JSON", detail=str(exc)) from exc
        envelope = payload.get("BookMyShow") or payload.get("bookmyshow") or {}
        if str(envelope.get("blnSuccess")).lower() != "true":
            raise SchemaError(
                "BookMyShow rejected the seat layout request",
                detail=str(envelope.get("strException") or payload)[:300],
            )
        encoded = envelope.get("strData")
        if not isinstance(encoded, str) or not encoded:
            raise SchemaError("BookMyShow seat response had no strData")
        layout = decode_bms_seat_layout(encoded, self.config.bms_seat_key)
        return self._parse_layout(show, layout)

    def _parse_layout(self, show: Show, layout: str) -> SeatMap:
        try:
            area_section, row_section = layout.split("||", 1)
        except ValueError as exc:
            raise SchemaError("BookMyShow seat layout has an unknown shape", detail=layout[:200]) from exc

        areas: dict[str, SeatArea] = {}
        for raw_area in filter(None, area_section.split("|")):
            fields = raw_area.split(":")
            if len(fields) < 4:
                raise SchemaError("BookMyShow seat area is malformed", detail=raw_area)
            name, area_id, area_code, _number = fields[:4]
            areas[area_id] = SeatArea(code=area_code, name=name, price=None)

        show_price_by_code = {
            str(item.get("code") or ""): item.get("price")
            for item in show.area_counts
            if item.get("code")
        }
        show_name_by_code = {
            str(item.get("code") or ""): item.get("label")
            for item in show.area_counts
            if item.get("code")
        }
        for area in areas.values():
            if area.code in show_price_by_code and show_price_by_code[area.code] is not None:
                area.price = _num(show_price_by_code[area.code])
            if area.code in show_name_by_code and show_name_by_code[area.code]:
                area.name = str(show_name_by_code[area.code])
                continue
            matched = _match_show_area(area.name, show.area_counts)
            if matched:
                area.name = str(matched.get("label") or area.name)
                if matched.get("price") is not None:
                    area.price = _num(matched.get("price"))

        rows: list[SeatRow] = []
        for row_index, raw_row in enumerate(filter(None, row_section.split("|"))):
            fields = raw_row.split(":")
            if len(fields) < 2:
                raise SchemaError("BookMyShow seat row is malformed", detail=raw_row)
            row_id, row_label, *raw_seats = fields
            seats: list[Seat] = []
            for position, raw_seat in enumerate(raw_seats):
                parts = raw_seat.split("+")
                token = parts[0]
                if len(token) < 2 or not token[1].isdigit():
                    raise SchemaError("BookMyShow seat token is malformed", detail=raw_seat)
                area_id = token[0]
                status_code = int(token[1])
                if status_code == 0:
                    continue
                number = parts[1] if len(parts) > 1 and parts[1] not in {"0", "00"} else token[2:]
                number = number.lstrip("0") or token[2:].lstrip("0")
                area = areas.get(area_id)
                seats.append(
                    Seat(
                        row=row_label,
                        number=number,
                        grid_position=position,
                        status=SEAT_STATUS_MAP.get(status_code, UNKNOWN),
                        area_code=area.code if area else "",
                        area_name=area.name if area else "",
                        price=area.price if area else None,
                        is_best=status_code == 4,
                    )
                )
            if seats:
                try:
                    order = int(row_id)
                except ValueError:
                    order = row_index
                rows.append(SeatRow(label=row_label, order=order, seats=seats))

        if not rows:
            raise SchemaError(
                "BookMyShow seat layout contained no seats",
                hint="The show may be closed, or BookMyShow changed its seat format",
            )
        rows.sort(key=lambda row: row.order)
        return SeatMap(
            platform=self.name,
            show_id=show.show_id,
            fetched_at=now_iso(),
            areas=list(areas.values()),
            rows=rows,
        )

    # ---------- helpers ----------

    def _get_page(self, url: str) -> str:
        response = self.client.get(
            url,
            headers={"Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-IN,en;q=0.9"},
            max_attempts=4,
            rotate_on=(403, 429),
            backoff=2.0,
        )
        if response.status_code == 403:
            raise BlockedError(
                "BookMyShow blocked the request",
                hint="Try an Indian residential connection or --proxy; BMS scores TLS fingerprints",
                detail=url,
            )
        return response.text

    def _seat_url(
        self,
        city: City,
        movie: MovieRef,
        date_code: str,
        venue_code: str,
        session_id: str,
        event_code: str | None = None,
    ) -> str:
        return (
            f"{BMS_BASE}/movies/{city.bms_slug}/{movie.bms_slug}/seat-layout/"
            f"{event_code or movie.bms_event_code}/{venue_code}/{session_id}/{date_code}"
        )


def decode_bms_seat_layout(encoded: str, key: bytes) -> str:
    if "||" in encoded:
        return encoded
    try:
        encrypted = base64.b64decode(encoded, validate=True)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        decoded = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except Exception as exc:
        raise SchemaError(
            "BookMyShow seat layout could not be decrypted",
            hint="The seat-layout key may have rotated; set SEATFINDER_BMS_SEAT_KEY",
            detail=str(exc),
        ) from exc
    if "||" not in decoded:
        raise SchemaError(
            "BookMyShow returned an unknown seat layout",
            hint="The seat-layout format may have changed",
            detail=decoded[:200],
        )
    return decoded


def _extract_shows_from_state(
    state: dict[str, Any],
    *,
    date_code: str,
    city: City,
    movie: MovieRef,
    event_code: str,
    format_hint: str,
    language_hint: str,
    include_previews: bool,
    seat_url,
) -> tuple[list[Show], set[str]]:
    shows: list[Show] = []
    dates: set[str] = set()
    seen: set[str] = set()

    old_dates = ((state.get("showtimesByEvent") or {}).get("showDates")) or {}
    if isinstance(old_dates, dict):
        dates.update(str(key) for key in old_dates.keys() if re.fullmatch(r"\d{8}", str(key)))
        date_data = old_dates.get(date_code)
        if isinstance(date_data, dict):
            static_venues = (
                ((date_data.get("primaryStatic") or {}).get("data") or {}).get("venues") or {}
            )
            widgets = ((date_data.get("dynamic") or {}).get("data") or {}).get("showtimeWidgets") or []
            for card in _iter_venue_cards(widgets):
                shows.extend(
                    _shows_from_card(
                        card,
                        date_code=date_code,
                        city=city,
                        movie=movie,
                        event_code=event_code,
                        format_hint=format_hint,
                        language_hint=language_hint,
                        include_previews=include_previews,
                        seat_url=seat_url,
                        static_venues=static_venues,
                        seen=seen,
                    )
                )

    queries = (state.get("showtimesFunctionalApi") or {}).get("queries") or {}
    for query in queries.values():
        data = (query.get("data") or {}).get("data")
        if not isinstance(data, dict):
            continue
        dates.update(_collect_date_codes(data.get("topStickyWidgets")))
        widgets = data.get("showtimeWidgets")
        if not isinstance(widgets, list):
            continue
        for card in _iter_venue_cards(widgets):
            shows.extend(
                _shows_from_card(
                    card,
                    date_code=date_code,
                    city=city,
                    movie=movie,
                    event_code=event_code,
                    format_hint=format_hint,
                    language_hint=language_hint,
                    include_previews=include_previews,
                    seat_url=seat_url,
                    static_venues={},
                    seen=seen,
                )
            )
    return shows, dates


def _shows_from_card(
    card: dict[str, Any],
    *,
    date_code: str,
    city: City,
    movie: MovieRef,
    event_code: str,
    format_hint: str,
    language_hint: str,
    include_previews: bool,
    seat_url,
    static_venues: dict[str, Any],
    seen: set[str],
) -> list[Show]:
    card_data = card.get("additionalData") or {}
    venue_code = str(card_data.get("venueCode") or card_data.get("venue_code") or "")
    venue = static_venues.get(venue_code) or {}
    venue_name = str(
        card_data.get("venueName")
        or card_data.get("venue_name")
        or venue.get("venueName")
        or venue.get("VenueName")
        or f"Venue {venue_code}"
    )
    show_seat_number = str(venue.get("showSeatNo") or venue.get("ShowSeatNo") or "Y")
    raw_shows: list[dict[str, Any]] = []
    if isinstance(card.get("showtimes"), list):
        raw_shows.extend(card["showtimes"])
    for section in card.get("showtimesSections") or []:
        if isinstance(section, dict) and isinstance(section.get("showtimes"), list):
            raw_shows.extend(section["showtimes"])

    shows: list[Show] = []
    for raw_show in raw_shows:
        data = raw_show.get("additionalData") or {}
        session_id = str(data.get("sessionId") or "")
        if not session_id or f"{venue_code}:{session_id}" in seen:
            continue
        show_date = str(data.get("showDateCode") or date_code)
        if show_date != date_code and not include_previews:
            continue
        seen.add(f"{venue_code}:{session_id}")
        categories = _categories_for_show(raw_show)
        prices = [category["price"] for category in categories if category.get("price") is not None]
        format_value, language_value = _format_for_show(raw_show, format_hint, language_hint)
        avail_status = _int(data.get("availStatus"))
        status = STATUS_MAP.get(avail_status, UNKNOWN)
        if status == UNKNOWN and categories:
            words = {str(c.get("availability") or "").upper() for c in categories}
            if words and words <= {"SOLD OUT"}:
                status = "sold_out"
        shows.append(
            Show(
                platform="bms",
                show_id=f"bms|{venue_code}|{session_id}|{show_date}",
                movie_title=movie.title,
                city=city.name,
                venue_id=venue_code,
                venue_name=venue_name,
                screen=str(raw_show.get("subtitleAcronym") or ""),
                starts_at=_bms_start_time(show_date, data, raw_show),
                format=format_value,
                language=language_value,
                price_min=min(prices) if prices else None,
                price_max=max(prices) if prices else None,
                total=None,
                available=None,
                status=status,
                booking_url=seat_url(
                    city,
                    movie,
                    show_date,
                    venue_code,
                    session_id,
                    event_code,
                ),
                area_counts=[
                    {
                        "code": "",
                        "label": category.get("label") or "",
                        "price": category.get("price"),
                        "available": None,
                        "total": None,
                        "availability": category.get("availability") or "",
                    }
                    for category in categories
                ],
                refs={
                    "venue_code": venue_code,
                    "session_id": session_id,
                    "show_seat_number": show_seat_number,
                    "event_code": event_code,
                    "date": show_date,
                    "city_slug": city.bms_slug,
                    "movie_slug": movie.bms_slug,
                    "venue_lat": venue.get("venueLatitude") or venue.get("VenueLatitude"),
                    "venue_lng": venue.get("venueLongitude") or venue.get("VenueLongitude"),
                    "address": venue.get("venueAddress") or venue.get("VenueAddress") or "",
                },
            )
        )
    return shows


def _categories_for_show(raw_show: dict[str, Any]) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    seen: set[tuple[str, float | None]] = set()
    widgets = (
        ((raw_show.get("customGestureCTA") or {}).get("additionalData") or {})
        .get("bottomSheetData", {})
        .get("widgets", [])
    )
    for widget in widgets:
        variable = widget.get("variableData") or {}
        label = str(variable.get("seatType") or "").strip()
        price = _money(variable.get("seatCost"))
        if label and price is not None:
            key = (label.casefold(), price)
            if key not in seen:
                seen.add(key)
                categories.append(
                    {
                        "label": label,
                        "price": price,
                        "availability": str(variable.get("seatAvalibility") or ""),
                    }
                )
    for category in (raw_show.get("additionalData") or {}).get("categories") or []:
        label = str(category.get("priceDesc") or category.get("label") or "").strip()
        price = _num(category.get("curPrice") or category.get("price"))
        if label:
            key = (label.casefold(), price)
            if key not in seen:
                seen.add(key)
                categories.append(
                    {
                        "label": label,
                        "price": price,
                        "availability": str(category.get("availability") or ""),
                    }
                )
    return categories


def _format_for_show(
    raw_show: dict[str, Any],
    format_hint: str,
    language_hint: str,
) -> tuple[str, str]:
    widgets = (
        ((raw_show.get("customGestureCTA") or {}).get("additionalData") or {})
        .get("bottomSheetData", {})
        .get("widgets", [])
    )
    format_value = ""
    for widget in widgets:
        variable = widget.get("variableData") or {}
        if variable.get("format"):
            format_value = str(variable["format"])
            break
    if not format_value:
        format_value = str(
            (raw_show.get("additionalData") or {}).get("attributes")
            or raw_show.get("screenAttr")
            or raw_show.get("attributes")
            or format_hint
            or ""
        )
    language = _language_from_text(format_value) or _language_from_text(language_hint)
    return format_value, language


def _collect_date_codes(value: Any) -> set[str]:
    dates: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            extra = node.get("additionalData") or {}
            if isinstance(extra, dict):
                date_code = extra.get("dateCode")
                if isinstance(date_code, str) and re.fullmatch(r"\d{8}", date_code):
                    dates.add(date_code)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return dates


def _walk_format_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("eventCode") and ("dimension" in value or "language" in value):
            yield value
        for child in value.values():
            yield from _walk_format_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_format_dicts(child)


def _match_show_area(area_name: str, area_counts: list[dict[str, Any]]) -> dict[str, Any] | None:
    wanted = _normalize(area_name)
    if not wanted:
        return None
    for item in area_counts:
        label = _normalize(str(item.get("label") or ""))
        if label and (wanted == label or wanted in label or label in wanted):
            return item
    return None


def _extract_initial_state(html: str) -> dict[str, Any]:
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if not match:
        raise SchemaError(
            "BookMyShow initial state was not found",
            hint="The page may be a bot interstitial or the site changed",
        )
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError as exc:
        raise SchemaError("BookMyShow initial state is invalid JSON", detail=str(exc)) from exc
    if not isinstance(payload, dict):
        raise SchemaError("BookMyShow initial state is not an object")
    return payload


def _iter_venue_cards(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("type") == "venue-card":
            yield value
            return
        for child in value.values():
            yield from _iter_venue_cards(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_venue_cards(child)


def _walk_items(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if "name" in value and "url" in value:
            yield value
        for child in value.values():
            yield from _walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_items(child)


def _walk_region_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if "RegionCode" in value and ("RegionName" in value or "RegionNameDisplay" in value):
            yield value
        for child in value.values():
            yield from _walk_region_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_region_dicts(child)


def _bms_start_time(show_date: str, data: dict[str, Any], raw_show: dict[str, Any]) -> str:
    compact = str(data.get("showDateTime") or raw_show.get("showDateTime") or "")
    if re.fullmatch(r"\d{12}", compact):
        try:
            return datetime.strptime(compact, "%Y%m%d%H%M").replace(tzinfo=IST).isoformat(timespec="minutes")
        except ValueError:
            pass
    display = str(data.get("showTime") or raw_show.get("title") or raw_show.get("showTime") or "")
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            parsed = datetime.strptime(display.strip().upper(), fmt)
            date = datetime.strptime(show_date, "%Y%m%d")
            return date.replace(
                hour=parsed.hour, minute=parsed.minute, tzinfo=IST
            ).isoformat(timespec="minutes")
        except ValueError:
            continue
    try:
        date = datetime.strptime(show_date, "%Y%m%d")
        return date.replace(tzinfo=IST).isoformat(timespec="minutes")
    except ValueError:
        return f"{show_date[:4]}-{show_date[4:6]}-{show_date[6:]}T00:00:00+05:30"


def _language_from_text(value: str) -> str:
    lowered = value.casefold()
    for language in (
        "english",
        "hindi",
        "tamil",
        "telugu",
        "malayalam",
        "kannada",
        "marathi",
        "bengali",
        "gujarati",
        "punjabi",
    ):
        if language in lowered:
            return language.title()
    return ""


def _movie_slug_from_url(value: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", value or "")
    segments = [segment for segment in path.split("/") if segment]
    for index, segment in enumerate(segments):
        if segment.upper().startswith("ET") and segment[2:].isdigit():
            if index > 0 and segments[index - 1] != "movies":
                return segments[index - 1]
    return ""


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_date_code(value: str) -> str:
    text = str(value or "")
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _money(value: Any) -> float | None:
    if value is None:
        return None
    text = re.sub(r"[^0-9.]", "", str(value))
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
