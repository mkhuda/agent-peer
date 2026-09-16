# Review & Audit `agent-peer` + `SKILL.md` dari Perspektif Harness opencode

**Penulis:** opencode-15297 (harness opencode v1.18.31, model openrouter-auto-claude)
**Tanggal:** 17 September 2026
**Konteks:** presentasi nyata: bash tool sync-only, timeout tool default default
pendek, wait sebagai tool call terakhir, relay 3-harness (agy → pi → opencode →
agy), pelatihan agent-peer untuk skill multi-harness.

---

## 1. Executive Summary

`agent-peer` bekerja dengan baik sebagai IPC mesh: delivery <200ms (165–168ms
terukur), reactive `wait` dengan backlog-merge, auto session-name, engine
detection, dan lock concurrency semua terkonfirmasi jalan dari harness opencode.
Tidak ada bug blocker yang saya temukan di alur inti.

Tetapi dari sudut khusus harness opencode — **strict synchronous bash tool,
timeout tool default yang perlu di-set ulang, dan detach `listen` secara
manual** — ada satu celah *design-level* yang saya nilai paling penting:
**konsekuensi timeout tool yang membunuh `agent-peer wait` tidak memicu ulang
standby secara otomatis** dari sisi agent-peer, dan tidak ada "watchdog"
engawa lokal yang bisa diandalkan untuk melakukannya. Bagian 4 membahas
mitigasi yang bisa masuk ke dalam produk maupun ke docs/SKILL.

---

## 2. Yang sudah terverifikasi live

- **`wait` menangkap backlog instan & mode blocking:** pesan dari `agent-peer-e4`
  dan `pi-98661` ditangkap tanpa polling; ketika ada backlog langsung return, saat
  kosong blocking (poll 100ms).
- **Relay 3-harness:** agy → pi → opencode → agy tertutup; `send` ke
  `antigravity-test` 166ms, laporan ke `agent-peer-e4` 165ms.
- **Auto-detection nama:** sesi terdaftar sebagai `opencode-15297`
  (PID dari `/Users/rg/.local/bin/opencode`, bukan PID transient dari `wait`).
- **Engine + CWD:** setelah fix di `listener.py`/`cli.py` (agentType dari
  `detect_harness_identity`, cwd = `os.getcwd()`), `agent-peer list` menampilkan
  ENGINE=OPENCODE dan CWD=`~/projects/agent-peer` dengan benar.
- **Lock:** `fcntl.flock` per-sesi menolak `wait` ganda untuk sesi yang sama
  (exit 1) — behavior benar, bukan untuk di-retry.

---

## 3. Hasil audit `SKILL.md` opencode (`~/.config/opencode/skills/agent-peer/SKILL.md`)

### 3.1 Ambiguitas fatal: "check your own bash tool first"
Baris 27–44 memberi dua jalur (background-capable vs synchronous-only) dan
meminta agent "cek dulu". Dalam praktik, agent (saya) tidak benar-benar bisa
menentukan kemampuan background tool secara andal dari dalam sesi — dan
verifikasi tidak tersedia saat `wait` harus dipanggil. Akibat: baris ini hanya
menjadi latihan tebakan, dan kesalahan tebakan berujung pada freeze/hilang
nya pesan (persis yang saya alami di turn 1: tanpa detach listen dan timeout
tool 5000 saat `wait` diblokir).

**Rekomendasi:** ganti "check your own bash tool" dengan **default yang aman
untuk synchronous + opini detach `listen` pakai `&`** (yang sudah terbukti),
lalu tambahkan catatan bahwa sesi dengan background-capable boleh memakai
jalur lain — bukan sebaliknya.

### 3.2 Tidak ada penjelasan hubungan `listen` vs `wait` (gap "wait-alone not enough")
`SKILL.md` menjelaskan keduanya terpisah tapi tidak pernah menegaskan
**`wait` saja tidak membuat sesi ter-reachable** — harus ada `listen` yang hidup
(`sender.py` connect ke socket; `wait` hanya baca inbox). Karena `wait` tidak
error saat tidak reachable (hanya warning stderr di `cli.py:_warn_if_unreachable`),
ini silent-failure yang mudah terlewat. Cermati bahwa HANDOFF.md sudah mencatat
kesimpulan "wait alone is not enough" dari audit pi, tapi belum dipropagasi ke
semua SKILL/AGENTS terpola. **Rekomendasi:** satu kalimat eksplisit di SKILL &
README (itu yang SKILL pakai sebagai acuan).

### 3.3 "urutan" untuk harness sync-only tidak eksplisit
SKILL memberi cuplikan detach tapi tidak menyebut urutan yang telah terbukti:
1) `listen > log &` dulu, 2) baru `wait` sebagai tool call terakhir. Kurangnya
urutan membuat kebingungan (seperti turn 1 saya). Tambahkan diagram langkah
yang singkat.

### 3.4 Warning stderr `_warn_if_unreachable` tidak sampai ke LLM
Ketika `wait` dijalankan sebagai tool call, stderr tool **tidak selalu dirender
ke model** (tergantung harness). Jadi warning "you are not reachable" bisa
hilang diam-diam. Agent-peer seharusnya menganggap ini bug UX — kalau konteks
tool mampu, print warning sebagai stdout atau buat exit code khusus yang bisa
pantau. (Lihat rekomendasi produk s.4.3 dalam review.)

---

## 4. Masalah utama: timeout tool yang membunuh `wait` = standby mati (design gap)

### 4.1 Analisis mekanik yang terjadi
- `agent-peer wait` sebagai blocking tool call, **tanpa `--timeout`** internal,
  akan block sampai pesan (atau mati karena timeout tool).
- Di opencode, bash tool **synchronous**. Default-nya punya timeout pending
  ~2 menit, dan seperti yang saya alami, tool memotong panggilan tanpa warning.
- Tanpa pemantauan eksternal, "kill" = sesi ditinggalkan sebagai "*poison*":
  tidak lagi menerima pesan, sedangkan `listener` yang tersisa masih menghisap.
- Karena opencode tidak punya *auto-reinvoke* saat command exit, tidak ada loop
  natural yang memulai ulang `wait`. Hasil netto: **silent standby-death**.

### 4.2 Kenapa ini bukan semata-mata "dokumentasi"
Timeout default yang pendek dan dipaksa setiap panggilan → kemungkinan besar LLM
tidak selalu memasukkan `timeout` yang cukup besar pada langkah terakhir (budget
tokel, konteks panjang, keputusan heuristik). Bahkan dengan doc yang benar,
kekhawatiran "tool membunuh panggilan" tetap ada setiap kali agent memanggil
`wait` sebagai langkah terakhir.

### 4.3 Mitigasi yang bisa dipertimbangkan dari sisi produk (bukan cuma docs)
1. **Pemakaian `--timeout` default yang lebih cocok untuk harness:**
   Tambahkan opsi `--timeout` di `cmd_wait` yang *default* kini `0` (unlimited).
   Untuk opencode (synchronous), default `0` memang benar, namun banyak harness
   punya batas internal. Satu alternatif: argumen tambahan seperti
   `--self-heal` / `--watch-stale`? Ini mengarah ke fitur "wait-and-relisten".
   Evaluasi dengan hati-hati — tidak ingin mengejek pola "auto-spawn listener"
   yang dibatalkan sebelumnya (risiko stale-listener). Tapi **watchdog untuk
   menangkap process mati** berbeda: bisa berupa opsi `--heartbeat <sec>` yang
   mencetak marker periodik (misal `HEARTBEAT`), yang bisa dipakai harness
   untuk "tahap tool call masih hidup" — sayangnya hanya membantu bila harness
   membaca stdout real-time, yang pada sync tool-kali tidak terlihat sampai
   command selesai. Jadi untuk opencode sync, opsi ini tidak menyelamatkan
   standby-death.
2. **Exit code & output contract yang jelas untuk deteksi kebangkitan:**
   Saat `wait` dipicu mati oleh tool timeout, tidak ada indikasi yang bisa
   dikonsumsi (memori). Jika opencode mendukung env var
   `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS` — saya tidak menemukan file
   config yang memicunya di instalasi 1.18.31 ini; default tetap ~2 menit.
   Konsekuensi: **cara paling praktis adalah memastikan LLM sadar untuk selalu
   mengirim `wait` dengan `timeout` tool yang panjang (± 10 menit) pada langkah
   TERAKHIR**, dan *optionally* menjalankan ulang `wait` pada langkah terakhir
   setelah *setiap* action yang bukan-wait — dikendalikan manual, bukan
   background.
3. **Dokumentasi terkuat:** sertakan **garis besar kasus "bagaimana agar
   standby tidak mati"** yang mengarahkan pada pola yang saya gunakan:
   - `listen > /tmp/agent-peer-listen.log 2>&1 &` (session terbuka, benar-benar
     detach)
   - `wait` sebagai tool call terakhir dengan `timeout` tool minimum 10 menit
   - jangan `wait` di background (proses akan mati tanpa trigger ulang)
   - setelah menerima pesan, jika akan beraksi lebih lanjut yang mungkin
     memicu ulang proses, waspada bahwa `wait` harus **dipanggil ulang**
     secara eksplisit setelahnya (tidak ada auto-reintegrator).

### 4.4 Prioritas
Menurut pendapat saya, memprioritaskan **menyelesaikan gap "standby-death tanpa
recover otomatis"** adalah perbaikan terbaik jangka pendek yang bisa dieksekusi
di sisi opencode: (a) memperkuat SKILL + README dengan pola urutan dan *escape
hatch*, (b) menambahkan `agent-peer wait` dokumentasi `timeout` tool yang harus
eksternal diharuskan. Sesuatu yang lebih mekanis (watchdog ber-link ke harness)
perlu evaluasi lebih karena muara ke stale-listener tersembunyi.

---

## 5. Edge case & kekhawatiran reliability lain (baru, khusus ditemukan dari opencode)

1. **Deteksi harness `opencode` tidak unik?** Di setup ini `opencode` executable
   ada di `/Users/rg/.opencode/bin/opencode` tetapi juga ada binary `herdr server`
   yang membungkus sesi. `detect_harness_identity` menemukan `opencode` dari
   chain PPID zsh → `opencode`. Tapi ada *banyak* sesi opencode yang mungkin
   berjalan (versi TUI + run), dan nama `opencode-<pid>` semuanya **saling
   mengunci pada basis pid unik** — sebenarnya oke. Namun kalau beberapa sesi
   docker/local ikut mendekati, ada risiko nama `opencode-<pid>` tetap beda karena
   pid berbeda. Tidak menemukan konflik nyata **di setup ini**.
2. **Bug minor `_GENERIC_PROC_NAMES`:** daftar berisi `node`, tetapi **tidak ada
   `pnpm`, `npm`, `vite`, `tsx`, dll.** Menjalankan `auto_session_name` dalam
   sesi yang di-boot dari `pnpm dev`/`npm run` akan menghasilkan nama seperti
   `pmap-...` atau `npm-...` — bukan `opencode-<pid>`. Ini berpotensi nama sesi
   berganti-ganti bila harness mengeksekusi melalui wrapper non-harness. Buat
   bagan ini memperhitungkan *burgs of generic task runtimes*.
   Risiko nyata rendah karena sesi nyata memakai zsh→opencode, tapi kalau user
   memakai cuplikan `npx`/`npm` untuk memulai opencode, deteksi bisa abort.
3. **Notifikasi bunyi mahal:** `listener.py` memanggil `terminal-notifier`
   per pesan masuk — pada harness dengan aliran banyak (mis. saat pengujian
   bulk), bisa spam. Tidak kritis untuk kerja local.
4. **Sinkronisasi cursor antar-session (nice-to-have):** `_read_cursor` default
   ke `time.time()` kalau file hilang. Kalau `agent-peer inbox --clear` terjadi
   tanpa reset cursor, pesan yang benar-benar baru bisa dimakan (timestamp
   monotonic membantu, tapi tidak sepenuhnya membuktikan). Sudah dicatat di
   laporan agy/weaknesses; saya setuju, dan tambahkan: lebih baik cursor di-reset
   di `clear_inbox()`.
5. **`cmd_wait` tidak pernah mengekspos "timeout karena tool" sebagai kasus
   yang bisa dibedakan:** `wait` return `None` → print "Timeout waiting" dan exit
   1; tool yang kill juga akan menghasilkan output truncated. Kombinasi keduanya
   membuat pengerjaan logika "apakah standby perlu di-restart" sulit dibedakan
   dari pesan kosong.

---

## 6. Rekomendasi konkret (dirangkum)

**Untuk kode `agent-peer` (opsional, prioritas sedang–tinggi):**
1. Dokumentasi di README (dan SKILL) secara eksplisit: "`wait` membutuhkan
   `listen` aktif", bukan hanya warning stderr ke stderr yang sering tidak
   dirender LLM.
2. Pertimbangkan `--timeout` di `wait` dan/atau env var default yang jelas
   untuk harness yang mengharuskan batas. Plantu: untuk opencode, default 0
   (unlimited) sudah benar — tapi dokumentasikan bahwa untuk harness sync, jika
   tool limit ada, `wait` harus pakai `timeout` tool jauh di atas turn terbuka.
3. Saat `wait` terpaksa berakhir karena timeout (internal) — hasilkan pesan
   yang berbeda dari "timeout (internal)" vs "tool kill" supaya bisa
   dibedakan (exit code, misal 3) untuk harness yang memantau.
4. Inbox/cursor/lock files: set permission `0o600`/`0o700` (persisten dgn
   temuan weaknesses-report).
5. `clear_inbox()` harus mereset cursor terkait (hindari skip pesan baru).

**Untuk `SKILL.md` opencode (prioritas tinggi agar bisa diterapkan):**
1. Ganti bagian "check bash tool first" dengan urutan yang sudah **teruji**
   (synchronous-first): detach listen → wait terakhir dengan timeout tool besar.
2. Tambahkan kalimat eksplisit "wait alone tidak membuat reachable — wajib ada
   listen".
3. Tambahkan "apa yang harus dilakukan SETELAH menerima pesan": jika masih akan
   berkomunikasi, panggil `wait` kembali sebagai tool call terakhir.
4. Catat bahwa `--name` optional dan subagent/sesai paralel wajib memberikan
   `--name` unik (menindaklanjuti temuan agy).

**Untuk AGENTS.md global opencode:**
- Prolihat klausa "run wait as a blocking shell call" sudah benar, tapi tambahkan
  satu kalimat bahwa `wait` harus menjadi tool call terakhir + `listen` harus
  detached lebih dulu.

---

## 7. Kesimpulan penilaian

- **Agent-peer lebih reliable daripada yang terlihat pada saat pertama** — semua
  mekanisme baru (cursor, lock, backlog-merge, auto-detection, engine/cwd)
  bekerja dan sudah teruji hidup di 3 harness. Saya tidak menemukan bug blocker.
- **Isu paling penting adalah gap manajemen keterbatasan timeout di harness
  synchronous** — memerlukan kombinasi dokumentasi yang tegas + kontrak output
  (exit code / pesan) yang lebih jelas dari sisi `agent-peer`, agar opencode dan
  harness serupa tidak diam-diam kehilangan standby.
- SKILL.md berarti sudah ada arah yang benar; yang kurang eksekusi adalah
  membuat jalur synchronous sebagai **default yang mutlak** dan memberi panduan
  urutan + recovery yang eksplisit.

> Prefer jujur dan kritis: bukan untuk memberitahu "semua sudah baik", tapi
> supaya gap yang sekarang kelihatan di opencode diperbaiki sebelum dipakai
> skala produksi multi-harness.