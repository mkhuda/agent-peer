# 0005 - agent-peer auto-discovers peers on the same LAN (v1.0, priority 1)

**Status:** todo (v1.0 scope - not started, architecture only)
**Owner:** unassigned
**Files:** likely a new `agent_peer/discovery.py` (UDP beacon sender/listener), `protocol.py`
(cluster secret + HMAC signing/verification, shared with 0006), `registry.py` (unified
`NodeRegistry`), `cli.py` (new `agent-peer pair` command), `listener.py` (start the discovery
beacon alongside the existing UDS listen)

## Why this exists

For v1.0, the owner wants `agent-peer` to reach sessions on other machines - concretely, two
Windows PCs at home plus this Mac, all on the same local network - without manually configuring
IP addresses. This is prioritized ahead of the Tailscale-based cross-machine task (0006) because
it needs **no external setup at all** (no Tailscale account, no daemon install, no remote server)
- it's testable on the owner's own home LAN right now, making it the fastest real end-to-end win.

Architecture research from `agy-8664` (2026-09-18), not yet built or tested against anything real.

## What to do

1. **Transport: raw UDP multicast/broadcast, stdlib only.** `239.255.42.99:47890` (multicast) or
   the subnet broadcast address, using `socket` with `SO_BROADCAST`/multicast options - under 60
   lines, no external dependency, works natively on Windows/macOS/Linux without a platform service
   (no Avahi/Bonjour needed).
   - Not mDNS/DNS-SD via `python-zeroconf`: an external dependency, against this project's
     zero-dependency principle - and `agent-peer` only needs IP/port rendezvous, not
     AirPlay/printer-style service compatibility.
   - Not SSDP/UPnP: port 1900 is commonly blocked or inspected by Windows Defender and modern
     routers.
2. **Signed discovery beacons.** Each beacon (`{"node": "win-pc1", "port": 47891, "t": <unix>,
   "nonce": "...", "sig": "<hmac>"}`) is HMAC-SHA256-signed with a shared cluster secret
   (`~/.claude/cluster.key`). A device on the same Wi-Fi without that key can't forge a beacon or
   learn node names from it.
3. **Replay protection.** Receivers drop any beacon older than ~5s or carrying a reused nonce.
4. **Pairing flow.** `agent-peer pair --export` prints the local cluster key (32 hex chars);
   `agent-peer pair <key>` installs it on a new machine. No pairing key installed means that
   machine's beacons are ignored and it can't see anyone else's.
5. **Unified `NodeRegistry`.** LAN-discovered peers and (later, via 0006) Tailscale peers both
   live in the same in-memory table, e.g. `{"win-pc1": {"transport": "lan", "endpoint":
   "192.168.1.50:47891", "last_seen": <unix>}}`, addressed the same way:
   `agent-peer send win-pc1/claude-worker "msg"` regardless of which transport found it.

## Open questions, not yet answered

- How does `agent-peer list` show *remote* sessions found this way - a live beacon table, or does
  it still need an explicit refresh/query step?
- What happens to `wait`'s cursor/backlog semantics across a LAN link that can drop and reconnect
  (Wi-Fi roaming, sleep/wake), unlike a local UDS connection?
- Actual message delivery (not just discovery) still needs a real transport once a peer's
  IP:port is known - likely the same TCP listener 0006 designs for Tailscale, just bound to the
  LAN interface instead. Worth sharing that implementation rather than building two.

## Acceptance

The owner sends a message from `agent-peer` running on this Mac to a session on one of the two
Windows PCs at home over the LAN - without manually configuring either machine's IP address - and
it arrives.

## Hand-walk

<empty>
