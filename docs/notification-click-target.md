# Fix: Notification Click Opens Script Editor Instead of iTerm

## Problem

Desktop notifications triggered by `listener.py` upon message arrival (`osascript -e 'display notification ...'`) always get attributed to **Script Editor.app** when clicked — instead of the relevant application (iTerm2), regardless of the AppleScript script content.

## Root Cause

This is standard macOS behavior, not a bug in `agent-peer`: `osascript` as a command-line tool is associated with `Script Editor.app` as its default AppleScript runner. macOS attributes any notification triggered via `osascript` to that identity, so clicking it always activates Script Editor — there is no way to alter the click target while still routing through `osascript`.

## Fix

Replace the notification trigger with [`terminal-notifier`](https://github.com/julienXX/terminal-notifier), which supports the `-activate <bundle-id>` flag — activating the app matching that bundle ID when the notification is clicked, independent of the sender identity.

`listener.py`:
```python
NOTIFY_ACTIVATE_BUNDLE_ID = "com.googlecode.iterm2"   # iTerm2 bundle ID

if shutil.which("terminal-notifier"):
    subprocess.Popen([
        "terminal-notifier", "-title", title, "-message", clean_snippet,
        "-activate", NOTIFY_ACTIVATE_BUNDLE_ID, "-sound", "Glass"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
else:
    # fallback to osascript if terminal-notifier is missing on other machines
    ...
```

Implementation Notes:
- **Do not use the `-sender` flag** alongside `-activate` — this causes conflicts on macOS Sequoia 15.x+ and breaks click-to-focus behavior (confirmed from community research).
- iTerm2 bundle ID confirmed via `osascript -e 'id of app "iTerm"'` → `com.googlecode.iterm2`.
- Fallback to `osascript` exists if `terminal-notifier` is not installed (checked via `shutil.which` dynamically on each call, not cached — so it automatically gets used if installed later without requiring a listener restart), ensuring portability across machines lacking `terminal-notifier`.

## Testing Gotcha Discovered

Notifications were "successfully delivered" (`terminal-notifier -list ALL` showed "Delivered" status) but **did not appear as banners** on screen. Root cause: macOS requires explicit notification permissions per application, and because this was the first time `terminal-notifier` (an app identity distinct from `osascript`/Script Editor) sent notifications on this machine, permissions were not granted by default.

**Manual Fix (one-time per machine):** System Settings → Notifications → search for "terminal-notifier" → ensure "Allow Notifications" is enabled and style is not set to "None".

## Status

Implemented, tested, and confirmed — clicking notifications now opens iTerm instead of Script Editor.
