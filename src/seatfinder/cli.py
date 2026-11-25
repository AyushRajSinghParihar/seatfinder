"""Command-line entry point."""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import typer

from . import __version__
from .config import Config
from .errors import (
    EXIT_UPSTREAM,
    PartialError,
    SeatfinderError,
    UsageError,
)
from .jsonutil import envelope, now_iso
from .output import (
    make_console,
    print_cities,
    print_json,
    print_movies,
    print_seat_results,
    print_shows,
    print_warnings,
)
from .service import SeatService, today_ist
from .wizard import available_for_wizard, run_wizard

app = typer.Typer(
    name="seatfinder",
    help="Find live empty seats on BookMyShow and District.",
    add_completion=False,
    no_args_is_help=False,
    invoke_without_command=True,
)


@dataclass(slots=True)
class Runtime:
    config: Config
    json_output: bool
    pretty: bool
    no_color: bool
    quiet: bool
    strict: bool


def _runtime(
    *,
    json_output: bool = False,
    pretty: bool = True,
    no_color: bool = False,
    quiet: bool = False,
    strict: bool = False,
    timeout: float | None = None,
    proxy: str | None = None,
) -> Runtime:
    config = Config.load()
    if timeout is not None:
        config.timeout = timeout
    if proxy:
        config.proxy = proxy
    return Runtime(
        config=config,
        json_output=json_output,
        pretty=pretty,
        no_color=no_color,
        quiet=quiet,
        strict=strict,
    )


def _service(runtime: Runtime, *, platform: str, no_cache: bool, include_previews: bool) -> SeatService:
    return SeatService(
        runtime.config,
        platform=platform,
        no_cache=no_cache,
        include_previews=include_previews,
    )


def _fail(exc: SeatfinderError, runtime: Runtime, command: str) -> None:
    if runtime.json_output:
        print_json(
            envelope(
                command,
                ok=False,
                error=exc.to_dict(),
            ),
            pretty=runtime.pretty,
        )
    else:
        _, err = make_console(quiet=runtime.quiet, no_color=runtime.no_color)
        err.print(f"[red]{exc.code}[/red] {exc.message}")
        if exc.hint:
            err.print(f"[dim]{exc.hint}[/dim]")
        if exc.detail:
            err.print(f"[dim]{exc.detail}[/dim]")
    raise typer.Exit(exc.exit_code)


def _strict_check(bundle, runtime: Runtime, command: str) -> None:
    if not runtime.strict:
        return
    failures = [w for w in bundle.warnings if w.code.startswith("E_")]
    if failures:
        raise PartialError(
            "One or more providers failed",
            detail="; ".join(f"{w.platform or 'provider'}: {w.code}" for w in failures),
        )


@app.callback()
def root(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable terminal colors"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress progress output"),
    version: bool = typer.Option(False, "--version", is_eager=True, help="Print version and exit"),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    ctx.ensure_object(dict)
    ctx.obj["json_output"] = json_output
    ctx.obj["no_color"] = no_color
    ctx.obj["quiet"] = quiet
    if ctx.invoked_subcommand is not None:
        return
    if not available_for_wizard():
        runtime = _runtime(json_output=json_output, no_color=no_color, quiet=quiet)
        exc = UsageError(
            "No command and stdin is not a TTY",
            hint="Pass a command such as: seatfinder seats --city bengaluru --movie doomsday --json",
        )
        _fail(exc, runtime, "wizard")
    run_wizard(Config.load(), no_color=no_color, quiet=quiet)


@app.command("cities")
def cities_command(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    no_color: bool = typer.Option(False, "--no-color"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """List cities known to seatfinder."""
    globals_ = ctx.obj or {}
    json_output = json_output or bool(globals_.get("json_output"))
    no_color = no_color or bool(globals_.get("no_color"))
    quiet = quiet or bool(globals_.get("quiet"))

    runtime = _runtime(json_output=json_output, pretty=pretty, no_color=no_color, quiet=quiet)
    try:
        service = _service(runtime, platform="both", no_cache=True, include_previews=False)
        data = [city.to_dict() for city in service.catalog.cities]
        if json_output:
            print_json(envelope("cities", ok=True, data={"cities": data}), pretty=pretty)
        else:
            out, _ = make_console(quiet=quiet, no_color=no_color)
            print_cities(service.catalog.cities, out)
    except SeatfinderError as exc:
        _fail(exc, runtime, "cities")


@app.command("movies")
def movies_command(
    ctx: typer.Context,
    city: str | None = typer.Option(None, "--city", "-c", help="City name or alias"),
    query: str | None = typer.Option(None, "--query", "-q", help="Title search"),
    platform: str = typer.Option("both", "--platform"),
    json_output: bool = typer.Option(False, "--json"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    no_color: bool = typer.Option(False, "--no-color"),
    quiet: bool = typer.Option(False, "--quiet"),
    timeout: float | None = typer.Option(None, "--timeout"),
    proxy: str | None = typer.Option(None, "--proxy"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Search movies across platforms."""
    globals_ = ctx.obj or {}
    json_output = json_output or bool(globals_.get("json_output"))
    no_color = no_color or bool(globals_.get("no_color"))
    quiet = quiet or bool(globals_.get("quiet"))

    runtime = _runtime(
        json_output=json_output,
        pretty=pretty,
        no_color=no_color,
        quiet=quiet,
        timeout=timeout,
        proxy=proxy,
    )
    service = _service(runtime, platform=platform, no_cache=no_cache, include_previews=False)
    try:
        city_obj, _ = service.resolve(city, None)
        results = service.search_movies(city_obj, query or runtime.config.default_movie)
        if json_output:
            print_json(
                envelope(
                    "movies",
                    ok=True,
                    query={"city": city_obj.name, "query": query or runtime.config.default_movie},
                    data={"city": city_obj.to_dict(), "movies": results},
                ),
                pretty=pretty,
            )
        else:
            out, _ = make_console(quiet=quiet, no_color=no_color)
            print_movies(results, out)
    except SeatfinderError as exc:
        _fail(exc, runtime, "movies")


@app.command("shows")
def shows_command(
    ctx: typer.Context,
    city: str | None = typer.Option(None, "--city", "-c"),
    movie: str | None = typer.Option(None, "--movie", "-m", help="Movie title/alias; defaults to Doomsday"),
    date: str | None = typer.Option(None, "--date", "-d", help="YYYY-MM-DD or earliest"),
    platform: str = typer.Option("both", "--platform"),
    include_previews: bool = typer.Option(False, "--include-previews"),
    strict: bool = typer.Option(False, "--strict"),
    json_output: bool = typer.Option(False, "--json"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    no_color: bool = typer.Option(False, "--no-color"),
    quiet: bool = typer.Option(False, "--quiet"),
    timeout: float | None = typer.Option(None, "--timeout"),
    proxy: str | None = typer.Option(None, "--proxy"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """List showtimes and coarse availability."""
    globals_ = ctx.obj or {}
    json_output = json_output or bool(globals_.get("json_output"))
    no_color = no_color or bool(globals_.get("no_color"))
    quiet = quiet or bool(globals_.get("quiet"))

    runtime = _runtime(
        json_output=json_output,
        pretty=pretty,
        no_color=no_color,
        quiet=quiet,
        strict=strict,
        timeout=timeout,
        proxy=proxy,
    )
    service = _service(
        runtime,
        platform=platform,
        no_cache=no_cache,
        include_previews=include_previews,
    )
    try:
        city_obj, movie_obj = service.resolve(city, movie)
        bundle = service.fetch_shows(city_obj, movie_obj, date)
        _strict_check(bundle, runtime, "shows")
        if json_output:
            print_json(
                envelope(
                    "shows",
                    ok=True,
                    query={
                        "city": city_obj.name,
                        "movie": movie_obj.title,
                        "date": date or "earliest",
                        "platform": platform,
                    },
                    data=bundle.to_dict(),
                ),
                pretty=pretty,
            )
        else:
            out, _ = make_console(quiet=quiet, no_color=no_color)
            out.print(
                f"[bold]{bundle.movie.title}[/bold] · {bundle.city.name} · {bundle.date} · {platform}"
            )
            if bundle.warnings:
                print_warnings(bundle.warnings, out)
            if bundle.show_dates:
                out.print("[dim]Selling dates: " + ", ".join(bundle.show_dates[:14]) + "[/dim]")
            print_shows(bundle.shows, out, date=bundle.date)
    except SeatfinderError as exc:
        _fail(exc, runtime, "shows")


@app.command("seats")
def seats_command(
    ctx: typer.Context,
    city: str | None = typer.Option(None, "--city", "-c"),
    movie: str | None = typer.Option(None, "--movie", "-m"),
    date: str | None = typer.Option(None, "--date", "-d", help="YYYY-MM-DD or earliest"),
    platform: str = typer.Option("both", "--platform"),
    min_seats: int = typer.Option(1, "--min-seats", "-n", help="Seats required together"),
    area: str | None = typer.Option(None, "--area", help="Filter by area/class substring"),
    include_accessible: bool = typer.Option(False, "--include-accessible"),
    limit: int = typer.Option(10, "--limit", help="Max shows to inspect"),
    all_shows: bool = typer.Option(False, "--all", help="Inspect every matching show"),
    detail: str = typer.Option("summary", "--detail", help="summary|full|none"),
    show_map: bool = typer.Option(False, "--map", help="Render a compact seat map"),
    include_previews: bool = typer.Option(False, "--include-previews"),
    strict: bool = typer.Option(False, "--strict"),
    json_output: bool = typer.Option(False, "--json"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    no_color: bool = typer.Option(False, "--no-color"),
    quiet: bool = typer.Option(False, "--quiet"),
    timeout: float | None = typer.Option(None, "--timeout"),
    proxy: str | None = typer.Option(None, "--proxy"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Fetch live seat maps and find contiguous empty seats."""
    globals_ = ctx.obj or {}
    json_output = json_output or bool(globals_.get("json_output"))
    no_color = no_color or bool(globals_.get("no_color"))
    quiet = quiet or bool(globals_.get("quiet"))

    runtime = _runtime(
        json_output=json_output,
        pretty=pretty,
        no_color=no_color,
        quiet=quiet,
        strict=strict,
        timeout=timeout,
        proxy=proxy,
    )
    service = _service(
        runtime,
        platform=platform,
        no_cache=no_cache,
        include_previews=include_previews,
    )
    try:
        city_obj, movie_obj = service.resolve(city, movie)
        bundle = service.fetch_shows(city_obj, movie_obj, date)
        _strict_check(bundle, runtime, "seats")
        results = service.fetch_seats(
            bundle,
            min_seats=min_seats,
            area=area,
            include_accessible=include_accessible,
            limit=None if all_shows else limit,
            detail="full" if show_map and detail == "summary" else detail,
        )
        if json_output:
            data = bundle.to_dict()
            data["shows"] = results
            print_json(
                envelope(
                    "seats",
                    ok=True,
                    query={
                        "city": city_obj.name,
                        "movie": movie_obj.title,
                        "date": date or "earliest",
                        "platform": platform,
                        "min_seats": min_seats,
                        "area": area,
                        "include_accessible": include_accessible,
                    },
                    data=data,
                    warnings=[w.to_dict() for w in bundle.warnings],
                ),
                pretty=pretty,
            )
        else:
            out, _ = make_console(quiet=quiet, no_color=no_color)
            out.print(
                f"[bold]{bundle.movie.title}[/bold] · {bundle.city.name} · {bundle.date} · {platform}"
            )
            if bundle.warnings:
                print_warnings(bundle.warnings, out)
            print_seat_results(results, out, show_map=show_map)
    except SeatfinderError as exc:
        _fail(exc, runtime, "seats")


@app.command("doctor")
def doctor_command(
    ctx: typer.Context,
    live: bool = typer.Option(False, "--live", help="Run live connectivity checks"),
    json_output: bool = typer.Option(False, "--json"),
    pretty: bool = typer.Option(True, "--pretty/--compact"),
    no_color: bool = typer.Option(False, "--no-color"),
    quiet: bool = typer.Option(False, "--quiet"),
    timeout: float | None = typer.Option(None, "--timeout"),
    proxy: str | None = typer.Option(None, "--proxy"),
) -> None:
    """Show configuration and connectivity status."""
    globals_ = ctx.obj or {}
    json_output = json_output or bool(globals_.get("json_output"))
    no_color = no_color or bool(globals_.get("no_color"))
    quiet = quiet or bool(globals_.get("quiet"))

    runtime = _runtime(
        json_output=json_output,
        pretty=pretty,
        no_color=no_color,
        quiet=quiet,
        timeout=timeout,
        proxy=proxy,
    )
    service = _service(runtime, platform="both", no_cache=True, include_previews=False)
    checks: list[dict] = []
    city = service.catalog.resolve_city(runtime.config.default_city or "Bengaluru")
    movie = service.catalog.resolve_movie(runtime.config.default_movie)
    if live:
        for platform in ("bms", "district"):
            started = time.monotonic()
            try:
                provider = service.providers[platform]
                if platform == "bms":
                    regions = provider.regions()
                    checks.append(
                        {
                            "platform": platform,
                            "ok": True,
                            "detail": f"{len(regions)} regions",
                            "ms": round((time.monotonic() - started) * 1000),
                        }
                    )
                else:
                    results = provider.search_movies(city, movie.title)
                    checks.append(
                        {
                            "platform": platform,
                            "ok": True,
                            "detail": f"{len(results)} search results",
                            "ms": round((time.monotonic() - started) * 1000),
                        }
                    )
            except SeatfinderError as exc:
                checks.append(
                    {
                        "platform": platform,
                        "ok": False,
                        "error": exc.to_dict(),
                        "ms": round((time.monotonic() - started) * 1000),
                    }
                )
    data = {
        "version": __version__,
        "python": sys.version.split()[0],
        "config_dir": str(runtime.config.config_dir),
        "cache_dir": str(runtime.config.cache_dir),
        "default_city": city.name,
        "default_movie": movie.title,
        "bms_event_code": movie.bms_event_code,
        "district_content_id": movie.district_content_id,
        "checks": checks,
    }
    ok = all(check.get("ok", True) for check in checks)
    if json_output:
        print_json(envelope("doctor", ok=ok, data=data), pretty=pretty)
    else:
        out, _ = make_console(quiet=quiet, no_color=no_color)
        out.print(f"seatfinder {__version__} · Python {data['python']}")
        out.print(f"default: {city.name} / {movie.title}")
        out.print(f"config: {data['config_dir']}")
        out.print(f"cache:  {data['cache_dir']}")
        for check in checks:
            status = "[green]ok[/green]" if check.get("ok") else "[red]fail[/red]"
            detail = check.get("detail") or (check.get("error") or {}).get("message", "")
            out.print(f"{check['platform']:<9} {status} {detail}")
    if not ok:
        raise typer.Exit(EXIT_UPSTREAM)


@app.command("schema")
def schema_command(
    pretty: bool = typer.Option(True, "--pretty/--compact"),
) -> None:
    """Emit the machine-readable output contract."""
    schema = {
        "schema_version": "1",
        "envelope": {
            "schema_version": "string",
            "command": "string",
            "ok": "boolean",
            "generated_at": "ISO-8601 string",
            "query": "object",
            "data": "object|null",
            "warnings": "Warning[]",
            "error": "Error|null",
            "meta": "object",
        },
        "warning": {"code": "string", "message": "string", "platform": "string|null"},
        "error": {
            "code": "string",
            "message": "string",
            "retryable": "boolean",
            "hint": "string|null",
        },
        "exit_codes": {
            "0": "success",
            "2": "usage/config error",
            "4": "upstream/network/blocked/timeout",
            "5": "schema drift/parse failure",
            "6": "cookies/auth required",
            "7": "partial result with --strict",
        },
        "commands": {
            "cities": {"options": ["--json", "--pretty/--compact", "--no-color", "--quiet"]},
            "movies": {"options": ["--city", "--query", "--platform", "--json"]},
            "shows": {"options": ["--city", "--movie", "--date", "--platform", "--json"]},
            "seats": {
                "options": [
                    "--city",
                    "--movie",
                    "--date",
                    "--platform",
                    "--min-seats",
                    "--area",
                    "--include-accessible",
                    "--limit",
                    "--all",
                    "--detail",
                    "--map",
                    "--json",
                ]
            },
            "watch": {
                "options": [
                    "--city",
                    "--movie",
                    "--date",
                    "--platform",
                    "--min-seats",
                    "--interval",
                    "--timeout",
                    "--ndjson",
                ]
            },
            "doctor": {"options": ["--live", "--json"]},
            "schema": {"options": ["--pretty/--compact"]},
        },
        "show": {
            "platform": "bms|district",
            "show_id": "stable string",
            "movie": "string",
            "city": "string",
            "venue": {"id": "string", "name": "string", "screen": "string"},
            "starts_at": "ISO-8601 string",
            "format": "string",
            "availability": {"status": "string", "total": "int|null", "available": "int|null"},
            "best_blocks": "SeatBlock[]",
            "seat_map": "SeatMap|null",
        },
        "seat_block": {
            "row": "string",
            "seats": "string[]",
            "area_name": "string",
            "score": "number",
            "price": "number|null",
        },
        "seat_map": {
            "summary": {
                "total": "int",
                "available": "int",
                "booked": "int",
                "occupancy_pct": "number",
            },
            "areas": "SeatArea[]",
            "rows": "SeatRow[] (when --detail full)",
        },
        "watch_events": {
            "type": "snapshot|match|error|done",
            "seq": "int",
            "ts": "ISO-8601 string",
            "data": "object",
        },
    }
    print_json(schema, pretty=pretty)


def _parse_duration(value: str) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", value or "")
    if not match:
        raise UsageError(f"Invalid duration: {value}", hint="Use values like 90s, 10m, 2h")
    number = float(match.group(1))
    unit = match.group(2) or "s"
    return number * {"s": 1, "m": 60, "h": 3600}[unit]


@app.command("watch")
def watch_command(
    ctx: typer.Context,
    city: str | None = typer.Option(None, "--city", "-c"),
    movie: str | None = typer.Option(None, "--movie", "-m"),
    date: str | None = typer.Option(None, "--date", "-d"),
    platform: str = typer.Option("both", "--platform"),
    min_seats: int = typer.Option(1, "--min-seats", "-n"),
    area: str | None = typer.Option(None, "--area"),
    include_accessible: bool = typer.Option(False, "--include-accessible"),
    limit: int = typer.Option(5, "--limit"),
    interval: str = typer.Option("120s", "--interval"),
    timeout: str = typer.Option("30m", "--timeout"),
    ndjson: bool = typer.Option(False, "--ndjson", "--json", help="Stream newline-delimited JSON"),
    include_previews: bool = typer.Option(False, "--include-previews"),
    no_color: bool = typer.Option(False, "--no-color"),
    quiet: bool = typer.Option(False, "--quiet"),
    proxy: str | None = typer.Option(None, "--proxy"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Poll until a matching contiguous seat block appears."""
    globals_ = ctx.obj or {}
    ndjson = ndjson or bool(globals_.get("json_output"))
    no_color = no_color or bool(globals_.get("no_color"))
    quiet = quiet or bool(globals_.get("quiet"))

    runtime = _runtime(
        json_output=ndjson,
        no_color=no_color,
        quiet=quiet,
        proxy=proxy,
    )
    service = _service(
        runtime,
        platform=platform,
        no_cache=no_cache,
        include_previews=include_previews,
    )
    try:
        interval_seconds = _parse_duration(interval)
        timeout_seconds = _parse_duration(timeout)
        if interval_seconds < 1:
            raise UsageError("interval must be at least 1s", hint="Use --interval 120s")
        if timeout_seconds < 1:
            raise UsageError("timeout must be at least 1s", hint="Use --timeout 30m")
        deadline = time.monotonic() + timeout_seconds
        city_obj, movie_obj = service.resolve(city, movie)
        seq = 0

        def emit(event_type: str, data: dict) -> None:
            nonlocal seq
            seq += 1
            if ndjson:
                print_json(
                    {
                        "type": event_type,
                        "seq": seq,
                        "ts": now_iso(),
                        "data": data,
                    },
                    pretty=False,
                )
            elif not quiet:
                out, _ = make_console(quiet=False, no_color=no_color)
                out.print(f"[dim]{now_iso()}[/dim] {event_type}: {data.get('message') or data}")

        while True:
            bundle = service.fetch_shows(city_obj, movie_obj, date)
            results = service.fetch_seats(
                bundle,
                min_seats=min_seats,
                area=area,
                include_accessible=include_accessible,
                limit=limit,
                detail="summary",
            )
            matches = [item for item in results if item.get("block_found")]
            payload = {
                "city": city_obj.name,
                "movie": movie_obj.title,
                "date": bundle.date,
                "show_count": len(bundle.shows),
                "matches": [
                    {
                        "show_id": item["show_id"],
                        "platform": item["platform"],
                        "starts_at": item["starts_at"],
                        "venue": item.get("venue"),
                        "blocks": item.get("best_blocks"),
                    }
                    for item in matches
                ],
                "warnings": [w.to_dict() for w in bundle.warnings],
            }
            if matches:
                emit("match", payload)
                raise typer.Exit(0)
            emit("snapshot", payload)
            if time.monotonic() + interval_seconds > deadline:
                emit("done", {"message": "timeout", "reason": "no matching seats before timeout"})
                raise typer.Exit(EXIT_UPSTREAM)
            time.sleep(interval_seconds)
    except SeatfinderError as exc:
        if ndjson:
            emit_error = envelope("watch", ok=False, error=exc.to_dict())
            print_json({"type": "error", "seq": 0, "ts": now_iso(), "data": emit_error}, pretty=False)
            raise typer.Exit(exc.exit_code)
        _fail(exc, runtime, "watch")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
