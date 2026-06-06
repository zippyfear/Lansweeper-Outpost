# Decision Log

Lightweight architecture decision records (ADRs). Each entry captures the
*why* so we can roll back **ideas**, not just code. Newest decisions can be
appended; superseded ones are marked, never deleted.

Format: **Context → Decision → Status → Consequences.**

---

### ADR-0001 — Hub-and-sensor split, standalone-first
- **Context:** Customers (esp. regulated/air-gapped) need asset discovery with
  no mandatory outbound traffic and full data residency.
- **Decision:** Three logical components (sensor, hub, dashboard). No component
  requires internet egress. Cloud sync is optional and deferred.
- **Status:** Accepted.
- **Consequences:** The "no phone-home" property must be protected in code —
  never bake in a required outbound call. Cloud sync stays strictly Phase 4.

### ADR-0002 — Single-VM and distributed are one codebase
- **Context:** Some customers want everything on one VM; others span subnets.
- **Decision:** Components are always logically separate; co-location is a
  deployment topology, not a code variant. Default lite install = one VM,
  components over localhost.
- **Status:** Accepted.
- **Consequences:** Code must never assume sensor and hub share a host.

### ADR-0003 — Prototype the sensor in Python; Go is the production target
- **Context:** The valuable, hard-to-get-right part is *what* we collect and
  the asset schema, which is language-portable. Velocity matters now.
- **Decision:** Build the sensor prototype in Python (reuses existing skill set
  and Flow Dashboard patterns). Plan a Go rewrite for production once behaviour
  and schema are locked (single static binary, low footprint, easy deploy).
- **Status:** Accepted (rewrite pending Phase 1 completion).
- **Consequences:** Keep discovery logic and schema cleanly separable so the
  Go port is mechanical, not a redesign.

### ADR-0004 — Local SQLite as the sensor's store-and-forward buffer
- **Context:** A sensor must keep working if the hub is unreachable.
- **Decision:** Sensor writes normalized assets to a local SQLite cache; the
  (future) hub shipper drains from it. The cache is the buffer.
- **Status:** Accepted.
- **Consequences:** Runtime DB files are git-ignored. Shipper must be
  idempotent against the cache.

### ADR-0005 — Assets keyed by IP at the sensor; correlation at the hub
- **Context:** A single device can appear under multiple IPs / across sensors.
- **Decision:** Sensors key assets by IP (simple, local). MAC-based and
  cross-sensor de-duplication/correlation is a Hub responsibility.
- **Status:** Accepted.
- **Consequences:** Sensors stay dumb and fast; correlation complexity is
  centralized where it has the full picture.

### ADR-0006 — ARP for local-subnet discovery → justifies multi-sensor
- **Context:** ARP only reaches the local broadcast domain.
- **Decision:** Use ARP as the primary local-subnet liveness/MAC primitive;
  cover routed segments by deploying a sensor per segment rather than
  cross-subnet scanning from one point.
- **Status:** Accepted.
- **Consequences:** Multi-sensor fan-in is a core design assumption, not an
  add-on.

### ADR-0007 — Documentation & versioning discipline
- **Context:** Need clean rollbacks, redeployability, and a shared source of truth.
- **Decision:** Semver project-wide; per-file version headers + change history;
  maintain master / changelog / decisions / roadmap; small frequent commits.
- **Status:** Accepted.
- **Consequences:** Every release touches `master.md` and `CHANGELOG.md`.

### ADR-0008 — Credential & action boundary
- **Context:** Clear ownership of irreversible / credentialed actions.
- **Decision:** The human runs all GitHub pushes and server deploys on the dev
  server, which holds the credentials. The assistant authors code/docs and
  produces commit-ready bundles + exact commands. The autonomous agentic loop
  (parallel subagents, self-driven commits/SSH) is run via Claude Code locally.
- **Status:** Accepted.
- **Consequences:** No credentials handled in chat; human stays in control of
  push/deploy/delete.

### ADR-0009 — Conservative identity resolution
- **Context:** Randomized/private MACs and DHCP churn make naive matching
  either over-count one device or wrongly merge two. (Full discussion in
  `docs/SCHEMA.md` §5.)
- **Decision:** Correlate on strong identifiers (serial/hw_uuid) and MAC first;
  treat IP-only sightings as **provisional**; let the hub **merge** later as
  better data arrives. Wrong merges are harder to unwind than splits.
- **Status:** Accepted (locked with schema wire v0.1, 2026-06-06).
- **Consequences:** Hub must support provisional assets and later merge/split.
  Sensors stay identity-agnostic (emit observations only).

### ADR-0010 — Single installable `outpost/` package
- **Context:** Sensor, hub, and dashboard must share one schema contract rather
  than duplicate it.
- **Decision:** Consolidate code into one installable package (`pyproject.toml`,
  `outpost/`), with the schema as a shared module both sides import and
  validate against. Run via `python -m outpost.sensor` after `pip install -e .`.
- **Status:** Accepted.
- **Consequences:** Run command changed from `python3 sensor.py`. Hub and
  dashboard will live under `outpost/` too.
