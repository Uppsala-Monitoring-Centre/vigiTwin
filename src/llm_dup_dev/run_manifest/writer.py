"""Serialise a :class:`RunManifest` to disk as YAML and/or JSON."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml

from llm_dup_dev.run_manifest.models import RunManifest

YAML_FILENAME = "run_manifest.yaml"
JSON_FILENAME = "run_manifest.json"

_SUPPORTED_FORMATS = frozenset({"yaml", "json"})


def write_run_manifest(
    output_dir: str | Path,
    manifest: RunManifest,
    formats: Iterable[str] = ("yaml",),
) -> dict[str, Path]:
    """Write ``manifest`` into ``output_dir`` as the requested formats.

    Creates ``output_dir`` (and any missing parents) if it does not exist.
    Always supports ``"yaml"`` and ``"json"``; any other format raises
    ``ValueError``. Returns a mapping from format name to the path written.
    """
    requested = list(formats)
    unknown = [f for f in requested if f not in _SUPPORTED_FORMATS]
    if unknown:
        raise ValueError(
            f"Unsupported run manifest format(s): {unknown}. "
            f"Supported: {sorted(_SUPPORTED_FORMATS)}."
        )
    if not requested:
        raise ValueError("At least one format must be requested.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}

    if "yaml" in requested:
        yaml_path = out_dir / YAML_FILENAME
        # ``mode="json"`` makes datetime / Path round-trip as plain strings.
        payload = manifest.model_dump(mode="json")
        yaml_path.write_text(
            yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        written["yaml"] = yaml_path

    if "json" in requested:
        json_path = out_dir / JSON_FILENAME
        json_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        written["json"] = json_path

    return written
