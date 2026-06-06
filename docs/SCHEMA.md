# Asset Schema — `docs/SCHEMA.md`

> **Status: ACCEPTED — schema wire version `0.1` (locked 2026-06-06).**
> This is the data contract shared by sensor, hub, and dashboard. The sensor
> emits it as of v0.2.0; the hub and dashboard consume it. Implemented in
> `outpost/schema.py` (dataclasses) and validated against `outpost/schema.json`
> (authoritative JSON Schema). Breaking changes bump the wire version and are
> recorded here and in `CHANGELOG.md`. Resolved design decisions are at the end.

---

## 1. Why two levels: Observation vs Asset

The single most important modelling decision. A sensor reports **what it saw**;
the hub maintains **what exists**. These are different objects:

```
  Sensor  ──emits──▶  Observation  ──hub correlates──▶  Asset
  (a sighting at a point in time)        (the canonical, de-duplicated entity)
```

- An **Observation** is an immutable, timestamped sighting from one sensor at
  one IP. Multiple observations (over time, across sensors) can describe the
  same physical thing.
- An **Asset** is the correlated entity the hub builds and updates by merging
  observations. It owns *multiple* interfaces, IPs, and hostnames.

This split is what lets a multi-NIC server seen by two sensors on two subnets
become **one** asset, and lets us revise correlation later as better data
(MAC, SNMP, serial) arrives. It also keeps sensors dumb and fast (ADR-0005):
they never decide identity — the hub does.

---

## 2. `schema_version` and evolution

Every Observation and Asset payload carries a `schema_version` string
(currently `"0.1"`). The hub must accept any minor version it knows and ignore
unknown fields (forward-compatible). Breaking field changes bump the version
and are recorded in `CHANGELOG.md` and here.

---

## 3. Observation (sensor → hub)

One record per endpoint a sensor saw. Batched on the wire.

| Field | Type | Null? | Notes |
|---|---|---|---|
| `schema_version` | string | no | e.g. `"0.1"` |
| `sensor_id` | string | no | stable ID of the reporting sensor |
| `observed_at` | timestamp | no | ISO-8601 UTC |
| `ip` | string | no | IP the endpoint was seen at |
| `mac` | string | yes | L2 address; null for cross-gateway passive sightings |
| `hostname` | string | yes | reverse DNS |
| `vendor` | string | yes | OUI-derived |
| `discovery_methods` | array\<string\> | no | subset of `arp`, `tcp`, `icmp`, `snmp`, `passive` |
| `services` | array\<Service\> | no | open ports; may be empty |
| `snmp` | object | yes | raw SNMP facts; null until SNMP lands (Phase 1) |
| `os_guess` | object | yes | `{family?, name?, confidence?}`; null until fingerprinting lands |
| `packet_count` | int | yes | passive packets seen this interval (sensor telemetry) |
| `extra` | object | yes | escape hatch for source-specific data |

**Service** object: `{ port: int, proto: "tcp"|"udp", name?: string, product?: string, version?: string }`
(v0 populates `port` + `proto` only; banners fill `name`/`product`/`version` later.)

```json
{
  "schema_version": "0.1",
  "sensor_id": "outpost-sensor-lab01",
  "observed_at": "2026-06-06T16:20:00Z",
  "ip": "192.168.2.10",
  "mac": "b8:27:eb:aa:bb:cc",
  "hostname": "pi.lan",
  "vendor": "Raspberry Pi Foundation",
  "discovery_methods": ["arp", "tcp"],
  "services": [
    {"port": 22, "proto": "tcp"},
    {"port": 443, "proto": "tcp"}
  ],
  "snmp": null,
  "os_guess": null,
  "packet_count": 0,
  "extra": null
}
```

---

## 4. Asset (hub canonical)

The merged entity. Built and owned by the hub.

| Field | Type | Null? | Notes |
|---|---|---|---|
| `schema_version` | string | no | |
| `asset_id` | string (UUID) | no | hub-generated stable internal key |
| `type` | enum | no | see §6; default `unknown` |
| `type_confidence` | float (0–1) | yes | how sure the classifier is |
| `display_name` | string | yes | best human label (hostname / SNMP sysName / vendor+IP) |
| `interfaces` | array\<Interface\> | no | the multi-NIC model |
| `hostnames` | array\<string\> | no | all observed names |
| `os` | object | yes | `{family, name, version, confidence, source}` |
| `services` | array\<Service\> | no | merged across observations |
| `snmp` | object | yes | merged SNMP facts |
| `identifiers` | object | yes | strong anchors when available: `{serial?, hw_uuid?}` |
| `discovery_methods` | array\<string\> | no | union across all observations |
| `sources` | array\<string\> | no | sensor_ids that reported this asset |
| `tags` | array\<string\> | yes | operator-applied labels |
| `first_seen` | timestamp | no | earliest observation across all sensors |
| `last_seen` | timestamp | no | most recent observation |
| `attributes` | object | yes | extensible enrichment bag |

**Interface** object: `{ mac: string, vendor?: string, ips: array<string> }`

```json
{
  "schema_version": "0.1",
  "asset_id": "a1f3c9e2-7b40-4d2a-9c11-0e6b2d8f4a55",
  "type": "server",
  "type_confidence": 0.7,
  "display_name": "pi.lan",
  "interfaces": [
    {"mac": "b8:27:eb:aa:bb:cc", "vendor": "Raspberry Pi Foundation",
     "ips": ["192.168.2.10"]}
  ],
  "hostnames": ["pi.lan"],
  "os": null,
  "services": [{"port": 22, "proto": "tcp"}, {"port": 443, "proto": "tcp"}],
  "snmp": null,
  "identifiers": null,
  "discovery_methods": ["arp", "tcp", "passive"],
  "sources": ["outpost-sensor-lab01"],
  "tags": [],
  "first_seen": "2026-06-06T16:20:00Z",
  "last_seen": "2026-06-06T16:25:00Z",
  "attributes": null
}
```

---

## 5. Identity & correlation rules (hub-side)

When an observation arrives, the hub decides which asset it belongs to, in this
precedence order:

1. **Strong identifier** — `identifiers.serial` or `identifiers.hw_uuid`
   matches an existing asset → same asset. (Rare until credentialed scans.)
2. **MAC** — observation `mac` matches any `interfaces[].mac` → same asset.
   New MAC for a matched asset → add an interface.
3. **IP (weak, windowed)** — no MAC (e.g. passive cross-gateway sighting):
   match by `ip` only within a recency window **and** flagged provisional.
4. **Otherwise** → create a new asset.

**Known pitfalls the rules must tolerate** (these drive the open decisions):
- **Randomized/private MACs** (phones, modern laptops) — a device can present
  rotating MACs; pure MAC identity will over-count it.
- **DHCP churn / IP reuse** — IP is a *temporary* lease, never a stable
  identity. Hence IP-only matching is provisional.
- **Virtual/shared MACs** — hypervisors, VRRP/HSRP, clustered NICs.
- **Correlation is revisable** — assets may need to be **merged** (two
  provisional assets turn out to be one) or **split** as data improves.

---

## 6. Asset type taxonomy (initial)

`type` enum — derived later by a classifier (OUI + open ports + SNMP
sysObjectID). Field exists now; classification logic is future work.

`server`, `workstation`, `laptop`, `mobile`, `network_device`, `printer`,
`storage`, `hypervisor`, `vm`, `iot`, `ot_medical`, `unknown`.

(`ot_medical` is called out explicitly given the healthcare target use cases.)

---

## 7. Notes for the eventual Go rewrite

Types are deliberately language-neutral. `timestamp` = RFC 3339/ISO-8601 UTC
string. Nullable fields map to pointers/Option. `extra`/`attributes` are
free-form JSON objects (`map[string]any` / `dict`). Keeping the schema as the
contract means the Go sensor is a mechanical port, not a redesign (ADR-0003).

---

## 8. Resolved decisions (locked 2026-06-06)

1. **Identity strategy** — *Conservative.* Match on strong identifiers and MAC;
   treat IP-only sightings as **provisional** and let the hub merge later.
   Rationale: unwinding a wrong merge is harder than merging two provisional
   records. (See ADR-0009.)
2. **Services** — modelled as **objects** (`{port, proto, name?, version?}`),
   forward-compatible with banner/version enrichment. v0 fills `port`/`proto`.
3. **Interfaces** — **multi-NIC `interfaces[]`** at the asset level (servers and
   hypervisors routinely have several).
4. **Type taxonomy** — §6 list adopted as-is, including `ot_medical`.
5. **Provenance** — **coarse** for now (`discovery_methods` + per-source
   `last_seen`). Per-field provenance can be added later without a breaking
   change (additive fields are forward-compatible).
