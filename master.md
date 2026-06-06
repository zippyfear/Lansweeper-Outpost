# Lansweeper Outpost — master.md

> **Canonical source of truth for the project.** If a fact about Outpost's
> architecture, version, or component status lives anywhere, it lives here
> first. Every release updates this file.

| | |
|---|---|
| **Codename** | Outpost (working — final product name is a marketing decision, TBD) |
| **Owner** | Lansweeper |
| **Project version** | `0.2.0` |
| **Last updated** | 2026-06-06 |
| **Repo** | https://github.com/zippyfear/Lansweeper-Outpost |

---

## 1. Mission

A lightweight, self-hostable **hub-and-sensor** asset discovery system.
Customers deploy it anywhere — including fully air-gapped ("black site")
environments — with **no mandatory outbound communication**. Asset data is
stored on a customer-controlled Linux host. Connecting to an existing
Lansweeper Cloud deployment is **optional and later** (see Roadmap, Phase 4).

This directly answers the data-residency / no-phone-home objections common in
regulated environments (healthcare, public sector).

---

## 2. Architecture

Three logical components. **They are always logically separate; where they run
is purely a deployment topology** (see §4).

```
        ┌────────────┐      ┌────────────┐
        │  Sensor A  │      │  Sensor B  │      (one per network segment)
        │  segment 1 │      │  segment 2 │
        └─────┬──────┘      └─────┬──────┘
              │ ship (mTLS+token, Phase 2)
              ▼                    ▼
          ┌─────────────────────────────┐
          │            Hub              │  receive · correlate/dedupe ·
          │   (customer Linux host)     │  store · serve API
          └──────────────┬──────────────┘
                         │
                  ┌──────▼──────┐
                  │  Dashboard  │  local web UI
                  └─────────────┘
        (optional, Phase 4)  →  Lansweeper Cloud sync
```

- **Sensor** — collects asset data on a network segment. Active discovery
  (ARP sweep + TCP scan today; SNMP, banners, OS guess next) and passive
  discovery (wire sniffing). Buffers locally and (Phase 2) ships to the Hub.
  Never depends on Hub reachability — store-and-forward.
- **Hub** — receives sensor data, correlates/dedupes into canonical assets,
  stores them, serves the dashboard + API. Lives on the customer host.
- **Dashboard** — local web UI over the Hub's API. Dark-themed SPA, branded
  later per Lansweeper guidelines.

---

## 3. Component status

| Component | Status | Version | Notes |
|-----------|--------|---------|-------|
| Asset schema | **Locked** | wire `0.1` | Shared contract. `outpost/schema.py` + `outpost/schema.json`. |
| Sensor | **Prototype** | `0.2.0` | ARP+TCP active, passive sniff. Emits schema-valid Observations. |
| Hub | Not started | — | Phase 2. |
| Dashboard | Not started | — | Phase 3. |
| Cloud sync | Not started | — | Phase 4, optional. |

---

## 4. Deployment topologies (same codebase)

- **Single-VM "lite"** (default): sensor + hub + dashboard all on one VM,
  talking over localhost. The minimal standalone / air-gap install.
- **Distributed**: a sensor per segment fanning into a central hub; dashboard
  alongside the hub. For larger / multi-subnet environments.

Nothing in the code assumes co-location.

---

## 5. Key technical decisions (snapshot — full rationale in `docs/DECISIONS.md`)

- Sensor prototyped in **Python** for velocity; **Go** is the likely production
  rewrite target (single static binary, small footprint). Discovery behaviour
  and the asset schema are the portable, valuable parts.
- Sensor uses **local SQLite** as a store-and-forward buffer (now an
  *observation* buffer, not an asset store — see schema below).
- One **shared schema contract** (`outpost/schema.py` + `outpost/schema.json`)
  with a two-level model: sensors emit **Observations**, the hub correlates
  them into **Assets**. Identity is **conservative** (MAC/strong-ID first,
  IP-only sightings provisional). Full spec in `docs/SCHEMA.md`.
- Code lives in one installable **`outpost/`** package (sensor today, hub/
  dashboard later) so every component validates against the same contract.
- **ARP** discovery is local-subnet only — this is *why* multiple sensors exist.
- Hub-scale storage (likely **PostgreSQL/TimescaleDB**) decided at Phase 2.

---

## 6. Document map

- `master.md` — this file. Canonical state.
- `CHANGELOG.md` — every change, semver-tagged.
- `docs/DECISIONS.md` — the *why* behind each choice (rollback ideas, not just code).
- `docs/ROADMAP.md` — staged future builds.
- `docs/SCHEMA.md` — the asset data contract (Observation + Asset). **Accepted, wire v0.1.**
- Per-source-file headers carry their own `__version__` + change history.

---

## 7. Versioning & workflow conventions

- **Semantic Versioning** project-wide (`MAJOR.MINOR.PATCH`).
- Every source file carries a `__version__` and a top-of-file change-history block.
- Small, frequent commits; conventional commit messages (`feat:`, `fix:`, `docs:`…).
- Every release updates `master.md` (§3 status, §1 version) and `CHANGELOG.md`.
- Pushes/deploys are run by the human on the dev server (which holds the
  GitHub + SSH credentials). The assistant produces commit-ready bundles.
