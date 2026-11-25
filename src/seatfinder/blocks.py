"""Contiguous-seat discovery and compact seat-map rendering."""

from __future__ import annotations

from .models import ACCESSIBLE, AVAILABLE, Seat, SeatBlock, SeatMap


def find_blocks(
    seat_map: SeatMap,
    *,
    min_seats: int = 1,
    area: str | None = None,
    include_accessible: bool = False,
    limit: int = 5,
) -> list[SeatBlock]:
    if min_seats < 1:
        raise ValueError("min_seats must be at least 1")
    allowed_statuses = {AVAILABLE}
    if include_accessible:
        allowed_statuses.add(ACCESSIBLE)
    wanted_area = (area or "").strip().casefold()
    blocks: list[SeatBlock] = []
    total_rows = max((row.order for row in seat_map.rows), default=1) or 1

    for row in seat_map.rows:
        run: list[Seat] = []
        if not row.seats:
            continue
        row_center = (min(seat.grid_position for seat in row.seats) + max(seat.grid_position for seat in row.seats)) / 2

        def flush() -> None:
            nonlocal run
            if len(run) >= min_seats:
                blocks.append(_make_block(run, total_rows, row.order, row_center))
            run = []

        previous_position: int | None = None
        for seat in row.seats:
            if wanted_area and wanted_area not in seat.area_name.casefold():
                flush()
                previous_position = None
                continue
            is_open = seat.status in allowed_statuses
            is_adjacent = (
                previous_position is not None
                and seat.grid_position == previous_position + 1
                and (not run or run[-1].area_code == seat.area_code)
            )
            if is_open and is_adjacent:
                run.append(seat)
            elif is_open:
                flush()
                run = [seat]
            else:
                flush()
            previous_position = seat.grid_position
        flush()

    blocks.sort(key=lambda block: block.score, reverse=True)
    return blocks[:limit]


def _make_block(seats: list[Seat], total_rows: int, order: int, row_center: float) -> SeatBlock:
    row = seats[0].row
    # Lower row order is the back of the auditorium in both providers.
    positions = [seat.grid_position for seat in seats]
    centre = (min(positions) + max(positions)) / 2
    depth_score = (max(total_rows, 1) - order + 1) / max(total_rows, 1)
    span = max(positions) - min(positions) + 1 if positions else 1
    centrality = 1.0 / (1.0 + abs(centre - row_center) / max(span, 1))
    score = round(depth_score * 0.75 + centrality * 0.25, 6)
    prices = [seat.price for seat in seats if seat.price is not None]
    ordered = sorted(
        (seat.number for seat in seats),
        key=lambda number: (int(number) if number.isdigit() else 10**9, number),
    )
    return SeatBlock(
        row=row,
        seats=ordered,
        area_code=seats[0].area_code,
        area_name=seats[0].area_name,
        score=score,
        price=min(prices) if prices else None,
    )


def render_seat_map(seat_map: SeatMap, *, max_width: int = 100) -> str:
    """Render a compact monospace map. Available seats are '.', taken '#'."""

    lines: list[str] = []
    symbols = {
        AVAILABLE: ".",
        ACCESSIBLE: "o",
        "booked": "#",
        "held": "#",
        "unknown": "?",
    }
    for row in seat_map.rows:
        seats = row.seats
        if not seats:
            continue
        low = min(seat.grid_position for seat in seats)
        high = max(seat.grid_position for seat in seats)
        width = high - low + 1
        if width > max_width:
            continue
        cells = [" "] * width
        for seat in seats:
            index = seat.grid_position - low
            if 0 <= index < width:
                cells[index] = "*" if seat.is_best else symbols.get(seat.status, "?")
        line = "".join(cells).rstrip()
        lines.append(f"{row.label:>3} {line}")
    if not lines:
        return "(no seat rows returned)"
    legend = "Legend: . available  o accessible  # taken  ? unknown  * best seat"
    return "\n".join(lines) + "\n" + legend
