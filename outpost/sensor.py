#!/usr/bin/env python3
"""
Outpost Sensor -- lightweight network asset collector.

Working codename "Outpost". Final product name TBD.

Discovers endpoints on a network segment and records them as canonical
**Observations** (see outpost/schema.py + docs/SCHEMA.md). The sensor never
decides asset identity -- that is the hub's job. It buffers observations
locally (SQLite) and is fully standalone / air-gap safe; shipping to a hub is
a later phase.

Two discovery modes, used together:
  * ACTIVE  -- ARP-sweeps the target subnet(s) for live hosts + MACs, then
               TCP connect-scans each for open services.
  * PASSIVE -- sniffs the wire to register hosts that never answer a probe
               and to keep last_seen fresh.

Active scanning and passive sniffing need root. The local buffer and --dump
work unprivileged.

Run (after `pip install -e .` at the repo root):
  sudo python3 -m outpost.sensor --cidr 192.168.2.0/24 --iface eth0
  sudo python3 -m outpost.sensor --cidr 10.10.0.0/24 --once
  python3 -m outpost.sensor --dump
"""

from __future__ import annotations

__version__ = "0.2.0"
# ── Change history ──────────────────────────────────────────────────────────
# 0.2.0  2026-06-06  Refactored onto the shared schema: records are now
#                    canonical Observations (outpost.schema), services modelled
#                    as Service objects, sensor_id added, --dump emits
#                    schema-validated Observation payloads. Store renamed
#                    AssetStore -> ObservationStore (table `observations`).
# 0.1.0  2026-06-06  Initial prototype: ARP sweep + TCP connect scan (active),
#                    passive sniffer, SQLite cache, CLI.
# ────────────────────────────────────────────────────────────────────────────

import argparse
import ipaddress
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone

from outpost.schema import Observation, Service, validate_observation

DEFAULT_PORTS = [
    22, 23, 25, 53, 80, 88, 110, 135, 139, 143, 161, 389, 443, 445,
    465, 514, 587, 631, 993, 995, 1433, 1521, 3306, 3389, 5432, 5900,
    8000, 8080, 8443, 9100,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_sensor_id() -> str:
    return f"outpost-{socket.gethostname()}"


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass
class Config:
    cidrs: list[str] = field(default_factory=list)
    sensor_id: str = field(default_factory=default_sensor_id)
    iface: str | None = None
    ports: list[int] = field(default_factory=lambda: list(DEFAULT_PORTS))
    interval: int = 300
    db_path: str = "outpost_observations.db"
    passive: bool = True
    scan_timeout: float = 2.0
    workers: int = 64


# --------------------------------------------------------------------------
# Observation buffer (local SQLite store-and-forward cache)
# --------------------------------------------------------------------------
class ObservationStore:
    """Local buffer of the sensor's current Observation per endpoint (keyed by
    IP). Active + passive sightings of the same IP are consolidated into one
    Observation; cross-sensor / identity correlation into Assets is the hub's
    job, not the sensor's (ADR-0005)."""

    def __init__(self, path: str, sensor_id: str):
        self.path = path
        self.sensor_id = sensor_id
        self._lock = threading.Lock()
        with closing(self._conn()) as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    ip          TEXT PRIMARY KEY,
                    sensor_id   TEXT,
                    mac         TEXT,
                    hostname    TEXT,
                    vendor      TEXT,
                    services    TEXT,    -- JSON list[Service]
                    methods     TEXT,    -- JSON list[str]
                    snmp        TEXT,    -- JSON | null
                    os_guess    TEXT,    -- JSON | null
                    packets     INTEGER DEFAULT 0,
                    first_seen  TEXT,
                    last_seen   TEXT
                )
                """
            )
            c.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def observe(
        self,
        ip: str,
        mac: str | None = None,
        hostname: str | None = None,
        vendor: str | None = None,
        services: list[Service] | None = None,
        method: str = "arp",
        packet: bool = False,
    ) -> None:
        ts = now_iso()
        with self._lock, closing(self._conn()) as c:
            row = c.execute("SELECT * FROM observations WHERE ip = ?", (ip,)).fetchone()
            svc_list = list(services or [])
            if row is None:
                c.execute(
                    """INSERT INTO observations
                       (ip, sensor_id, mac, hostname, vendor, services, methods,
                        snmp, os_guess, packets, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ip, self.sensor_id, mac, hostname, vendor,
                        json.dumps([s.__dict__ for s in svc_list]),
                        json.dumps([method]),
                        None, None,
                        1 if packet else 0, ts, ts,
                    ),
                )
            else:
                methods = set(json.loads(row["methods"] or "[]"))
                methods.add(method)
                merged: dict[tuple[int, str], Service] = {}
                for s in json.loads(row["services"] or "[]"):
                    sv = Service(**s)
                    merged[sv.key()] = sv
                for sv in svc_list:
                    merged[sv.key()] = sv  # newer wins (may carry banner data)
                c.execute(
                    """UPDATE observations SET
                        mac      = COALESCE(?, mac),
                        hostname = COALESCE(?, hostname),
                        vendor   = COALESCE(?, vendor),
                        services = ?,
                        methods  = ?,
                        packets  = packets + ?,
                        last_seen = ?
                       WHERE ip = ?""",
                    (
                        mac, hostname, vendor,
                        json.dumps([s.__dict__ for s in merged.values()]),
                        json.dumps(sorted(methods)),
                        1 if packet else 0, ts, ip,
                    ),
                )
            c.commit()

    def _row_to_observation(self, r: sqlite3.Row) -> Observation:
        return Observation(
            sensor_id=r["sensor_id"] or self.sensor_id,
            observed_at=r["last_seen"],
            ip=r["ip"],
            mac=r["mac"],
            hostname=r["hostname"],
            vendor=r["vendor"],
            discovery_methods=json.loads(r["methods"] or "[]"),
            services=[Service(**s) for s in json.loads(r["services"] or "[]")],
            snmp=json.loads(r["snmp"]) if r["snmp"] else None,
            os_guess=json.loads(r["os_guess"]) if r["os_guess"] else None,
            packet_count=r["packets"],
        )

    def observations(self, validate: bool = True) -> list[dict]:
        with closing(self._conn()) as c:
            rows = c.execute("SELECT * FROM observations ORDER BY ip").fetchall()
        out = []
        for r in rows:
            payload = self._row_to_observation(r).to_dict()
            if validate:
                validate_observation(payload)  # fail loud if we drift from contract
            out.append(payload)
        return out

    def count(self) -> int:
        with closing(self._conn()) as c:
            return c.execute("SELECT COUNT(*) FROM observations").fetchone()[0]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def reverse_dns(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None


def mac_vendor(mac: str | None) -> str | None:
    if not mac:
        return None
    try:
        from scapy.all import conf  # type: ignore
        db = getattr(conf, "manufdb", None)
        if db is None:
            return None
        for attr in ("_get_manuf", "lookup"):
            fn = getattr(db, attr, None)
            if fn:
                res = fn(mac)
                if isinstance(res, (tuple, list)):
                    return res[-1] or None
                return res or None
    except Exception:
        return None
    return None


def tcp_connect_scan(ip: str, ports: list[int], timeout: float) -> list[Service]:
    found = []
    for p in ports:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(timeout)
            if s.connect_ex((ip, p)) == 0:
                found.append(Service(port=p, proto="tcp"))
    return found


# --------------------------------------------------------------------------
# Active discovery
# --------------------------------------------------------------------------
def arp_sweep(cidr: str, iface: str | None, timeout: float) -> dict[str, str]:
    from scapy.all import ARP, Ether, srp  # type: ignore
    pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=cidr)
    answered, _ = srp(pkt, timeout=timeout, iface=iface, verbose=0)
    return {rcv[ARP].psrc: rcv[ARP].hwsrc for _, rcv in answered}


def active_scan(store: ObservationStore, cfg: Config) -> int:
    found = 0
    for cidr in cfg.cidrs:
        print(f"[active] ARP sweep {cidr} ...", flush=True)
        try:
            live = arp_sweep(cidr, cfg.iface, cfg.scan_timeout)
        except PermissionError:
            print("  ! need root for ARP sweep -- skipping active scan", flush=True)
            return found
        except Exception as e:  # noqa: BLE001
            print(f"  ! ARP sweep failed: {e}", flush=True)
            continue

        print(f"[active] {len(live)} live host(s); port-scanning ...", flush=True)

        def probe(item: tuple[str, str]) -> None:
            ip, mac = item
            services = tcp_connect_scan(ip, cfg.ports, cfg.scan_timeout)
            store.observe(
                ip=ip, mac=mac, hostname=reverse_dns(ip),
                vendor=mac_vendor(mac), services=services, method="arp",
            )

        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            list(pool.map(probe, live.items()))
        found += len(live)
    return found


# --------------------------------------------------------------------------
# Passive discovery
# --------------------------------------------------------------------------
def passive_listen(store: ObservationStore, cfg: Config, stop: threading.Event) -> None:
    from scapy.all import IP, Ether, sniff  # type: ignore

    def handle(pkt) -> None:
        if not pkt.haslayer(IP):
            return
        l2 = pkt[Ether] if pkt.haslayer(Ether) else None
        for ip, mac in (
            (pkt[IP].src, l2.src if l2 else None),
            (pkt[IP].dst, l2.dst if l2 else None),
        ):
            try:
                if ipaddress.ip_address(ip).is_multicast:
                    continue
            except ValueError:
                continue
            store.observe(ip=ip, mac=mac, vendor=mac_vendor(mac),
                          method="passive", packet=True)

    print(f"[passive] sniffing on {cfg.iface or 'default iface'} ...", flush=True)
    sniff(iface=cfg.iface, prn=handle, store=0,
          stop_filter=lambda _p: stop.is_set())


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def run(cfg: Config, once: bool) -> None:
    store = ObservationStore(cfg.db_path, cfg.sensor_id)
    stop = threading.Event()

    if cfg.passive and not once:
        threading.Thread(target=passive_listen, args=(store, cfg, stop),
                         daemon=True).start()

    try:
        while True:
            t0 = time.time()
            n = active_scan(store, cfg)
            print(f"[cycle] {n} host(s) scanned in {time.time() - t0:.1f}s; "
                  f"{store.count()} observation(s) buffered", flush=True)
            if once:
                break
            time.sleep(cfg.interval)
    except KeyboardInterrupt:
        print("\n[exit] stopping ...", flush=True)
    finally:
        stop.set()


def dump(cfg: Config) -> None:
    store = ObservationStore(cfg.db_path, cfg.sensor_id)
    print(json.dumps(store.observations(), indent=2))


def parse_args(argv: list[str]) -> tuple[Config, argparse.Namespace]:
    p = argparse.ArgumentParser(description="Outpost network sensor")
    p.add_argument("--cidr", action="append", default=[],
                   help="target subnet, e.g. 192.168.2.0/24 (repeatable)")
    p.add_argument("--sensor-id", help="stable sensor id (default: outpost-<hostname>)")
    p.add_argument("--iface", help="interface for ARP/sniff (default: scapy's)")
    p.add_argument("--ports", help="comma-separated ports (default: common set)")
    p.add_argument("--interval", type=int, default=300,
                   help="seconds between active scans (default 300)")
    p.add_argument("--db", default="outpost_observations.db", help="SQLite buffer path")
    p.add_argument("--once", action="store_true", help="single scan then exit (no passive)")
    p.add_argument("--no-passive", action="store_true", help="disable passive sniffing")
    p.add_argument("--dump", action="store_true",
                   help="print buffered observations as schema-valid JSON and exit")
    a = p.parse_args(argv)

    cfg = Config(
        cidrs=a.cidr,
        sensor_id=a.sensor_id or default_sensor_id(),
        iface=a.iface,
        ports=[int(x) for x in a.ports.split(",")] if a.ports else list(DEFAULT_PORTS),
        interval=a.interval,
        db_path=a.db,
        passive=not a.no_passive,
    )
    return cfg, a


def main(argv: list[str]) -> int:
    cfg, args = parse_args(argv)
    if args.dump:
        dump(cfg)
        return 0
    if not cfg.cidrs:
        print("error: at least one --cidr is required (or use --dump)", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("warning: not root -- ARP sweep and sniffing will likely fail.\n"
              "         re-run with sudo for full discovery.", file=sys.stderr)
    run(cfg, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
