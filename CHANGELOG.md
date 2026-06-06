# Changelog

All notable changes to Lansweeper Outpost are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/); the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]
- (nothing yet)

## [0.2.0] — 2026-06-06
### Added
- **Shared asset schema** — locked at wire version `0.1`.
  - `outpost/schema.py`: `Service`, `Interface`, `Observation`, `Asset`
    dataclasses with `to_dict`/`from_dict` and JSON Schema validation.
  - `outpost/schema.json`: authoritative JSON Schema ($defs for each type),
    forward-compatible (unknown fields permitted/ignored).
  - Two-level model documented in `docs/SCHEMA.md` (Observation → Asset),
    conservative identity strategy, and the asset `type` taxonomy.
- Project is now an installable package (`pyproject.toml`, `outpost/`).

### Changed
- **Sensor refactored to v0.2.0** to emit canonical, schema-validated
  Observations: services modelled as `Service` objects, `sensor_id` added,
  `--dump` now outputs schema-valid Observation payloads (validated on read).
- Sensor local store reframed as an **observation buffer**
  (`AssetStore` → `ObservationStore`, table `assets` → `observations`).
- **Run command changed:** `pip install -e .` then
  `sudo python3 -m outpost.sensor --cidr ...` (was `python3 sensor.py`).
- Repo layout: `sensor/` directory removed; code consolidated under `outpost/`.

### Decisions
- ADR-0009 (identity strategy) and ADR-0010 (package layout) recorded in
  `docs/DECISIONS.md`. `docs/SCHEMA.md` moved DRAFT → ACCEPTED.

## [0.1.0] — 2026-06-06
### Added
- **Sensor prototype** (`sensor/sensor.py`, file v0.1.0):
  - Active discovery: ARP sweep of target subnet(s) for live hosts + MACs,
    followed by TCP connect-scan of a common port set.
  - Reverse DNS and best-effort OUI → vendor lookup per host.
  - Passive discovery: background sniffer registers hosts seen on the wire
    and refreshes `last_seen` without generating traffic.
  - Local SQLite asset cache with merge logic (a host seen by both active and
    passive methods unions its ports and discovery methods).
  - CLI: `--cidr` (repeatable), `--iface`, `--ports`, `--interval`, `--db`,
    `--once`, `--no-passive`, `--dump`.
- **Project scaffold & conventions**: `master.md`, this changelog,
  `docs/DECISIONS.md`, `docs/ROADMAP.md`, top-level `README.md`, `.gitignore`.
- Established semantic versioning, per-file version headers, and the
  documentation discipline (master / changelog / decisions / roadmap).

### Known limitations (by design at this stage)
- ARP discovery is local-subnet only (routed ranges → use additional sensors).
- No SNMP, no service/version banners, no OS fingerprinting yet.
- Sensor has no Hub-shipping path yet — fully standalone.
- Assets keyed by IP; no cross-sensor de-duplication yet.

[Unreleased]: https://github.com/zippyfear/Lansweeper-Outpost/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/zippyfear/Lansweeper-Outpost/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zippyfear/Lansweeper-Outpost/releases/tag/v0.1.0
