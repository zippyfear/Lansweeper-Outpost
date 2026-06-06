"""
Outpost hub -- correlation engine + asset store.

The hub is the brain of the system (ADR-0002, ADR-0005): sensors emit
per-IP Observations and stay identity-agnostic; the hub folds those
Observations into de-duplicated Assets.

Identity strategy is ADR-0009 (conservative correlation):

    strong identifier (serial / hw_uuid)   -- highest confidence
    MAC address                            -- strong, the usual anchor
    IP only                                -- PROVISIONAL (DHCP churn)

A wrong merge is harder to unwind than a wrong split, so we only merge on
strong evidence. IP-only sightings become *provisional* assets (no interface,
IP held in attributes, tagged "provisional"); when a later sighting presents a
MAC for that IP, the provisional asset is promoted/merged into the MAC-anchored
asset. "Merge as better data arrives" -- the hub supports it; sensors never do.

This module is deliberately framework-free stdlib + jsonschema so the eventual
Go rewrite (ADR-0003) is a mechanical port, not a redesign. The SQLite asset
store mirrors the sensor's buffer approach; the production store
(PostgreSQL/TimescaleDB) is still TBD per the roadmap (ADR-0011).
"""

from __future__ import annotations

__version__ = "0.3.0"
# -- Change history ----------------------------------------------------------
# 0.3.0  2026-06-06  Initial hub: in-process correlation engine implementing
#                    ADR-0009 (strong-id > MAC > provisional IP), SQLite
#                    AssetStore with cross-run merge, CLI (ingest/list/show/
#                    stats/selftest). Emits schema-valid Assets (wire 0.1).
# ----------------------------------------------------------------------------

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import OrderedDict

from outpost.schema import (
    Asset,
    Interface,
    Service,
    SCHEMA_VERSION,
    validate_asset,
)

STRONG_ID_KEYS = ("serial", "hw_uuid")  # checked in Observation.extra


# --------------------------------------------------------------------------
# Internal working representation. Richer than Asset so correlation is easy;
# serialized to a schema-valid Asset on the way out.
# --------------------------------------------------------------------------
class _Work:
    __slots__ = (
        "asset_id", "strong_ids", "ifaces", "iface_vendor", "loose_ips",
        "hostnames", "services", "methods", "sources", "first_seen",
        "last_seen", "type", "type_confidence",
    )

    def __init__(self, asset_id: str):
        self.asset_id = asset_id
        self.strong_ids: dict = {}
        self.ifaces: dict[str, set] = {}        # mac -> set(ips)
        self.iface_vendor: dict[str, str] = {}  # mac -> vendor
        self.loose_ips: set = set()             # ips seen without a mac
        self.hostnames: set = set()
        self.services: "OrderedDict[tuple, Service]" = OrderedDict()
        self.methods: set = set()
        self.sources: set = set()
        self.first_seen: str = ""
        self.last_seen: str = ""
        self.type: str = "unknown"
        self.type_confidence: float | None = None

    @property
    def is_provisional(self) -> bool:
        return not self.ifaces and not self.strong_ids

    def all_ips(self) -> set:
        ips = set(self.loose_ips)
        for s in self.ifaces.values():
            ips |= s
        return ips

    def to_asset(self) -> Asset:
        interfaces = [
            Interface(mac=mac, vendor=self.iface_vendor.get(mac),
                      ips=sorted(ips))
            for mac, ips in sorted(self.ifaces.items())
        ]
        tags = []
        attributes: dict = {}
        if self.is_provisional:
            tags.append("provisional")
            attributes["ips"] = sorted(self.loose_ips)
        elif self.loose_ips - self.all_ips_in_ifaces():
            # IP-only sightings folded into a MAC asset but not tied to a NIC
            unbound = sorted(self.loose_ips - self.all_ips_in_ifaces())
            if unbound:
                attributes["unbound_ips"] = unbound
        return Asset(
            asset_id=self.asset_id,
            type=self.type,
            interfaces=interfaces,
            hostnames=sorted(self.hostnames),
            services=list(self.services.values()),
            discovery_methods=sorted(self.methods),
            sources=sorted(self.sources),
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            type_confidence=self.type_confidence,
            identifiers=self.strong_ids or None,
            tags=tags,
            attributes=attributes or None,
            schema_version=SCHEMA_VERSION,
        )

    def all_ips_in_ifaces(self) -> set:
        out: set = set()
        for s in self.ifaces.values():
            out |= s
        return out


def _anchor_id(anchor: str) -> str:
    return "a_" + hashlib.sha1(anchor.encode()).hexdigest()[:12]


def _strong_ids(obs: dict) -> dict:
    extra = obs.get("extra") or {}
    return {k: extra[k] for k in STRONG_ID_KEYS if extra.get(k)}


def _infer_type(w: _Work) -> tuple[str, float | None]:
    """Conservative type inference. Better an honest 'unknown' than a wrong
    guess (docs/SCHEMA.md). Only classify on a clear signal."""
    if "snmp" in w.methods:
        return "network-device", 0.6
    ports = {svc.port for svc in w.services.values()}
    if {139, 445} & ports and {22} & ports == set():
        return "endpoint", 0.4
    if 631 in ports or 9100 in ports:  # IPP / JetDirect
        return "printer", 0.6
    return "unknown", None


# --------------------------------------------------------------------------
# Correlator -- the in-memory engine. Stateless across batches except for the
# assets you seed it with (the store seeds it with what's already persisted).
# --------------------------------------------------------------------------
class Correlator:
    def __init__(self):
        self._assets: dict[str, _Work] = {}
        self._by_strong: dict = {}   # (k,v) -> asset_id
        self._by_mac: dict = {}      # mac -> asset_id
        self._by_ip: dict = {}       # ip -> asset_id  (provisional assets only)

    # -- seeding from already-stored assets (cross-run continuity) ----------
    def seed(self, assets: list[Asset]) -> None:
        for a in assets:
            w = _Work(a.asset_id)
            w.strong_ids = dict(a.identifiers or {})
            for iface in a.interfaces:
                w.ifaces[iface.mac] = set(iface.ips)
                if iface.vendor:
                    w.iface_vendor[iface.mac] = iface.vendor
            attrs = a.attributes or {}
            w.loose_ips = set(attrs.get("ips", [])) | set(attrs.get("unbound_ips", []))
            w.hostnames = set(a.hostnames)
            for svc in a.services:
                w.services[svc.key()] = svc
            w.methods = set(a.discovery_methods)
            w.sources = set(a.sources)
            w.first_seen, w.last_seen = a.first_seen, a.last_seen
            w.type, w.type_confidence = a.type, a.type_confidence
            self._register(w)

    def _register(self, w: _Work) -> None:
        self._assets[w.asset_id] = w
        for k, v in w.strong_ids.items():
            self._by_strong[(k, v)] = w.asset_id
        for mac in w.ifaces:
            self._by_mac[mac] = w.asset_id
        if w.is_provisional:
            for ip in w.loose_ips:
                self._by_ip[ip] = w.asset_id

    # -- the core: fold one observation in ----------------------------------
    def ingest(self, obs: dict) -> None:
        ip = (obs.get("ip") or "").split("/")[0]
        mac = obs.get("mac")
        if mac:
            mac = mac.strip().lower()
        sids = _strong_ids(obs)

        target = self._resolve(sids, mac, ip)

        # fold data in
        if sids:
            target.strong_ids.update(sids)
            self._index_strong(target)
        if mac:
            target.ifaces.setdefault(mac, set())
            if ip:
                target.ifaces[mac].add(ip)
            if obs.get("vendor"):
                target.iface_vendor[mac] = obs["vendor"]
            self._by_mac[mac] = target.asset_id
            # promoting out of provisional: shed the IP-only index + tag
            self._by_ip.pop(ip, None)
            target.loose_ips.discard(ip)
        elif ip:
            target.loose_ips.add(ip)
            if target.is_provisional:
                self._by_ip[ip] = target.asset_id

        if obs.get("hostname"):
            target.hostnames.add(obs["hostname"])
        for s in obs.get("services", []):
            svc = s if isinstance(s, Service) else Service(**s)
            target.services[svc.key()] = svc
        for m in obs.get("discovery_methods", []):
            target.methods.add(m)
        if obs.get("sensor_id"):
            target.sources.add(obs["sensor_id"])

        ts = obs.get("observed_at", "")
        if ts:
            if not target.first_seen or ts < target.first_seen:
                target.first_seen = ts
            if ts > target.last_seen:
                target.last_seen = ts

        target.type, target.type_confidence = _infer_type(target)

    def _resolve(self, sids: dict, mac: str | None, ip: str) -> _Work:
        # 1. strong identifier -- highest confidence
        for k, v in sids.items():
            aid = self._by_strong.get((k, v))
            if aid:
                return self._assets[aid]
        # 2. MAC -- the usual strong anchor
        if mac and mac in self._by_mac:
            return self._assets[self._by_mac[mac]]
        # 3. new MAC whose IP is currently a PROVISIONAL asset -> promote/merge
        if mac and ip and ip in self._by_ip:
            promoted = self._assets[self._by_ip.pop(ip)]
            return promoted
        # 4. IP-only matching an existing provisional asset -> same asset
        if not mac and ip and ip in self._by_ip:
            return self._assets[self._by_ip[ip]]
        # 5. IP-only whose IP a MAC-anchored asset currently owns -> best-effort
        #    attach (current owner of that IP). Future MAC evidence can correct.
        if not mac and ip:
            for aid, w in self._assets.items():
                if ip in w.all_ips_in_ifaces():
                    return w
        # 6. nothing matched -> brand-new asset (provisional if IP-only)
        anchor = (next(iter(f"{k}:{v}" for k, v in sids.items()), None)
                  or (f"mac:{mac}" if mac else f"ip:{ip}"))
        w = _Work(_anchor_id(anchor))
        # guard against id collision on re-seed
        while w.asset_id in self._assets:
            anchor += "+"
            w.asset_id = _anchor_id(anchor)
        self._assets[w.asset_id] = w
        return w

    def _index_strong(self, w: _Work) -> None:
        for k, v in w.strong_ids.items():
            self._by_strong[(k, v)] = w.asset_id

    def assets(self) -> list[Asset]:
        return [w.to_asset() for w in self._assets.values()]


# --------------------------------------------------------------------------
# AssetStore -- SQLite persistence wrapping the correlator for cross-run merge.
# --------------------------------------------------------------------------
class AssetStore:
    def __init__(self, path: str = "outpost_assets.db"):
        self.path = path
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS assets (
                       asset_id TEXT PRIMARY KEY,
                       payload  TEXT NOT NULL,
                       updated  TEXT
                   )"""
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def load(self) -> list[Asset]:
        with self._conn() as c:
            rows = c.execute("SELECT payload FROM assets").fetchall()
        return [Asset.from_dict(json.loads(r["payload"])) for r in rows]

    def ingest(self, observations: list[dict]) -> dict:
        """Fold observations into stored assets and persist. Returns a summary."""
        corr = Correlator()
        corr.seed(self.load())
        before = len(corr.assets())
        for obs in observations:
            corr.ingest(obs)
        assets = corr.assets()
        with self._conn() as c:
            c.execute("DELETE FROM assets")
            for a in assets:
                d = a.to_dict()
                validate_asset(d)  # never persist an invalid asset
                c.execute(
                    "INSERT INTO assets (asset_id, payload, updated) VALUES (?,?,?)",
                    (a.asset_id, json.dumps(d), a.last_seen),
                )
        provisional = sum(1 for a in assets if "provisional" in (a.tags or []))
        return {
            "observations_ingested": len(observations),
            "assets_before": before,
            "assets_after": len(assets),
            "provisional": provisional,
        }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _load_observations(src: str | None) -> list[dict]:
    raw = sys.stdin.read() if (src in (None, "-")) else open(src, encoding="utf-8").read()
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def cmd_ingest(args) -> int:
    store = AssetStore(args.db)
    summary = store.ingest(_load_observations(args.source))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_list(args) -> int:
    for a in AssetStore(args.db).load():
        ips = sorted({ip for i in a.interfaces for ip in i.ips}
                     | set((a.attributes or {}).get("ips", [])))
        macs = [i.mac for i in a.interfaces] or ["-"]
        flag = " [provisional]" if "provisional" in (a.tags or []) else ""
        print(f"{a.asset_id}  {a.type:14} macs={','.join(macs)}  "
              f"ips={','.join(ips) or '-'}  hosts={','.join(a.hostnames) or '-'}{flag}")
    return 0


def cmd_show(args) -> int:
    for a in AssetStore(args.db).load():
        if a.asset_id == args.asset_id:
            print(json.dumps(a.to_dict(), indent=2))
            return 0
    print(f"no asset {args.asset_id}", file=sys.stderr)
    return 1


def cmd_stats(args) -> int:
    assets = AssetStore(args.db).load()
    prov = sum(1 for a in assets if "provisional" in (a.tags or []))
    by_type: dict = {}
    for a in assets:
        by_type[a.type] = by_type.get(a.type, 0) + 1
    print(json.dumps({"assets": len(assets), "provisional": prov,
                      "by_type": by_type}, indent=2))
    return 0


def cmd_selftest(args) -> int:
    return _selftest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="outpost.hub",
                                description="Outpost hub: correlate Observations into Assets.")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__} (schema wire {SCHEMA_VERSION})")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="fold observations (JSON array; file or stdin) into the store")
    pi.add_argument("source", nargs="?", help="path to JSON, or '-'/omitted for stdin "
                                              "(e.g. `python -m outpost.sensor --dump | ... ingest`)")
    pi.add_argument("--db", default="outpost_assets.db")
    pi.set_defaults(func=cmd_ingest)

    pl = sub.add_parser("list", help="one line per asset")
    pl.add_argument("--db", default="outpost_assets.db")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("show", help="full JSON for one asset")
    ps.add_argument("asset_id")
    ps.add_argument("--db", default="outpost_assets.db")
    ps.set_defaults(func=cmd_show)

    pst = sub.add_parser("stats", help="counts by type + provisional")
    pst.add_argument("--db", default="outpost_assets.db")
    pst.set_defaults(func=cmd_stats)

    pt = sub.add_parser("selftest", help="run the built-in correlation scenarios")
    pt.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    return args.func(args)


# --------------------------------------------------------------------------
# Built-in self-test: proves the three ADR-0009 scenarios without a network.
# --------------------------------------------------------------------------
def _selftest() -> int:
    def obs(**kw):
        kw.setdefault("observed_at", "2026-06-06T12:00:00Z")
        kw.setdefault("discovery_methods", ["arp"])
        kw.setdefault("services", [])
        kw.setdefault("schema_version", SCHEMA_VERSION)
        return kw

    ok = True

    # Scenario A: dual-NIC host seen by two sensors on two subnets, same MAC
    #             on each NIC's sighting -> ONE asset with two interfaces? No:
    #             same MAC => one interface, two IPs. Two MACs => two interfaces.
    c = Correlator()
    c.ingest(obs(sensor_id="ts1", ip="192.168.2.88", mac="00:11:22:33:44:55"))
    c.ingest(obs(sensor_id="ts2", ip="10.10.0.88",  mac="00:11:22:33:44:55"))
    a = c.assets()
    if len(a) != 1 or sorted(a[0].interfaces[0].ips) != ["10.10.0.88", "192.168.2.88"]:
        print("FAIL A: same-MAC across sensors did not collapse to one asset"); ok = False
    else:
        print(f"PASS A: same MAC, two subnets -> 1 asset, 2 IPs ({a[0].asset_id})")

    # Scenario B: IP-only sighting -> provisional asset (no interface)
    c = Correlator()
    c.ingest(obs(sensor_id="ts2", ip="10.10.15.75"))  # no mac
    a = c.assets()
    if len(a) != 1 or a[0].interfaces or "provisional" not in a[0].tags:
        print("FAIL B: IP-only sighting was not a provisional, interface-less asset"); ok = False
    else:
        print(f"PASS B: IP-only -> provisional asset, no interface ({a[0].asset_id})")

    # Scenario C: later MAC sighting for that IP -> provisional promoted/merged
    c.ingest(obs(sensor_id="ts2", ip="10.10.15.75", mac="aa:bb:cc:dd:ee:ff"))
    a = c.assets()
    promoted = [x for x in a if x.interfaces]
    if len(a) != 1 or not promoted or "provisional" in a[0].tags:
        print("FAIL C: MAC evidence did not promote the provisional asset"); ok = False
    else:
        print(f"PASS C: MAC for known IP -> provisional promoted to MAC asset ({a[0].asset_id})")

    # Scenario D: every emitted asset validates against the JSON Schema
    try:
        for x in a:
            validate_asset(x.to_dict())
        print("PASS D: emitted assets are schema-valid (wire 0.1)")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL D: schema validation: {exc}"); ok = False

    print("\nALL PASS" if ok else "\nFAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
