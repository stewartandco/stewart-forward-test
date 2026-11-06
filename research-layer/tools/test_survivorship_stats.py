import numpy as np
import pytest

from tools import survivorship_stats as ss


def _idx(**sizes):
    out, start = {}, 0
    for s in ss.STRATA:
        n = sizes.get(s, 0)
        out[s] = np.arange(start, start + n)
        start += n
    return out


def _ab(idx):
    a = np.concatenate([idx["S"], idx["R"]])
    b = np.concatenate([idx["S"], idx["F"], idx["N"], idx["D"]])
    return a, b


@pytest.mark.parametrize("args,want", [
    ((0.10, 1e-9, 0.3), "MATERIAL"),
    ((0.10, 0.0, 0.3), "INCONCLUSIVE"),
    ((0.05, -0.02, 0.0999), "NOT MATERIAL"),
    ((-0.05, -0.1, -0.001), "REVERSE"),
    ((0.05, -0.02, 0.10), "INCONCLUSIVE"),
    ((0.12, -0.01, 0.09), "NOT MATERIAL"),
])
def test_verdict_boundaries(args, want):
    assert ss.verdict(*args) == want


def test_gap_known_value():
    # S=2 cols at 0.5, R=1 col at 1.0 (A only), F=2 cols at 0.0 (B only)
    idx = _idx(S=2, R=1, F=2)
    m = np.zeros((3, 5))
    m[:, idx["S"]] = 0.5
    m[:, idx["R"]] = 1.0
    a, b = _ab(idx)
    # A mean = (0.5+0.5+1.0)/3 ; B mean = (0.5+0.5+0+0)/4
    want = 2.0 / 3 - 0.25
    assert ss.gap(m, a, b) == pytest.approx(want)


def test_bootstrap_deterministic_and_shared_S():
    idx = _idx(S=6, R=3, F=4, N=2, D=2)
    rng = np.random.default_rng(1)
    m = rng.normal(size=(8, 17))
    g1 = ss.bootstrap(m, idx, resamples=200, seed=7)
    g2 = ss.bootstrap(m, idx, resamples=200, seed=7)
    assert g1.shape == (200,) and np.array_equal(g1, g2)
    # all cells of a template equal -> every G* is 0 up to float noise
    base = rng.normal(size=(8, 1))
    flat = np.repeat(base, 17, axis=1)
    g0 = ss.bootstrap(flat, idx, resamples=200, seed=7)
    assert np.allclose(g0, 0.0, atol=1e-12)
    # R, F, N, D carry no cells, varying S: A and B are the SAME S draw, so
    # every G* is exactly 0.0 (an independent B draw of S would not be)
    only_s = _idx(S=10)
    ms = rng.normal(size=(8, 10))
    gs = ss.bootstrap(ms, only_s, resamples=200, seed=7)
    assert np.all(gs == 0.0)


def test_bootstrap_ci_covers_known_gap():
    idx = _idx(S=20, R=10, F=10, N=5, D=5)
    rng = np.random.default_rng(3)
    m = rng.normal(0, 0.3, size=(30, 50))
    # lift R (A-only) so the A-B gap is about 0.2: A = (S+R)/30 cells,
    # B = S+F+N+D over 40 cells, so the lift on R is 0.2 * 30 / 10
    a, b = _ab(idx)
    m[:, idx["R"]] += 0.2 * len(a) / len(idx["R"])
    est = ss.gap(m, a, b)
    g = ss.bootstrap(m, idx, resamples=500, seed=11)
    lo, hi = ss.interval(g)
    assert lo <= est <= hi
    assert est == pytest.approx(0.2, abs=0.05)
    assert lo <= 0.2 <= hi
    assert lo > 0.0 and hi < 1.5


def test_ci_is_the_2_5_and_97_5_percentiles():
    idx = _idx(S=4, R=2, F=3, N=1, D=1)
    m = np.random.default_rng(5).normal(size=(6, 11))
    g = ss.bootstrap(m, idx, resamples=300, seed=99)
    lo, hi = ss.interval(g)
    want = np.percentile(g, [2.5, 97.5])
    assert (lo, hi) == (want[0], want[1])


def test_matrix_raises_on_missing_cell():
    rows = [{"tkey": "t1", "pair": "AAA", "m": 1.0},
            {"tkey": "t1", "pair": "BBB", "m": 2.0},
            {"tkey": "t2", "pair": "AAA", "m": 3.0}]
    with pytest.raises(ValueError):
        ss.matrix(rows, ["t1", "t2"], ["AAA", "BBB"])
    rows.append({"tkey": "t2", "pair": "BBB", "m": 4.0})
    mat = ss.matrix(rows, ["t1", "t2"], ["AAA", "BBB"])
    assert mat.tolist() == [[1.0, 2.0], [3.0, 4.0]]


def _merged():
    # S: P1 ; R: P2 ; F: P3
    def row(m, none, hold, sp, gp=None):
        d = {"m": m, "m_none": none, "hold_sharpe": hold, "screen_pass": sp}
        if gp is not None:
            d["passed"] = gp
        return d
    merged = {
        "t1|P1|1d": row(1.0, False, 0.5, True, True),
        "t2|P1|1d": row(0.0, True, 0.5, False),
        "t1|P2|1d": row(2.0, False, 1.0, True, False),
        "t2|P2|1d": row(1.0, False, None, True, True),
        "t1|P3|1d": row(0.0, True, 0.2, False),
        "t2|P3|1d": row(0.5, False, 0.2, True, False),
    }
    strata = {"P1": "S", "P2": "R", "P3": "F"}
    family = {"t1": "famX", "t2": "famY"}
    return merged, strata, family


def test_secondaries():
    r = ss.secondaries(*_merged())
    # A = S+R (4 cells: 3 screen passes), B = S+F (4 cells: 2 passes)
    assert r["screen_rate"]["A"] == pytest.approx(3 / 4)
    assert r["screen_rate"]["B"] == pytest.approx(2 / 4)
    assert r["screen_gap"] == pytest.approx(0.25)
    # gauntlet passes: A: t1P1, t2P2 =2/4 ; B: t1P1 =1/4 (absent counts as not)
    assert r["gauntlet_gap"] == pytest.approx(0.5 - 0.25)
    # m-hold: A cells with hold: (0.5, -0.5, 1.0) -> mean 1/3 ; B: (0.5,-0.5,-0.2,0.3)=0.025
    assert r["m_minus_hold_gap"] == pytest.approx(1 / 3 - 0.025)
    assert r["m_minus_hold_excluded"] == {"A": 1, "B": 0}
    # hold-only: per pair mean hold; A pairs P1 0.5, P2 1.0 ; B pairs P1 0.5, P3 0.2
    assert r["hold_gap"] == pytest.approx(0.75 - 0.35)
    assert r["stratum_mean_m"] == {"S": 0.5, "R": 1.5, "F": 0.25}
    assert r["family_mean_m"]["A"]["famX"] == pytest.approx(1.5)
    assert r["family_mean_m"]["B"]["famY"] == pytest.approx(0.25)
    assert r["none_counts"] == {"S": 1, "R": 0, "F": 1, "N": 0, "D": 0}


def test_report_states_required_lines():
    sec = ss.secondaries(*_merged())
    res = {"tf": "1d", "verdict": "INCONCLUSIVE", "G": 0.05, "L": -0.02,
           "U": 0.12, "tau": 0.10,
           "stratum_sizes": {"S": 66, "R": 10, "F": 22, "N": 7, "D": 5},
           "stratum_means": {"S": 0.5, "R": 1.5, "F": 0.25, "N": 0.1, "D": 0.0},
           "secondaries": sec}
    text = ss.report(res)
    for needle in ("INCONCLUSIVE", "0.0500", "-0.0200", "0.1200", "0.10",
                   "66", "secondary, not decision-bearing", "1d"):
        assert needle in text


def test_bootstrap_matches_reference_procedure():
    idx = _idx(S=6, R=2, F=2, N=2, D=2)
    m = np.random.default_rng(42).normal(size=(5, 14))
    got = ss.bootstrap(m, idx, resamples=5, seed=ss.SEED)
    rng = np.random.default_rng(ss.SEED)
    want = []
    for _ in range(5):
        t = rng.integers(0, 5, 5)
        d = {}
        for s in ss.STRATA:
            n = len(idx[s])
            d[s] = idx[s][rng.integers(0, n, n)]
        a = list(d["S"]) + list(d["R"])
        b = list(d["S"]) + list(d["F"]) + list(d["N"]) + list(d["D"])
        want.append(np.mean([m[ti, a].mean() - m[ti, b].mean() for ti in t]))
    assert np.allclose(got, np.array(want), rtol=0, atol=1e-12)


def _res(tf):
    sec = ss.secondaries(*_merged())
    return {"tf": tf, "verdict": "NOT MATERIAL", "G": 0.01, "L": -0.02,
            "U": 0.05, "tau": 0.10,
            "stratum_sizes": {"S": 1, "R": 1, "F": 1, "N": 0, "D": 0},
            "stratum_means": {"S": 0.5}, "secondaries": sec}


def test_report_labels_non_1d_verdict_line():
    label = "secondary, not decision-bearing"
    line4 = next(l for l in ss.report(_res("4h")).splitlines() if "Verdict" in l)
    line1 = next(l for l in ss.report(_res("1d")).splitlines() if "Verdict" in l)
    assert label in line4
    assert label not in line1


def test_matrix_raises_on_duplicate_cell():
    rows = [{"tkey": "t1", "pair": "AAA", "m": 1.0},
            {"tkey": "t1", "pair": "AAA", "m": 2.0}]
    with pytest.raises(ValueError, match="t1.*AAA|AAA.*t1"):
        ss.matrix(rows, ["t1"], ["AAA"])
