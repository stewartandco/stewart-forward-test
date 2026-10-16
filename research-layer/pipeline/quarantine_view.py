"""What a quarantine run asks the chain, from ONE read (2026-10-16 fast path
design s3.1).

Before this, every --date pass re-read the whole chain about eight times and
every decision row three more (strategy_states, the duplicate scan, the head
hash): ~42 s per row on the 2026-10-15 chain, 4 h 27 min for 382 rows. The
gauntlet worker fixed the same shape in Build 2a; this is its pattern. A run
builds the view from one Registry.snapshot(on_entry=...), and everything after
moves it over the tail only (Registry.advance), so catch-up's ten dates cost
one read plus their tails.

Every accessor returns exactly what the module-level reader of the same name
in quarantine.py returns -- those readers keep their bodies as the oracle the
view is tested against (test_quarantine_fastpath.py) -- and hands out SHALLOW
copies: the containers are copied, the chained payload dicts (specs,
snapshots, supplements, decision_payload) are shared, so a caller must not
mutate them."""
from __future__ import annotations

from .registry import ChainSnapshot, Registry


class QuarantineView:
    def __init__(self) -> None:
        self.snap: ChainSnapshot | None = None
        self._entered: dict[str, str] = {}
        self._specs: dict[str, dict] = {}
        self._rows: list[dict] = []
        self._first_row: dict[tuple[str, str, str], dict] = {}
        self._snapshots: dict[str, dict] = {}
        self._supplements: dict[str, list[dict]] = {}

    @classmethod
    def build(cls, registry: Registry) -> "QuarantineView":
        """ONE full read of the chain."""
        view = cls()
        view.snap = registry.snapshot(on_entry=view.on_entry)
        return view

    def on_entry(self, e: dict) -> None:
        """Fold one chained entry in, in chain order. The rules are the old
        readers': entry date and spec LAST wins (dict overwrite), base
        snapshot and decision payload FIRST wins (the writers' and the
        verifier's precedent), supplements kept in chain order."""
        et, p = e["entry_type"], e["payload"]
        if et == "state_change":
            if p.get("to") == "quarantine":
                self._entered[p["strategy_id"]] = e["ts_utc"][:10]
        elif et == "strategy_registered":
            self._specs[p["strategy_id"]] = p
        elif et == "quarantine_decision":
            key = (p["strategy_id"], p["date"], p["asset"])
            self._rows.append({"strategy_id": key[0], "date": key[1],
                               "asset": key[2], "ts_utc": e["ts_utc"]})
            self._first_row.setdefault(key, p)
        elif et == "quarantine_data_snapshot":
            self._snapshots.setdefault(p["date"], p)
        elif et == "quarantine_data_snapshot_supplement":
            self._supplements.setdefault(p["date"], []).append(p)

    def advance(self, registry: Registry) -> None:
        """Move over whatever was appended since: the tail only. Raises
        ChainMoved (and changes nothing) on a rewritten or truncated log."""
        self.snap = registry.advance(self.snap, on_entry=self.on_entry)

    def rebuild(self, registry: Registry) -> None:
        """A fresh full read, in place: the recovery after ChainMoved."""
        self.__dict__.update(QuarantineView.build(registry).__dict__)

    def states(self) -> dict[str, str]:
        return dict(self.snap.states)

    def entry_dates(self) -> dict[str, str]:
        return dict(self._entered)

    def specs(self) -> dict[str, dict]:
        return dict(self._specs)

    def decision_rows(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def decision_keys(self) -> set[tuple[str, str, str]]:
        return set(self._first_row)

    def decision_payload(self, key: tuple[str, str, str]) -> dict | None:
        return self._first_row.get(key)

    def snapshots(self) -> dict[str, dict]:
        return dict(self._snapshots)

    def supplements(self) -> dict[str, list[dict]]:
        return {d: list(ps) for d, ps in self._supplements.items()}
