# Roadmap

Staged build plan. Phases are sequential in priority but items within a phase
can move. This is intent, not a contract — `CHANGELOG.md` records what actually
shipped.

---

## Phase 0 — Sensor prototype  ✅ (v0.1.0)
- ARP sweep + TCP connect scan (active), passive sniffer, SQLite buffer, CLI.

## Phase 1 — Harden the sensor & lock the asset schema  ◀ in progress
- ✅ **Canonical asset schema** — locked at wire v0.1 (`docs/SCHEMA.md`,
  `outpost/schema.py`, `outpost/schema.json`).
- ✅ **Sensor emits schema-valid Observations** (v0.2.0).
- **SNMP** (v2c/v3) polling — sysDescr, interfaces, ARP/CAM tables. Critical for
  switches, printers, and OT/medical gear that ignore TCP scans.
- **Service/version banners** — populate `Service.name`/`product`/`version`.
- **OS guess** — lightweight fingerprint (TTL/port heuristics to start).
- Config file (in addition to CLI flags), structured logging, robust OUI db.

## Phase 2 — Hub & sensor↔hub protocol
- Hub service: ingest API, asset store (PostgreSQL/TimescaleDB TBD).
- Transport: mutual TLS + per-sensor provisioning token.
- Sensor gains an outbound **shipper** draining the local SQLite buffer.
- **Correlation/de-dup** by MAC across sensors; asset **change history**.
- Multi-sensor fan-in.

## Phase 3 — Dashboard
- Local dark-themed SPA over the hub API (reuse Flow Dashboard patterns).
- Asset list, asset detail, search/filter, change timeline.
- Lansweeper brand pass (per brand skill) before any customer-facing build.

## Phase 4 — Optional Lansweeper Cloud sync
- Opt-in connector to push assets to an existing Lansweeper Cloud deployment.
- Strictly optional; standalone remains fully functional without it.

---

## Cross-cutting (slotted as needed)
- Packaging: Docker/Podman images + native install; single-VM "lite" installer.
- AuthN/AuthZ for dashboard + API; RBAC.
- Credentialed deep scans (SSH/WMI) for OS/software inventory.
- Go rewrite of the sensor (per ADR-0003) once Phase 1 schema/behaviour locks.
- Health/heartbeat + sensor management from the hub.
