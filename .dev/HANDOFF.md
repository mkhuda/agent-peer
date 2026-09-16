# HANDOFF

## 2026-09-17 — Analisa mekanisme `wait`/`watch`, rencana unread cursor

**Konteks:** user pakai `agent-peer` aktif untuk komunikasi Claude Code <-> Antigravity
(agy). Sesi diskusi ini murni riset + perencanaan, belum ada implementasi kode.

**Temuan mekanisme `wait` vs `watch`:**
- `wait` (`inbox.py:wait_for_message`) = blocking tool call, satu-satunya yang
  benar-benar memicu "auto wakeup" agy (exit proses = kontrol balik ke LLM loop).
  Tapi rapuh: proses ini mati tiap kali agy pindah ke tool call/task lain, jadi harus
  di-restart manual tiap selesai kerja.
- `watch` (`logs.py:show_logs` follow mode) = proses background yang tidak pernah
  exit sendiri, tetap hidup lintas task — bagus untuk visibility pasif, tapi tidak
  membuktikan diri memicu wakeup (tergantung apakah harness agy memantau stdout-nya
  sebagai notifikasi async — belum dikonfirmasi).
- `notify_idle` di `peerFeatures` (listener.py:103) dicek: field kosmetik, tidak ada
  implementasi apapun yang membacanya di codebase ini.

**Bug konkret ditemukan di `wait_for_message`:**
1. Baseline dihitung dari `len(inbox)` saat `wait` dipanggil → backlog pesan yang
   masuk selagi `wait` mati (agy sibuk kerja) tidak pernah "ditagih" otomatis saat
   `wait` di-restart.
2. `return msgs[-1]` cuma ambil 1 pesan terakhir → kalau beberapa pesan numpuk dalam
   satu siklus poll (100ms), yang lain hilang permanen (baseline berikutnya sudah
   menganggapnya "lama").

**Keputusan & status:** desain di
[`docs/wait-unread-cursor.md`](../docs/wait-unread-cursor.md) — cursor per-sesi
berbasis timestamp (`~/.agent-peer/cursors/<nama>.json`), auto-advance saat `wait`
dipanggil (tanpa agent perlu "mark as read" eksplisit), `wait` return list bukan
1 pesan. **Sudah diimplementasikan** (`protocol.py`, `inbox.py`, `cli.py`) dan
diverifikasi via test terisolasi (`HOME` di-override, plus simulasi pakai copy
read-only inbox produksi asli antigravity 42 pesan — first-call-after-restart
terbukti tidak replay histori lama). Instalasi lokal editable install, jadi
perubahan source sudah otomatis aktif untuk panggilan `agent-peer` berikutnya;
proses `listen`/`wait` yang sudah berjalan sebelum edit baru kepakai kode baru
setelah di-restart. Settle-window/delay sebelum return dipertimbangkan lalu
diputuskan tidak perlu (pola pakai user tidak burst sub-detik, backlog-merge
yang ada sudah cukup).

**Temuan terpisah (operasional, bukan bug kode):** `agent-peer list` menampilkan
`antigravity-2` (PID 71277) sebagai ALIVE=yes walau user merasa sudah menutup
sesinya. Diverifikasi bukan PID-reuse (procStart cocok `ps lstart`) — proses
`agent-peer listen` genuinely masih hidup, cuma tidak pernah dikirimi
SIGTERM/SIGINT saat sesi UI ditutup, jadi `cleanup()` di `listener.py` tidak
pernah terpanggil. **Koreksi:** sempat diduga bisa dikenali lewat TTY
detached (`??`) — ternyata salah, proses `antigravity` (72769) yang masih
aktif dipakai punya TTY/PPID/STAT identik (`??`, ppid=1/launchd, `S`). Tidak
ada sinyal level-OS yang membedakan basi vs aktif. Rencana fix (berbasis
deteksi grup nama duplikat + recency, plus command `stop` pakai SIGTERM) ada
di [`docs/stale-listener-detection.md`](../docs/stale-listener-detection.md).
Fix manual sementara: `kill <pid>`.

**Dead end:** sempat curiga ada mekanisme push/interrupt aktif dari `agent-peer` ke
proses agy untuk wakeup — ditelusuri lewat fork agent, hasilnya nihil. Tidak ada
signal/callback apapun; semuanya bergantung pada pola "blocking tool call" yang
diinisiasi agy sendiri, bukan wiring di sisi `agent-peer`.

**Verifikasi live end-to-end (sesi agy nyata, `antigravity-test` PID 52285):**
kirim 2 pesan (jeda 5 detik) sambil agy sibuk mengerjakan task lain. Dibuktikan
lewat perbandingan timestamp cursor vs `received_at` pesan (match persis di kedua
kasus): pesan 1 memicu wakeup instan saat `wait` sedang blocking; pesan 2 numpuk
selagi sibuk lalu ke-consume otomatis saat `wait` dipanggil ulang setelah task
selesai. Mekanisme cursor/backlog terbukti bekerja di kondisi nyata, bukan cuma
unit test terisolasi.

**Susulan ditemukan langsung dari sesi live (`antigravity-test`):** `ps` konfirmasi
2 proses `agent-peer wait --name antigravity-test` hidup bersamaan (PPID sama,
TTY beda) — harness agy men-spawn `wait` baru tanpa menutup yang lama. Race
nyata: berpotensi dua proses sama-sama menangkap pesan yang sama (duplikat,
bukan hilang). **Fix diimplementasikan:** `cmd_wait` ambil exclusive lock
non-blocking per-sesi (`fcntl.flock` di `~/.agent-peer/locks/<sesi>.lock`) —
invocation kedua untuk sesi yang sama langsung gagal (`exit 1` + pesan jelas)
alih-alih diam-diam race. Lock lepas otomatis oleh OS saat proses mati/crash.
Diverifikasi di sandbox: proses kedua ditolak instan, proses pertama gak
terganggu, lock lepas setelah proses pertama mati (termasuk lewat `kill`),
sesi nama beda tidak saling blokir. Detail di
[`docs/wait-unread-cursor.md`](../docs/wait-unread-cursor.md).

**Fix tambahan dikerjakan sesi ini (di luar rencana awal, ditemukan/diminta
selama sesi berjalan):**
- **Auth-bypass di `listener.py`** — ditemukan sendiri saat bedah laporan agy
  (lebih parah dari temuan 1.2 mereka): variabel `authenticated` dihitung tapi
  gak pernah dicek sebelum `process_incoming_frame` dipanggil, jadi token auth
  gak ter-enforce sama sekali. Fix: tambah `if not authenticated: continue`
  sebelum frame `user`/`control` diproses. Dites (auth-gate test): frame tanpa
  auth ditolak, token salah ditolak, token benar diterima — semua PASS.
- **Notifikasi klik buka Script Editor, bukan app yang relevan** — root cause
  perilaku macOS (notifikasi via `osascript` selalu ter-attribute ke Script
  Editor). Fix: ganti ke `terminal-notifier -activate com.googlecode.iterm2`
  (fallback ke `osascript` kalau `terminal-notifier` gak ada). Dikonfirmasi
  user klik notif sekarang buka iTerm. Detail di
  [`docs/notification-click-target.md`](../docs/notification-click-target.md).

**Generalisasi multi-harness (`agy`/`pi`/`opencode`/dst) — auto-detected session
names:** user pengen `agent-peer` gak Antigravity-only lagi. Sempat coba deteksi
identitas harness via env var (grep string di binary `agy`/`pi`/`opencode`) —
inconclusive, banyak noise, gak ada env var identitas yang jelas. **Pendekatan
yang terbukti jalan:** jalan ke atas rantai parent process (`os.getppid()` →
`ps -o comm=`), skip nama shell/interpreter generik (`zsh`, `bash`, `python3`,
dst), pakai nama proses pertama yang khas ditemukan + PID proses itu (bukan PID
`agent-peer` sendiri, karena `wait` dipanggil berulang dengan PID beda tiap
kali — butuh identitas yang stabil, dan proses harness-nya yang stabil selama
sesi hidup). Divalidasi langsung dari data nyata sesi ini: `zsh` (PID 79203) →
`claude` (PID 23386), `agy` (PID 33402) juga sudah dikonfirmasi lewat data
sebelumnya. Diimplementasikan di `protocol.py:detect_harness_identity/
auto_session_name`, dipakai lazy (cuma dihitung kalau `--name`/`--sender`
kosong DAN `$AGENT_PEER_NAME` kosong) di `cmd_send`/`cmd_listen`/`cmd_wait` —
command lain (`list`, `status`, dst) gak kena overhead sama sekali (dites,
`list` tetap ~0.05s). Scope disepakati: semua command (`wait`, `listen`,
`send`), dan default `wait` tanpa nama sekarang baca inbox per-harness
(auto-named), bukan lagi inbox global gabungan — keputusan sadar, bukan
regresi.

**Bug tambahan ditemukan & difix saat testing auto-name:** urutan
listen→send→wait pertama kali (sebelum sesi itu pernah manggil `wait`) bikin
pesan yang sudah nangkring duluan **terlewat** — karena cursor di-init ke
"sekarang" pas `wait` PERTAMA dipanggil (bukan pas sesi mulai hidup), jadi
pesan yang datang sebelum panggilan `wait` pertama itu keanggap "sudah lama".
Fix: `inbox.py:mark_session_start()` dipanggil di akhir `PeerListener.setup()`
— inisialisasi cursor di titik paling awal (sebelum `accept()` mungkin
memproses apapun), bukan nunggu `wait` pertama dipanggil. Sesi lama yang sudah
jalan sebelum fix ini tetap aman (masih pakai fallback lama di `_read_cursor`,
gak berubah — regresi `test_wait_cursor.py` tetap PASS semua).

`README.md` diupdate: section listen digeneralisasi (bukan Antigravity-only),
ditambah section baru soal auto-detected names dan penjelasan `wait` (backlog/
cursor/lock) yang sebelumnya sama sekali gak terdokumentasi di README.

**Bug lain ditemukan user:** `listener.py` hardcode `self.cwd = cwd or
os.path.expanduser("~/projects")` — jadi kolom CWD di `agent-peer list`
selalu nampilin `~/projects` gak peduli direktori asli tempat `agent-peer
listen` dijalankan. Fix: ganti ke `os.getcwd()`. Ini di kode inti (shared
semua harness), jadi otomatis berlaku ke agy/pi/opencode sekaligus, gak perlu
kerjaan terpisah per-harness. Dites di sandbox (cwd sekarang benar nunjuk ke
direktori kerja asli). Sesi live yang sudah terlanjur salah (`antigravity-test`
PID 52285, `pi-98661` PID 512) dipatch manual ke `/Users/rg/projects/agent-peer`
sesuai konteks kerja mereka saat ini.

**Bug ditemukan user saat test live `pi`:** `listener.py` hardcode
`"agentType": "AGY"` di session json — peninggalan waktu project ini masih
Antigravity-only. Sesi `pi` (`pi-98661`, PID 512) terdaftar dengan kolom
ENGINE salah nampilin "AGY". Fix: `PeerListener.__init__` terima `agent_type`
opsional (default fallback `"AGENT"` kalau gak diketahui), `cli.py:cmd_listen`
deteksi engine lewat `detect_harness_identity()` (independen dari nama sesi —
walau `--name` dikasih manual, engine tetap dideteksi dari process tree) dan
oper ke `PeerListener`. Dites di sandbox: sesi baru sekarang benar nampilin
`CLAUDE`/`PI`/dst sesuai proses induknya. Session json `pi-98661` yang sudah
terlanjur salah (start sebelum fix) dipatch manual (`agentType: "PI"`) —
proses listener-nya sendiri gak perlu di-restart karena file cuma dibaca
pasif oleh `agent-peer list`, tidak dipegang lock terus-menerus oleh listener.

**Root cause opencode cuma jalanin `wait` tanpa `listen`:** ditanyakan ke
`pi-98661` buat audit — jawabannya tajam. Konfirmasi: `pi` sendiri sengaja
jalankan `listen` dulu (sesuai SKILL.md), TAPI dia identifikasi celah nyata di
`SKILL.md`: gak pernah ada kalimat eksplisit "wait doang gak cukup buat jadi
reachable — tanpa listen yang hidup, `send` ke namamu bakal gagal, dan `wait`
sendiri gak akan error walau kamu gak reachable" — silent failure mode yang
gampang ke-miss karena `wait` tetap "berhasil" jalan (blocking normal) walau
sebenarnya sesi itu gak bisa dihubungi dari luar.

**Sempat coba fix radikal (auto-spawn `listen` dari dalam `wait`), lalu
dibatalkan user** — alasan: subprocess yang di-spawn otomatis & fully
detached (`start_new_session=True`) berisiko bikin listener menumpuk diam-diam
tanpa disadari, PERSIS masalah "stale listener" yang sudah susah payah
didiagnosis di [[stale-listener-detection]]. **Diganti jadi guard non-invasif:**
`cmd_wait` sekarang cek `resolve_session(session)` dulu — kalau gagal, print
warning jelas ke stderr ("no listener running... run agent-peer listen if you
want to be reachable") lalu tetap lanjut proses wait seperti biasa (gak
refuse total, karena baca backlog yang sudah numpuk tanpa listener hidup
tetap valid use-case). Gak ada proses baru yang dibuat sama sekali — cuma
kasih tahu, keputusan tetap di tangan agent/user. Dites: warning muncul tepat
saat gak reachable, silent saat sudah reachable, tidak ada listener baru
muncul di kedua kasus.

**Keputusan final soal dokumentasi:** dipertimbangkan lalu diputuskan TIDAK
perlu propagasi tambahan ke SKILL.md/AGENTS.md — warning runtime di `wait`
sudah menutup celah paling bahaya (poin 2 pi) lebih reliable daripada
dokumentasi statis (self-documenting tepat di momen yang tepat, gak
bergantung agent baca skill dengan teliti).

**Kolaborasi 4-arah review (agy, pi, opencode, Claude) + handwalk task system:**
diinisialisasi `.dev/CHARTER.md` (goal M1: "pakai agent-peer tanpa was-was ada bug
tersembunyi", gate: `agent-peer list`+`wait` sekali lagi gak aneh), ownership map
per-file antar 4 sesi. Dari ~20 temuan di `.dev/reviews/*.md`, ditriase jadi 10
kandidat task; 3 prioritas tertinggi (task 0001-0003) dikerjakan langsung (bukan
worktree, sesuai instruksi user — worktree ditunda):

- **0001** chmod 0600 file inbox/cursor/lock + 0700 direktori `~/.agent-peer/*`
  (bukan `SOCKET_DIR`/`SESSIONS_DIR` punya Claude Code, sengaja gak disentuh).
- **0002** cursor safety: (a) `_read_cursor` korup sekarang fallback ke `0`
  (replay semua) bukan `time.time()` (nelan diam-diam) + warning stderr;
  (b) `clear_inbox()` reset cursor. **Regresi ketemu saat testing sendiri**:
  fix awal saya HAPUS file cursor saat clear — itu membuka lagi celah
  `mark_session_start` yang sudah ditutup (pesan yang masuk antara clear dan
  wait berikutnya ikut ketelan). Diperbaiki: cursor di-**tulis ulang ke
  waktu-saat-clear**, bukan dihapus — sama persis pola `mark_session_start`.
- **0003** status `new-msg` di session json sekarang di-reset ke `idle` (lewat
  `_reset_status_idle` di `cmd_wait`, best-effort via `resolve_session`) setiap
  `wait` berhasil dapat pesan — bug yang ditemukan `pi` (status macet selamanya
  sejak pesan pertama, gak pernah ada kode yang nulis ulang `idle`).

Semua dites di sandbox (regresi `test_wait_cursor.py`/`test_auth_gate.py` tetap
PASS + skenario baru), **belum di-hand-walk oleh user** (aturan skill handwalk:
task cuma boleh ditutup kalau owner sendiri yang jalanin acceptance sentence-nya
di mesin nyata dan tulis hasilnya). Task file: `docs/tasks/0001-*.md` s/d
`0003-*.md`. 7 kandidat task sisanya (sender-label resolution, wording
"Delivered", `_GENERIC_PROC_NAMES` expansion, warning ke stdout, dokumentasi
subagent `--name`, test suite, skill-ke-repo) belum dikerjakan — didaftar di
chat, belum jadi task file resmi.

**Validasi live `opencode` selesai, self-diagnosed oleh sesi itu sendiri**
(laporan lengkap di `.dev/opencode-report.md`, dicek silang ke cursor/inbox/
lock file asli — akurat semua). Root cause persis diprediksi: bash tool
opencode synchronous, timeout TOOL default 5 detik (lebih ketat dari 2 menit
yang diduga dari riset web awal), plus sesi pertama belum `listen` duluan.
Setelah urutan benar (`listen` detached → kerja lain → `wait` tool call
terakhir tanpa timeout), semua fitur (auto-name `opencode-15297`, ENGINE
`OPENCODE`, cwd-fix, backlog-merge, lock) tervalidasi identik dengan agy/pi.
**Ketiga harness target (agy, pi, opencode) sekarang tervalidasi end-to-end**,
masing-masing dengan diagnosis mandiri dari agent-nya sendiri, bukan cuma
klaim dari sesi ini.

**Koreksi arsitektur penting dari test live `pi`:** user nanya soal pola
`agent-peer wait; echo "WAIT-EXIT:$?" (timeout 600s)` yang muncul di tool call
`pi-98661`. Ditanyakan langsung ke sesi itu, jawabannya detail dan akurat
(dicek ke source `dist/core/tools/bash.js` oleh agent pi sendiri): **`pi`
sama sekali gak punya background-bash** (beda dari Antigravity yang punya
`run_command` + wakeup notification) — bash tool-nya sinkron total. 600s itu
bukan limit infra pi, itu parameter `timeout` yang agent-nya sendiri kasih ke
tool call (SIGKILL process tree kalau kelewat). Tanpa `timeout` di tool call
= block indefinite beneran.

Implikasi: `SKILL.md` yang saya buat awal buat `pi` salah framing "background
task" (kecopy dari versi Antigravity). **Sudah diperbaiki:**
- `wait` → tetap tool call sinkron, jadi tool call TERAKHIR di turn (bukan
  "background"), tanpa `--timeout` agent-peer MAUPUN `timeout` bash tool-nya
  pi (dua hal beda yang sempat ketuker) — turn tetap "terbuka" sampai pesan
  masuk.
- `listen` → sebaliknya JUSTRU harus di-detach manual di level shell (`&`),
  karena kalau dipanggil sinkron biasa bakal freeze turn selamanya (proses
  `listen` didesain gak pernah exit sendiri). `AGENTS.md`/`README.md` gak
  kena isu sama karena udah pakai frasa "blocking shell call" dari awal,
  gak pernah nyebut "background task" secara eksplisit kayak SKILL.md.

**Skill `agent-peer` untuk `opencode`:** riset convention-nya beda lagi dari
agy/pi — dikonfirmasi via WebFetch ke `opencode.ai/docs/skills/`:
frontmatter wajib `name`+`description` (opsional `license`/`compatibility`/
`metadata`), **gak ada auto-load atau slash-command** — skill dipanggil
eksplisit lewat tool call `skill({ name: "agent-peer" })`. Lokasi global:
`~/.config/opencode/skills/<name>/SKILL.md` (ada fallback ke
`~/.claude/skills/`/`~/.agents/skills/` juga, sengaja gak dipakai biar gak
nyampur efek ke Claude Code asli). Dibuat di
`~/.config/opencode/skills/agent-peer/SKILL.md`.

Soal bash tool opencode: riset web nemuin default **synchronous, timeout 2
menit** (env var `OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS` buat ubah),
plus ada fitur `run_in_background` (mirip pola Antigravity, auto re-invoke
pas command exit) — tapi **belum dikonfirmasi apakah fitur ini ada di versi
opencode terpasang (1.18.28)**, sumbernya dari PR yang kemungkinan baru.
Belajar dari kesalahan asumsi soal `pi` sebelumnya, SKILL.md ini SENGAJA
ditulis tidak berasumsi (kasih 2 jalur: background-capable vs
synchronous-only, minta agent cek dulu) — **belum divalidasi live**, nunggu
user buka sesi `opencode` beneran buat ditest persis seperti `agy`/`pi`.

**Skill `agent-peer` untuk `pi`:** diinjek ke `~/.pi/agent/skills/agent-peer/SKILL.md`
(lokasi native yang dibaca langsung core `pi`, dikonfirmasi dari source
`skill-store.ts` — sejajar sama skill `web-search` bawaan). Format frontmatter
sama (`name`+`description`), isi diadaptasi dari SKILL.md Antigravity tapi
lebih ringkas sesuai gaya `web-search`. **Belum diverifikasi live** apakah
`/agent-peer` di `pi` beneran resolve ke file ini — `pi -p` gagal karena auth
error (`UnrecognizedClientException`) gak terkait perubahan kita. User lagi
tes manual sesi `pi` interaktif buat konfirmasi.

**AGENTS.md global harness lain:** dicek 6 file (`pi`, `opencode`, Codex,
Hermes, Gemini x2) — semuanya kosong soal `agent-peer` (ternyata instruksi
`agy` pakai `agent-peer wait --name antigravity` selama ini murni manual
diketik user tiap sesi, bukan dari config permanen `~/.gemini/AGENTS.md`).
Atas persetujuan user, ditambahkan section ringkas "Cross-agent messaging
(agent-peer)" ke `~/.pi/agent/AGENTS.md` dan `~/.config/opencode/AGENTS.md`
(isi identik, cuma beda contoh nama auto-detect `pi-<pid>` vs
`opencode-<pid>`) — poin-poin: `listen`/`wait` tanpa `--name`, `send`, `list`,
arahkan ke README buat detail (gak duplikasi isi). `~/.gemini/GEMINI.md` (config global agy sebenarnya — bukan
`~/.gemini/AGENTS.md`/`~/.gemini/config/AGENTS.md` yang ternyata gak dipakai,
isinya framework "SuperAntigravity Skills") juga ditambahkan section yang sama
(contoh nama disesuaikan `agy-<pid>`). Codex/Hermes AGENTS.md **belum**
disentuh — belum diminta. Capability blocking-tool-call `pi`/`opencode`
sendiri (apakah `wait` beneran bisa jadi reactive trigger buat mereka, sama
seperti `agy`) **belum diverifikasi** — baru asumsi mekanismenya mirip, belum
ada test end-to-end kayak yang kita lakukan ke `agy`.

**Ketemu skill `agent-peer` yang sudah ada duluan di agy** (`~/.gemini/antigravity/skills/agent-peer/SKILL.md`)
— ini yang selama ini bikin `agy` "tahu sendiri" cara pakai `agent-peer` tanpa
pernah diajarin manual di sesi ini (GEMINI.md auto-load skill berdasar
deskripsi). Ternyata skill ini **sumber asli** dari 2 bug yang kita fix sesi
ini: SOP lama eksplisit nyuruh `--name antigravity` terus (bikin numpuk
`antigravity-2`/`-3`), dan mandatory-rule lama nyuruh selalu launch `wait`
baru sebelum akhiri turn tanpa cek yang lama masih hidup atau nggak (persis
skenario race 2-wait yang kita temukan). **Sudah diupdate:** hapus semua
`--name antigravity` hardcode (ganti auto-detect), perbaiki deskripsi `wait`
(sekarang backlog-merge instan, bukan cuma "next message"), tambah guidance
soal lock-error baru ("already running" = normal, bukan perlu di-retry).
Ditambah baris "Trigger explicitly `/agent-peer intro`" di atas — user
konfirmasi `/agent-peer` di agy memang auto-list skill by name.

**Koreksi:** sempat rekomendasi `--timeout 60` di mandatory-standby rule —
user tolak, benar. Karena `wait` dijalankan sebagai background task yang
men-trigger wakeup begitu proses exit, timeout bounded bikin dia exit tiap 60
detik tanpa isi lalu perlu di-relaunch — itu polling terselubung, kontradiktif
sama tujuan `wait` sendiri ("never poll"). Argumen lock-safety saya juga keliru
— `flock` lepas otomatis di OS level apapun sebab prosesnya mati, gak butuh
timeout buat itu. Sesi sequential yang nunggu peer menitan/jaman itu valid,
biarkan `wait` block tanpa batas. Sudah direvisi: mandatory rule sekarang
`agent-peer wait` tanpa `--timeout`; flag itu tetap didokumentasikan sebagai
opsi tersedia buat pemakaian non-background lain.

**Temuan tambahan dari eksplorasi agy** (`docs/agent-peer-weaknesses-report.md`,
dicek silang ke source — valid): inbox/cursor file tidak di-`chmod 0600` (cuma
socket & key file yang diproteksi); tidak ada verifikasi `SO_PEERCRED` di socket
auth (cuma cocokin token string); race condition non-atomic write di
`append_inbox` (tanpa `flock`); cursor tidak ikut ter-reset saat
`agent-peer inbox --clear`; stale session juga terjadi kalau listener kena
`SIGKILL`/crash (bukan cuma saat sesi UI ditutup — overlap dengan
[[stale-listener-detection]] tapi pemicu beda). Belum ada yang dikerjakan dari
daftar ini — murni temuan, menunggu prioritas dari user.
