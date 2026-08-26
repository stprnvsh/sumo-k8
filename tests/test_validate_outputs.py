"""Tests for scripts/validate_outputs.py — the post-sumo output gate.

Requirement: after `sumo` runs, every file-valued output declared in the
sumocfg <output> section (vehroute-output, fcd-output, summary-output, ...)
must exist and be non-empty in the workspace. If any is missing/empty the
validator exits non-zero so the pod fails loudly (instead of reporting success
and 404-ing at the next pipeline step). Sub-options (e.g. fcd-output.geo) and
non-file knobs (device.fcd.begin) must be ignored.
"""
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_outputs.py"


def _write_cfg(d: Path, outputs):
    lines = ["<configuration>", "  <output>"]
    for tag, val in outputs:
        lines.append(f'    <{tag} value="{val}"/>')
    lines += ["  </output>", "</configuration>"]
    cfg = d / "sim.sumocfg"
    cfg.write_text("\n".join(lines))
    return cfg


def _run(cfg: Path, workspace: Path):
    env = {**os.environ, "CONFIG_FILE": str(cfg), "WORKSPACE": str(workspace)}
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)


def test_all_outputs_present_passes(tmp_path):
    cfg = _write_cfg(tmp_path, [("vehroute-output", "routes.xml"), ("fcd-output", "fcd.parquet")])
    (tmp_path / "routes.xml").write_text("<routes/>")
    (tmp_path / "fcd.parquet").write_bytes(b"PAR1data")
    r = _run(cfg, tmp_path)
    assert r.returncode == 0, r.stderr


def test_missing_output_fails_and_names_it(tmp_path):
    cfg = _write_cfg(tmp_path, [("vehroute-output", "routes.xml"), ("fcd-output", "fcd.parquet")])
    (tmp_path / "fcd.parquet").write_bytes(b"PAR1data")  # routes.xml absent
    r = _run(cfg, tmp_path)
    assert r.returncode != 0
    assert "routes.xml" in (r.stdout + r.stderr)


def test_empty_output_fails(tmp_path):
    cfg = _write_cfg(tmp_path, [("vehroute-output", "routes.xml")])
    (tmp_path / "routes.xml").write_text("")  # zero bytes
    r = _run(cfg, tmp_path)
    assert r.returncode != 0


def test_suboptions_and_nonfile_knobs_ignored(tmp_path):
    cfg = _write_cfg(
        tmp_path,
        [
            ("vehroute-output", "routes.xml"),
            ("vehroute-output.exit-times", "true"),
            ("fcd-output.geo", "true"),
            ("summary-output.period", "900"),
            ("device.fcd.begin", "25200"),
        ],
    )
    (tmp_path / "routes.xml").write_text("<routes/>")
    r = _run(cfg, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_relative_dot_path_resolved(tmp_path):
    cfg = _write_cfg(tmp_path, [("summary-output", "./summary.out.xml")])
    (tmp_path / "summary.out.xml").write_text("<summary/>")
    r = _run(cfg, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_output_section_passes(tmp_path):
    cfg = tmp_path / "sim.sumocfg"
    cfg.write_text("<configuration><input/></configuration>")
    r = _run(cfg, tmp_path)
    assert r.returncode == 0


def test_missing_config_does_not_block(tmp_path):
    # sumo would already have failed; the gate must not hard-fail on a bad path.
    env = {**os.environ, "CONFIG_FILE": str(tmp_path / "nope.sumocfg"), "WORKSPACE": str(tmp_path)}
    r = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
    assert r.returncode == 0
