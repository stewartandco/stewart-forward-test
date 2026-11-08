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
    """Coen accepts, Coen overreach rejects, Coen duplicate rejects, and auto-reviewed cards."""
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
    assert {cid for cid, (_, lab) in labels.items() if lab == "accepted"} == set(ids["accept"])
    assert {cid for cid, (_, lab) in labels.items() if lab == "overreach"} == set(ids["overreach"])
    assert not set(ids["dupe"]) & set(labels)            # fingerprint work, not a panel judgement
    assert not set(ids["auto"]) & set(labels)            # the panel's own output is not ground truth


def test_a_later_auto_review_overrides_an_earlier_coen_label(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=1, n_overreach=0, n_dupe=0, n_auto=0)
    reg.review_card(ids["accept"][0], "rejected", "auto-d44", reject_reason="claim_not_supported")
    assert E.load_labels(reg) == {}


def test_sample_takes_every_overreach_and_a_seeded_accept_draw(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=10, n_overreach=3)
    labels = E.load_labels(reg)
    a = E.select_sample(labels, n_accepts=4, seed=7)
    b = E.select_sample(labels, n_accepts=4, seed=7)
    c = E.select_sample(labels, n_accepts=4, seed=8)
    assert a == b                                        # reproducible
    assert set(ids["overreach"]) <= set(a)               # every governed case
    assert sum(1 for cid in a if labels[cid][1] == "accepted") == 4
    assert a != c
    assert len(E.select_sample(labels, n_accepts=99, seed=7)) == 13   # capped at what exists


# ---------------------------------------------------------------- scoring

def _panel(decisions: dict[str, bool], served="claude-served"):
    """A fake review_fn: accept/reject per claim text, 3 votes each."""
    def review(card):
        ok = decisions[card["claim"]]
        return [{"accept": ok, "reason": "r", "served_model": served}] * 3
    return review


def test_a_false_accept_fails_the_run_and_names_the_card(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=4, n_overreach=2, n_dupe=0, n_auto=0)
    labels = E.load_labels(reg)
    sample = E.select_sample(labels, n_accepts=4, seed=1)
    bad = ids["overreach"][0]
    verdicts = {labels[cid][0]["claim"]: (cid != bad and labels[cid][1] == "accepted") or cid == bad
                for cid in sample}
    out = E.evaluate({cid: labels[cid] for cid in sample}, _panel(verdicts), can_spend=lambda: True,
                     halted=lambda: None)
    assert out["complete"] and out["false_accepts"] == [bad] and not out["pass"]


def test_a_clean_run_passes_and_reports_false_rejects_with_an_interval(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=4, n_overreach=2, n_dupe=0, n_auto=0)
    labels = E.load_labels(reg)
    sample = E.select_sample(labels, n_accepts=4, seed=1)
    lost = ids["accept"][0]
    verdicts = {labels[cid][0]["claim"]: labels[cid][1] == "accepted" and cid != lost for cid in sample}
    out = E.evaluate({cid: labels[cid] for cid in sample}, _panel(verdicts), can_spend=lambda: True,
                     halted=lambda: None)
    assert out["pass"] and out["false_accepts"] == []
    assert out["false_rejects"] == [lost] and out["n_accept"] == 4 and out["n_overreach"] == 2
    lo, hi = out["false_reject_rate_ci95"]
    assert 0 < lo < out["false_reject_rate"] == 0.25 < hi < 1
    assert out["served_models"] == ["claude-served"]


def test_an_incomplete_panel_counts_as_the_reject_it_is(tmp_path):
    # D44: a panel short after bounded re-asks REJECTS, so on an overreach card it is not a false accept
    reg, ids = _labelled_registry(tmp_path, n_accept=0, n_overreach=1, n_dupe=0, n_auto=0)
    labels = E.load_labels(reg)
    out = E.evaluate(labels, lambda card: [], can_spend=lambda: True, halted=lambda: None)
    assert out["pass"] and out["false_accepts"] == [] and out["bases"]["incomplete_panel"] == 1


def test_a_halt_or_the_budget_stops_the_run_and_it_cannot_pass(tmp_path):
    reg, ids = _labelled_registry(tmp_path, n_accept=3, n_overreach=1, n_dupe=0, n_auto=0)
    labels = E.load_labels(reg)
    calls = []

    def review(card):
        calls.append(card["card_id"])
        return [{"accept": False, "reason": "r", "served_model": "s"}] * 3

    out = E.evaluate(labels, review, can_spend=lambda: len(calls) < 2, halted=lambda: None)
    assert out["stopped"] == "budget" and not out["complete"] and not out["pass"] and len(calls) == 2
    calls.clear()
    out = E.evaluate(labels, review, can_spend=lambda: True, halted=lambda: "coen: spend review")
    assert out["stopped"] == "halted" and calls == [] and not out["pass"]


def test_a_run_stopped_after_clean_overreach_cards_still_cannot_pass(tmp_path):
    # overreach cards come first; stop after they all rejected cleanly: partial is never a pass
    reg, ids = _labelled_registry(tmp_path, n_accept=3, n_overreach=1, n_dupe=0, n_auto=0)
    labels = E.load_labels(reg)
    order = E.select_sample(labels, n_accepts=3, seed=1)
    calls = []

    def review(card):
        calls.append(1)
        return [{"accept": False, "reason": "r", "served_model": "s"}] * 3

    out = E.evaluate({cid: labels[cid] for cid in order}, review, can_spend=lambda: len(calls) < 2,
                     halted=lambda: None)
    assert out["n_overreach"] == 1 and out["false_accepts"] == [] and out["stopped"] == "budget"
    assert not out["complete"] and not out["pass"]


def test_wilson_interval():
    lo, hi = E.wilson(0, 150)
    assert lo == 0 and 0.02 < hi < 0.03                 # 0 of 150: upper bound about 2.5%
    lo, hi = E.wilson(15, 150)
    assert 0.06 < lo < 0.1 < hi < 0.16
    assert E.wilson(0, 0) == (0.0, 1.0)


# ---------------------------------------------------------------- results and the gate

def _doc(**kw):
    base = {"prompt_sha": triage_batch.REVIEW_PROMPT_SHA, "model": triage_batch.DEFAULT_MODEL,
            "complete": True, "pass": True, "false_accepts": [], "n_overreach": 104, "n_accept": 150}
    base.update(kw)
    return base


def test_results_are_written_per_prompt_and_model_and_the_gate_reads_them(tmp_path):
    p = E.write_result(_doc(), tmp_path)
    assert p.parent == tmp_path and p.name.startswith(f"{triage_batch.REVIEW_PROMPT_SHA}__{triage_batch.DEFAULT_MODEL}__")
    ok, why = E.gate_status(tmp_path, triage_batch.REVIEW_PROMPT_SHA, triage_batch.DEFAULT_MODEL)
    assert ok, why


@pytest.mark.parametrize("bad", [
    {"pass": False}, {"complete": False}, {"false_accepts": ["c1"]},
    {"prompt_sha": "0" * 16}, {"model": "claude-other"}, {"n_overreach": 0},
])
def test_the_gate_refuses_anything_but_a_complete_clean_run_for_this_prompt_and_model(tmp_path, bad):
    E.write_result(_doc(**bad), tmp_path)
    ok, why = E.gate_status(tmp_path, triage_batch.REVIEW_PROMPT_SHA, triage_batch.DEFAULT_MODEL)
    assert not ok and why


def test_a_renamed_result_for_another_prompt_does_not_satisfy_the_gate(tmp_path):
    p = E.write_result(_doc(prompt_sha="0" * 16), tmp_path)
    p.rename(tmp_path / p.name.replace("0" * 16, triage_batch.REVIEW_PROMPT_SHA))
    ok, why = E.gate_status(tmp_path, triage_batch.REVIEW_PROMPT_SHA, triage_batch.DEFAULT_MODEL)
    assert not ok


def test_default_model_is_what_the_loop_runs():
    # the loop passes no --model, so triage runs on its argparse default: it must be DEFAULT_MODEL
    src = Path(triage_batch.__file__).read_text(encoding="utf-8")
    assert 'ap.add_argument("--model", default=DEFAULT_MODEL)' in src
    loop_src = (Path(triage_batch.__file__).parent / "loop.py").read_text(encoding="utf-8")
    start = loop_src.index('triage_argv = [py, "-m", "pipeline.triage_batch"')
    assert "--model" not in loop_src[start:start + 200]


def test_the_gate_with_no_results_says_how_to_run_one(tmp_path):
    ok, why = E.gate_status(tmp_path / "none", "abc", "m")
    assert not ok and "eval_triage" in why


def test_the_meter_books_eval_spend_under_its_own_purpose():
    seen = []

    class Meter:
        def can_spend(self):
            return True

        def record_call(self, model, usage, purpose, agent=None):
            seen.append((purpose, agent))

    m = E.EvalMeter(Meter())
    m.record_call("claude-sonnet-5", object(), "triage", agent="pipeline")
    assert seen == [("triage-eval", "pipeline")] and m.can_spend()


def test_dry_run_spends_nothing_and_reports_the_sample(tmp_path, capsys, monkeypatch):
    reg, ids = _labelled_registry(tmp_path, n_accept=5, n_overreach=2)
    monkeypatch.setattr(E, "_live_review", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spent")))
    rc = E.run(["--layer", str(tmp_path), "--registry", str(tmp_path / "registry_log.jsonl"),
                "--n-accepts", "3", "--out-dir", str(tmp_path / "out"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and "2 overreach" in out and "3 accept" in out and "USD" in out
    assert not (tmp_path / "out").exists()
