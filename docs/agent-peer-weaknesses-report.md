# Laporan Eksplorasi Kelemahan & Potensi Masalah `agent-peer`

## Overview
Dokumen ini berisi analisis mendalam terhadap arsitektur dan implementasi codebase `agent-peer` (`agent_peer/*.py`). Analisis difokuskan pada potensi masalah **Keamanan**, **Reliability / Race Condition**, dan **Desain Arsitektur** yang belum dibahas di dokumentasi yang ada ([docs/stale-listener-detection.md](file:///Users/rg/projects/agent-peer/docs/stale-listener-detection.md) dan [docs/wait-unread-cursor.md](file:///Users/rg/projects/agent-peer/docs/wait-unread-cursor.md)).

---

## 1. Masalah Keamanan & Autentikasi (Security & Access Control)

### 1.1 Unauthenticated Read Access pada File Inbox (`~/.agent-peer/inbox.jsonl`)
* **Lokasi Code:** [inbox.py](file:///Users/rg/projects/agent-peer/agent_peer/inbox.py#L22-L34), [protocol.py](file:///Users/rg/projects/agent-peer/agent_peer/protocol.py#L15-L20)
* **Temuan:**
  Ketika `ensure_dirs()` memicu pembuatan direktori `~/.agent-peer`, direktori dan file `inbox.jsonl` / `inboxes/*.jsonl` dibuat menggunakan *umask* standar sistem (biasanya `0644` / `0755`). Berbeda dengan socket `/tmp/cc-socks/*.sock` dan key file `~/.claude/sessions/*.key` yang secara eksplisit diset ke `chmod 0600`, file inbox dan cursor tidak diset permission khusus.
* **Dampak:**
  Pengguna lokal lain pada sistem (atau proses dengan privilege user umum) dapat membaca seluruh percakapan inter-agent yang tersimpan dalam format JSON plain-text di `~/.agent-peer/inbox.jsonl`.

### 1.2 Potensi Token Leaks / Lack of Socket Verification pada Response Flow
* **Lokasi Code:** [sender.py](file:///Users/rg/projects/agent-peer/agent_peer/sender.py#L22-L28), [registry.py](file:///Users/rg/projects/agent-peer/agent_peer/registry.py#L50-L65)
* **Temuan:**
  Fungsi `sender.send_message` membaca token autentikasi (`peerToken`) langsung dari file registrasi `~/.claude/sessions/<pid>.<hash>.key`. Namun, saat pengirim (`sender`) mengautentikasi ke socket UDS target, target hanya mencocokkan token string saja (`frame.get("token") == self.peer_token`) di [listener.py](file:///Users/rg/projects/agent-peer/agent_peer/listener.py#L161-L164) tanpa memverifikasi kredensial OS pengirim (seperti `SO_PEERCRED` / `LOCAL_PEERCRED` pada Unix Domain Sockets).
* **Dampak:**
  Setiap proses lokal yang dapat membaca direktori `~/.claude/sessions/` dapat menyamar sebagai pengirim manapun (*impersonation*) dan mengirimkan instruksi ke socket agent mana pun.

---

## 2. Masalah Keandalan & Konkurrensi (Reliability & Race Conditions)

### 2.1 Race Condition pada Pengulangan Tulis Inbox File (Non-Atomic File I/O)
* **Lokasi Code:** [inbox.py](file:///Users/rg/projects/agent-peer/agent_peer/inbox.py#L21-L34)
* **Temuan:**
  Penulisan pesan masuk di [inbox.py](file:///Users/rg/projects/agent-peer/agent_peer/inbox.py) dilakukan dengan `open(..., "a")` langsung dari handler thread listener (`handle_client` -> `process_incoming_frame` -> `append_inbox`).
* **Dampak:**
  Jika ada dua pesan masuk yang tiba secara bersamaan dari dua thread client yang berbeda (atau dua listener berbeda), penulisan ke `inbox.jsonl` atau `inboxes/<session>.jsonl` dapat mengalami *interleaving* (karakter terpotong/bercampur) karena penulisan tanpa locking mechanism (misalnya `fcntl.flock`). Hal ini menyebabkan `json.loads(line)` di `read_inbox()` gagal me-parse line tersebut dan membuang (*drop*) pesan secara diam-diam.

### 2.2 Desynchronization Cursor pada File Reset / Manual Truncation
* **Lokasi Code:** [inbox.py](file:///Users/rg/projects/agent-peer/agent_peer/inbox.py#L60-L90)
* **Temuan:**
  Cursor penanda `last_read_at` mengandalkan epoch float (`time.time()`). Apabila `agent-peer inbox --clear` dijalankan, file inbox dikosongkan (`w`), tetapi file cursor di `~/.agent-peer/cursors/<session>.json` tidak di-reset atau di-delete.
* **Dampak:**
  Jika pesan baru masuk setelah `--clear`, tetapi pesan tersebut memiliki timestamp yang lebih kecil atau sama dari `last_read_at` sebelumnya (misal dalam pengujian cepat atau pencatatan waktu yang berdekatan), pesan baru tersebut berisiko terlewat (*skipped*) oleh `get_unread()`.

### 2.3 Symlink Poisoning & Deadlock Risks pada `/tmp/cc-socks/`
* **Lokasi Code:** [listener.py](file:///Users/rg/projects/agent-peer/agent_peer/listener.py#L75-L80)
* **Temuan:**
  Di [listener.py](file:///Users/rg/projects/agent-peer/agent_peer/listener.py#L78), listener mencoba melakukan `os.unlink(self.symlink_path)` dan `os.symlink(self.sock_path, self.symlink_path)`. Karena `/tmp` adalah sticky directory yang dibagikan antar pengguna lokal, symlink statis seperti `/tmp/cc-socks/antigravity.sock` rentan terhadap masalah permission jika dibuat oleh pengguna lain atau jika terjadi tubrukan nama socket.

---

## 3. Masalah Desain & Skalabilitas (Architecture & Usability)

### 3.1 Synchronous Blocking Operations pada AppleScript & GUI Triggers
* **Lokasi Code:** [listener.py](file:///Users/rg/projects/agent-peer/agent_peer/listener.py#L235-L242)
* **Temuan:**
  Pada pencatatan pesan masuk, `listener.py` mengeksekusi `subprocess.Popen(["osascript", "-e", script])` untuk menampilkan notifikasi desktop macOS. Walau menggunakan `Popen`, dalam kondisi macOS `notificationcenterd` hang atau di bawah load tinggi, *forking subprocess* berulang tanpa limit/throttling dapat memicu penumpukan proses zombie/resource exhaustion jika menerima burst pesan.

### 3.2 Pembersihan Resource Tidak Terjamin saat Unexpected Crash
* **Lokasi Code:** [listener.py](file:///Users/rg/projects/agent-peer/agent_peer/listener.py#L257-L263)
* **Temuan:**
  Fungsi `cleanup()` hanya didaftarkan pada handler signal `SIGINT` dan `SIGTERM`. Jika proses terkena `SIGKILL` (`kill -9`), segfault, atau crash tak terduga, file registrasi JSON di `~/.claude/sessions/<pid>.json` dan socket file di `/tmp/cc-socks/` akan tertinggal secara permanen (*stale files*) sampai dibersihkan secara manual.

---

## Ringkasan Rekomendasi Perbaikan
1. **File Permissions:** Terapkan `os.chmod(..., 0o700)` pada `~/.agent-peer/` dan `0o600` pada semua file `.jsonl` / `.json` cursor.
2. **Atomic Writes & File Locking:** Gunakan `fcntl.flock(f, fcntl.LOCK_EX)` saat menambah baris di `append_inbox()` dan memperbarui cursor di `_write_cursor()`.
3. **Synchronization & Cleanup Cursor:** Pastikan `clear_inbox()` juga menghapus atau me-reset file cursor yang sesuai.
