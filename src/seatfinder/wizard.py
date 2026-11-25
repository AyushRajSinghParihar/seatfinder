"""Interactive wizard used when no subcommand is given."""

from __future__ import annotations

import sys

import typer
from rich.console import Console

from .config import Config
from .output import (
    fetch_seats_with_progress,
    print_seat_results,
    print_shows,
    print_warnings,
)
from .service import SeatService


def available_for_wizard() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def run_wizard(
    config: Config,
    *,
    no_cache: bool = False,
    include_previews: bool = False,
    no_color: bool = False,
    quiet: bool = False,
) -> None:
    console = Console(no_color=no_color, quiet=quiet, highlight=False)
    err = Console(stderr=True, no_color=no_color, quiet=quiet, highlight=False)
    catalog_service = SeatService(
        config,
        platform="both",
        no_cache=no_cache,
        include_previews=include_previews,
    )
    cities = catalog_service.catalog.cities
    popular = [
        "Bengaluru",
        "Mumbai",
        "Delhi",
        "Hyderabad",
        "Chennai",
        "Pune",
        "Kolkata",
    ]
    default_city = config.default_city or "Bengaluru"
    city_choices = [city.name for city in cities if city.name in popular]
    console.print(
        "[bold]seatfinder[/bold] — find empty seats on BookMyShow and District"
    )
    console.print(
        "Popular cities: " + ", ".join(city_choices or [c.name for c in cities[:7]])
    )
    city_name = typer.prompt("City", default=default_city).strip()

    movie_default = config.default_movie
    movie_name = typer.prompt("Movie", default=movie_default).strip() or movie_default

    platform = (
        typer.prompt(
            "Platform (both/bms/district)",
            default=config.default_platform,
            show_default=True,
        )
        .strip()
        .lower()
    )
    if platform not in {"both", "bms", "district"}:
        platform = "both"

    service = SeatService(
        config,
        platform=platform,
        no_cache=no_cache,
        include_previews=include_previews,
    )
    city, movie = service.resolve(city_name, movie_name)

    console.print("Checking showdates…")
    bundle = service.fetch_shows(city, movie, "earliest")
    if bundle.warnings:
        print_warnings(bundle.warnings, console)
    if not bundle.shows:
        console.print("[yellow]No shows found for this movie yet.[/yellow]")
        return
    date_default = bundle.date
    if bundle.show_dates:
        console.print("Selling dates: " + ", ".join(bundle.show_dates[:14]))
        date_value = typer.prompt(
            "Date (YYYY-MM-DD or earliest)", default=date_default
        ).strip()
    else:
        date_value = date_default
    if date_value not in {"earliest", "any"}:
        bundle = service.fetch_shows(city, movie, date_value)

    print_shows(bundle.shows, console, date=bundle.date)
    if not bundle.shows:
        return

    min_seats = typer.prompt("Minimum seats together", default=1, type=int)
    prefer = (
        typer.prompt(
            "Where do you want to sit? (center/back/front/any)",
            default="center",
            show_default=True,
        )
        .strip()
        .lower()
    )
    if prefer not in {"center", "back", "front", "any"}:
        prefer = "center"
    choice = (
        typer.prompt(
            "Inspect which shows? (top/all/number)",
            default="top",
            show_default=True,
        )
        .strip()
        .lower()
    )
    limit: int | None = 5
    if choice == "all":
        limit = None
    elif choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(bundle.shows):
            bundle.shows = [bundle.shows[index - 1]]
        limit = None
    results = fetch_seats_with_progress(
        service,
        bundle,
        err,
        enabled=not quiet,
        min_seats=min_seats,
        prefer=prefer,
        limit=limit,
        detail="summary",
    )
    print_seat_results(results, console, show_map=False)
    console.print(
        "Tip: use --json for agent-friendly output and --map for a seat grid."
    )
