# Review & Audit `agent-peer` + `SKILL.md` dari Perspektif Harness Google Antigravity (AGY)

**Penulis:** Google Antigravity Agent (AGY)  
**Tanggal:** 17 September 2026  
**Konteks Sesi:** Evaluasi nyata penerimaan pesan, backlog cursor, lock concurrency, auth-fix, multi-harness relay test (AGY -> PI -> OPENCODE -> AGY), dan auto-session name detection.

---

## 1. Executive Summary

Dalam rangkaian pengujian live multi-harness, `agent-peer` membuktikan fungsionalitas inti IPC (inter-process communication) antar-agen dengan sangat baik:
- Latensi pengiriman via Unix Domain Socket konsisten di **< 200ms** (~165-174ms).
- Fitur **unread cursor & backlog merging** berhasil menangkap pesan yang datang saat agen sedang sibuk menjalankan task lain tanpa ada pesan yang hilang.
- Exclusive lock `fcntl.flock` berhasil mencegah *race condition* ganda untuk nama sesi yang sama.

Namun, pengujian mendalam dari perspektif harness **Google Antigravity** menunjukkan beberapa **ambiguitas pada `SKILL.md`**, **edge case pada proses auto-detection**, dan **potensi masalah konkurrensi pada subagent/multi-session** yang kritis untuk diperbaiki.

---

## 2. Ambiguitas & Masalah pada `SKILL.md`

### 2.1 ~~Perintah `/agent-peer intro` Tidak Kompatibel dengan CLI Antigravity~~ — DITARIK
* **Klaim awal:** Baris 8 menyebutkan `Trigger explicitly any time with /agent-peer intro to load this skill`, sempat dinilai gak kompatibel dengan slash command bawaan Antigravity.
* **Koreksi (verifikasi ulang di sesi ini):** `/agent-peer` terbukti didukung penuh & terdaftar resmi begitu skill terpasang — integrasi UI Antigravity untuk custom skill slash command berjalan sempurna. Klaim 2.1 di atas **tidak valid**, dibiarkan tercoret (bukan dihapus) sebagai jejak investigasi.

### 2.2 Kerancuan antara Role Alias vs Registered Session Name
* **Masalah pada SKILL.md:** Bagian B menginstruksikan `agent-peer send <peer-name> "[fyi from antigravity]: ..."` dan memperbolehkan `--sender`.
* **Kenyataan Harness:** Saat pengirim mengeksekusi `agent-peer send claude-test "..."`, perintah tersebut gagal (`ValueError: Session 'claude-test' not found`) jika listener penerima terdaftar di `~/.claude/sessions/` dengan nama fisik seperti `agent-peer-e4`. Parameter `--sender` pada frame pengirim hanya mengubah string `from` di dalam payload, bukan mendaftarkan alias nama di registry.
* **Rekomendasi:** Tegaskan di `SKILL.md` bahwa `<peer-name>` **HARUS** cocok dengan kolom `SESSION NAME` yang muncul pada `agent-peer list`, bukan nama alias fungsional/peran agen (kecuali sesi tersebut memang di-start dengan `--name <role>`).

---

## 3. Edge Cases Auto-Detection (`detect_harness_identity`)

### 3.1 Subagent & Parallel Task Lock Collision
* **Mekanisme Auto-Detect:** `detect_harness_identity()` berjalan ke atas process tree (`os.getppid()`) mencari nama proses non-generik pertama. Untuk Antigravity, ia menemukan `agy` dengan PID utama (misal `PID 33402`), lalu membentuk nama `agy-33402`.
* **Masalah pada Subagent/Child Tasks:**
  Jika agen Antigravity memanggil subagent (misalnya via `invoke_subagent` atau background worker processes dalam workspace yang sama), seluruh subagent tersebut akan mewarisi parent process `agy` yang **sama** (PID `33402`).
  Akibatnya:
  1. Semua subagent akan ter-detect dengan nama yang **sama persis** (`agy-33402`).
  2. Ketika subagent A dan subagent B sama-sama memanggil `agent-peer wait`, subagent B akan langsung gagal karena terkena `fcntl.flock` lock di `~/.agent-peer/locks/agy-33402.lock` ("already running").
  3. Pesan yang ditujukan untuk subagent tertentu akan tercampur di inbox `agy-33402`.
* **Rekomendasi:** Tambahkan aturan di `SKILL.md` bahwa jika agen menjalankan subagent terpisah atau task paralel yang membutuhkan komunikasi independen, subagent **WAJIB** menentukan nama eksplisit (misal `agent-peer wait --name agy-subagent-1`).

### 3.2 Timbunan File Lock & Multi-Name Standby Leak
* **Kenyataan Harness Antigravity:**
  Antigravity mengelola background process via `run_command` dengan `WaitMsBeforeAsync: 1000`. Selama satu sesi panjang, agen bisa saja mengganti nama standby dari `--name antigravity-test` ke auto-detected `agy-33402`.
* **Efek:** `task-58` terus memegang lock `antigravity-test.lock`, sementara `task-84` memegang lock `agy-33402.lock`. Dua listener/waiter terpisah berjalan bersamaan di background OS tanpa saling melepaskan lock karena nama sesinya berbeda.
* **Rekomendasi:** `agent-peer` perlu memiliki perintah `agent-peer stop` atau *cleanup lock* otomatis jika ada pendeteksian pid induk yang sama tetapi dengan alias nama lama.

---

## 4. Evaluasi Keamanan & Reliability Codebase (Ringkasan Temuan Teknis)

1. **Permissions File Inbox (`~/.agent-peer/inbox.jsonl`):**
   Direktori `~/.agent-peer/` dan file inbox dibuat dengan permission default umask (`0644`). Di lingkungan multi-user Unix, pengguna lain dapat membaca isi pesan inter-agent. *Perbaikan: Wajib set `chmod 0700` pada folder dan `0600` pada file inbox/cursor.*
2. **Missing `SO_PEERCRED` Socket Verification:**
   `listener.py` memverifikasi token autentikasi string, tetapi tidak memverifikasi kredensial socket OS pengirim (`SO_PEERCRED`).
3. **Non-Atomic File Append (`append_inbox`):**
   `append_inbox()` melakukan penulisan pesan tanpa `fcntl.flock`. Di bawah beban tinggi dari multiple socket threads, penulisan JSONL dapat mengalami interleaving line.
4. **Sanitasi Notifikasi (AppleScript / Terminal Notifier):**
   Regex `clean_snippet = re.sub(r'<[^>]+>', '', content)` di `listener.py:216` memotong tag HTML/XML. Jika agen mengirimkan potongan kode JSX/HTML (seperti `<div>`), karakter di dalam tag tersebut terhapus secara tidak sengaja dari tampilan notifikasi.

---

## 5. Kesimpulan & Check-list Perbaikan SKILL.md

| Item | Status | Tindakan yang Direkomendasikan |
|---|---|---|
| Slash Command `/agent-peer` | Ambigus | Hapus referensi `/agent-peer intro` sebagai slash command bawaan |
| Peer Name Resolution | Rawan Error | Tegaskan `<peer-name>` harus persis sesuai hasil `agent-peer list` |
| Subagent Isolation | Risk High | Dokumentasikan kewajiban `--name <subagent-id>` untuk subagent |
| File & Socket Security | Risk Med | Terapkan `chmod 0600` pada inbox/cursors & lock files |
| Atomic File Write | Risk Med | Gunakan `fcntl.flock` pada `append_inbox()` |
