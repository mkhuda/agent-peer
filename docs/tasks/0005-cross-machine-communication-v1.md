# 0005 - agent-peer can talk across machines (v1.0)

**Status:** todo (v1.0 scope - not started, architecture only)
**Owner:** unassigned
**Files:** likely a new `agent_peer/remote.py` (TCP listener/dialer), changes to `listener.py`
(bind both UDS and TCP), `sender.py` (route by node prefix), `registry.py` (node-aware
addressing), `protocol.py` (cluster secret + replay-protection helpers), plus new docs

## Why this exists

For v1.0, the owner wants `agent-peer` to reach sessions on other machines - a home laptop, a
cloud VPS - not just processes on the machine it's running on. Today `agent-peer` is same-machine
only - the whole design (UDS sockets in `/tmp/cc-socks/`, registry in `~/.claude/sessions/`)
assumes every peer is a process on this one machine.

This is architecture research turned into a plan, not yet built or tested against anything real -
informed by `agy-8664`'s live analysis (2026-09-18), not verified independently yet.

## What to do

Recommended shape, per `agy-8664`'s proposal:

1. **Add a TCP listener alongside UDS, don't replace it.** UDS stays the same-machine transport
   (sub-1ms, no reason to touch it). A new TCP listener binds specifically to the machine's
   Tailscale IP (`100.x.y.z:<port>`), only reachable from inside the tailnet - never binds to
   `0.0.0.0`.
2. **Node-aware addressing.** Cross-machine targets need a `<node>/<session-name>` format (e.g.
   `vps-sgp/codex-12`) so `agent-peer send` can tell a local name from a remote one and route
   accordingly. Needs a node identity/label (hostname? a configured name?) - not yet decided.
3. **Application-layer auth on top of Tailscale's own device auth** - Tailscale's WireGuard layer
   already handles encryption/NAT traversal/device identity, but `agent-peer` still needs its own
   trust boundary so not every process on every tailnet device can message every other:
   - A pre-shared cluster secret (e.g. `~/.claude/cluster.key`), used for HMAC-SHA256 signing of
     cross-machine frames.
   - Replay protection: each frame carries a UTC timestamp + a nonce, receiver rejects anything
     more than ~5s old or a reused nonce.
4. **Stays zero-dependency.** Everything needed (`socket`, `ssl`, `hmac`, `hashlib`, `secrets`,
   `json`) is already in the stdlib - confirmed by `agy-8664`, not yet verified by writing actual
   code against it.
5. **Opt-in, off by default.** Cross-machine listening should require an explicit flag/config, not
   silently start listening on a network-reachable port just because Tailscale happens to be
   installed.

---

## LAN Auto-Discovery (Non-Tailscale: 2 Windows PCs + Mac on same Wi-Fi)

For machines on the same local subnet without Tailscale:

### 1. Transport Choice: Raw UDP Multicast / Broadcast (Stdlib Only)
- **Selected: Raw UDP Multicast (`239.255.42.99:47890`) or Subnet Broadcast (`<subnet_bcast>:47890`).**
  - **Zero Dependency:** 100% Python stdlib (`socket` with `AF_INET`, `SOCK_DGRAM`, `SO_BROADCAST`/multicast options). Less than 60 lines of code.
  - **Cross-Platform:** Works natively across Windows, macOS, and Linux without platform-specific system services (Avahi/Bonjour).
  - **Why not mDNS/DNS-SD via `python-zeroconf`:** `zeroconf` violates the zero-dependency core principle. Writing a raw binary DNS RFC 1035 parser in stdlib adds brittle complexity. For `agent-peer`, we don't need Apple AirPlay compatibility; we only need peer IP/port rendezvous.
  - **Why not SSDP (UPnP):** Port 1900 is often aggressively blocked or inspected by Windows Defender and modern routers.

### 2. Unified Node Addressing (`<node>/<session-name>`)
LAN discovery and Tailscale coexist under the exact same abstraction:
- Both feed into a unified in-memory **`NodeRegistry`**:
  ```python
  {
      "win-pc1": {"transport": "lan", "endpoint": "192.168.1.50:47891", "last_seen": 1726671000},
      "vps-sgp": {"transport": "tailscale", "endpoint": "100.80.20.10:47891", "last_seen": 1726671005}
  }
  ```
- To send: `agent-peer send win-pc1/claude-worker "msg"` works identically whether `win-pc1` was discovered via LAN multicast or Tailscale.
- If a node is reachable via both LAN and Tailscale, LAN is prioritized (lower latency).

### 3. Security Boundary for LAN Discovery (Anti-Snoop & Anti-Injection)
Because UDP broadcast/multicast is unencrypted at the Wi-Fi level:
- **Signed Discovery Beacons:** Each beacon is signed using the shared `~/.claude/cluster.key` via HMAC-SHA256:
  `{"node": "win-pc1", "port": 47891, "t": 1726671000, "nonce": "...", "sig": "<hmac>"}`
  Rogue devices on the same Wi-Fi without the `cluster.key` cannot forge beacons or discover node names.
- **Replay & Jitter:** Beacons carry timestamp; receivers drop packets older than 5s.
- **Pairing Flow:** A simple CLI command `agent-peer pair --export` (shows 32-hex string) and `agent-peer pair <key>` to set up `~/.claude/cluster.key` on new machines.

---

## Open Questions & Resolution
- How does `agent-peer list` discover *remote* sessions - does each node need to periodically
  announce itself, or is discovery pull-based (ask a known node "who do you have")?
- What happens to `wait`'s cursor/backlog semantics across a network link that can drop and
  reconnect, unlike a local UDS connection?
- Self-hosted WebSocket relay was raised as a fallback for machines that can't share a tailnet
  (e.g. a public server behind NAT nobody wants on the same tailnet) - not designed at all yet.

## Acceptance

The owner sends a message from `agent-peer` on one machine to a session on a different machine
(over Tailscale, a public server, or a home laptop) and it actually arrives.

## Hand-walk

<empty>
