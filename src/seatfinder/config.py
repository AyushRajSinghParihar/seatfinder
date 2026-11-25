"""Configuration and filesystem locations."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from platformdirs import user_cache_dir, user_config_dir

APP_NAME = "seatfinder"
DEFAULT_BMS_SEAT_KEY = b"kYp3s6v9y$B&E)H+MbQeThWmZq4t7w!z"
DEFAULT_BMS_PROFILES = ("safari180", "safari170", "firefox144", "edge101")


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _env(name: str, default: Any = None) -> Any:
    value = os.environ.get(f"SEATFINDER_{name}")
    return default if value is None or value == "" else value


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class Config:
    default_city: str | None = None
    default_movie: str = "Avengers: Doomsday"
    default_platform: str = "both"
    timeout: float = 30.0
    proxy: str | None = None
    bms_tls_profiles: list[str] = field(default_factory=lambda: list(DEFAULT_BMS_PROFILES))
    bms_seat_key: bytes = DEFAULT_BMS_SEAT_KEY
    district_cookie: str | None = None
    cache_ttl: int = 300
    cache_dir: Path = field(default_factory=lambda: Path(user_cache_dir(APP_NAME)))
    config_dir: Path = field(default_factory=lambda: Path(user_config_dir(APP_NAME)))

    @classmethod
    def load(cls) -> Config:
        config_dir = Path(user_config_dir(APP_NAME))
        data = _read_toml(config_dir / "config.toml")
        cfg = cls()
        cfg.config_dir = config_dir
        cfg.default_city = data.get("default_city") or cfg.default_city
        cfg.default_movie = data.get("default_movie") or cfg.default_movie
        cfg.default_platform = data.get("default_platform") or cfg.default_platform
        cfg.timeout = float(data.get("timeout", cfg.timeout))
        cfg.proxy = data.get("proxy") or cfg.proxy
        cfg.district_cookie = data.get("district_cookie") or cfg.district_cookie
        cfg.cache_ttl = int(data.get("cache_ttl", cfg.cache_ttl))
        if data.get("bms_tls_profiles"):
            cfg.bms_tls_profiles = list(data["bms_tls_profiles"])
        if data.get("bms_seat_key"):
            cfg.bms_seat_key = str(data["bms_seat_key"]).encode("utf-8")
        if data.get("cache_dir"):
            cfg.cache_dir = Path(data["cache_dir"])

        cfg.default_city = _env("CITY", cfg.default_city)
        cfg.default_movie = _env("MOVIE", cfg.default_movie)
        cfg.default_platform = _env("PLATFORM", cfg.default_platform)
        cfg.timeout = _env_float("TIMEOUT", cfg.timeout)
        cfg.proxy = _env("PROXY", cfg.proxy) or os.environ.get("HTTPS_PROXY")
        cfg.district_cookie = _env("DISTRICT_COOKIE", cfg.district_cookie)
        profiles = _env("BMS_TLS_PROFILES")
        if profiles:
            cfg.bms_tls_profiles = [p.strip() for p in str(profiles).split(",") if p.strip()]
        key = _env("BMS_SEAT_KEY")
        if key:
            cfg.bms_seat_key = str(key).encode("utf-8")
        return cfg
