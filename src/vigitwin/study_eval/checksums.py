"""Content checksums for provenance.

The repo's manifest packages capture *where* an artifact came from (git sha,
versions, timestamps) but not a hash of its bytes. The study freeze needs a
verifiable chain — each stage's manifest references the previous stage's outputs
by content hash — so these helpers add sha256 over both raw files and canonical
model JSON.

Canonical JSON = ``model_dump(mode="json")`` re-serialised with sorted keys and
compact separators, so two logically equal models hash identically regardless
of field order or whitespace.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel


def sha256_bytes(data: bytes) -> str:
    """Return the hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Return the hex sha256 of a file's bytes, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(model: BaseModel) -> str:
    """Return the canonical JSON string for a pydantic model."""
    return json.dumps(
        model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )


def sha256_canonical_json(model: BaseModel) -> str:
    """Return the hex sha256 of a model's canonical JSON."""
    return sha256_bytes(canonical_json(model).encode("utf-8"))


__all__ = [
    "sha256_bytes",
    "sha256_file",
    "canonical_json",
    "sha256_canonical_json",
]
