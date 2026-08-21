"""Tests for the quarantine -> live gate (quarantine-live-protocol-v1, entry 2515)."""
import pytest

from .livegate import (forward_series, closed_forward_trades, rebuilt_cone,
                       kill_verdict, forward_psr, benjamini_hochberg, assess,
                       KILL_PCTILE, MIN_FORWARD_TRADES, BH_Q, CONE_PATHS)


def row(sid, date, asset, equity, action="hold"):
    return {"strategy_id": sid, "date": date, "asset": asset,
            "equity": equity, "action": action, "price": 1.0,
            "position_frac": 0.0}


# ---------------- reading the forward record ----------------

def test_forward_series_is_the_mean_across_assets_per_date():
    """quarantine.observe_day records one REBASED equity per asset per day.
    The strategy's forward curve combines them the same way engine.run_spec
    does -- equal-weight mean -- or the gate would judge a different book
    than the one the gauntlet modelled."""
    rows = [row("s", "2026-01-01", "BTCUSD", 1.0), row("s", "2026-01-01", "ETHUSD", 1.0),
            row("s", "2026-01-02", "BTCUSD", 1.10), row("s", "2026-01-02", "ETHUSD", 0.90)]
    assert forward_series(rows) == [("2026-01-01", 1.0), ("2026-01-02", 1.0)]


def test_forward_series_ignores_a_date_missing_an_asset():
    """A half-recorded day would silently reweight the book toward whichever
    asset reported. Drop it rather than average over a different universe."""
    rows = [row("s", "2026-01-01", "BTCUSD", 1.0), row("s", "2026-01-01", "ETHUSD", 1.0),
            row("s", "2026-01-02", "BTCUSD", 1.5)]
    assert forward_series(rows) == [("2026-01-01", 1.0)]


def test_closed_forward_trades_counts_exits_across_assets():
    rows = [row("s", "2026-01-01", "BTCUSD", 1.0, "enter_long"),
            row("s", "2026-01-02", "BTCUSD", 1.0, "exit"),
            row("s", "2026-01-02", "ETHUSD", 1.0, "exit"),
            row("s", "2026-01-03", "BTCUSD", 1.0, "hold")]
    assert closed_forward_trades(rows) == 2


# ---------------- the kill arm ----------------

def test_the_cone_is_rebuilt_at_the_matched_trade_count():
    """Not the cone recorded in the gauntlet verdict, which spans the FULL
    train trade count -- quarantine.py's own CONE_CAVEAT says so."""
    contribs = [0.05, -0.02, 0.03, -0.01] * 25
    small = rebuilt_cone(contribs, n_trades=MIN_FORWARD_TRADES, seed=1)
    large = rebuilt_cone(contribs, n_trades=60, seed=1)
    assert small["n_trades"] == MIN_FORWARD_TRADES and large["n_trades"] == 60
    assert len(small["terminals"]) == CONE_PATHS
    # more trades compounds a positive edge further out
    assert large["p01"] > small["p01"] or large["terminals"] != small["terminals"]


def test_cone_is_deterministic_for_a_seed():
    c = [0.05, -0.02, 0.03]
    assert rebuilt_cone(c, 5, seed=7) == rebuilt_cone(c, 5, seed=7)
    assert rebuilt_cone(c, 5, seed=7) != rebuilt_cone(c, 5, seed=8)


def test_a_catastrophic_forward_record_is_buried():
    contribs = [0.05, -0.02, 0.03, -0.01] * 25
    cone = rebuilt_cone(contribs, n_trades=10, seed=3)
    assert kill_verdict(0.05, cone) is True          # equity down 95%


def test_an_ordinary_forward_record_is_not_buried():
    contribs = [0.05, -0.02, 0.03, -0.01] * 25
    cone = rebuilt_cone(contribs, n_trades=10, seed=3)
    assert kill_verdict(1.02, cone) is False


def test_the_kill_line_is_the_first_percentile_not_the_fifth():
    """Burial is terminal and irreversible on an append-only chain. A gate
    that buries one strategy in twenty by chance is not one to own."""
    assert KILL_PCTILE == 0.01


def test_the_kill_arm_cannot_fire_on_too_few_trades():
    """The mirror of protocol-v4's rule that a gate passing on absence of
    evidence is not a gate: this gate BURIES, so silence must not condemn."""
    contribs = [0.05, -0.02, 0.03]
    assert rebuilt_cone(contribs, n_trades=MIN_FORWARD_TRADES - 1, seed=1) is None
    assert kill_verdict(0.001, None) is False


# ---------------- the graduation arm ----------------

def test_forward_psr_is_measured_against_zero_not_a_deflated_benchmark():
    """protocol-v3 put a DEFLATED Sharpe here. quarantine-live-protocol-v1
    removed the deflation: a forward record is one pre-registered hypothesis,
    not a selected maximum, so deflating it counts the same search twice."""
    # deliberately MARGINAL: a strong series saturates PSR at 1.0 and the
    # comparison below becomes 1.0 < 1.0, a test that cannot fail.
    rets = [0.002, -0.0015] * 40
    plain = forward_psr(rets)
    assert plain == pytest.approx(forward_psr(rets, sr_star=0.0))
    assert 0.0 < plain < 1.0, "fixture must not saturate or the test is vacuous"
    # a deflated benchmark can only make it harder
    assert forward_psr(rets, sr_star=0.05) < plain


def test_forward_psr_of_a_flat_record_is_not_significant():
    assert forward_psr([0.0] * 200) < 0.95


# ---------------- the cohort rule ----------------

def test_benjamini_hochberg_known_answers():
    # every p exactly on its own k*q/m line -> all reject
    assert benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05], q=0.05) == {0, 1, 2, 3, 4}
    # nothing close -> none
    assert benjamini_hochberg([0.9, 0.8], q=0.05) == set()
    # one strong among two: k=1 needs p <= 0.025
    assert benjamini_hochberg([0.001, 0.9], q=0.05) == {0}
    assert benjamini_hochberg([], q=0.05) == set()


def test_bh_is_adaptive_the_lone_survivor_faces_bonferroni():
    """The property that makes BH the right choice: a uniformly strong cohort
    lets its weakest member through at q, while a lone strong strategy among
    many faces q/m, which is Bonferroni's bar."""
    m = 20
    lone = [0.05 / m] + [0.5] * (m - 1)
    assert benjamini_hochberg(lone, q=0.05) == {0}
    just_above = [0.05 / m * 1.01] + [0.5] * (m - 1)
    assert benjamini_hochberg(just_above, q=0.05) == set()
    # uniformly strong: the weakest still clears at q
    assert 19 in benjamini_hochberg([0.001 * (i + 1) for i in range(m)], q=0.05)


def test_bh_q_is_five_percent():
    assert BH_Q == 0.05


# ---------------- assessment ----------------

def make_case(sid, days, equity_end, trades, contribs):
    rows = []
    for i in range(days):
        d = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        e = 1.0 + (equity_end - 1.0) * (i + 1) / days
        act = "exit" if i < trades else "hold"
        rows += [row(sid, d, "BTCUSD", e, act), row(sid, d, "ETHUSD", e, act)]
    return {"sid": sid, "rows": rows, "train_contributions": contribs}


def test_assessment_requires_the_minimum_window():
    case = make_case("short", 30, 1.2, 6, [0.05, -0.02] * 40)
    out = assess([case], min_days=60)
    assert out["short"]["eligible"] is False
    assert out["short"]["verdict"] == "hold"


def test_a_catastrophic_record_is_buried_and_never_graduates():
    case = make_case("bad", 90, 0.02, 20, [0.05, -0.02, 0.03, -0.01] * 25)
    out = assess([case], min_days=60)
    assert out["bad"]["verdict"] == "graveyard"


def test_assess_writes_nothing_and_returns_a_report():
    """The gate computes; chaining is a separate, Coen-gated step."""
    case = make_case("x", 90, 1.1, 20, [0.05, -0.02] * 40)
    out = assess([case], min_days=60)
    assert set(out["x"]) >= {"eligible", "verdict", "psr", "forward_trades",
                             "forward_days", "cone_p01", "terminal"}
    assert out["x"]["verdict"] in {"hold", "graveyard", "live"}


# ---------------- graduation actually fires ----------------

def case_with_sharpe(sid, days, ann_sharpe, trades, contribs, seed=1):
    """A forward record whose REALIZED annualized Sharpe is exactly
    `ann_sharpe`.

    Two earlier attempts at this fixture were wrong in instructive ways.
    make_case above ramps equity linearly, giving zero-variance returns and a
    Sharpe of exactly 0 -- fine for the kill arm, useless here. A plain
    gauss(mu, sd) draw is worse: at 200 days the standard error of the mean is
    large enough that a nominally weak strategy lands wherever the seed puts
    it, so the test passes or fails on luck rather than on the gate. Here the
    draws are standardized and rescaled, so the statistic under test is pinned.
    """
    import random as _r
    rng = _r.Random(seed)
    xs = [rng.gauss(0, 1) for _ in range(days)]
    m = sum(xs) / days
    sd = (sum((x - m) ** 2 for x in xs) / (days - 1)) ** 0.5
    target_sd = 0.01
    target_mu = ann_sharpe / (365 ** 0.5) * target_sd
    rets = [target_mu + target_sd * (x - m) / sd for x in xs]
    rows, eq = [], 1.0
    for i, r_i in enumerate(rets):
        d = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
        eq *= 1 + r_i
        act = "exit" if i < trades else "hold"
        rows += [row(sid, d, "BTCUSD", eq, act), row(sid, d, "ETHUSD", eq, act)]
    return {"sid": sid, "rows": rows, "train_contributions": contribs}


CONTRIBS = [0.05, -0.02, 0.03, -0.01] * 25      # mean +1.25% per trade
# A near-zero-edge train distribution, so the cone centres on 1.0 and the KILL
# arm stays out of the way. Needed to test the graduation arm in isolation:
# with CONTRIBS the cone's p01 rises ABOVE 1.0 by 30 trades (see
# test_the_kill_arm_buries_a_flat_record_against_a_strong_train_edge), so a
# flat forward record is buried before the graduation arm is ever reached.
FLAT_CONTRIBS = [0.02, -0.02, 0.015, -0.015] * 25


def test_the_kill_arm_buries_a_flat_record_against_a_strong_train_edge():
    """PINNED DELIBERATELY, because it is stronger than the phrase "grossly
    outside" suggests and should not be discovered by surprise later.

    The cone is the terminal distribution implied by the strategy's OWN train
    trades. When those carry a real edge, the 1st percentile rises above 1.0 as
    trades accumulate: 0.922 at 10 trades, 1.010 at 30, 1.221 at 60. So a
    strategy that merely goes FLAT is buried once it has traded enough, because
    a book with a genuine +1.25%-per-trade edge going nowhere over 60 trades is
    real evidence the edge is gone. The chained note specifies exactly this
    test -- below the first percentile of that distribution -- so the behaviour
    is correct; it is the surrounding prose that reads milder than the rule.
    """
    strong_edge = rebuilt_cone(CONTRIBS, n_trades=60, seed=1)
    assert strong_edge["p01"] > 1.0
    assert kill_verdict(1.0, strong_edge) is True     # flat is not survivable

    no_edge = rebuilt_cone(FLAT_CONTRIBS, n_trades=60, seed=1)
    assert no_edge["p01"] < 1.0
    assert kill_verdict(1.0, no_edge) is False        # flat is fine here


def test_a_strong_forward_record_graduates():
    """The gate must actually be able to promote, or it is a burial machine."""
    out = assess([case_with_sharpe("strong", 900, 1.5, 30, CONTRIBS)], min_days=60)
    assert out["strong"]["verdict"] == "live", out["strong"]
    assert out["strong"]["psr"] > 0.95


def test_a_weak_forward_record_holds_rather_than_graduating():
    out = assess([case_with_sharpe("weak", 200, 0.1, 30, FLAT_CONTRIBS)], min_days=60)
    assert out["weak"]["verdict"] == "hold"
    assert out["weak"]["psr"] < 0.95


def test_the_cohort_raises_the_bar_for_a_lone_strong_strategy():
    """Decision 4's whole point, and why BH was chosen over nothing: a record
    that graduates on its own can fail to graduate when many other
    pre-registered tests run beside it. The concurrent-confirmation burden is
    real and had never been charged anywhere in this pipeline before."""
    strong = case_with_sharpe("s0", 900, 1.5, 30, CONTRIBS)
    alone = assess([strong], min_days=60)
    assert alone["s0"]["verdict"] == "live"
    assert 0.95 <= alone["s0"]["psr"] < 0.9975      # clears q, not q/20

    cohort = [strong] + [case_with_sharpe(f"w{i}", 900, 0.1, 30, FLAT_CONTRIBS,
                                          seed=100 + i) for i in range(19)]
    crowded = assess(cohort, min_days=60)
    assert crowded["s0"]["verdict"] == "hold", (
        "graduated alone; among 20 it must face q/m and fail")
    assert crowded["s0"]["cohort_size"] == 20


def test_a_buried_strategy_is_excluded_from_the_cohort_that_sets_the_bar():
    """A strategy the record has already disproved must not inflate m and make
    graduation harder for everyone still standing."""
    good = case_with_sharpe("g", 900, 1.5, 30, CONTRIBS)
    dead = make_case("d", 900, 0.01, 40, CONTRIBS)      # equity down 99%
    out = assess([good, dead], min_days=60)
    assert out["d"]["verdict"] == "graveyard"
    assert out["g"]["cohort_size"] == 1
    assert out["g"]["verdict"] == "live"
