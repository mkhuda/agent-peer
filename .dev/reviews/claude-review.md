# Tinjauan `agent-peer` — perspektif Claude Code (implementer sesi ini)

**Dari:** sesi Claude Code `agent-peer-e4` (yang mengerjakan sebagian besar fix
di sesi kolaborasi ini)
**Konteks:** bagian dari kolaborasi 4-arah (agy, pi, opencode, Claude) untuk
menemukan celah tersisa dan mematangkan `agent-peer` menuju siap-publish.

Fokus tinjauan ini: konsolidasi backlog yang SUDAH diketahui tapi belum
dikerjakan (supaya gak hilang di tengah banyaknya perubahan sesi ini), plus
beberapa gap kematangan proyek yang belum disentuh sama sekali.

---

## 1. Backlog keamanan/reliability yang sudah ditemukan, belum dikerjakan

Semua ini berasal dari `docs/agent-peer-weaknesses-report.md` (ditulis sesi
`antigravity-test` sebelumnya) — dicek masih valid, belum ada satupun yang
dikerjakan:

1. **File permission** — `inbox.jsonl`, `inboxes/*.jsonl`, `cursors/*.json`,
   `locks/*.lock` semua dibuat dengan umask default (bukan `chmod 0600`
   seperti socket & key file). User lain di mesin yang sama (kalau ada) bisa
   baca seluruh histori chat plain-text. Fix murah: `os.chmod(path, 0o600)`
   tiap kali file-file ini dibuat/ditulis pertama kali.
2. **Race condition non-atomic write** — `append_inbox` (`inbox.py`) pakai
   `open(...,"a")` polos tanpa `fcntl.flock`. Ini genuinely cross-PROCESS,
   bukan cuma cross-thread: `INBOX_FILE` global dipakai bareng oleh SEMUA
   listener yang jalan di mesin (agy, pi, opencode, dst sekaligus). Kalau 2
   listener nulis baris besar bersamaan, interleaving bisa merusak parsing
   JSON baris itu (silently dropped di `read_inbox`, bukan crash — makin
   berbahaya karena gak kelihatan).
3. **`agent-peer inbox --clear` tidak reset cursor** — edge case rendah
   (butuh jam mundur buat benar-benar bug), tapi tetap counter-intuitive:
   user yang expect "clear = reset semua" akan bingung kalau perilaku unread
   gak ikut ke-reset.
4. **Symlink squatting `/tmp/cc-socks/`** — low severity (sticky bit /tmp
   sudah melindungi sebagian besar), tapi tetap worth `os.chmod` yang lebih
   ketat pada direktori socket kalau mau benar-benar aman dari local user lain.
5. **`osascript`/`terminal-notifier` spam tak dibatasi** saat burst pesan
   masuk cepat — rendah untuk pemakaian personal, tapi kalau `agent-peer`
   dipublish dan dipakai orang dengan traffic lebih tinggi, ini bisa jadi
   masalah nyata (banyak proses notifikasi ke-spawn tanpa throttle).

## 2. `docs/stale-listener-detection.md` masih rencana, belum kode

Sudah ada analisis lengkap (kenapa deteksi "detached" gak valid, harus pakai
duplikat-nama + recency + command `stop` eksplisit) tapi **belum ada satupun
baris kode buat itu**. `antigravity`/`antigravity-2` masih nyangkut di
`agent-peer list` sampai sekarang (dari awal sesi ini) sebagai bukti hidup
masalah ini belum tertangani.

## 3. Risiko auto-name collision pada arsitektur multi-child-per-parent

*(Saya lihat draft ringkasan agy juga menyentuh ini dari sudut pandangnya
sendiri — saya catat versi saya karena implikasinya penting.)*

`detect_harness_identity()` pakai PID **proses ancestor pertama yang bukan
shell generik** sebagai identitas stabil. Ini valid untuk kasus "satu
window/sesi = satu proses harness yang jelas". Tapi kalau satu proses induk
(mis. satu app Antigravity, PID 33402) menjalankan **banyak sub-sesi/tab
paralel** yang semuanya punya proses induk sama persis, mereka semua akan
resolve ke nama auto-generated **yang identik** (`agy-33402`) — inbox,
cursor, DAN lock bakal ke-share tanpa sadar antar sub-sesi yang sebenarnya
independen. Ini belum pernah benar-benar diuji sesi ini (semua test kita
kebetulan 1 harness = 1 proses induk unik), jadi masih teori, tapi
arsitekturnya real dan worth diverifikasi sebelum publish.

## 4. Kematangan proyek untuk publish (belum disentuh sama sekali sesi ini)

1. **Tidak ada automated test suite di repo.** Semua verifikasi sesi ini
   (cursor, lock, auth-gate, dst) dilakukan lewat script ad-hoc di scratchpad
   session — gak ada satupun yang jadi `tests/` permanen di repo. Kalau
   dipublish, kontributor lain gak punya safety net buat regresi.
2. **Versi masih `0.1.0`** (`pyproject.toml`) walau sudah banyak fitur besar
   ditambahkan sesi ini (unread cursor, lock, auto-name, engine-detection,
   cwd-fix, auth-fix). Gak ada `CHANGELOG.md` sama sekali.
3. **Skill template belum masuk repo** — ini pertanyaan yang sempat
   ditanyakan user di awal sesi kolaborasi ini, belum dijawab tuntas:
   `SKILL.md` untuk agy/pi/opencode semuanya cuma hidup di
   `~/.gemini/...`/`~/.pi/...`/`~/.config/opencode/...` milik user LOKAL,
   TIDAK ada salinannya di repo `agent-peer` sendiri. Kalau di-publish
   sekarang, orang lain yang clone repo ini sama sekali gak dapat skill-nya
   — harus rekonstruksi manual dari nol. Rekomendasi: tambah folder
   `skills/<harness>/SKILL.md` di repo sebagai source-of-truth, lalu
   dokumentasikan cara symlink/copy ke lokasi masing-masing tool di README
   (idealnya lewat install script kecil, biar tetap sinkron kayak filosofi
   `uv tool install --editable`).
4. **Tidak ada `agent-peer stop`/`prune` command** — dibahas di
   `docs/stale-listener-detection.md`, masih rencana.

## 5. Yang sudah TERBUKTI solid (biar gak cuma daftar kekurangan)

- Cursor/backlog-merge, lock, auto-name, engine-detection, cwd-fix — semua
  tervalidasi end-to-end di 3 harness berbeda (agy, pi, opencode) plus relay
  3-arah nyata antar mereka. Ini bukan klaim, ada bukti file (cursor/inbox/
  lock timestamp) yang dicek silang tiap kali.
- Auth-bypass (token gak pernah di-enforce) — ditemukan dan difix, dites
  reject/accept dengan benar.

## Rekomendasi prioritas kalau mau ditindaklanjuti

Urutan realistis (murah→mahal, dampak tinggi dulu):
1. `chmod 0600` di file inbox/cursor/lock (murah, dampak keamanan jelas).
2. Test suite dasar (`tests/`) sebelum publish — minimal cover cursor logic
   + lock, karena itu yang paling rawan regresi diam-diam.
3. Skill template masuk repo + install script.
4. Sisanya (race condition write, `--clear` reset cursor, stale-listener
   command, auto-name collision) — dokumentasikan sebagai known limitation
   di README kalau belum sempat dikerjakan sebelum publish, biar jujur ke
   calon pengguna.
