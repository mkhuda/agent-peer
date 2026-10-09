"""Bridges one agent-peer thread to one Telegram chat, both directions.

Stdlib only; talks to the `agent-peer` CLI as a subprocess and to the Telegram Bot API over HTTPS.
Each user creates their own bot (BotFather) - no shared bot or token exists.
"""
from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Optional

from .protocol import atomic_write_json
from .thread import read_thread

DEFAULT_NAME = "telegram-bridge"
MAX_TELEGRAM_CHARS = 3900  # Telegram's real cap is 4096 - leave room for a sender prefix
POLL_TIMEOUT_S = 30  # Telegram long-poll window - one open request, not a tight loop
RETRY_BASE_S = 2
MAX_RETRIES = 5

# crc32, not hash(): str hashes are randomized per process, and a sender must keep its dot across restarts.
_COLOR_DOTS = ["🔵", "🟠", "🟢", "🟣", "🟡", "🔴", "🟤", "⚪"]

# The urgency/kind prefixes every skill writes at the start of a message.
_TYPE_ICONS = {
    "fyi": "ℹ️",
    "stop": "🛑",
    "change": "🔄",
    "ask": "❓",
    "ack": "✅",
    "direct": "📌",
    "report": "📋",
}
_TAG_RE = re.compile(r"^\[([a-zA-Z]+)\]:\s*(.*)$", re.DOTALL)
_REF_RE = re.compile(r"#(\d+)")
# Wrapped in <code> so Telegram does not auto-link a bare @word as a username.
_MENTION_RE = re.compile(r"@([A-Za-z0-9_.\-]+)")
_BLOCKQUOTE_CHAR_THRESHOLD = 300
_BLOCKQUOTE_LINE_THRESHOLD = 3

_stop = threading.Event()
_fatal = threading.Event()
_active_follow_proc: dict = {"proc": None}
_active_follow_lock = threading.Lock()

# ValueError covers a 200 reply with a non-JSON body; it is retried like a network error.
_API_ERRORS = (urllib.error.URLError, TimeoutError, OSError, ValueError)


_CONFIG_KEYS = ("TELEGRAM_BOT_KEY", "TELEGRAM_CHAT_ID", "TELEGRAM_ALLOWED_USER_IDS")
_DEFAULT_ENV_FILE = Path.home() / ".agent-peer" / "telegram.env"


def _read_env_file(path: Path) -> dict:
    """Only the Telegram keys are read, and never copied into os.environ, so an unrelated
    project `.env` cannot leak its other secrets into the child agent-peer processes."""
    values = {}
    try:
        if os.name == "posix" and os.stat(path).st_mode & 0o077:
            print(f"[telegram-bridge] {path} is readable by others; run: chmod 600 {path}", file=sys.stderr)
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() in _CONFIG_KEYS:
                    values[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


def load_config(env_file: Optional[str] = None) -> dict:
    """Real environment variables win over the file (default ~/.agent-peer/telegram.env)."""
    cfg = _read_env_file(Path(env_file).expanduser() if env_file else _DEFAULT_ENV_FILE)
    for key in _CONFIG_KEYS:
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    return cfg


def _allowed_user_ids(cfg: dict) -> set:
    return {p.strip() for p in cfg.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if p.strip()}


def _is_authorized(msg: dict, chat_id: str, allowed: set) -> bool:
    if str(msg.get("chat", {}).get("id")) != str(chat_id):
        return False
    return not allowed or str((msg.get("from") or {}).get("id")) in allowed


class _State:
    """last relayed thread seq and Telegram update offset, so a restart neither repeats nor skips messages."""

    def __init__(self, thread_id: str):
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", thread_id)
        self.path = Path.home() / ".agent-peer" / f"telegram.{name}.json"
        self._lock = threading.Lock()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, ValueError):
            self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value: int) -> None:
        with self._lock:
            self._data[key] = value
            try:
                atomic_write_json(str(self.path), self._data)
            except OSError:
                pass


def _child_env() -> dict:
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _api(token: str, method: str, params: dict, timeout: float) -> dict:
    """Telegram error replies (4xx/5xx) come back as the same JSON shape with ok=false."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("ok", False)
        data.setdefault("error_code", exc.code)
        return data


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def _split(text: str, limit: int = MAX_TELEGRAM_CHARS) -> list:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut >= limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text]


def _send_one(token: str, chat_id: str, text: str, formatted: bool) -> None:
    for attempt in range(MAX_RETRIES):
        params = {"chat_id": chat_id, "text": text}
        if formatted:
            params["parse_mode"] = "HTML"
        try:
            result = _api(token, "sendMessage", params, timeout=15)
        except _API_ERRORS:
            time.sleep(RETRY_BASE_S * (attempt + 1))
            continue
        if result.get("ok"):
            return
        code = result.get("error_code")
        if code == 400 and formatted:
            formatted, text = False, _plain(text)  # Telegram rejected the markup - send it unformatted
            continue
        if code in (400, 401, 403, 404):
            print(f"[telegram-bridge] Telegram refused the message ({code}): {result.get('description')}", file=sys.stderr)
            if code == 401:
                _fatal.set()
                _stop.set()
            return
        # 429 flood control carries the exact wait Telegram wants.
        retry_after = (result.get("parameters") or {}).get("retry_after")
        time.sleep(retry_after if retry_after else RETRY_BASE_S * (attempt + 1))
    print("[telegram-bridge] gave up sending a message after repeated failures.", file=sys.stderr)


def _send_to_telegram(token: str, chat_id: str, text: str) -> None:
    if len(text) <= MAX_TELEGRAM_CHARS:
        _send_one(token, chat_id, text, True)
        return
    for chunk in _split(_plain(text)):  # a cut could land inside a tag, so long messages go unformatted
        _send_one(token, chat_id, chunk, False)


def _color_for(name: str) -> str:
    return _COLOR_DOTS[zlib.crc32(name.encode("utf-8")) % len(_COLOR_DOTS)]


def _format_for_telegram(record: dict) -> str:
    """HTML parse_mode: MarkdownV2 rejects the whole message on any unescaped punctuation."""
    raw_content = record.get("content", "")
    if record.get("type") == "event":
        return f"<i>· {html.escape(raw_content, quote=False)}</i>"

    sender = record.get("from", "unknown")
    tag_match = _TAG_RE.match(raw_content)
    tag, body = (tag_match.group(1).lower(), tag_match.group(2)) if tag_match else (None, raw_content)

    body = html.escape(body, quote=False)
    body = _REF_RE.sub(r"<code>#\1</code>", body)
    body = _MENTION_RE.sub(r"<u><code>@\1</code></u>", body)
    if len(body) > _BLOCKQUOTE_CHAR_THRESHOLD or body.count("\n") > _BLOCKQUOTE_LINE_THRESHOLD:
        body = f"<blockquote expandable>{body}</blockquote>"

    header = f"{_color_for(sender)} <b><code>@{html.escape(sender, quote=False)}</code></b>"
    if tag:
        header += f"  {_TYPE_ICONS.get(tag, '💬')}"

    return f"{header}\n{body}"


def _drain_stderr(pipe, label: str) -> None:
    """Surfaces the follow child's stderr - the only place a setup mistake becomes visible."""
    try:
        for line in pipe:
            line = line.rstrip()
            if line:
                print(f"[telegram-bridge] {label}: {line}", file=sys.stderr)
    except ValueError:
        pass  # pipe closed under us during shutdown - nothing left to drain


_REPLAY_ON_RESTART = 500
DEFAULT_MAX_AGE_MINUTES = 0.0


def _skip_stale_backlog(thread_id: str, participant: str, token: str, chat_id: str, state: _State, cutoff: float) -> None:
    """On restart, records older than `cutoff` are not relayed; one line says how many were left out."""
    last_seq = state.get("last_seq")
    if last_seq is None:
        return
    records = [r for r in read_thread(thread_id) if isinstance(r.get("seq"), int)]
    stale = [r for r in records if r["seq"] > last_seq and r.get("ts", 0) < cutoff]
    if not stale:
        return
    state.set("last_seq", max(r["seq"] for r in stale))
    skipped = sum(1 for r in stale if r.get("from") != participant and (r.get("content") or r.get("type") == "event"))
    if skipped:
        span = records[-1]["seq"] - stale[0]["seq"] + 1
        _send_to_telegram(token, chat_id, f"⏸ Bridge is back. {skipped} older messages were skipped.\n"
                                          f"Read them: <code>agent-peer logs --thread {html.escape(thread_id, quote=False)} -n {span}</code>")


def thread_to_telegram(agent_cmd: list, thread_id: str, participant: str, token: str, chat_id: str, state: _State,
                       max_age: float = DEFAULT_MAX_AGE_MINUTES * 60) -> None:
    """Relays new thread records from one persistent `logs --follow --raw` child."""
    started = time.time()
    _skip_stale_backlog(thread_id, participant, token, chat_id, state, started - max_age)
    while not _stop.is_set():
        last_seq = state.get("last_seq")
        cmd = [*agent_cmd, "logs", "--thread", thread_id, "--follow", "--raw", "-n", str(_REPLAY_ON_RESTART if last_seq is not None else 1)]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", env=_child_env(),
                                    start_new_session=os.name == "posix")  # Ctrl+C reaches only the bridge, which stops the child itself
        except OSError as exc:
            print(f"[telegram-bridge] failed to start logs --follow: {exc}", file=sys.stderr)
            time.sleep(5)
            continue
        with _active_follow_lock:
            _active_follow_proc["proc"] = proc
        threading.Thread(target=_drain_stderr, args=(proc.stderr, "logs --follow"), daemon=True).start()
        try:
            for line in proc.stdout:
                if _stop.is_set():
                    break
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                seq = record.get("seq")
                if isinstance(seq, int):
                    last_seq = state.get("last_seq")
                    # First run: the one replayed record predates this bridge, so it is not relayed.
                    if (last_seq is not None and seq <= last_seq) or (last_seq is None and record.get("ts", 0) < started):
                        if last_seq is None:
                            state.set("last_seq", seq)
                        continue
                if record.get("from") != participant and (record.get("content") or record.get("type") == "event"):
                    _send_to_telegram(token, chat_id, _format_for_telegram(record))
                if isinstance(seq, int):
                    state.set("last_seq", seq)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            with _active_follow_lock:
                if _active_follow_proc["proc"] is proc:
                    _active_follow_proc["proc"] = None
        if not _stop.is_set():
            time.sleep(3)  # the follow child died unexpectedly - restart it


_BOT_COMMANDS = {
    "/list": ["list"],
    "/status": ["status", "--no-color"],
}
_MAX_PRE_CHARS = 3500  # comfortably under Telegram's 4096 cap with the <pre> tag included


def _handle_bot_command(agent_cmd: list, token: str, chat_id: str, text: str) -> None:
    """/list and /status from the phone; never relayed into the thread."""
    word = text.strip().split()[0].split("@")[0].lower()  # strip Telegram's optional @botname suffix
    if word in ("/start", "/help"):
        commands = "\n".join(f"<code>{c}</code>" for c in sorted(_BOT_COMMANDS))
        _send_to_telegram(token, chat_id, f"Commands:\n{commands}\n\nAnything else is relayed as a message into the thread.")
        return
    args = _BOT_COMMANDS.get(word)
    if args is None:
        _send_to_telegram(token, chat_id, f"Unknown command: <code>{html.escape(word, quote=False)}</code>. Try <code>/help</code>.")
        return
    try:
        result = subprocess.run([*agent_cmd, *args], capture_output=True, text=True, timeout=15,
                                encoding="utf-8", errors="replace", env=_child_env())
    except subprocess.TimeoutExpired:
        _send_to_telegram(token, chat_id, f"⚠️ {html.escape(word, quote=False)} timed out.")
        return
    output = html.escape((result.stdout or result.stderr or "(no output)").strip(), quote=False)
    if len(output) <= _MAX_PRE_CHARS:
        output = f"<pre>{output}</pre>"
    _send_to_telegram(token, chat_id, output)


def _relay_update(agent_cmd: list, thread_id: str, participant: str, token: str, chat_id: str, allowed: set, update: dict) -> None:
    msg = update.get("message") or {}
    if not _is_authorized(msg, chat_id, allowed):
        if str(msg.get("chat", {}).get("id")) == str(chat_id):
            print(f"[telegram-bridge] ignored a message from user id {(msg.get('from') or {}).get('id')} (not in TELEGRAM_ALLOWED_USER_IDS).", file=sys.stderr)
        return
    text = msg.get("text")
    if not text:
        return
    if text.strip().startswith("/"):
        _handle_bot_command(agent_cmd, token, chat_id, text)
        return
    # argv list, no shell: backticks and $ reach agent-peer literally; "--" keeps a leading "-" from being an option.
    result = subprocess.run(
        [*agent_cmd, "send", "--thread", thread_id, "--sender", participant, "--", text],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=_child_env(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        _send_to_telegram(token, chat_id, f"⚠️ failed to post to thread: {html.escape(detail[:300], quote=False)}")


def telegram_to_thread(agent_cmd: list, thread_id: str, participant: str, token: str, chat_id: str, allowed: set, state: _State) -> None:
    offset = state.get("offset")
    if offset is None:
        # First run: updates queued before the bridge existed (setup chatter) are not relayed.
        try:
            latest = _api(token, "getUpdates", {"offset": -1, "timeout": 0}, timeout=15).get("result") or []
            offset = latest[-1]["update_id"] + 1 if latest else None
        except (*_API_ERRORS, KeyError, IndexError):
            offset = None
    warned_conflict = False
    while not _stop.is_set():
        params = {"timeout": POLL_TIMEOUT_S}
        if offset is not None:
            params["offset"] = offset
        try:
            result = _api(token, "getUpdates", params, timeout=POLL_TIMEOUT_S + 10)
        except _API_ERRORS:
            time.sleep(RETRY_BASE_S)
            continue
        if not result.get("ok"):
            code = result.get("error_code")
            if code == 401:
                print("[telegram-bridge] Telegram rejected the bot token (401). Check TELEGRAM_BOT_KEY.", file=sys.stderr)
                _fatal.set()
                _stop.set()
                return
            if code == 409:
                if not warned_conflict:
                    print("[telegram-bridge] another process is polling this bot token (409). Only one bridge can run per bot.", file=sys.stderr)
                    warned_conflict = True
                time.sleep(10)
                continue
            time.sleep(RETRY_BASE_S)
            continue
        warned_conflict = False
        for update in result.get("result", []):
            try:
                _relay_update(agent_cmd, thread_id, participant, token, chat_id, allowed, update)
            except Exception as exc:  # one bad update must not end the bridge
                print(f"[telegram-bridge] could not relay an update: {exc}", file=sys.stderr)
            if isinstance(update.get("update_id"), int):
                offset = update["update_id"] + 1
                state.set("offset", offset)


def print_chat_ids(env_file: Optional[str] = None) -> int:
    """Lists chats (and the sender's user id) with pending messages for the bot."""
    token = load_config(env_file).get("TELEGRAM_BOT_KEY")
    if not token:
        print("TELEGRAM_BOT_KEY is not set (environment or ~/.agent-peer/telegram.env).", file=sys.stderr)
        return 1
    try:
        result = _api(token, "getUpdates", {"timeout": 0}, timeout=15)
    except _API_ERRORS as exc:
        print(f"Could not reach Telegram: {exc}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"Telegram refused the request: {result.get('description')}", file=sys.stderr)
        return 1
    seen = {}
    for update in result.get("result", []):
        msg = update.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            label = chat.get("title") or chat.get("username") or chat.get("first_name")
            seen[chat["id"]] = f"{label}\tuser id {(msg.get('from') or {}).get('id')}"
    if not seen:
        print("No pending messages. Send the bot a message first, then re-run this.")
        return 0
    for chat_id, label in seen.items():
        print(f"{chat_id}\t{label}")
    return 0


def run(thread_id: str, participant: str, env_file: Optional[str] = None, max_age_minutes: float = DEFAULT_MAX_AGE_MINUTES) -> int:
    """Blocks until Ctrl+C/SIGTERM. Returns a process exit code."""
    _stop.clear()
    _fatal.clear()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)

    cfg = load_config(env_file)
    token = cfg.get("TELEGRAM_BOT_KEY")
    if not token:
        print("TELEGRAM_BOT_KEY is not set (environment or ~/.agent-peer/telegram.env).", file=sys.stderr)
        return 1
    chat_id = cfg.get("TELEGRAM_CHAT_ID")
    if not chat_id:
        print("TELEGRAM_CHAT_ID is not set. Run with --print-chat-id first to find it.", file=sys.stderr)
        return 1
    allowed = _allowed_user_ids(cfg)
    if chat_id.startswith("-") and not allowed:
        print("A group chat needs TELEGRAM_ALLOWED_USER_IDS (comma-separated user ids); anyone in the "
              "group could otherwise instruct your agents. --print-chat-id shows your user id.", file=sys.stderr)
        return 1
    agent_cmd = [sys.executable, "-m", "agent_peer"]
    state = _State(thread_id)

    def _handle_signal(signum, frame):
        _stop.set()

    prev_sigint = signal.signal(signal.SIGINT, _handle_signal)
    prev_sigterm = signal.signal(signal.SIGTERM, _handle_signal)
    prev_sighup = signal.signal(signal.SIGHUP, _handle_signal) if hasattr(signal, "SIGHUP") else None

    failed = threading.Event()

    def guarded(fn, *fn_args):
        try:
            fn(*fn_args)
        except BaseException as exc:
            failed.set()
            _stop.set()
            print(f"[telegram-bridge] {getattr(fn, '__name__', 'worker')} stopped: {exc!r}", file=sys.stderr)

    workers = [
        threading.Thread(target=guarded, args=(thread_to_telegram, agent_cmd, thread_id, participant, token, chat_id, state, max(max_age_minutes, 0) * 60), daemon=True),
        threading.Thread(target=guarded, args=(telegram_to_thread, agent_cmd, thread_id, participant, token, chat_id, allowed, state), daemon=True),
    ]
    for w in workers:
        w.start()

    print(f"[telegram-bridge] bridging thread '{thread_id}' as '{participant}'. Ctrl+C to stop.")
    try:
        while not _stop.is_set():
            time.sleep(0.5)
    finally:
        # Daemon threads die with the interpreter without running their
        # `finally` blocks - terminate the tracked subprocess explicitly so
        # `logs --follow` never survives as an orphan past this shutdown.
        with _active_follow_lock:
            proc = _active_follow_proc["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        for w in workers:
            w.join(timeout=2)
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
        if prev_sighup is not None:
            signal.signal(signal.SIGHUP, prev_sighup)

    print("[telegram-bridge] stopped.")
    return 1 if failed.is_set() or _fatal.is_set() else 0
