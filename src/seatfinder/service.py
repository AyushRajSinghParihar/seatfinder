"""Application service that coordinates both providers."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

from .blocks import find_blocks
from .cache import JsonCache
from .catalog import Catalog, normalize
from .config import Config
from .errors import NotFoundError, SeatfinderError, UsageError
from .models import City, MovieRef, ProviderResult, SeatMap, Show, Warning
from .providers.bms import BookMyShowProvider
from .providers.district import DistrictProvider

IST = timezone(timedelta(hours=5, minutes=30))
PLATFORMS = ("bms", "district")


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


@dataclass(slots=True)
class ShowBundle:
    city: City
    movie: MovieRef
    date: str
    shows: list[Show] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    show_dates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "city": self.city.to_dict(),
            "movie": self.movie.to_dict(),
            "date": self.date,
            "show_dates": self.show_dates,
            "shows": [show.to_dict() for show in self.shows],
            "warnings": [warning.to_dict() for warning in self.warnings],
        }


class SeatService:
    def __init__(
        self,
        config: Config,
        *,
        platform: str = "both",
        no_cache: bool = False,
        include_previews: bool = False,
    ) -> None:
        self.config = config
        self.catalog = Catalog()
        self.cache = JsonCache(config, enabled=not no_cache)
        self.platform = _validate_platform(platform)
        self.include_previews = include_previews
        self.providers = {
            "bms": BookMyShowProvider(config, self.cache),
            "district": DistrictProvider(config, self.cache),
        }
        self._selected = list(PLATFORMS) if self.platform == "both" else [self.platform]

    def resolve(self, city: str | None, movie: str | None) -> tuple[City, MovieRef]:
        city_name = city or self.config.default_city or ""
        try:
            city_obj = self.catalog.resolve_city(city_name)
        except NotFoundError:
            city_obj = self._city_from_bms(city_name)
        movie_obj = self.catalog.resolve_movie(movie or self.config.default_movie)
        return city_obj, movie_obj

    def _city_from_bms(self, value: str) -> City:
        from .catalog import normalize, slugify

        wanted = normalize(value)
        if not wanted:
            raise NotFoundError(
                "City is required", hint="Pass --city or run seatfinder interactively"
            )
        try:
            regions = self.providers["bms"].regions()
        except SeatfinderError as exc:
            raise NotFoundError(
                f"Unknown city: {value}",
                hint="Run 'seatfinder cities' or pass a major Indian city",
                detail=exc.message,
            ) from exc
        candidates = [
            region
            for region in regions
            if wanted
            in {
                normalize(region.get("name") or ""),
                normalize(region.get("slug") or ""),
            }
        ]
        if not candidates:
            candidates = [
                region
                for region in regions
                if wanted in normalize(region.get("name") or "")
                or normalize(region.get("name") or "") in wanted
            ]
        if not candidates:
            raise NotFoundError(
                f"Unknown city: {value}",
                hint="Run 'seatfinder cities' to see bundled cities",
            )
        region = candidates[0]
        name = str(region.get("name") or value)
        slug = str(region.get("slug") or slugify(name))
        lat = region.get("lat")
        lng = region.get("lng")
        if lat is None or lng is None:
            raise NotFoundError(
                f"City {name} has no coordinates",
                hint="Pass a major city or add coordinates to cities.json",
            )
        return City(
            name=name,
            aliases=[],
            bms_slug=slug,
            bms_region=str(region.get("code") or ""),
            district_city_key=slug,
            lat=float(lat),
            lng=float(lng),
        )

    def search_movies(self, city: City, query: str) -> list[dict]:
        combined: dict[str, dict] = {}
        warnings: list[Warning] = []
        for platform in self._selected:
            try:
                results = self.providers[platform].search_movies(city, query)
            except SeatfinderError as exc:
                warnings.append(
                    Warning(code=exc.code, message=exc.message, platform=platform)
                )
                continue
            for result in results:
                key = normalize(result["title"])
                entry = combined.setdefault(
                    key,
                    {
                        "title": result["title"],
                        "bms": None,
                        "district": None,
                        "warnings": [],
                    },
                )
                if platform == "bms":
                    entry["bms"] = {
                        "event_code": result.get("event_code"),
                        "slug": result.get("slug"),
                        "url": result.get("url"),
                    }
                else:
                    entry["district"] = {
                        "content_id": result.get("content_id"),
                        "slug": result.get("slug"),
                        "url": result.get("url"),
                        "release_date": result.get("release_date"),
                        "not_released": result.get("not_released"),
                        "has_no_shows": result.get("has_no_shows"),
                    }
        if not combined:
            default = self.catalog.default_movie()
            if normalize(query) in {
                normalize(default.title),
                *[normalize(a) for a in default.aliases],
            }:
                combined[normalize(default.title)] = {
                    "title": default.title,
                    "bms": {
                        "event_code": default.bms_event_code,
                        "slug": default.bms_slug,
                    },
                    "district": {
                        "content_id": default.district_content_id,
                        "slug": default.district_slug,
                    },
                    "warnings": [],
                }
        results = list(combined.values())
        results.sort(key=lambda item: item["title"].casefold())
        return results

    def fetch_shows(self, city: City, movie: MovieRef, date: str | None) -> ShowBundle:
        target, resolved_earliest = self._target_date(city, movie, date)
        bundle = self._fetch_shows_on(city, movie, target)
        if resolved_earliest:
            candidates = [d for d in bundle.show_dates if d >= today_ist()]
            if candidates:
                earliest = min(candidates)
                if earliest != target:
                    bundle = self._fetch_shows_on(city, movie, earliest)
        return bundle

    def _target_date(
        self, city: City, movie: MovieRef, date: str | None
    ) -> tuple[str, bool]:
        if date and date not in {"earliest", "any"}:
            return _validate_date(date), False
        today = today_ist()
        if movie.release_date and movie.release_date >= today:
            return movie.release_date, True
        return today, True

    def _fetch_shows_on(self, city: City, movie: MovieRef, target: str) -> ShowBundle:
        shows: list[Show] = []
        warnings: list[Warning] = []
        show_dates: set[str] = set()
        errors: list[SeatfinderError] = []
        for platform in self._selected:
            provider = self.providers[platform]
            try:
                result: ProviderResult = provider.fetch_shows(
                    city,
                    movie,
                    target,
                    allow_empty=True,
                    include_previews=self.include_previews,
                )
            except SeatfinderError as exc:
                errors.append(exc)
                warnings.append(
                    Warning(
                        code=exc.code,
                        message=exc.message,
                        platform=platform,
                        detail=exc.hint or exc.detail,
                    )
                )
                continue
            shows.extend(result.shows)
            warnings.extend(result.warnings)
            show_dates.update(result.show_dates)
        if not shows and errors and len(errors) == len(self._selected):
            raise errors[0]
        shows.sort(key=lambda show: (show.starts_at, show.platform, show.venue_name))
        if not show_dates:
            show_dates.update(show.starts_at[:10] for show in shows)
        return ShowBundle(
            city=city,
            movie=movie,
            date=target,
            shows=shows,
            warnings=warnings,
            show_dates=sorted(show_dates),
        )

    def _select_shows(self, shows: list[Show], limit: int | None) -> list[Show]:
        if limit is None or len(shows) <= limit:
            return list(shows)
        if self.platform != "both":
            return list(shows[:limit])
        buckets = {
            platform: [show for show in shows if show.platform == platform]
            for platform in self._selected
        }
        selected: list[Show] = []
        while len(selected) < limit and any(buckets.values()):
            for platform in self._selected:
                queue = buckets[platform]
                if queue and len(selected) < limit:
                    selected.append(queue.pop(0))
        selected.sort(key=lambda show: (show.starts_at, show.platform, show.venue_name))
        return selected

    def fetch_seats(
        self,
        bundle: ShowBundle,
        *,
        min_seats: int = 1,
        area: str | None = None,
        include_accessible: bool = False,
        limit: int | None = 10,
        detail: str = "summary",
    ) -> list[dict]:
        if detail not in {"summary", "full", "none"}:
            raise UsageError("detail must be one of: summary, full, none")
        if min_seats < 1:
            raise UsageError("min_seats must be at least 1")
        if limit is not None and limit < 1:
            raise UsageError("limit must be at least 1, or use --all")
        shows = self._select_shows(bundle.shows, limit)
        output: list[dict] = []
        for index, show in enumerate(shows):
            if index:
                time.sleep(random.uniform(0.8, 1.6))
            provider = self.providers[show.platform]
            item = show.to_dict()
            try:
                seat_map: SeatMap = provider.fetch_seat_map(
                    bundle.city, bundle.movie, show
                )
                blocks = find_blocks(
                    seat_map,
                    min_seats=min_seats,
                    area=area,
                    include_accessible=include_accessible,
                    limit=5,
                )
                item["best_blocks"] = [block.to_dict() for block in blocks]
                item["block_found"] = bool(blocks)
                item["seat_map"] = seat_map.to_dict(include_rows=detail == "full")
            except SeatfinderError as exc:
                item["best_blocks"] = []
                item["block_found"] = False
                item["seat_map"] = None
                item["seat_map_error"] = exc.to_dict()
            output.append(item)
        return output


def _validate_platform(value: str) -> str:
    if value not in {"both", "bms", "district"}:
        raise UsageError("platform must be one of: both, bms, district")
    return value


def _validate_date(value: str) -> str:
    try:
        date_cls.fromisoformat(value)
    except ValueError as exc:
        raise UsageError(f"Invalid date: {value}", hint="Use YYYY-MM-DD") from exc
    return value
