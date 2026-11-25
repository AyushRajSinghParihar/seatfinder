# Agent usage

Use `seatfinder` in non-interactive mode. Never rely on prompts.

## Canonical commands

```bash
seatfinder seats --city <city> --movie doomsday --date earliest --min-seats 2 --json
seatfinder shows --city <city> --movie doomsday --date YYYY-MM-DD --json
seatfinder movies --city <city> --query "<title>" --json
seatfinder cities --json
seatfinder schema
seatfinder doctor --live --json
```

## Contract

- Output is one JSON envelope on `stdout`; logs are on `stderr`.
- Check `.ok`, then read `.data`; check `.warnings`.
- `show_id` is stable across runs.
- Seat statuses: `available`, `accessible`, `booked`, `held`, `unknown`.
- `best_blocks[]` contains contiguous free seats; `block_found` says whether
  `--min-seats` was satisfied.
- `--detail full` adds `seat_map.rows`; omit it to keep output small.
- Exit codes: 0 success, 2 usage, 4 upstream/blocked/timeout, 5 schema drift,
  6 auth/cookies, 7 partial with `--strict`.
- Do not scrape faster than a person refreshing. Prefer `watch --interval 120s`
  over tight loops.
- `seatfinder` never books or holds seats. Do not add booking automation.
