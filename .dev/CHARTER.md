# Charter

Written by the owner. Read by every session at start. Changes rarely.

## What we are building, and for whom

`agent-peer` is a local IPC mesh so any agent harness (Claude Code, Antigravity/agy,
pi, opencode, others later) can discover, message, and reactively wake up any other
harness session on the same machine — no polling, no per-harness configuration. For
the owner (rg): pakai agent-peer sehari-hari, lintas harness, tanpa was-was ada bug
tersembunyi yang baru ketahuan pas dipakai beneran.

## Current milestone

**M1: Pakai agent-peer tanpa was-was ada bug tersembunyi.**

Bereskan temuan prioritas tinggi dari 4 dokumen review (`.dev/reviews/*.md`) yang
ditulis 17 Sep 2026 oleh agy, pi, opencode, dan Claude — terutama yang dikonfirmasi
independen oleh 2+ reviewer, dan yang menyangkut keamanan/kebenaran data (bukan cuma
kenyamanan dokumentasi).

**Gate:** Owner menjalankan `agent-peer list` dan `agent-peer wait` sekali lagi
setelah semua task M1 ditutup, dan hasilnya gak ada yang aneh.

## Who owns what

Paths, never responsibilities. A path with no owner is nobody's, and changing it is a
request to the owner rather than an edit.

| Path | Owner |
| --- | --- |
| `agent_peer/*.py` (core package) | `agent-peer-e4` |
| `~/.gemini/antigravity/skills/agent-peer/SKILL.md`, `~/.gemini/GEMINI.md` (bagian agent-peer) | `antigravity-test` |
| `~/.pi/agent/skills/agent-peer/SKILL.md`, `~/.pi/agent/AGENTS.md` (bagian agent-peer) | `pi-98661` |
| `~/.config/opencode/skills/agent-peer/SKILL.md`, `~/.config/opencode/AGENTS.md` (bagian agent-peer) | `opencode-15297` |
| `.dev/reviews/<nama>-review.md` | sesi yang namanya sama, masing-masing punya sendiri |
| `docs/`, `README.md`, `.dev/CHARTER.md`, `pyproject.toml` | owner only (rg) |

**Not owned by anyone working here:** git operations (commit/push/branch) — owner only,
sesuai instruksi global rg. Menghapus/mematikan proses agent lain — owner only.

## Decisions that are already made

- **Test policy:** semua verifikasi sesi 17 Sep 2026 dilakukan manual live (ad-hoc
  script + tes lintas-harness nyata), belum ada automated test suite di repo — itu
  sendiri salah satu temuan M1 (lihat review), tapi belum ada aturan test-wajib buat
  task lain sampai suite dasarnya ada.
- **Jangan auto-spawn proses baru sebagai side-effect command lain.** Dicoba sekali
  (`wait` auto-start `listen`), dibatalkan owner — risiko listener menumpuk diam-diam.
  Kalau butuh proses baru, harus eksplisit dari user/agent, bukan otomatis.
- **`wait` tidak refuse total kalau gak reachable** — cuma warning (stderr), tetap
  lanjut proses (baca backlog tetap valid use-case walau gak ada listener hidup).
- **Auto session-name (`detect_harness_identity`) sengaja gak nebak env var per-tool**
  — dipilih walk-parent-process-chain karena lebih reliable & gak butuh kerjasama tiap
  harness. Tau keterbatasannya (subagent share-parent collision) — itu task M1.
- **`--timeout` bukan default yang dipaksakan** — sempat direkomendasikan lalu ditolak
  owner untuk mandatory-standby pattern (background task); biarkan per-context, jangan
  hardcode rekomendasi timeout tertentu di skill manapun.
- Editable install (`uv tool install --editable .`) — perubahan source otomatis aktif
  ke binary terinstall tanpa reinstall manual. Proses yang sudah jalan sebelum edit
  tetap pakai kode lama sampai di-restart.
