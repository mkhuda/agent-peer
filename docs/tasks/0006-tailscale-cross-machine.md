# 0006 - agent-peer can talk across machines over Tailscale (v1.0, priority 2)

**Status:** todo (v1.0 scope - not started, architecture only)
**Owner:** unassigned
**Files:** likely a new `agent_peer/remote.py` (TCP listener/dialer bound to the Tailscale IP),
changes to `listener.py` (bind both UDS and TCP), `sender.py` (route by node prefix),
`registry.py` (shares the `NodeRegistry` from 0005), `protocol.py` (shares the cluster secret +
replay-protection helpers from 0005)

## Why this exists

For v1.0, the owner also wants `agent-peer` to reach machines that aren't on the same local
network - a home laptop, a cloud VPS - via Tailscale. This is priority 2, after 0005 (LAN
auto-discovery): it needs a Tailscale account and daemon installed on every machine involved
before anything can be tested, so it's slower to get a first real end-to-end win from. It
deliberately builds on the same `NodeRegistry`/cluster-secret foundation as 0005 rather than
duplicating it.

Architecture research from `agy-8664` (2026-09-18), not yet built or tested against anything real.

## What to do

1. **Add a TCP listener alongside UDS, don't replace it.** UDS stays the same-machine transport
   (sub-1ms, no reason to touch it). A new TCP listener binds specifically to the machine's
   Tailscale IP (`100.x.y.z:<port>`), only reachable from inside the tailnet - never binds to
   `0.0.0.0`.
2. **Node-aware addressing**, shared with 0005: `<node>/<session-name>` (e.g. `vps-sgp/codex-12`)
   so `agent-peer send` can tell a local name from a remote one and route accordingly. Needs a
   node identity/label (hostname? a configured name?) - not yet decided.
3. **Application-layer auth on top of Tailscale's own device auth** - Tailscale's WireGuard layer
   already handles encryption/NAT traversal/device identity, but `agent-peer` still needs its own
   trust boundary so not every process on every tailnet device can message every other. Reuses
   0005's pre-shared cluster secret (`~/.claude/cluster.key`, HMAC-SHA256) and replay protection
   (timestamp + nonce, reject anything more than ~5s old) rather than a separate scheme.
4. **Stays zero-dependency.** Everything needed (`socket`, `ssl`, `hmac`, `hashlib`, `secrets`,
   `json`) is already in the stdlib - confirmed by `agy-8664`, not yet verified by writing actual
   code against it.
5. **Opt-in, off by default.** Cross-machine listening should require an explicit flag/config, not
   silently start listening on a network-reachable port just because Tailscale happens to be
   installed.

## Open questions, not yet answered

- How does `agent-peer list` discover *remote* sessions - does each node need to periodically
  announce itself, or is discovery pull-based (ask a known node "who do you have")?
- What happens to `wait`'s cursor/backlog semantics across a network link that can drop and
  reconnect, unlike a local UDS connection?
- Self-hosted WebSocket relay was raised as a fallback for machines that can't share a tailnet
  (e.g. a public server behind NAT nobody wants on the same tailnet) - not designed at all yet.

## Acceptance

The owner sends a message from `agent-peer` on one machine to a session on a different machine
over Tailscale and it actually arrives.

## Hand-walk

<empty>
