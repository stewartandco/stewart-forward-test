# Scope 2 — One designation and registry

**Status:** scoped, not designed. Needs its own brainstorm → spec → plan.
**Depends on:** Scope 1 (the gate standard defines what "designated" means).
**Changes:** `stewart-forward-test/research-layer` SCHEMA, verifier, registry.

## Problem

Six registered systems live in `trading-systems` with their own hash-chained
witnessed record. Three live in the research layer's `quarantine`. There is no
single place that answers "what have I designated, and on what evidence?" — and
the naive fix breaks something valuable.

The research layer's chain currently guarantees that anything in `quarantine`
got there by passing pre-registered gates on data it had never seen. Import six
externally-originated systems as ordinary quarantine entries and that guarantee
silently becomes false. The funnel's central claim — *"verify my funnel, every
survivor passed these gates"* — would be laundered, and the 80 → 43 → 3 figure
would stop meaning what it says.

That risk is sharper now than when this was first raised: quarantine has exactly
three honest members, so contamination would be a large fraction of a small
population.

## Scope IN

- **An explicit external-origin record.** A new entry type or a required
  `origin` field distinguishing `literature` (Composer), `discovery` (condition
  scanner), and `external` (registered in another tree before this pipeline
  existed).
- **A distinct lifecycle entry point for external systems** so they never
  transit `proposed → screened → gauntlet`, which they cannot honestly pass.
- **Cross-reference, not re-derivation.** The research layer records each
  external system's `trading-systems` registration hash (e.g. R5's
  `351ae6d437b259bd…`), its registration doc path, its kill wire, its success
  band, and where its ledger lives. It never recomputes those numbers.
- **Funnel statistics that report populations separately**, so 80 → 43 → 3 stays
  literally true and "9 designated systems across 2 origins" is also available.
- Verifier invariants covering whatever is added, in the style of invariants 7–9.

## Scope OUT

- **Importing `trading-systems` code.** Explicitly forbidden by that repo's
  CLAUDE.md and reaffirmed by the concurrent session's `data_import.py`: copying
  data across trees is fine, importing code is not.
- **Re-judging the six.** They passed a stricter bar than the research layer's
  current gauntlet (DSR ≥ 0.95 at registration: R5 0.9743, Q9 0.9573,
  BUNDLE 0.9976 at N=1737). Re-running them under research-layer gates on a
  holdout they have already seen would manufacture false evidence.
- Running external systems through the screen or gauntlet at all. See the fence
  argument in [README.md](README.md).
- Merging the two ledgers. That is Scope 4.

## Decisions needed before this can be specced

1. **Which registry is authoritative for an external system?** Proposal: the
   `trading-systems` registration hash remains the single source of truth, and
   the research layer holds a pointer plus a snapshot of the declared numbers,
   with the pointer verifiable. Avoids two homes for one constant — the same
   principle `bot/status.py` already follows ("it re-derives NOTHING").
2. **Does an external system's designation ever expire in the research layer?**
   `trading-systems` kills on dd_p95 breach. If a system is killed there, what
   happens to its research-layer record? Proposal: the record is a fact about
   history and stays; a `state_change` reflects the kill.
3. **Do the two DSR numbers get shown side by side?** An external system's DSR
   was computed at registration against its own N; a research-layer strategy's is
   computed against clusters of the research registry. They are not comparable
   quantities and must not be tabulated as though they were.
4. **Does `HOUSE-CORE` belong here at all?** It is VT-EW, an allocation product
   rather than a discrete-trade system, and the SOP explicitly scopes rotation
   products to a different SOP. Possibly out of scope for the whole unification.

## Success criteria

- One command answers "what have I designated, on what evidence, and where does
  its record live?" across both origins.
- The 80 → 43 → 3 funnel figure is unchanged and still literally true.
- An outsider running `verify_registry.py` can tell an internally-derived
  survivor from an externally-registered one without reading prose.
- No number has two homes.

## Hazards

- **This is where the project's honesty claim is most easily lost**, and it would
  be lost quietly. Any design that makes external and internal systems
  indistinguishable in the funnel statistics is wrong, however convenient.
- Pre-declare the import rule in a chained note **before** importing anything —
  same discipline as every other protocol change here.
