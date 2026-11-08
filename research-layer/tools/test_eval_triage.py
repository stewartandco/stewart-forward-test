"""tools/eval_triage.py (harness audit 2026-11-08, Tier 2 #9): the live triage panel replayed on
Coen's own card judgements. Offline: tmp registries and fake panels only; nothing here spends."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import reader, triage_batch
from pipeline.registry import Registry
from tools import eval_triage as E

RAW = {"claim": "Momentum persists over 12 months.", "quote": "returns persist for twelve months",
       "locator": "p.3", "asset_classes": ["crypto"], "topics": ["momentum"], "horizon": "multi_month",
       "testability_score": 0.8, "data_required": ["daily close"], "notes": ""}
SOURCE = {"type": "paper", "title": "A paper", "credibility_tier": "practitioner", "url": "https://example.org/p"}


def _card(i: int) -> dict:
    return reader.build_card(dict(RAW, claim=f"claim {i}"), SOURCE, "m", f"r{i}")


def _labelled_registry(tmp_path, n_accept=6, n_overreach=3, n_dupe=2, n_auto=2):
    reg = Registry(tmp_path / "registry_log.jsonl")
    ids = {"accept": [], "overreach": [], "dupe": [], "auto": []}
    i = 0
    for kind, n in (("accept", n_accept), ("overreach", n_overreach), ("dupe", n_dupe), ("auto", n_auto)):
        for _ in range(n):
            c = _card(i); i += 1
            reg.register_card(c)
            cid = c["card_id"]
            if kind == "accept":
                reg.review_card(cid, "accepted", "coen")
            elif kind == "overreach":
                reg.review_card(cid, "rejected", "coen", reject_reason="claim_not_supported")
            elif kind == "dupe":
                reg.review_card(cid, "rejected", "coen", reject_reason="duplicate")
            else:
                reg.review_card(cid, "accepted", "auto-d44")
            ids[kind].append(cid)
    return reg, ids


# ---------------------------------------------------------------- labels and sample

def test_labels_are_coens_last_judgement_minus_duplicates(tmp_path):
    reg, ids = _labelled_registry(tmp_path)
    labels = E.load_labels(reg)
    assert {cid for cid, v in labels.items() if v[1] == "accepted"} == set(ids["accept"])
    assert {cid for cid, v in labels.items() if v[1] == "overreach"} == set(ids["overreach"])
    assert not set(ids["dupe"]) & set(labels)
    assert not set(ids["auto"]) & set(labels)
    assert {v[2] for v in labels.values()} == {"panel_flagged"}     # reviewed after D31 (today)


def test_origin_splits_on_the_d31_date():
    class Reg:
        def entries(self):
            return [{"entry_type": "card_reviewed", "ts_utc": "2026-08-14T01:00:00Z",
                     "payload": {"card_id": "a", "status": "rejected", "reviewed_by": "coen",
                                 "reject_reason": "claim_not_supported"}},
                    {"entry_type": "card_reviewed", "ts_utc": "2026-09-02T01:00:00Z",
                     "payload": {"card_id": "b", "status": "rejected", "reviewed_by": "coen",
                                 "reject_reason": "claim_not_supported"}}]

        def cards(self):
            return {"a": {"claim": "a"}, "b": {"claim": "b"}}
    labels = E.load_labels(Reg())
    assert labels["a"][2] == "independent" and labels["b"][2] == "panel_flagged"


def test_a_later_auto_review_overrides_an_earlier_coen_label(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=1, n_overreach=0, n_dupe=0, n_auto=0)
    reg.review_card(ids["accept"][0], "rejected", "auto-d44", reject_reason="claim_not_supported")
    assert E.load_labels(reg) == {}


def test_sample_takes_every_overreach_and_a_seeded_accept_draw(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=10, n_overreach=3)
    labels = E.load_labels(reg)
    a = E.select_sample(labels, n_accepts=4, seed=7)
    assert a == E.select_sample(labels, n_accepts=4, seed=7)
    assert set(ids["overreach"]) <= set(a)
    assert sum(1 for cid in a if labels[cid][1] == "accepted") == 4
    assert a != E.select_sample(labels, n_accepts=4, seed=8)
    assert len(E.select_sample(labels, n_accepts=99, seed=7)) == 13


def test_label_digest_moves_with_any_label():
    base = {"a": ({}, "overreach", "independent"), "b": ({}, "accepted", "independent")}
    d = E.label_digest(base)
    assert E.label_digest({**base, "c": ({}, "accepted", "x")}) != d
    assert E.label_digest({"a": ({}, "accepted", "x"), "b": ({}, "overreach", "x")}) != d


# ---------------------------------------------------------------- scoring

def _panel(decisions: dict[str, bool], served="claude-served"):
    def review(card):
        ok = decisions[card["claim"]]
        return [{"accept": ok, "reason": "r", "served_model": served}] * 3
    return review


def _scored(tmp_path, n_accept=4, n_overreach=2):
    reg, ids = _labelled_registry(tmp_path, n_accept=n_accept, n_overreach=n_overreach, n_dupe=0, n_auto=0)
    labels = E.load_labels(reg)
    order = E.select_sample(labels, n_accepts=n_accept, seed=1)
    return ids, labels, {cid: labels[cid] for cid in order}


def test_a_false_accept_fails_the_run_and_names_the_card(tmp_path):
    ids, labels, sample = _scored(tmp_path)
    bad = ids["overreach"][0]
    verdicts = {labels[c][0]["claim"]: labels[c][1] == "accepted" or c == bad for c in sample}
    out = E.evaluate(sample, _panel(verdicts), can_spend=lambda: True, halted=lambda: None)
    assert out["complete"] and out["false_accepts"] == [bad] and not out["pass"]
    assert out["false_accepts_by_origin"]["panel_flagged"] == [bad]


def test_a_clean_run_passes_and_reports_false_rejects_with_an_interval(tmp_path):
    ids, labels, sample = _scored(tmp_path)
    lost = ids["accept"][0]
    verdicts = {labels[c][0]["claim"]: labels[c][1] == "accepted" and c != lost for c in sample}
    out = E.evaluate(sample, _panel(verdicts), can_spend=lambda: True, halted=lambda: None)
    assert out["pass"] and out["false_accepts"] == []
    assert out["false_rejects"] == [lost] and out["n_accept"] == 4 and out["n_overreach"] == 2
    lo, hi = out["false_reject_rate_ci95"]
    assert 0 < lo < out["false_reject_rate"] == 0.25 < hi < 1
    assert out["served_models"] == ["claude-served"] and out["incomplete_panel_rate"] == 0


def test_too_many_incomplete_panels_fails_a_run_with_no_false_accept(tmp_path):
    # a broken model whose replies never parse rejects everything; Coen's floor fails it
    ids, labels, sample = _scored(tmp_path, n_accept=4, n_overreach=2)
    out = E.evaluate(sample, lambda card: [], can_spend=lambda: True, halted=lambda: None)
    assert out["false_accepts"] == [] and out["incomplete_panel_rate"] == 1.0 and not out["pass"]


def test_a_few_incomplete_panels_under_the_floor_still_pass(tmp_path):
    ids, labels, sample = _scored(tmp_path, n_accept=10, n_overreach=1)
    first = next(iter(sample))
    verdicts = {labels[c][0]["claim"]: labels[c][1] == "accepted" for c in sample}

    def review(card):
        return [] if card["card_id"] == first else _panel(verdicts)(card)
    out = E.evaluate(sample, review, can_spend=lambda: True, halted=lambda: None)
    assert out["incomplete_panel_rate"] == round(1 / 11, 4) and out["pass"]


def test_a_halt_or_the_budget_stops_the_run_and_it_cannot_pass(tmp_path):
    ids, labels, sample = _scored(tmp_path, n_accept=3, n_overreach=1)
    calls = []

    def review(card):
        calls.append(1)
        return [{"accept": False, "reason": "r", "served_model": "s"}] * 3

    out = E.evaluate(sample, review, can_spend=lambda: len(calls) < 2, halted=lambda: None)
    assert out["n_overreach"] == 1 and out["false_accepts"] == [] and out["stopped"] == "budget"
    assert not out["complete"] and not out["pass"] and len(calls) == 2
    calls.clear()
    out = E.evaluate(sample, review, can_spend=lambda: True, halted=lambda: "coen: spend review")
    assert out["stopped"] == "halted" and calls == [] and not out["pass"]


def test_wilson_interval():
    lo, hi = E.wilson(0, 150)
    assert lo == 0 and 0.02 < hi < 0.03
    lo, hi = E.wilson(15, 150)
    assert 0.06 < lo < 0.1 < hi < 0.16
    assert E.wilson(0, 0) == (0.0, 1.0)


# ---------------------------------------------------------------- the rule key

def test_rule_sha_moves_with_the_decision_rule_not_only_the_prompt(monkeypatch):
    base = E.rule_sha()
    monkeypatch.setattr(triage_batch, "PANEL_SIZE", 5)
    assert E.rule_sha() != base
    monkeypatch.undo()
    monkeypatch.setattr(triage_batch, "MAX_VOTE_ATTEMPTS", 9)
    assert E.rule_sha() != base
    monkeypatch.undo()

    def majority(votes):
        return ("accepted", "majority")
    monkeypatch.setattr(triage_batch, "panel_verdict", majority)
    assert E.rule_sha() != base
    monkeypatch.undo()
    monkeypatch.setattr(triage_batch, "REVIEW_PROMPT_SHA", "0" * 16)
    assert E.rule_sha() != base


# ---------------------------------------------------------------- results and the gate

RULE = "r" * 16
MODEL = "claude-sonnet-5"


def _doc(**kw):
    base = {"rule_sha": RULE, "model": MODEL, "complete": True, "pass": True, "false_accepts": [],
            "label_digest": E.LABEL_PIN["digest"], "n_overreach": E.LABEL_PIN["n_overreach"],
            "n_accept": E.N_ACCEPTS, "incomplete_panel_rate": 0.0}
    base.update(kw)
    return base


def test_two_clean_runs_open_the_gate_and_one_does_not(tmp_path):
    E.write_result(_doc(), tmp_path)
    ok, why = E.gate_status(tmp_path, RULE, MODEL)
    assert not ok and "1 of 2" in why
    E.write_result(_doc(), tmp_path)
    ok, why = E.gate_status(tmp_path, RULE, MODEL)
    assert ok, why


def test_any_run_with_a_false_accept_blocks_the_key_for_good(tmp_path):
    for _ in range(3):
        E.write_result(_doc(), tmp_path)
    E.write_result(_doc(**{"pass": False, "false_accepts": ["c1"]}), tmp_path)
    ok, why = E.gate_status(tmp_path, RULE, MODEL)
    assert not ok and "blocked for good" in why


@pytest.mark.parametrize("bad", [
    {"pass": False}, {"complete": False}, {"label_digest": "0" * 16}, {"n_overreach": 1},
    {"n_accept": 10}, {"incomplete_panel_rate": 0.5}, {"model": "claude-other"},
])
def test_runs_on_the_wrong_labels_or_unclean_runs_do_not_count(tmp_path, bad):
    E.write_result(_doc(), tmp_path)
    E.write_result(_doc(**bad), tmp_path)
    ok, why = E.gate_status(tmp_path, RULE, MODEL)
    assert not ok


def test_a_renamed_result_for_another_rule_does_not_count(tmp_path):
    E.write_result(_doc(), tmp_path)
    p = E.write_result(_doc(rule_sha="0" * 16), tmp_path)
    p.rename(tmp_path / p.name.replace("0" * 16, RULE))
    ok, why = E.gate_status(tmp_path, RULE, MODEL)
    assert not ok


def test_the_gate_with_no_results_says_how_to_run_one(tmp_path):
    ok, why = E.gate_status(tmp_path / "none", "abc", "m")
    assert not ok and "eval_triage" in why


def test_model_ids_with_path_characters_make_safe_filenames(tmp_path):
    p = E.write_result(_doc(model="vendor/model:1"), tmp_path)
    assert p.parent == tmp_path and "/" not in p.name and ":" not in p.name


def test_default_model_is_what_the_loop_runs():
    src = Path(triage_batch.__file__).read_text(encoding="utf-8")
    assert 'ap.add_argument("--model", default=DEFAULT_MODEL)' in src
    loop_src = (Path(triage_batch.__file__).parent / "loop.py").read_text(encoding="utf-8")
    start = loop_src.index('triage_argv = [py, "-m", "pipeline.triage_batch"')
    assert "--model" not in loop_src[start:start + 200]


def test_the_meter_books_eval_spend_under_its_own_purpose_and_the_pipeline_agent():
    seen = []

    class Meter:
        def can_spend(self):
            return True

        def record_call(self, model, usage, purpose, agent=None):
            seen.append((purpose, agent))

    m = E.EvalMeter(Meter())
    m.record_call("claude-sonnet-5", object(), "triage", agent="pipeline")
    m.record_call("claude-sonnet-5", object(), "triage")              # agent dropped by a caller
    assert seen == [("triage-eval", "pipeline")] * 2 and m.can_spend()


# ---------------------------------------------------------------- the CLI guards (nothing spends)

def _no_spend(monkeypatch):
    monkeypatch.setattr(E, "_live_review", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spent")))


def _pinned_to(monkeypatch, reg):
    labels = E.load_labels(reg)
    monkeypatch.setitem(E.LABEL_PIN, "digest", E.label_digest(labels))


def test_dry_run_spends_nothing_and_reports_the_sample(tmp_path, capsys, monkeypatch):
    reg, ids = _labelled_registry(tmp_path, n_accept=5, n_overreach=2)
    _no_spend(monkeypatch)
    rc = E.run(["--layer", str(tmp_path), "--n-accepts", "3", "--out-dir", str(tmp_path / "out"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "2 overreach" in out and "3 accept" in out and "USD" in out and "DOES NOT MATCH" in out
    assert not (tmp_path / "out").exists()


def test_a_paid_run_on_unpinned_labels_is_refused(tmp_path, capsys, monkeypatch):
    reg, ids = _labelled_registry(tmp_path, n_accept=5, n_overreach=2)
    _no_spend(monkeypatch)
    assert E.run(["--layer", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 2
    assert "re-pin" in capsys.readouterr().out


def test_a_paid_run_without_the_live_ledger_is_refused(tmp_path, capsys, monkeypatch):
    reg, ids = _labelled_registry(tmp_path, n_accept=5, n_overreach=2)
    _pinned_to(monkeypatch, reg)
    _no_spend(monkeypatch)
    assert E.run(["--layer", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 2
    assert "no ledger" in capsys.readouterr().out
    assert not (tmp_path / "logs").exists()                       # refusing created nothing


def test_a_paid_run_during_a_cycle_or_under_a_held_chain_lock_is_refused(tmp_path, capsys, monkeypatch):
    reg, ids = _labelled_registry(tmp_path, n_accept=5, n_overreach=2)
    _pinned_to(monkeypatch, reg)
    _no_spend(monkeypatch)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "budget_ledger.jsonl").write_text("", encoding="utf-8")
    import tools.reextract_shadow as RS
    monkeypatch.setattr(RS, "ensure_no_cycle_running",
                        lambda logs: (_ for _ in ()).throw(RuntimeError("loop cycle running")))
    assert E.run(["--layer", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 2
    assert "loop cycle running" in capsys.readouterr().out
    monkeypatch.undo()
    _pinned_to(monkeypatch, reg)
    _no_spend(monkeypatch)
    monkeypatch.setattr(RS, "ensure_no_cycle_running", lambda logs: None)
    from pipeline.chainlock import ChainLock
    held = ChainLock(tmp_path / "logs", holder="someone", purpose="t")
    held.acquire()
    try:
        assert E.run(["--layer", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 2
    finally:
        held.release()
    assert "REFUSED" in capsys.readouterr().out


def test_a_paid_run_releases_the_lock_and_writes_one_result(tmp_path, capsys, monkeypatch):
    reg, ids = _labelled_registry(tmp_path, n_accept=4, n_overreach=2)
    _pinned_to(monkeypatch, reg)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "budget_ledger.jsonl").write_text("", encoding="utf-8")
    import tools.reextract_shadow as RS
    monkeypatch.setattr(RS, "ensure_no_cycle_running", lambda logs: None)
    labels = E.load_labels(reg)
    verdicts = {v[0]["claim"]: v[1] == "accepted" for v in labels.values()}

    class M:
        def can_spend(self):
            return True
    monkeypatch.setattr(E, "_live_review", lambda model, layer: (_panel(verdicts), M()))
    rc = E.run(["--layer", str(tmp_path), "--n-accepts", "4", "--out-dir", str(tmp_path / "out")])
    files = list((tmp_path / "out").glob("*.json"))
    assert rc == 0 and len(files) == 1
    doc = json.loads(files[0].read_text(encoding="utf-8"))
    assert doc["rule_sha"] == E.rule_sha() and doc["label_digest"] == E.LABEL_PIN["digest"] and doc["pass"]
    assert not (tmp_path / "logs" / "chain.lock").exists()           # released
