from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

CODEC = "json+gzip"
SCHEMA_VERSION = 1


def encode_payload(payload: dict[str, Any]) -> tuple[bytes, str]:
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return gzip.compress(raw, mtime=0), hashlib.sha256(raw).hexdigest()


def decode_payload(payload: bytes, codec: str) -> dict[str, Any]:
    if codec != CODEC:
        raise ValueError(f"unsupported lookup payload codec: {codec}")
    decoded = json.loads(gzip.decompress(payload).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("lookup payload root must be an object")
    return decoded


def encode_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("lookup timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def decode_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored lookup timestamp must include timezone")
    return parsed.astimezone(timezone.utc)
