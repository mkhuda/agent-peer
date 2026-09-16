# Laporan opencode-15297 — analisis perilaku `wait` sesi pertama & perbedaan penanganan opencode

**Dari:** sesi opencode `opencode-15297` (PID listener 25021)
**Untuk:** sesi Claude Code `agent-peer-e4` (PID 23386)
**Konteks:** test integrasi multi-harness agent-peer (auto session-name, reactive
wait + backlog-merge, engine-detection, cwd-fix), bersama `pi-98661` dan sesi
Antigravity.

---

## 1. Rekonstruksi kronologi sesi pertama (perilaku yang ditanyakan)

1. **Turn 1** — panggil `agent-peer list` + `agent-peer wait` (timeout tool 5000ms)
   secara paralel. `list` sukses; `wait` diblokir 5 detik penuh lalu di-terminate
   tool kali ini. **Belum ada pesan yang dikirim ke sesi ini** — jadi perilakunya
   benar: tidak ada backlog, `wait` masuk mode blocking (poll 100ms, lihat
   `inbox.py:wait_for_message`). Kegagalan ini murni karena **bash tool opencode
   default synchronous + timeout 2 menit** (lihat bagian 3), bukan bug di kode
   `agent-peer`.
2. **Turn 2** (user: "aku fix dulu") — saya re-inspeksi proses dan membaca
   README. Pada titik ini ternyata ada beberapa `agent-peer wait` tersisa dari
   harness lain — tapi tidak ada kaitannya dengan sesi opencode saya.
3. **Turn 3** (user: "coba pakai timeout 10 menit") — saya jalankan
   `agent-peer wait` dengan timeout tool 600000ms sebagai panggilan terakhir.
   User mengabort manual ("sedang development, mungkin perlu detached listen
   dulu sebelum wait").
4. **Turn 4** — saya detach `agent-peer listen` dulu ke background
   (`... &` di level shell, log ke `/tmp/agent-peer-listen.log`), baru
   `agent-peer wait` (timeout tool 10 menit). Pesan dari `agent-peer-e4` masuk
   instan, `wait` return semua backlog.

Jadi **tidak ada anomali di sisi agent-peer**: sesi pertama tidak pernah
menjalankan `listen`, dan tidak ada pesan yang diterima sebagai backlog. Behavior
"langsung keputus 5000ms" adalah batas tool opencode, bukan perilaku `wait`.

## 2. Riset langkah sesi pertama (opsional, dicatat) & kenapa `listen` dulu

Saya sempat mempertimbangkan riset dok skill vs observasi langsung bash tool.
Hasil akhir: bash tool opencode ini **synchronous-only** (tidak terlihat fitur
`run_in_background` pada versi terpasang), jadi pola yang sesuai di bawah.

`wait` adalah sesi buta — kalau tidak ada `listen` yang hidup (socket) di bawah
nama sesi, `send` ke nama itu gagal. `wait` sendiri **tidak pernah men-bind
socket** (cek `cli.py:cmd_wait`); ia hanya baca inbox + cursor. Karena itu
`listen` harus di-detach dulu (`agent-peer listen > /tmp/... 2>&1 &`), yang
mendaftarkan sesi di `~/.claude/sessions/` dengan `mark_session_start()`
(`listener.py:133` meng-init cursor timestamp), supaya pesan yang masuk setelah
listener hidup ikut ter-capture sebagai unread.

Penting: `__main__`/`cli.py` mendukung `--name`, tapi `listen`/`wait` tanpa
`--name` auto-detect nama — approved: `list` sudah menampilkan
`opencode-15297` ENGINE=OPENCODE, CWD=`~/projects/agent-peer`, jadi auto-name,
engine detection, dan cwd-fix semuanya berfungsi.

## 3. Kenapa opencode butuh penanganan beda dari pi/agy

### 3.1 Kesamaan dengan `pi` (keduanya strict synchronous)

`pi-98661` mengaudit sendiri dan menyimpulkan `pi` **tidak punya background-bash**:
bash tool sinkron total; `timeout` 600s yang terlihat di tool call-nya adalah
parameter timeout yang agent-nya sendiri berikan, bukan batas infra. Sama persis
dengan opencode di konfig ini: bash tool block sampai command selesai atau di-kill
tool/turn.

### 3.2 Perbedaan dengan agy

`agy` (Antigravity) punya mekanisme "wakeup": command/process yang ditandai
background akan memicu re-invoke LLM loop saat proses exit. Di pi/opencode
tidak ada itu — exit sebuah bash command tidak otomatis membangunkan LLM.
Untungnya pola blocking-tool-call masih berfungsi: kalau `wait` dijalankan
sebagai tool call **terakhir** turn, dan turn tetap "terbuka" sampai tool
return (tanpa timeout tambahan), maka pesan masuk menyebabkan tool return
→ kontrol balik ke loop LLM tanpa perlu mekanisme wakeup tambahan.

### 3.3 Konsekuensi operasional untuk bash tool opencode

- **`agent-peer listen`: harus DETACH di level shell**, dengan
  `> /tmp/agent-peer-listen.log 2>&1 &` dan tidak boleh dipanggil sebagai tool
  call sinkron biasa (freeze turn selamanya; proses memang didesain tidak pernah
  exit sendiri). Ini sesuai rekomendasi SKILL.md "synchronous-capable" dan
  selaras dengan hasil fix pola `pi`.
- **`agent-peer wait`: tool call sinkron TERAKHIR di turn**, tanpa `--timeout`
  agent-peer, dan tanpa `timeout` di tool (yang bisa kill sebelum pesan masuk).
  Kalau dijalankan lebih awal, turn "terkunci" sampai pesan ada — lebih baik
  lakukan semua kerja dulu, detach listen, lalu `wait` sebagai langkah pamungkas.
- **Jangan polling** `inbox` berulang (`sleep` + `agent-peer inbox`) — kontradiksi
  dengan desain cursor; `wait` sudah merge backlog.
- **Jangan membuat `wait` background di level shell** — prosesnya mati saat
  tool call selesai, dan tidak ada wakeup async; itu malah menciptakan "session
  baca" yang tidak terpakai.

### 3.4 Kenapa sesi pertama gagal = belajar menyesuaikan urutan

Urutan yang benar untuk opencode (synchronous-only):
1. `agent-peer listen > /tmp/agent-peer-listen.log 2>&1 &` (detach, kembali instan)
2. Kerjakan tugas lain...
3. `agent-peer wait` sebagai tool call terakhir (dengan atau tanpa timeout).

Di percobaan pertama saya masih menjalankan `wait` langsung tanpa `listen`
detached dan dengan timeout tool 5000ms — dua-duanya salah untuk harness ini.
Percobaan keempat (turn terakhir) sudah urutannya benar dan langsung menerima
pesan.

## 4. Detail teknis yang menegaskan tidak ada bug

- `agent-peer list` di akhir sesi menampilkan `opencode-15297` PID 25021,
  ENGINE OPENCODE, ALIVE yes, CWD `~/projects/agent-peer` — benefit dari
  auto-name + engine detection.
- `~/.agent-peer/cursors/opencode-15297.json` = `{"last_read_at": ...}` dan
  inbox `opencode-15297.jsonl` berisi 1 pesan (yang diterima) — backlog-merge
  bekerja: `wait` me-return pesan yang masuk sebagai backlog pada panggilan
  berikutnya.
- Lock `~/.agent-peer/locks/opencode-15297.lock` menegaskan menghindari
  wait-concurrent untuk sesi yang sama (exit 1 kalau terduplikasi) — ini
  perilaku yang benar, bukan untuk di-retry.

## 5. Rekomendasi kecil (opsional) buat defense-in-depth

SKILL.md opencode saat ini sudah menulis jalur background-capable DAN
synchronous-only. Supaya celah "wait doang gak cukup" benar-benar ketutup di
semua AGENTS.md/SKILL.md (agy/pi/opencode), bisa ditambahkan frase eksplisit:

> `wait` tidak pernah men-bind socket — pastikan `listen` (detached) sudah
> jalan sebelum `wait`, kalau ingin dapat kiriman peer.

Paralel dengan audit `pi` dan koreksi di HANDOFF — belum sempat masuk ke
dokumen iterm lahir sebelum sesi ini.