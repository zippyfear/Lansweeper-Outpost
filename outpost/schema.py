"""
Outpost shared asset schema.

The single data contract shared by the sensor, the hub, and the dashboard.
Defines the two-level model from docs/SCHEMA.md:

    Sensor  --emits-->  Observation  --hub correlates-->  Asset

Observation = an immutable sighting from one sensor at one IP.
Asset       = the de-duplicated entity the hub builds by merging observations.

Dataclasses here are the in-code representation; `outpost/schema.json` is the
authoritative JSON Schema both sides validate against. Keep the two in sync.
"""

from __future__ import annotations

__version__ = "0.2.0"
# ── Change history ──────────────────────────────────────────────────────────
# 0.2.0  2026-06-06  Initial shared schema module: Service/Interface/
#                    Observation/Asset dataclasses, to_dict/from_dict, and
#                    JSON Schema validation. Wire schema_version "0.1".
# ────────────────────────────────────────────────────────────────────────────

import functools
import json
import os
from dataclasses import asdict, dataclass, field

# Wire format version carried on every Observation/Asset payload. Bump on a
# breaking field change (and record it in docs/SCHEMA.md + CHANGELOG.md).
SCHEMA_VERSION = "0.1"

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.json")


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------
@dataclass
class Service:
    """An open port / service on an endpoint."""
    port: int
    proto: str = "tcp"          # "tcp" | "udp"
    name: str | None = None     # e.g. "ssh"
    product: str | None = None  # e.g. "OpenSSH"
    version: str | None = None  # e.g. "9.6p1"

    def key(self) -> tuple[int, str]:
        return (self.port, self.proto)


@dataclass
class Observation:
    """A single sensor's sighting of an endpoint at a point in time."""
    sensor_id: str
    observed_at: str            # ISO-8601 UTC
    ip: str
    mac: str | None = None
    hostname: str | None = None
    vendor: str | None = None
    discovery_methods: list[str] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    snmp: dict | None = None
    os_guess: dict | None = None
    packet_count: int | None = None
    extra: dict | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Observation":
        d = dict(d)
        d["services"] = [
            s if isinstance(s, Service) else Service(**s)
            for s in d.get("services", [])
        ]
        return cls(**_only_known(cls, d))


@dataclass
class Interface:
    """A network interface belonging to an Asset."""
    mac: str
    vendor: str | None = None
    ips: list[str] = field(default_factory=list)


@dataclass
class Asset:
    """The canonical, correlated entity owned by the hub."""
    asset_id: str
    type: str = "unknown"
    interfaces: list[Interface] = field(default_factory=list)
    hostnames: list[str] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    discovery_methods: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    type_confidence: float | None = None
    display_name: str | None = None
    os: dict | None = None
    snmp: dict | None = None
    identifiers: dict | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Asset":
        d = dict(d)
        d["interfaces"] = [
            i if isinstance(i, Interface) else Interface(**i)
            for i in d.get("interfaces", [])
        ]
        d["services"] = [
            s if isinstance(s, Service) else Service(**s)
            for s in d.get("services", [])
        ]
        return cls(**_only_known(cls, d))


def _only_known(cls, d: dict) -> dict:
    """Drop unknown keys so newer payloads stay forward-compatible."""
    return {k: v for k, v in d.items() if k in cls.__dataclass_fields__}


# --------------------------------------------------------------------------
# Validation against the authoritative JSON Schema
# --------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _schema() -> dict:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_observation(payload: dict) -> None:
    """Raise jsonschema.ValidationError if payload is not a valid Observation."""
    _validate(payload, "Observation")


def validate_asset(payload: dict) -> None:
    """Raise jsonschema.ValidationError if payload is not a valid Asset."""
    _validate(payload, "Asset")


def _validate(payload: dict, defn: str) -> None:
    import jsonschema  # imported lazily so the schema types work without it

    root = _schema()
    sub = {"$ref": f"#/$defs/{defn}", "$defs": root["$defs"]}
    jsonschema.validate(instance=payload, schema=sub)
