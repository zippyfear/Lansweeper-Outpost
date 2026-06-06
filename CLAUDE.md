# CLAUDE.md — project memory & handoff

You are a Claude Code instance operating against this repo on the dev server.
You inherit **nothing** from the design chats — the repo is the only bridge
(ADR-0008). Read `master.md` and `docs/DECISIONS.md` before acting.

## Source of truth
- **`master.md`** is canonical.
- **`docs/DECISIONS.md`** holds ADR-0001..0010. Honor them; to change a
  decision, append a superseding ADR — never delete or silently contradict.
- **`docs/SCHEMA.md`** is the data contract — **ACCEPTED, wire `0.1`**. The
  schema is codified in `outpost/schema.py` (+ `outpost/schema.json`); both
  sensor and hub import and validate against it. Don't fork the shape.

## Hard boundaries (do not cross)
- **No phone-home.** No component may require outbound internet egress; never
  bake in a mandatory outbound call. Cloud sync is Phase 4 and optional. (ADR-0001)
- **Sensors are identity-agnostic.** They emit Observations keyed by IP and make
  no cross-sensor/MAC correlation — that's the hub's job. (ADR-0005, ADR-0009)
- **Credential / action boundary.** The human runs all GitHub pushes and server
  deploys (they hold the creds). You author code/docs and produce commit-ready
  bundles + exact commands. The autonomous agentic loop runs via Claude Code
  locally, but push/deploy/delete stay with the human. (ADR-0008)
- **Never commit runtime DB files** (`*.db`) — gitignored buffer, not state.

## Conventions
- SemVer project-wide; per-file version headers + change history. Every release
  touches `master.md` and `CHANGELOG.md`. (ADR-0007)
- Keep discovery logic and schema cleanly separable — the production Go rewrite
  (ADR-0003) should be a mechanical port, not a redesign. Python is prototype;
  Go is the production target once behaviour + schema are locked.
- Components stay logically separate even on one VM; never assume sensor and hub
  share a host. (ADR-0002)

## Layout & run
```
master.md            source of truth — read first
CHANGELOG.md         semver history (currently 0.2.0)
docs/SCHEMA.md       Observation + Asset contract (ACCEPTED, wire 0.1)
docs/DECISIONS.md    ADR-0001..0010
docs/ROADMAP.md      phased plan
outpost/             installable package
  schema.py          Service / Interface / Observation / Asset + validation
  schema.json        authoritative JSON Schema
  sensor.py          sensor @ 0.2.0 (active ARP+TCP, passive sniff, SQLite buffer)
pyproject.toml       deps: scapy, jsonschema
```

Install & run the sensor:
```bash
pip install -e .
sudo python3 -m outpost.sensor --cidr 192.168.2.0/24 --iface eth0
sudo python3 -m outpost.sensor --cidr 10.10.0.0/24 --once   # single scan, no passive
python3 -m outpost.sensor --dump                            # emit schema-valid Observations
```

## Current state / next step
- Released **0.2.0**: schema accepted and codified; sensor refactored to emit
  canonical, validated Observations; consolidated into the `outpost/` package.
- Next per `docs/ROADMAP.md`: the **hub** — an ingest path that drains sensor
  buffers and performs the cross-sensor / strong-identifier correlation that
  ADR-0009 specifies (provisional IP-only assets, merge as better data arrives).
```bash
# if the repo isn't initialized on the server yet:
git init && git add . && git commit -m "chore: import Lansweeper-Outpost v0.2.0"
```
