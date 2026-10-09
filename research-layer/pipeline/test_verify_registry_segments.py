"""verify_registry.py reads a registry_log.d directory as its joined
segments, after checking the manifest (segments design s5)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import chain_mirror as cm
from .registry import Registry

LAYER = Path(__file__).resolve().parent.parent


def _verify(path):
    return subprocess.run([sys.executable, str(LAYER / "verify_registry.py"), str(path)],
                          cwd=str(LAYER), capture_output=True, text=True)


def _layer(tmp_path, n=30):
    reg = Registry(tmp_path / "registry_log.jsonl")
    for i in range(n):
        reg.append("note", {"text": f"segment test {i:04d}"})
    (tmp_path / "logs").mkdir()
    line = len(cm.split_lines(cm.read_live(reg.log_path))[0])
    assert cm.sync(reg.log_path, tmp_path / "registry_log.d",
                   logs_dir=tmp_path / "logs", max_bytes=line * 7).ok
    return reg.log_path, tmp_path / "registry_log.d"


def _entries_line(out):
    return next(ln for ln in out.splitlines() if ln.strip().startswith("Entries"))


def test_directory_and_file_modes_agree_on_a_good_mirror(tmp_path):
    f, d = _layer(tmp_path)
    rf, rd = _verify(f), _verify(d)
    assert rf.returncode == 0 and rd.returncode == 0, rd.stdout
    assert _entries_line(rf.stdout) == _entries_line(rd.stdout)
    assert "REGISTRY VALID" in rd.stdout


def test_a_flipped_byte_in_a_sealed_segment_fails(tmp_path):
    _, d = _layer(tmp_path)
    data = bytearray((d / "000001.jsonl").read_bytes())
    data[data.index(b"segment test 0002") + 13] = ord("9")
    (d / "000001.jsonl").write_bytes(bytes(data))
    r = _verify(d)
    assert r.returncode == 1 and "SEGMENTS:" in r.stdout


def test_a_missing_segment_fails(tmp_path):
    _, d = _layer(tmp_path)
    (d / "000002.jsonl").unlink()
    r = _verify(d)
    assert r.returncode == 1 and "BROKEN CHAIN" in r.stdout


def test_an_extra_active_segment_fails(tmp_path):
    _, d = _layer(tmp_path)
    n = len(cm.load_manifest(d)["sealed"])
    (d / cm.segment_name(n + 2)).write_bytes(b"")
    r = _verify(d)
    assert r.returncode == 1 and "segment files" in r.stdout


def test_a_directory_without_a_manifest_fails(tmp_path):
    _, d = _layer(tmp_path)
    (d / cm.MANIFEST).unlink()
    r = _verify(d)
    assert r.returncode != 0


def _clone_with_only_the_mirror(tmp_path):
    """A public clone after the switch: registry_log.d, no registry_log.jsonl."""
    _, d = _layer(tmp_path / "src")
    clone = tmp_path / "clone"
    clone.mkdir()
    shutil.copytree(d, clone / "registry_log.d")
    return clone


def test_no_path_argument_falls_back_to_the_mirror_when_the_live_file_is_absent(tmp_path):
    clone = _clone_with_only_the_mirror(tmp_path)
    r = subprocess.run([sys.executable, str(LAYER / "verify_registry.py")],
                       cwd=str(clone), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Verifying registry at registry_log.d" in r.stdout
    assert "REGISTRY VALID" in r.stdout


def test_an_explicit_path_is_never_second_guessed(tmp_path):
    clone = _clone_with_only_the_mirror(tmp_path)
    r = subprocess.run([sys.executable, str(LAYER / "verify_registry.py"), "registry_log.jsonl"],
                       cwd=str(clone), capture_output=True, text=True)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Verifying registry at registry_log.jsonl" in r.stdout


def test_no_path_argument_prefers_the_live_file_when_it_exists(tmp_path):
    f, _ = _layer(tmp_path)
    r = subprocess.run([sys.executable, str(LAYER / "verify_registry.py")],
                       cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Verifying registry at registry_log.jsonl" in r.stdout
