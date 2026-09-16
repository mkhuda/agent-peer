# Fix: notifikasi klik buka Script Editor, bukan iTerm

## Masalah

Notifikasi desktop yang dipicu `listener.py` saat pesan masuk (`osascript -e
'display notification ...'`) selalu ter-attribute ke **Script Editor.app**
saat diklik — bukan app yang relevan (iTerm2), berapapun isi skrip AppleScript-nya.

## Root cause

Ini perilaku macOS, bukan bug di `agent-peer`: `osascript` sebagai command-line
tool terasosiasi dengan `Script Editor.app` sebagai default AppleScript runner.
macOS meng-attribute notifikasi apapun yang dipicu lewat `osascript` ke identitas
itu, jadi klik selalu mengaktifkan Script Editor — gak ada cara mengubah target
klik selama masih lewat `osascript`.

## Fix

Ganti pemicu notifikasi dengan [`terminal-notifier`](https://github.com/julienXX/terminal-notifier)
(sudah terinstall di mesin ini, `/usr/local/bin/terminal-notifier`), yang
mendukung flag `-activate <bundle-id>` — app dengan bundle id itu yang
diaktifkan saat notifikasi diklik, independen dari identitas pengirim.

`listener.py`:
```python
NOTIFY_ACTIVATE_BUNDLE_ID = "com.googlecode.iterm2"   # bundle id iTerm2

if shutil.which("terminal-notifier"):
    subprocess.Popen([
        "terminal-notifier", "-title", title, "-message", clean_snippet,
        "-activate", NOTIFY_ACTIVATE_BUNDLE_ID, "-sound", "Glass"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
else:
    # fallback ke osascript kalau terminal-notifier gak ada di mesin lain
    ...
```

Catatan implementasi:
- **Jangan pakai flag `-sender`** bersamaan dengan `-activate` — konflik di
  macOS Sequoia 15.x+ dan bikin click-to-focus gak jalan (dikonfirmasi dari
  riset komunitas).
- Bundle id iTerm2 dikonfirmasi via `osascript -e 'id of app "iTerm"'` →
  `com.googlecode.iterm2`.
- Ada fallback ke `osascript` kalau `terminal-notifier` gak terinstall (dicek
  `shutil.which` tiap kali, bukan di-cache — supaya otomatis kepakai begitu
  di-install belakangan tanpa perlu restart listener), supaya tetap portable
  ke mesin lain yang belum punya `terminal-notifier`.

## Gotcha yang ditemukan saat testing

Notifikasi sempat "berhasil terkirim" (`terminal-notifier -list ALL` nunjukin
status "Delivered") tapi **gak muncul sebagai banner** di layar. Root cause:
macOS butuh izin notifikasi eksplisit per-app, dan karena ini pertama kalinya
`terminal-notifier` (identitas app terpisah dari `osascript`/Script Editor)
ngirim notifikasi di mesin ini, izinnya belum granted secara default.

**Fix manual (sekali saja, per mesin):** System Settings → Notifications →
cari "terminal-notifier" → pastikan "Allow Notifications" nyala dan style-nya
bukan "None".

## Status

Diimplementasikan, dites, dan dikonfirmasi user — klik notifikasi sekarang
membuka iTerm, bukan Script Editor.
