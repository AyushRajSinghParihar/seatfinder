from seatfinder.blocks import find_blocks
from seatfinder.models import (
    ACCESSIBLE,
    AVAILABLE,
    BOOKED,
    UNKNOWN,
    Seat,
    SeatArea,
    SeatMap,
    SeatRow,
)


def _seat(row, number, position, status=AVAILABLE, area="A", area_name="PREMIUM"):
    return Seat(
        row=row,
        number=str(number),
        grid_position=position,
        status=status,
        area_code=area,
        area_name=area_name,
        price=500,
    )


def _map(rows):
    return SeatMap(
        platform="test",
        show_id="test|1",
        fetched_at="2026-01-01T00:00:00+05:30",
        areas=[SeatArea(code="A", name="PREMIUM", price=500)],
        rows=rows,
    )


def test_prefers_back_row_and_returns_sorted_seat_numbers():
    seat_map = _map(
        [
            SeatRow(
                label="B",
                order=2,
                seats=[
                    _seat("B", 1, 1),
                    _seat("B", 2, 2),
                    _seat("B", 3, 3),
                ],
            ),
            SeatRow(
                label="A",
                order=1,
                seats=[
                    _seat("A", 1, 1),
                    _seat("A", 2, 2),
                    _seat("A", 3, 3),
                ],
            ),
        ]
    )
    blocks = find_blocks(seat_map, min_seats=2)
    assert blocks[0].row == "A"
    assert blocks[0].seats == ["1", "2", "3"]


def test_aisle_gap_breaks_adjacency():
    seat_map = _map(
        [
            SeatRow(
                label="A",
                order=1,
                seats=[
                    _seat("A", 1, 1),
                    _seat("A", 2, 2),
                    _seat("A", 4, 4),
                    _seat("A", 5, 5),
                ],
            )
        ]
    )
    assert find_blocks(seat_map, min_seats=3) == []
    blocks = find_blocks(seat_map, min_seats=2)
    assert {tuple(block.seats) for block in blocks} == {("1", "2"), ("4", "5")}


def test_booked_and_unknown_seats_split_a_block():
    seat_map = _map(
        [
            SeatRow(
                label="A",
                order=1,
                seats=[
                    _seat("A", 1, 1),
                    _seat("A", 2, 2, BOOKED),
                    _seat("A", 3, 3, UNKNOWN),
                    _seat("A", 4, 4),
                ],
            )
        ]
    )
    blocks = find_blocks(seat_map, min_seats=2)
    assert blocks == []


def test_accessible_only_counts_when_requested():
    seat_map = _map(
        [
            SeatRow(
                label="A",
                order=1,
                seats=[
                    _seat("A", 1, 1),
                    _seat("A", 2, 2, ACCESSIBLE),
                    _seat("A", 3, 3),
                ],
            )
        ]
    )
    assert find_blocks(seat_map, min_seats=3) == []
    blocks = find_blocks(seat_map, min_seats=3, include_accessible=True)
    assert blocks[0].seats == ["1", "2", "3"]


def _stacked_rows(count=10):
    rows = []
    for order in range(1, count + 1):
        label = chr(64 + order)
        rows.append(
            SeatRow(
                label=label,
                order=order,
                seats=[_seat(label, 5, 5), _seat(label, 6, 6)],
            )
        )
    return _map(rows)


def test_center_preference_picks_slightly_behind_the_middle():
    blocks = find_blocks(_stacked_rows(), min_seats=2, prefer="center")
    assert blocks[0].row == "D"


def test_back_preference_picks_back_rows():
    blocks = find_blocks(_stacked_rows(), min_seats=2, prefer="back")
    assert blocks[0].row == "A"


def test_front_preference_picks_front_rows():
    blocks = find_blocks(_stacked_rows(), min_seats=2, prefer="front")
    assert blocks[0].row in {"I", "J"}


def test_invalid_preference_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        find_blocks(_stacked_rows(), prefer="aisle")
