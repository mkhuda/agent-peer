"""agy: deliver a message as a real user turn through its internal Cascade Language Server.

agy exports the server's address, CSRF token and conversation id into every shell it spawns,
so `agent-peer listen` run inside agy reads them from its own environment. The token is
kept only in a 0600 file under ~/.agent-peer/agy-ls/, never in the registry, a log or a message.
Everything here fails safe: `send` returns None whenever the generic socket path should be used.
"""
import http.client
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit

from .. import compat, protocol

REGISTRY_FIELD = "agyConversationId"
OFF_SWITCH_ENV = "AGENT_PEER_AGY_NATIVE"  # set to 0 to force the socket path
_ENV_ADDRESS = "ANTIGRAVITY_LS_ADDRESS"
_ENV_TOKEN = "ANTIGRAVITY_CSRF_TOKEN"
_ENV_CONVERSATION = "ANTIGRAVITY_CONVERSATION_ID"
_SERVICE = "exa.language_server_pb.LanguageServerService"
_MODEL_LOOKUP_TIMEOUT = 15.0  # GetCascadeTrajectory carries the whole history, 1.4-2 s on a long conversation
_SEND_TIMEOUT = 5.0
_TAIL_TIMEOUT = 3.0  # per call of the cheap model lookup
_TAIL_DEADLINE = 4.0  # for all of its calls together; anything slower falls back to the full history
_TAIL_STEPS = (5, 50)  # how many recent steps to read, widened once, before reading the whole history
_MODEL_CACHE_SECONDS = 60.0  # a model switched inside this window is picked up one turn late
_STALE_MODEL_SECONDS = 600.0  # only after a dropped read; a model switched meanwhile is overridden for that turn
_TRANSIENT = (http.client.HTTPException, ConnectionError)  # a truncated or dropped read; timeouts are not retried
_FALLBACK_LOG_LINES = 50
progress: Dict[int, str] = {}  # thread id -> what a send is doing now, read by a poster that gives up waiting
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _valid_conversation(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _valid_address(address: Any) -> bool:
    """The token is only ever sent to a loopback host."""
    try:
        parts = urlsplit("http://" + str(address))
        return parts.hostname in _LOOPBACK and parts.port is not None
    except ValueError:
        return False


def _endpoints_dir() -> str:
    return os.path.join(protocol.AGENT_PEER_DIR, "agy-ls")


def _endpoint_path(conversation: str) -> str:
    return os.path.join(_endpoints_dir(), f"{conversation}.json")


def identity(env: Mapping[str, str]) -> Optional[str]:
    conversation = env.get(_ENV_CONVERSATION)
    return conversation if _valid_conversation(conversation) else None


def _names_dir() -> str:
    return os.path.join(protocol.AGENT_PEER_DIR, "agy-names")


def remember_name(env: Mapping[str, str], name: Optional[str]) -> None:
    """Keep the session name per conversation: a clean `listen` exit unregisters, so the
    registry alone cannot give a resumed conversation its name back."""
    conversation = identity(env)
    if not conversation or not name:
        return
    try:
        os.makedirs(_names_dir(), exist_ok=True)
        path = os.path.join(_names_dir(), f"{conversation}.json")
        tmp = f"{path}.tmp.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"name": name, "updatedAt": time.time()}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def remembered_name(env: Mapping[str, str]) -> Optional[str]:
    conversation = identity(env)
    if not conversation:
        return None
    try:
        with open(os.path.join(_names_dir(), f"{conversation}.json"), "r", encoding="utf-8") as f:
            name = json.load(f).get("name")
    except (OSError, ValueError, AttributeError):
        return None
    return name if isinstance(name, str) and name else None


def _prune_stale(directory: str) -> None:
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                pid = json.load(f).get("pid")
            if not (isinstance(pid, int) and compat.is_pid_alive(pid)):
                os.unlink(path)
        except (OSError, ValueError, AttributeError):
            continue


def listen_info(env: Mapping[str, str]) -> Dict[str, Any]:
    """Non-secret registration fields; also (re)writes the endpoint file, whose address
    changes with every agy process. {} when the environment is not agy's."""
    conversation = identity(env)
    address, token = env.get(_ENV_ADDRESS), env.get(_ENV_TOKEN)
    if not conversation or not token or not _valid_address(address):
        return {}
    directory = _endpoints_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        _prune_stale(directory)
        path = _endpoint_path(conversation)
        tmp = f"{path}.tmp.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"addr": address, "token": token, "conversationId": conversation,
                       "pid": os.getpid(), "updatedAt": time.time()}, f)
        os.replace(tmp, path)
        compat.secure_file(path)
    except OSError:
        return {}
    return {REGISTRY_FIELD: conversation}


def _read_endpoint(conversation: str) -> Optional[Dict[str, Any]]:
    try:
        with open(_endpoint_path(conversation), "r", encoding="utf-8") as f:
            endpoint = json.load(f)
    except (OSError, ValueError):
        return None
    if (not isinstance(endpoint, dict) or endpoint.get("conversationId") != conversation
            or not isinstance(endpoint.get("token"), str) or not _valid_address(endpoint.get("addr"))
            or not isinstance(endpoint.get("pid"), int) or not compat.is_pid_alive(endpoint["pid"])):
        return None
    return endpoint


# The server is on loopback: never send it through an environment or system proxy (a VPN can switch one on).
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _rpc(endpoint: Dict[str, Any], method: str, body: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    request = urllib.request.Request(
        f"http://{endpoint['addr']}/{_SERVICE}/{method}", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": endpoint["token"]})
    try:
        with _LOCAL_OPENER.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        exc.close()
        raise


def _find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], (str, dict)):
            return obj[key]
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        hit = _find(child, key)
        if hit is not None:
            return hit
    return None


def _find_last(obj: Any, key: str) -> Any:
    """The last value stored under `key` (a string or an object), in document order."""
    found = None
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], (str, dict)):
            found = obj[key]
        children = list(obj.values())
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        hit = _find_last(child, key)
        if hit is not None:
            found = hit
    return found


def note_fallback(conversation: str, stage: str, exc: Optional[BaseException] = None, started: Optional[float] = None) -> None:
    """One line saying why the native door was not used (no token, no message text); best effort."""
    try:
        directory = _endpoints_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "last-fallback.log")
        reason = type(exc).__name__ if exc else "none"
        code = getattr(exc, "code", "")
        took = f" after {time.time() - started:.1f}s" if started else ""
        line = f"{time.strftime('%H:%M:%S')} {conversation[:8]} stage={stage} reason={reason}{(' http=' + str(code)) if code else ''}{took}"
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        tmp = f"{path}.tmp.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join((lines + [line])[-_FALLBACK_LOG_LINES:]) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _model_cache_path(conversation: str) -> str:
    return os.path.join(_endpoints_dir(), f"{conversation}.model")


def _cached_model(endpoint: Dict[str, Any], conversation: str, max_age: float = _MODEL_CACHE_SECONDS) -> Optional[str]:
    try:
        with open(_model_cache_path(conversation), "r", encoding="utf-8") as f:
            cached = json.load(f)
        if (cached.get("addr") == endpoint.get("addr") and isinstance(cached.get("model"), str)
                and 0 <= time.time() - float(cached.get("at", 0)) < max_age):
            return cached["model"]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def _remember_model(endpoint: Dict[str, Any], conversation: str, model: str) -> None:
    try:
        tmp = f"{_model_cache_path(conversation)}.tmp.{os.getpid()}"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"model": model, "addr": endpoint.get("addr"), "at": time.time()}, f)
        os.replace(tmp, _model_cache_path(conversation))
    except OSError:
        pass


def _model_from_recent_steps(endpoint: Dict[str, Any], conversation: str) -> Optional[str]:
    """The model of the latest generation, read from the last few steps: two small calls instead of the
    whole history, which on a long conversation is tens of megabytes. None = ask the full history."""
    deadline = time.monotonic() + _TAIL_DEADLINE

    def budget() -> float:
        return min(_TAIL_TIMEOUT, deadline - time.monotonic())

    try:
        summaries = _rpc(endpoint, "GetAllCascadeTrajectories", {}, budget()).get("trajectorySummaries")
        count = (summaries or {}).get(conversation, {}).get("stepCount")
        if not isinstance(count, int) or count <= 0:
            return None
        for window in _TAIL_STEPS:
            if budget() < 0.3:
                return None
            steps = _rpc(endpoint, "GetCascadeTrajectorySteps",
                         {"cascadeId": conversation, "stepOffset": max(count - window, 0)}, budget())
            for key in ("generatorModel", "planModel"):
                hit = _find_last(steps, key)
                if isinstance(hit, str) and hit:
                    return hit
    except Exception:
        pass
    return None


def _conversation_model(endpoint: Dict[str, Any], conversation: str) -> Optional[str]:
    """The conversation's own model enum; its names change per agy version, so never hardcoded.
    Cached briefly per conversation and LS address: the lookup reads the whole history."""
    cached = _cached_model(endpoint, conversation)
    if cached:
        return cached
    progress[threading.get_ident()] = "tail"
    recent = _model_from_recent_steps(endpoint, conversation)
    if recent:
        _remember_model(endpoint, conversation, recent)
        return recent
    progress[threading.get_ident()] = "history"
    deadline = time.monotonic() + _MODEL_LOOKUP_TIMEOUT
    try:
        try:
            trajectory = _rpc(endpoint, "GetCascadeTrajectory", {"cascadeId": conversation}, _MODEL_LOOKUP_TIMEOUT)
        except _TRANSIENT:
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                raise
            trajectory = _rpc(endpoint, "GetCascadeTrajectory", {"cascadeId": conversation}, remaining)
    except _TRANSIENT:
        stale = _cached_model(endpoint, conversation, _STALE_MODEL_SECONDS)
        if stale:
            return stale
        raise
    chat_model = _find(trajectory, "chatModel")
    candidates = [chat_model.get("model") if isinstance(chat_model, dict) else chat_model,
                  _find(trajectory, "generatorModel"), _find(trajectory, "planModel")]
    model = next((c for c in candidates if isinstance(c, str) and c), None)
    if model:
        _remember_model(endpoint, conversation, model)
    return model


def send(session: Dict[str, Any], wire_content: str) -> Optional[Dict[str, Any]]:
    """Inject `wire_content` as a user turn, whatever the target's status: an injection while it works
    is handled at its next step. None means "use the socket path": switched off, the session did not
    advertise a conversation (older agent-peer), no live endpoint, or any failure."""
    if os.environ.get(OFF_SWITCH_ENV) == "0":
        return None
    conversation = session.get(REGISTRY_FIELD)
    if not _valid_conversation(conversation):
        return None
    endpoint = _read_endpoint(conversation)
    if endpoint is None:
        note_fallback(conversation, "endpoint")
        return None
    started = time.time()
    stage = "model"
    try:
        model = _conversation_model(endpoint, conversation)
        if not model:
            note_fallback(conversation, "model", started=started)
            return None
        stage = "send"
        progress[threading.get_ident()] = "send"
        _rpc(endpoint, "SendUserCascadeMessage", {
            "cascadeId": conversation,
            "items": [{"text": wire_content}],
            "messageOrigin": "MESSAGE_ORIGIN_SDK_EXECUTABLE",
            "cascadeConfig": {"plannerConfig": {"requestedModel": {"model": model}}},
        }, _SEND_TIMEOUT)
    except Exception as exc:
        note_fallback(conversation, stage, exc, started)
        return None
    finally:
        progress.pop(threading.get_ident(), None)
    return {"transport": "agy-ls", "target": f"agy-ls:{conversation}",
            "elapsed_ms": round((time.time() - started) * 1000, 2)}
