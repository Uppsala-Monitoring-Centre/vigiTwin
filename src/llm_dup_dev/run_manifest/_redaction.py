"""Best-effort redaction of secret-looking keys in user-supplied dicts."""

from __future__ import annotations

import warnings
from typing import Any

REDACTED = "***REDACTED***"

SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "bearer",
    "authorization",
    "client_secret",
    "connection_string",
)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in SECRET_KEY_PATTERNS)


def redact_secrets(
    payload: dict[str, Any] | None,
    *,
    field_name: str,
) -> dict[str, Any] | None:
    """Walk ``payload`` and replace values for secret-looking keys with REDACTED.

    Recurses into nested dicts and into dicts contained in lists/tuples. Emits
    a single ``UserWarning`` per call if any key was redacted, naming the
    field so callers can find and remove the leak at the source.
    """
    if payload is None:
        return None

    redacted_keys: list[str] = []

    def _walk(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                key_path = f"{path}.{k}" if path else k
                if isinstance(k, str) and _is_secret_key(k):
                    out[k] = REDACTED
                    redacted_keys.append(key_path)
                else:
                    out[k] = _walk(v, key_path)
            return out
        if isinstance(node, list):
            return [_walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        if isinstance(node, tuple):
            return [_walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return node

    cleaned = _walk(payload, "")

    if redacted_keys:
        warnings.warn(
            f"run_manifest: redacted secret-looking key(s) in {field_name}: "
            f"{', '.join(redacted_keys)}. Remove these at the call site — "
            "manifests must not contain secrets.",
            UserWarning,
            stacklevel=3,
        )

    return cleaned
