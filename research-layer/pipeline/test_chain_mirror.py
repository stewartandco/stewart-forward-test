"""chain_mirror: git-only segments of registry_log.jsonl
(docs/2026-10-09-registry-segments-design.md)."""
from __future__ import annotations

import pytest

from . import chain_mirror as cm


def test_constants_match_the_spec():
    assert cm.SEGMENT_MAX_BYTES == 40 * 1024 * 1024
    assert cm.FORMAT == "registry-segments-v1"
    assert cm.MANIFEST == "MANIFEST.json"
    assert cm.segment_name(1) == "000001.jsonl"
    assert cm.segment_name(123) == "000123.jsonl"


def test_read_live_normalises_crlf_and_drops_a_partial_tail(tmp_path):
    p = tmp_path / "registry_log.jsonl"
    p.write_bytes(b'{"a":1}\r\n{"b":2}\r\n{"c":')       # writer mid-append
    assert cm.read_live(p) == b'{"a":1}\n{"b":2}\n'


def test_read_live_of_lf_only_and_empty_files(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_bytes(b'{"a":1}\n')
    assert cm.read_live(p) == b'{"a":1}\n'
    p.write_bytes(b"")
    assert cm.read_live(p) == b""


def test_split_lines_keeps_each_lf():
    assert cm.split_lines(b"a\nbb\n") == [b"a\n", b"bb\n"]
    assert cm.split_lines(b"") == []


def test_plan_pieces_seals_only_when_the_next_line_would_overflow():
    lines = [b"aaaa\n"] * 5                     # 5 bytes each
    assert cm.plan_pieces(lines, 0, 10) == [(0, 2), (2, 4), (4, 5)]
    assert cm.plan_pieces(lines, 0, 25) == [(0, 5)]      # all fits: active only
    assert cm.plan_pieces(lines, 4, 10) == [(4, 5)]      # starts after sealed lines


def test_plan_pieces_does_not_seal_a_full_remainder_until_the_next_line():
    lines = [b"aaaa\n"] * 4
    # 2 lines fill a 10-byte segment exactly; nothing is due after the last
    # full piece until a line arrives, so the last piece is the remainder.
    assert cm.plan_pieces(lines, 0, 10) == [(0, 2), (2, 4)]
    assert cm.plan_pieces([], 0, 10) == [(0, 0)]


def test_plan_pieces_refuses_an_entry_larger_than_a_segment():
    with pytest.raises(cm.MirrorRefused, match="line 2"):
        cm.plan_pieces([b"a\n", b"x" * 20 + b"\n"], 0, 10)


import json
import subprocess
import sys
import threading
from pathlib import Path

from .common import entry_hash
from .registry import Registry

LAYER = Path(__file__).resolve().parent.parent


def _chain(tmp_path, n, text="note"):
    """A real chain written by Registry (text mode: CRLF on Windows)."""
    reg = Registry(tmp_path / "registry_log.jsonl")
    for i in range(n):
        reg.append("note", {"text": f"{text} {i:04d}"})
    return reg.log_path


def _setup(tmp_path, n=10):
    p = _chain(tmp_path, n)
    (tmp_path / "logs").mkdir(exist_ok=True)
    return p, tmp_path / "registry_log.d", tmp_path / "logs"


def _ledger(logs):
    return json.loads((logs / cm.LEDGER).read_text(encoding="utf-8"))


def test_first_sync_writes_one_active_segment_and_a_manifest(tmp_path):
    reg, md, logs = _setup(tmp_path)
    r = cm.sync(reg, md, logs_dir=logs)
    assert r.ok and r.sealed_new == [] and r.active == "000001.jsonl"
    assert (md / "000001.jsonl").read_bytes() == cm.read_live(reg)
    man = json.loads((md / cm.MANIFEST).read_text(encoding="utf-8"))
    assert man["format"] == cm.FORMAT and man["sealed"] == []
    assert man["segment_max_bytes"] == cm.SEGMENT_MAX_BYTES
    assert _ledger(logs) | {"ts_utc": None} == {"writer": "commit", "ts_utc": None, "items": []}


def test_sync_across_seal_boundaries_joins_back_to_the_live_file(tmp_path):
    reg, md, logs = _setup(tmp_path, 30)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert r.ok and r.sealed_new == [cm.segment_name(k) for k in range(1, 8)]
    assert cm.joined(md) == cm.read_live(reg)
    man = cm.load_manifest(md)
    assert [s["first_line"] for s in man["sealed"]] == [1, 5, 9, 13, 17, 21, 25]
    assert man["sealed"][0]["first_prev_entry_hash"] == "0" * 64
    lines = cm.split_lines(cm.read_live(reg))
    assert man["sealed"][-1]["last_entry_hash"] == entry_hash(json.loads(lines[27]))
    assert cm.check(reg, md) == "MATCH"


def test_a_sealed_segment_is_never_rewritten(tmp_path):
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    sealed = (md / "000001.jsonl").stat().st_mtime_ns
    Registry(reg).append("note", {"text": "later"})
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert r.ok and (md / "000001.jsonl").stat().st_mtime_ns == sealed
    assert cm.check(reg, md) == "MATCH"


def test_the_manifests_max_bytes_wins_over_the_argument(tmp_path):
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    r = cm.sync(reg, md, logs_dir=logs)                  # default max ignored
    assert r.ok and cm.load_manifest(md)["segment_max_bytes"] == line * 4


def test_exact_boundary_then_next_entry_seals_and_starts_an_active_segment(tmp_path):
    reg, md, logs = _setup(tmp_path, 4)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 2)   # (0,2) sealed, (2,4) active and full
    assert [s["file"] for s in cm.load_manifest(md)["sealed"]] == ["000001.jsonl"]
    Registry(reg).append("note", {"text": "next 0000"})
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 2)
    assert r.sealed_new == ["000002.jsonl"] and r.active == "000003.jsonl"
    assert cm.check(reg, md) == "MATCH"


def test_a_rewritten_sealed_range_is_refused_and_recorded(tmp_path):
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    before = {p.name: p.read_bytes() for p in md.iterdir()}
    data = reg.read_bytes()
    reg.write_bytes(data.replace(b"note 0001", b"note 9999"))
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert not r.ok and "prefix mismatch at segment 000001.jsonl" in r.reason
    assert {p.name: p.read_bytes() for p in md.iterdir()} == before   # nothing written
    (item,) = _ledger(logs)["items"]
    assert (item["source"], item["key"]) == ("chain_mirror", "registry_log.d")


def test_a_truncated_live_file_is_refused(tmp_path):
    reg, md, logs = _setup(tmp_path, 6)
    cm.sync(reg, md, logs_dir=logs)
    data = reg.read_bytes()
    reg.write_bytes(data[: data.find(b"\n") + 1])         # keep one line
    r = cm.sync(reg, md, logs_dir=logs)
    assert not r.ok and "not a prefix" in r.reason


def test_a_sealed_file_edited_or_deleted_on_disk_is_refused(tmp_path):
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    (md / "000001.jsonl").write_bytes(b"tampered\n")
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert not r.ok and "000001.jsonl on disk" in r.reason
    (md / "000001.jsonl").unlink()
    assert not cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4).ok


def test_a_crash_between_segment_and_manifest_writes_repairs_itself(tmp_path, monkeypatch):
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    real = cm._write

    def crash_on_manifest(path, data):
        if path.name == cm.MANIFEST:
            raise OSError("killed")
        real(path, data)
    monkeypatch.setattr(cm, "_write", crash_on_manifest)
    assert not cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4).ok
    monkeypatch.setattr(cm, "_write", real)
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert r.ok and cm.check(reg, md) == "MATCH"
    assert _ledger(logs)["items"] == []                   # recovered, item dropped


def test_leftover_tmp_files_and_stale_segments_are_removed(tmp_path):
    reg, md, logs = _setup(tmp_path, 4)
    md.mkdir()
    (md / "000001.jsonl.tmp").write_bytes(b"half")
    (md / "000009.jsonl").write_bytes(b"stray\n")
    assert cm.sync(reg, md, logs_dir=logs).ok
    assert sorted(p.name for p in md.iterdir()) == ["000001.jsonl", cm.MANIFEST]


def test_an_oversized_entry_is_refused(tmp_path):
    reg, md, logs = _setup(tmp_path, 2)
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=10)
    assert not r.ok and "larger than segment_max_bytes" in r.reason


def test_two_concurrent_syncs_serialise_on_the_lock(tmp_path, capsys):
    reg, md, logs = _setup(tmp_path, 40)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    results = []
    ts = [threading.Thread(target=lambda: results.append(
        cm.sync(reg, md, logs_dir=logs, max_bytes=line * 3))) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert all(r.ok for r in results)
    assert cm.check(reg, md) == "MATCH"
    assert not (tmp_path / "registry_log.d.lock").exists()
    led = _ledger(logs)                                   # written under the lock, intact
    assert led["writer"] == "commit" and led["items"] == []
    assert "ledger not written" not in capsys.readouterr().out


def test_a_held_lock_times_out_as_a_refusal_not_an_exception(tmp_path, monkeypatch):
    reg, md, logs = _setup(tmp_path, 2)
    md.mkdir()
    (tmp_path / "registry_log.d.lock").write_text("1 0", encoding="utf-8")
    monkeypatch.setattr(cm, "LOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(cm, "LOCK_STALE_S", 3600.0)
    r = cm.sync(reg, md, logs_dir=logs)
    assert not r.ok and "lock busy" in r.reason


def test_check_reports_behind_and_mismatch(tmp_path):
    reg, md, logs = _setup(tmp_path, 3)
    cm.sync(reg, md, logs_dir=logs)
    Registry(reg).append("note", {"text": "after"})
    assert cm.check(reg, md) == "BEHIND"
    (md / "000001.jsonl").write_bytes(b"x\n")
    assert cm.check(reg, md) == "MISMATCH"


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "pipeline.chain_mirror", *args],
                          cwd=str(LAYER), capture_output=True, text=True)


def test_cli_sync_and_check_exit_codes(tmp_path):
    reg, md, logs = _setup(tmp_path, 3)
    base = ["--registry", str(reg), "--mirror-dir", str(md), "--logs-dir", str(logs)]
    assert _cli("check", *base).returncode == 1           # nothing mirrored yet
    assert _cli("sync", *base).returncode == 0
    assert _cli("check", *base).returncode == 0
    (md / cm.MANIFEST).write_text('{"format": "other"}', encoding="utf-8")
    r = _cli("sync", *base)
    assert r.returncode == cm.EXIT_REFUSED == 3 and "refused" in r.stdout


def test_cli_defaults_point_at_the_layer():
    ns = cm.build_parser().parse_args(["sync"])
    assert ns.registry == LAYER / "registry_log.jsonl"
    assert ns.mirror_dir == LAYER / "registry_log.d"
    assert ns.logs_dir == LAYER / "logs"


def test_a_sync_that_lost_a_stale_lock_race_refuses_instead_of_overwriting(tmp_path, monkeypatch):
    """Sync A passed its checks on a SHORT live file, then B (which broke A's
    stale lock) ran a complete sync on the LONGER one and sealed 000001. A must
    not write over it (finding 1, reproduction by the task reviewer)."""
    reg, md, logs = _setup(tmp_path, 1)
    short = tmp_path / "short.jsonl"
    short.write_bytes(reg.read_bytes())                   # A's read: one line
    for k in (1, 2):
        Registry(reg).append("note", {"text": f"n {k:04d}"})
    line = len(cm.split_lines(cm.read_live(reg))[0])
    mx = line * 2
    real_plan = cm.plan_pieces
    inner = []

    def hooked(lines, start, max_bytes):
        monkeypatch.setattr(cm, "plan_pieces", real_plan)
        inner.append(cm._sync_locked(reg, md, mx))        # B runs while A is paused
        return real_plan(lines, start, max_bytes)
    monkeypatch.setattr(cm, "plan_pieces", hooked)
    r = cm.sync(short, md, logs_dir=logs, max_bytes=mx)
    assert inner and inner[0].ok and inner[0].sealed_new == ["000001.jsonl"]
    assert not r.ok and "manifest changed during sync" in r.reason
    man = cm.load_manifest(md)
    assert [s["file"] for s in man["sealed"]] == ["000001.jsonl"]
    for rec in man["sealed"]:
        assert cm._sha((md / rec["file"]).read_bytes()) == rec["sha256"]
    assert (md / "000002.jsonl").is_file()                # B's active segment survived
    assert cm.sync(reg, md, logs_dir=logs).ok
    assert cm.check(reg, md) == "MATCH"


def test_check_flags_a_sealed_segment_that_differs_or_is_missing(tmp_path):
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert cm.check(reg, md) == "MATCH"
    good = (md / "000001.jsonl").read_bytes()
    altered = bytearray(good)
    altered[10] = (altered[10] + 1) % 256                 # same length, other content
    (md / "000001.jsonl").write_bytes(bytes(altered))
    assert cm.check(reg, md) == "MISMATCH"
    (md / "000001.jsonl").write_bytes(good)
    assert cm.check(reg, md) == "MATCH"
    (md / "000001.jsonl").unlink()
    assert cm.check(reg, md) == "MISMATCH"
    # with the active segment gone too, what is left is an empty (prefix) join:
    # without the manifest comparison that would read BEHIND
    (md / "000002.jsonl").unlink()
    assert cm.check(reg, md) == "MISMATCH"


def _mirrored(tmp_path, n=12, lines_per_seg=4):
    reg, md, logs = _setup(tmp_path, n)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    assert cm.sync(reg, md, logs_dir=logs, max_bytes=line * lines_per_seg).ok
    return reg, md


def test_layout_of_a_good_mirror_is_clean(tmp_path):
    reg, md = _mirrored(tmp_path)
    assert cm.check_layout(md) == []
    assert "".join(cm.iter_lines(md)).encode("utf-8") == cm.read_live(reg)


def test_layout_accepts_an_empty_active_segment(tmp_path):
    reg, md, logs = _setup(tmp_path, 0)
    reg.write_bytes(b"")
    assert cm.sync(reg, md, logs_dir=logs).ok
    assert (md / "000001.jsonl").read_bytes() == b""
    assert cm.check_layout(md) == []


def test_layout_flags_a_flipped_byte_a_missing_file_and_an_extra_file(tmp_path):
    _, md = _mirrored(tmp_path)
    data = bytearray((md / "000001.jsonl").read_bytes())
    data[5] ^= 1
    (md / "000001.jsonl").write_bytes(bytes(data))
    assert any("000001.jsonl: bytes/sha256" in p for p in cm.check_layout(md))
    (md / "000002.jsonl").unlink()
    assert any("missing" in p for p in cm.check_layout(md))
    (md / "000099.jsonl").write_bytes(b"")
    assert any("segment files" in p for p in cm.check_layout(md))


def test_layout_flags_cr_and_a_missing_manifest(tmp_path):
    _, md = _mirrored(tmp_path)
    act = md / cm.segment_name(len(cm.load_manifest(md)["sealed"]) + 1)
    act.write_bytes(act.read_bytes().replace(b"\n", b"\r\n"))
    assert any("CR" in p for p in cm.check_layout(md))
    (md / cm.MANIFEST).unlink()
    assert cm.check_layout(md) == [f"{cm.MANIFEST} missing"]


def test_layout_flags_a_broken_cross_segment_link(tmp_path):
    _, md = _mirrored(tmp_path)
    man = json.loads((md / cm.MANIFEST).read_text(encoding="utf-8"))
    man["sealed"][0]["last_entry_hash"] = "f" * 64
    (md / cm.MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    probs = cm.check_layout(md)
    assert any("last_entry_hash differs" in p for p in probs)
    assert any("does not link" in p for p in probs)


def test_a_stray_tmp_that_no_write_would_replace_is_removed(tmp_path):
    """The leftover in the test above shares its name with the file the sync
    writes anyway, so os.replace consumes it; this one is only gone if the
    cleanup loop really runs."""
    reg, md, logs = _setup(tmp_path, 4)
    md.mkdir()
    (md / "000007.jsonl.tmp").write_bytes(b"half")
    assert cm.sync(reg, md, logs_dir=logs).ok
    assert sorted(p.name for p in md.iterdir()) == ["000001.jsonl", cm.MANIFEST]


def test_a_crash_before_the_first_segment_write_leaves_no_manifest_claiming_it(tmp_path, monkeypatch):
    """Segment files first, manifest last: a kill in between must leave a
    manifest that names no file that does not exist, or the next sync would
    refuse forever on a sealed segment missing from disk."""
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    real = cm._write

    def crash_on_first_segment(path, data):
        if path.name == "000001.jsonl":
            raise OSError("killed")
        real(path, data)
    monkeypatch.setattr(cm, "_write", crash_on_first_segment)
    assert not cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4).ok
    assert not (md / cm.MANIFEST).exists()
    monkeypatch.setattr(cm, "_write", real)
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert r.ok and cm.check(reg, md) == "MATCH"


def test_check_flags_a_sealed_range_rewritten_identically_in_live_and_segment(tmp_path):
    """Live file and mirror still join to equal bytes, so only the per-record
    comparison against the manifest can see that a sealed range changed."""
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4)
    assert cm.check(reg, md) == "MATCH"
    reg.write_bytes(reg.read_bytes().replace(b"note 0001", b"note 9999"))
    seg = md / "000001.jsonl"
    seg.write_bytes(seg.read_bytes().replace(b"note 0001", b"note 9999"))
    assert cm.read_live(reg) == cm.joined(md)
    assert cm.check(reg, md) == "MISMATCH"


def test_hard_ceiling_is_50_mib_above_the_segment_size():
    assert cm.HARD_MAX_BYTES == 50 * 1024 * 1024
    assert cm.SEGMENT_MAX_BYTES < cm.HARD_MAX_BYTES


def test_sync_refuses_a_hand_raised_manifest_max_over_the_hard_ceiling(tmp_path):
    """A hand-edited segment_max_bytes must never let sync write a segment
    of any size (final review 2026-10-09)."""
    reg, md, logs = _setup(tmp_path, 8)
    line = len(cm.split_lines(cm.read_live(reg))[0])
    assert cm.sync(reg, md, logs_dir=logs, max_bytes=line * 4).ok
    man = json.loads((md / cm.MANIFEST).read_text(encoding="utf-8"))
    man["segment_max_bytes"] = cm.HARD_MAX_BYTES + 1
    (md / cm.MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in md.iterdir()}
    Registry(reg).append("note", {"text": "later"})
    r = cm.sync(reg, md, logs_dir=logs)
    assert not r.ok and "hard ceiling" in r.reason
    assert {p.name: p.read_bytes() for p in md.iterdir()} == before
    assert [i["source"] for i in _ledger(logs)["items"]] == ["chain_mirror"]


def test_sync_refuses_a_fresh_mirror_asked_for_segments_over_the_hard_ceiling(tmp_path):
    reg, md, logs = _setup(tmp_path, 3)
    r = cm.sync(reg, md, logs_dir=logs, max_bytes=cm.HARD_MAX_BYTES + 1)
    assert not r.ok and "hard ceiling" in r.reason
    assert not (md / cm.MANIFEST).exists()
    assert cm.sync(reg, md, logs_dir=logs, max_bytes=cm.HARD_MAX_BYTES).ok


def test_layout_flags_segments_and_a_manifest_max_over_the_hard_ceiling(tmp_path, monkeypatch):
    """The ceiling holds whatever the manifest says: a segment over it is a
    problem even when the manifest's own segment_max_bytes allows it."""
    _, md = _mirrored(tmp_path)                      # 4-line segments
    line = len(cm.split_lines((md / "000001.jsonl").read_bytes())[0])
    man = json.loads((md / cm.MANIFEST).read_text(encoding="utf-8"))
    man["segment_max_bytes"] = 10 ** 12
    (md / cm.MANIFEST).write_text(json.dumps(man), encoding="utf-8")
    assert cm.check_layout(md) == [f"{cm.MANIFEST}: segment_max_bytes {10 ** 12} is over "
                                   f"the {cm.HARD_MAX_BYTES}-byte hard ceiling"]
    monkeypatch.setattr(cm, "HARD_MAX_BYTES", line * 2)
    probs = cm.check_layout(md)
    assert any(p.startswith(f"{cm.MANIFEST}: segment_max_bytes") for p in probs)
    for k in (1, 2, 3):                              # two sealed + the active one
        assert any(p.startswith(f"{cm.segment_name(k)}: ") and "over the" in p
                   for p in probs), probs
