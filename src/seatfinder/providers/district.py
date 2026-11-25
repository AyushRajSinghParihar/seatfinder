"""District (district.in) read-only provider."""

from __future__ import annotations

import random
import re
import string
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from ..cache import JsonCache
from ..config import Config
from ..errors import AuthError, BlockedError, SchemaError, UpstreamError
from ..http import HttpClient, new_request_id
from ..jsonutil import now_iso
from ..models import (
    ACCESSIBLE,
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
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"
)
DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def _guest_token() -> str:
    digits = "".join(random.choice(string.digits) for _ in range(18))
    alphabet = string.ascii_lowercase + string.digits
    suffix = "".join(random.choice(alphabet) for _ in range(11))
    return f"{int(time.time())}_{digits}_{suffix}"


def _ist_iso(value: str | None, fallback_date: str) -> str:
    if not value:
        return f"{fallback_date}T00:00:00+05:30"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST).isoformat(timespec="minutes")
    except ValueError:
        return f"{fallback_date}T00:00:00+05:30"


def _language_from_tags(tags: list[str] | None) -> str:
    languages = {
        "english": "English",
        "hindi": "Hindi",
        "tamil": "Tamil",
        "telugu": "Telugu",
        "malayalam": "Malayalam",
        "kannada": "Kannada",
        "marathi": "Marathi",
        "bengali": "Bengali",
        "gujarati": "Gujarati",
        "punjabi": "Punjabi",
    }
    for tag in tags or []:
        for key, label in languages.items():
            if key in tag.casefold():
                return label
    return ""


class DistrictProvider:
    name = "district"
    base_url = "https://www.district.in"

    def __init__(self, config: Config, cache: JsonCache | None = None) -> None:
        self.config = config
        self.cache = cache or JsonCache(config)
        self.client = HttpClient(
            timeout=config.timeout,
            proxy=config.proxy,
            headers={"User-Agent": MOBILE_UA, "Accept-Language": "en-IN,en;q=0.9"},
        )
        self._warmed: set[str] = set()

    # ---------- movie resolution ----------

    def search_movies(self, city: City, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        body = {
            "get_search_results_request_type": 1,
            "post_body": {"hp_selected_tab_id": "movies"},
            "search_id": new_request_id(),
            "keyword": query,
            "tab_id": "movies",
        }
        response = self.client.post_json(
            f"{self.base_url}/gw/web/search",
            body,
            headers=self._search_headers(city),
            max_attempts=2,
            rotate_on=(),
            backoff=1.5,
        )
        try:
            payload = response.json()
        except UpstreamError as exc:
            raise SchemaError("District search returned non-JSON", detail=str(exc)) from exc
        results = payload.get("results")
        if results is None:
            raise SchemaError(
                "District search response has no results",
                hint="District may have changed its search endpoint",
                detail=str(payload)[:300],
            )
        movies: list[dict[str, Any]] = []
        for item in results:
            if item.get("entity_type") != "EntityTypeMovie":
                continue
            meta = item.get("metadata") or {}
            content_id = str(item.get("id") or "")
            if not content_id:
                continue
            title = str(item.get("display_title") or "").strip()
            slug = _slug_for_movie(title, meta.get("web_deeplink") or meta.get("deeplink"))
            url = meta.get("web_deeplink") or meta.get("deeplink")
            if not url:
                url = f"{self.base_url}/movies/{slug}-movie-tickets-MV{content_id}"
            movies.append(
                {
                    "platform": self.name,
                    "title": title,
                    "content_id": content_id,
                    "slug": slug,
                    "url": url,
                    "release_date": _release_date(meta.get("release_date")),
                    "language": meta.get("language"),
                    "not_released": str(meta.get("not_released", "")).lower() == "true",
                    "has_no_shows": str(meta.get("has_no_shows", "")).lower() == "true",
                }
            )
            if len(movies) >= limit:
                break
        return movies

    def resolve_movie(self, city: City, movie: MovieRef) -> MovieRef:
        if movie.district_content_id:
            return movie
        results = self.search_movies(city, movie.title)
        if not results:
            raise SchemaError(
                f"Could not find {movie.title!r} on District",
                hint="Pass --district-content-id or use a known movie title",
            )
        match = results[0]
        movie.district_content_id = match["content_id"]
        movie.district_slug = match["slug"]
        if not movie.title:
            movie.title = match["title"]
        return movie

    # ---------- showtimes ----------

    def _show_dates_key(self, city: City, movie: MovieRef, date: str) -> str:
        return f"district:showdates:{city.district_city_key}:{movie.district_content_id}:{date}"

    def show_dates(self, city: City, movie: MovieRef, date: str) -> list[str]:
        result = self.fetch_shows(city, movie, date, allow_empty=True)
        return result.show_dates

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
        params = {
            "version": "3",
            "site_id": "1",
            "channel": "mweb",
            "child_site_id": "1",
            "platform": "district",
            "movieCode": "",
            "city_key": city.district_city_key,
            "content_id": movie.district_content_id,
            "date": date,
            "latitude": city.lat,
            "longitude": city.lng,
            "cinemaOrderLogic": "3",
        }
        response = self.client.get(
            f"{self.base_url}/gw/consumer/movies/v5/movie",
            params=params,
            headers=self._movie_headers(city),
            max_attempts=2,
            rotate_on=(),
            backoff=1.5,
            allow_statuses=(204, 400),
        )
        if response.status_code == 204:
            return ProviderResult(
                warnings=[
                    Warning(
                        code="DATE_NOT_OPEN",
                        message=f"District has no sessions listed for {date}",
                        platform=self.name,
                    )
                ]
            )
        if response.status_code == 400:
            try:
                body = response.json()
            except UpstreamError:
                body = {}
            code = body.get("code")
            message = body.get("message") or body.get("msg") or "District rejected the request"
            if code == 32:
                return ProviderResult(
                    warnings=[
                        Warning(
                            code="CONTENT_NOT_LISTED",
                            message=message,
                            platform=self.name,
                        )
                    ]
                )
            raise UpstreamError(f"District rejected the request: {message}", detail=str(body)[:300])
        if response.status_code == 403:
            raise BlockedError("District blocked the request", hint="Try a residential connection or --proxy")

        payload = response.json()
        page = payload.get("pageData")
        meta = payload.get("meta") or {}
        if not isinstance(page, dict):
            raise SchemaError(
                "District showtime response has no pageData",
                hint="District may have changed its API shape",
                detail=str(payload)[:300],
            )

        shows: list[Show] = []
        warnings: list[Warning] = []
        cinemas = list(page.get("nearbyCinemas") or []) + list(page.get("farCinemas") or [])
        for cinema in cinemas:
            info = cinema.get("cinemaInfo") or {}
            cinema_id = str(cinema.get("id") or info.get("id") or "")
            venue_name = info.get("label") or info.get("name") or f"Cinema {cinema_id}"
            default_provider_id = str(info.get("providerId") or "")
            sessions = list(cinema.get("sessions") or []) + list(cinema.get("extraSessions") or [])
            seen: set[str] = set()
            for session in sessions:
                sid = str(session.get("sid") or "")
                if not sid or sid in seen:
                    continue
                seen.add(sid)
                day = str(session.get("showTime") or "")[:10]
                if day and day != date and not include_previews:
                    continue
                if not day:
                    day = date
                areas = list(session.get("areas") or [])
                area_counts = [
                    {
                        "code": str(area.get("code") or ""),
                        "label": str(area.get("label") or ""),
                        "price": _num(area.get("price")),
                        "available": _int(area.get("sAvail")),
                        "total": _int(area.get("sTotal")),
                    }
                    for area in areas
                ]
                prices = [a["price"] for a in area_counts if a["price"] is not None]
                avail = _int(session.get("avail"))
                total = _int(session.get("total"))
                status = "sold_out" if avail == 0 else ("available" if (avail or 0) > 0 else UNKNOWN)
                tags = [str(t) for t in session.get("tags") or []]
                provider_id = str(session.get("pid") or default_provider_id or "")
                shows.append(
                    Show(
                        platform=self.name,
                        show_id=f"district|{cinema_id}|{sid}|{date}",
                        movie_title=movie.title,
                        city=city.name,
                        venue_id=cinema_id,
                        venue_name=venue_name,
                        screen=str(session.get("audi") or ""),
                        starts_at=_ist_iso(session.get("showTime"), date),
                        format=str(session.get("scrnFmt") or ""),
                        language=_language_from_tags(tags),
                        price_min=min(prices) if prices else None,
                        price_max=max(prices) if prices else None,
                        total=total,
                        available=avail,
                        status=status,
                        booking_url=self._booking_url(city, movie, date),
                        area_counts=area_counts,
                        refs={
                            "cinema_id": str(session.get("cid") or cinema_id),
                            "session_id": sid,
                            "provider_id": provider_id,
                            "screen_format": str(session.get("scrnFmt") or ""),
                            "movie_code": str(session.get("mid") or ""),
                            "content_id": movie.district_content_id,
                            "date": date,
                            "chain": info.get("chainKey") or "",
                        },
                    )
                )

        if not shows and not allow_empty:
            warnings.append(
                Warning(
                    code="NO_SHOWS",
                    message=f"District returned no sessions for {movie.title} on {date}",
                    platform=self.name,
                )
            )
        raw_dates = meta.get("showDates") or []
        show_dates = sorted({str(d) for d in raw_dates if d})
        if not show_dates:
            show_dates = sorted({show.starts_at[:10] for show in shows})
        return ProviderResult(shows=shows, warnings=warnings, show_dates=show_dates)

    # ---------- seat maps ----------

    def _warm(self, city: City, movie: MovieRef) -> None:
        key = f"{city.district_city_key}:{movie.district_content_id}"
        if key in self._warmed:
            return
        slug = movie.district_slug or _slug_for_movie(movie.title, None)
        urls = [
            f"{self.base_url}/movies/{slug}-movie-tickets-MV{movie.district_content_id}",
            f"{self.base_url}/movies/",
        ]
        for url in urls:
            try:
                response = self.client.get(
                    url,
                    headers={"User-Agent": MOBILE_UA, "Accept": "text/html,application/xhtml+xml"},
                    max_attempts=1,
                    rotate_on=(),
                    allow_statuses=(404,),
                )
                if response.status_code < 500:
                    self._warmed.add(key)
                    return
            except Exception:
                continue
        self._warmed.add(key)

    def fetch_seat_map(self, city: City, movie: MovieRef, show: Show) -> SeatMap:
        if not movie.district_content_id:
            movie = self.resolve_movie(city, movie)
        self._warm(city, movie)
        refs = show.refs
        body = {
            "cinemaId": _int(refs.get("cinema_id")),
            "sessionId": str(refs.get("session_id") or ""),
            "providerId": _int(refs.get("provider_id")),
            "screenFormat": str(refs.get("screen_format") or show.format),
            "moviecode": str(refs.get("movie_code") or ""),
            "contentId": str(refs.get("content_id") or movie.district_content_id),
        }
        url = f"{self.base_url}/gw/consumer/movies/v1/select-seat"
        params = {"version": "3", "site_id": "1", "channel": "web"}
        headers = self._seat_headers(city, movie, show)

        response = self.client.post_json(
            url,
            body,
            params=params,
            headers=headers,
            max_attempts=1,
            rotate_on=(),
        )
        if response.status_code == 401:
            self._warmed.discard(f"{city.district_city_key}:{movie.district_content_id}")
            self._warm(city, movie)
            response = self.client.post_json(
                url,
                body,
                params=params,
                headers=self._seat_headers(city, movie, show),
                max_attempts=1,
                rotate_on=(),
            )
        if response.status_code == 403:
            raise BlockedError("District blocked the seat map request", hint="Try a residential connection or --proxy")
        if response.status_code == 401:
            raise AuthError(
                "District seat map needs a browser session",
                hint="Set SEATFINDER_DISTRICT_COOKIE with cookies copied from district.in",
            )

        payload = response.json()
        layout = _find_layout(payload)
        if not layout:
            raise SchemaError(
                "District seat map has no seatLayout",
                hint="District may have changed its seat endpoint",
                detail=str(payload)[:300],
            )
        return self._parse_layout(show, layout, payload)

    def _parse_layout(self, show: Show, layout: dict[str, Any], payload: dict[str, Any]) -> SeatMap:
        areas_raw = (((layout.get("colAreas") or {}).get("objArea")) or [])
        if isinstance(areas_raw, dict):
            areas_raw = [areas_raw]
        prices = {
            str(a.get("code") or ""): _num(a.get("price"))
            for a in show.area_counts
            if a.get("price") is not None
        }
        areas: list[SeatArea] = []
        rows: list[SeatRow] = []
        warnings: list[Warning] = []
        representational = bool(
            payload.get("layoutIsRepresentational")
            or payload.get("isRepresentational")
            or layout.get("isRepresentational")
        )
        if representational:
            warnings.append(
                Warning(
                    code="LAYOUT_REPRESENTATIONAL",
                    message="District marks this layout as approximate; seat positions may differ slightly",
                    platform=self.name,
                )
            )

        for area in sorted(areas_raw, key=lambda a: _int(a.get("AreaNum")) or 0):
            area_code = str(area.get("AreaCode") or "")
            area_name = str(area.get("AreaDesc") or "")
            area_price = _num(area.get("AreaPrice"))
            if area_price is None:
                area_price = prices.get(area_code)
            areas.append(SeatArea(code=area_code, name=area_name, price=area_price))
            for raw_row in area.get("objRow") or []:
                row_label = str(raw_row.get("PhyRowId") or raw_row.get("GridRowId") or "")
                row_order = _int(raw_row.get("GridRowId")) or 0
                seats: list[Seat] = []
                for raw_seat in raw_row.get("objSeat") or []:
                    status_code = str(raw_seat.get("SeatStatus") or "")
                    status = _seat_status(status_code)
                    number = str(
                        raw_seat.get("displaySeatNumber")
                        or raw_seat.get("seatNumber")
                        or raw_seat.get("GridSeatNum")
                        or ""
                    )
                    grid = _int(raw_seat.get("GridSeatNum")) or 0
                    seat_type = str(raw_seat.get("type") or raw_seat.get("seatType") or "")
                    is_best = str(raw_seat.get("highlightSeat") or "") == "bestSeat"
                    seats.append(
                        Seat(
                            row=row_label,
                            number=number,
                            grid_position=grid,
                            status=status,
                            area_code=area_code,
                            area_name=area_name,
                            price=_num(raw_seat.get("seatPrice")) or area_price,
                            seat_type=seat_type,
                            is_best=is_best,
                        )
                    )
                if seats:
                    seats.sort(key=lambda s: s.grid_position)
                    rows.append(SeatRow(label=row_label, order=row_order, seats=seats))

        if not areas or not rows:
            raise SchemaError(
                "District seat layout contained no seats",
                hint="The show may be closed, or District changed its seat format",
                detail=str(payload)[:300],
            )
        rows.sort(key=lambda r: r.order)
        return SeatMap(
            platform=self.name,
            show_id=show.show_id,
            fetched_at=now_iso(),
            areas=areas,
            rows=rows,
            warnings=warnings,
        )

    # ---------- headers / urls ----------

    def _search_headers(self, city: City) -> dict[str, str]:
        return {
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json, text/plain, */*",
            "api_source": "district",
            "x-app-type": "ed_web",
            "x-guest-token": _guest_token(),
            "x-request-id": new_request_id(),
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/search",
            "x-user-lat": str(city.lat),
            "x-user-lng": str(city.lng),
            "x-gps-lat": str(city.lat),
            "x-gps-lng": str(city.lng),
            "x-pcity-key": city.district_city_key,
            "x-city-name": city.name,
            "x-pcity-name": city.name,
            "x-available-tabs": "movies,events,dining",
        }

    def _movie_headers(self, city: City) -> dict[str, str]:
        return {
            "User-Agent": MOBILE_UA,
            "Accept": "*/*",
            "api_source": "district",
            "x-app-type": "ed_mweb",
            "x-guest-token": _guest_token(),
            "x-request-id": new_request_id(),
            "Referer": f"{self.base_url}/movies/",
            "x-user-lat": str(city.lat),
            "x-user-lng": str(city.lng),
            "x-gps-lat": str(city.lat),
            "x-gps-lng": str(city.lng),
        }

    def _seat_headers(self, city: City, movie: MovieRef, show: Show) -> dict[str, str]:
        headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "*/*",
            "api_source": "district",
            "x-app-type": "ed_web",
            "x-guest-token": _guest_token(),
            "x-request-id": new_request_id(),
            "paytmcinemaid": str(show.refs.get("cinema_id") or ""),
            "Origin": self.base_url,
            "Referer": self._booking_url(city, movie, show.starts_at[:10]),
        }
        if self.config.district_cookie:
            headers["Cookie"] = self.config.district_cookie
        return headers

    def _booking_url(self, city: City, movie: MovieRef, date: str) -> str:
        slug = movie.district_slug or _slug_for_movie(movie.title, None)
        return (
            f"{self.base_url}/movies/{slug}-movie-tickets-in-{city.district_city_key}"
            f"-MV{movie.district_content_id}?fromdate={date}"
        )


def _release_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), IST).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return text


def _seat_status(code: str) -> str:
    if code == "0":
        return AVAILABLE
    if code in {"1000", "1001", "1002"}:
        return ACCESSIBLE
    if code == "1":
        return BOOKED
    return UNKNOWN


def _find_layout(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        for item in payload:
            found = _find_layout(item)
            if found:
                return found
        return None
    if not isinstance(payload, dict):
        return None
    layout = payload.get("seatLayout")
    if isinstance(layout, dict) and layout.get("colAreas"):
        return layout
    for value in payload.values():
        if isinstance(value, (dict, list)):
            found = _find_layout(value)
            if found:
                return found
    return None


def _slug_for_movie(title: str, deeplink: str | None) -> str:
    if deeplink:
        match = re.search(r"/movies/([^/?]+)-movie-tickets", deeplink)
        if match:
            return match.group(1)
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


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
