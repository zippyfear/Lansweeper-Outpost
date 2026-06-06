# Lansweeper Outpost

> Working codename. Final product name TBD.

A lightweight, self-hostable **hub-and-sensor** asset discovery system.
Deploy it anywhere — including fully air-gapped environments — with **no
mandatory outbound communication**. Asset data stays on a customer-controlled
Linux host. Optional Lansweeper Cloud sync comes later.

**Project version:** `0.2.0` · **Status:** sensor prototype emitting the
locked v0.1 asset schema. Canonical project state lives in
[`master.md`](./master.md).

## Repo layout

```
Lansweeper-Outpost/
├── master.md            # canonical source of truth (read this first)
├── CHANGELOG.md         # every change, semver-tagged
├── README.md            # this file
├── pyproject.toml       # installable package + dependencies
├── .gitignore           # ignores *.db so the local buffer is never committed
├── docs/
│   ├── DECISIONS.md     # the "why" behind each choice (ADRs)
│   ├── ROADMAP.md       # staged future builds
│   └── SCHEMA.md        # the asset data contract (Observation → Asset)
├── scripts/             # helper scripts
└── outpost/             # the package
    ├── __init__.py
    ├── schema.py        # shared dataclasses + JSON Schema validation
    ├── schema.json      # authoritative JSON Schema (the contract)
    └── sensor.py        # the network sensor
```

## Quick start

```bash
pip install -e .                                          # installs deps (scapy, jsonschema)
sudo python3 -m outpost.sensor --cidr 192.168.2.0/24 --iface <iface>   # scan + sniff
python3 -m outpost.sensor --dump                          # buffered observations as JSON (no root)
```

The sensor discovers endpoints and records them as canonical **Observations**
(see [`docs/SCHEMA.md`](./docs/SCHEMA.md)). It buffers locally and makes no
outbound connections; shipping to a hub is a later phase.

## Conventions

- Semantic Versioning project-wide; every source file carries a `__version__`
  and a change-history header. Wire schema is versioned separately (`0.1`).
- Small, frequent commits with conventional messages (`feat:`, `fix:`, `docs:`).
- Every release updates `master.md` and `CHANGELOG.md`.
- Pushes/deploys are run by the human on the dev server (see ADR-0008).
