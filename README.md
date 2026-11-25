# seatfinder

Find live empty seats on **BookMyShow** and **District** from the terminal.

`seatfinder` is read-only: it never books, holds seats, logs in, or stores
account data. It reads the same public availability the websites render, at a
human-like pace.

## Install

```bash
uv tool install .
# or
pipx install .
```

Python 3.11–3.13 is supported. The BookMyShow client uses a Safari/Firefox TLS
fingerprint through `curl_cffi`; no browser is required.

## Quick start

Interactive:

```bash
seatfinder
```

Defaults: city is asked first, then the movie with **Avengers: Doomsday**
selected. Press Enter to accept each default.

Agent-friendly (no prompts):

```bash
seatfinder seats \
  --city bengaluru \
  --movie doomsday \
  --date 2026-12-17 \
  --min-seats 2 \
  --json
```

Other commands:

```bash
seatfinder cities
seatfinder movies --city bengaluru --query doomsday
seatfinder shows --city bengaluru --movie doomsday --date earliest
seatfinder doctor --live
seatfinder schema
```

## Output

Human mode prints compact tables and seat blocks. `--json` emits one stable
envelope:

```json
{
  "schema_version": "1",
  "command": "seats",
  "ok": true,
  "generated_at": "2026-11-25T19:30:00+05:30",
  "query": {},
  "data": {},
  "warnings": [],
  "error": null,
  "meta": {}
}
```

`stdout` contains data only. Progress and warnings go to `stderr`.

Useful flags:

- `--json` machine-readable output
- `--min-seats N` require N contiguous seats
- `--area "PREMIUM"` filter by seat class
- `--include-accessible` include wheelchair/companion seats in blocks
- `--detail summary|full|none` include the full seat grid only when needed
- `--map` render a compact terminal seat map
- `--limit N` / `--all` control how many shows get a seat-map request
- `--strict` fail when one provider fails
- `--proxy` / `SEATFINDER_PROXY` for a proxy

Exit codes: `0` success, `2` usage, `4` upstream/blocked/timeout, `5` schema
drift, `6` cookies/auth required, `7` partial result with `--strict`.

## Watch mode

```bash
seatfinder watch --city bengaluru --movie doomsday --min-seats 2 --interval 120s --timeout 30m --ndjson
```

`watch` streams `snapshot`, `match`, `error`, and `done` events. It exits `0`
when a matching block appears and `4` on timeout.

## Configuration

`~/.config/seatfinder/config.toml`:

```toml
default_city = "Bengaluru"
default_movie = "Avengers: Doomsday"
default_platform = "both"
timeout = 30
cache_ttl = 300
# proxy = "socks5h://127.0.0.1:1080"
# bms_tls_profiles = ["safari180", "firefox144"]
# bms_seat_key = "..."
# district_cookie = "..."
```

Environment overrides: `SEATFINDER_CITY`, `SEATFINDER_MOVIE`,
`SEATFINDER_PLATFORM`, `SEATFINDER_TIMEOUT`, `SEATFINDER_PROXY`,
`SEATFINDER_BMS_SEAT_KEY`, `SEATFINDER_DISTRICT_COOKIE`.

## Notes and limits

- BookMyShow uses an undocumented web-client seat endpoint. If its encryption
  key rotates, set `SEATFINDER_BMS_SEAT_KEY` or update the provider; the tool
  fails loudly rather than reporting zero seats.
- BookMyShow may block datacenter IPs. A home connection in India is safest.
- District PVR/INOX layouts can be approximate; a warning is emitted.
- Availability is a snapshot. Re-run or use `watch` for changes.
- Not affiliated with BookMyShow or District. Use responsibly and respect
  their terms and rate limits.
