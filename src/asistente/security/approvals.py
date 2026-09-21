import hashlib
import json
from typing import Any


def payload_hash(tool: str, args: dict[str, Any]) -> str:
    """Hash canónico de una acción. Una aprobación vale solo para este payload exacto."""
    canonical = json.dumps(
        {"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
