# Review & Audit `agent-peer` + `SKILL.md` dari Perspektif Harness pi

**Penulis:** pi agent (sesi `pi-98661`)
**Tanggal:** 17 September 2026
**Konteks Sesi:** Audit bash tool pi (timeout = SIGKILL process tree), koreksi framing SKILL.md "background task", relay test 3-harness (agy → pi → opencode → agy), dan bedah source (`protocol.py`, `inbox.py`, `listener.py`, `registry.py`, `sender.py`, `cli.py`) plus baca ulang `docs/` & `.dev/HANDOFF.md`.

---

## 1. Executive Summary

`agent-peer` terbukti andal untuk kolaborasi multi-harness dari sisi pi: auto-wakeup via `wait` (tanpa timeout) bekerja end-to-end, relay 3-harness selesai tanpa polling, dan auto-name (`pi-98661`) stabil. Temuan-temuan HANDOFF sebelumnya (cursor/backlog, lock anti-race, auth-fix, ENGINE/cwd hardcode) sudah terverifikasi benar di source saat ini.

Tapi dari sudut pandang harness pi, ada beberapa hal yang **belum tercakup** di review agy / opencode-report / HANDOFF:

- **BUG UX terverifikasi:** status `new-msg` di session json tidak pernah di-reset ke `idle` setelah pesan dibaca → `agent-peer list` selamanya menampilkan `new-msg` untuk sesi yang pernah menerima pesan, menyesatkan deteksi "siapa yang belum baca pesan".
- **Ambiguitas semantik "Delivered":** `agent-peer send` mencetak "✅ Delivered" saat pesan sampai ke *listener socket*, BUKAN saat agent membacanya. Tidak ada delivery-receipt/ack-read. Dalam sesi ini foreman berulang kali menyimpulkan "pesan sampai dibaca" dari "Delivered" — asumsi yang salah dan berpotensi mengganggu koordinasi.
- **Label sender `uds:` = PID, bukan nama sesi:** pesan dari sesi agy yang listen muncul sebagai `From: uds:/tmp/cc-socks/52285.sock` (label `52285`), bukan `antigravity-test`. Kurang informatif untuk debugging/notifikasi.
- **Tidak ada test suite sama sekali di repo** — semua validasi live-manual. Pola ini sudah membuktikan rapuhnya: beberapa bug (ENGINE hardcode, cwd hardcode, auth-bypass) lolos sampai ditemukan di sesi live.

---

## 2. Temuan Terverifikasi (Empiris + Source)

### 2.1 Status `new-msg` Tidak Pernah Di-reset ke `idle` — BUG UX
- **Lokasi:** `listener.py:244` (set `new-msg` saat pesan masuk) vs `inbox.py` (`wait_for_message` tidak menyentuh session json sama sekali).
- **Bukti empiris:** `agent-peer list` menampilkan `pi-98661 ... new-msg`, `antigravity-test ... new-msg`, `opencode-15297 ... new-msg` — padahal SEMUA pesan sudah dibaca via `wait` berkali-kali. `grep "idle\|new-msg"` di codebase mengonfirmasi: `idle` hanya ditulis sekali di `setup()` (`listener.py:121`); tidak ada kode yang menulis `idle` setelah itu.
- **Dampak:** kolom STATUS di `agent-peer list` — yang tampaknya mengindikasikan "ada pesan belum dibaca" — jadi permanen `new-msg` setelah pesan pertama. Siapa pun yang memakai kolom ini untuk memutuskan "apakah saya perlu baca inbox" akan terkecoh.
- **Rekomendasi:** reset `status: "idle"` (+ `statusUpdatedAt`) saat cursor maju di `wait_for_message` / `clear_inbox`, atau saat `wait` return. Paling rapi: `wait_for_message` sudah tahu session mana yang maju cursornya — sekaligus update session json di `~/.claude/sessions/<pid>.json`.

### 2.2 "Delivered" ≠ "Dibaca" — Ambiguitas Semantik yang Bahaya untuk Koordinasi
- **Lokasi:** `sender.py:send_message` return `elapsed_ms` setelah socket send; `cli.py:cmd_send` mencetak `✅ Delivered in <ms>ms to <target>`.
- **Fakta:** delivery = frame sukses ditulis ke socket listener + masuk inbox. Tidak ada mekanisme ack "sudah dibaca agent" (agent baca via `wait` yang menarik cursor).
- **Bukti empiris sesi ini:** foreman (claude-test) bilang "Dua-duanya sampai, makasih" berdasarkan pesan send saya "✅ Delivered" — padahal yang terverifikasi hanya sampai listener, bukan dibaca. Dalam alur relay multi-hop (agy → pi → opencode → agy) kalau satu hop macet sebelum `wait`, "Delivered" di hop sebelumnya memberi kesan palsu bahwa pesan sudah diproses.
- **Rekomendasi (minimal):** ubah teks jadi `✅ Delivered to listener` + tambahkan baris penjelas (misal "this means received by <name>'s listener, not yet read"). Opsi lebih kuat: tambahkan status `read` per-message (agent yang `wait`-kan pesan menandai `read: true`), dan `agent-peer status`/`list` menampilkan "unread" vs "read" per sesi. Ini turn-key untuk deteksi "sesi mana yang belum memproses instruksi".

### 2.3 Label Sender `uds:` = PID Bukan Nama Sesi
- **Lokasi:** `protocol.py:format_user_frame` → `origin_from = f"uds:{from_sock}"`; `listener.py:sender_label` parsing `uds:` → `os.path.basename(...).replace(".sock","")` = **PID murni**.
- **Bukti empiris:** pesan dari `antigravity-test` (yang listen, jadi punya socket) tercatat `From: uds:/tmp/cc-socks/52285.sock`; sementara pesan dari `claude-test` (tidak listen, tidak punya socket) tampil `From: claude-test`. Jadi label berisi PID untuk sesi yang terdaftar, dan nama hanya untuk sesi yang tidak terdaftar — **terbalik dari yang paling informatif**.
- **Dampak:** notifikasi macOS, judul di `agent-peer list`, dan log tampil sebagai angka PID; menyulitkan debug siapa pengirim sebenarnya.
- **Rekomendasi:** di `handle_client`/`process_incoming_frame`, resolve `uds:*` ke nama sesi via `resolve_session(pid)` (registry) bila ada; fallback ke PID bila tidak ditemukan.

---

## 3. Temuan Source-Only (Belum Terverifikasi Live, tapi Terbaca di Kode)

### 3.1 `peerFeatures` Mengiklankan `notify_idle` yang Tidak Diimplementasikan + `version` Hardcoded
- `listener.py:105-111`: `"peerFeatures": ["notify_idle", "reply_across_default_dirs", "artifact_yield"]`; `version: "2.1.270"` hardcoded (`listener.py:112`).
- HANDOFF sendiri mencatat `notify_idle` "field kosmetik, tidak ada implementasi". Mengklaim fitur yang tidak ada ke konsumen protocol (Claude Code asli membaca `peerFeatures`) berisiko: agent peer bisa mengira sesi punya kemampuan `notify_idle`/`artifact_yield` lalu bergantung padanya.
- **Rekomendasi:** buang `notify_idle` (atau implementasi), jadikan `version` dinamis dari package (`__version__`), atau minimal komentari kalau hardcode ini sengaja untuk kompatibilitas.

### 3.2 `detect_harness_identity` Tidak Skip `sshd`/`tmux`/`screen`
- `_GENERIC_PROC_NAMES` (`protocol.py`) memuat `zsh/bash/sh/dash/tcsh/csh/ksh/fish/login/env/sudo/su/node/uv/uvx/python/python3` — tapi TIDAK memuat `sshd`, `tmux`, `screen`, `sshd`-anak, container runtime.
- Skenario nyata untuk pi: pi dijalankan dari laptop via SSH (chain `pi ← bash ← sshd`) atau di dalam tmux session (`pi ← zsh ← tmux`). Auto-name akan jadi `sshd-<pid>` / `tmux-<pid>` — nama identitas yang salah & tidak stabil untuk kolaborasi.
- **Rekomendasi:** tambahkan `sshd`, `tmux`, `screen`, `ssh`, `mosh-server`, `containerd-shim`, dst ke daftar skip; tambahkan unit test untuk chain ini supaya tidak regresi.

### 3.3 `client.settimeout(5.0)` Bisa Memutus Frame Besar yang Terfragmentasi
- `listener.py:handle_client`: satu timeout 5s untuk seluruh koneksi. Pesan JSON besar (> socket buffer) yang terkirim dengan jeda antar-pecahan > 5s akan di-treat sebagai timeout → koneksi ditutup → pesan drop.
- Untuk frame kecil (pesan tipikal) tidak masalah. Tapi `agent-peer` sendiri menyarankan "jangan kirim diff besar" — tidak ada enforcement ukuran frame; siapa pun yang kirim payload MB-scale berisiko.
- **Rekomendasi:** timeout per-`recv` (reset timer tiap data datang) + dokumentasi limit ukuran frame; atau batasi ukuran frame di sisi penerima (drop + log bila > N MB).

### 3.4 Partial-Match `resolve_session` Bisa Mengirim ke Sesi yang Tidak Dimaksud
- `registry.py`: ketika exact match tidak ada, fallback `target_lower in name`. `agent-peer send pi 1.0.0 "..."` akan cocok ke `pi-98661` — tepat bila hanya satu cocok, tanpa konfirmasi. Risiko rendah tapi nyata di ekosistem dengan banyak sesi `pi-*`/`claude-*`.
- Ambiguity sudah di-guard (error kalau >1 cocok). Batas yang tersisa: **exactly-satu-tapi-salah**. Rekomendasi: kalau ada exact match, prioritas exact; kalau partial dan jumlah sesi > 5, tampilkan "kurir match" konfirmasi/echo di output.

### 3.5 Lock File Tidak Pernah Di-unlink (Sampah Menumpuk)
- `cmd_wait` (`cli.py`) membuat `~/.agent-peer/locks/<session>.lock` dan melepas `flock` saat selesai — tetapi file-nya tidak di-`unlink`. Aman secara fungsional (flock di-release OS saat proses mati), tapi direktori `locks/` menumpuk file permanen per sesi. Minor; rekomendasi: `os.unlink(lock_path)` di `finally`.

### 3.6 `_read_cursor` Fallback ke `time.time()` Saat Cursor Corrupt — Backlog Bisa "Hilang"
- `inbox.py:_read_cursor`: kalau file cursor tidak bisa di-parse, fallback `return time.time()` → cursor melompat ke "sekarang"; SEMUA pesan yang belum dibaca sebelum korupsi terlewat permanen (keanggap lama).
- Lebih aman: fallback `0` (anggap belum ada yang dibaca — jangan sampai melewatkan pesan) dan warning ke stderr. Konsisten dengan semangat "never lose a message" yang sudah diterapkan di backlog-merge.

---

## 4. Penilaian SKILL.md pi (Versi Saat Ini yang Sudah Dikoreksi)

Yang sudah bagus (hasil koreksi sesi ini, terverifikasi benar vs source):
- `listen` = detach di level shell dengan `&`, karena proses-nya tidak pernah exit sendiri (kalau dipanggil sinkron = freeze turn selamanya) ✓
- `wait` = tool call sinkron TERAKHIR di turn, tanpa `--timeout` agent-peer MAUPUN `timeout` bash tool (dua hal beda yang sempat ketuker) ✓
- Penjelasan bash tool pi: sinkron, `timeout` = SIGKILL process tree, tidak ada auto-relaunch ✓

Celah yang masih tersisa di SKILL.md pi:

1. **`agent-peer listen > log 2>&1 &` tanpa `nohup`/`disown` belum terverifikasi bertahan di semua harness.** Di sesi ini yang TERBUKTI bertahan adalah `nohup agent-peer listen > /tmp/ap-listen.log 2>&1 & disown`. Di bash tool pi, proses anak detached (`detached: true`) dan listener saya (PID 512) masih hidup — tapi itu dengan `nohup` tambahan. **Rekomendasi:** tambahkan `nohup` + `disown` di contoh SKILL.md (aman untuk pi/opencode/bash apa pun, tidak merugikan kalau harness lain punya background-task), dan tulis satu kalimat "verify listener survives the tool call (`agent-peer list` shows you before you rely on it)".
2. **Tidak ada satu kalimat pun soal makna "Delivered"** (lihat 2.2) — tambahkan: "Receiving the delivery confirmation does NOT mean the peer has read your message; it means their listener wrote it to their inbox."
3. **Tidak ada guidance subagent/parallel-task** (ditemukan agy juga): kalau sesi menjalankan subagent/parallel task yang butuh komunikasi independen, wajib `--name <unik>` — kalau tidak, semua subagent mewarisi nama induk (`pi-<pid>`) dan saling tabrak di lock `wait` serta inbox campur. SKILL.md pi boleh menambahkan satu baris ini (agy sudah merekomendasikan hal sama untuk harness-nya).
4. **Belum ada langkah verifikasi eksplisit setelah `listen`:** "konfirmasi di `agent-peer list` bahwa namamu muncul (ENGINE benar, STATUS idle)" — persis langkah yang menangkap bug ENGINE/cwd di sesi ini. Satu baris ini murah dan menyelamatkan sesi-sesi berikutnya.

---

## 5. Prioritas Perbaikan yang Disarankan (untuk Foreman)

| # | Temuan | Tingkat | Perbaikan |
|---|---|---|---|
| 1 | Status `new-msg` macet (2.1) | **High (UX)** | Reset status saat cursor maju / `wait` return |
| 2 | "Delivered" ≠ "dibaca" (2.2) | **High (semantik koordinasi)** | Ubah teks + opsional status read/unread |
| 3 | Tidak ada test suite (exec summary) | **High (engineering)** | Unit test untuk protocol/inbox/cursor/registry + smoke integration |
| 4 | Label sender UDS = PID (2.3) | Medium | Resolve PID → nama via registry |
| 5 | `peerFeatures`/`version` menyesatkan (3.1) | Medium | Buang klaim fitur tidak ada |
| 6 | `sshd`/`tmux` tidak di-skip (3.2) | Medium | Perluas `_GENERIC_PROC_NAMES` + test |
| 7 | SKILL.md: `nohup`/`disown`, makna Delivered, subagent `--name`, verifikasi post-listen (4) | Medium | Patch SKILL.md pi |

---

## 6. Kesimpulan

`agent-peer` secara fungsional sudah "proven" untuk pi (auto-wakeup, backlog, lock, relay 3-harness semuanya bekerja di sesi nyata). Issue paling mendesak bukan di alur inti messaging, melainkan di **semantik status yang menyesatkan** (`new-msg` macet, "Delivered" disalahartikan sebagai "dibaca") dan **tidak adanya test suite** yang membuat regresi (ENGINE/cwd/auth hardcode) baru ketahuan di sesi live. Keduanya layak dibereskan sebelum agent-peer dipakai sebagai penggerak koordinasi yang lebih serius (misal handoff task dengan banyak sesi paralel).