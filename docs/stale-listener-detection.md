# Rencana: deteksi listener basi (stale) di `agent-peer list`

## Masalah

Contoh nyata (`agent-peer list`):

```
72769    antigravity      AGY   new-msg  yes  72769.sock  ~/projects   <- masih dipakai
71277    antigravity-2    AGY   new-msg  yes  71277.sock  ~/projects   <- user sudah "menutup" sesinya di UI
```

`antigravity-2` (PID 71277) tetap muncul ALIVE=yes walau user sudah menutup sesi
itu di Antigravity. Root cause **bukan** PID-reuse:

- `procStart` di `~/.claude/sessions/71277.json` = `Sun Sep 13 21:31:59 2026`
- `LC_ALL=C TZ=UTC ps -o lstart= -p 71277` = `Sun Sep 13 21:31:59 2026` (identik)

Jadi proses itu memang literally masih hidup, bukan PID yang dipakai ulang proses
lain. `agent-peer list` melaporkan "alive" dengan **benar** secara teknis.

## Kenapa "deteksi detached" BUKAN solusi (sudah dicoba, salah)

Awalnya diduga: proses basi bisa dikenali dari tidak punya controlling terminal
(TTY `??`) karena sesi UI yang menutupnya tidak sempat kirim SIGTERM/SIGINT.
Diverifikasi langsung dengan `ps -o pid,ppid,tty,stat -p <pid>` untuk KEDUA proses:

```
PID    PPID  TTY   STAT   COMMAND
71277  1     ??    S      agent-peer listen --name antigravity   (basi)
72769  1     ??    S      agent-peer listen --name antigravity   (masih dipakai aktif)
```

Keduanya **identik**: `TTY=??`, `PPID=1` (reparented ke `launchd`), `STAT=S`. Proses
yang sedang aktif dipakai pun sama-sama detached — karena begitu memang cara
Antigravity men-spawn `agent-peer listen` (background, tanpa terminal), bukan
tanda proses itu ditinggalkan. Jadi **tidak ada atribut level-OS (TTY/PPID/STAT)
yang bisa membedakan "basi" vs "masih dipakai"** — keduanya proses yang sama-sama
sah secara teknis, cuma satu sudah tidak relevan secara *intent* pengguna, dan itu
bukan sesuatu yang bisa dibaca dari `ps`.

## Root cause sebenarnya

`cleanup()` (`listener.py:121-138`) — yang menghapus socket, symlink, session json,
dan key file — cuma terpanggil lewat `_signal_handler` saat proses menerima
`SIGINT`/`SIGTERM` (`listener.py:262-263`), atau lewat exit loop normal. Menutup
sesi di UI Antigravity tidak mengirim sinyal itu ke proses `agent-peer listen`
yang berjalan terpisah di background — jadi proses itu terus hidup selamanya
sampai dibunuh manual, walau tidak ada yang "memakainya" lagi.

Faktor pemicu lain: `PeerListener.setup()` (`listener.py:53-58`) — saat nama sudah
terpakai oleh sesi lain yang masih alive, dia cuma menambahkan suffix (`-2`, `-3`,
...) alih-alih menggantikan/mematikan yang lama. Jadi listener basi menumpuk terus
tanpa pernah tergantikan otomatis.

## Dead end lain yang sudah dicoba: cek parent process

Ide: simpan `PPID` saat `agent-peer listen` baru start, lalu anggap "detached/basi"
kalau parent itu sudah tidak hidup lagi. Diverifikasi langsung, gagal di dua lapis:

1. `ps aux | grep -i antigravity` — **tidak ada proses aplikasi Antigravity yang
   berjalan lokal sama sekali** yang bisa dijadikan acuan "pemilik sesi". Tidak ada
   target untuk dicek keberadaannya.
2. `ps -ef` untuk kedua listener (basi maupun aktif) sama-sama sudah `PPID=1`
   (`launchd`) — proses yang di-spawn detached langsung ke-reparent ke launchd
   seketika lahir, jadi bahkan merekam `os.getppid()` di awal `setup()` pun
   kemungkinan besar sudah dapat `1`, bukan proses pemanggil aslinya. Tidak ada
   window waktu untuk menangkap sinyal itu.

Kesimpulan: batas "sesi ditutup di UI Antigravity" adalah state internal aplikasi
Antigravity yang tidak pernah termanifestasi sebagai sinyal OS (proses/parent)
apapun yang bisa diamati `agent-peer` dari luar. Jangan coba pendekatan
parent-liveness lagi — sudah terbukti tidak ada channel-nya.

## Sinyal yang TERSEDIA untuk deteksi (tidak sempurna, tapi berguna)

Karena tidak ada sinyal OS yang pasti, deteksi harus berbasis heuristik di level
aplikasi:

1. **Grup nama duplikat** — sesi dengan pola nama dasar sama (`antigravity`,
   `antigravity-2`, `antigravity-3`, ...) adalah kandidat kuat "salah satunya basi",
   karena hanya muncul lewat mekanisme suffix di `listener.py:53-58`, bukan input
   manual user. Multiple listener hidup bersamaan di bawah base name yang sama itu
   sendiri sudah sinyal.
2. **Recency** — `statusUpdatedAt`/`updatedAt` di session json. Yang paling baru
   diupdate dalam satu grup duplikat kemungkinan besar yang masih relevan; yang
   lebih lama lebih mencurigakan (bukan bukti mutlak — listener idle lama tapi
   masih valid juga mungkin).
3. Tidak ada sinyal yang cukup untuk **auto-kill** dengan aman — keduanya cuma
   petunjuk untuk manusia, bukan dasar penghapusan otomatis (menyentuh proses
   hidup + menghapus registrasi adalah aksi destruktif, tidak boleh dilakukan
   otomatis tanpa konfirmasi).

## Rencana

Bukan "fix deteksi detached" (karena sinyal itu tidak valid) — melainkan:

1. **`agent-peer list`**: tandai visual sesi yang berada dalam grup nama duplikat
   (mis. badge `dup?` di sebelah nama), diurutkan/ditandai mana yang paling baru
   aktif dalam grup itu, supaya user bisa lihat sekilas mana yang kandidat basi
   tanpa perlu `ps` manual.
2. **Command baru `agent-peer stop <nama-atau-pid>`**: kirim SIGTERM ke proses
   listener target — memicu `cleanup()` miliknya sendiri secara graceful (bukan
   hapus file registrasi manual dari luar, supaya tetap konsisten dengan siklus
   hidup yang sudah ada di `listener.py`). Ini pengganti resmi untuk `kill <pid>`
   manual.
3. **Opsional, evaluasi belakangan**: `agent-peer prune` — list semua grup nama
   duplikat + rekomendasi mana yang basi (berdasar recency), minta konfirmasi user
   per proses sebelum `stop`. Tidak auto-jalan tanpa approval, sesuai prinsip
   "jangan ambil aksi destruktif tanpa konfirmasi".

## Scope perubahan (minimal)

- `registry.py`: tambah helper untuk group-by base-name (regex `^(.*?)(-\d+)?$`
  pada `name`) dan hitung mana yang paling recent per grup.
- `cli.py:cmd_list`: pakai helper itu untuk badge visual.
- `cli.py` + `listener.py` atau modul baru kecil: command `stop` yang connect ke
  socket target lalu kirim frame `control` dengan action semacam `"shutdown"` —
  **atau**, lebih sederhana dan tidak butuh ubah protokol: `cli.py` langsung kirim
  `SIGTERM` via `os.kill(pid, signal.SIGTERM)` setelah resolve target lewat
  `registry.resolve_session` (proses target sudah punya handler SIGTERM sendiri di
  `listener.py:262-263`, jadi cukup kirim sinyal, tidak perlu frame protokol baru).
- Tidak menyentuh `sender.py`, `inbox.py`, `protocol.py` (format frame), atau
  socket handshake.

## Status

Rencana, belum diimplementasikan. Menunggu konfirmasi user sebelum eksekusi.
