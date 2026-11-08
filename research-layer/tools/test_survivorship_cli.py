"""Tests for tools.survivorship (the study CLI and its order gate).

All offline: tmp_path study dirs, a tiny fixture registry COPY, synthetic
monthly zips, a fake fetch, and a fixture pipeline-data dir. Never the live
chain, never research-layer/data/.
"""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import math
import shutil
import os
import sys
import urllib.error
import urllib.parse
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.common import content_id
from tools import survivorship as cli
from tools import survivorship_archive as sa
from tools import survivorship_harness as h
from tools import survivorship_templates as st
from tools import survivorship_universe as su
from tools.survivorship_archive import write_pipeline_csv

NOW = "2026-11-07T10:00+08:00"
START, STOP = date(2022, 6, 1), date(2024, 12, 31)
DESIGN = Path(cli.__file__).resolve().parent.parent / cli.DESIGN_REL


# ---------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _test_clock(monkeypatch):
    # --now is a test-only clock (final review M-b)
    monkeypatch.setenv("SURVIVORSHIP_TEST_CLOCK", "1")


def _bars(start, stop, seed=0.0, skip=()):
    out, i, d = [], 0, start
    while d <= stop:
        if d not in skip:
            c = round(100.0 + 20.0 * math.sin(i / 15.0 + seed) + i * 0.05, 6)
            out.append({"date": d.strftime("%Y-%m-%d 00:00:00"),
                        "open": round(c - 0.3, 6), "high": round(c + 1.0, 6),
                        "low": round(c - 1.0, 6), "close": c, "volume": 1000.0})
        d += timedelta(days=1)
        i += 1
    return out


def _zip_bytes(sym, tf, month, bars):
    lines = []
    for b in bars:
        t = int(datetime.strptime(b["date"], "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=timezone.utc).timestamp() * 1000)
        if month >= "2025-01":
            t *= 1000                      # the archive's microsecond era
        lines.append(f"{t},{b['open']!r},{b['high']!r},{b['low']!r},"
                     f"{b['close']!r},{b['volume']!r},{t + 1},0,0,0,0,0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{sym}-{tf}-{month}.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def _by_month(bars):
    out = {}
    for b in bars:
        out.setdefault(b["date"][:7], []).append(b)
    return out


def _write_archive(study, sym, bars, tf="1d"):
    folder = study / "archive" / sym / tf
    folder.mkdir(parents=True, exist_ok=True)
    for month, mb in _by_month(bars).items():
        (folder / f"{sym}-{tf}-{month}.zip").write_bytes(_zip_bytes(sym, tf, month, mb))


def _write_plan(study, tf="1d"):
    """plan_<tf>.json listing every zip now in the study's archive (what
    plan-fetch would have planned for it)."""
    files = sorted((z.parent.parent.name, tf, z.stem[-7:])
                   for z in (study / "archive").glob(f"*/{tf}/*.zip"))
    (study / f"plan_{tf}.json").write_text(json.dumps({
        "tf": tf, "symbols": sorted({f[0] for f in files}), "files": [list(f) for f in files],
        "sizes": []}), encoding="utf-8")


def _spec(pair, fast=10, slow=30):
    s = {"version": 2, "name": f"t{fast}", "family": f"fam{fast}",
         "universe": {"assets": [pair], "asset_class": "crypto",
                      "timeframe": "1d", "session": "24x7"},
         "blocks": [
             {"role": "entry", "type": "ma_cross", "params": {"fast": fast, "slow": slow}},
             {"role": "stop", "type": "ma_stop", "params": {"ma_len": 20}},
             {"role": "risk", "type": "fixed_fraction", "params": {"f": 0.02}}],
         "cost_model": {"commission_per_side": 0.001, "slippage_ticks": 0.0005}}
    s["strategy_id"] = content_id(s, "strategy_id")
    return s


def _entry(etype, payload):
    return {"entry_type": etype, "payload": payload, "prev_entry_hash": "x",
            "ts_utc": "2026-01-01T00:00:00Z", "version": 1}


def _write_reg(path, entries):
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def _cmc_bytes(rows):
    data = [{"cmcRank": r, "symbol": s, "slug": s.lower(),
             "tags": ["stablecoin"] if stable else [],
             "quotes": [{"name": "USD", "price": 1.0 if stable else 5.0}]}
            for r, s, stable in rows]
    return json.dumps({"data": data}).encode("utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "RENAMES", {})
    monkeypatch.setattr(su, "DELISTED", {})
    monkeypatch.setattr(su, "CUTS", {})
    monkeypatch.setattr(su, "HALTS", {})
    monkeypatch.setattr(su, "EXCLUDED", {})
    monkeypatch.setattr(su, "END", STOP.isoformat())    # the fixture's common end
    # the fixture's own counts stand in for the design's 80/960 and strata
    monkeypatch.setattr(cli, "DESIGN_COUNTS", {
        "templates_1d": 2, "sids_1d": 3,
        "strata": {"S": 1, "R": 1, "F": 1, "N": 0, "D": 0}})
    study, pipe, layer = tmp_path / "study", tmp_path / "pipe", tmp_path / "layer"
    (layer / "docs").mkdir(parents=True)
    # the committed note the gate byte-compares with (the tool's own layer in a
    # real run; draft-note writes it there with --layer-dir here)
    monkeypatch.setattr(cli, "NOTE_FILE", layer / cli.NOTE_REL)
    shutil.copyfile(DESIGN, layer / cli.DESIGN_REL)
    series = {"AAAUSDT": _bars(START, STOP, 0.0), "BBBUSDT": _bars(START, STOP, 1.0),
              "CCCUSDT": _bars(START, STOP, 2.0)}
    for sym, bars in series.items():
        _write_archive(study, sym, bars)
    _write_plan(study)
    for sym in ("AAAUSDT", "BBBUSDT"):
        write_pipeline_csv(series[sym], pipe / f"{sym}_1d.csv")
    cmc = _cmc_bytes([(1, "AAA", False), (2, "CCC", False)])
    (study / cli.CMC_FILE).write_bytes(cmc)
    A, B = ["AAAUSDT", "BBBUSDT"], ["AAAUSDT", "CCCUSDT"]
    (study / "universe.json").write_text(json.dumps({
        "A": A, "B": B, "strata": su.strata(A, B), "excluded": [], "target": 2,
        "cmc_sha256": hashlib.sha256(cmc).hexdigest(),
        "declared_exclusions": {}}), encoding="utf-8")
    specs = [_spec("AAAUSDT"), _spec("BBBUSDT"), _spec("AAAUSDT", 5, 20)]
    entries = []
    for s in specs:
        r = h.train_job((s, str(pipe), "1d"))
        entries.append(_entry("strategy_registered", s))
        entries.append(_entry("verdict", {
            "strategy_id": s["strategy_id"], "stage": "screened",
            "verdict": "pass" if r["screen_pass"] else "fail",
            "metrics": {"trades": r["trades"], "net_pnl": r["net_pnl"]},
            "artifacts_hash": "h"}))
    reg = study / "registry_copy.jsonl"
    _write_reg(reg, entries)
    return SimpleNamespace(study=study, pipe=pipe, layer=layer, reg=reg, specs=specs,
                           entries=entries, series=series, tmp=tmp_path)


def _run(*argv, fetch=None):
    return cli.main([str(a) for a in argv], fetch=fetch)


def _base(env):
    return ["--study-dir", env.study, "--now", NOW]


def _preflight(env, *extra):
    return _run("preflight", *_base(env), "--registry", env.reg,
                "--pipeline-data-dir", env.pipe, "--workers", "1", *extra)


def _freeze(env, upto=6):
    return _run("freeze", *_base(env), "--registry", env.reg, "--upto", upto)


def _draft(env):
    return _run("draft-note", *_base(env), "--registry", env.reg, "--layer-dir", env.layer)


def _note_bytes(env):
    return (env.layer / cli.NOTE_REL).read_bytes()


def _frozen(env, upto=6):
    assert _preflight(env) == 0
    assert _freeze(env, upto) == 0
    assert _draft(env) == 0
    return _note_bytes(env).decode("utf-8")


def _chain(env, text):
    with env.reg.open("a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(_entry("note", {"text": text})) + "\n")


def _trap_engine(monkeypatch):
    """Every engine entry point raises and is recorded."""
    from pipeline import engine
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise AssertionError("engine called")

    real = engine.run_spec
    for mod in list(sys.modules.values()):
        try:
            if getattr(mod, "run_spec", None) is real:
                monkeypatch.setattr(mod, "run_spec", boom)
        except Exception:      # noqa: BLE001 - exotic module objects
            continue
    for name in ("train_job", "full_job", "gauntlet_job", "run_arm"):
        monkeypatch.setattr(h, name, boom)
    return calls


# ------------------------------------------------------------- the window

WINDOW_CMDS = {
    "fetch-cmc": lambda e: [],
    "fetch": lambda e: ["--tf", "1d"],
    "preflight": lambda e: ["--registry", e.reg, "--pipeline-data-dir", e.pipe],
    "run": lambda e: ["--registry", e.reg, "--tf", "1d"],
    "gauntlet": lambda e: ["--registry", e.reg, "--tf", "1d"],
}


@pytest.mark.parametrize("now", ["2026-11-07T20:05+08:00", "2026-11-07T08:00+08:00"])
@pytest.mark.parametrize("cmd", sorted(WINDOW_CMDS))
def test_refuses_start_outside_window(env, monkeypatch, cmd, now):
    calls = _trap_engine(monkeypatch)
    fetched = []
    (env.study / cli.CMC_FILE).unlink()
    (env.study / "plan_1d.json").write_text(json.dumps(
        {"tf": "1d", "symbols": [], "files": [], "sizes": []}), encoding="utf-8")
    rc = _run(cmd, "--study-dir", env.study, "--now", now, *WINDOW_CMDS[cmd](env),
              fetch=lambda url: fetched.append(url) or b"")
    assert rc == 2
    assert calls == [] and fetched == []
    assert not (env.study / cli.CMC_FILE).exists()


@pytest.mark.parametrize("cmd", ["run", "gauntlet"])
def test_a_noted_run_still_refuses_outside_the_window(env, monkeypatch, cmd):
    _chain(env, _frozen(env))
    calls = _trap_engine(monkeypatch)
    for now in ("2026-11-07T20:00+08:00", "2026-11-08T08:39+08:00"):
        assert _run(cmd, "--study-dir", env.study, "--now", now,
                    "--registry", env.reg) == 2
    assert calls == []


def test_window_edges():
    a = SimpleNamespace(now="2026-11-07T08:40+08:00")
    cli._require_window(a)
    a.now = "2026-11-07T19:59:59+08:00"
    cli._require_window(a)
    a.now = "2026-11-07T20:00+08:00"
    with pytest.raises(cli.Refused):
        cli._require_window(a)
    a.now = "2026-11-07T00:39+00:00"          # 08:39 Perth
    with pytest.raises(cli.Refused):
        cli._require_window(a)


# ------------------------------------------------------- fetch / universe

class FakeS3:
    def __init__(self, series, cmc, tf="1d"):
        self.cmc, self.tf, self.calls = cmc, tf, []
        self.by_name, self.listing = {}, {}
        for sym, bars in series.items():
            for month, mb in _by_month(bars).items():
                name = f"{sym}-{tf}-{month}.zip"
                self.by_name[name] = _zip_bytes(sym, tf, month, mb)
                self.listing.setdefault(sym, []).append(name)
        self.corrupt = set()

    def __call__(self, url):
        self.calls.append(url)
        if url == su.CMC_URL:
            return self.cmc
        if url.startswith(sa.S3_LIST):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            prefix = q["prefix"][0]
            sym = prefix.split("/")[4]
            items = "".join(
                f"<Contents><Key>{prefix}{n}</Key><Size>{len(self.by_name[n])}</Size></Contents>"
                f"<Contents><Key>{prefix}{n}.CHECKSUM</Key><Size>90</Size></Contents>"
                for n in sorted(self.listing.get(sym, [])))
            return (f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/'
                    f'doc/2006-03-01/"><IsTruncated>false</IsTruncated>{items}'
                    f'</ListBucketResult>').encode("utf-8")
        if url.startswith(sa.DL):
            name = url.rsplit("/", 1)[1]
            if name.endswith(".CHECKSUM"):
                z = name[:-len(".CHECKSUM")]
                digest = hashlib.sha256(self.by_name[z]).hexdigest()
                if z in self.corrupt:
                    digest = "0" * 64
                return f"{digest}  {z}\n".encode("utf-8")
            return self.by_name[name]
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def s3env(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "RENAMES", {})
    monkeypatch.setattr(su, "DELISTED", {})
    monkeypatch.setattr(su, "CUTS", {})
    monkeypatch.setattr(su, "HALTS", {})
    monkeypatch.setattr(su, "TARGET", 3)
    monkeypatch.setattr(su, "EXCLUDED", {})
    monkeypatch.setattr(su, "declared_assets", lambda: ["AAAUSDT", "BBBUSDT"])
    series = {
        "AAAUSDT": _bars(date(2022, 1, 1), date(2025, 2, 28)),
        "BBBUSDT": _bars(date(2022, 1, 1), date(2024, 3, 31), 1.0),
        "CCCUSDT": _bars(date(2022, 1, 1), date(2024, 3, 31), 2.0),
        "NEWUSDT": _bars(date(2023, 6, 1), date(2024, 3, 31), 3.0),   # too young
        "DDDUSDT": _bars(date(2021, 1, 1), date(2023, 6, 30), 4.0),   # inactive
        "FFFUSDT": _bars(date(2022, 1, 1), date(2024, 3, 31), 5.0),
        "GGGUSDT": _bars(date(2022, 1, 1), date(2024, 3, 31), 6.0),   # never reached
    }
    cmc = _cmc_bytes([(1, "AAA", False), (2, "USDC", True), (3, "CCC", False),
                      (4, "NEW", False), (5, "DDD", False), (6, "EEE", False),
                      (7, "FFF", False), (8, "GGG", False), (9, "USDT", True)])
    return SimpleNamespace(study=tmp_path / "study", fake=FakeS3(series, cmc), cmc=cmc)


def test_fetch_cmc_writes_raw_bytes(s3env):
    rc = _run("fetch-cmc", "--study-dir", s3env.study, "--now", NOW, fetch=s3env.fake)
    assert rc == 0
    assert (s3env.study / cli.CMC_FILE).read_bytes() == s3env.cmc
    assert s3env.fake.calls == [su.CMC_URL]
    # a frozen input is never overwritten
    assert _run("fetch-cmc", "--study-dir", s3env.study, "--now", NOW, fetch=s3env.fake) == 2


def test_fetch_cmc_refuses_an_unparseable_response(s3env):
    rc = _run("fetch-cmc", "--study-dir", s3env.study, "--now", NOW,
              fetch=lambda url: b"<html>rate limited</html>")
    assert rc == 1
    assert not (s3env.study / cli.CMC_FILE).exists()


@pytest.mark.parametrize("cmd", ["plan-fetch", "universe"])
def test_plan_fetch_and_universe_refuse_without_cmc(s3env, cmd):
    rc = _run(cmd, "--study-dir", s3env.study, "--tf", "1d", fetch=s3env.fake)
    assert rc == 2
    assert s3env.fake.calls == []


def test_plan_fetch_fetch_universe_end_to_end(s3env, capsys):
    s, f = s3env.study, s3env.fake
    assert _run("fetch-cmc", "--study-dir", s, "--now", NOW, fetch=f) == 0
    assert _run("plan-fetch", "--study-dir", s, "--tf", "1d", fetch=f) == 0
    plan = json.loads((s / "plan_1d.json").read_text(encoding="utf-8"))
    # every CMC row's pair, then the 2 A pairs (BBB is not in CMC); deduped
    assert plan["symbols"] == ["AAAUSDT", "USDCUSDT", "CCCUSDT", "NEWUSDT", "DDDUSDT",
                               "EEEUSDT", "FFFUSDT", "GGGUSDT", "USDTUSDT", "BBBUSDT"]
    n_files = sum(len(v) for v in f.listing.values())
    assert len(plan["files"]) == n_files
    out = capsys.readouterr().out
    assert f'"files": {n_files}' in out
    assert not (s / "archive").exists()                 # plan-fetch downloads nothing
    assert _run("universe", "--study-dir", s, fetch=f) == 2   # fetch not run yet
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 0
    n_calls = len(f.calls)
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 0
    assert len(f.calls) == n_calls                      # resume: nothing re-downloaded
    assert _run("universe", "--study-dir", s, fetch=f) == 0
    u = json.loads((s / "universe.json").read_text(encoding="utf-8"))
    assert u["A"] == ["AAAUSDT", "BBBUSDT"]
    assert u["B"] == ["AAAUSDT", "CCCUSDT", "FFFUSDT"]
    assert u["strata"] == {"AAAUSDT": "S", "BBBUSDT": "R", "CCCUSDT": "F", "FFFUSDT": "F"}
    assert {e["symbol"]: e["reason"] for e in u["excluded"]} == {
        "USDC": "stable", "NEW": "history", "DDD": "inactive", "EEE": "no_pair"}
    assert u["cmc_sha256"] == hashlib.sha256(s3env.cmc).hexdigest()
    assert u["spans"]["AAAUSDT"] == ["2022-01-01", "2025-02-28"]


def test_universe_exits_1_when_short_of_target(s3env, monkeypatch):
    s, f = s3env.study, s3env.fake
    monkeypatch.setattr(su, "TARGET", 10)
    assert _run("fetch-cmc", "--study-dir", s, "--now", NOW, fetch=f) == 0
    assert _run("plan-fetch", "--study-dir", s, "--tf", "1d", fetch=f) == 0
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 0
    assert _run("universe", "--study-dir", s, fetch=f) == 1


def test_fetch_checksum_mismatch_exits_1(s3env):
    s, f = s3env.study, s3env.fake
    f.corrupt.add("CCCUSDT-1d-2023-01.zip")
    assert _run("fetch-cmc", "--study-dir", s, "--now", NOW, fetch=f) == 0
    assert _run("plan-fetch", "--study-dir", s, "--tf", "1d", fetch=f) == 0
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 1
    assert not (s / "archive" / "CCCUSDT" / "1d" / "CCCUSDT-1d-2023-01.zip").exists()


# ---------------------------------------------------------------- preflight

def _pf(env, tf="1d"):
    return json.loads((env.study / f"preflight_{tf}.json").read_text(encoding="utf-8"))


def test_preflight_passes_and_builds_data(env, monkeypatch):
    seen = []
    real = h.train_job

    def spy(item):
        seen.append(item[0]["universe"]["assets"][0])
        return real(item)

    monkeypatch.setattr(h, "train_job", spy)
    assert _preflight(env) == 0
    pf = _pf(env)
    assert pf["passed"] is True and pf["upto"] == 6
    assert pf["checks"]["P2"]["checked"] == 3
    assert sorted(seen) == ["AAAUSDT", "AAAUSDT", "BBBUSDT"]   # never the B-only CCC
    for p in ("AAAUSDT", "BBBUSDT", "CCCUSDT"):
        f = env.study / "data_1d" / f"{p}_1d.csv"
        assert hashlib.sha256(f.read_bytes()).hexdigest() == pf["data_sha256"][p]
    assert pf["first_bars"] == {p: "2022-06-01" for p in ("AAAUSDT", "BBBUSDT", "CCCUSDT")}
    # the pipeline data dir is read-only to the tool
    assert sorted(x.name for x in env.pipe.iterdir()) == ["AAAUSDT_1d.csv", "BBBUSDT_1d.csv"]


def test_preflight_never_runs_the_engine_on_a_b_only_cell(env, monkeypatch):
    rogue = _spec("CCCUSDT")             # a registered spec whose own cell is B-only
    env.entries += [_entry("strategy_registered", rogue), _entry("verdict", {
        "strategy_id": rogue["strategy_id"], "stage": "screened", "verdict": "fail",
        "metrics": {"trades": 0, "net_pnl": 0.0}, "artifacts_hash": "h"})]
    _write_reg(env.reg, env.entries)
    seen = []
    real = h.train_job
    monkeypatch.setattr(h, "train_job", lambda item: seen.append(
        item[0]["universe"]["assets"][0]) or real(item))
    assert _preflight(env) == 1
    assert "CCCUSDT" not in seen and len(seen) == 3
    misses = _pf(env)["checks"]["P2"]["misses"]
    assert [(m["sid"], m["why"]) for m in misses] == [
        (rogue["strategy_id"], "own cell not in A; not run")]


def test_preflight_never_calls_full_job(env, monkeypatch):
    def boom(item):
        raise AssertionError("full/gauntlet job called by preflight")

    monkeypatch.setattr(h, "full_job", boom)
    monkeypatch.setattr(h, "gauntlet_job", boom)
    assert _preflight(env) == 0
    assert _pf(env)["passed"] is True


def test_preflight_source_names_no_outcome_job():
    src = "".join(inspect.getsource(f) for f in (
        cli.cmd_preflight, cli._build_pair, cli._p2))
    assert "full_job" not in src and "gauntlet_job" not in src


def test_preflight_p2_detects_a_changed_trade_count(env, capsys):
    sid = env.specs[1]["strategy_id"]
    env.entries[3]["payload"]["metrics"]["trades"] += 1
    _write_reg(env.reg, env.entries)
    assert _preflight(env) == 1
    misses = _pf(env)["checks"]["P2"]["misses"]
    assert [m["sid"] for m in misses] == [sid]
    assert sid in capsys.readouterr().out


def test_preflight_p2_detects_a_changed_net_pnl_and_verdict(env):
    env.entries[1]["payload"]["metrics"]["net_pnl"] += 1e-9
    v = env.entries[5]["payload"]
    v["verdict"] = "fail" if v["verdict"] == "pass" else "pass"
    _write_reg(env.reg, env.entries)
    assert _preflight(env) == 1
    misses = {m["sid"] for m in _pf(env)["checks"]["P2"]["misses"]}
    assert misses == {env.specs[0]["strategy_id"], env.specs[2]["strategy_id"]}


def test_preflight_p1_detects_a_mismatched_bar(env):
    bars = [dict(b) for b in env.series["AAAUSDT"]]
    bars[100]["close"] += 0.000001
    write_pipeline_csv(bars, env.pipe / "AAAUSDT_1d.csv")
    assert _preflight(env) == 1
    p1 = _pf(env)["checks"]["P1"]
    assert list(p1) == ["AAAUSDT"]
    assert any("close" in x for x in p1["AAAUSDT"])


def test_preflight_first_bar_is_the_later_of_archive_and_pipeline(env):
    write_pipeline_csv(env.series["BBBUSDT"][30:], env.pipe / "BBBUSDT_1d.csv")
    assert _preflight(env) == 0
    pf = _pf(env)
    assert pf["first_bars"]["BBBUSDT"] == "2022-07-01"
    text = (env.study / "data_1d" / "BBBUSDT_1d.csv").read_text(encoding="utf-8")
    assert text.splitlines()[1].startswith("2022-07-01")


def test_preflight_p4_flags_a_gap_on_a_b_only_pair(env):
    skip = {date(2023, 3, 1) + timedelta(days=i) for i in range(10)}
    shutil.rmtree(env.study / "archive" / "CCCUSDT")
    _write_archive(env.study, "CCCUSDT", _bars(START, STOP, 2.0, skip=skip))
    _write_plan(env.study)
    assert _preflight(env) == 1
    p4 = _pf(env)["checks"]["P4"]
    assert list(p4) == ["CCCUSDT"] and "gap of 11 days" in p4["CCCUSDT"][0]


def _rename_env(env, monkeypatch, swap="2023-09-01", ratio=1.0, scale=1.0):
    old = [b for b in _bars(START, STOP, 2.0) if b["date"][:10] < "2023-09-15"]
    new = [dict(b, open=b["open"] / scale, high=b["high"] / scale, low=b["low"] / scale,
                close=b["close"] / scale)
           for b in _bars(START, STOP, 2.0) if b["date"][:10] >= "2023-08-20"]
    shutil.rmtree(env.study / "archive" / "CCCUSDT")
    _write_archive(env.study, "OLDUSDT", old)
    _write_archive(env.study, "NEWUSDT", new)
    _write_plan(env.study)
    monkeypatch.setattr(su, "RENAMES", {"OLDUSDT": ("NEWUSDT", swap, ratio, "https://x/a")})
    A, B = ["AAAUSDT", "BBBUSDT"], ["AAAUSDT", "OLDUSDT"]
    u = json.loads((env.study / "universe.json").read_text(encoding="utf-8"))
    u.update(B=B, strata=su.strata(A, B))
    (env.study / "universe.json").write_text(json.dumps(u), encoding="utf-8")


def test_preflight_splices_a_renamed_pair(env, monkeypatch):
    _rename_env(env, monkeypatch, ratio=24000.0, scale=24000.0)
    assert _preflight(env) == 0
    rows = (env.study / "data_1d" / "OLDUSDT_1d.csv").read_text(encoding="utf-8").splitlines()
    dates = [r.split(",")[0][:10] for r in rows[1:]]
    assert dates[0] == "2022-06-01" and dates[-1] == "2024-12-31"
    assert len(dates) == len(set(dates))


def test_preflight_p3_splice_break_fails(env, monkeypatch):
    _rename_env(env, monkeypatch, ratio=1.0, scale=24000.0)
    assert _preflight(env) == 1
    assert list(_pf(env)["checks"]["P3"]) == ["OLDUSDT"]


def test_preflight_refuses_a_none_swap_date(env, monkeypatch):
    _rename_env(env, monkeypatch, swap=None)
    assert _preflight(env) == 2
    assert not (env.study / "preflight_1d.json").exists()


def test_preflight_refuses_live_chain(env, monkeypatch):
    reg = _live_copy(env)
    calls = _trap_engine(monkeypatch)
    assert _run("preflight", *_base(env), "--registry", reg, "--pipeline-data-dir",
                env.pipe, "--workers", "1") == 2
    assert calls == []
    assert not (env.study / "preflight_1d.json").exists()


@pytest.mark.parametrize("where", ["study_inside_pipe", "pipe_inside_study"])
def test_preflight_refuses_nested_study_and_pipeline_dirs(env, monkeypatch, where):
    calls = _trap_engine(monkeypatch)
    if where == "study_inside_pipe":
        # the pipeline dir CONTAINS the study dir: preflight must not write
        # archive/ or data_1d/ into a pipeline data dir
        pipe = env.tmp
        for f in env.pipe.iterdir():
            shutil.copyfile(f, pipe / f.name)
    else:
        pipe = env.study / "pipe"
        shutil.copytree(env.pipe, pipe)
    assert _run("preflight", *_base(env), "--registry", env.reg, "--pipeline-data-dir",
                pipe, "--workers", "1") == 2
    assert calls == []
    assert not (env.study / "data_1d").exists()
    assert not (env.study / "preflight_1d.json").exists()


def test_preflight_empties_its_data_dir_before_building(env):
    stale = env.study / "data_1d" / "STALEUSDT_1d.csv"
    stale.parent.mkdir(parents=True)
    stale.write_text("date,open,high,low,close,volume\n", encoding="utf-8")
    assert _preflight(env) == 0
    assert sorted(f.name for f in (env.study / "data_1d").iterdir()) == [
        "AAAUSDT_1d.csv", "BBBUSDT_1d.csv", "CCCUSDT_1d.csv"]


# ------------------------------------------------------------------- freeze

@pytest.mark.parametrize("renames,delisted,cuts", [
    ({"MATICUSDT": ("POLUSDT", None, 1.0, "https://a")}, {}, {}),
    ({"MATICUSDT": ("POLUSDT", "2024-09-10", None, "https://a")}, {}, {}),
    ({"MATICUSDT": ("POLUSDT", "2024-09-10", 1.0, None)}, {}, {}),
    ({}, {"XMRUSDT": None}, {}),
    ({}, {}, {"LUNAUSDT": None}),
])
def test_freeze_refuses_none_rename_fields(env, monkeypatch, renames, delisted, cuts):
    # none of these pairs is in the fixture universe, so preflight passes with
    # them declared and the refusal can only come from freeze's None check
    monkeypatch.setattr(su, "RENAMES", renames)
    monkeypatch.setattr(su, "DELISTED", delisted)
    monkeypatch.setattr(su, "CUTS", cuts)
    assert _preflight(env) == 0
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def test_freeze_refuses_without_a_passed_preflight(env):
    assert _freeze(env) == 2
    env.entries[1]["payload"]["metrics"]["trades"] += 1
    _write_reg(env.reg, env.entries)
    assert _preflight(env) == 1
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def test_freeze_refuses_a_universe_changed_after_preflight(env):
    assert _preflight(env) == 0
    u = json.loads((env.study / "universe.json").read_text(encoding="utf-8"))
    u["B"] = ["AAAUSDT", "EEEUSDT"]            # a re-run universe: a never-checked pair
    u["strata"] = su.strata(u["A"], u["B"])
    (env.study / "universe.json").write_text(json.dumps(u), encoding="utf-8")
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def _fake_arm(env, tf, drop=None, declared=None):
    """preflight_<tf>.json + data_<tf>/ cloned from the 1d arm (same bytes)."""
    pf = _pf(env)
    pf["tf"] = tf
    d = env.study / f"data_{tf}"
    d.mkdir()
    for p in list(pf["data_sha256"]):
        if p == drop:
            pf["pairs"].remove(p)
            del pf["data_sha256"][p]
            continue
        shutil.copyfile(env.study / "data_1d" / f"{p}_1d.csv", d / f"{p}_{tf}.csv")
    if declared is not None:
        pf["declared"] = declared
    (env.study / f"preflight_{tf}.json").write_text(json.dumps(pf), encoding="utf-8")


@pytest.mark.parametrize("case", ["pair_missing", "declared_changed"])
def test_gate_refuses_an_arm_whose_preflight_disagrees(env, case):
    _chain(env, _frozen(env))
    args = SimpleNamespace(registry=str(env.reg))
    cli._gate(args, env.study, "1d")                      # the 1d arm is consistent
    if case == "pair_missing":
        _fake_arm(env, "4h", drop="CCCUSDT")
    else:
        _fake_arm(env, "4h", declared={
            "renames": {"OLDUSDT": ["NEWUSDT", "2024-01-01", 1.0, "u"]},
            "delisted": {}, "cuts": {}})
    with pytest.raises(cli.Refused):
        cli._gate(args, env.study, "4h")


def test_gate_refuses_1d_preflight_pairs_that_differ_from_the_universe(env):
    _chain(env, _frozen(env))
    pf = _pf(env)
    pf["pairs"] = ["AAAUSDT", "CCCUSDT", "BBBUSDT"]        # same set, other order
    (env.study / "preflight_1d.json").write_text(json.dumps(pf), encoding="utf-8")
    with pytest.raises(cli.Refused):
        cli._gate(SimpleNamespace(registry=str(env.reg)), env.study, "1d")


def test_freeze_refuses_an_upto_preflight_did_not_use(env):
    assert _preflight(env) == 0
    assert _freeze(env, upto=4) == 2


def test_freeze_writes_canonical_hashes(env):
    assert _preflight(env) == 0
    assert _freeze(env) == 0
    m = json.loads((env.study / "manifest.json").read_text(encoding="utf-8"))
    assert m["upto"] == 6
    canon = lambda o: json.dumps(o, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = lambda b: hashlib.sha256(b).hexdigest()
    assert m["hashes"]["cells_A"] == sha(canon(["AAAUSDT", "BBBUSDT"]))
    assert m["hashes"]["cells_B"] == sha(canon(["AAAUSDT", "CCCUSDT"]))
    assert m["hashes"]["cmc"] == sha((env.study / cli.CMC_FILE).read_bytes())
    copy = st.read_copy(env.reg, 6)
    assert m["hashes"]["templates"] == sha(canon({"1d": st.templates(copy, "1d")}))
    assert m["hashes"]["renames"] == sha(canon({}))
    assert m["hashes"]["first_bars"] == sha(canon(_pf(env)["first_bars"]))
    assert set(m["hashes"]) == {"templates", "cells_A", "cells_B", "cmc", "renames",
                                "delisted", "cuts", "first_bars", "data_1d",
                                "design_doc_sha256", "study_code", "excluded",
                                "halts"}


# --------------------------------------------------------------- draft-note

def test_draft_note_is_lf_and_starts_with_prefix(env):
    text = _frozen(env)
    raw = _note_bytes(env)
    assert b"\r" not in raw
    assert text.startswith(cli.NOTE_PREFIX)
    assert raw.endswith(b"\n")


def test_draft_note_carries_s5_verbatim_and_every_hash(env, monkeypatch):
    monkeypatch.setattr(su, "CUTS", {})
    text = _frozen(env)
    design = DESIGN.read_bytes().decode("utf-8").replace("\r\n", "\n")
    s5 = design[design.index("\n## 5.") + 1:design.index("\n## 6.")].strip("\n")
    assert s5.startswith("## 5. Decision rule") and "**MATERIAL**" in s5
    assert s5 in text
    # final review I9: s5 itself says first match wins; no extra clarification line
    assert "Where G >= 0.10, L <= 0 and U < 0.10" not in text
    assert not hasattr(cli, "CLARIFICATION")
    m = json.loads((env.study / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in m["hashes"].items():
        assert f"{name}: {digest}" in text
    assert "upto: 6" in text


def test_draft_note_lists_splices_deaths_and_cuts(env, monkeypatch):
    monkeypatch.setattr(su, "RENAMES", {"MKRUSDT": ("SKYUSDT", "2025-09-18", 24000.0, "https://a/mkr")})
    monkeypatch.setattr(su, "DELISTED", {"XMRUSDT": "https://a/xmr"})
    monkeypatch.setattr(su, "CUTS", {"LUNAUSDT": "2022-05-31"})
    assert _preflight(env) == 0          # none of these pairs is in the fixture universe
    assert _freeze(env) == 0
    assert _draft(env) == 0
    text = _note_bytes(env).decode("utf-8")
    assert "MKRUSDT -> SKYUSDT" in text and "2025-09-18" in text and "https://a/mkr" in text
    assert "XMRUSDT" in text and "https://a/xmr" in text
    assert "LUNAUSDT" in text and "2022-05-31" in text


def test_draft_note_reads_the_design_doc_from_its_own_layer(env):
    doc = env.layer / cli.DESIGN_REL
    doc.write_bytes(doc.read_bytes().replace(b"**MATERIAL**", b"**TAMPERED**"))
    text = _frozen(env)
    assert "**TAMPERED**" not in text and "**MATERIAL**" in text
    lf = DESIGN.read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(lf).hexdigest()
    assert _manifest(env)["hashes"]["design_doc_sha256"] == digest
    assert f"design_doc_sha256: {digest}" in text


def test_freeze_and_note_state_template_and_sid_counts(env, capsys):
    assert _preflight(env) == 0
    assert _freeze(env) == 0
    assert "templates 1d: 2 templates, 3 strategy ids" in capsys.readouterr().out
    assert _draft(env) == 0
    assert "- templates per timeframe: 1d 2 templates, 3 strategy ids" in \
        _note_bytes(env).decode("utf-8")


def test_draft_note_refuses_a_stale_manifest(env, monkeypatch):
    _frozen(env)
    monkeypatch.setattr(su, "CUTS", {"LUNAUSDT": "2022-05-31"})
    assert _draft(env) == 2


# ------------------------------------------------------------- the note gate

def _manifest(env):
    return json.loads((env.study / "manifest.json").read_text(encoding="utf-8"))


def test_require_note_boundaries(tmp_path, monkeypatch):
    m = {"upto": 6, "hashes": {"a": "aa11", "b": "bb22"}}
    good = cli.NOTE_PREFIX + "x aa11 bb22"
    monkeypatch.setattr(cli, "NOTE_FILE", tmp_path / "note.md")
    (tmp_path / "note.md").write_bytes(good.encode("utf-8"))
    assert cli.require_note(st.Copy(notes=[(6, good)]), m)[0] == 6
    assert cli.require_note(st.Copy(notes=[(9, "other"), (7, good)]), m)[0] == 7
    for notes in ([], [(5, good)], [(8, cli.NOTE_PREFIX + "aa11 only")],
                  [(8, "survivorship-study-v1-result: aa11 bb22")],
                  [(8, "x " + good)]):
        with pytest.raises(cli.NoteMissing):
            cli.require_note(st.Copy(notes=notes), m)


def _note_line_sha(reg, idx):
    line = reg.read_bytes().split(b"\n")[idx].rstrip(b"\r")
    return hashlib.sha256(line).hexdigest()


@pytest.mark.parametrize("eol", [b"\n", b"\r\n"])
def test_require_note_returns_the_exact_line_sha(env, eol):
    text = _frozen(env)
    _chain(env, text)
    raw = env.reg.read_bytes().replace(b"\r\n", b"\n")
    # the same entries LF-terminated, and CRLF-terminated as the live chain is
    env.reg.write_bytes(raw if eol == b"\n" else raw.replace(b"\n", b"\r\n"))
    m = _manifest(env)
    idx, digest = cli.require_note(st.read_copy(env.reg, 6), m, env.reg)
    assert idx == 6
    assert digest == _note_line_sha(env.reg, 6)
    assert digest == hashlib.sha256(raw.split(b"\n")[6]).hexdigest()


@pytest.mark.parametrize("cmd", ["run", "gauntlet", "analyse"])
def test_run_refuses_without_note(env, monkeypatch, cmd):
    _frozen(env)
    calls = _trap_engine(monkeypatch)
    rc = _run(cmd, *_base(env), "--registry", env.reg, "--tf", "1d", "--layer-dir", env.layer)
    assert rc == 2
    assert calls == []
    assert not list(env.study.glob("results_*")) and not list(env.study.glob("train_*"))


def test_run_refuses_on_hash_mismatch(env, monkeypatch):
    text = _frozen(env)
    digest = _manifest(env)["hashes"]["cells_B"]
    _chain(env, text.replace(digest, "f" * 64))
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


def test_run_refuses_on_input_drift_after_the_note(env, monkeypatch):
    _chain(env, _frozen(env))
    u = json.loads((env.study / "universe.json").read_text(encoding="utf-8"))
    u["B"] = ["AAAUSDT"]
    (env.study / "universe.json").write_text(json.dumps(u), encoding="utf-8")
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


def test_run_refuses_on_data_drift_after_the_note(env, monkeypatch):
    _chain(env, _frozen(env))
    f = env.study / "data_1d" / "CCCUSDT_1d.csv"
    f.write_bytes(f.read_bytes() + b"2025-01-01 00:00:00,1.0,1.0,1.0,1.0,1.0\n")
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


def _live_copy(env):
    """The registry copy, byte for byte, in a separate directory that holds
    registry_log.d (the live layout); the study dir stays clean, so only
    refuse_live can refuse."""
    live = env.tmp / "live"
    (live / "registry_log.d").mkdir(parents=True)
    reg = live / "registry_copy.jsonl"
    shutil.copyfile(env.reg, reg)
    return reg


def test_run_refuses_live_chain(env, monkeypatch):
    _chain(env, _frozen(env))
    reg = _live_copy(env)
    calls = _trap_engine(monkeypatch)
    for cmd in ("run", "gauntlet", "analyse", "freeze", "draft-note"):
        extra = ["--upto", "6"] if cmd == "freeze" else []
        assert _run(cmd, *_base(env), "--registry", reg, "--layer-dir", env.layer,
                    *extra) == 2
    assert calls == []
    assert not list(env.study.glob("results_*")) and not list(env.study.glob("train_*"))


def test_analyse_refuses_live_chain_with_complete_results(env, monkeypatch):
    # every result is on disk, so only refuse_live can stop analyse
    _chain(env, _frozen(env))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": False, "reason": "f", "beats_hold": None})
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    reg = _live_copy(env)
    assert _run("analyse", *_base(env), "--registry", reg, "--layer-dir", env.layer) == 2
    assert not (env.layer / "docs" / "runs").exists()
    assert not (env.layer / cli.RESULT_REL).exists()


def test_run_refuses_a_note_inside_the_frozen_prefix(env, monkeypatch):
    filler = _entry("verdict", {"strategy_id": "zz", "stage": "other"})
    _write_reg(env.reg, env.entries + [filler])            # index 6, upto 7
    assert _preflight(env) == 0
    assert _freeze(env, upto=7) == 0
    assert _draft(env) == 0
    note = _note_bytes(env).decode("utf-8")
    _write_reg(env.reg, env.entries + [_entry("note", {"text": note})])   # note at 6 < 7
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


# -------------------------------------------------------------- the arms

def _results(env, name):
    p = env.study / name
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_run_writes_every_template_cell_and_resumes(env):
    _chain(env, _frozen(env))
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    rows = _results(env, "results_1d.jsonl")
    assert len(rows) == 2 * 3
    assert {r["pair"] for r in rows} == {"AAAUSDT", "BBBUSDT", "CCCUSDT"}
    assert {r["pair"]: r["stratum"] for r in rows} == {
        "AAAUSDT": "S", "BBBUSDT": "R", "CCCUSDT": "F"}
    assert all(r["key"] == f"{r['tkey']}|{r['pair']}|1d" for r in rows)
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert len(_results(env, "results_1d.jsonl")) == 6


def test_now_override_is_an_offset_clock(monkeypatch):
    t0 = datetime(2026, 11, 7, 1, 0, tzinfo=timezone.utc)
    ticks = iter([t0, t0 + timedelta(seconds=90), t0 + timedelta(hours=1)])
    monkeypatch.setattr(cli, "_real_now", lambda: next(ticks))
    fn = cli._now_fn(SimpleNamespace(now="2026-11-07T19:59:00+08:00"))
    assert fn().isoformat() == "2026-11-07T20:00:30+08:00"
    assert fn().isoformat() == "2026-11-07T20:59:00+08:00"


def test_run_stops_at_the_window_with_exit_3(env, monkeypatch):
    _chain(env, _frozen(env))
    still = datetime.now(timezone.utc)
    monkeypatch.setattr(cli, "_real_now", lambda: still)      # no real time passes
    rc = _run("run", "--study-dir", env.study, "--now", "2026-11-07T19:59:50+08:00",
              "--registry", env.reg, "--workers", "1")
    assert rc == 3
    assert not (env.study / "results_1d.jsonl").exists() or \
        _results(env, "results_1d.jsonl") == []


def test_gauntlet_runs_train_then_screen_passers_only(env, monkeypatch):
    _chain(env, _frozen(env))
    sent = []

    def fake_gauntlet(item):
        sent.append(item[0]["universe"]["assets"][0])
        return {"sid": item[0]["strategy_id"], "passed": False, "reason": "fake",
                "beats_hold": None}

    real_train = h.train_job

    def train_aaa_passes(item):
        # the fixture series pass no screen: force AAA cells to pass so the
        # routing (only screen passers reach gauntlet_job) is exercised
        r = real_train(item)
        r["screen_pass"] = item[0]["universe"]["assets"] == ["AAAUSDT"]
        return r

    monkeypatch.setattr(h, "train_job", train_aaa_passes)
    monkeypatch.setattr(h, "gauntlet_job", fake_gauntlet)
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    train = _results(env, "train_1d.jsonl")
    assert len(train) == 6
    passers = sorted(r["pair"] for r in train if r["screen_pass"])
    assert passers == ["AAAUSDT", "AAAUSDT"]
    assert sorted(sent) == passers
    assert len(_results(env, "gauntlet_1d.jsonl")) == len(passers)


def test_analyse_refuses_incomplete_results(env, monkeypatch):
    _chain(env, _frozen(env))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": False, "reason": "f", "beats_hold": None})
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("analyse", *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 2
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    p = env.study / "results_1d.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    p.write_text("".join(lines[:-1]), encoding="utf-8")
    assert _run("analyse", *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 2
    assert not (env.layer / "docs" / "runs").exists()


def test_analyse_writes_report_json_and_result_note(env, monkeypatch):
    _chain(env, _frozen(env))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": True, "reason": "f", "beats_hold": None})
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("analyse", *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 0
    md = env.layer / "docs" / "runs" / "2026-11-07-survivorship-1d.md"
    res = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    assert md.read_text(encoding="utf-8").startswith("# Survivorship study report: 1d arm")
    assert res["verdict"] in ("MATERIAL", "NOT MATERIAL", "REVERSE", "INCONCLUSIVE")
    assert res["stratum_sizes"] == {"S": 1, "R": 1, "F": 1, "N": 0, "D": 0}
    rows = _results(env, "results_1d.jsonl")
    by = {(r["tkey"], r["pair"]): r["m"] for r in rows}
    tkeys = sorted({r["tkey"] for r in rows})
    G = sum((by[(t, "AAAUSDT")] + by[(t, "BBBUSDT")]) / 2
            - (by[(t, "AAAUSDT")] + by[(t, "CCCUSDT")]) / 2 for t in tkeys) / len(tkeys)
    assert res["G"] == pytest.approx(G, abs=1e-12)
    raw = (env.layer / cli.RESULT_REL).read_bytes()
    assert b"\r" not in raw
    text = raw.decode("utf-8")
    assert text.startswith(cli.RESULT_PREFIX)
    assert f"{res['G']:.4f}" in text and "tau = 0.10" in text and "preflight" in text
    digest = _note_line_sha(env.reg, 6)
    line = f"pre-registration note: entry 6, line sha256 {digest}"
    assert res["note_index"] == 6 and res["note_line_sha256"] == digest
    assert line in md.read_text(encoding="utf-8").split("\n")
    assert line in text.split("\n")


def test_only_the_1d_arm_drafts_the_result_note():
    # an arm other than 1d: report only, never the result note (SV4)
    assert cli._result_note_for("4h") is False and cli._result_note_for("1d") is True


# ------------------------------------------- integrity (final review I1-I3, I6)

def _set_universe(env, **kw):
    p = env.study / "universe.json"
    u = json.loads(p.read_text(encoding="utf-8"))
    u.update(kw)
    p.write_text(json.dumps(u), encoding="utf-8")


def test_freeze_refuses_a_hand_edited_stratum(env):
    assert _preflight(env) == 0
    # the same stratum sizes (so the I6 count check cannot be what refuses)
    _set_universe(env, strata={"AAAUSDT": "R", "BBBUSDT": "S", "CCCUSDT": "F"})
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def test_gate_refuses_a_hand_edited_stratum(env, monkeypatch):
    _chain(env, _frozen(env))
    _set_universe(env, strata={"AAAUSDT": "S", "BBBUSDT": "R", "CCCUSDT": "D"})
    calls = _trap_engine(monkeypatch)
    for cmd in ("run", "gauntlet", "analyse"):
        assert _run(cmd, *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 2
    assert calls == []


def _stamp_want(env):
    m = _manifest(env)
    digest = hashlib.sha256(cli.canon(m["hashes"]).encode("utf-8")).hexdigest()
    return digest, _pf(env)["data_sha256"]


def test_written_rows_carry_sid_data_sha_and_manifest_digest(env, monkeypatch):
    _chain(env, _frozen(env))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": False, "reason": "f", "beats_hold": None})
    monkeypatch.setattr(h, "train_job", lambda item, real=h.train_job: dict(
        real(item), screen_pass=True))
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    digest, data = _stamp_want(env)
    copy = st.read_copy(env.reg, 6)
    tmpl = st.templates(copy, "1d")
    for name in ("results_1d.jsonl", "train_1d.jsonl", "gauntlet_1d.jsonl"):
        rows = _results(env, name)
        assert rows
        for r in rows:
            sid = st.cell_spec(copy, r["tkey"], tmpl[r["tkey"]], r["pair"], "1d")["strategy_id"]
            assert r["strategy_id"] == sid
            assert r["data_sha256"] == data[r["pair"]]
            assert r["manifest_digest"] == digest


def test_run_recomputes_a_row_whose_stamp_is_stale(env):
    _chain(env, _frozen(env))
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    p = env.study / "results_1d.jsonl"
    rows = _results(env, "results_1d.jsonl")
    stale = dict(rows[0], manifest_digest="0" * 64, m=123.0)
    p.write_text("".join(json.dumps(r) + "\n" for r in [stale] + rows[1:]), encoding="utf-8")
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    now = _results(env, "results_1d.jsonl")
    digest, _d = _stamp_want(env)
    assert len(now) == 6 and len({r["key"] for r in now}) == 6
    assert all(r["manifest_digest"] == digest for r in now)
    assert {r["key"]: r["m"] for r in now}[rows[0]["key"]] == rows[0]["m"]
    moved = [json.loads(x) for x in (env.study / "results_1d.stale.jsonl")
             .read_text(encoding="utf-8").splitlines()]
    assert moved == [stale]


@pytest.mark.parametrize("name,field", [("results_1d.jsonl", "data_sha256"),
                                        ("results_1d.jsonl", "manifest_digest"),
                                        ("train_1d.jsonl", "strategy_id")])
def test_analyse_refuses_a_row_whose_stamp_differs(env, monkeypatch, name, field):
    _chain(env, _frozen(env))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": False, "reason": "f", "beats_hold": None})
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    p = env.study / name
    rows = _results(env, name)
    rows[0][field] = "f" * 16
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert _run("analyse", *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 2
    assert not (env.layer / "docs" / "runs").exists()


def _code_copy(env, monkeypatch):
    code = env.tmp / "code"
    code.mkdir()
    for f in Path(cli.__file__).resolve().parent.glob("survivorship*.py"):
        shutil.copyfile(f, code / f.name)
    monkeypatch.setattr(cli, "CODE_DIR", code)
    return code


def test_freeze_pins_the_study_code_and_records_git_head(env, monkeypatch):
    code = _code_copy(env, monkeypatch)
    assert _preflight(env) == 0
    assert _freeze(env) == 0
    m = _manifest(env)
    files = {f.name: hashlib.sha256(f.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
             for f in sorted(code.glob("survivorship*.py"))}
    assert set(files) >= {"survivorship.py", "survivorship_harness.py",
                          "survivorship_stats.py", "survivorship_universe.py"}
    assert not any(n.startswith("test_") for n in files)
    assert m["inputs"]["study_code_files"] == files
    assert m["hashes"]["study_code"] == hashlib.sha256(
        cli.canon(files).encode("utf-8")).hexdigest()
    assert "git_head" in m and "git_head" not in m["hashes"]


def test_gate_refuses_study_code_changed_after_freeze(env, monkeypatch):
    code = _code_copy(env, monkeypatch)
    _chain(env, _frozen(env))
    f = code / "survivorship_stats.py"
    f.write_bytes(f.read_bytes().replace(b"TAU = 0.10", b"TAU = 0.20"))
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


def test_study_code_hash_ignores_crlf(env, monkeypatch):
    code = _code_copy(env, monkeypatch)
    before = cli._study_code_files()
    f = code / "survivorship_checks.py"
    f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert cli._study_code_files() == before


@pytest.mark.parametrize("change", [
    {"templates_1d": 80}, {"sids_1d": 960},
    {"strata": {"S": 66, "R": 10, "F": 22, "N": 7, "D": 5}}])
def test_freeze_refuses_counts_that_differ_from_the_design(env, monkeypatch, change):
    monkeypatch.setattr(cli, "DESIGN_COUNTS", {**cli.DESIGN_COUNTS, **change})
    assert _preflight(env) == 0
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def test_freeze_accept_counts_records_it_and_the_note_says_so(env, monkeypatch):
    monkeypatch.setattr(cli, "DESIGN_COUNTS", {
        "templates_1d": 80, "sids_1d": 960,
        "strata": {"S": 66, "R": 10, "F": 22, "N": 7, "D": 5}})
    assert _preflight(env) == 0
    assert _run("freeze", *_base(env), "--registry", env.reg, "--upto", 6,
                "--accept-counts") == 0
    assert _manifest(env)["accepted_counts"] is True
    assert _draft(env) == 0
    text = _note_bytes(env).decode("utf-8")
    assert "differ from the design" in text
    assert "1d 2 templates, 3 strategy ids (design: 80 templates, 960 strategy ids)" in text
    assert "strata S 1, R 1, F 1, N 0, D 0 (design: S 66, R 10, F 22, N 7, D 5)" in text


def test_freeze_at_the_design_counts_records_accepted_counts_false(env):
    assert _preflight(env) == 0
    assert _freeze(env) == 0
    assert _manifest(env)["accepted_counts"] is False
    assert _draft(env) == 0
    assert "differ from the design" not in _note_bytes(env).decode("utf-8")


# ---------------------------------------- the box (final review I4, I7, M-b, M-h)

def _ticking(monkeypatch, step_s):
    """The real clock behind the offset --now clock advances step_s per read."""
    t = [datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)]

    def tick():
        t[0] += timedelta(seconds=step_s)
        return t[0]

    monkeypatch.setattr(cli, "_real_now", tick)


def _planned(s3env):
    s, f = s3env.study, s3env.fake
    assert _run("fetch-cmc", "--study-dir", s, "--now", NOW, fetch=f) == 0
    assert _run("plan-fetch", "--study-dir", s, "--tf", "1d", fetch=f) == 0
    return json.loads((s / "plan_1d.json").read_text(encoding="utf-8"))["files"]


def _downloads(fake):
    return [u for u in fake.calls if u.startswith(sa.DL) and not u.endswith(".CHECKSUM")]


def test_fetch_stops_at_the_window_with_exit_3_and_resumes(s3env, monkeypatch, capsys):
    s, f = s3env.study, s3env.fake
    files = _planned(s3env)
    _ticking(monkeypatch, 20)
    # 19:59:00 + 20 s per clock read: two files fit, the third check is 20:00:00
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now",
                "2026-11-07T19:59:00+08:00", fetch=f) == 3
    assert len(_downloads(f)) == 2
    assert "stopped at the run window" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_real_now", lambda: datetime.now(timezone.utc))
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 0
    got = _downloads(f)
    assert len(got) == len(files) and len(set(got)) == len(files)   # none fetched twice


@pytest.mark.parametrize("code", [403, 418, 429])
def test_fetch_stops_at_once_on_a_ban_status(s3env, capsys, code):
    s, f = s3env.study, s3env.fake
    _planned(s3env)
    n = []

    def banning(url):
        if url.startswith(sa.DL) and not url.endswith(".CHECKSUM"):
            n.append(url)
            if len(n) == 2:
                raise urllib.error.HTTPError(url, code, "banned", {}, None)
            if len(n) > 2:
                raise AssertionError("fetched after a ban status")
        return f(url)

    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=banning) == 1
    assert len(n) == 2
    out = capsys.readouterr().out
    assert f"HTTP {code}" in out and "stopped" in out


def test_fetch_carries_on_past_an_ordinary_http_error(s3env):
    s, f = s3env.study, s3env.fake
    files = _planned(s3env)
    n = []

    def flaky(url):
        if url.startswith(sa.DL) and not url.endswith(".CHECKSUM"):
            n.append(url)
            if len(n) == 1:
                raise urllib.error.HTTPError(url, 500, "server", {}, None)
        return f(url)

    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=flaky) == 1
    assert len(n) == len(files)


def test_fetch_prints_progress_per_batch(s3env, capsys, monkeypatch):
    monkeypatch.setattr(cli, "FETCH_BATCH", 10)
    files = _planned(s3env)
    capsys.readouterr()
    assert _run("fetch", "--study-dir", s3env.study, "--tf", "1d", "--now", NOW,
                fetch=s3env.fake) == 0
    lines = [x for x in capsys.readouterr().out.splitlines() if "files/s" in x]
    assert len(lines) == -(-len(files) // 10)
    assert lines[-1].startswith(f"fetch: {len(files)}/{len(files)} files") and "ETA" in lines[-1]


def test_fetch_cmc_network_error_is_a_fail_line_not_a_traceback(s3env, capsys):
    def down(url):
        raise urllib.error.URLError("connection refused")

    assert _run("fetch-cmc", "--study-dir", s3env.study, "--now", NOW, fetch=down) == 1
    assert "fetch-cmc: FAIL" in capsys.readouterr().out
    assert not (s3env.study / cli.CMC_FILE).exists()


def test_preflight_p2_stops_at_the_window_with_exit_3(env, monkeypatch):
    still = datetime.now(timezone.utc)
    monkeypatch.setattr(cli, "_real_now", lambda: still)
    calls = []
    monkeypatch.setattr(h, "train_job", lambda item: calls.append(1))
    # 3 train runs x 2 s prior from 19:59:57 would end after 20:00
    assert _run("preflight", "--study-dir", env.study, "--now", "2026-11-07T19:59:57+08:00",
                "--registry", env.reg, "--pipeline-data-dir", env.pipe, "--workers", "1") == 3
    assert calls == []
    pf = _pf(env)
    assert pf["passed"] is False and pf["checks"]["P2"]["stopped_at_window"] is True


def test_preflight_p2_runs_in_chunks_inside_the_worker_env(env, monkeypatch, capsys):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "8")
    seen = []
    real = h.run_all

    def spy(job, items, workers):
        seen.append((len(items), os.environ.get("OPENBLAS_NUM_THREADS"),
                     os.environ.get("OMP_NUM_THREADS"), os.environ.get("MKL_NUM_THREADS")))
        return real(job, items, workers)

    monkeypatch.setattr(h, "run_all", spy)
    monkeypatch.setattr(cli, "P2_CHUNK_PER_WORKER", 2)
    assert _preflight(env) == 0
    assert seen == [(2, "1", "1", "1"), (1, "1", "1", "1")]
    assert os.environ["OPENBLAS_NUM_THREADS"] == "8"
    lines = [x for x in capsys.readouterr().out.splitlines() if x.startswith("preflight P2:")]
    assert [x.split()[2] for x in lines] == ["2/3", "3/3"]


@pytest.mark.parametrize("cmd", ["preflight", "run", "gauntlet"])
def test_workers_default_fits_the_box(monkeypatch, cmd):
    monkeypatch.setattr(h.os, "cpu_count", lambda: 16)
    extra = ["--pipeline-data-dir", "p"] if cmd == "preflight" else []
    a = cli._parser().parse_args([cmd, "--study-dir", "s", "--registry", "r", *extra])
    assert a.workers == 4
    monkeypatch.setattr(h.os, "cpu_count", lambda: 8)
    a = cli._parser().parse_args([cmd, "--study-dir", "s", "--registry", "r", *extra])
    assert a.workers == 3


def test_now_is_refused_without_the_test_clock_env(env, monkeypatch):
    monkeypatch.delenv("SURVIVORSHIP_TEST_CLOCK")
    fetched = []
    (env.study / cli.CMC_FILE).unlink()
    assert _run("fetch-cmc", "--study-dir", env.study, "--now", NOW,
                fetch=lambda u: fetched.append(u) or b"") == 2
    assert fetched == []
    calls = _trap_engine(monkeypatch)
    assert _preflight(env) == 2
    assert calls == [] and not (env.study / "preflight_1d.json").exists()
    monkeypatch.setenv("SURVIVORSHIP_TEST_CLOCK", "yes")
    assert _preflight(env) == 2


# --------------------------------- data completeness (final review I5, M-d)

def _drop_month(env, sym, month):
    (env.study / "archive" / sym / "1d" / f"{sym}-1d-{month}.zip").unlink()


def test_preflight_fails_on_a_planned_month_missing_from_the_archive(env, capsys):
    _drop_month(env, "AAAUSDT", "2023-05")          # still in plan_1d.json
    assert _preflight(env) == 1
    build = _pf(env)["checks"]["build"]
    assert list(build) == ["AAAUSDT"] and "2023-05" in build["AAAUSDT"][0]
    assert "AAAUSDT" in capsys.readouterr().out


def test_preflight_fails_a_pair_the_plan_has_no_months_for(env):
    plan = json.loads((env.study / "plan_1d.json").read_text(encoding="utf-8"))
    plan["files"] = [f for f in plan["files"] if f[0] != "CCCUSDT"]
    (env.study / "plan_1d.json").write_text(json.dumps(plan), encoding="utf-8")
    assert _preflight(env) == 1
    assert "no planned months" in _pf(env)["checks"]["build"]["CCCUSDT"][0]


def test_preflight_refuses_without_a_plan(env):
    (env.study / "plan_1d.json").unlink()
    assert _preflight(env) == 2


def test_preflight_fails_a_missing_successor_month(env, monkeypatch):
    _rename_env(env, monkeypatch)
    _drop_month(env, "NEWUSDT", "2024-03")
    assert _preflight(env) == 1
    assert "NEWUSDT" in _pf(env)["checks"]["build"]["OLDUSDT"][0]


def _end_early(env, sym, last_month="2024-11"):
    for z in (env.study / "archive" / sym / "1d").glob("*.zip"):
        if z.stem[-7:] > last_month:
            z.unlink()
    _write_plan(env.study)


@pytest.mark.parametrize("sym", ["AAAUSDT", "BBBUSDT", "CCCUSDT"])   # S, R, F
def test_preflight_fails_a_survivor_whose_last_bar_is_not_end(env, sym):
    _end_early(env, sym)
    if sym != "CCCUSDT":
        write_pipeline_csv(env.series[sym][:-31], env.pipe / f"{sym}_1d.csv")
    assert _preflight(env) == 1
    pf = _pf(env)
    p3 = pf["checks"]["P3"]
    assert list(p3) == [sym] and "stratum classification error" in p3[sym][0]
    assert not pf["checks"]["P1"]


def test_preflight_fails_a_renamed_pair_that_ends_before_end(env, monkeypatch):
    _rename_env(env, monkeypatch)
    _end_early(env, "NEWUSDT")
    assert _preflight(env) == 1
    assert "stratum classification error" in _pf(env)["checks"]["P3"]["OLDUSDT"][0]


def _delist_ccc(env, monkeypatch):
    monkeypatch.setattr(su, "DELISTED", {"CCCUSDT": "https://x/ccc"})
    _set_universe(env, strata=su.strata(["AAAUSDT", "BBBUSDT"], ["AAAUSDT", "CCCUSDT"]))


def test_preflight_passes_a_delisted_pair_that_ends_before_end(env, monkeypatch):
    _end_early(env, "CCCUSDT")
    _delist_ccc(env, monkeypatch)
    assert _preflight(env) == 0
    assert _pf(env)["last_bars"]["CCCUSDT"] == "2024-11-30"


def test_preflight_fails_a_delisted_pair_that_trades_to_end(env, monkeypatch):
    _delist_ccc(env, monkeypatch)
    assert _preflight(env) == 1
    p3 = _pf(env)["checks"]["P3"]
    assert list(p3) == ["CCCUSDT"] and "stratum classification error" in p3["CCCUSDT"][0]


def test_preflight_refuses_a_hand_edited_stratum(env):
    _set_universe(env, strata={"AAAUSDT": "R", "BBBUSDT": "S", "CCCUSDT": "F"})
    assert _preflight(env) == 2


def test_universe_refuses_a_cmc_listing_without_stablecoin_tags(s3env):
    s, f = s3env.study, s3env.fake
    assert _run("fetch-cmc", "--study-dir", s, "--now", NOW, fetch=f) == 0
    assert _run("plan-fetch", "--study-dir", s, "--tf", "1d", fetch=f) == 0
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 0
    (s / cli.CMC_FILE).write_bytes(_cmc_bytes([(1, "AAA", False), (2, "USDT", False),
                                               (3, "USDC", False)]))
    assert _run("universe", "--study-dir", s) != 0
    assert not (s / "universe.json").exists()


def test_fetch_cmc_refuses_a_listing_without_stablecoin_tags(s3env):
    bad = _cmc_bytes([(1, "AAA", False), (2, "USDT", False), (3, "USDC", False)])
    assert _run("fetch-cmc", "--study-dir", s3env.study, "--now", NOW,
                fetch=lambda u: bad) == 1
    assert not (s3env.study / cli.CMC_FILE).exists()


# ------------------------------------- declared exclusions (final review I8)

def test_universe_walk_refills_past_a_declared_exclusion(s3env, monkeypatch):
    s, f = s3env.study, s3env.fake
    monkeypatch.setattr(su, "EXCLUDED", {"CCCUSDT": "ticker reuse (Coen 2026-11-06)"})
    assert _run("fetch-cmc", "--study-dir", s, "--now", NOW, fetch=f) == 0
    assert _run("plan-fetch", "--study-dir", s, "--tf", "1d", fetch=f) == 0
    assert _run("fetch", "--study-dir", s, "--tf", "1d", "--now", NOW, fetch=f) == 0
    assert _run("universe", "--study-dir", s, fetch=f) == 0
    u = json.loads((s / "universe.json").read_text(encoding="utf-8"))
    assert u["B"] == ["AAAUSDT", "FFFUSDT", "GGGUSDT"]        # GGG takes CCC's slot
    assert {e["symbol"]: e["reason"] for e in u["excluded"]}["CCC"] == \
        "declared exclusion: ticker reuse (Coen 2026-11-06)"
    assert u["declared_exclusions"] == {"CCCUSDT": "ticker reuse (Coen 2026-11-06)"}


def test_the_manifest_hash_changes_when_excluded_changes(env, monkeypatch):
    _chain(env, _frozen(env))
    copy = st.read_copy(env.reg, 6)
    before = cli._input_hashes(env.study, copy)
    monkeypatch.setattr(su, "EXCLUDED", {"ZZZUSDT": "ticker reuse"})
    after = cli._input_hashes(env.study, copy)
    assert sorted(k for k in before if before[k] != after[k]) == ["excluded"]
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


def test_freeze_refuses_a_universe_built_under_other_exclusions(env, monkeypatch):
    assert _preflight(env) == 0
    monkeypatch.setattr(su, "EXCLUDED", {"ZZZUSDT": "ticker reuse"})
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def test_freeze_refuses_an_empty_exclusion_reason(env, monkeypatch):
    monkeypatch.setattr(su, "EXCLUDED", {"ZZZUSDT": ""})
    _set_universe(env, declared_exclusions={"ZZZUSDT": ""})
    assert _preflight(env) == 0
    assert _freeze(env) == 2


def test_draft_note_lists_every_declared_exclusion(env, monkeypatch):
    ex = {"ZZZUSDT": "ticker reuse inside the used span", "YYYUSDT": "halt 2024-03"}
    monkeypatch.setattr(su, "EXCLUDED", ex)
    _set_universe(env, declared_exclusions=ex)
    text = _frozen(env)
    assert "- declared exclusion YYYUSDT: halt 2024-03" in text
    assert "- declared exclusion ZZZUSDT: ticker reuse inside the used span" in text
    assert "P4 flag" not in text
    m = _manifest(env)
    assert m["inputs"]["excluded"] == ex
    assert f"excluded: {m['hashes']['excluded']}" in text


def test_draft_note_says_no_exclusions_when_none_are_declared(env):
    text = _frozen(env)
    assert "- exclusions: none" in text and "P4 flag" not in text


def test_preflight_refuses_a_universe_walked_under_other_exclusions(env, monkeypatch):
    # an EXCLUDED edit after `universe` would otherwise reach preflight and freeze
    # with a B that was never walked under it
    monkeypatch.setattr(su, "EXCLUDED", {"ZZZUSDT": "ticker reuse"})
    assert _preflight(env) == 2
    assert not (env.study / "preflight_1d.json").exists()
    _set_universe(env, B=["AAAUSDT", "CCCUSDT"], declared_exclusions={"CCCUSDT": "x"})
    monkeypatch.setattr(su, "EXCLUDED", {"CCCUSDT": "x"})
    assert _preflight(env) == 2                        # B holds an excluded pair



# ------------------------------------------------ minors (M-g, M-j)

def test_require_note_compares_the_chained_text_with_the_committed_note(env, monkeypatch):
    text = _frozen(env)
    _chain(env, text)
    m = _manifest(env)
    copy = st.read_copy(env.reg, 6)
    assert cli.require_note(copy, m)[0] == 6
    f = env.layer / cli.NOTE_REL
    f.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))      # CRLF checkout: same
    assert cli.require_note(copy, m)[0] == 6
    f.write_bytes((text + "an edit after chaining\n").encode("utf-8"))
    with pytest.raises(cli.NoteMissing):
        cli.require_note(copy, m)
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []
    f.unlink()
    with pytest.raises(cli.NoteMissing):
        cli.require_note(copy, m)


def test_require_note_reads_the_note_file_of_the_tools_own_layer():
    assert cli.NOTE_FILE == cli.LAYER / cli.NOTE_REL


def test_write_json_refuses_nan(tmp_path):
    with pytest.raises(ValueError):
        cli._write_json(tmp_path / "x.json", {"a": float("nan")})
    assert not (tmp_path / "x.json").exists()


def test_analyse_writes_empty_strata_statistics_as_null(env, monkeypatch):
    _chain(env, _frozen(env))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": False, "reason": "f", "beats_hold": None})
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("analyse", *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 0
    raw = (env.layer / "docs" / "runs" / "2026-11-07-survivorship-1d.json").read_text(
        encoding="utf-8")

    def no_constant(c):
        raise AssertionError(f"non-JSON constant {c}")

    res = json.loads(raw, parse_constant=no_constant)
    assert res["stratum_means"]["N"] is None and res["stratum_means"]["D"] is None
    assert res["stratum_means"]["S"] is not None


def test_analyse_writes_a_nan_secondary_as_null(env, monkeypatch):
    # e.g. (m minus hold) over a universe whose every hold Sharpe is None
    _chain(env, _frozen(env))
    real = cli.ss.secondaries
    monkeypatch.setattr(cli.ss, "secondaries", lambda *a: dict(
        real(*a), m_minus_hold_gap=float("nan")))
    monkeypatch.setattr(h, "gauntlet_job", lambda item: {
        "sid": item[0]["strategy_id"], "passed": False, "reason": "f", "beats_hold": None})
    assert _run("run", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("gauntlet", *_base(env), "--registry", env.reg, "--workers", "1") == 0
    assert _run("analyse", *_base(env), "--registry", env.reg, "--layer-dir", env.layer) == 0
    res = json.loads((env.layer / "docs" / "runs" / "2026-11-07-survivorship-1d.json")
                     .read_text(encoding="utf-8"))
    assert res["secondaries"]["m_minus_hold_gap"] is None


# ---------------------------------------------- R4 preflight rules (Coen 2026-11-08)

def test_preflight_p1_volume_difference_is_reported_not_failed(env):
    bars = [dict(b) for b in env.series["AAAUSDT"]]
    bars[100]["volume"] += 7.0
    write_pipeline_csv(bars, env.pipe / "AAAUSDT_1d.csv")
    assert _preflight(env) == 0
    pf = _pf(env)
    assert pf["passed"] is True and not pf["checks"]["P1"]
    vol = pf["checks"]["P1_volume"]
    assert list(vol) == ["AAAUSDT"] and "volume" in vol["AAAUSDT"][0]


def test_preflight_p1_ohlc_difference_still_fails_beside_a_volume_one(env):
    bars = [dict(b) for b in env.series["AAAUSDT"]]
    bars[100]["volume"] += 7.0
    bars[101]["high"] += 0.000001
    write_pipeline_csv(bars, env.pipe / "AAAUSDT_1d.csv")
    assert _preflight(env) == 1
    pf = _pf(env)
    assert list(pf["checks"]["P1"]) == ["AAAUSDT"]
    assert all("volume" not in x for x in pf["checks"]["P1"]["AAAUSDT"])
    assert "AAAUSDT" in pf["checks"]["P1_volume"]


def _ccc_with_duplicate(env, differing):
    bars = _bars(START, STOP, 2.0)
    i = 200
    dup = dict(bars[i], volume=bars[i]["volume"] + 1.0) if differing else dict(bars[i])
    bars.insert(i + 1, dup)
    shutil.rmtree(env.study / "archive" / "CCCUSDT")
    _write_archive(env.study, "CCCUSDT", bars)
    _write_plan(env.study)
    return bars[i]["date"][:10]


def test_preflight_drops_an_exact_duplicate_row_and_reports_it(env):
    day = _ccc_with_duplicate(env, differing=False)
    assert _preflight(env) == 0
    pf = _pf(env)
    assert pf["passed"] is True and not pf["checks"]["P4"]
    note = pf["checks"]["dedup"]["CCCUSDT"]
    assert len(note) == 1 and day in note[0]
    assert pf["n_bars"]["CCCUSDT"] == len(_bars(START, STOP, 2.0))


def test_preflight_still_fails_a_duplicate_date_with_differing_values(env):
    _ccc_with_duplicate(env, differing=True)
    assert _preflight(env) == 1
    pf = _pf(env)
    assert "duplicate" in pf["checks"]["P4"]["CCCUSDT"][0]
    assert not pf["checks"].get("dedup")


def _ccc_gap(env):
    skip = {date(2023, 3, 1) + timedelta(days=i) for i in range(10)}
    shutil.rmtree(env.study / "archive" / "CCCUSDT")
    _write_archive(env.study, "CCCUSDT", _bars(START, STOP, 2.0, skip=skip))
    _write_plan(env.study)


def test_preflight_passes_a_declared_halt_gap(env, monkeypatch):
    _ccc_gap(env)
    monkeypatch.setattr(su, "HALTS", {"CCCUSDT": ("2023-02-28", "2023-03-11")})
    assert _preflight(env) == 0
    pf = _pf(env)
    assert not pf["checks"]["P4"]
    assert pf["declared"]["halts"] == {"CCCUSDT": ["2023-02-28", "2023-03-11"]}


@pytest.mark.parametrize("halt", [("2023-02-28", "2023-03-12"),
                                  ("2023-02-27", "2023-03-11"),
                                  ("2023-04-01", "2023-04-20")])
def test_preflight_fails_a_gap_that_does_not_match_the_declared_halt(env, monkeypatch, halt):
    _ccc_gap(env)
    monkeypatch.setattr(su, "HALTS", {"CCCUSDT": halt})
    assert _preflight(env) == 1
    assert any("gap of 11 days" in x for x in _pf(env)["checks"]["P4"]["CCCUSDT"])


def test_the_manifest_hash_changes_when_halts_change(env, monkeypatch):
    _chain(env, _frozen(env))
    copy = st.read_copy(env.reg, 6)
    before = cli._input_hashes(env.study, copy)
    monkeypatch.setattr(su, "HALTS", {"ZZZUSDT": ("2022-11-15", "2023-09-22")})
    after = cli._input_hashes(env.study, copy)
    assert sorted(k for k in before if before[k] != after[k]) == ["halts"]
    calls = _trap_engine(monkeypatch)
    assert _run("run", *_base(env), "--registry", env.reg) == 2
    assert calls == []


@pytest.mark.parametrize("bad", [None, ("2022-11-15", None), ("2023-09-22", "2022-11-15"),
                                 ("2022-11-15",), ("nope", "2023-09-22"),
                                 ("2022-11-15", "2023-09-22", "x")])
def test_freeze_refuses_a_malformed_halt(env, monkeypatch, bad):
    monkeypatch.setattr(su, "HALTS", {"ZZZUSDT": bad})
    assert _preflight(env) == 0
    assert _freeze(env) == 2
    assert not (env.study / "manifest.json").exists()


def test_freeze_refuses_halts_changed_since_preflight(env, monkeypatch):
    assert _preflight(env) == 0
    monkeypatch.setattr(su, "HALTS", {"ZZZUSDT": ("2022-11-15", "2023-09-22")})
    assert _freeze(env) == 2


def test_draft_note_lists_every_declared_halt(env, monkeypatch):
    monkeypatch.setattr(su, "HALTS", {"ZZZUSDT": ("2022-11-15", "2023-09-22")})
    text = _frozen(env)
    assert "- declared halt ZZZUSDT: last bar 2022-11-15, next bar 2023-09-22" in text
    m = _manifest(env)
    assert m["inputs"]["halts"] == {"ZZZUSDT": ["2022-11-15", "2023-09-22"]}
    assert f"halts: {m['hashes']['halts']}" in text


def test_draft_note_says_no_halts_when_none_are_declared(env):
    assert "- halts: none" in _frozen(env)

