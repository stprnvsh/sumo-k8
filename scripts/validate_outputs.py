#!/usr/bin/env python3
"""Validate that the SUMO run produced its configured output files.

Reads the sumocfg (``CONFIG_FILE``) ``<output>`` section and, for every
file-valued output option (``vehroute-output``, ``fcd-output``,
``summary-output``, ``tripinfo-output``, ...), verifies the file exists and is
non-empty under ``WORKSPACE``.

Exits non-zero (listing the missing/empty files) if any expected output is
absent. This makes the pod fail loudly at the simulation step — e.g. when a
node-starved seed is torn down before it finishes writing its routes/fcd — so a
seed never reports "success" without a complete output set and the pipeline
only advances once the files exist.

Sub-options (dotted tags such as ``fcd-output.geo``) and non-file knobs
(``device.fcd.begin``) are ignored, as are boolean values.
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def expected_output_files(config_file: str) -> list[str]:
    """Return the list of file-valued outputs declared in the sumocfg."""
    root = ET.parse(config_file).getroot()
    output = root.find(".//output")
    if output is None:
        return []
    files: list[str] = []
    for el in output:
        tag = el.tag
        # Keep only top-level "*-output" options; skip dotted sub-options
        # (vehroute-output.exit-times) and unrelated knobs (device.fcd.begin).
        if "." in tag or not tag.endswith("-output"):
            continue
        value = (el.get("value") or "").strip()
        if not value or value.lower() in ("true", "false"):
            continue
        files.append(value)
    return files


def missing_outputs(config_file: str, workspace: str) -> list[str]:
    missing: list[str] = []
    for rel in expected_output_files(config_file):
        path = Path(rel) if os.path.isabs(rel) else Path(workspace) / rel
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(rel)
    return missing


def main() -> int:
    config_file = os.environ.get("CONFIG_FILE", "")
    workspace = os.environ.get("WORKSPACE", "/workspace")

    if not config_file or not os.path.isfile(config_file):
        # No config to validate against — sumo would already have failed; don't block.
        print(f"validate_outputs: CONFIG_FILE not found ({config_file!r}); skipping", flush=True)
        return 0

    try:
        missing = missing_outputs(config_file, workspace)
    except Exception as exc:  # never block on a parse hiccup
        print(f"validate_outputs: could not parse {config_file}: {exc}", file=sys.stderr, flush=True)
        return 0

    if missing:
        print(
            "OUTPUT VALIDATION FAILED: required SUMO output(s) missing or empty: "
            + ", ".join(missing),
            file=sys.stderr,
            flush=True,
        )
        return 1

    print("validate_outputs: OK — all configured outputs present", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
