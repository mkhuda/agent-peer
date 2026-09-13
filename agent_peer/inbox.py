import os
import json
import time
from typing import List, Dict, Any, Optional

from .protocol import INBOX_FILE, ensure_dirs

def append_inbox(record: Dict[str, Any]):
    ensure_dirs()
    record["received_at"] = time.time()
    record["received_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record["received_at"]))
    with open(INBOX_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

def read_inbox(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if not os.path.exists(INBOX_FILE):
        return []
    messages = []
    with open(INBOX_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except Exception:
                continue
    if limit:
        return messages[-limit:]
    return messages

def clear_inbox():
    if os.path.exists(INBOX_FILE):
        with open(INBOX_FILE, "w", encoding="utf-8") as f:
            f.write("")
