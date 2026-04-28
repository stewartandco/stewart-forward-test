# Stewart & Co. — Forward-Test Log

This repository contains the **cryptographically anchored, append-only daily decision log** for the three production trading systems operated by Stewart & Co.:

| System | Description |
|---|---|
| **DHRS** | Dynamic Hedging Rotation System — multi-asset crypto rotation with trend filter |
| **MRS** | Majors Rotation System — 3-asset (BTC/ETH/SOL) + Gold defensive escape |
| **SDCA** | System 2 — composite Z-score BTC dollar-cost-averaging strategy |

Every day at 00:30 UTC, an automated job appends one entry per system to [`forward_test_log.jsonl`](./forward_test_log.jsonl), hash-chains it to the prior entry, and submits the file's hash to the [OpenTimestamps](https://opentimestamps.org/) calendar servers — which anchor it into a Bitcoin transaction within ~1 hour.

The result: a track record that **no party** (not Stewart & Co., not GitHub, not OpenTimestamps) can backdate or forge after the fact.

---

## Why this exists

Backtests are easy to fake. Forward tests are harder — but only if the audit trail itself is tamper-proof.

This log uses **three independent third-party anchors** so that any prospective licensee can verify the track record without trusting Stewart & Co.:

### 1. OpenTimestamps + Bitcoin blockchain (mathematical proof)

`forward_test_log.jsonl.ots` contains a Merkle-tree proof linking the JSONL file's SHA-256 hash to a specific Bitcoin transaction. Once that transaction has confirmations, the proof is mathematically immutable — altering even one byte of the JSONL would invalidate the proof.

Verify with:
```bash
pip install opentimestamps-client
ots verify forward_test_log.jsonl.ots
```

This will report the Bitcoin block height and timestamp at which the file's hash was anchored. No internet authority is involved — only Bitcoin's proof-of-work.

### 2. GitHub commit timestamps (independent server-side timestamp)

Every daily run is committed to this repository. GitHub's commit timestamps and (optionally) signed-commit infrastructure provide an independent audit trail. The repo's commit history shows exactly when each batch of decisions was published:

```bash
git log --pretty=format:"%h  %ad  %s" --date=iso-strict forward_test_log.jsonl
```

### 3. Internal hash chain (tamper detection within the file)

Each JSONL entry contains the SHA-256 hash of the previous entry, forming a Merkle-style linked list. Any single modification, deletion, or reordering of past entries is detected by re-walking the chain.

Verify the chain locally:
```bash
python verify.py forward_test_log.jsonl
```

A tampered file will fail with `✗ BROKEN CHAIN` at the line where the modification occurred.

---

## Entry schema

```jsonc
{
  "ts_utc"            : "2026-04-28T00:31:14Z",     // when the entry was written
  "decision_date"     : "2026-04-27",               // bar-close date the signal was computed from
  "system"            : "MRS",                      // DHRS | MRS | SDCA
  "version"           : 1,                          // schema version
  "decision"          : { /* system-specific */ },
  "prices_at_decision": { "BTC": 77890.39, ... },   // prices used to compute the signal
  "params_hash"       : "a3f2e9...",                // SHA-256 of locked algo params
  "prev_entry_hash"   : "9c4b81..."                 // SHA-256 of previous entry
}
```

The `params_hash` pins each decision to a specific algorithm configuration. If we ever retroactively tuned a parameter and re-published, every subsequent `params_hash` would change, which would be obvious to anyone re-running the chain check.

---

## System-specific decision payloads

### DHRS
```json
"decision": {
  "dominant":  "ETH",
  "regime_on": true,
  "ranks":     ["ETH", "SOL", "AVAX", "ARB", ...]
}
```

### MRS
```json
"decision": {
  "dominant": "Gold",
  "scores":   {"BTC": 1, "ETH": 2, "SOL": 3, "Gold": 3, "USD": 1}
}
```

### SDCA
```json
"decision": {
  "action":         "BUY",         // BUY | HOLD | SELL
  "composite_z":    -1.654,
  "buy_threshold":  -1.6,
  "sell_threshold":  2.3
}
```

---

## Reproducing the decisions

For each entry, the price and parameter inputs are captured. The MRS and DHRS algorithms are deterministic functions of those inputs — meaning anyone with access to the published indicators (e.g. the [MRS Pine Script indicator on TradingView](https://tradingview.com/)) can replay the exact decision from a given day's prices.

SDCA uses on-chain data inputs that are not all freely accessible (some require ChartInspect / Glassnode subscriptions). The `composite_z` is published in the log so the action is verifiable from the threshold relationship even without re-running the full computation.

---

## Limitations & honest caveats

- The forward test starts from `decision_date = <day this log was first published>`. Earlier entries are not in this log because they were never live.
- "Forward test" ≠ "live trading". These are the system's recommended allocations; actual subscriber portfolios may differ due to tax, brokerage, and rebalancing constraints.
- A small number of network failures may cause the daily commit to slip by a few hours; the OpenTimestamps anchor still pins the data to roughly the right time.
- This log is published as a credibility tool, not as financial advice. Past performance does not guarantee future results.

---

## Contact

Stewart & Co.  
[stewartandco.org](https://stewartandco.org)
