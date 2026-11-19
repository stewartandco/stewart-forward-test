---
paths:
  - "pipeline/triage_batch.py"
  - "logs/triage_rejections.jsonl"
---
## Triage escalations -- REMOVED by D44 (2026-09-26)
- The 2026-09-03 `--queue` view and the skip-set's `dissent_reasons` are gone
  with the queue itself. Dissent reasons now live in
  `logs/triage_rejections.jsonl`, one row per rejected card.

