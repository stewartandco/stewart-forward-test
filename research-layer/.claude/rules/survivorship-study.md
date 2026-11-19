---
paths:
  - "tools/survivorship*.py"
  - "tools/test_survivorship_*.py"
---
## Survivorship study (tools/, NOT a pipeline stage; closed 2026-11-11)

`python -m tools.survivorship <cmd>` (design
`docs/2026-11-06-crypto-survivorship-study-design.md`, plan
`docs/plans/2026-11-06-crypto-survivorship-study.md`). One-off measurement:
pre-registration chained at 0-based entry 196930, the 1d result (NOT
MATERIAL, G 0.0313, 95% [-0.0255, 0.0965], tau 0.10) at 196931
(`docs/notes/survivorship-study-v1-result.md`). Per design s6 it changed no
code, gate, universe or quarantine.
- **Intraday arms CLOSED with no result.** 4h (11-09) and 12h (11-11) FAILED
  preflight and stopped by the pre-registered rule; 1h was dropped by Coen
  (11-11). Both failed P1 (archive vs pipeline OHLC: HBARUSDT 2021-01-21 low;
  at 4h also the 2021-01-11 08:00 lows on 6 pairs, one-tick closes, and the
  BTC/ETH 2017-09-06 16:00 bar missing from the archive) and P3 (FXSUSDT
  splice at 2026-01-15, +46% vs tol 0.25). Code and protocol are frozen by the
  manifest hashes: an intraday re-attempt is a NEW pre-registered study, never
  a patch to make these pass.
- **Run window [08:40, 20:00) Perth, enforced in code.** fetch / preflight's
  P2 / run / gauntlet stop with exit 3 at 20:00; a re-run resumes, except
  preflight, which starts over. Exit codes: 0 ok, 1 check failed, 2 refused,
  3 stopped at the window. fetch exits 1 at once on HTTP 403/418/429.
- **`--registry` is always a COPY** (it refuses the live chain); the tool never
  writes `research-layer/data/` (`--pipeline-data-dir` only reads it). `--now`
  is refused unless `SURVIVORSHIP_TEST_CLOCK=1`.
- Tests: `tools/test_survivorship_*.py` (250, about 40 s; `--capture=sys`).
