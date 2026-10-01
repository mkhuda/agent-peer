"""agy: deliver a message as a real user turn through its internal Cascade Language Server.

agy exports the server's address, CSRF token and conversation id into every shell it spawns,
so `agent-peer listen` run inside agy reads them from its own environment. The token is
kept only in a 0600 file under ~/.agent-peer/agy-ls/, never in the registry, a log or a message.
Everything here fails safe: `send` returns None whenever the generic socket path should be used.
"""
import json
import os
import re
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


def _rpc(endpoint: Dict[str, Any], method: str, body: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    request = urllib.request.Request(
        f"http://{endpoint['addr']}/{_SERVICE}/{method}", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": endpoint["token"]})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def _conversation_model(endpoint: Dict[str, Any], conversation: str) -> Optional[str]:
    """The conversation's own model enum; its names change per agy version, so never hardcoded."""
    trajectory = _rpc(endpoint, "GetCascadeTrajectory", {"cascadeId": conversation})
    chat_model = _find(trajectory, "chatModel")
    candidates = [chat_model.get("model") if isinstance(chat_model, dict) else chat_model,
                  _find(trajectory, "generatorModel"), _find(trajectory, "planModel")]
    return next((c for c in candidates if isinstance(c, str) and c), None)


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
        return None
    started = time.time()
    try:
        model = _conversation_model(endpoint, conversation)
        if not model:
            return None
        _rpc(endpoint, "SendUserCascadeMessage", {
            "cascadeId": conversation,
            "items": [{"text": wire_content}],
            "messageOrigin": "MESSAGE_ORIGIN_SDK_EXECUTABLE",
            "cascadeConfig": {"plannerConfig": {"requestedModel": {"model": model}}},
        })
    except Exception:
        return None
    return {"transport": "agy-ls", "target": f"agy-ls:{conversation}",
            "elapsed_ms": round((time.time() - started) * 1000, 2)}
