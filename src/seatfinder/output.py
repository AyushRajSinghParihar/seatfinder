"""Human and machine output renderers."""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.table import Table

from .jsonutil import dumps
from .models import City, Show, Warning


def make_console(
    *, quiet: bool = False, no_color: bool = False
) -> tuple[Console, Console]:
    out = Console(
        stderr=False,
        quiet=quiet,
        no_color=no_color,
        highlight=False,
        soft_wrap=False,
    )
    err = Console(
        stderr=True,
        quiet=quiet,
        no_color=no_color,
        highlight=False,
        soft_wrap=False,
    )
    return out, err


def print_json(payload: dict[str, Any], *, pretty: bool = True) -> None:
    sys.stdout.write(dumps(payload, pretty=pretty) + "\n")


def print_warnings(warnings: list[Warning], console: Console) -> None:
    for warning in warnings:
        platform = f" [{warning.platform}]" if warning.platform else ""
        console.print(f"[yellow]{warning.code}{platform}[/yellow] {warning.message}")


def print_cities(cities: list[City], console: Console) -> None:
    table = Table(title="Cities", show_header=True, header_style="bold")
    table.add_column("City")
    table.add_column("BMS")
    table.add_column("District")
    table.add_column("Lat, Lng", justify="right")
    for city in cities:
        table.add_row(
            city.name,
            city.bms_slug,
            city.district_city_key,
            f"{city.lat:.4f}, {city.lng:.4f}",
        )
    console.print(table)


def print_movies(movies: list[dict[str, Any]], console: Console) -> None:
    table = Table(title="Movies", show_header=True, header_style="bold")
    table.add_column("Movie")
    table.add_column("BMS")
    table.add_column("District")
    for movie in movies:
        bms = movie.get("bms") or {}
        district = movie.get("district") or {}
        table.add_row(
            movie.get("title") or "",
            bms.get("event_code") or "-",
            f"MV{district.get('content_id')}" if district.get("content_id") else "-",
        )
    console.print(table)


def print_shows(shows: list[Show], console: Console, *, date: str) -> None:
    if not shows:
        console.print("[yellow]No shows found.[/yellow]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Platform")
    table.add_column("Start")
    table.add_column("Venue")
    table.add_column("Format")
    table.add_column("Price", justify="right")
    table.add_column("Status")
    for index, show in enumerate(shows, start=1):
        start = show.starts_at[:16].replace("T", " ")
        if show.starts_at[:10] != date:
            start = show.starts_at[:10] + " " + show.starts_at[11:16]
        price = ""
        if show.price_min is not None and show.price_max is not None:
            price = (
                f"₹{show.price_min:.0f}"
                if show.price_min == show.price_max
                else f"₹{show.price_min:.0f}-{show.price_max:.0f}"
            )
        table.add_row(
            str(index),
            show.platform,
            start,
            show.venue_name,
            show.format or show.screen or "",
            price,
            show.status,
        )
    console.print(table)


def print_seat_results(
    results: list[dict[str, Any]], console: Console, *, show_map: bool = False
) -> None:
    if not results:
        console.print("[yellow]No shows to inspect.[/yellow]")
        return
    for index, item in enumerate(results, start=1):
        venue = item.get("venue") or {}
        summary = ((item.get("seat_map") or {}).get("summary")) or {}
        header = (
            f"[bold]{index}. {item.get('platform')} · {item.get('starts_at', '')[11:16]} · "
            f"{venue.get('name', '')}[/bold]"
        )
        console.print(header)
        details = []
        if item.get("format"):
            details.append(str(item["format"]))
        if venue.get("screen"):
            details.append(str(venue["screen"]))
        if details:
            console.print("   " + " · ".join(details))
        if summary:
            console.print(
                f"   seats: {summary.get('available', 0)} available / {summary.get('total', 0)} total"
                f" · {summary.get('occupancy_pct', 0)}% booked"
            )
        blocks = item.get("best_blocks") or []
        if blocks:
            rendered = []
            for block in blocks[:3]:
                seats = ", ".join(block.get("seats") or [])
                rendered.append(
                    f"row {block.get('row')} [{seats}] {block.get('area_name') or ''}".strip()
                )
            console.print("   best blocks: " + " | ".join(rendered))
        elif item.get("block_found") is False:
            console.print("   [yellow]no contiguous block matched[/yellow]")
        error = item.get("seat_map_error")
        if error:
            console.print(f"   [red]{error.get('code')}[/red] {error.get('message')}")
        if show_map and item.get("seat_map") and item["seat_map"].get("rows"):
            from .blocks import render_seat_map
            from .models import Seat, SeatArea, SeatMap, SeatRow

            seat_map = SeatMap(
                platform=item["platform"],
                show_id=item["show_id"],
                fetched_at=item["seat_map"].get("fetched_at", ""),
                areas=[SeatArea(**area) for area in item["seat_map"].get("areas", [])],
                rows=[
                    SeatRow(
                        label=row["label"],
                        order=row.get("order", 0),
                        seats=[
                            Seat(
                                row=seat["row"],
                                number=seat["number"],
                                grid_position=seat["grid_position"],
                                status=seat["status"],
                                area_code=seat["area_code"],
                                area_name=seat.get("area_name", ""),
                                price=seat.get("price"),
                                seat_type=seat.get("seat_type", ""),
                                is_best=seat.get("is_best", False),
                            )
                            for seat in row.get("seats", [])
                        ],
                    )
                    for row in item["seat_map"].get("rows", [])
                ],
            )
            console.print(render_seat_map(seat_map))
        console.print()
