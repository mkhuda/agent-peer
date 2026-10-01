"""Harness-specific native delivery, one module per agent type.

Shared code asks `get_native(agent_type)` and treats None as "nothing special", so a
harness without a module here runs exactly the generic socket path.
"""
import importlib
from typing import Any, Dict, Mapping, Optional

_MODULES = {"AGY": "agy_ls"}


def get_native(agent_type: Optional[str]):
    name = _MODULES.get(str(agent_type or "").upper())
    if not name:
        return None
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except Exception:
        return None


def session_name_from_env(env: Mapping[str, str]) -> Optional[str]:
    """Name this conversation had before, found through a native module's stable conversation id:
    a live registration, else the most recent one, else the remembered name (unless another live
    session holds it now)."""
    for agent_type in _MODULES:
        module = get_native(agent_type)
        conversation = module.identity(env) if module else None
        if not conversation:
            continue
        try:
            from ..registry import get_active_sessions

            sessions = list(get_active_sessions())
        except Exception:
            return None
        matches = [s for s in sessions if s.get(module.REGISTRY_FIELD) == conversation and s.get("name")]
        if matches:
            live = [s for s in matches if s.get("alive")]
            return max(live or matches, key=lambda s: float(s.get("startedAt") or 0))["name"]
        remembered = module.remembered_name(env)
        if remembered and not any(s.get("alive") and s.get("name") == remembered for s in sessions):
            return remembered
    return None
