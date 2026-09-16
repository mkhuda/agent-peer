# Rencana: unread cursor untuk `agent-peer wait`

## Masalah

`wait` saat ini (`inbox.py:wait_for_message`) memakai **baseline count-at-call-time**:

```python
def wait_for_message(session=None, timeout=None):
    initial_count = len(read_inbox(session=session))
    while True:
        msgs = read_inbox(session=session)
        if len(msgs) > initial_count:
            return msgs[-1]
        ...
```

Dua akibat nyata dari desain ini:

1. **Backlog hilang setelah restart.** `wait` dijalankan sebagai tool call blocking di
   agentic loop agy. Kalau agy sedang mengerjakan task lain (bukan sedang menjalankan
   `wait`), proses `wait` sebelumnya sudah mati (tool call sebelumnya selesai/turn
   berganti). Pesan yang masuk selama window itu tetap tercatat di inbox file, tapi
   begitu `wait` dipanggil ulang setelah task selesai, baseline baru dihitung dari
   total count **termasuk** pesan yang numpuk itu — jadi `wait` tidak langsung return,
   malah menunggu pesan berikutnya yang benar-benar baru.
2. **Cuma return 1 pesan, sisanya hilang permanen.** Kalau beberapa pesan masuk dalam
   satu siklus polling (100ms), `return msgs[-1]` cuma ambil yang terakhir. Baseline
   panggilan berikutnya sudah termasuk semuanya, jadi pesan yang "terlewat" itu tidak
   pernah bisa direplay lagi lewat `wait`.

Efek gabungan: agy kehilangan kesadaran atas pesan yang masuk saat dia sibuk
mengerjakan task lain, dan `wait` yang dijalankan lagi setelah task selesai tidak
otomatis "menagih" backlog itu.

## Target perilaku

Agent tidak perlu melakukan aksi eksplisit "mark as read". Begitu agy memanggil
`wait` (kapan pun, entah sedang idle atau baru selesai task lain):

- Kalau ada backlog pesan yang belum pernah "dilihat" oleh sesi ini → langsung
  `return` semuanya (merge, bukan cuma 1), **tanpa masuk mode polling/blocking**.
- Kalau tidak ada backlog → jalan seperti sekarang: blocking poll sampai ada pesan
  baru, lalu return.
- Di kedua kasus, cursor otomatis maju setelah `return` — efeknya "auto flag read"
  tanpa agent perlu tahu ada state read/unread di baliknya sama sekali.

## Desain

Cursor disimpan **per sesi**, berbasis **timestamp** (bukan line count) supaya tahan
terhadap `agent-peer inbox --clear`:

```
~/.agent-peer/cursors/<nama-atau-pid>.json
{ "last_read_at": <epoch float> }
```

Kenapa timestamp, bukan count:
- Count rapuh kalau file inbox di-`--clear` (index reset ke 0, bisa salah baca ulang
  pesan lama kalau file numpuk lagi dari awal).
- Timestamp (`received_at`, sudah ada di tiap record — lihat `inbox.py:append_inbox`)
  cukup untuk filter `received_at > last_read_at`, dan tetap konsisten walau file
  dikosongkan.

Perubahan di `inbox.py`:

```python
def get_unread(session=None):
    cursor = _read_cursor(session)          # default: waktu sekarang, kalau belum ada file cursor
    msgs = read_inbox(session=session)
    return [m for m in msgs if m.get("received_at", 0) > cursor]

def wait_for_message(session=None, timeout=None):
    unread = get_unread(session)
    if unread:
        _write_cursor(session, unread[-1]["received_at"])
        return unread                        # list, bukan 1 pesan
    t0 = time.time()
    while True:
        unread = get_unread(session)
        if unread:
            _write_cursor(session, unread[-1]["received_at"])
            return unread
        if timeout is not None and (time.time() - t0) >= timeout:
            return None
        time.sleep(0.1)
```

Default cursor saat file belum ada: **waktu saat itu juga** (bukan 0) — supaya
panggilan `wait` pertama kali tidak tiba-tiba me-replay seluruh histori lama sebagai
"unread".

## Perubahan API

- `wait_for_message` return `List[dict]` (bisa 1 atau banyak), bukan `Optional[dict]`.
- `cli.py:cmd_wait` perlu iterasi list saat print, bukan asumsikan 1 pesan.
- Command baru opsional: `agent-peer unread` (mirip `inbox` tapi hanya yang belum
  ter-cursor, tanpa mengubah cursor — buat cek manual tanpa efek samping) — **belum
  diputuskan, evaluasi kalau perlu setelah versi dasar jalan**.

## Scope perubahan (minimal)

- `inbox.py`: tambah `_read_cursor`/`_write_cursor`/`get_unread`, ubah
  `wait_for_message`.
- `cli.py`: `cmd_wait` handle list.
- Tidak menyentuh `sender.py`, `listener.py`, `protocol.py`, `registry.py`, socket
  handshake, atau format frame — murni logic sisi baca inbox.

## Status

**Diimplementasikan** (`protocol.py`: `CURSORS_DIR`/`get_cursor_path`; `inbox.py`:
`_read_cursor`/`_write_cursor`/`get_unread`/`wait_for_message` return list;
`cli.py:cmd_wait` iterasi list). Diverifikasi dengan unit test terisolasi (`HOME`
di-override ke scratch dir, tidak menyentuh `~/.agent-peer` produksi) dan simulasi
memakai copy read-only inbox produksi asli (`antigravity`, 42 pesan lama) — first
call setelah "restart" terbukti tidak replay histori lama (cursor default ke
waktu-saat-itu-juga, bukan 0).

Catatan: instalasi `agent-peer` di mesin ini adalah **editable install**
(`uv tool install --editable .` → nunjuk langsung ke repo ini), jadi perubahan
source otomatis aktif untuk panggilan `agent-peer` berikutnya tanpa perlu
reinstall. Proses `listen`/`wait` yang sudah berjalan sebelum edit ini tidak
terpengaruh sampai proses itu di-restart (Python sudah load kode lama ke memory).

**Settle window (delay sebelum return, untuk merge burst pesan) dipertimbangkan
tapi diputuskan TIDAK ditambahkan** — pola pemakaian user tidak melibatkan burst
kirim pesan sub-detik, dan backlog-merge yang sudah ada (`get_unread` selalu
ambil semua pesan dari cursor ke titik saat itu dalam satu snapshot) sudah cukup
untuk skenario utama (numpuk selagi sibuk kerja). Delay tambahan cuma akan
menambah latency wakeup tanpa manfaat nyata di kasus ini.

## Susulan: concurrent `wait` lock

Ditemukan lewat test live: harness agy bisa men-spawn `agent-peer wait` baru
tanpa menutup invocation lama untuk sesi yang sama (dikonfirmasi lewat `ps` —
2 proses `wait --name antigravity-test` hidup bersamaan, PPID sama, TTY beda).
Ini race condition nyata: kedua proses baca cursor yang sama, berpotensi
sama-sama menangkap & mengembalikan pesan yang sama (duplikat delivery, bukan
data hilang).

**Fix:** `cmd_wait` (`cli.py`) sekarang mengambil exclusive lock non-blocking
(`fcntl.flock`, `LOCK_EX | LOCK_NB`) di `~/.agent-peer/locks/<sesi>.lock`
sebelum mulai polling. Kalau lock sudah dipegang proses `wait` lain untuk sesi
yang sama → gagal cepat (`exit 1`, pesan jelas ke stderr), bukan ikut polling
diam-diam. Lock per-sesi (nama beda tidak saling blokir), dan otomatis lepas
oleh OS saat proses exit/crash/`kill` — tidak menambah risiko stale-file baru.

Diverifikasi: proses B ditolak instan saat proses A masih hidup, proses A tidak
terganggu, lock lepas otomatis setelah A mati (termasuk lewat `kill`, bukan
cuma exit normal) sehingga proses C berikutnya bisa jalan lagi, dan sesi dengan
nama berbeda tidak saling memblokir.

## Temuan terkait (bukan bagian dari rencana ini, dicatat karena ditemukan saat riset)

`agent-peer list` bisa menampilkan sesi listener yang secara UI sudah "ditutup"
tapi masih ALIVE=yes. Analisis lengkap + rencana fix dipindah ke
[`docs/stale-listener-detection.md`](./stale-listener-detection.md) — ringkasnya:
bukan PID-reuse, prosesnya memang belum benar-benar mati (verified via
`procStart` cocok `ps lstart`), cuma tidak pernah menerima sinyal yang memicu
`cleanup()`-nya sendiri. Fix manual sementara: `kill <pid>`.
