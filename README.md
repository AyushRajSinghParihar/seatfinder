# seatfinder

**Find live empty seats on BookMyShow and District from the terminal.**

`seatfinder` is a read-only CLI for checking real seat maps, finding contiguous
empty seats, and watching for availability changes. It defaults to
**Avengers: Doomsday**, works in both TTY and agent/non-interactive mode, and
never books, holds seats, logs in, or stores account data.

[![Python](https://img.shields.io/badge/python-3.11--3.13-blue)](pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Read-only](https://img.shields.io/badge/status-read--only-success)](#safety)

## Features

- Live seat maps from **BookMyShow** and **District**.
- **District** uses the public seat layout JSON.
- **BookMyShow** decrypts the same AES seat-layout blob the web client uses.
  No Playwright, Selenium, or browser required.
- Interactive city → movie → date wizard with **Avengers: Doomsday** as the
  movie default.
- Non-interactive `--json` mode with a stable schema, stable show IDs, typed
  warnings, and documented exit codes.
- Contiguous block finder: `--min-seats 4`, seat-class filtering, accessible
  seats, and seat-area preference.
- Default preference is **centre, slightly behind the middle**; choose
  `center`, `back`, `front`, or `any`.
- Live progress while seat maps are fetched (stderr; suppressed with `--json`
  or `--quiet`).
- `watch` mode streams NDJSON snapshots and matches until seats appear.
- City fallback: unknown cities are resolved from BookMyShow's live region
  directory.
- Retries, TLS-profile rotation, proxy support, and human-paced requests.

## Install

From this repository:

```bash
uv tool install --python 3.12 "git+https://github.com/AyushRajSinghParihar/seatfinder.git"
# or
pipx install --python 3.12 "git+https://github.com/AyushRajSinghParihar/seatfinder.git"
```

From a local clone:

```bash
git clone https://github.com/AyushRajSinghParihar/seatfinder.git
cd seatfinder
uv tool install --python 3.12 .
```

Python 3.11–3.13 is supported. BookMyShow sits behind Cloudflare; `seatfinder`
uses `curl_cffi` with a Safari/Firefox TLS fingerprint instead of a browser.

## Quick start

Interactive:

```bash
seatfinder
```

You are asked for a city first, then a movie with **Avengers: Doomsday**
pre-filled. Press Enter to accept each default.

Agent-friendly:

```bash
seatfinder seats \
  --city bengaluru \
  --movie doomsday \
  --date earliest \
  --min-seats 2 \
  --json
```

More examples:

```bash
# What is selling, with coarse availability
seatfinder shows --city delhi --movie doomsday --date earliest

# Live seat maps for both platforms, best contiguous blocks
seatfinder seats --city delhi --movie doomsday --date 2026-12-17 \
  --platform both --min-seats 2 --limit 4 --json

# Human-readable seat grid
seatfinder seats --city delhi --movie doomsday --date 2026-12-17 \
  --platform district --limit 1 --min-seats 2 --map

# Watch until two adjacent seats appear
seatfinder watch --city bengaluru --movie doomsday \
  --min-seats 2 --interval 120s --timeout 30m --ndjson
```

### Sample output

```text
Avengers: Doomsday · Delhi · 2026-12-17 · district
1. district · 00:35 · Cinepolis Unity City Centre, Dwarka
   MS INFINITY VISION 3D · SCREEN 03
   seats: 48 available / 129 total · 62.79% booked
   best blocks: row E [1, 2, 3] PREMIUM | row B [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] EXECUTIVE
  J oo
  J ## ## #####
  I ## ## ## ##    #.
  H ######ooooo  ###.
  G ###########  ####
  F ###########  ###.
  E ###########  #...
  D ###########  ....
  C ......#.#..
  B ...........
  A ...........
Legend: . available  o accessible  # taken  ? unknown  * best seat
```

## Commands

| Command | What it does |
| --- | --- |
| `seatfinder` | Interactive wizard |
| `seatfinder cities` | List bundled cities |
| `seatfinder movies --city <city>` | Search movies across platforms |
| `seatfinder shows --city <city> --movie <title>` | Showtimes and coarse availability |
| `seatfinder seats --city <city> --movie <title>` | Live seat maps and best blocks |
| `seatfinder watch --city <city> --movie <title>` | Poll until matching seats appear |
| `seatfinder doctor --live` | Check config and live connectivity |
| `seatfinder schema` | Print the machine-readable output contract |

Common flags:

| Flag | Meaning |
| --- | --- |
| `--date earliest\|YYYY-MM-DD` | Earliest bookable date or an explicit date |
| `--platform both\|bms\|district` | Provider selection |
| `--min-seats N` | Require N contiguous seats |
| `--area "PREMIUM"` | Filter by seat class |
| `--include-accessible` | Count wheelchair/companion seats in blocks |
| `--prefer center\|back\|front\|any` | Seat-area preference (default `center`, slightly behind the middle) |
| `--detail summary\|full\|none` | Seat-map payload size |
| `--map` | Render a compact terminal seat map |
| `--limit N` / `--all` | Control how many shows get a seat-map request |
| `--strict` | Fail when one provider fails |
| `--proxy` | Use an HTTP/SOCKS proxy |

## JSON contract

`--json` emits exactly one envelope on `stdout`; progress and warnings go to
`stderr`.

```json
{
  "schema_version": "1",
  "command": "seats",
  "ok": true,
  "generated_at": "2026-11-25T19:30:00+05:30",
  "query": {
    "city": "Delhi",
    "movie": "Avengers: Doomsday",
    "min_seats": 2
  },
  "data": {
    "shows": [
      {
        "platform": "district",
        "show_id": "district|1102253|7753|2026-12-17",
        "starts_at": "2026-12-17T00:35+05:30",
        "best_blocks": [
          {
            "row": "E",
            "seats": ["1", "2", "3"],
            "area_name": "PREMIUM",
            "price": 850.0
          }
        ],
        "block_found": true,
        "seat_map": {
          "summary": {
            "total": 129,
            "available": 48,
            "booked": 81,
            "occupancy_pct": 62.79
          }
        }
      }
    ]
  },
  "warnings": [],
  "error": null,
  "meta": {}
}
```

- `show_id` is stable across runs.
- Seat statuses: `available`, `accessible`, `booked`, `held`, `unknown`.
- `best_blocks[]` lists contiguous free seats; `block_found` says whether
  `--min-seats` was satisfied.
- `--detail full` adds `seat_map.rows`; omit it to keep output small.
- `seatfinder schema` describes the full contract for agents.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, including “no seats found” |
| `2` | Usage or configuration error |
| `4` | Upstream/network/blocked/timeout |
| `5` | Schema drift or parse failure |
| `6` | Cookies/auth required |
| `7` | Partial result with `--strict` |

## Watch mode

```bash
seatfinder watch \
  --city bengaluru \
  --movie doomsday \
  --min-seats 2 \
  --interval 120s \
  --timeout 30m \
  --ndjson
```

Events are newline-delimited JSON: `snapshot`, `match`, `error`, and `done`.
`watch` exits `0` on a match and `4` on timeout.

## Configuration

`~/.config/seatfinder/config.toml`:

```toml
default_city = "Bengaluru"
default_movie = "Avengers: Doomsday"
default_platform = "both"
timeout = 30
cache_ttl = 300
# proxy = "socks5h://127.0.0.1:1080"
# bms_tls_profiles = ["safari180", "safari170", "firefox144"]
# bms_seat_key = "..."
# district_cookie = "..."
```

Environment overrides:

`SEATFINDER_CITY`, `SEATFINDER_MOVIE`, `SEATFINDER_PLATFORM`,
`SEATFINDER_TIMEOUT`, `SEATFINDER_PROXY`, `SEATFINDER_BMS_SEAT_KEY`,
`SEATFINDER_DISTRICT_COOKIE`.

## Seat scoring

Seats are scored with simple geometry, not hardcoded rows:

- Row depth is normalised from front (`0`) to back (`1`).
- `center` targets depth `0.62`, i.e. centre and a little higher than the
  middle; `back` targets `0.95`; `front` targets `0.12`; `any` skips row bias.
- Distance from the row's horizontal centre breaks ties.
- Only contiguous seats count, and aisle gaps are respected.

The winning blocks look like “row E, seats 1–3” instead of a single arbitrary
empty seat.

## How it works

- **District** exposes a movie-session API and a `select-seat` endpoint. The
  seat response is plain JSON; `GridSeatNum` gaps represent aisles.
- **BookMyShow** server-renders showtimes into `window.__INITIAL_STATE__`.
  Seat maps come from its web-client endpoint as an AES-CBC blob, which
  `seatfinder` decrypts and parses into rows, areas, prices, and statuses.
- Availability is a snapshot. The tool spaces seat-map requests and never
  books, holds, or touches checkout.
- Unknown cities are resolved from BookMyShow's live region directory, so you
  are not limited to the bundled city list.

## Safety

`seatfinder` is observe-only:

- It never logs into an account.
- It never books or holds seats.
- It never touches payment or checkout.
- It reads public availability only, at a human-like pace.
- It is not affiliated with BookMyShow or District.

Use it responsibly and respect the sites' terms and rate limits. A home
connection in India is the most reliable network for BookMyShow.

## Development

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
uvx ruff check src tests
.venv/bin/pytest -q
```

Tests are offline and use captured fixtures. `AGENTS.md` contains the
non-interactive contract for coding agents.

## License

MIT
