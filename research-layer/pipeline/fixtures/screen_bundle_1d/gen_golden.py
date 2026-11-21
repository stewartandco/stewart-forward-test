"""Regenerates this directory's golden bundle (trades.csv, equity.csv,
config.json) with the PRE-CHANGE write_artifacts, so test_a_1d_bundle_is_byte_identical
compares today's code against bytes the old code wrote (never hand-written).

Recorded generation (2026-10-29, plan 4 Task 5, BASE a04e855c3):
  git -C <worktree> archive --format=zip a04e855c3 research-layer/pipeline -o <t5>/base.zip
  unzip base.zip into <t5>/base (Python zipfile)
  python gen_golden.py <t5>/base/research-layer <this dir>

screen.py uses relative imports, so BASE is imported as the package
`pipeline` from the extracted research-layer root."""
import json
import sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from pipeline import screen  # noqa: E402

inp = json.loads((out / "inputs.json").read_text(encoding="utf-8"))
r = inp["result"]
r["equity"] = [tuple(x) for x in r["equity"]]
b = screen.write_artifacts(out / "_gen", inp["spec"], r, inp["cutoff"],
                           inp["data_hashes"], inp["data_end"])
for n in ("trades.csv", "equity.csv", "config.json"):
    (out / n).write_bytes((b / n).read_bytes())
import shutil
shutil.rmtree(out / "_gen")
print("screen module:", screen.__file__)
