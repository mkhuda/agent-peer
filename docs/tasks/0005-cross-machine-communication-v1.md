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

Open questions, not yet answered:
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
