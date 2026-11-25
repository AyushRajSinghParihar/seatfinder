"""Built-in city and movie catalog with forgiving name matching."""

from __future__ import annotations

import json
import re
from importlib import resources

from .errors import NotFoundError
from .models import City, MovieRef


def normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _load(name: str) -> dict:
    path = resources.files("seatfinder.data").joinpath(name)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class Catalog:
    def __init__(self) -> None:
        self._cities: list[City] = []
        self._movies: list[MovieRef] = []
        self._load()

    def _load(self) -> None:
        for raw in _load("cities.json").get("cities", []):
            self._cities.append(
                City(
                    name=raw["name"],
                    aliases=list(raw.get("aliases", [])),
                    bms_slug=raw.get("bms_slug") or slugify(raw["name"]),
                    bms_region=raw.get("bms_region"),
                    district_city_key=raw.get("district_city_key") or slugify(raw["name"]),
                    lat=float(raw.get("lat", 0)),
                    lng=float(raw.get("lng", 0)),
                )
            )
        for raw in _load("movies.json").get("movies", []):
            bms = raw.get("bms") or {}
            district = raw.get("district") or {}
            self._movies.append(
                MovieRef(
                    title=raw["title"],
                    aliases=list(raw.get("aliases", [])),
                    default=bool(raw.get("default")),
                    release_date=raw.get("release_date"),
                    bms_event_code=bms.get("event_code"),
                    bms_slug=bms.get("slug"),
                    district_content_id=(str(district["content_id"]) if district.get("content_id") else None),
                    district_slug=district.get("slug"),
                )
            )

    @property
    def cities(self) -> list[City]:
        return list(self._cities)

    @property
    def movies(self) -> list[MovieRef]:
        return list(self._movies)

    def resolve_city(self, value: str) -> City:
        wanted = normalize(value)
        if not wanted:
            raise NotFoundError("City is required", hint="Pass --city or run seatfinder interactively")
        for city in self._cities:
            names = [city.name, city.bms_slug, city.district_city_key, *city.aliases]
            if wanted in {normalize(name) for name in names}:
                return city
        for city in self._cities:
            names = [city.name, city.bms_slug, city.district_city_key, *city.aliases]
            if any(wanted in normalize(name) or normalize(name) in wanted for name in names):
                return city
        raise NotFoundError(
            f"Unknown city: {value}",
            hint="Run 'seatfinder cities' to see known cities, or add it to cities.json",
        )

    def default_movie(self) -> MovieRef:
        for movie in self._movies:
            if movie.default:
                return movie
        if self._movies:
            return self._movies[0]
        return MovieRef(title="Avengers: Doomsday", default=True)

    def resolve_movie(self, value: str | None) -> MovieRef:
        if not value:
            return self.default_movie()
        wanted = normalize(value)
        for movie in self._movies:
            names = [movie.title, *movie.aliases]
            if wanted in {normalize(name) for name in names}:
                return movie
        for movie in self._movies:
            names = [movie.title, *movie.aliases]
            if any(wanted in normalize(name) or normalize(name) in wanted for name in names):
                return movie
        return MovieRef(title=value.strip(), aliases=[], default=False)
