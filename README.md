# Stewart & Co. — Forward-Test Log

This repository contains the **cryptographically anchored, append-only daily decision log** for the production trading systems operated by Stewart & Co.:

| System | Description |
|---|---|
| **DHRS** | Dynamic Hedging Rotation System — multi-asset crypto rotation with trend filter |
| **MRS** | Majors Rotation System — 3-asset (BTC/ETH/SOL) + Gold defensive escape |
| **SDCA** | System 2 — composite Z-score BTC dollar-cost-averaging strategy |
| **MARS** | Multi-Asset Rotation System — 6-asset (BTC/ETH/SOL/SUI/XRP/BNB) + Gold defensive escape — *currently in final optimization, joins this log once locked* |

Every day at 00:30 UTC, an automated job appends one entry per system to [`forward_test_log.jsonl`](./forward_test_log.jsonl), hash-chains it to the prior entry, and pushes the result to this public GitHub repo. The same decisions are also broadcast in real time to a public Discord channel.

The result: a track record where each entry is anchored by **two independent third parties** (GitHub commit timestamps + Discord server timestamps) plus an internal Merkle-style hash chain that detects any retroactive edits.

---

## Verify it yourself in 30 seconds

```bash
git clone https://github.com/stewartandco/stewart-forward-test.git
cd stewart-forward-test
python verify.py
```

That re-walks the entire hash chain and reports whether any entry has been tampered with. Cross-check the public GitHub commit history and the Discord channel timestamps for additional independent verification.

---

## Why this exists

Backtests are easy to fake. Forward tests are harder — but only if the audit trail itself is tamper-proof.

This log uses **two independent third-party anchors** plus an internal hash chain, so that any prospective licensee can verify the track record without trusting Stewart & Co.:

### 1. GitHub commit timestamps (independent server-side timestamp)

Every daily run is committed to this repository. GitHub's commit timestamps are recorded server-side and can't be backdated by Stewart & Co.; the public commit history is visible to anyone:

```bash
git log --pretty=format:"%h  %ad  %s" --date=iso-strict forward_test_log.jsonl
```

Each commit lands within minutes of the daily 00:30 UTC run, so the GitHub-recorded commit time is itself a third-party witness to when each entry was published.

### 2. Discord server timestamps (real-time public broadcast)

The same daily decisions are posted to a public read-only Discord channel via webhook. Discord stamps every message with the receive-time on its servers — these timestamps are visible in the channel's message history and Discord's API, and are independent of both GitHub and Stewart & Co.'s clock.

### 3. Internal hash chain (tamper detection within the file)

Each JSONL entry contains the SHA-256 hash of the previous entry, forming a Merkle-style linked list. Any single modification, deletion, or reordering of past entries is detected by re-walking the chain.

Verify the chain locally:
```bash
python verify.py forward_test_log.jsonl
```

A tampered file will fail with `✗ BROKEN CHAIN` at the line where the modification occurred.

### Roadmap: Bitcoin-blockchain anchoring

A fourth anchor — [OpenTimestamps](https://opentimestamps.org/) submission, which writes the file's hash into a Bitcoin transaction — is on the roadmap. It's currently paused while a python-bitcoinlib + Python 3.14 compatibility issue gets sorted upstream. Once active, `forward_test_log.jsonl.ots` will appear alongside the JSONL with a Bitcoin Merkle proof.

---

## Entry schema

```jsonc
{
  "ts_utc"            : "2026-04-28T00:31:14Z",     // when the entry was written
  "decision_date"     : "2026-04-27",               // bar-close date the signal was computed from
  "system"            : "MRS",                      // DHRS | MRS | SDCA  (MARS joining)
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

### MARS
```json
"decision": {
  "dominant": "Gold",
  "scores":   {"BTC": 2, "ETH": 1, "SOL": 4, "SUI": 3, "XRP": 1, "BNB": 5, "Gold": 6, "USD": 0}
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
GitHub: [@stewartandco](https://github.com/stewartandco)
